"""
Block-level measured naloxone access for Dallas County.

Every value in the outputs is either a count of people (Census 2020 P.L. 94-171
P1_001N block population) or a straight-line distance in metres computed in
EPSG:32138 -- no modelling, estimation, or interpolation. Block locations are
the Census Bureau's own internal points (INTPTLAT20/INTPTLON20). A block is
"beyond X" when its internal point lies farther than X metres from the nearest
relevant site, by nearest-neighbour search over a KD-tree.

Inputs
  Census 2020 P.L. block population (API; snapshotted to data/raw/census/)
  TIGER/Line 2024 TABBLOCK20, filtered COUNTYFP20=113
  data/clean/naloxone_locations.geojson
  data/raw/dart/google_transit.zip (GTFS stops.txt)
  data/clean/svi_tracts.geojson (tract polygons for the rollup)

Outputs (data/clean/)
  block_access.csv, block_access_summary.json, block_access_tracts.geojson,
  block_access_points.geojson (stranded blocks only, when small enough to ship)

Needs CENSUS_API_KEY only on a first run with no data/raw/census snapshot present.
Run: ./venv/bin/python scripts/build_block_access.py
"""
import csv
import io
import json
import os
import sys
import time
import zipfile

import numpy as np
import requests
from dotenv import load_dotenv
from pyproj import Transformer
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    DATA_CLEAN,
    DATA_RAW,
    REPO_ROOT,
    DALLAS_COUNTYFP as COUNTYFP,
    DALLAS_STATEFP as STATEFP,
    atomic_download,
    ensure_dirs,
    latest_raw_path,
    log,
    pipeline_date,
    save_json,
)

PROJ_CRS = "EPSG:32138"  # NAD83 / Texas North Central (metres)
TODAY = pipeline_date().isoformat()

MI = 1609.344
D_1MI, D_2MI, D_5MI = MI, 2 * MI, 5 * MI
DART_M = 805.0           # half-mile transit-proximity threshold
MAX_STRANDED_POINTS = 5000

ACS_COUNTY_POP = 2_604_053  # cross-check reference only; never enters the math

CENSUS_URL = "https://api.census.gov/data/2020/dec/pl"
TIGER_BLOCK_URL = (
    "https://www2.census.gov/geo/tiger/TIGER2024/TABBLOCK20/tl_2024_48_tabblock20.zip"
)
TIGER_BLOCK_ZIP = os.path.join(DATA_RAW, "tiger", "tl_2024_48_tabblock20.zip")
CENSUS_RAW = os.path.join(DATA_RAW, "census", f"pl2020_block_dallas_{TODAY}.json")

NALOXONE = os.path.join(DATA_CLEAN, "naloxone_locations.geojson")
DART_GTFS_URL = "https://www.dart.org/transitdata/latest/google_transit.zip"
DART_ZIP = os.path.join(DATA_RAW, "dart", "google_transit.zip")
SVI_TRACTS = os.path.join(DATA_CLEAN, "svi_tracts.geojson")

OUT_CSV = os.path.join(DATA_CLEAN, "block_access.csv")
OUT_SUMMARY = os.path.join(DATA_CLEAN, "block_access_summary.json")
OUT_TRACTS = os.path.join(DATA_CLEAN, "block_access_tracts.geojson")
OUT_POINTS = os.path.join(DATA_CLEAN, "block_access_points.geojson")


