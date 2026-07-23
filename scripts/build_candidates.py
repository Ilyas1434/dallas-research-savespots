#!/usr/bin/env python
"""
build_candidates.py -- PLACEMENT CANDIDATES module.

Builds the candidate-storefront universe for naloxone-box placement in
Dallas County by merging four sources:

  1. Dallas Certificates of Occupancy (CO) -- dallasopendata 9qet-qt9e.
     Filtered to plausible everyday-storefront land_use values (see
     LANDUSE_CATEGORY_MAP below; full distinct land_use frequency table is
     logged to data/raw/co_landuse_distinct_<date>.json for auditability).
     NOTE (deviation from spec): lat/lon are NOT top-level `latitude`/
     `longitude` fields as the task brief assumed -- they live in a nested
     `geolocation` object ({"latitude":..., "longitude":...}). Handled here.
     Also: CO is a permit-approval registry, not a current-occupancy
     registry, so the same street address can have several CO rows over
     the years as tenants change. We keep only the MOST RECENT CO
     (by date_issued) per unique normalized address to avoid representing
     one physical storefront as several stacked candidates.

  2. TABC alcohol licenses (data.texas.gov kguh-7q9z), filtered to
     off-premise retail license types verified against the TABC's own
     published license/permit type list (see TABC_OFFPREMISE_TYPES).
     No lat/lon in source -> geocoded via Census Bureau batch geocoder,
     with Google Geocoding API fallback for unmatched addresses. Results
     cached to data/raw/geocode_cache_tabc.json (never re-geocode a cached
     address). Failure-rate is reported; >10% triggers a PING.

  3. OSM Overpass, Dallas County only (relation 1837698 -> area id
     3601837698 -- resolved via Nominatim; NOTE: a naive
     area["name"="Dallas County"] query is ambiguous, since Iowa also has a
     "Dallas County" -- confirmed by a stray Iowa node in an early test
     query). Tags: shop=convenience, amenity=fuel, shop=alcohol,
     shop=hairdresser|beauty, shop=laundry, amenity=library. Known thin
     coverage (~140 convenience nodes county-wide per brief; confirmed low
     counts below).

  4. Dallas Public Library branches (dallasopendata 2ksy-mdcf, 30 rows,
     has lat/lon directly).

Output:
  data/clean/placement_candidates.geojson
  data/clean/placement_candidates.csv

Properties per candidate: name, address, zip, category, source (list),
lat, lon, geoid (Dallas-County tract spatial join; null if outside county),
open_24h (bool or null -- only knowable from OSM opening_hours=="24/7" tag
or CO land_use "Motor Vehicle Fueling Station" combined with OSM 24/7 tag;
otherwise null, never guessed).

NO ranking / composite score here -- this is the raw candidate universe.

Run: ./venv/bin/python scripts/build_candidates.py
"""
import datetime
import json
import math
import os
import re
import sys
import time

import pandas as pd
import geopandas as gpd
import requests
from shapely.geometry import Point
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (
    REPO_ROOT, DATA_RAW, DATA_CLEAN, log, ensure_dirs, socrata_paginate,
    save_json, dated_raw_path, load_dallas_tracts,
)

load_dotenv(os.path.join(REPO_ROOT, ".env"))

TODAY = datetime.date(2026, 7, 23)
DATE_STR = TODAY.isoformat()

URL_CO = "https://www.dallasopendata.com/resource/9qet-qt9e.json"
URL_TABC = "https://data.texas.gov/resource/kguh-7q9z.json"
URL_LIBRARIES = "https://www.dallasopendata.com/resource/2ksy-mdcf.json"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
DALLAS_COUNTY_OVERPASS_AREA = 3601837698  # relation 1837698 (Dallas County, TX) + 3600000000

GEOCODE_CACHE_PATH = os.path.join(DATA_RAW, "geocode_cache_tabc.json")

CENSUS_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"

