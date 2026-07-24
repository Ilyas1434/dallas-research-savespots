#!/usr/bin/env python3
"""
Build the Dallas County naloxone supply inventory.

Merges three sources into data/clean/naloxone_locations.geojson:
  1. Texas DSHS ArcGIS NarcanSites live feed (source of truth)
  2. data/raw/NaloxoneSites_April2026.csv local snapshot (may carry newer rows)
  3. SAMHSA findtreatment.gov opioid treatment programs

Sites without usable coordinates are emitted with null geometry and flagged,
never given a placeholder point.

Run: ./venv/bin/python scripts/build_naloxone.py
"""

import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

try:
    import geopandas as gpd
    from shapely.geometry import Point
except ImportError:
    print("FATAL: geopandas/shapely required. pip install -r requirements.txt", file=sys.stderr)
    raise

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import pipeline_date  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = REPO_ROOT / "data" / "raw"
DATA_CLEAN = REPO_ROOT / "data" / "clean"
TIGER_DIR = DATA_RAW / "tiger"
TODAY = pipeline_date()
TODAY_STR = TODAY.isoformat()

ARCGIS_URL = (
    "https://services3.arcgis.com/vljlarU2635mITsl/arcgis/rest/services/"
    "NarcanSites_10152024/FeatureServer/0/query?where=1%3D1&outFields=*&f=json"
)
CSV_PATH = DATA_RAW / "NaloxoneSites_April2026.csv"
SAMHSA_URL_TMPL = (
    "https://findtreatment.gov/locator/exportsAsJson/v2?sAddr=32.7767,-96.797"
    "&limitType=2&limitValue=55000&sCodes=OTP&pageSize=200&page={page}"
)
COUNTY_ZIP_URL = "https://www2.census.gov/geo/tiger/TIGER2024/COUNTY/tl_2024_us_county.zip"
CENSUS_GEOCODER_URL = (
    "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    "?address={addr}&benchmark=Public_AR_Current&format=json"
)
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

DALLAS_COUNTY_GEOID = "48113"

# Whitelist of incorporated cities that lie (fully or substantially) within Dallas County.
DALLAS_CITY_WHITELIST = {
    "dallas", "garland", "irving", "mesquite", "grand prairie", "farmers branch",
    "addison", "richardson", "carrollton", "duncanville", "desoto", "de soto",
    "lancaster", "cedar hill", "balch springs", "seagoville", "rowlett", "sachse",
    "coppell", "hutchins", "wilmer", "glenn heights", "cockrell hill",
    "highland park", "university park",
}

NIGHTLIFE_KEYWORDS = [
    "bar", "club", "lounge", "tavern", "pub", "saloon", "nightclub",
    "music", "pavillion", "pavilion", "cabaret", "station",
]
STOREFRONT_KEYWORDS = [
    "food mart", "smoke shop", "smoke", "puff", "liquor", "vape", "dollar",
    "mart", "gas", "chevron", "shell", "exxon", "texaco", "7-eleven",
    "circle k", "store", "shop", "cafe", "café", "coffee", "pharmacy",
    "cvs", "walgreens",
]
# Late-night closing times (hyphen followed by 12a/1a-5a) are a strong signal
# for a bar/nightclub even when the business name gives no clue (e.g. "TMC",
# "Sue Ellen's"). Applied only to raw types 'Freestanding Dispenser'/'Private Entity'.
LATE_NIGHT_CLOSE_RE = re.compile(r"-\s*(12|1|2|3|4|5)\s*a\b")

GEOCODE_CACHE_PATH = DATA_RAW / "geocode_cache_naloxone.json"


def log(msg):
    print(msg, flush=True)


def norm_text(s):
    if s is None:
        return ""
    s = str(s).strip().lower()
    if s == "nan":
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def norm_key(name, address):
    """Normalized dedupe key: org name + street number+name (drop suite/unit noise)."""
    n = norm_text(name)
    a = norm_text(address)
    # drop common suite/unit tokens for looser matching
    a = re.sub(r"\b(suite|ste|unit|apt|bldg|building)\b.*$", "", a).strip()
    return f"{n}|{a}"


