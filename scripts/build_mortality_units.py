#!/usr/bin/env python
"""
Publish CDC drug-overdose mortality at its native reporting geography.

pull_mortality.py explodes each CDC 4day-mt2f aggregation unit's single
model-based rate onto all of its constituent census tracts, which is what the
tract-level joins downstream need but which visually smears 150 units of real
signal across 645 tracts. This script dissolves those tracts back into the true
aggregation-unit polygons and publishes one feature per unit -- the finest
mortality geography Texas suppression actually permits.

Reads the newest data/raw/tract_drug_od_*.json snapshot written by
pull_mortality.py; never re-pulls from CDC, and never touches
data/clean/tract_overdose.geojson, whose tract-level shape the composite index
depends on.

Outputs: data/clean/mortality_units.geojson, mortality_units_meta.json

Run: ./venv/bin/python scripts/build_mortality_units.py
"""

import json
import os
import statistics
import sys
from datetime import datetime, timezone

import geopandas as gpd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    DATA_CLEAN as CLEAN,
    DATA_RAW as RAW,
    REPO_ROOT as REPO,
    TIGER_ZIP,
    DALLAS_COUNTYFP,
    DALLAS_FIPS,
    DALLAS_STATEFP,
    latest_raw_path,
)

EXPECTED_UNITS = 150
SOURCE_LABEL = "CDC 4day-mt2f (native aggregation units)"

MONTHS = {
    "January": "01", "February": "02", "March": "03", "April": "04",
    "May": "05", "June": "06", "July": "07", "August": "08",
    "September": "09", "October": "10", "November": "11", "December": "12",
}


def log(msg):
    print(msg, flush=True)


def period_from_range(ttm):
    # "January, 2025 to December, 2025" -> "2025-01/2025-12"
    try:
        left, right = [s.strip() for s in ttm.split(" to ")]
        lm, ly = [t.strip() for t in left.split(",")]
        rm, ry = [t.strip() for t in right.split(",")]
        return f"{ly}-{MONTHS[lm]}/{ry}-{MONTHS[rm]}"
    except Exception:
        return ttm


def parse_ci(ci_str):
    """'9.3-42.3' -> (9.3, 42.3). None if absent/unparseable."""
    if not ci_str:
        return None, None
    try:
        lo, hi = ci_str.split("-")
        return float(lo), float(hi)
    except Exception:
        return None, None


def load_raw_units(snapshot):
    log(f"=== Loading raw CDC snapshot: {snapshot} ===")
    with open(snapshot) as f:
        rows = json.load(f)
    log(f"  total TX agg-unit rows in snapshot: {len(rows)}")

    units = []
    for x in rows:
        consts = [c.strip() for c in x.get("name", "").split(",") if c.strip()]
        dallas_consts = [c for c in consts if c.startswith(DALLAS_FIPS)]
        if not dallas_consts:
            continue
        non_dallas = [c for c in consts if not c.startswith(DALLAS_FIPS)]
        if non_dallas:
            log(f"  WARNING: unit {x.get('geoid')} mixes Dallas + non-Dallas "
                f"tracts ({len(non_dallas)} non-Dallas) -- keeping Dallas "
                f"constituents only for the dissolve, rate is unaffected.")

        rate_raw = x.get("rate")
        rate = float(rate_raw) if rate_raw is not None else None
        ci_lo, ci_hi = parse_ci(x.get("rate_m_ci"))
        count_sup = x.get("count_sup")
        count_suppressed = (count_sup == "1-9")

        units.append({
            "unit_id": x.get("geoid"),
            "tract_geoids": dallas_consts,
            "n_tracts": len(dallas_consts),
            "od_death_rate_per_100k": rate,
            "ci_low": ci_lo,
            "ci_high": ci_hi,
            "count_suppressed": count_suppressed,
            "count_sup_raw": count_sup,
            "period": period_from_range(x.get("ttm_date_range", "")),
            "data_as_of": (x.get("data_as_of") or "")[:10],
        })

    log(f"  Dallas County (48113) aggregation units: {len(units)}")
    total_tracts = sum(u["n_tracts"] for u in units)
    unique_tracts = len({g for u in units for g in u["tract_geoids"]})
    log(f"  constituent tract references: {total_tracts} "
        f"(unique: {unique_tracts})")
    if total_tracts != unique_tracts:
        log("  WARNING: some tracts appear in >1 aggregation unit "
            "(overlap) -- dissolve will still work but units are not "
            "strictly partitioning.")
    return units


