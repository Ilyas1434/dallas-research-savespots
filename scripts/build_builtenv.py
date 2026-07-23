#!/usr/bin/env python
"""
build_builtenv.py -- BUILT ENVIRONMENT module.

Builds a per-census-tract index of blight/disorder signals for Dallas County,
to be combined later (by another module) into a composite naloxone-box
placement score. Signals:

  1. 311 blight-relevant service requests (Dallas Open Data d7e7-envw),
     last 24 months (2024-07-23 .. 2026-07-23), categories: illegal_dumping,
     graffiti, litter. These are the CURRENT, actively-updated signal
     (dataset spans Oct 2020 - present).

  2. Code Compliance "Code Violations" nuisance records (Dallas Open Data
     x9pz-kdq9), categories: high_weeds, litter, illegal_dumping,
     junk_motor_vehicle, illegal_outside_storage, bulky_trash,
     graffiti_private_property.

     *** IMPORTANT / PING ***: this dataset is a frozen ARCHIVE. Its own
     Socrata metadata shows rowsUpdatedAt = 2019-02-06 and max(created) =
     2018-07-31. It has NOT been updated in ~8 years and contains ZERO
     records in the last-24-months window. We therefore report it
     separately as "violations_alltime_2013_2018" (lifetime totals over its
     actual coverage, 2013-10-01 .. 2018-07-31) rather than pretending it
     covers "last 24 months." This is flagged prominently in the output
     meta.caveats and must be flagged to the user (PING).

  3. Vacancy PROXY (heuristic, documented): Dallas has no vacant-structure
     registry. We proxy likely-vacant/neglected commercial addresses as
     addresses in the (stale, pre-2018) code-violations dataset that have
     BOTH a High Weeds violation AND (an Illegal Dumping OR Junk Motor
     Vehicle violation) on record. Because the source data stops in 2018,
     this proxy reflects historical neglect patterns only, not current
     vacancy -- documented as such.

Output: data/clean/builtenv_index.json
  { "meta": {...}, "tracts": { GEOID: {...per-tract counts...} } }

Run: ./venv/bin/python scripts/build_builtenv.py
"""
import datetime
import json
import os
import sys

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (
    REPO_ROOT, DATA_RAW, DATA_CLEAN, log, ensure_dirs, socrata_paginate,
    save_json, dated_raw_path, load_dallas_tracts,
)

TODAY = datetime.date(2026, 7, 23)
WINDOW_START_311 = TODAY - datetime.timedelta(days=730)  # ~24 months
DATE_STR = TODAY.isoformat()

URL_311 = "https://www.dallasopendata.com/resource/d7e7-envw.json"
URL_VIOLATIONS = "https://www.dallasopendata.com/resource/x9pz-kdq9.json"

# ---- 311 blight-relevant service_request_type -> category mapping --------
TYPES_311 = {
    "illegal_dumping": [
        "Illegal Dumping Sign - CCS",
        "Illegal Dumping Urgent - OEQS",
        "Illegal Dumping Sign - CDS",
        "Illegal Dumping Urgent - DWU",
        "Illegal Dumping Sign - MSH",
        "illegal dumping abatement",
        "illegal dump abatement",
    ],
    "graffiti": [
        "Graffiti City Streets & Bridges - PBW",
        "Graffiti Traffic Signal - TRN",
        "Graffiti City Streets & Bridges - TPW",
        "Graffiti Traffic Sign - TRN",
        "Graffiti Traffic Signal - TPW",
        "Graffiti Traffic Sign - TPW",
    ],
    "litter": [
        "Clean Sweep Litter Removal - TPW",
        "Sanitation Litter Cans - SAN",
    ],
}
ALL_311_TYPES = [t for v in TYPES_311.values() for t in v]

# NOTE excluded: "Code Concern - CCS" (683k lifetime records, largest single
# 311 type) -- excluded because it is an undifferentiated catch-all bucket
# for Code Compliance issues (covers everything from garage-sale tracking to
# structural complaints) with no sub-type field to isolate blight signal.
# Logged here as a caveat, not silently dropped.

