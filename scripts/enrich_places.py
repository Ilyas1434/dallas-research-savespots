#!/usr/bin/env python
"""
enrich_places.py -- OPTIONAL, TARGETED Google Places enrichment.

*** COST NOTE ***
Google Places Nearby Search is billed per request (Basic SKU as of 2026 is
on the order of $0.032/request for Nearby Search; a naive countywide sweep
of ~645 Dallas County tracts x multiple categories would run into the
hundreds of dollars for no clear research benefit given CO+TABC+OSM already
cover gas stations, liquor stores, and convenience stores reasonably well.
This script therefore:
  - is OFF by default in the pipeline (never called by build_candidates.py
    or build_builtenv.py -- must be invoked manually),
  - only queries within tract(s) explicitly passed via --tracts,
  - is capped by --max-requests (default 100, i.e. worst case is a few
    dollars),
  - caches every response (keyed by lat,lon,radius,type) to
    data/raw/places_cache.json so a re-run never re-bills a cached query,
  - is intended to backfill categories that are known-thin in OSM/CO:
    barbershop_beauty (OSM shop=hairdresser|beauty coverage is sparse) and
    laundromat (OSM shop=laundry coverage is sparse).

USAGE
  ./venv/bin/python scripts/enrich_places.py --tracts 48113004300,48113020402 \\
      --max-requests 50

  ./venv/bin/python scripts/enrich_places.py --smoke-test
      # sends <= 5 Nearby Search requests total, to confirm the API key
      # works, and reports the raw status. Does NOT write to candidates.

Places Nearby Search "type" values used: hair_care (barbershop/beauty) and
laundry (laundromat). One request per (tract-centroid, type) pair, using a
search radius derived from the tract's bounding-circle (tract geometry ->
minimum enclosing circle of the tract polygon, capped at 3000m per Places
API's practical radius limits for meaningful "nearby" results).

Output: appends newly-found places to data/clean/places_enrichment.json
(NOT merged into placement_candidates.geojson automatically -- that merge,
if desired, is a separate manual step so the core candidate pipeline stays
free of paid-API dependency).
"""
import argparse
import json
import math
import os
import sys
import time

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import REPO_ROOT, DATA_RAW, DATA_CLEAN, log, ensure_dirs, save_json, load_dallas_tracts

load_dotenv(os.path.join(REPO_ROOT, ".env"))

PLACES_CACHE_PATH = os.path.join(DATA_RAW, "places_cache.json")
NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"

PLACES_TYPE_TO_CATEGORY = {
    "hair_care": "barbershop_beauty",
    "laundry": "laundromat",
}

MAX_RADIUS_M = 3000


def load_cache():
    if os.path.exists(PLACES_CACHE_PATH):
        with open(PLACES_CACHE_PATH) as f:
            return json.load(f)
    return {}


def save_cache(cache):
    save_json(cache, PLACES_CACHE_PATH)


def tract_search_circle(tract_row):
    """Approximate a tract's bounding circle: centroid + radius to farthest
    vertex of its geometry (capped at MAX_RADIUS_M)."""
    geom = tract_row.geometry
    centroid = geom.centroid
    # rough meters-per-degree at this latitude
    lat0 = centroid.y
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat0))
    max_d = 0.0
    minx, miny, maxx, maxy = geom.bounds
    corners = [(minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)]
    for x, y in corners:
        dx = (x - centroid.x) * m_per_deg_lon
        dy = (y - centroid.y) * m_per_deg_lat
        d = math.sqrt(dx * dx + dy * dy)
        max_d = max(max_d, d)
    radius = min(max_d, MAX_RADIUS_M)
    return centroid.y, centroid.x, radius