def load_dallas_tracts():
    log(f"\n=== Loading TIGER tracts: {TIGER_ZIP} ===")
    g = gpd.read_file(TIGER_ZIP)
    dal = g[(g["STATEFP"] == DALLAS_STATEFP) & (g["COUNTYFP"] == DALLAS_COUNTYFP)].copy()
    dal = dal.to_crs("EPSG:4326")
    dal["geoid"] = dal["GEOID"].astype(str)
    log(f"  TIGER Dallas County tracts: {len(dal)}")
    return dal


def build_units_geodataframe(units, tracts_gdf):
    log("\n=== Dissolving constituent tracts per aggregation unit ===")
    tracts_by_geoid = tracts_gdf.set_index("geoid")

    records = []
    missing_tract_refs = 0
    for u in units:
        geoids = u["tract_geoids"]
        present = [g for g in geoids if g in tracts_by_geoid.index]
        missing = [g for g in geoids if g not in tracts_by_geoid.index]
        if missing:
            missing_tract_refs += len(missing)
            log(f"  WARNING: unit {u['unit_id']} references tract(s) not "
                f"found in TIGER: {missing}")
        if not present:
            log(f"  ERROR: unit {u['unit_id']} has NO matching TIGER "
                f"geometry -- dropped.")
            continue

        sub = tracts_by_geoid.loc[present]
        geom = sub.geometry.union_all() if hasattr(sub.geometry, "union_all") \
            else sub.geometry.unary_union

        records.append({
            "unit_id": u["unit_id"],
            "od_death_rate_per_100k": u["od_death_rate_per_100k"],
            "ci_low": u["ci_low"],
            "ci_high": u["ci_high"],
            "count_suppressed": bool(u["count_suppressed"]),
            "n_tracts": u["n_tracts"],
            "n_tracts_matched": len(present),
            "period": u["period"],
            "source": SOURCE_LABEL,
            "data_as_of": u["data_as_of"],
            "geometry": geom,
        })

    if missing_tract_refs:
        log(f"  total missing tract refs across all units: {missing_tract_refs}")

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    log(f"  dissolved units: {len(gdf)}")
    return gdf


def fix_invalid_geometries(gdf):
    log("\n=== Validity check ===")
    invalid_before = (~gdf.geometry.is_valid).sum()
    log(f"  invalid geometries before fix: {invalid_before}")
    if invalid_before:
        bad_ids = gdf.loc[~gdf.geometry.is_valid, "unit_id"].tolist()
        log(f"  invalid unit_ids: {bad_ids}")
        gdf["geometry"] = gdf.geometry.buffer(0)
        invalid_after = (~gdf.geometry.is_valid).sum()
        log(f"  invalid geometries after buffer(0) fix: {invalid_after}")
        if invalid_after:
            still_bad = gdf.loc[~gdf.geometry.is_valid, "unit_id"].tolist()
            log(f"  WARNING: still invalid after buffer(0): {still_bad}")
    else:
        log("  all geometries valid -- no fix needed.")
    empty = gdf.geometry.is_empty.sum()
    if empty:
        log(f"  WARNING: {empty} empty geometries after fix")
    return gdf


def write_geojson(gdf, path):
    cols = ["unit_id", "od_death_rate_per_100k", "ci_low", "ci_high",
            "count_suppressed", "n_tracts", "period", "source",
            "data_as_of", "geometry"]
    out = gdf[cols].copy().reset_index(drop=True)
    tmp = path + ".tmp"
    out.to_file(tmp, driver="GeoJSON")
    os.replace(tmp, path)
    log(f"\n=== Wrote {path} ===")
    log(f"  features: {len(out)}")
    return out


