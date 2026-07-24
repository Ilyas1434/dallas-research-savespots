#!/usr/bin/env python
"""
Pull Dallas County overdose mortality from CDC and build the tract layer.

Sources
  1. CDC VSRR provisional county drug-overdose deaths (Socrata gb4e-yj24),
     as a monthly 12-month-ending series for FIPS 48113.
  2. CDC drug-overdose death rates by census tract (4day-mt2f). Rows are
     model-based aggregation units whose `name` field lists their constituent
     tract GEOIDs; each unit's rate is exploded onto its Dallas tracts.
  3. TIGER/Line 2024 Texas tract geometries, filtered to COUNTYFP=113.

Dated raw snapshots go to data/raw/. Nothing is imputed; suppression is
recorded explicitly, and deaths_by_zip.csv is emitted header-only because no
public ZIP-level mortality product exists for Dallas.

Outputs (data/clean/): deaths_dallas.csv, tract_overdose.geojson,
deaths_by_zip.csv, deaths_by_zip.README.md, mortality_meta.json

Run: ./venv/bin/python scripts/pull_mortality.py
"""

import json
import os
import sys
import zipfile
from datetime import datetime, timezone

import requests
import geopandas as gpd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(REPO, "data", "raw")
RAW_TIGER = os.path.join(RAW, "tiger")
CLEAN = os.path.join(REPO, "data", "clean")

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
PULLED_AT = datetime.now(timezone.utc).isoformat()

VSRR_URL = "https://data.cdc.gov/resource/gb4e-yj24.json"
TRACT_URL = "https://data.cdc.gov/resource/4day-mt2f.json"
TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_48_tract.zip"
TIGER_ZIP = os.path.join(RAW_TIGER, "tl_2024_48_tract.zip")

DALLAS_FIPS = "48113"
DALLAS_COUNTYFP = "113"

MONTHS = {
    "January": "01", "February": "02", "March": "03", "April": "04",
    "May": "05", "June": "06", "July": "07", "August": "08",
    "September": "09", "October": "10", "November": "11", "December": "12",
}


def log(msg):
    print(msg, flush=True)


def ensure_dirs():
    for d in (RAW, RAW_TIGER, CLEAN):
        os.makedirs(d, exist_ok=True)


def pull_vsrr():
    log("\n=== Source 1: CDC VSRR provisional county OD deaths (gb4e-yj24) ===")
    params = {
        "$where": "state_name='Texas' and countyname='Dallas'",
        "$order": "monthendingdate ASC",
        "$limit": 5000,
    }
    r = requests.get(VSRR_URL, params=params, timeout=120)
    r.raise_for_status()
    rows = r.json()

    raw_path = os.path.join(RAW, f"vsrr_county_{TODAY}.json")
    with open(raw_path, "w") as f:
        json.dump(rows, f, indent=1)
    log(f"raw snapshot -> {raw_path} ({len(rows)} rows)")

    out_rows = []
    suppressed = 0
    for x in rows:
        me = x.get("monthendingdate", "")[:10]
        raw_count = x.get("provisional_drug_overdose")
        # Socrata omits suppressed or null numeric cells entirely.
        if raw_count is None:
            count = None
            is_supp = True
            suppressed += 1
        else:
            count = int(float(raw_count))
            is_supp = False
        pending = x.get("percentage_of_records_pending")
        pending = float(pending) if pending is not None else None
        pct_complete = round(100.0 * (1.0 - pending), 4) if pending is not None else None
        out_rows.append({
            "month_ending": me,
            "provisional_od_deaths_12mo_ending": count,
            "count_suppressed": is_supp,
            "pct_records_pending": None if pending is None else round(pending * 100.0, 6),
            "pct_records_complete": pct_complete,
            "data_as_of": x.get("data_as_of", "")[:10],
            "pulled_at": PULLED_AT,
        })

    out_rows.sort(key=lambda d: d["month_ending"])
    csv_path = os.path.join(CLEAN, "deaths_dallas.csv")
    _write_csv(csv_path, out_rows, [
        "month_ending", "provisional_od_deaths_12mo_ending", "count_suppressed",
        "pct_records_pending", "pct_records_complete", "data_as_of", "pulled_at",
    ])

    months = [r["month_ending"] for r in out_rows if r["month_ending"]]
    non_null = [r["provisional_od_deaths_12mo_ending"] for r in out_rows
                if r["provisional_od_deaths_12mo_ending"] is not None]
    latest = out_rows[-1] if out_rows else {}
    log(f"clean -> {csv_path}")
    log(f"  monthly rows: {len(out_rows)}")
    if months:
        log(f"  date range: {min(months)} .. {max(months)}")
    log(f"  suppressed count cells: {suppressed}")
    log(f"  latest month {latest.get('month_ending')}: "
        f"{latest.get('provisional_od_deaths_12mo_ending')} deaths "
        f"(12-mo-ending), {latest.get('pct_records_complete')}% complete")

    return {
        "row_count": len(out_rows),
        "date_min": min(months) if months else None,
        "date_max": max(months) if months else None,
        "suppressed_count_cells": suppressed,
        "latest_month": latest.get("month_ending"),
        "latest_deaths": latest.get("provisional_od_deaths_12mo_ending"),
        "data_as_of": latest.get("data_as_of"),
        "value_min": min(non_null) if non_null else None,
        "value_max": max(non_null) if non_null else None,
    }