# ---- Code-violations nuisance -> category mapping -------------------------
NUISANCE_MAP = {
    "high_weeds": ["High Weeds - CCS"],
    "litter": ["Litter -  CCS"],
    "illegal_dumping": ["Illegal Dumping - CCS"],
    "junk_motor_vehicle": ["Junk Motor Vehicle - CCS"],
    "illegal_outside_storage": ["Illegal Outside Storage - CCS"],
    "bulky_trash": ["Bulky Trash Violations - CCS"],
    "graffiti_private_property": [
        "Graffiti Private Property - Residential/Commercial - CCS",
        "Graffiti Abatement Request - CCS",
    ],
}
ALL_NUISANCE_TYPES = [t for v in NUISANCE_MAP.values() for t in v]

VACANCY_PROXY_WEEDS = set(NUISANCE_MAP["high_weeds"])
VACANCY_PROXY_TRIGGER = set(NUISANCE_MAP["illegal_dumping"] + NUISANCE_MAP["junk_motor_vehicle"])


def fetch_311():
    """Pull blight-relevant 311 requests in the last 24 months, server-side filtered."""
    quoted_types = ",".join(f"'{t}'" for t in ALL_311_TYPES)
    where = (
        f"service_request_type in({quoted_types}) "
        f"AND created_date >= '{WINDOW_START_311.isoformat()}T00:00:00.000'"
    )
    params = {
        "$select": "service_request_type,created_date,lat_location",
        "$where": where,
    }
    log(f"Fetching 311 blight requests since {WINDOW_START_311} ...")
    rows = socrata_paginate(URL_311, params=params, limit=50000, sleep_s=0.4)
    log(f"311: fetched {len(rows)} rows in window {WINDOW_START_311}..{TODAY}")
    raw_path = dated_raw_path("dallas311_blight", DATE_STR)
    save_json(rows, raw_path)
    return rows


def fetch_violations():
    """Pull ALL code-violations nuisance rows relevant to blight categories (full history;
    dataset is frozen, see module docstring)."""
    quoted_types = ",".join(f"'{t}'" for t in ALL_NUISANCE_TYPES)
    where = f"nuisance in({quoted_types})"
    params = {
        "$select": "service_request_id,str_num,str_prefix,str_nam,str_suffix,zone,"
                   "nuisance,status,created,x_value,y_value",
        "$where": where,
    }
    log("Fetching code-violations nuisance records (full history, dataset is a frozen archive)...")
    rows = socrata_paginate(URL_VIOLATIONS, params=params, limit=50000, sleep_s=0.4)
    log(f"Code violations: fetched {len(rows)} rows")
    raw_path = dated_raw_path("dallas_code_violations", DATE_STR)
    save_json(rows, raw_path)
    return rows


def parse_lat_location(s):
    if not s:
        return None, None
    s = s.strip().strip("()")
    try:
        lat_str, lon_str = s.split(",")
        return float(lat_str), float(lon_str)
    except Exception:
        return None, None


def build_311_gdf(rows):
    recs = []
    n_missing_geo = 0
    for r in rows:
        loc = r.get("lat_location")
        lat, lon = parse_lat_location(loc)
        if lat is None:
            n_missing_geo += 1
            continue
        cat = None
        srt = r.get("service_request_type")
        for c, types in TYPES_311.items():
            if srt in types:
                cat = c
                break
        recs.append({"category": cat, "lat": lat, "lon": lon, "created_date": r.get("created_date")})
    log(f"311: {n_missing_geo} rows missing lat_location out of {len(rows)} "
        f"({0 if not rows else 100*n_missing_geo/len(rows):.1f}%)")
    df = pd.DataFrame(recs)
    if df.empty:
        return gpd.GeoDataFrame(df, geometry=[], crs="EPSG:4326")
    geom = [Point(xy) for xy in zip(df["lon"], df["lat"])]
    gdf = gpd.GeoDataFrame(df, geometry=geom, crs="EPSG:4326")
    return gdf