def rate_quartiles(rates):
    if not rates:
        return None
    rs = sorted(rates)
    q = statistics.quantiles(rs, n=4, method="inclusive")
    return {
        "min": round(min(rs), 3),
        "q1": round(q[0], 3),
        "median": round(statistics.median(rs), 3),
        "q3": round(q[2], 3),
        "max": round(max(rs), 3),
        "mean": round(statistics.fmean(rs), 3),
        "n": len(rs),
    }


def main():
    os.makedirs(CLEAN, exist_ok=True)
    pulled_at = datetime.now(timezone.utc).isoformat()
    log(f"build_mortality_units.py -- {pulled_at}")

    snapshot = latest_raw_path("tract_drug_od")
    if not snapshot:
        raise SystemExit(
            f"No data/raw/tract_drug_od_*.json snapshot found in {RAW}; "
            "run scripts/pull_mortality.py first")

    units = load_raw_units(snapshot)
    tracts_gdf = load_dallas_tracts()
    gdf = build_units_geodataframe(units, tracts_gdf)
    gdf = fix_invalid_geometries(gdf)

    geojson_path = os.path.join(CLEAN, "mortality_units.geojson")
    out = write_geojson(gdf, geojson_path)

    rates = [r for r in out["od_death_rate_per_100k"].tolist() if r is not None]
    qs = rate_quartiles(rates)
    n_suppressed = int(out["count_suppressed"].sum())
    n_valid_geom = int(out.geometry.is_valid.sum())
    period = out["period"].mode().iat[0] if len(out) else None
    data_as_of = out["data_as_of"].mode().iat[0] if len(out) else None

    log("\n=== Summary ===")
    log(f"  unit count: {len(out)}")
    log(f"  valid geometries: {n_valid_geom}/{len(out)}")
    log(f"  count_suppressed units: {n_suppressed}")
    if qs:
        log(f"  rate quartiles (per 100k): min={qs['min']} q1={qs['q1']} "
            f"median={qs['median']} q3={qs['q3']} max={qs['max']} "
            f"mean={qs['mean']}")

    meta = {
        "module": "mortality_units",
        "pulled_at": pulled_at,
        "description": (
            "CDC drug-overdose death rates published at CDC's NATIVE "
            "reporting geography (4day-mt2f aggregation units), one feature "
            "per unit. Companion to tract_overdose.geojson, which explodes "
            "the same rates onto constituent census tracts for tract-level "
            "joins elsewhere in the pipeline and therefore visually smears "
            "variance across ~4.3 tracts per unit on average."
        ),
        "method_note": (
            "CDC's native reporting geography; rates are CDC's published "
            "model-based values, shown here without downscaling."
        ),
        "source": {
            "resource": "https://data.cdc.gov/resource/4day-mt2f.json",
            "dataset": "4day-mt2f",
            "raw_snapshot": os.path.relpath(snapshot, REPO),
            "intent": "Drug_OD",
            "period": period,
            "data_as_of": data_as_of,
        },
        "tract_geometry": {
            "resource": "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_48_tract.zip",
            "vintage": "TIGER/Line 2024 (2020 census tracts)",
            "county_fips": DALLAS_FIPS,
        },
        "outputs": {
            "mortality_units.geojson": {
                "features": len(out),
                "expected_features": EXPECTED_UNITS,
                "valid_geometries": n_valid_geom,
                "count_suppressed_units": n_suppressed,
                "rate_quartiles_per_100k": qs,
            }
        },
        "does_not_modify": "data/clean/tract_overdose.geojson",
    }
    meta_path = os.path.join(CLEAN, "mortality_units_meta.json")
    tmp = meta_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, meta_path)
    log(f"\n=== Wrote {meta_path} ===")


if __name__ == "__main__":
    main()
