"""Shared paths, HTTP helpers, and TIGER geometry loading for the pipeline."""
import glob
import json
import os
import time
import urllib.request
from datetime import date

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_RAW = os.path.join(REPO_ROOT, "data", "raw")
DATA_CLEAN = os.path.join(REPO_ROOT, "data", "clean")
TIGER_DIR = os.path.join(DATA_RAW, "tiger")

TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_48_tract.zip"
TIGER_ZIP = os.path.join(TIGER_DIR, "tl_2024_48_tract.zip")

DALLAS_STATEFP = "48"
DALLAS_COUNTYFP = "113"
DALLAS_FIPS = DALLAS_STATEFP + DALLAS_COUNTYFP

USER_AGENT = "dallas-naloxone-access/1.0 (research pipeline)"


def pipeline_date():
    """Run date for snapshot filenames and 'generated' stamps.

    Defaults to today. Set PIPELINE_DATE=YYYY-MM-DD to re-create an archived
    run exactly (e.g. to reproduce the figures published with the manuscript).
    """
    override = os.environ.get("PIPELINE_DATE")
    if override:
        return date.fromisoformat(override.strip())
    return date.today()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_dirs():
    for d in (DATA_RAW, DATA_CLEAN, TIGER_DIR):
        os.makedirs(d, exist_ok=True)


def dated_raw_path(name_prefix, date_str, ext="json"):
    return os.path.join(DATA_RAW, f"{name_prefix}_{date_str}.{ext}")


def latest_raw_path(name_prefix, ext="json", subdir=None):
    """Newest dated snapshot for a prefix, or None. Filenames sort as dates do."""
    base = os.path.join(DATA_RAW, subdir) if subdir else DATA_RAW
    matches = sorted(glob.glob(os.path.join(base, f"{name_prefix}_*.{ext}")))
    return matches[-1] if matches else None


def socrata_paginate(base_url, params=None, limit=50000, sleep_s=0.3, max_pages=None,
                     session=None, app_token=None):
    """Page a Socrata $limit/$offset endpoint. No app token by default, so page
    size stays modest and each page is followed by a sleep to respect the
    anonymous throttle."""
    params = dict(params or {})
    sess = session or requests.Session()
    headers = {"X-App-Token": app_token} if app_token else {}
    rows = []
    offset = 0
    page = 0
    while True:
        page_params = dict(params, **{"$limit": limit, "$offset": offset})
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
    tmp_path = dest_path + ".tmp"
    log(f"Downloading {url} -> {dest_path}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp_path, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp_path, dest_path)
    log(f"Downloaded {os.path.getsize(dest_path)} bytes")


def load_dallas_tracts():
    """Dallas County tracts from TIGER/Line 2024, EPSG:4326, with a 'geoid' column."""
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
    log(f"Loaded {len(gdf)} Dallas County tracts from TIGER/Line 2024")
    return gdf


def save_json(obj, path):
    ensure_dirs()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)
    log(f"Wrote {path} ({os.path.getsize(path):,} bytes)")