def _period_from_range(ttm):
    # "January, 2025 to December, 2025" -> "2025-01/2025-12"
    try:
        left, right = [s.strip() for s in ttm.split(" to ")]
        lm, ly = [t.strip() for t in left.split(",")]
        rm, ry = [t.strip() for t in right.split(",")]
        return f"{ly}-{MONTHS[lm]}/{ry}-{MONTHS[rm]}"
    except Exception:
        return ttm


def pull_tract():
    log("\n=== Source 2: CDC drug-OD death rates by tract (4day-mt2f) ===")
    # period='TTM' selects the trailing-twelve-month vintage (the target
    # Jan-Dec 2025). The dataset also carries calendar-year rows (2022-2024)
    # for the same aggregation units; without this filter those vintages mix
    # in and the same tract appears under multiple periods.
    params = {
        "$where": "st_geoid='48' and intent='Drug_OD' and period='TTM'",
        "$limit": 50000,
    }
    r = requests.get(TRACT_URL, params=params, timeout=180)
    r.raise_for_status()
    rows = r.json()

    raw_path = os.path.join(RAW, f"tract_drug_od_{TODAY}.json")
    with open(raw_path, "w") as f:
        json.dump(rows, f, indent=1)
    log(f"raw snapshot -> {raw_path} ({len(rows)} TX agg-unit rows)")

    # Each unit's single model-based rate is exploded onto all its constituent
    # Dallas tracts, which is what the tract-level joins downstream expect.
    tract_map = {}
    units_with_dallas = 0
    period = None
    data_as_of = None
    dup = 0
    for x in rows:
        consts = [c.strip() for c in x.get("name", "").split(",") if c.strip()]
        dallas_consts = [c for c in consts if c.startswith(DALLAS_FIPS)]
        if not dallas_consts:
            continue
        units_with_dallas += 1
        if period is None:
            period = _period_from_range(x.get("ttm_date_range", ""))
            data_as_of = x.get("data_as_of", "")[:10]

        count_sup = x.get("count_sup")
        rate_raw = x.get("rate")
        # The live product publishes a rate for every unit, including small-count
        # ones (which carry a CI), so a rate counts as suppressed only if absent.
        rate = float(rate_raw) if rate_raw is not None else None
        rate_suppressed = rate is None
        count_suppressed = (count_sup == "1-9")

        for geoid in dallas_consts:
            if geoid in tract_map:
                dup += 1
            tract_map[geoid] = {
                "od_death_rate_per_100k": rate,
                "count_suppressed": count_suppressed,
                "rate_suppressed": rate_suppressed,
                "agg_geoid": x.get("geoid"),
            }

    log(f"  agg-units containing >=1 Dallas tract: {units_with_dallas}")
    log(f"  distinct Dallas 48113 tracts in CDC data: {len(tract_map)}")
    if dup:
        log(f"  WARNING: {dup} tract(s) appeared in >1 agg-unit (kept last)")
    log(f"  period: {period}")

    return tract_map, {
        "period": period,
        "data_as_of": data_as_of,
        "tx_agg_units": len(rows),
        "dallas_agg_units": units_with_dallas,
        "cdc_tracts": len(tract_map),
    }


