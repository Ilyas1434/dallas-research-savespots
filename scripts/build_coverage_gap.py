#!/usr/bin/env python3
"""
build_coverage_gap.py

For each Dallas County census tract, computes distance from the tract's
geometric centroid to the nearest naloxone supply point, split by:
  - any supply
  - 24/7 supply
  - storefront/dispenser supply

Depends on:
  data/clean/tract_overdose.geojson   (built by the mortality module; contract:
      properties geoid, od_death_rate_per_100k, count_suppressed,
      rate_suppressed, period)
  data/clean/naloxone_locations.geojson (built by scripts/build_naloxone.py)

If tract_overdose.geojson is absent, this script still runs the distance-only
layer using TIGER 2024 tract geometry for Dallas County (COUNTYFP 113) as a
fallback, and notes the missing dependency in the output.

Run standalone from repo root:
    ./venv/bin/python scripts/build_coverage_gap.py

Output:
    data/clean/coverage_gap.json
"""

import json
import statistics
import sys
from pathlib import Path

import requests

try:
    import geopandas as gpd
except ImportError:
    print("FATAL: geopandas required. pip install -r requirements.txt", file=sys.stderr)
    raise

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = REPO_ROOT / "data" / "raw"
DATA_CLEAN = REPO_ROOT / "data" / "clean"
TIGER_DIR = DATA_RAW / "tiger"

TRACT_OVERDOSE_PATH = DATA_CLEAN / "tract_overdose.geojson"
NALOXONE_PATH = DATA_CLEAN / "naloxone_locations.geojson"
TRACT_ZIP_URL = "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_48_tract.zip"
DALLAS_COUNTYFP = "113"

# Projected CRS in meters covering North Texas (per task spec).
PROJECTED_CRS = "EPSG:32138"  # NAD83 / Texas North Central, unit = metre

MILE_IN_M = 1609.344


def log(msg):
    print(msg, flush=True)


def load_dallas_tracts():
    """Return a GeoDataFrame of Dallas County tracts with a 'geoid' column,
    preferring tract_overdose.geojson (already matched to CDC mortality data)
    and falling back to a fresh TIGER 2024 tract download otherwise."""
    if TRACT_OVERDOSE_PATH.exists():
        gdf = gpd.read_file(TRACT_OVERDOSE_PATH)
        log(f"[tracts] loaded {len(gdf)} tracts from {TRACT_OVERDOSE_PATH.relative_to(REPO_ROOT)} "
            f"(has mortality attributes)")
        return gdf, True

    log(f"[tracts] WARNING: {TRACT_OVERDOSE_PATH.relative_to(REPO_ROOT)} not found "
        f"(mortality module dependency missing) -- falling back to raw TIGER tract geometry")
    zpath = TIGER_DIR / "tl_2024_48_tract.zip"
    if not zpath.exists():
        log(f"[tracts] downloading {TRACT_ZIP_URL}")
        TIGER_DIR.mkdir(parents=True, exist_ok=True)
        r = requests.get(TRACT_ZIP_URL, timeout=300)
        r.raise_for_status()
        tmp = zpath.with_suffix(".zip.tmp")
        tmp.write_bytes(r.content)
        tmp.replace(zpath)
        log(f"[tracts] saved {zpath.relative_to(REPO_ROOT)}")
    full = gpd.read_file(f"zip://{zpath}")
    dallas = full[full["COUNTYFP"] == DALLAS_COUNTYFP].copy()
    dallas = dallas.rename(columns={"GEOID": "geoid"})
    log(f"[tracts] loaded {len(dallas)} Dallas County (COUNTYFP {DALLAS_COUNTYFP}) tracts from TIGER")
    return dallas, False


def load_supply_points():
    if not NALOXONE_PATH.exists():
        raise FileNotFoundError(
            f"{NALOXONE_PATH.relative_to(REPO_ROOT)} not found -- run scripts/build_naloxone.py first"
        )
    gdf = gpd.read_file(NALOXONE_PATH)
    n_total = len(gdf)
    gdf = gdf[gdf.geometry.notna()].copy()
    log(f"[supply] loaded {n_total} naloxone features, {len(gdf)} with usable geometry")
    return gdf