def fetch_arcgis():
    snap_path = DATA_RAW / f"narcansites_arcgis_{TODAY_STR}.json"
    log(f"[arcgis] GET {ARCGIS_URL}")
    r = requests.get(ARCGIS_URL, timeout=60)
    r.raise_for_status()
    data = r.json()
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = snap_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(snap_path)
    feats = data.get("features", [])
    log(f"[arcgis] snapshot saved -> {snap_path.relative_to(REPO_ROOT)} ({len(feats)} features)")
    fields = [f["name"] for f in data.get("fields", [])]
    log(f"[arcgis] fields: {fields}")

    rows = []
    for feat in feats:
        attrs = dict(feat.get("attributes", {}))
        geom = feat.get("geometry") or {}
        attrs["_lat"] = geom.get("y")
        attrs["_lon"] = geom.get("x")
        if "full_" in attrs and "full" not in attrs:
            attrs["full"] = attrs["full_"]
        attrs["_source"] = "arcgis"
        rows.append(attrs)
    return rows


def load_csv():
    if not CSV_PATH.exists():
        log(f"[csv] WARNING: {CSV_PATH} not found, skipping CSV source")
        return []
    import pandas as pd
    df = pd.read_csv(CSV_PATH)
    log(f"[csv] loaded {CSV_PATH.relative_to(REPO_ROOT)}: {len(df)} rows, columns={df.columns.tolist()}")

    rows = []
    for _, row in df.iterrows():
        attrs = row.to_dict()
        lon = attrs.get("N_LON")
        lat = attrs.get("N_LAT")
        # Defensive: treat non-numeric / literal "M" markers as missing coords
        for key in ("N_LON", "N_LAT"):
            v = attrs.get(key)
            if isinstance(v, str) and not re.match(r"^-?\d+(\.\d+)?$", v.strip()):
                attrs[key] = None
        try:
            lon = float(lon) if lon is not None and str(lon).strip() != "" else None
        except (ValueError, TypeError):
            lon = None
        try:
            lat = float(lat) if lat is not None and str(lat).strip() != "" else None
        except (ValueError, TypeError):
            lat = None
        attrs["_lat"] = lat
        attrs["_lon"] = lon
        attrs["_source"] = "csv_snapshot"
        rows.append(attrs)
    return rows


def fetch_samhsa_otps():
    all_rows = []
    page = 1
    total_pages = None
    while True:
        url = SAMHSA_URL_TMPL.format(page=page)
        log(f"[samhsa] GET page {page}: {url}")
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        d = r.json()
        total_pages = d.get("totalPages", 1)
        all_rows.extend(d.get("rows", []))
        if page >= total_pages:
            break
        page += 1

    log(f"[samhsa] fetched {len(all_rows)} rows across {total_pages} page(s) "
        f"(recordCount={d.get('recordCount')})")

    otp_rows = [r for r in all_rows if r.get("typeFacility") == "OTP"]
    log(f"[samhsa] rows tagged typeFacility=='OTP': {len(otp_rows)}")

    seen = set()
    deduped = []
    for r in otp_rows:
        key = norm_key(r.get("name1", ""), f"{r.get('street1','')} {r.get('zip','')}")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    log(f"[samhsa] after dedupe: {len(deduped)}")

    rows = []
    for r in deduped:
        try:
            lat = float(r["latitude"]) if r.get("latitude") else None
            lon = float(r["longitude"]) if r.get("longitude") else None
        except (ValueError, TypeError):
            lat = lon = None
        rows.append({
            "Organization": r.get("name1"),
            "Address": r.get("street1"),
            "City": r.get("city"),
            "State": r.get("state"),
            "Zipcode": r.get("zip"),
            "Phone": r.get("phone"),
            "Hours": None,
            "Type": "Opioid Treatment Program (OTP)",
            "Website": r.get("website"),
            "VerifiedOn": None,
            "_lat": lat,
            "_lon": lon,
            "_source": "findtreatment",
        })
    return rows