def ensure_tiger():
    log("\n=== Source 3: TIGER/Line 2024 TX tract geometries ===")
    if os.path.exists(TIGER_ZIP) and _valid_zip(TIGER_ZIP):
        log(f"  reusing existing valid file -> {TIGER_ZIP}")
        return TIGER_ZIP
    tmp = os.path.join(RAW_TIGER, f".tmp_{os.getpid()}_tl_2024_48_tract.zip")
    log(f"  downloading {TIGER_URL}")
    with requests.get(TIGER_URL, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    if not _valid_zip(tmp):
        os.remove(tmp)
        raise RuntimeError("Downloaded TIGER zip failed validation")
    os.replace(tmp, TIGER_ZIP)  # atomic
    log(f"  saved -> {TIGER_ZIP} ({os.path.getsize(TIGER_ZIP) / 1e6:.1f} MB)")
    return TIGER_ZIP


def _valid_zip(path):
    try:
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None and any(
                n.endswith(".shp") for n in z.namelist())
    except Exception:
        return False


def build_geojson(tiger_zip, tract_map, tmeta):
    log("\n=== Build tract_overdose.geojson ===")
    g = gpd.read_file(tiger_zip)
    dal = g[g["COUNTYFP"] == DALLAS_COUNTYFP].copy()
    dal = dal.to_crs("EPSG:4326")  # WGS84
    log(f"  TIGER Dallas tracts (geometry): {len(dal)}")

    period = tmeta["period"]
    data_as_of = tmeta["data_as_of"]
    source = "CDC NCHS drug overdose death rates by census tract (4day-mt2f)"

    matched = 0
    missing = 0  # in TIGER but absent from CDC
    count_supp = 0
    rate_supp = 0
    rates_present = []

    props_rows = []
    for _, row in dal.iterrows():
        geoid = str(row["GEOID"])
        rec = tract_map.get(geoid)
        if rec is None:
            missing += 1
            od_rate = None
            csupp = False
            rsupp = True
        else:
            matched += 1
            od_rate = rec["od_death_rate_per_100k"]
            csupp = rec["count_suppressed"]
            rsupp = rec["rate_suppressed"]
            if od_rate is not None:
                rates_present.append(od_rate)
        if csupp:
            count_supp += 1
        if rsupp:
            rate_supp += 1
        props_rows.append({
            "geoid": geoid,
            "od_death_rate_per_100k": od_rate,
            "count_suppressed": bool(csupp),
            "rate_suppressed": bool(rsupp),
            "period": period,
            "source": source,
            "data_as_of": data_as_of,
        })

    out = dal[["geometry"]].copy().reset_index(drop=True)
    for key in ["geoid", "od_death_rate_per_100k", "count_suppressed",
                "rate_suppressed", "period", "source", "data_as_of"]:
        out[key] = [p[key] for p in props_rows]
    out = gpd.GeoDataFrame(out, geometry="geometry", crs="EPSG:4326")

    geojson_path = os.path.join(CLEAN, "tract_overdose.geojson")
    tmp = geojson_path + ".tmp"
    out.to_file(tmp, driver="GeoJSON")
    os.replace(tmp, geojson_path)

    cdc_only = set(tract_map) - set(dal["GEOID"].astype(str))
    log(f"  clean -> {geojson_path}")
    log(f"  features written: {len(out)}")
    log(f"  matched to CDC data: {matched}")
    log(f"  in TIGER but absent from CDC (null rate, rate_suppressed=true): {missing}")
    log(f"  in CDC but absent from TIGER (dropped, no geometry): {len(cdc_only)}")
    log(f"  count_suppressed features: {count_supp}")
    log(f"  rate_suppressed features: {rate_supp}")
    if rates_present:
        log(f"  rate range (per 100k): {min(rates_present):.2f} .. "
            f"{max(rates_present):.2f} (n={len(rates_present)})")

    return {
        "features": len(out),
        "matched": matched,
        "missing_from_cdc": missing,
        "cdc_not_in_tiger": len(cdc_only),
        "count_suppressed": count_supp,
        "rate_suppressed": rate_supp,
        "rate_min": min(rates_present) if rates_present else None,
        "rate_max": max(rates_present) if rates_present else None,
        "rates_present": len(rates_present),
    }


def write_zip_placeholder():
    log("\n=== deaths_by_zip.csv (honest placeholder) ===")
    csv_path = os.path.join(CLEAN, "deaths_by_zip.csv")
    # Header only. The tract product is finer than ZIP, and the one public
    # tract-to-ZIP crosswalk (HUD USPS) is gated behind signup, so no ZIP-level
    # mortality is produced rather than backing one into existence.
    header = ("zip,od_death_rate_per_100k,count_suppressed,rate_suppressed,"
              "period,source,data_as_of\n")
    with open(csv_path, "w") as f:
        f.write(header)

    readme_path = os.path.join(CLEAN, "deaths_by_zip.README.md")
    with open(readme_path, "w") as f:
        f.write(
            "# deaths_by_zip.csv — intentionally header-only\n\n"
            "ZIP/ZCTA-level overdose mortality **does not exist as a public "
            "product** for Dallas County.\n\n"
            "- DSHS / CDC VSRR publish provisional overdose deaths at the "
            "**county** resolution only (see `deaths_dallas.csv`).\n"
            "- The CDC NCHS tract product (`tract_overdose.geojson`) is "
            "**finer** than ZIP and is model-based on census-tract "
            "aggregation units.\n"
            "- Producing honest ZIP figures would require a "
            "population-weighted tract->ZCTA crosswalk. The HUD USPS "
            "tract-ZIP crosswalk requires signup (gated), and ACS "
            "block-level population weights are not part of this module's "
            "verified sources. We therefore do **not** fabricate ZIP numbers.\n\n"
            "## Resolution\n"
            "This file is emitted with a header row only. Any ZIP-level "
            "comparison in Phase 3.5 must be produced via a **tract-to-ZIP "
            "spatial join** using `tract_overdose.geojson` geometries, and "
            "clearly labeled as a spatial approximation of a tract product "
            "(not an independent ZIP mortality measurement).\n"
        )
    log(f"  clean -> {csv_path} (header only)")
    log(f"  sidecar -> {readme_path}")
    return {"csv": csv_path, "readme": readme_path, "rows": 0}


def _write_csv(path, rows, cols):
    import csv
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})
    os.replace(tmp, path)