def fetch_block_pop():
    """2020 decennial block populations, with the snapshot they came from.

    The decennial count never revises, so an existing snapshot is reused rather
    than re-pulled; the path is returned so the summary records what was
    actually read rather than what today's run would have written.
    """
    snapshot = latest_raw_path("pl2020_block_dallas", subdir="census")
    if snapshot:
        log(f"Reusing Census snapshot {os.path.relpath(snapshot, REPO_ROOT)}")
        with open(snapshot) as f:
            data = json.load(f)
    else:
        load_dotenv(os.path.join(REPO_ROOT, ".env"))
        key = os.environ.get("CENSUS_API_KEY")
        if not key:
            raise SystemExit(
                "CENSUS_API_KEY missing from .env and no data/raw/census snapshot present")
        log("Fetching Census 2020 PL P1_001N block population (Dallas County)...")
        r = requests.get(
            CENSUS_URL,
            params={"get": "P1_001N", "for": "block:*",
                    "in": f"state:{STATEFP} county:{COUNTYFP}", "key": key},
            timeout=180,
        )
        r.raise_for_status()
        data = r.json()
        os.makedirs(os.path.dirname(CENSUS_RAW), exist_ok=True)
        with open(CENSUS_RAW, "w") as f:
            json.dump(data, f)
        log(f"Saved raw Census snapshot -> {CENSUS_RAW} ({len(data) - 1} block rows)")
        snapshot = CENSUS_RAW

    idx = {name: i for i, name in enumerate(data[0])}
    pop_by_geoid = {}
    for row in data[1:]:
        geoid = row[idx["state"]] + row[idx["county"]] + row[idx["tract"]] + row[idx["block"]]
        pop_by_geoid[geoid] = int(row[idx["P1_001N"]])
    log(f"Parsed {len(pop_by_geoid)} block populations")
    return pop_by_geoid, snapshot


def load_blocks(pop_by_geoid):
    import geopandas as gpd

    if not os.path.exists(TIGER_BLOCK_ZIP):
        os.makedirs(os.path.dirname(TIGER_BLOCK_ZIP), exist_ok=True)
        atomic_download(TIGER_BLOCK_URL, TIGER_BLOCK_ZIP)
    log(f"Reading TIGER 2024 blocks (statewide) from {TIGER_BLOCK_ZIP} ...")
    gdf = gpd.read_file(TIGER_BLOCK_ZIP)
    log(f"  statewide blocks: {len(gdf)}")
    gdf = gdf[gdf["COUNTYFP20"] == COUNTYFP].copy()
    log(f"  Dallas County (COUNTYFP20={COUNTYFP}) blocks: {len(gdf)}")

    gdf["geoid20"] = gdf["GEOID20"]
    gdf["tract_geoid"] = gdf["STATEFP20"] + gdf["COUNTYFP20"] + gdf["TRACTCE20"]
    gdf["intpt_lat"] = gdf["INTPTLAT20"].astype(float)
    gdf["intpt_lon"] = gdf["INTPTLON20"].astype(float)
    gdf["pop"] = gdf["geoid20"].map(pop_by_geoid)

    n_missing = int(gdf["pop"].isna().sum())
    if n_missing:
        log(f"  WARNING: {n_missing} TIGER blocks had no API population (set to 0)")
    gdf["pop"] = gdf["pop"].fillna(0).astype(int)
    return gdf


def load_naloxone():
    d = json.load(open(NALOXONE))
    feats = d["features"]
    all_pts, pts_247, pts_otp = [], [], []
    skipped = []
    for f in feats:
        geom = f.get("geometry")
        p = f["properties"]
        if not geom or not geom.get("coordinates"):
            skipped.append({"name": p.get("name"), "category": p.get("category"),
                            "access_247": p.get("access_247")})
            continue
        lon, lat = geom["coordinates"][:2]
        all_pts.append((lon, lat))
        if p.get("access_247") is True:
            pts_247.append((lon, lat))
        if p.get("category") == "otp_methadone":
            pts_otp.append((lon, lat))
    log(f"Naloxone supply points: {len(all_pts)} total | "
        f"{len(pts_247)} 24/7 | {len(pts_otp)} OTP (otp_methadone)"
        f" ({len(skipped)} feature(s) skipped: no geometry)")
    for s in skipped:
        log(f"  SKIPPED (unmeasurable, no coords): {s['name']} "
            f"[{s['category']}, access_247={s['access_247']}]")
    return all_pts, pts_247, pts_otp, skipped


def load_dart_stops():
    if not os.path.exists(DART_ZIP):
        os.makedirs(os.path.dirname(DART_ZIP), exist_ok=True)
        atomic_download(DART_GTFS_URL, DART_ZIP)
    with zipfile.ZipFile(DART_ZIP) as z, z.open("stops.txt") as fh:
        rdr = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
        pts = []
        for row in rdr:
            try:
                lat = float(row["stop_lat"])
                lon = float(row["stop_lon"])
            except (ValueError, KeyError):
                continue
            pts.append((lon, lat))
    log(f"DART GTFS stops: {len(pts)}")
    return pts