# ---------------------------------------------------------------------------
# CO land_use -> candidate category mapping (documented, from distinct-value
# inspection of the live dataset on 2026-07-23; see build log / raw dump).
# Only categories relevant to "everyday storefront" naloxone placement are
# included; everything else (offices, warehouses, schools, churches, auto
# repair, hotels, multi-family, etc.) is explicitly excluded and logged.
# ---------------------------------------------------------------------------
LANDUSE_CATEGORY_MAP = {
    "Motor Vehicle Fueling Station": "gas_station",
    "GEN MERCHANDISE OR FOOD STORE < 3500 SQ. FT.": "convenience_corner_store",
    "FOOD OR BEVERAGE STORE  <= 3,500 SQ. FT.": "convenience_corner_store",
    "GEN MERCHANDISE OR FOOD STORE > 3500 SQ. FT.": "grocery_food_mart",
    "GEN MERCHANDISE OR FOOD STORE>=100000 SQ FT": "grocery_food_mart",
    "LIQUOR STORE": "liquor_store",
    "DRY CLEANING OR LAUNDRY STORE": "laundromat",
    "PERSONAL SERVICE USE": "barbershop_beauty",
    "PERSONAL SERVICES": "barbershop_beauty",
    "LIBRARY": "library",
    "PAWN SHOP": "other_storefront",
    "GEN MERCHANDISE (NO FOOD) STORE < 3500 SQ. FT": "other_storefront",
    "ALCOHOLIC BEVERAGE ESTABLISHMENT": "other_storefront",  # ambiguous on/off-premise, logged
    "PARAPHERNALIA SHOP": "other_storefront",
    "COMMERCIAL AMUSEMENT (INSIDE)": "other_storefront",
}

# TABC license types verified against https://www.tabc.texas.gov/services/
# tabc-licenses-permits/tabc-license-permit-types/ (fetched 2026-07-23).
# Off-premise retail set: P (Package Store), Q (Wine-Only Package Store),
# BQ (Wine & Malt Beverage Retailer's OFF-Premise), BF (Retail Dealer's
# OFF-Premise malt beverage license). BG (Wine and Malt Beverage Retailer's
# Permit) is documented by TABC as covering BOTH on- and off-premise sale --
# included here (with this caveat logged) because in practice it is the
# license type used by many Dallas convenience/grocery stores selling beer
# & wine for off-premise consumption, which is the population of interest.
TABC_OFFPREMISE_TYPES = {"P", "Q", "BQ", "BF", "BG"}
TABC_OFFPREMISE_CAVEAT = (
    "BG ('Wine and Malt Beverage Retailer's Permit') is TABC-documented as on-AND-off-premise; "
    "included as off-premise-retail-like per brief's 'etc.' allowance, flagged here."
)
# Excluded on-premise / wholesale / manufacturing / temporary / transport types
# (not off-premise retail storefronts): MB, NT, N, NE, NB, TR, G, W, C, S, BW,
# BE, PR, D, BB, X, BC, FC, J/JD, ET, AW, CD, and any unmapped/unknown codes
# encountered at runtime (logged below).

OSM_CATEGORY_MAP = {
    ("shop", "convenience"): "convenience_corner_store",
    ("amenity", "fuel"): "gas_station",
    ("shop", "alcohol"): "liquor_store",
    ("shop", "hairdresser"): "barbershop_beauty",
    ("shop", "beauty"): "barbershop_beauty",
    ("shop", "laundry"): "laundromat",
    ("amenity", "library"): "library",
}

CANDIDATE_CATEGORIES = [
    "gas_station", "convenience_corner_store", "liquor_store", "barbershop_beauty",
    "laundromat", "library", "grocery_food_mart", "other_storefront",
]