def get_dallas_county_polygon():
    zpath = TIGER_DIR / "tl_2024_us_county.zip"
    if not zpath.exists():
        log(f"[county] downloading {COUNTY_ZIP_URL}")
        TIGER_DIR.mkdir(parents=True, exist_ok=True)
        r = requests.get(COUNTY_ZIP_URL, timeout=300)
        r.raise_for_status()
        tmp = zpath.with_suffix(".zip.tmp")
        tmp.write_bytes(r.content)
        tmp.replace(zpath)
        log(f"[county] saved {zpath.relative_to(REPO_ROOT)}")
    gdf = gpd.read_file(f"zip://{zpath}")
    dallas = gdf[gdf["GEOID"] == DALLAS_COUNTY_GEOID]
    if dallas.empty:
        raise RuntimeError("Dallas County (GEOID 48113) not found in TIGER county file")
    poly = dallas.geometry.iloc[0]
    log(f"[county] loaded Dallas County polygon (CRS {gdf.crs})")
    return poly, gdf.crs


def load_geocode_cache():
    if GEOCODE_CACHE_PATH.exists():
        return json.loads(GEOCODE_CACHE_PATH.read_text())
    return {}


def save_geocode_cache(cache):
    GEOCODE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = GEOCODE_CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=2))
    tmp.replace(GEOCODE_CACHE_PATH)


def geocode_census(address_str):
    url = CENSUS_GEOCODER_URL.format(addr=quote(address_str))
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        d = r.json()
        matches = d.get("result", {}).get("addressMatches", [])
        if matches:
            c = matches[0]["coordinates"]
            return float(c["y"]), float(c["x"])
    except Exception as e:
        log(f"    [census-geocode] error for '{address_str}': {e}")
    return None


def geocode_google(address_str, api_key):
    if not api_key:
        return None
    try:
        r = requests.get(GOOGLE_GEOCODE_URL, params={"address": address_str, "key": api_key}, timeout=20)
        r.raise_for_status()
        d = r.json()
        if d.get("status") == "OK" and d.get("results"):
            loc = d["results"][0]["geometry"]["location"]
            return float(loc["lat"]), float(loc["lng"])
        elif d.get("status") not in ("ZERO_RESULTS",):
            log(f"    [google-geocode] status={d.get('status')} for '{address_str}'")
    except Exception as e:
        log(f"    [google-geocode] error for '{address_str}': {e}")
    return None


def geocode_address(address_str, cache, api_key):
    if address_str in cache:
        entry = cache[address_str]
        return entry.get("lat"), entry.get("lon"), entry.get("method")

    result = geocode_census(address_str)
    method = "census"
    if result is None:
        time.sleep(0.1)
        result = geocode_google(address_str, api_key)
        method = "google"
    if result is None:
        cache[address_str] = {"lat": None, "lon": None, "method": "failed"}
        return None, None, "failed"
    lat, lon = result
    cache[address_str] = {"lat": lat, "lon": lon, "method": method}
    return lat, lon, method


def classify_category(source, raw_type, org_name, hours_raw=None):
    if source == "findtreatment":
        return "otp_methadone"

    t = norm_text(raw_type)
    name = norm_text(org_name)

    if "regional distribution" in t or "risk reduction" in t:
        return "harm_reduction"
    if "community health clinic" in t or "health department" in t:
        return "health_clinic"
    if "recovery support" in t:
        return "recovery_services"
    if "public safety" in t:
        return "public_safety"
    if "public social service" in t:
        return "other"
    if t in ("freestanding dispenser", "private entity"):
        if any(k in name for k in NIGHTLIFE_KEYWORDS):
            return "nightlife_venue"
        if any(k in name for k in STOREFRONT_KEYWORDS):
            return "storefront_dispenser"
        # Fallback for name-ambiguous venues (e.g. "TMC", "Sue Ellen's", "Station 4"):
        # a bar-hours pattern (open into the 12a-5a range) is a strong nightlife signal
        # once explicit storefront/retail keywords have already been ruled out.
        hours_str = "" if hours_raw is None else str(hours_raw).lower()
        if LATE_NIGHT_CLOSE_RE.search(hours_str):
            return "nightlife_venue"
        return "harm_reduction"
    return "other"