def main():
    ensure_dirs()
    log(f"MORTALITY pull — {PULLED_AT}")

    vsrr_stats = pull_vsrr()
    tract_map, tmeta = pull_tract()
    tiger_zip = ensure_tiger()
    geo_stats = build_geojson(tiger_zip, tract_map, tmeta)
    zip_info = write_zip_placeholder()

    meta = {
        "module": "mortality",
        "pulled_at": PULLED_AT,
        "run_date": TODAY,
        "caveats": [
            "VSRR county counts are PROVISIONAL 12-month-ending totals; "
            "toxicology and death-certificate reporting lag materially "
            "affects the most recent ~6 months (counts revise upward).",
            "Tract rates are MODEL-BASED (spatially smoothed) rates per "
            "100,000 on census-tract aggregation units; a single modeled "
            "rate is applied to every constituent tract of an aggregation "
            "unit.",
            "Live 4day-mt2f product provides rates for all units (with "
            "margins/CIs) rather than suppressing small-numerator rates; "
            "rate_suppressed is therefore true only for TIGER tracts absent "
            "from the CDC product.",
            "Counts of 1-9 are suppressed per NCHS confidentiality "
            "(count_suppressed flag); rates are still provided for those.",
            "ZIP-level mortality is not a public product; deaths_by_zip.csv "
            "is header-only by design (see sidecar README).",
        ],
        "sources": {
            "vsrr_county": {
                "resource": VSRR_URL,
                "dataset": "gb4e-yj24",
                "data_as_of": vsrr_stats["data_as_of"],
                "row_count": vsrr_stats["row_count"],
                "date_range": [vsrr_stats["date_min"], vsrr_stats["date_max"]],
                "latest_month": vsrr_stats["latest_month"],
                "latest_deaths_12mo_ending": vsrr_stats["latest_deaths"],
                "value_range": [vsrr_stats["value_min"], vsrr_stats["value_max"]],
                "suppressed_count_cells": vsrr_stats["suppressed_count_cells"],
            },
            "tract_rates": {
                "resource": TRACT_URL,
                "dataset": "4day-mt2f",
                "intent": "Drug_OD",
                "data_as_of": tmeta["data_as_of"],
                "period": tmeta["period"],
                "tx_agg_units": tmeta["tx_agg_units"],
                "dallas_agg_units": tmeta["dallas_agg_units"],
                "cdc_dallas_tracts": tmeta["cdc_tracts"],
            },
            "tract_geometry": {
                "resource": TIGER_URL,
                "vintage": "TIGER/Line 2024 (2020 census tracts)",
                "county_fips": DALLAS_FIPS,
            },
        },
        "outputs": {
            "deaths_dallas.csv": {
                "rows": vsrr_stats["row_count"],
                "date_range": [vsrr_stats["date_min"], vsrr_stats["date_max"]],
            },
            "tract_overdose.geojson": {
                "features": geo_stats["features"],
                "matched_to_cdc": geo_stats["matched"],
                "in_tiger_absent_from_cdc": geo_stats["missing_from_cdc"],
                "in_cdc_absent_from_tiger": geo_stats["cdc_not_in_tiger"],
                "count_suppressed_features": geo_stats["count_suppressed"],
                "rate_suppressed_features": geo_stats["rate_suppressed"],
                "rate_range_per_100k": [geo_stats["rate_min"], geo_stats["rate_max"]],
                "rates_present": geo_stats["rates_present"],
                "period": tmeta["period"],
            },
            "deaths_by_zip.csv": {
                "rows": zip_info["rows"],
                "note": "header-only by design; see deaths_by_zip.README.md",
            },
        },
        "vintages": {
            "vsrr_data_as_of": vsrr_stats["data_as_of"],
            "tract_data_as_of": tmeta["data_as_of"],
            "tiger": "2024",
        },
        "lag_note": (
            "Provisional data. Most recent ~6 monthly points are subject to "
            "upward revision due to toxicology/reporting lag."
        ),
        "suppressed_tract_count": geo_stats["count_suppressed"],
        "row_counts": {
            "deaths_dallas": vsrr_stats["row_count"],
            "tract_overdose_features": geo_stats["features"],
            "deaths_by_zip": zip_info["rows"],
        },
    }
    meta_path = os.path.join(CLEAN, "mortality_meta.json")
    tmp = meta_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, meta_path)
    log(f"\nmeta -> {meta_path}")
    log("\nDONE.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}")
        sys.exit(1)