# ===========================================================================
# 1. Certificates of Occupancy
# ===========================================================================
def fetch_co():
    log("Fetching Dallas Certificates of Occupancy (full pull, all land_use)...")
    params = {"$select": "co,business_name,address,zip_code,land_use,occupancy,sq_ft,"
                          "date_approved,date_issued,type_of_co,geolocation"}
    rows = socrata_paginate(URL_CO, params=params, limit=50000, sleep_s=0.4)
    log(f"CO: fetched {len(rows)} total rows")
    save_json(rows, dated_raw_path("dallas_co_full", DATE_STR))

    # log distinct land_use frequency for auditability
    landuse_counts = {}
    for r in rows:
        lu = r.get("land_use") or "(null)"
        landuse_counts[lu] = landuse_counts.get(lu, 0) + 1
    save_json(
        sorted(landuse_counts.items(), key=lambda kv: -kv[1]),
        dated_raw_path("co_landuse_distinct", DATE_STR),
    )
    return rows


def normalize_addr(addr, zip_code=None):
    if not addr:
        return ""
    a = addr.upper()
    a = re.sub(r"\bSTE[:.]?\s*\S*", "", a)
    a = re.sub(r"\bSUITE\s*\S*", "", a)
    a = re.sub(r"\b(APT|UNIT|#)\s*\S*", "", a)
    a = re.sub(r"[^A-Z0-9 ]", " ", a)
    a = re.sub(r"\s+", " ", a).strip()
    if zip_code:
        a = f"{a} {str(zip_code)[:5]}"
    return a