def build_violations_gdf(rows):
    transformer = Transformer.from_crs("EPSG:2276", "EPSG:4326", always_xy=True)
    recs = []
    n_missing_xy = 0
    for r in rows:
        xv, yv = r.get("x_value"), r.get("y_value")
        if xv in (None, "") or yv in (None, ""):
            n_missing_xy += 1
            continue
        try:
            x, y = float(xv), float(yv)
        except (TypeError, ValueError):
            n_missing_xy += 1
            continue
        lon, lat = transformer.transform(x, y)
        cat = None
        nuis = r.get("nuisance")
        for c, types in NUISANCE_MAP.items():
            if nuis in types:
                cat = c
                break
        addr = " ".join(
            str(r.get(k) or "").strip()
            for k in ("str_num", "str_prefix", "str_nam", "str_suffix")
        ).strip()
        addr_key = f"{addr}|{r.get('zone','')}".upper()
        recs.append({
            "category": cat, "lat": lat, "lon": lon,
            "created": r.get("created"), "addr_key": addr_key,
        })
    log(f"Code violations: {n_missing_xy} rows missing x_value/y_value out of {len(rows)} "
        f"({0 if not rows else 100*n_missing_xy/len(rows):.1f}%)")
    df = pd.DataFrame(recs)
    if df.empty:
        return gpd.GeoDataFrame(df, geometry=[], crs="EPSG:4326")
    geom = [Point(xy) for xy in zip(df["lon"], df["lat"])]
    gdf = gpd.GeoDataFrame(df, geometry=geom, crs="EPSG:4326")
    return gdf


def spatial_join_tract(points_gdf, tracts_gdf):
    if points_gdf.empty:
        out = points_gdf.copy()
        out["geoid"] = None
        return out
    joined = gpd.sjoin(points_gdf, tracts_gdf[["geoid", "geometry"]], how="left", predicate="within")
    n_unmatched = joined["geoid"].isna().sum()
    log(f"  spatial join: {n_unmatched}/{len(joined)} points did not fall inside any Dallas tract "
        f"({100*n_unmatched/len(joined):.1f}%) -- likely just outside county line / bad coords")
    return joined


def compute_vacancy_proxy(violations_joined):
    """Addresses with BOTH a high-weeds violation AND (illegal dumping OR junk vehicle)."""
    df = violations_joined.dropna(subset=["addr_key"])
    if df.empty:
        return pd.DataFrame(columns=["addr_key", "geoid"])
    has_weeds = df[df["category"] == "high_weeds"][["addr_key", "geoid"]].drop_duplicates()
    trigger_cats = {"illegal_dumping", "junk_motor_vehicle"}
    has_trigger = df[df["category"].isin(trigger_cats)][["addr_key", "geoid"]].drop_duplicates()
    merged = has_weeds.merge(has_trigger, on="addr_key", suffixes=("", "_trig"))
    merged["geoid"] = merged["geoid"].fillna(merged["geoid_trig"])
    return merged[["addr_key", "geoid"]].drop_duplicates()