def main():
    DATA_CLEAN.mkdir(parents=True, exist_ok=True)

    log("=" * 70)
    log("Load Dallas County tracts")
    log("=" * 70)
    tracts, has_mortality = load_dallas_tracts()

    log("")
    log("=" * 70)
    log("Load naloxone supply points")
    log("=" * 70)
    try:
        supply = load_supply_points()
    except FileNotFoundError as e:
        log(f"[FATAL] {e}")
        out = {
            "generated": "2026-07-23",
            "status": "failed",
            "reason": str(e),
        }
        out_path = DATA_CLEAN / "coverage_gap.json"
        out_path.write_text(json.dumps(out, indent=2))
        log(f"[output] wrote failure stub -> {out_path.relative_to(REPO_ROOT)}")
        return

    if supply.empty:
        log("[FATAL] no naloxone supply points with geometry -- cannot compute distances")
        return

    log("")
    log("=" * 70)
    log("Project to EPSG:32138 (NAD83 / Texas North Central, meters) and compute centroids")
    log("=" * 70)
    tracts_proj = tracts.to_crs(PROJECTED_CRS)
    supply_proj = supply.to_crs(PROJECTED_CRS)
    tracts_proj["centroid"] = tracts_proj.geometry.centroid

    any_supply = supply_proj
    supply_247 = supply_proj[supply_proj["access_247"] == True]  # noqa: E712
    supply_storefront = supply_proj[supply_proj["category"] == "storefront_dispenser"]

    log(f"[supply] any={len(any_supply)}  24/7={len(supply_247)}  storefront/dispenser={len(supply_storefront)}")

    def nearest_dist(centroid, points_gdf):
        if points_gdf.empty:
            return None
        dists = points_gdf.geometry.distance(centroid)
        return float(dists.min())

    log("")
    log("=" * 70)
    log("Compute nearest-distance layers per tract")
    log("=" * 70)

    records = []
    for _, row in tracts_proj.iterrows():
        centroid = row["centroid"]
        geoid = row.get("geoid") or row.get("GEOID")
        d_any = nearest_dist(centroid, any_supply)
        d_247 = nearest_dist(centroid, supply_247)
        d_store = nearest_dist(centroid, supply_storefront)

        rec = {
            "geoid": geoid,
            "dist_nearest_any_m": round(d_any, 1) if d_any is not None else None,
            "dist_nearest_247_m": round(d_247, 1) if d_247 is not None else None,
            "dist_nearest_storefront_m": round(d_store, 1) if d_store is not None else None,
            "supply_within_1mi": (d_any is not None and d_any <= MILE_IN_M),
            "supply_within_2mi": (d_any is not None and d_any <= 2 * MILE_IN_M),
        }
        if has_mortality:
            rec["od_death_rate_per_100k"] = row.get("od_death_rate_per_100k")
        records.append(rec)

    log(f"[distance] computed for {len(records)} tracts")

    # summary stats
    def summary_stats(key):
        vals = [r[key] for r in records if r[key] is not None]
        if not vals:
            return None
        return {
            "n": len(vals),
            "mean_m": round(statistics.mean(vals), 1),
            "median_m": round(statistics.median(vals), 1),
            "min_m": round(min(vals), 1),
            "max_m": round(max(vals), 1),
            "mean_mi": round(statistics.mean(vals) / MILE_IN_M, 2),
            "median_mi": round(statistics.median(vals) / MILE_IN_M, 2),
            "max_mi": round(max(vals) / MILE_IN_M, 2),
        }

    n_within_1mi = sum(1 for r in records if r["supply_within_1mi"])
    n_within_2mi = sum(1 for r in records if r["supply_within_2mi"])

    summary = {
        "n_tracts": len(records),
        "n_supply_any": len(any_supply),
        "n_supply_247": len(supply_247),
        "n_supply_storefront": len(supply_storefront),
        "dist_nearest_any_m": summary_stats("dist_nearest_any_m"),
        "dist_nearest_247_m": summary_stats("dist_nearest_247_m"),
        "dist_nearest_storefront_m": summary_stats("dist_nearest_storefront_m"),
        "pct_tracts_within_1mi_any": round(100 * n_within_1mi / len(records), 1) if records else None,
        "pct_tracts_within_2mi_any": round(100 * n_within_2mi / len(records), 1) if records else None,
    }

    log("")
    log("=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log(f"Tracts: {summary['n_tracts']}")
    log(f"Supply points used: any={summary['n_supply_any']} 24/7={summary['n_supply_247']} "
        f"storefront={summary['n_supply_storefront']}")
    log(f"Nearest-any distance (mi): mean={summary['dist_nearest_any_m']['mean_mi']} "
        f"median={summary['dist_nearest_any_m']['median_mi']} max={summary['dist_nearest_any_m']['max_mi']}")
    if summary["dist_nearest_247_m"]:
        log(f"Nearest-24/7 distance (mi): mean={summary['dist_nearest_247_m']['mean_mi']} "
            f"median={summary['dist_nearest_247_m']['median_mi']} max={summary['dist_nearest_247_m']['max_mi']}")
    if summary["dist_nearest_storefront_m"]:
        log(f"Nearest-storefront distance (mi): mean={summary['dist_nearest_storefront_m']['mean_mi']} "
            f"median={summary['dist_nearest_storefront_m']['median_mi']} max={summary['dist_nearest_storefront_m']['max_mi']}")
    log(f"% tracts with any supply within 1mi: {summary['pct_tracts_within_1mi_any']}%")
    log(f"% tracts with any supply within 2mi: {summary['pct_tracts_within_2mi_any']}%")

    out = {
        "generated": "2026-07-23",
        "status": "ok",
        "method_note": (
            "Distances computed from each tract's GEOMETRIC centroid (population-weighted "
            "centroid not available) to the nearest naloxone supply point, projected to "
            "EPSG:32138 (NAD83 / Texas North Central, unit=metre). Dallas is car-dependent; "
            "distances are recorded raw with no capping."
        ),
        "mortality_join": "joined" if has_mortality else "missing_dependency_distance_only",
        "crs_used": PROJECTED_CRS,
        "summary": summary,
        "tracts": records,
    }

    out_path = DATA_CLEAN / "coverage_gap.json"
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2))
    tmp.replace(out_path)
    log(f"[output] wrote {out_path.relative_to(REPO_ROOT)}")

    # validate
    check = json.loads(out_path.read_text())
    assert check["status"] == "ok"
    assert len(check["tracts"]) == len(records)
    log(f"[validate] reloaded OK, {len(check['tracts'])} tract records")


if __name__ == "__main__":
    main()
