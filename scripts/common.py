"""
common.py -- shared utilities for the Dallas overdose surveillance pipeline
(BUILT ENVIRONMENT + PLACEMENT CANDIDATES modules).

Provides:
  - Socrata pagination helper (with polite sleep, no app token)
  - Dallas County TIGER 2024 tract loader (atomic download + cache reuse)
  - small logging helpers

Run from repo root. Not a standalone script.
"""
import json
import os
import sys
import time
import urllib.request
import zipfile

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_RAW = os.path.join(REPO_ROOT, "data", "raw")
DATA_CLEAN = os.path.join(REPO_ROOT, "data", "clean")
TIGER_DIR = os.path.join(DATA_RAW, "tiger")

TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_48_tract.zip"
TIGER_ZIP = os.path.join(TIGER_DIR, "tl_2024_48_tract.zip")

DALLAS_COUNTYFP = "113"
DALLAS_STATEFP = "48"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_dirs():
    os.makedirs(DATA_RAW, exist_ok=True)
    os.makedirs(DATA_CLEAN, exist_ok=True)
    os.makedirs(TIGER_DIR, exist_ok=True)


def socrata_paginate(base_url, params=None, limit=50000, sleep_s=0.3, max_pages=None,
                      session=None, app_token=None):
    """
    Paginate a Socrata $limit/$offset endpoint. No app token by default -> stay
    under the anonymous throttle by sleeping between pages and using a modest
    page size (Socrata's own docs recommend <= 50000 per page; we use smaller
    below for safety on some endpoints). Returns a list of dict rows.
    """
    params = dict(params or {})
    sess = session or requests.Session()
    headers = {}
    if app_token:
        headers["X-App-Token"] = app_token
    rows = []
    offset = 0
    page = 0
    while True:
        page_params = dict(params)
        page_params["$limit"] = limit
        page_params["$offset"] = offset
        resp = sess.get(base_url, params=page_params, headers=headers, timeout=60)
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected Socrata response (not a list): {str(batch)[:300]}")
        rows.extend(batch)
        page += 1
        log(f"  ...page {page}: +{len(batch)} rows (total {len(rows)}) offset={offset}")
        if len(batch) < limit:
            break
        offset += limit
        if max_pages and page >= max_pages:
            log(f"  stopping at max_pages={max_pages}")
            break
        time.sleep(sleep_s)
    return rows


def atomic_download(url, dest_path, timeout=300):
    """Download url to dest_path atomically (tmp file + os.replace)."""
    tmp_path = dest_path + ".tmp"
    log(f"Downloading {url} -> {dest_path}")
    req = urllib.request.Request(url, headers={"User-Agent": "dallas-research-savespos/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp_path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp_path, dest_path)
    log(f"Downloaded {os.path.getsize(dest_path)} bytes")


def load_dallas_tracts():
    """
    Load Dallas County (COUNTYFP=113) census tracts from TIGER 2024.
    Reuses data/raw/tiger/tl_2024_48_tract.zip if present (other agents may
    download in parallel); otherwise downloads atomically.
    Returns a GeoDataFrame in EPSG:4326 with column 'geoid'.
    """
    import geopandas as gpd

    ensure_dirs()
    if not os.path.exists(TIGER_ZIP):
        atomic_download(TIGER_URL, TIGER_ZIP)
    else:
        log(f"Reusing existing {TIGER_ZIP}")

    gdf = gpd.read_file(TIGER_ZIP)
    gdf = gdf[(gdf["STATEFP"] == DALLAS_STATEFP) & (gdf["COUNTYFP"] == DALLAS_COUNTYFP)].copy()
    gdf = gdf.to_crs("EPSG:4326")
    gdf["geoid"] = gdf["GEOID"]
    log(f"Loaded {len(gdf)} Dallas County (COUNTYFP={DALLAS_COUNTYFP}) tracts from TIGER 2024")
    return gdf


def save_json(obj, path):
    ensure_dirs()
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
    log(f"Wrote {path} ({os.path.getsize(path):,} bytes)")


def dated_raw_path(name_prefix, date_str, ext="json"):
    return os.path.join(DATA_RAW, f"{name_prefix}_{date_str}.{ext}")