def project_lonlat(lonlat_pairs):
    """Project (lon, lat) WGS84 pairs to EPSG:32138 metres."""
    if not lonlat_pairs:
        return np.empty((0, 2))
    tr = Transformer.from_crs("EPSG:4326", PROJ_CRS, always_xy=True)
    x, y = tr.transform([p[0] for p in lonlat_pairs], [p[1] for p in lonlat_pairs])
    return np.column_stack([x, y])


def nearest_dist(block_xy, site_xy):
    """Nearest straight-line distance (m) from each block to any site."""
    if len(site_xy) == 0:
        return np.full(len(block_xy), np.inf)
    d, _ = cKDTree(site_xy).query(block_xy, k=1)
    return d


def main():
    t0 = time.time()
    ensure_dirs()

    pop_by_geoid, census_snapshot = fetch_block_pop()
    blocks = load_blocks(pop_by_geoid)

    total_blocks = len(blocks)
    pop_total_blocks = int(blocks["pop"].sum())
    populated = blocks[blocks["pop"] > 0].copy()
    n_populated = len(populated)
    log(f"Total blocks: {total_blocks} | populated (pop>0): {n_populated}")
    log(f"County population summed from blocks (P1_001N): {pop_total_blocks:,}")
    log(f"ACS 2024 reference county pop (cross-check only): {ACS_COUNTY_POP:,} "
        f"| block-2020 minus ACS = {pop_total_blocks - ACS_COUNTY_POP:,}")

    all_pts, pts_247, pts_otp, skipped_supply = load_naloxone()
    dart_pts = load_dart_stops()

    log(f"Projecting to {PROJ_CRS} and computing nearest-neighbor distances...")
    block_xy = project_lonlat(list(zip(populated["intpt_lon"], populated["intpt_lat"])))
    d_supply = nearest_dist(block_xy, project_lonlat(all_pts))
    d_247 = nearest_dist(block_xy, project_lonlat(pts_247))
    d_otp = nearest_dist(block_xy, project_lonlat(pts_otp))
    d_dart = nearest_dist(block_xy, project_lonlat(dart_pts))

    populated = populated.assign(
        dist_supply_m=np.round(d_supply, 1),
        dist_247_m=np.round(d_247, 1),
        dist_otp_m=np.round(d_otp, 1),
        dist_dart_m=np.round(d_dart, 1),
    )
    populated["dart_within_805m"] = d_dart <= DART_M
    populated["within_1mi_supply"] = d_supply <= D_1MI
    populated["within_2mi_supply"] = d_supply <= D_2MI
    populated["stranded"] = (d_supply > D_1MI) & (d_dart > DART_M)  # pop > 0 already

    def pop_where(mask):
        return int(populated.loc[mask, "pop"].sum())

    beyond_1mi = pop_where(d_supply > D_1MI)
    beyond_2mi = pop_where(d_supply > D_2MI)
    beyond_5mi = pop_where(d_supply > D_5MI)
    beyond_5mi_247 = pop_where(d_247 > D_5MI)
    stranded_pop = pop_where(populated["stranded"].values)
    n_stranded_blocks = int(populated["stranded"].sum())

    def pct(n):
        return round(100.0 * n / pop_total_blocks, 2) if pop_total_blocks else 0.0

    log(f"Residents >1mi from any supply: {beyond_1mi:,} ({pct(beyond_1mi)}%)")
    log(f"Residents >2mi from any supply: {beyond_2mi:,} ({pct(beyond_2mi)}%)")
    log(f"Residents >5mi from any supply: {beyond_5mi:,} ({pct(beyond_5mi)}%)")
    log(f"Residents >5mi from 24/7 site (24/7 desert): {beyond_5mi_247:,} ({pct(beyond_5mi_247)}%)")
    log(f"Stranded population (>1mi supply AND >805m DART): {stranded_pop:,} "
        f"across {n_stranded_blocks} blocks")

    grp = populated.groupby("tract_geoid")
    tract_pop = grp["pop"].sum()
    tract_beyond_1mi = grp.apply(
        lambda g: g.loc[g["dist_supply_m"] > D_1MI, "pop"].sum(), include_groups=False)
    tract_beyond_2mi = grp.apply(
        lambda g: g.loc[g["dist_supply_m"] > D_2MI, "pop"].sum(), include_groups=False)
    tract_no247_5mi = grp.apply(
        lambda g: g.loc[g["dist_247_m"] > D_5MI, "pop"].sum(), include_groups=False)
    tract_stranded = grp.apply(
        lambda g: g.loc[g["stranded"], "pop"].sum(), include_groups=False)

    tract_rollup = {}
    for tg in tract_pop.index:
        p = int(tract_pop[tg])
        b1 = int(tract_beyond_1mi[tg])
        b2 = int(tract_beyond_2mi[tg])
        n247 = int(tract_no247_5mi[tg])
        strd = int(tract_stranded[tg])
        tract_rollup[tg] = {
            "pop": p,
            "residents_beyond_1mi": b1,
            "residents_beyond_2mi": b2,
            "residents_no247_5mi": n247,
            "stranded_pop": strd,
            "pct_pop_beyond_1mi": round(100.0 * b1 / p, 2) if p else 0.0,
            "pct_pop_beyond_2mi": round(100.0 * b2 / p, 2) if p else 0.0,
            "pct_pop_no247_5mi": round(100.0 * n247 / p, 2) if p else 0.0,
        }

    log(f"Writing {OUT_CSV} ...")
    cols = ["geoid20", "pop", "intpt_lat", "intpt_lon", "tract_geoid",
            "dist_supply_m", "dist_247_m", "dist_otp_m", "dist_dart_m",
            "dart_within_805m", "within_1mi_supply", "within_2mi_supply", "stranded"]
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for _, r in populated.iterrows():
            w.writerow([
                r["geoid20"], r["pop"], round(r["intpt_lat"], 6), round(r["intpt_lon"], 6),
                r["tract_geoid"], r["dist_supply_m"], r["dist_247_m"], r["dist_otp_m"],
                r["dist_dart_m"], bool(r["dart_within_805m"]), bool(r["within_1mi_supply"]),
                bool(r["within_2mi_supply"]), bool(r["stranded"]),
            ])
    log(f"  wrote {n_populated} populated-block rows ({os.path.getsize(OUT_CSV):,} bytes)")

    method = (
        "100% measured, not modeled. Every value is a count of people (Census "
        "2020 PL P1_001N block population) or a straight-line (Euclidean) "
        "distance in meters computed in EPSG:32138. Block locations are the "
        "Census Bureau internal points (INTPTLAT20/INTPTLON20). No modeling, "
        "estimation, or interpolation. A resident is 'beyond X mi' if their "
        "block's internal point is farther than X miles from the nearest "
        "relevant site (nearest-neighbor via scipy.cKDTree)."
    )
    summary = {
        "method": method,
        "generated": TODAY,
        "crs_measurement": PROJ_CRS,
        "thresholds_m": {"1mi": D_1MI, "2mi": D_2MI, "5mi": D_5MI, "dart": DART_M},
        "inputs": {
            "census_pl_snapshot": os.path.relpath(census_snapshot, REPO_ROOT),
            "tiger_blocks": os.path.relpath(TIGER_BLOCK_ZIP, REPO_ROOT),
            "naloxone_locations": os.path.relpath(NALOXONE, REPO_ROOT),
            "dart_gtfs": os.path.relpath(DART_ZIP, REPO_ROOT),
        },
        "counts": {
            "total_blocks": total_blocks,
            "populated_blocks": n_populated,
            "supply_points_total": len(all_pts),
            "supply_points_247": len(pts_247),
            "supply_points_otp": len(pts_otp),
            "dart_stops": len(dart_pts),
            "supply_points_skipped_no_coords": skipped_supply,
        },
        "population": {
            "county_pop_from_2020_blocks": pop_total_blocks,
            "county_pop_acs2024_reference": ACS_COUNTY_POP,
            "block2020_minus_acs2024": pop_total_blocks - ACS_COUNTY_POP,
            "note": "Block sum is the exact 2020 decennial census count; ACS "
                    "2024 is a later survey estimate shown only for cross-check.",
        },
        "headline": {
            "residents_beyond_1mi_supply": beyond_1mi,
            "pct_beyond_1mi_supply": pct(beyond_1mi),
            "residents_beyond_2mi_supply": beyond_2mi,
            "pct_beyond_2mi_supply": pct(beyond_2mi),
            "residents_beyond_5mi_supply": beyond_5mi,
            "pct_beyond_5mi_supply": pct(beyond_5mi),
            "residents_beyond_5mi_247": beyond_5mi_247,
            "pct_beyond_5mi_247": pct(beyond_5mi_247),
            "residents_beyond_5mi_247_caveat": (
                f"Measured against {len(pts_247)} 24/7 site(s) that have "
                f"coordinates. {len(skipped_supply)} listed supply point(s) "
                "lack coordinates and are unmeasurable; if any 24/7 site among "
                "them were geocoded this desert could shrink."),
            "stranded_population": stranded_pop,
            "stranded_blocks": n_stranded_blocks,
            "stranded_definition": "pop>0 AND nearest supply >1609.344m (1mi) "
                                    "AND nearest DART stop >805m",
        },
        "tract_rollup": tract_rollup,
    }
    save_json(summary, OUT_SUMMARY)

    log(f"Building {OUT_TRACTS} from svi_tracts polygons + measured rollups...")
    svi = json.load(open(SVI_TRACTS))
    out_feats = []
    matched, unmatched = 0, 0
    for f in svi["features"]:
        tg = str(f["properties"].get("geoid"))
        roll = tract_rollup.get(tg)
        if roll is None:
            unmatched += 1
            props = {
                "geoid": tg, "measured": True, "pop": 0,
                "pct_pop_beyond_1mi": 0.0, "pct_pop_beyond_2mi": 0.0,
                "pct_pop_no247_5mi": 0.0, "stranded_pop": 0,
                "note": "no populated blocks matched",
            }
        else:
            matched += 1
            props = {
                "geoid": tg, "measured": True, "pop": roll["pop"],
                "pct_pop_beyond_1mi": roll["pct_pop_beyond_1mi"],
                "pct_pop_beyond_2mi": roll["pct_pop_beyond_2mi"],
                "pct_pop_no247_5mi": roll["pct_pop_no247_5mi"],
                "residents_beyond_1mi": roll["residents_beyond_1mi"],
                "residents_beyond_2mi": roll["residents_beyond_2mi"],
                "stranded_pop": roll["stranded_pop"],
            }
        out_feats.append({"type": "Feature", "properties": props,
                          "geometry": f["geometry"]})
    tracts_geojson = {
        "type": "FeatureCollection",
        "meta": {"label": "measured, not modeled", "method": method,
                 "crs_measurement": PROJ_CRS},
        "features": out_feats,
    }
    with open(OUT_TRACTS, "w") as fh:
        json.dump(tracts_geojson, fh)
    log(f"  wrote {len(out_feats)} tract polygons "
        f"(matched={matched}, unmatched={unmatched}, "
        f"{os.path.getsize(OUT_TRACTS):,} bytes)")

    svi_geoids = {str(f["properties"].get("geoid")) for f in svi["features"]}
    missing_poly = [tg for tg in tract_rollup if tg not in svi_geoids]
    if missing_poly:
        log(f"  NOTE: {len(missing_poly)} tract(s) with population have no svi "
            f"polygon (omitted from choropleth): {missing_poly[:10]}")

    stranded_df = populated[populated["stranded"]]
    if len(stranded_df) <= MAX_STRANDED_POINTS:
        feats = []
        for _, r in stranded_df.iterrows():
            feats.append({
                "type": "Feature",
                "properties": {"geoid20": r["geoid20"], "pop": int(r["pop"]),
                               "tract_geoid": r["tract_geoid"],
                               "dist_supply_m": r["dist_supply_m"],
                               "dist_dart_m": r["dist_dart_m"]},
                "geometry": {"type": "Point",
                             "coordinates": [round(r["intpt_lon"], 6),
                                             round(r["intpt_lat"], 6)]},
            })
        with open(OUT_POINTS, "w") as fh:
            json.dump({"type": "FeatureCollection",
                       "meta": {"label": "measured, not modeled",
                                "desc": "stranded populated blocks"},
                       "features": feats}, fh)
        log(f"  wrote {len(feats)} stranded-block points -> {OUT_POINTS} "
            f"({os.path.getsize(OUT_POINTS):,} bytes)")
    else:
        log(f"  SKIP stranded points: {len(stranded_df)} > {MAX_STRANDED_POINTS} features")

    log(f"DONE in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