def main():
    ensure_dirs()
    log("=== BUILT ENVIRONMENT build starting ===")

    tracts = load_dallas_tracts()

    rows_311 = fetch_311()
    rows_viol = fetch_violations()

    gdf_311 = build_311_gdf(rows_311)
    gdf_viol = build_violations_gdf(rows_viol)

    log("Spatial join: 311 points -> tracts")
    j311 = spatial_join_tract(gdf_311, tracts)
    log("Spatial join: code-violations points -> tracts")
    jviol = spatial_join_tract(gdf_viol, tracts)

    vacancy = compute_vacancy_proxy(jviol)
    vacancy_by_tract = vacancy.groupby("geoid").size().to_dict()

    # per-tract 311 counts (24mo window) by category
    tract_out = {}
    for geoid in tracts["geoid"]:
        tract_out[geoid] = {
            "blight_311_24mo": 0,
            "blight_311_24mo_by_category": {c: 0 for c in TYPES_311},
            "violations_alltime_2013_2018": 0,
            "violations_alltime_2013_2018_by_category": {c: 0 for c in NUISANCE_MAP},
            "vacancy_proxy_addresses": int(vacancy_by_tract.get(geoid, 0)),
        }

    if not j311.empty:
        g = j311.dropna(subset=["geoid"])
        for geoid, sub in g.groupby("geoid"):
            if geoid not in tract_out:
                continue
            tract_out[geoid]["blight_311_24mo"] = int(len(sub))
            vc = sub["category"].value_counts().to_dict()
            for c in TYPES_311:
                tract_out[geoid]["blight_311_24mo_by_category"][c] = int(vc.get(c, 0))

    if not jviol.empty:
        g = jviol.dropna(subset=["geoid"])
        for geoid, sub in g.groupby("geoid"):
            if geoid not in tract_out:
                continue
            tract_out[geoid]["violations_alltime_2013_2018"] = int(len(sub))
            vc = sub["category"].value_counts().to_dict()
            for c in NUISANCE_MAP:
                tract_out[geoid]["violations_alltime_2013_2018_by_category"][c] = int(vc.get(c, 0))

    total_311_matched = sum(t["blight_311_24mo"] for t in tract_out.values())
    total_viol_matched = sum(t["violations_alltime_2013_2018"] for t in tract_out.values())
    total_vacancy = sum(t["vacancy_proxy_addresses"] for t in tract_out.values())

    meta = {
        "generated": datetime.datetime.now().isoformat(),
        "tract_source": "TIGER 2024 tl_2024_48_tract.zip, COUNTYFP=113 (Dallas County), n_tracts=%d" % len(tracts),
        "sources": {
            "311": {
                "endpoint": URL_311,
                "coverage": "Oct 2020 - present (live, actively updated)",
                "window_used": f"{WINDOW_START_311.isoformat()} to {TODAY.isoformat()} (last 24 months)",
                "categories": {c: v for c, v in TYPES_311.items()},
                "rows_fetched": len(rows_311),
                "rows_matched_to_tract": total_311_matched,
                "excluded_note": "'Code Concern - CCS' (683k lifetime records, largest 311 type) "
                                  "excluded: undifferentiated catch-all with no sub-type field, "
                                  "cannot be isolated as a specific blight category.",
            },
            "code_violations": {
                "endpoint": URL_VIOLATIONS,
                "coverage_actual": "2013-10-01 to 2018-07-31 ONLY",
                "rows_fetched": len(rows_viol),
                "rows_matched_to_tract": total_viol_matched,
                "categories": {c: v for c, v in NUISANCE_MAP.items()},
            },
        },
        "vacancy_proxy": {
            "method": "Addresses (from the code-violations archive) with BOTH a High Weeds "
                      "violation AND (Illegal Dumping OR Junk Motor Vehicle) violation on record, "
                      "matched by normalized street-number+street-name+zip key.",
            "total_addresses_flagged": int(total_vacancy),
            "caveat": "HEURISTIC. Dallas has no vacant-structure registry. Because the underlying "
                      "code-violations dataset stops in 2018, this proxy reflects HISTORICAL "
                      "(pre-2018) co-occurrence patterns, not current vacancy. Use with caution.",
        },
        "caveats": [
            "PING: dallasopendata.com resource x9pz-kdq9 ('Code Violations') is a frozen archive "
            "-- Socrata metadata shows rowsUpdatedAt=2019-02-06T18:05:23 and max(created)="
            "2018-07-31T12:56:00. It contains ZERO records in the last-24-months window "
            "(2024-07-23..2026-07-23) specified in the build spec. We report its contents as "
            "'violations_alltime_2013_2018' (lifetime totals over its actual 2013-2018 coverage) "
            "instead of fabricating a 24-month figure. Current-window blight signal for Dallas "
            "therefore relies on the 311 dataset (d7e7-envw), which IS live through 2026-07-22.",
            "Graffiti categories differ slightly between sources: 311 graffiti requests are "
            "almost all on PUBLIC infrastructure (streets/bridges/traffic signs/signals), while "
            "the code-violations 'graffiti_private_property' category (private/commercial "
            "property, archived pre-2018) is a distinct signal not blended together.",
        ],
    }

    out = {"meta": meta, "tracts": tract_out}
    out_path = os.path.join(DATA_CLEAN, "builtenv_index.json")
    save_json(out, out_path)

    log("=== SUMMARY ===")
    log(f"Tracts: {len(tracts)}")
    log(f"311 rows fetched: {len(rows_311)} | matched to tract: {total_311_matched}")
    log(f"Code-violation rows fetched: {len(rows_viol)} | matched to tract: {total_viol_matched}")
    log(f"Vacancy-proxy addresses flagged: {total_vacancy}")
    log("=== BUILT ENVIRONMENT build complete ===")


if __name__ == "__main__":
    main()