def nearby_search(lat, lon, radius, place_type, api_key, cache, session):
    key = f"{round(lat,5)},{round(lon,5)},{int(radius)},{place_type}"
    if key in cache:
        log(f"  [cache hit] {key}")
        return cache[key], 0
    params = {
        "location": f"{lat},{lon}",
        "radius": int(radius),
        "type": place_type,
        "key": api_key,
    }
    resp = session.get(NEARBY_URL, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    cache[key] = data
    return data, 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tracts", type=str, default=None,
                     help="Comma-separated GEOID list to enrich (required unless --smoke-test)")
    ap.add_argument("--max-requests", type=int, default=100,
                     help="Hard cap on the number of BILLED (non-cached) API requests this run")
    ap.add_argument("--smoke-test", action="store_true",
                     help="Send <=5 Nearby Search requests near downtown Dallas to verify the "
                          "API key works; does not require --tracts, does not write candidates.")
    args = ap.parse_args()

    ensure_dirs()
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        log("ERROR: GOOGLE_MAPS_API_KEY not set in .env")
        sys.exit(1)

    session = requests.Session()
    cache = load_cache()
    n_billed = 0

    if args.smoke_test:
        log("=== SMOKE TEST: <=5 Nearby Search requests near downtown Dallas ===")
        # Dallas City Hall, a known point, small radius, one type only.
        lat, lon = 32.7767, -96.7970
        for place_type in ["hair_care"]:
            data, billed = nearby_search(lat, lon, 1500, place_type, api_key, cache, session)
            n_billed += billed
            status = data.get("status")
            n_results = len(data.get("results", []))
            log(f"  type={place_type} status={status} n_results={n_results} billed_requests_so_far={n_billed}")
            if status not in ("OK", "ZERO_RESULTS"):
                log(f"  API error_message: {data.get('error_message')}")
            if n_billed >= 5:
                break
        save_cache(cache)
        log("=== SMOKE TEST COMPLETE ===")
        return

    if not args.tracts:
        log("ERROR: --tracts is required (or pass --smoke-test). Refusing to sweep the whole county.")
        sys.exit(1)

    tract_ids = [t.strip() for t in args.tracts.split(",") if t.strip()]
    log(f"Enriching {len(tract_ids)} tract(s): {tract_ids}")
    log(f"Hard cap: {args.max_requests} billed requests this run")

    tracts = load_dallas_tracts()
    tracts = tracts[tracts["geoid"].isin(tract_ids)]
    if tracts.empty:
        log("ERROR: none of the requested GEOIDs matched Dallas County tracts")
        sys.exit(1)

    found = []
    for _, row in tracts.iterrows():
        if n_billed >= args.max_requests:
            log(f"Hit --max-requests cap ({args.max_requests}), stopping.")
            break
        lat, lon, radius = tract_search_circle(row)
        for place_type, category in PLACES_TYPE_TO_CATEGORY.items():
            if n_billed >= args.max_requests:
                break
            log(f"Tract {row['geoid']}: searching type={place_type} at "
                f"({lat:.5f},{lon:.5f}) r={radius:.0f}m")
            data, billed = nearby_search(lat, lon, radius, place_type, api_key, cache, session)
            n_billed += billed
            status = data.get("status")
            results = data.get("results", [])
            log(f"  status={status} n_results={len(results)} (billed so far: {n_billed})")
            for r in results:
                loc = r.get("geometry", {}).get("location", {})
                found.append({
                    "name": r.get("name"),
                    "address": r.get("vicinity"),
                    "zip": None,
                    "category": category,
                    "source": "GooglePlaces",
                    "lat": loc.get("lat"),
                    "lon": loc.get("lng"),
                    "geoid": row["geoid"],
                    "open_24h": None,
                    "place_id": r.get("place_id"),
                })
            time.sleep(0.2)

    save_cache(cache)

    out_path = os.path.join(DATA_CLEAN, "places_enrichment.json")
    existing = []
    if os.path.exists(out_path):
        with open(out_path) as f:
            existing = json.load(f)
    seen_place_ids = {e.get("place_id") for e in existing}
    new_unique = [f for f in found if f["place_id"] not in seen_place_ids]
    existing.extend(new_unique)
    save_json(existing, out_path)

    log(f"=== DONE: {n_billed} billed requests, {len(found)} places found "
        f"({len(new_unique)} newly added, {len(found)-len(new_unique)} already known) ===")
    log(f"Output (NOT auto-merged into placement_candidates.geojson): {out_path}")


if __name__ == "__main__":
    main()