def classify_access(hours_raw):
    if hours_raw is None or (isinstance(hours_raw, float) and pd_isnan(hours_raw)):
        return False, None
    h = str(hours_raw).strip()
    if h.lower() in ("nan", "none", ""):
        return False, None
    is_247 = h.replace(" ", "") in ("24/7", "24hrs", "24hours", "247")
    return is_247, h


def pd_isnan(x):
    try:
        return x != x  # NaN check without importing pandas at module scope repeatedly
    except Exception:
        return False


def parse_verified_on(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    # ArcGIS gives ISO 'YYYY-MM-DD', CSV gives 'M/D/YYYY'
    parsed = None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(s, fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    # Sanity bound: source data has at least one confirmed typo (e.g. "2/12/2926"
    # instead of "2/12/2026"). Reject implausible years rather than let a typo
    # win the freshest-VerifiedOn merge tie-break.
    if not (2015 <= parsed.year <= 2028):
        log(f"    [verified_on] rejecting implausible date '{s}' (parsed year {parsed.year})")
        return None
    return parsed


def merge_sources(arcgis_rows, csv_rows):
    """Merge ArcGIS + CSV rows, dedupe on normalized (name, address), keep freshest VerifiedOn.
    Coordinates: prefer the freshest record's own coords, but backfill from the other
    source's version of the same site if the freshest record is missing coords."""
    groups = {}
    for row in arcgis_rows + csv_rows:
        key = norm_key(row.get("Organization"), row.get("Address"))
        row["_verified_date"] = parse_verified_on(row.get("VerifiedOn"))
        groups.setdefault(key, []).append(row)

    merged = []
    for key, candidates in groups.items():
        # winner = freshest VerifiedOn; ties/missing-dates prefer the one with coords
        def sort_key(r):
            d = r.get("_verified_date")
            has_coords = r.get("_lat") is not None
            return (d is not None, d or date.min, has_coords)
        winner = max(candidates, key=sort_key)
        if winner.get("_lat") is None:
            for c in candidates:
                if c.get("_lat") is not None:
                    winner = dict(winner)  # don't mutate original
                    winner["_lat"] = c["_lat"]
                    winner["_lon"] = c["_lon"]
                    winner["_coords_backfilled_from"] = c.get("_source")
                    break
        merged.append(winner)
    return merged


def main():
    load_dotenv(REPO_ROOT / ".env")
    google_api_key = os.environ.get("GOOGLE_MAPS_API_KEY")

    DATA_RAW.mkdir(parents=True, exist_ok=True)
    DATA_CLEAN.mkdir(parents=True, exist_ok=True)

    log("=" * 70)
    log("STEP 1: Fetch ArcGIS live feed")
    log("=" * 70)
    arcgis_rows = fetch_arcgis()

    log("")
    log("=" * 70)
    log("STEP 2: Load local CSV snapshot")
    log("=" * 70)
    csv_rows = load_csv()

    log("")
    log("=" * 70)
    log("STEP 3: Fetch SAMHSA findtreatment.gov OTPs")
    log("=" * 70)
    samhsa_rows = fetch_samhsa_otps()

    log("")
    log("=" * 70)
    log("STEP 4: Merge ArcGIS + CSV (dedupe, keep freshest VerifiedOn)")
    log("=" * 70)
    merged = merge_sources(arcgis_rows, csv_rows)
    log(f"[merge] arcgis={len(arcgis_rows)} + csv={len(csv_rows)} -> merged unique={len(merged)}")
    all_rows = merged + samhsa_rows

    log("")
    log("=" * 70)
    log("STEP 5: Filter to Dallas County")
    log("=" * 70)
    county_poly, _ = get_dallas_county_polygon()

    dallas_rows = []
    mobile_rows = []
    for row in all_rows:
        org = row.get("Organization") or row.get("name1") or ""
        city = norm_text(row.get("City"))
        lat, lon = row.get("_lat"), row.get("_lon")

        # Mobile outreach org with no physical address; kept, but never geocoded.
        if "dfw harm reduction access movement" in norm_text(org):
            row["_mobile"] = True
            row["_in_dallas"] = True  # organization explicitly serves DFW/Dallas
            mobile_rows.append(row)
            continue

        in_whitelist = city in DALLAS_CITY_WHITELIST

        in_polygon = None
        if lat is not None and lon is not None:
            try:
                pt = Point(lon, lat)
                in_polygon = county_poly.contains(pt)
            except Exception:
                in_polygon = None

        if in_whitelist or in_polygon:
            row["_in_dallas"] = True
            dallas_rows.append(row)
        # Rows outside the whitelist with no coords (or coords outside the polygon)
        # are out of scope; statewide rows are not geocoded just to test membership.

    log(f"[filter] Dallas County candidates (whitelist city or point-in-polygon): {len(dallas_rows)}")
    log(f"[filter] mobile/no-address special-case rows: {len(mobile_rows)}")

    log("")
    log("=" * 70)
    log("STEP 6: Geocode missing coordinates (Census -> Google fallback)")
    log("=" * 70)
    cache = load_geocode_cache()
    n_needed = sum(1 for r in dallas_rows if r.get("_lat") is None)
    log(f"[geocode] {n_needed} Dallas-scope rows missing coordinates")

    n_census = n_google = n_failed = n_source = 0
    for row in dallas_rows:
        if row.get("_lat") is not None:
            row["_geocode_method"] = "source_coords"
            n_source += 1
            continue
        addr_parts = [row.get("Address"), row.get("City"), "TX", str(row.get("Zipcode") or "")]
        addr_str = ", ".join([p for p in addr_parts if p and str(p).strip() and str(p).lower() != "nan"])
        if not addr_str:
            row["_geocode_method"] = "failed"
            n_failed += 1
            continue
        lat, lon, method = geocode_address(addr_str, cache, google_api_key)
        row["_lat"], row["_lon"] = lat, lon
        row["_geocode_method"] = method
        if method == "census":
            n_census += 1
        elif method == "google":
            n_google += 1
        else:
            n_failed += 1
    save_geocode_cache(cache)

    log(f"[geocode] source_coords={n_source} census={n_census} google={n_google} failed={n_failed}")

    # Re-test membership for rows geocoded above; drop those confirmed outside.
    final_rows = []
    dropped_outside = 0
    for row in dallas_rows:
        if row.get("_lat") is None or row.get("_lon") is None:
            final_rows.append(row)  # failed geocode is reported, never silently dropped
            continue
        city = norm_text(row.get("City"))
        if city in DALLAS_CITY_WHITELIST:
            final_rows.append(row)
            continue
        try:
            pt = Point(row["_lon"], row["_lat"])
            if county_poly.contains(pt):
                final_rows.append(row)
            else:
                dropped_outside += 1
        except Exception:
            final_rows.append(row)
    if dropped_outside:
        log(f"[filter] dropped {dropped_outside} row(s) whose geocoded point fell outside Dallas County")

    final_rows.extend(mobile_rows)

    n_geocode_attempted = n_census + n_google + n_failed
    geocode_fail_rate = (n_failed / n_geocode_attempted * 100) if n_geocode_attempted else 0.0
    log(f"[geocode] failure rate among rows needing geocoding: {geocode_fail_rate:.1f}%")
    if geocode_fail_rate > 10:
        log(f"PING: geocode failure rate {geocode_fail_rate:.1f}% exceeds 10% threshold for Dallas records")

    log("")
    log("=" * 70)
    log("STEP 7: Build output GeoJSON")
    log("=" * 70)

    features = []
    category_counts = {}
    for row in final_rows:
        org = row.get("Organization") or row.get("name1") or ""
        address = row.get("Address")
        city = row.get("City")
        zipc = row.get("Zipcode")
        raw_type = row.get("Type")
        hours_raw = row.get("Hours")
        source = row.get("_source", "unknown")
        verified = row.get("_verified_date")
        verified_str = verified.isoformat() if verified else None
        mobile = bool(row.get("_mobile", False))

        category = classify_category(source, raw_type, org, hours_raw)
        access_247, hours_str = classify_access(hours_raw)
        category_counts[category] = category_counts.get(category, 0) + 1

        props = {
            "name": org,
            "address": address if not mobile else None,
            "city": city,
            "zip": (None if zipc is None or str(zipc).lower() == "nan" else str(zipc).split(".")[0]),
            "type": raw_type,
            "category": category,
            "access_247": access_247,
            "hours_raw": hours_str,
            "source": source,
            "verified_on": verified_str,
            "geocode_method": ("not_applicable_mobile" if mobile else row.get("_geocode_method")),
            "mobile": mobile,
        }
        if mobile:
            props["mobile_note"] = (
                "No physical address; org conducts mobile outreach across DFW. "
                "Geometry intentionally set to null rather than using a placeholder point."
            )
            geometry = None
        else:
            lat, lon = row.get("_lat"), row.get("_lon")
            if lat is None or lon is None:
                geometry = None
                props["geocode_method"] = "failed"
            else:
                geometry = {"type": "Point", "coordinates": [lon, lat]}

        features.append({"type": "Feature", "geometry": geometry, "properties": props})

    fc = {
        "type": "FeatureCollection",
        "properties": {
            "generated": TODAY_STR,
            "county": "Dallas County, TX (GEOID 48113)",
            "n_features": len(features),
            "n_with_geometry": sum(1 for f in features if f["geometry"] is not None),
            "category_counts": category_counts,
            "geocode_failure_rate_pct": round(geocode_fail_rate, 1),
            "sources": {
                "arcgis": ARCGIS_URL,
                "csv_snapshot": str(CSV_PATH.relative_to(REPO_ROOT)),
                "findtreatment_otp": SAMHSA_URL_TMPL.format(page="N"),
            },
            "notes": [
                "DFW Harm Reduction Access Movement is mobile/no-fixed-address; included with "
                "null geometry and mobile=true rather than a fabricated point.",
                "Category for 'Freestanding Dispenser'/'Private Entity' raw types is inferred "
                "from organization-name keywords (bar/club/venue -> nightlife_venue; "
                "gas/liquor/smoke/convenience -> storefront_dispenser; else harm_reduction).",
            ],
        },
        "features": features,
    }

    out_path = DATA_CLEAN / "naloxone_locations.geojson"
    tmp = out_path.with_suffix(".geojson.tmp")
    tmp.write_text(json.dumps(fc, indent=2))
    tmp.replace(out_path)
    log(f"[output] wrote {out_path.relative_to(REPO_ROOT)}")

    log("")
    log("=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log(f"Total Dallas County naloxone-related features: {len(features)}")
    log(f"  with geometry: {fc['properties']['n_with_geometry']}")
    log(f"  without geometry (failed geocode or mobile): {len(features) - fc['properties']['n_with_geometry']}")
    log("Category breakdown:")
    for cat, cnt in sorted(category_counts.items(), key=lambda x: -x[1]):
        log(f"  {cat}: {cnt}")
    log(f"Geocode failure rate: {geocode_fail_rate:.1f}%")

    check = json.loads(out_path.read_text())
    assert check["type"] == "FeatureCollection"
    log(f"[validate] reloaded OK, {len(check['features'])} features")


if __name__ == "__main__":
    main()