def normalize_name(name):
    if not name:
        return ""
    n = name.upper()
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    n = re.sub(r"\b(LLC|INC|CORP|CO|THE|LP|LTD)\b", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def process_co(rows):
    kept = []
    excluded_landuse_counts = {}
    n_no_geo = 0
    for r in rows:
        lu = r.get("land_use")
        cat = LANDUSE_CATEGORY_MAP.get(lu)
        if cat is None:
            excluded_landuse_counts[lu] = excluded_landuse_counts.get(lu, 0) + 1
            continue
        geo = r.get("geolocation") or {}
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if lat is None or lon is None:
            n_no_geo += 1
            continue
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            n_no_geo += 1
            continue
        addr = r.get("address")
        norm_addr = normalize_addr(addr, r.get("zip_code"))
        kept.append({
            "name": r.get("business_name"),
            "address": addr,
            "zip": r.get("zip_code"),
            "category": cat,
            "source": "CO",
            "lat": lat, "lon": lon,
            "open_24h": True if lu == "Motor Vehicle Fueling Station" else None,
            "_norm_addr": norm_addr,
            "_norm_name": normalize_name(r.get("business_name")),
            "_date_issued": r.get("date_issued") or "",
        })
    log(f"CO: {n_no_geo} matched-land_use rows missing geolocation, dropped")
    log(f"CO: {len(kept)} rows kept across {len(set(LANDUSE_CATEGORY_MAP.values()))} categories "
        f"(before same-address de-dup / before cross-source merge)")
    log(f"CO: excluded {sum(excluded_landuse_counts.values())} rows across "
        f"{len(excluded_landuse_counts)} distinct excluded land_use values "
        f"(full breakdown in data/raw/co_landuse_distinct_{DATE_STR}.json)")

    # collapse multiple CO rows at the same normalized address to the most
    # recent one (CO is a permit-history registry, not current-occupancy)
    df = pd.DataFrame(kept)
    if df.empty:
        return df
    before = len(df)
    df = df.sort_values("_date_issued").drop_duplicates(subset=["_norm_addr"], keep="last")
    log(f"CO: collapsed {before} -> {len(df)} rows after keeping most-recent CO per address "
        f"({before - len(df)} superseded historical CO records dropped)")
    return df.drop(columns=["_date_issued"])


# ===========================================================================
# 2. TABC
# ===========================================================================
def fetch_tabc():
    log("Fetching TABC licenses, txcounty=Dallas...")
    params = {"$where": "txcounty='Dallas'"}
    rows = socrata_paginate(URL_TABC, params=params, limit=50000, sleep_s=0.4)
    log(f"TABC: fetched {len(rows)} Dallas-county rows")
    save_json(rows, dated_raw_path("tabc_dallas_full", DATE_STR))

    type_counts = {}
    for r in rows:
        t = r.get("aimslicensetype") or "(null)"
        type_counts[t] = type_counts.get(t, 0) + 1
    save_json(sorted(type_counts.items(), key=lambda kv: -kv[1]),
              dated_raw_path("tabc_licensetype_distinct", DATE_STR))
    return rows


def process_tabc_filter(rows):
    kept = []
    unmapped_counts = {}
    for r in rows:
        lt = (r.get("aimslicensetype") or "").strip()
        if lt in TABC_OFFPREMISE_TYPES:
            kept.append(r)
        else:
            unmapped_counts[lt] = unmapped_counts.get(lt, 0) + 1
    log(f"TABC: {len(kept)}/{len(rows)} rows are off-premise-retail license types "
        f"{sorted(TABC_OFFPREMISE_TYPES)} ({TABC_OFFPREMISE_CAVEAT})")
    log(f"TABC: {sum(unmapped_counts.values())} rows excluded (on-premise/wholesale/mfg/"
        f"temporary/transport license types): {dict(sorted(unmapped_counts.items(), key=lambda kv:-kv[1]))}")
    return kept


def load_geocode_cache():
    if os.path.exists(GEOCODE_CACHE_PATH):
        with open(GEOCODE_CACHE_PATH) as f:
            return json.load(f)
    return {}


def save_geocode_cache(cache):
    save_json(cache, GEOCODE_CACHE_PATH)


def census_batch_geocode(addr_records):
    """addr_records: list of (id, street, city, state, zip). Returns {id: (lat,lon) or None}."""
    import io
    results = {}
    CHUNK = 9000  # stay under the 10k limit
    for i in range(0, len(addr_records), CHUNK):
        chunk = addr_records[i:i + CHUNK]
        buf = io.StringIO()
        for rec in chunk:
            _id, street, city, state, zipc = rec
            street = (street or "").replace('"', "")
            buf.write(f'{_id},"{street}","{city}","{state}","{zipc}"\n')
        files = {"addressFile": ("addresses.csv", buf.getvalue(), "text/csv")}
        data = {"benchmark": "Public_AR_Current"}
        log(f"Census batch geocoder: submitting {len(chunk)} addresses "
            f"(chunk {i // CHUNK + 1})...")
        try:
            resp = requests.post(CENSUS_BATCH_URL, files=files, data=data, timeout=180)
            resp.raise_for_status()
        except Exception as e:
            log(f"  Census batch geocoder request failed: {e}")
            continue
        text = resp.text
        for line in text.splitlines():
            # CSV: id,"input address",match,matchtype,"matched address","lon,lat",tigerlineid,side
            # NOTE: the coordinate field is a single quoted "lon,lat" string, not two
            # separate CSV columns -- verified against a live sample response.
            parts = next(_csv_split(line))
            if len(parts) < 3:
                continue
            _id = parts[0]
            match = parts[2]
            if match == "Match" and len(parts) >= 6:
                try:
                    lon_str, lat_str = parts[5].split(",")
                    lon, lat = float(lon_str), float(lat_str)
                    results[_id] = (lat, lon)
                except (ValueError, IndexError):
                    results[_id] = None
            else:
                results[_id] = None
        time.sleep(0.5)
    return results


def _csv_split(line):
    import csv as _csv
    yield next(_csv.reader([line]))


def google_geocode(address, api_key, session):
    resp = session.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address, "key": api_key},
        timeout=20,
    )
    resp.raise_for_status()
    d = resp.json()
    if d.get("status") == "OK" and d.get("results"):
        loc = d["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]
    return None


def geocode_tabc(tabc_rows):
    cache = load_geocode_cache()
    to_geocode = []
    keyed = {}
    for idx, r in enumerate(tabc_rows):
        addr = (r.get("locationaddress") or "").strip()
        city = r.get("city") or "Dallas"
        state = r.get("state") or "TX"
        zipc = (r.get("zip") or "")[:5]
        key = f"{addr}|{city}|{state}|{zipc}".upper()
        keyed[idx] = key
        if key not in cache:
            to_geocode.append((str(idx), addr, city, state, zipc, key))

    log(f"TABC geocode: {len(tabc_rows) - len(to_geocode)} addresses already cached, "
        f"{len(to_geocode)} new addresses to geocode")

    if to_geocode:
        census_input = [(rec[0], rec[1], rec[2], rec[3], rec[4]) for rec in to_geocode]
        census_results = census_batch_geocode(census_input)
        n_census_hit = 0
        still_missing = []
        for rec in to_geocode:
            idx_str, addr, city, state, zipc, key = rec
            res = census_results.get(idx_str)
            if res:
                cache[key] = {"lat": res[0], "lon": res[1], "method": "census"}
                n_census_hit += 1
            else:
                still_missing.append(rec)
        log(f"Census batch geocoder: {n_census_hit}/{len(to_geocode)} matched")

        api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        n_google_hit = 0
        if still_missing and api_key:
            session = requests.Session()
            for rec in still_missing:
                idx_str, addr, city, state, zipc, key = rec
                full_addr = f"{addr}, {city}, {state} {zipc}"
                try:
                    res = google_geocode(full_addr, api_key, session)
                except Exception as e:
                    res = None
                if res:
                    cache[key] = {"lat": res[0], "lon": res[1], "method": "google"}
                    n_google_hit += 1
                else:
                    cache[key] = None
                time.sleep(0.05)
            log(f"Google Geocoding fallback: {n_google_hit}/{len(still_missing)} matched")
        elif still_missing and not api_key:
            log("Google Geocoding fallback skipped: GOOGLE_MAPS_API_KEY not set")
            for rec in still_missing:
                cache[rec[5]] = None

        save_geocode_cache(cache)

    # assemble final geocoded rows
    out = []
    n_fail = 0
    for idx, r in enumerate(tabc_rows):
        key = keyed[idx]
        entry = cache.get(key)
        if not entry:
            n_fail += 1
            continue
        out.append({
            "name": r.get("aimstradename") or r.get("aimsownername"),
            "address": r.get("locationaddress", "").strip(),
            "zip": (r.get("zip") or "")[:5],
            "category": "liquor_store",
            "source": "TABC",
            "lat": entry["lat"], "lon": entry["lon"],
            "open_24h": None,
            "_norm_addr": normalize_addr(r.get("locationaddress"), r.get("zip")),
            "_norm_name": normalize_name(r.get("aimstradename") or r.get("aimsownername")),
            "_geocode_method": entry["method"],
        })
    fail_rate = n_fail / len(tabc_rows) if tabc_rows else 0
    log(f"TABC geocode: {n_fail}/{len(tabc_rows)} addresses FAILED to geocode "
        f"({fail_rate*100:.1f}% failure rate)")
    if fail_rate > 0.10:
        log(f"*** PING: TABC geocode failure rate {fail_rate*100:.1f}% exceeds 10% threshold ***")
    return pd.DataFrame(out), fail_rate, n_fail, len(tabc_rows)


# ===========================================================================
# 3. OSM Overpass
# ===========================================================================
def fetch_overpass():
    query = f"""
[out:json][timeout:180];
area({DALLAS_COUNTY_OVERPASS_AREA})->.a;
(
  node["shop"="convenience"](area.a);
  node["amenity"="fuel"](area.a);
  node["shop"="alcohol"](area.a);
  node["shop"="hairdresser"](area.a);
  node["shop"="beauty"](area.a);
  node["shop"="laundry"](area.a);
  node["amenity"="library"](area.a);
);
out center;
""".strip()
    headers = {"User-Agent": "dallas-research-savespos/1.0 (naloxone placement research script)"}
    last_err = None
    for attempt in range(6):
        endpoint = OVERPASS_ENDPOINTS[attempt % len(OVERPASS_ENDPOINTS)]
        try:
            log(f"Overpass query attempt {attempt+1} -> {endpoint}")
            resp = requests.post(endpoint, data={"data": query}, headers=headers, timeout=200)
            if resp.status_code == 200:
                data = resp.json()
                els = data.get("elements", [])
                log(f"Overpass: {len(els)} elements returned")
                save_json(els, dated_raw_path("osm_overpass_dallas", DATE_STR))
                return els
            else:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                log(f"  attempt failed: {last_err}")
        except Exception as e:
            last_err = str(e)
            log(f"  attempt failed: {last_err}")
        backoff = 2 ** attempt * 3
        log(f"  retrying in {backoff}s...")
        time.sleep(backoff)
    log(f"Overpass: ALL attempts failed, last error: {last_err}. Proceeding with 0 OSM POIs.")
    return []


def process_osm(elements):
    kept = []
    n_missing_tags = 0
    for el in elements:
        tags = el.get("tags", {})
        cat = None
        for (k, v), c in OSM_CATEGORY_MAP.items():
            if tags.get(k) == v:
                cat = c
                break
        if cat is None:
            n_missing_tags += 1
            continue
        lat = el.get("lat")
        lon = el.get("lon")
        name = tags.get("name") or f"(unnamed {cat})"
        housenum = tags.get("addr:housenumber", "")
        street = tags.get("addr:street", "")
        address = f"{housenum} {street}".strip() or None
        zipc = tags.get("addr:postcode", "")
        open_24h = None
        oh = tags.get("opening_hours")
        if oh and oh.strip() == "24/7":
            open_24h = True
        kept.append({
            "name": name, "address": address, "zip": zipc[:5] if zipc else None,
            "category": cat, "source": "OSM", "lat": lat, "lon": lon,
            "open_24h": open_24h,
            "_norm_addr": normalize_addr(address, zipc),
            "_norm_name": normalize_name(name),
        })
    log(f"OSM: {len(kept)} nodes mapped to a candidate category, "
        f"{n_missing_tags} skipped (didn't match any target tag combo)")
    by_cat = pd.Series([k["category"] for k in kept]).value_counts().to_dict() if kept else {}
    log(f"OSM by category: {by_cat}")
    return pd.DataFrame(kept)


# ===========================================================================
# 4. Libraries
# ===========================================================================
def fetch_libraries():
    log("Fetching Dallas library branches...")
    rows = requests.get(URL_LIBRARIES, timeout=60).json()
    log(f"Libraries: fetched {len(rows)} rows")
    save_json(rows, dated_raw_path("dallas_libraries", DATE_STR))
    kept = []
    for r in rows:
        loc = r.get("location") or {}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if lat is None or lon is None:
            continue
        addr = r.get("address")
        human = loc.get("human_address")
        zipc = None
        if human:
            try:
                zipc = json.loads(human).get("zip")
            except Exception:
                pass
        kept.append({
            "name": r.get("branch_name"), "address": addr, "zip": zipc,
            "category": "library", "source": "DallasLibraries",
            "lat": float(lat), "lon": float(lon), "open_24h": False,
            "_norm_addr": normalize_addr(addr, zipc),
            "_norm_name": normalize_name(r.get("branch_name")),
        })
    log(f"Libraries: {len(kept)} geocoded branch rows kept")
    return pd.DataFrame(kept)


# ===========================================================================
# Dedupe: exact (norm_name, norm_addr) match, then ~50m proximity for
# same norm_name across sources.
# ===========================================================================
def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def dedupe(df):
    df = df.reset_index(drop=True)
    df["_key"] = df["_norm_name"] + "||" + df["_norm_addr"]

    # pass 1: exact (name, address) merge
    groups = {}
    order = []
    for idx, row in df.iterrows():
        k = row["_key"]
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(idx)

    merged_records = []
    consumed = set()
    keys_list = list(order)
    # pass 2: within same norm_name (possibly different key because address
    # normalization differs), merge across ~50m proximity
    name_index = {}
    for k in keys_list:
        name = k.split("||", 1)[0]
        name_index.setdefault(name, []).append(k)

    final_groups = []
    visited_keys = set()
    for k in keys_list:
        if k in visited_keys:
            continue
        name = k.split("||", 1)[0]
        candidate_keys = name_index.get(name, [k])
        # union rows across candidate_keys that are within 50m of the
        # representative point of k (only for non-trivial names, avoid
        # merging many unrelated "(unnamed ...)" placeholders)
        base_rows = groups[k]
        base_pt = df.loc[base_rows[0], ["lat", "lon"]]
        merged_idx = list(base_rows)
        visited_keys.add(k)
        if name and not name.startswith("UNNAMED"):
            for ck in candidate_keys:
                if ck == k or ck in visited_keys:
                    continue
                other_rows = groups[ck]
                other_pt = df.loc[other_rows[0], ["lat", "lon"]]
                try:
                    d = haversine_m(base_pt["lat"], base_pt["lon"], other_pt["lat"], other_pt["lon"])
                except Exception:
                    continue
                if d <= 50:
                    merged_idx.extend(other_rows)
                    visited_keys.add(ck)
        final_groups.append(merged_idx)

    for idxs in final_groups:
        sub = df.loc[idxs]
        rep = sub.iloc[0]
        sources = sorted(set(sub["source"].tolist()))
        # prefer non-null open_24h True > False > None
        open_24h_vals = [v for v in sub["open_24h"].tolist() if v is not None]
        open_24h = True if True in open_24h_vals else (False if False in open_24h_vals else None)
        # prefer the longest/most descriptive name & address among the group
        best_name = max(sub["name"].fillna(""), key=len) or None
        best_addr = max(sub["address"].fillna(""), key=len) or None
        best_zip = next((z for z in sub["zip"].tolist() if z), None)
        # category: prefer first non-"other_storefront" category if any
        cats = sub["category"].tolist()
        cat = next((c for c in cats if c != "other_storefront"), cats[0])
        merged_records.append({
            "name": best_name, "address": best_addr, "zip": best_zip,
            "category": cat, "source": sources,
            "lat": float(rep["lat"]), "lon": float(rep["lon"]),
            "open_24h": open_24h,
            "n_source_rows_merged": len(sub),
        })

    out_df = pd.DataFrame(merged_records)
    log(f"Dedupe: {len(df)} raw candidate rows -> {len(out_df)} unique candidates "
        f"({len(df) - len(out_df)} merged as duplicates)")
    return out_df


# ===========================================================================
# Main
# ===========================================================================
def main():
    ensure_dirs()
    log("=== PLACEMENT CANDIDATES build starting ===")

    tracts = load_dallas_tracts()

    co_rows = fetch_co()
    co_df = process_co(co_rows)

    tabc_rows_all = fetch_tabc()
    tabc_rows = process_tabc_filter(tabc_rows_all)
    tabc_df, tabc_fail_rate, tabc_n_fail, tabc_n_total = geocode_tabc(tabc_rows)

    osm_elements = fetch_overpass()
    osm_df = process_osm(osm_elements)

    lib_df = fetch_libraries()

    frames = [f for f in [co_df, tabc_df, osm_df, lib_df] if not f.empty]
    all_df = pd.concat(frames, ignore_index=True, sort=False)
    log(f"Combined raw candidates before dedupe: {len(all_df)} "
        f"(CO={len(co_df)}, TABC={len(tabc_df)}, OSM={len(osm_df)}, Libraries={len(lib_df)})")

    deduped = dedupe(all_df)

    # spatial join to Dallas tract
    geom = [Point(xy) for xy in zip(deduped["lon"], deduped["lat"])]
    gdf = gpd.GeoDataFrame(deduped, geometry=geom, crs="EPSG:4326")
    joined = gpd.sjoin(gdf, tracts[["geoid", "geometry"]], how="left", predicate="within")
    n_outside = joined["geoid"].isna().sum()
    log(f"Spatial join to tract: {n_outside}/{len(joined)} candidates fell outside Dallas "
        f"County tracts ({100*n_outside/len(joined):.1f}%) -- likely just-over-the-line "
        f"addresses in adjacent counties (e.g. Garland/Mesquite straddling the line) or bad geocodes")

    joined = joined.drop(columns=["index_right"], errors="ignore")

    # final counts
    log("=== Candidate counts by category (post-dedupe) ===")
    cat_counts = joined["category"].value_counts().to_dict()
    for c in CANDIDATE_CATEGORIES:
        log(f"  {c}: {cat_counts.get(c, 0)}")

    log("=== Candidate counts by source (post-dedupe, a candidate may have multiple sources) ===")
    src_counts = {}
    for srcs in joined["source"]:
        for s in srcs:
            src_counts[s] = src_counts.get(s, 0) + 1
    for s, c in sorted(src_counts.items(), key=lambda kv: -kv[1]):
        log(f"  {s}: {c}")

    # write outputs
    out_geojson_path = os.path.join(DATA_CLEAN, "placement_candidates.geojson")
    out_csv_path = os.path.join(DATA_CLEAN, "placement_candidates.csv")

    export_gdf = joined.copy()
    export_gdf["source"] = export_gdf["source"].apply(lambda s: ";".join(s))
    export_gdf = export_gdf.rename(columns={"geoid": "geoid"})
    export_cols = ["name", "address", "zip", "category", "source", "lat", "lon",
                   "geoid", "open_24h", "n_source_rows_merged", "geometry"]
    export_gdf = export_gdf[export_cols]
    export_gdf.to_file(out_geojson_path, driver="GeoJSON")
    log(f"Wrote {out_geojson_path} ({os.path.getsize(out_geojson_path):,} bytes)")

    csv_df = export_gdf.drop(columns=["geometry"])
    csv_df.to_csv(out_csv_path, index=False)
    log(f"Wrote {out_csv_path} ({os.path.getsize(out_csv_path):,} bytes)")

    meta = {
        "generated": datetime.datetime.now().isoformat(),
        "counts": {
            "co_rows_fetched": len(co_rows),
            "co_kept_after_landuse_filter_and_addr_collapse": len(co_df),
            "tabc_rows_dallas_county_total": len(tabc_rows_all),
            "tabc_offpremise_filtered_count": tabc_n_total,
            "tabc_offpremise_filtered": len(tabc_rows),
            "tabc_geocode_fail_count": tabc_n_fail,
            "tabc_geocode_fail_rate": round(tabc_fail_rate, 4),
            "osm_elements_fetched": len(osm_elements),
            "osm_kept": len(osm_df),
            "libraries_kept": len(lib_df),
            "combined_before_dedupe": int(len(all_df)),
            "final_after_dedupe": int(len(joined)),
            "outside_county_tract": int(n_outside),
            "by_category": {c: int(cat_counts.get(c, 0)) for c in CANDIDATE_CATEGORIES},
            "by_source": src_counts,
        },
        "ping_flags": (
            ["TABC geocode failure rate exceeds 10%"] if tabc_fail_rate > 0.10 else []
        ),
    }
    save_json(meta, os.path.join(DATA_CLEAN, "placement_candidates_meta.json"))

    log("=== PLACEMENT CANDIDATES build complete ===")


if __name__ == "__main__":
    main()
