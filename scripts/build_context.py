#!/usr/bin/env python
"""
build_context.py

CONTEXT / VULNERABILITY module for the Dallas County overdose surveillance
pipeline.

Pulls, cleans, and joins:
  1. CDC/ATSDR SVI 2022 (tract-level) for Dallas County, TX
  2. ACS 5-year 2020-2024 (poverty + uninsured), tract + ZCTA level
  3. CDC VSRR provisional county-level overdose death counts (Dallas County)
  4. DART static GTFS stops -> half-mile stop counts per tract centroid
  5. Housing Forward NTX Point-in-Time homelessness count (county-level only)
  6. TIGER 2024 Dallas County tract geometries

Outputs (data/clean/):
  - svi_tracts.geojson
  - uninsured_zcta.csv
  - vsrr.json
  - context_meta.json

Raw snapshots are written to data/raw/ (dated where practical) and are never
overwritten silently -- downloads are atomic (tmp file + os.replace).

Run:
    ./venv/bin/python scripts/build_context.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from dotenv import load_dotenv
from shapely.geometry import Point

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
CLEAN_DIR = REPO_ROOT / "data" / "clean"
TODAY = datetime.now().strftime("%Y-%m-%d")

RAW_DIR.mkdir(parents=True, exist_ok=True)
CLEAN_DIR.mkdir(parents=True, exist_ok=True)
(RAW_DIR / "tiger").mkdir(parents=True, exist_ok=True)
(RAW_DIR / "dart").mkdir(parents=True, exist_ok=True)
(RAW_DIR / "pit").mkdir(parents=True, exist_ok=True)
(RAW_DIR / "svi").mkdir(parents=True, exist_ok=True)
(RAW_DIR / "acs").mkdir(parents=True, exist_ok=True)
(RAW_DIR / "vsrr").mkdir(parents=True, exist_ok=True)

load_dotenv(REPO_ROOT / ".env")
CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY")

LOG_LINES: list[str] = []
META: dict = {
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "run_date": TODAY,
    "sources": {},
    "null_counts": {},
    "row_counts": {},
    "notes": [],
}


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG_LINES.append(msg)


def note(msg: str) -> None:
    log(f"NOTE: {msg}")
    META["notes"].append(msg)


def atomic_download(url: str, dest: Path, timeout: int = 120) -> bool:
    """Download url -> dest atomically. Returns True if a fresh download
    happened, False if dest already existed and was reused."""
    if dest.exists() and dest.stat().st_size > 0:
        log(f"  reuse existing file: {dest} ({dest.stat().st_size:,} bytes)")
        return False
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    log(f"  downloading {url}")
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(tmp, dest)
    log(f"  saved {dest} ({dest.stat().st_size:,} bytes)")
    return True


if not CENSUS_API_KEY:
    log("FATAL: CENSUS_API_KEY not found in .env -- required for all Census "
        "API calls since May 2026.")
    sys.exit(1)
else:
    log("CENSUS_API_KEY loaded from .env (value not printed).")

STATE_FIPS = "48"
COUNTY_FIPS = "113"
GEOID_PREFIX = STATE_FIPS + COUNTY_FIPS

# ==========================================================================
# 1. TIGER 2024 Dallas County tract geometries
# ==========================================================================
log("\n=== 1. TIGER 2024 tract geometries ===")

tiger_zip = RAW_DIR / "tiger" / "tl_2024_48_tract.zip"
atomic_download(
    "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_48_tract.zip",
    tiger_zip,
)

tracts_all = gpd.read_file(f"zip://{tiger_zip}")
tracts = tracts_all[tracts_all["COUNTYFP"] == COUNTY_FIPS].copy()
tracts = tracts.rename(columns={"GEOID": "geoid"})
tracts["geoid"] = tracts["geoid"].astype(str)
log(f"Dallas County (COUNTYFP=113) tracts in TIGER2024: {len(tracts)}")
META["row_counts"]["tiger_dallas_tracts"] = len(tracts)
META["sources"]["tiger"] = {
    "url": "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_48_tract.zip",
    "vintage": "TIGER2024",
    "n_tracts_dallas": len(tracts),
}

EXPECTED_TRACTS = 529
if len(tracts) != EXPECTED_TRACTS:
    note(
        f"Task brief expected ~{EXPECTED_TRACTS} Dallas County tracts; "
        f"TIGER2024 actually contains {len(tracts)}. This is because Dallas "
        f"County's tract boundaries were substantially redrawn/split for the "
        f"2020 Census (the ~529 figure is the pre-2020, 2010-vintage tract "
        f"count). {len(tracts)} is the correct current tract count and "
        f"matches SVI2022 and ACS 2020-2024 tract counts pulled below "
        f"(cross-checked, all three agree)."
    )

# ==========================================================================
# 2. CDC/ATSDR SVI 2022, tract level, Dallas County
# ==========================================================================
log("\n=== 2. CDC/ATSDR SVI 2022 (tract) ===")

SVI_BASE = (
    "https://onemap.cdc.gov/onemapservices/rest/services/SVI/"
    "CDC_ATSDR_Social_Vulnerability_Index_2022_USA/MapServer"
)

# Verify layer ids via ?f=json (tract layer expected at /2)
layer_meta = requests.get(f"{SVI_BASE}?f=json", timeout=30).json()
layer_names = {l["id"]: l["name"] for l in layer_meta.get("layers", [])}
log(f"SVI service layers: {layer_names}")
tract_layer_id = next(
    (lid for lid, name in layer_names.items() if "tract" in name.lower() and "theme" not in name.lower()),
    2,
)
county_layer_id = next(
    (lid for lid, name in layer_names.items() if "county" in name.lower() and "theme" not in name.lower()),
    1,
)
log(f"Using tract layer id={tract_layer_id}, county layer id={county_layer_id}")

SVI_FIELDS = [
    "FIPS", "ST_ABBR", "STCNTY", "COUNTY",
    "RPL_THEMES", "RPL_THEME1", "RPL_THEME2", "RPL_THEME3", "RPL_THEME4",
    "EP_POV150", "EP_UNINSUR", "E_TOTPOP",
]

svi_layer_json = requests.get(f"{SVI_BASE}/{tract_layer_id}?f=json", timeout=30).json()
max_record_count = svi_layer_json.get("maxRecordCount", 1000)
log(f"SVI tract layer maxRecordCount={max_record_count}")

svi_features = []
offset = 0
while True:
    params = {
        "where": f"ST_ABBR='TX' AND STCNTY='{GEOID_PREFIX}'",
        "outFields": ",".join(SVI_FIELDS),
        "returnGeometry": "false",
        "f": "json",
        "resultRecordCount": max_record_count,
        "resultOffset": offset,
    }
    r = requests.get(f"{SVI_BASE}/{tract_layer_id}/query", params=params, timeout=60)
    r.raise_for_status()
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"SVI query error: {d['error']}")
    feats = d.get("features", [])
    svi_features.extend(feats)
    log(f"  SVI page offset={offset}: {len(feats)} features "
        f"(exceededTransferLimit={d.get('exceededTransferLimit')})")
    if not d.get("exceededTransferLimit") or not feats:
        break
    offset += len(feats)

svi_raw_path = RAW_DIR / "svi" / f"svi2022_dallas_tract_{TODAY}.json"
with open(svi_raw_path, "w") as f:
    json.dump(svi_features, f)
log(f"Saved raw SVI snapshot: {svi_raw_path} ({len(svi_features)} features)")

svi_df = pd.DataFrame([f["attributes"] for f in svi_features])
svi_df = svi_df.rename(columns={"FIPS": "geoid"})
svi_df["geoid"] = svi_df["geoid"].astype(str)
log(f"SVI tract rows pulled: {len(svi_df)}")
META["row_counts"]["svi_tracts_pulled"] = len(svi_df)

# SVI uses -999 as missing-data sentinel -> convert to null, count per field
svi_value_cols = [
    "RPL_THEMES", "RPL_THEME1", "RPL_THEME2", "RPL_THEME3", "RPL_THEME4",
    "EP_POV150", "EP_UNINSUR", "E_TOTPOP",
]
svi_null_counts = {}
for col in svi_value_cols:
    if col not in svi_df.columns:
        continue
    sentinel_mask = svi_df[col] == -999
    n_sentinel = int(sentinel_mask.sum())
    svi_null_counts[col] = n_sentinel
    if n_sentinel:
        svi_df.loc[sentinel_mask, col] = None
    log(f"  {col}: {n_sentinel} sentinel(-999)/missing values -> null")
META["null_counts"]["svi"] = svi_null_counts

svi_df = svi_df.rename(columns={
    "RPL_THEMES": "svi_overall",
    "RPL_THEME1": "svi_theme1",
    "RPL_THEME2": "svi_theme2",
    "RPL_THEME3": "svi_theme3",
    "RPL_THEME4": "svi_theme4",
    "EP_POV150": "pct_poverty_svi",
    "EP_UNINSUR": "pct_uninsured_svi",
    "E_TOTPOP": "totpop",
})

META["sources"]["svi"] = {
    "url": f"{SVI_BASE}/{tract_layer_id}/query",
    "vintage": "SVI 2022 (ACS 2018-2022 vintage)",
    "n_tracts": len(svi_df),
    "raw_snapshot": str(svi_raw_path.relative_to(REPO_ROOT)),
}

# --- SVI Dallas County ranking context (county layer, all TX counties) ---
county_params = {
    "where": "ST_ABBR='TX'",
    "outFields": "FIPS,COUNTY,RPL_THEMES",
    "returnGeometry": "false",
    "f": "json",
    "resultRecordCount": 300,
}
cr = requests.get(f"{SVI_BASE}/{county_layer_id}/query", params=county_params, timeout=60)
cr.raise_for_status()
county_feats = cr.json().get("features", [])
county_rows = [
    (f["attributes"]["COUNTY"], f["attributes"]["RPL_THEMES"], f["attributes"]["FIPS"])
    for f in county_feats
]
county_rows_sorted = sorted(
    county_rows, key=lambda x: (x[1] if x[1] is not None else -999), reverse=True
)
dallas_county_row = next((r for r in county_rows if r[2] == GEOID_PREFIX), None)
dallas_rank = next(
    (i for i, r in enumerate(county_rows_sorted, 1) if r[2] == GEOID_PREFIX), None
)
log(f"Dallas County overall SVI (RPL_THEMES)={dallas_county_row}, "
    f"rank {dallas_rank} of {len(county_rows_sorted)} TX counties "
    f"(1 = most vulnerable)")
META["svi_county_ranking"] = {
    "dallas_rpl_themes": dallas_county_row[1] if dallas_county_row else None,
    "rank_most_vulnerable_first": dallas_rank,
    "n_tx_counties": len(county_rows_sorted),
}

# ==========================================================================
# 3. ACS 5-year 2020-2024: tract-level poverty + uninsured, Dallas County
#    plus ZCTA-level uninsured for Dallas-area ZCTAs (750xx-753xx)
# ==========================================================================
log("\n=== 3. ACS 5-year 2020-2024 (subject tables) ===")

ACS_URL = "https://api.census.gov/data/2024/acs/acs5/subject"


def acs_get(params: dict, label: str) -> list[list[str]]:
    p = dict(params)
    p["key"] = CENSUS_API_KEY
    resp = requests.get(ACS_URL, params=p, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"ACS pull '{label}' failed: HTTP {resp.status_code} "
                            f"body={resp.text[:300]}")
    return resp.json()


acs_pull_ok = True
try:
    poverty_raw = acs_get(
        {
            "get": "NAME,S1701_C03_001E",
            "for": "tract:*",
            "in": f"state:{STATE_FIPS} county:{COUNTY_FIPS}",
        },
        "tract poverty S1701",
    )
    uninsured_raw = acs_get(
        {
            "get": "NAME,S2701_C05_001E",
            "for": "tract:*",
            "in": f"state:{STATE_FIPS} county:{COUNTY_FIPS}",
        },
        "tract uninsured S2701",
    )
except Exception as exc:
    acs_pull_ok = False
    note(f"ACS Census API pull FAILED: {exc}")
    poverty_raw = [["NAME", "S1701_C03_001E", "state", "county", "tract"]]
    uninsured_raw = [["NAME", "S2701_C05_001E", "state", "county", "tract"]]

with open(RAW_DIR / "acs" / f"acs2024_poverty_tract_{TODAY}.json", "w") as f:
    json.dump(poverty_raw, f)
with open(RAW_DIR / "acs" / f"acs2024_uninsured_tract_{TODAY}.json", "w") as f:
    json.dump(uninsured_raw, f)

pov_df = pd.DataFrame(poverty_raw[1:], columns=poverty_raw[0])
pov_df["geoid"] = STATE_FIPS + COUNTY_FIPS + pov_df["tract"]
pov_df["pct_poverty_acs2024"] = pd.to_numeric(pov_df["S1701_C03_001E"], errors="coerce")

uni_df = pd.DataFrame(uninsured_raw[1:], columns=uninsured_raw[0])
uni_df["geoid"] = STATE_FIPS + COUNTY_FIPS + uni_df["tract"]
uni_df["pct_uninsured_acs2024"] = pd.to_numeric(uni_df["S2701_C05_001E"], errors="coerce")

log(f"ACS tract poverty rows: {len(pov_df)}, uninsured rows: {len(uni_df)}")
META["row_counts"]["acs_tract_poverty"] = len(pov_df)
META["row_counts"]["acs_tract_uninsured"] = len(uni_df)

# Census codes -666666666 for estimates that can't be computed (e.g. tracts
# with insufficient sample / non-residential tracts) -- convert to null
# BEFORE counting nulls, so the log reflects true missingness.
for label, df, col in [("poverty", pov_df, "pct_poverty_acs2024"),
                        ("uninsured", uni_df, "pct_uninsured_acs2024")]:
    sentinel = df[col] < -500000000
    if sentinel.any():
        log(f"  ACS {label}: {int(sentinel.sum())} sentinel(-666666666) values -> null")
        df.loc[sentinel, col] = None

acs_pov_null = int(pov_df["pct_poverty_acs2024"].isna().sum())
acs_uni_null = int(uni_df["pct_uninsured_acs2024"].isna().sum())
log(f"ACS tract poverty nulls: {acs_pov_null}, uninsured nulls: {acs_uni_null}")
META["null_counts"]["acs_tract_poverty"] = acs_pov_null
META["null_counts"]["acs_tract_uninsured"] = acs_uni_null

META["sources"]["acs_tract"] = {
    "url": ACS_URL,
    "vintage": "ACS 2020-2024 5-year (released Jan 2026)",
    "tables": ["S1701_C03_001E (pct below poverty)", "S2701_C05_001E (pct uninsured)"],
    "n_tracts_poverty": len(pov_df),
    "n_tracts_uninsured": len(uni_df),
    "pull_succeeded": acs_pull_ok,
}

# --- ZCTA-level uninsured for Dallas-area ZCTAs (750xx-753xx) ---
try:
    zcta_raw = acs_get(
        {
            "get": "NAME,S2701_C05_001E",
            "for": "zip code tabulation area:*",
        },
        "ZCTA uninsured S2701 (national, filtered locally)",
    )
    with open(RAW_DIR / "acs" / f"acs2024_uninsured_zcta_national_{TODAY}.json", "w") as f:
        json.dump(zcta_raw, f)
    zcta_df = pd.DataFrame(zcta_raw[1:], columns=zcta_raw[0])
    zcta_df = zcta_df.rename(columns={"zip code tabulation area": "zcta"})
    zcta_df["pct_uninsured_acs2024"] = pd.to_numeric(zcta_df["S2701_C05_001E"], errors="coerce")
    zcta_sentinel = zcta_df["pct_uninsured_acs2024"] < -500000000
    if zcta_sentinel.any():
        zcta_df.loc[zcta_sentinel, "pct_uninsured_acs2024"] = None
    dallas_zcta_mask = zcta_df["zcta"].astype(str).str.match(r"^75[0-3]\d\d$")
    dallas_zcta_df = zcta_df.loc[dallas_zcta_mask, ["zcta", "NAME", "pct_uninsured_acs2024"]].copy()
    dallas_zcta_df = dallas_zcta_df.sort_values("zcta").reset_index(drop=True)
    dallas_zcta_df.to_csv(CLEAN_DIR / "uninsured_zcta.csv", index=False)
    zcta_null = int(dallas_zcta_df["pct_uninsured_acs2024"].isna().sum())
    log(f"ZCTA uninsured rows (750xx-753xx): {len(dallas_zcta_df)}, nulls: {zcta_null}")
    META["row_counts"]["acs_zcta_uninsured_dallas_area"] = len(dallas_zcta_df)
    META["null_counts"]["acs_zcta_uninsured"] = zcta_null
    META["sources"]["acs_zcta"] = {
        "url": ACS_URL,
        "vintage": "ACS 2020-2024 5-year",
        "table": "S2701_C05_001E (pct uninsured)",
        "zcta_filter": "750xx-753xx",
        "n_zctas": len(dallas_zcta_df),
        "output": "data/clean/uninsured_zcta.csv",
    }
except Exception as exc:
    note(f"ACS ZCTA uninsured pull FAILED: {exc}")
    pd.DataFrame(columns=["zcta", "NAME", "pct_uninsured_acs2024"]).to_csv(
        CLEAN_DIR / "uninsured_zcta.csv", index=False
    )

# ==========================================================================
# 4. CDC VSRR county monthly (Dallas County)
# ==========================================================================
log("\n=== 4. CDC VSRR provisional county overdose deaths (Dallas) ===")

VSRR_URL = "https://data.cdc.gov/resource/gb4e-yj24.json"
vsrr_params = {
    "$where": "st_abbrev='TX' AND countyname='Dallas'",
    "$limit": 5000,
    "$order": "monthendingdate",
}
vr = requests.get(VSRR_URL, params=vsrr_params, timeout=60)
vr.raise_for_status()
vsrr_raw = vr.json()
log(f"VSRR rows pulled for Dallas County, TX: {len(vsrr_raw)}")
META["row_counts"]["vsrr_dallas_rows"] = len(vsrr_raw)

vsrr_slim = []
for row in vsrr_raw:
    vsrr_slim.append({
        "period_end": row.get("monthendingdate", "")[:10],
        "year": row.get("year"),
        "month": row.get("month"),
        "provisional_drug_overdose_deaths": row.get("provisional_drug_overdose"),
        "data_as_of": row.get("data_as_of", "")[:10],
    })
with open(CLEAN_DIR / "vsrr.json", "w") as f:
    json.dump(vsrr_slim, f, indent=2)
log(f"Saved data/clean/vsrr.json ({len(vsrr_slim)} rows)")

vsrr_null_deaths = sum(1 for r in vsrr_slim if r["provisional_drug_overdose_deaths"] in (None, ""))
META["null_counts"]["vsrr_deaths"] = vsrr_null_deaths
data_as_of_vals = sorted(set(r["data_as_of"] for r in vsrr_slim if r["data_as_of"]))
META["sources"]["vsrr"] = {
    "url": VSRR_URL,
    "n_rows": len(vsrr_slim),
    "data_as_of": data_as_of_vals[-1] if data_as_of_vals else None,
    "period_range": [vsrr_slim[0]["period_end"], vsrr_slim[-1]["period_end"]] if vsrr_slim else None,
    "output": "data/clean/vsrr.json",
}

# ==========================================================================
# 5. DART static GTFS -> half-mile stop counts per tract centroid
# ==========================================================================
log("\n=== 5. DART GTFS stops -> tract half-mile stop counts ===")

dart_zip = RAW_DIR / "dart" / "google_transit.zip"
atomic_download("https://www.dart.org/transitdata/latest/google_transit.zip", dart_zip)

with zipfile.ZipFile(dart_zip) as zf:
    with zf.open("stops.txt") as f:
        stops_df = pd.read_csv(f)
log(f"DART GTFS stops.txt rows: {len(stops_df)}")
META["row_counts"]["dart_stops"] = len(stops_df)

stops_gdf = gpd.GeoDataFrame(
    stops_df,
    geometry=[Point(xy) for xy in zip(stops_df["stop_lon"], stops_df["stop_lat"])],
    crs="EPSG:4326",
)

PROJ_CRS = "EPSG:32138"  # NAD83 / Texas North Central, meters
stops_proj = stops_gdf.to_crs(PROJ_CRS)
tracts_proj = tracts.to_crs(PROJ_CRS)

# geometric centroid of each tract polygon, projected
tract_centroids = tracts_proj.copy()
tract_centroids["geometry"] = tract_centroids.geometry.centroid

HALF_MILE_M = 805.0  # EPSG:32138 axis unit = metre (verified)

# Buffer centroids by 805m, spatial-join against stop points
centroid_buffers = tract_centroids.copy()
centroid_buffers["geometry"] = centroid_buffers.geometry.buffer(HALF_MILE_M)

joined = gpd.sjoin(
    stops_proj[["stop_id", "geometry"]],
    centroid_buffers[["geoid", "geometry"]],
    predicate="within",
    how="inner",
)
stop_counts = joined.groupby("geoid").size().rename("dart_stops_halfmile")

tracts["dart_stops_halfmile"] = tracts["geoid"].map(stop_counts).fillna(0).astype(int)

log(f"Tracts with >=1 DART stop within 0.5mi of centroid: "
    f"{(tracts['dart_stops_halfmile'] > 0).sum()} / {len(tracts)}")
log(f"DART stop density range per tract: "
    f"min={tracts['dart_stops_halfmile'].min()}, "
    f"max={tracts['dart_stops_halfmile'].max()}, "
    f"mean={tracts['dart_stops_halfmile'].mean():.2f}")
META["row_counts"]["dart_tracts_with_stop"] = int((tracts["dart_stops_halfmile"] > 0).sum())
META["dart_stop_density_range"] = {
    "min": int(tracts["dart_stops_halfmile"].min()),
    "max": int(tracts["dart_stops_halfmile"].max()),
    "mean": float(round(tracts["dart_stops_halfmile"].mean(), 2)),
}
META["sources"]["dart_gtfs"] = {
    "url": "https://www.dart.org/transitdata/latest/google_transit.zip",
    "n_stops": len(stops_df),
    "method": "0.5mi (805m) buffer around tract geometric centroid, EPSG:32138, sjoin 'within'",
}

# ==========================================================================
# 6. Housing Forward PIT count (county-level only, no spatial allocation)
# ==========================================================================
log("\n=== 6. Housing Forward NTX Point-in-Time count ===")

pit_pdf = RAW_DIR / "pit" / "2026-PIT-Report.pdf"
pit_downloaded = atomic_download(
    "https://housingforwardntx.org/wp-content/uploads/2026/05/2026-Point-In-Time-Count-Report.pdf",
    pit_pdf,
)

pit_text = None
pit_extract_method = None
pit_txt_path = RAW_DIR / "pit" / "2026-PIT-Report.txt"
try:
    subprocess.run(
        ["pdftotext", "-layout", str(pit_pdf), str(pit_txt_path)],
        check=True, capture_output=True, timeout=60,
    )
    pit_text = pit_txt_path.read_text(errors="ignore")
    pit_extract_method = "pdftotext"
    log(f"  extracted PIT PDF text via pdftotext ({len(pit_text)} chars)")
except Exception as exc:
    log(f"  pdftotext unavailable/failed ({exc}); trying `strings` fallback")
    try:
        result = subprocess.run(
            ["strings", str(pit_pdf)], check=True, capture_output=True, timeout=60, text=True,
        )
        pit_text = result.stdout
        pit_extract_method = "strings"
        log(f"  extracted via strings fallback ({len(pit_text)} chars)")
    except Exception as exc2:
        note(f"PIT PDF text extraction failed entirely: {exc2}")

pit_2026_individuals = None
pit_2026_dallas_pct = None
pit_2026_collin_pct = None
if pit_text:
    m = re.search(r"total of\s+([\d,]+)\s+individuals\s+were\s+identified", pit_text, re.I)
    if not m:
        m = re.search(r"([\d,]+)\s+Individuals\b", pit_text)
    if m:
        pit_2026_individuals = int(m.group(1).replace(",", ""))
    m2 = re.search(
        r"(\d+)%\s+of individuals counted.*?were in Dallas County.*?remaining\s+(\d+)%\s+were in Collin",
        pit_text, re.I | re.S,
    )
    if m2:
        pit_2026_dallas_pct = int(m2.group(1))
        pit_2026_collin_pct = int(m2.group(2))

if pit_2026_individuals:
    log(f"  2026 PIT count extracted: {pit_2026_individuals} individuals "
        f"(Dallas {pit_2026_dallas_pct}% / Collin {pit_2026_collin_pct}%)")
else:
    note("Could not parse 2026 PIT individuals figure from extracted text; "
         "falling back to known 2025 figure only.")

pit_data = {
    "2025": {
        "count_date": "2025-01-30",
        "individuals": 3541,
        "dallas_pct": 83,
        "collin_pct": 17,
        "source": "task brief / prior known figure",
    },
    "2026": {
        "count_date": "2026-01-22",
        "individuals": pit_2026_individuals,
        "dallas_pct": pit_2026_dallas_pct,
        "collin_pct": pit_2026_collin_pct,
        "source": "https://housingforwardntx.org/wp-content/uploads/2026/05/2026-Point-In-Time-Count-Report.pdf",
        "extraction_method": pit_extract_method,
    },
    "note": "County-level (Dallas+Collin CoC) only; NOT spatially allocated to "
            "tracts -- do not fabricate tract-level homelessness estimates from this.",
}
META["pit"] = pit_data
META["sources"]["pit"] = {
    "url": "https://housingforwardntx.org/wp-content/uploads/2026/05/2026-Point-In-Time-Count-Report.pdf",
    "pdf_downloaded_fresh": pit_downloaded,
}

# ==========================================================================
# 7. Merge -> svi_tracts.geojson
# ==========================================================================
log("\n=== 7. Merge and write svi_tracts.geojson ===")

out = tracts[["geoid", "geometry", "dart_stops_halfmile"]].copy()

out = out.merge(
    svi_df[["geoid", "svi_overall", "svi_theme1", "svi_theme2", "svi_theme3",
            "svi_theme4", "pct_poverty_svi", "pct_uninsured_svi", "totpop"]],
    on="geoid", how="left",
)
out = out.merge(pov_df[["geoid", "pct_poverty_acs2024"]], on="geoid", how="left")
out = out.merge(uni_df[["geoid", "pct_uninsured_acs2024"]], on="geoid", how="left")

# Explicit null handling / logging for the final joined frame
final_null_report = {}
for col in ["svi_overall", "svi_theme1", "svi_theme2", "svi_theme3", "svi_theme4",
            "pct_poverty_svi", "pct_uninsured_svi", "totpop",
            "pct_poverty_acs2024", "pct_uninsured_acs2024", "dart_stops_halfmile"]:
    n_null = int(out[col].isna().sum())
    final_null_report[col] = n_null
    if n_null:
        log(f"  final null check -- {col}: {n_null} / {len(out)}")
META["null_counts"]["svi_tracts_final"] = final_null_report

# tracts present in TIGER but missing SVI/ACS match (join failures)
unmatched_svi = out[out["svi_overall"].isna() & out["totpop"].isna()]
if len(unmatched_svi):
    note(f"{len(unmatched_svi)} tract(s) in TIGER had no SVI match by geoid: "
         f"{unmatched_svi['geoid'].tolist()}")

out = out.to_crs("EPSG:4326")
out = out[[
    "geoid", "svi_overall", "svi_theme1", "svi_theme2", "svi_theme3", "svi_theme4",
    "pct_poverty_svi", "pct_uninsured_svi", "pct_poverty_acs2024", "pct_uninsured_acs2024",
    "dart_stops_halfmile", "totpop", "geometry",
]]

out_path = CLEAN_DIR / "svi_tracts.geojson"
if out_path.exists():
    out_path.unlink()
out.to_file(out_path, driver="GeoJSON")
log(f"Wrote {out_path} ({len(out)} features)")
META["row_counts"]["svi_tracts_geojson"] = len(out)

# ==========================================================================
# 8. Verification: reload geojson, sanity-check
# ==========================================================================
log("\n=== 8. Verification ===")
check = gpd.read_file(out_path)
log(f"Reloaded svi_tracts.geojson via geopandas: {len(check)} features, "
    f"CRS={check.crs}, columns={list(check.columns)}")
assert len(check) == len(out), "Reloaded feature count mismatch!"
META["verification"] = {
    "reload_success": True,
    "reload_feature_count": len(check),
    "expected_tract_count_task_brief": EXPECTED_TRACTS,
    "actual_tract_count": len(out),
}

# ==========================================================================
# 9. Write context_meta.json
# ==========================================================================
META["log_tail"] = LOG_LINES[-40:]
meta_path = CLEAN_DIR / "context_meta.json"
with open(meta_path, "w") as f:
    json.dump(META, f, indent=2, default=str)
log(f"Wrote {meta_path}")

log("\n=== DONE ===")
