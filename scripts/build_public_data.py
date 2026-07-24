#!/usr/bin/env python3
"""Emit the redacted copies of the placement layers that the public map reads.

Why this stage exists
---------------------
`data/clean/placement_candidates.*` and `data/clean/placement_ranked.*` name
5,428 real businesses, with street addresses and exact coordinates, and rank
them as naloxone placement targets. None of those owners has been contacted.
The full files stay in the repository so the ranking is reproducible, but the
deployed map must not let a visitor walk up to a named storefront.

Dropping `name` and `address` is not sufficient on its own: an exact coordinate
is an identifier. So every public placement feature here is snapped to the
representative point of its 2020 census tract and aggregated with the other
candidates in that tract. The public layers answer "placing in this tract newly
covers N unserved residents", never "place it at this address".

What is NOT redacted, and why
-----------------------------
`naloxone_locations.geojson` -- existing supply. Those are published public
resources, not candidates, and stay exactly as they are. Every analytical layer
(access desert, composite vulnerability, tiers, coverage gap, walk coverage)
is tract-level already and is served unchanged.

Outputs (all under data/public/, all committed, all deterministic)
------------------------------------------------------------------
  placement_opportunity_tracts.geojson  ranked candidates, aggregated by tract
  placement_universe_tracts.geojson     full candidate universe, counts by tract
  placement_ranked_meta.json            meta with every identifier stripped

Deterministic: no clock reads, no dict-order dependence, fixed rounding. The
daily and monthly refresh jobs re-run this stage and commit data/public, so the
redaction cannot drift out of sync with data/clean.
"""
import json
import os

import geopandas as gpd

from common import DATA_CLEAN, REPO_ROOT, log, save_json

DATA_PUBLIC = os.path.join(REPO_ROOT, "data", "public")

# Property keys that identify a specific storefront. Stripped everywhere,
# at every nesting depth, from the public meta file. `category` is included
# because in a tract holding one liquor store, "liquor store in tract X" is an
# address; tract geography only anonymises when the business type is dropped
# with the coordinates.
IDENTIFYING_KEYS = {"name", "address", "zip", "top_pick_name", "category"}

# Per-site listings the map never reads. Each row is a single storefront keyed
# to a tract, so they are dropped whole rather than thinned.
DROPPED_SECTIONS = {"top10_by_composite", "per_category_top10_by_composite"}

# Non-deterministic wall-clock measurements. Dropping them keeps the public
# file byte-stable across refreshes that changed no data.
VOLATILE_KEYS = {"timings_sec"}


def load_json(name):
    with open(os.path.join(DATA_CLEAN, name)) as f:
        return json.load(f)


def tract_points():
    """geoid -> [lon, lat] representative point, from the published tract layer.

    representative_point() rather than centroid: it is guaranteed to fall
    inside the polygon, which matters for the concave river-front tracts.
    """
    gdf = gpd.read_file(os.path.join(DATA_CLEAN, "composite_index.geojson"))
    pts = {}
    for geoid, geom in zip(gdf["geoid"], gdf.geometry):
        if geom is None or geom.is_empty:
            continue
        p = geom.representative_point()
        pts[str(geoid)] = [round(p.x, 6), round(p.y, 6)]
    return pts


def tract_context():
    """geoid -> the tract-level NEED fields the public popups quote."""
    fc = load_json("composite_index.geojson")
    out = {}
    for f in fc.get("features", []):
        p = f.get("properties") or {}
        out[str(p.get("geoid"))] = {
            "tier": p.get("tier"),
            "tier_label": p.get("tier_label"),
            "composite_score": p.get("composite_score"),
            "totpop": p.get("totpop"),
        }
    return out


def fc(features):
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }


def strip_identifiers(obj):
    """Recursively drop identifying and volatile keys from the meta tree."""
    if isinstance(obj, dict):
        return {k: strip_identifiers(v) for k, v in obj.items()
                if k not in IDENTIFYING_KEYS
                and k not in VOLATILE_KEYS
                and k not in DROPPED_SECTIONS}
    if isinstance(obj, list):
        return [strip_identifiers(v) for v in obj]
    return obj


def build_opportunity(points, context):
    """Ranked candidates collapsed to one feature per tract.

    Per tract we keep the best single site's measured reach -- "the best
    available storefront in this tract would newly reach N unserved residents
    within a half-mile walk" -- plus how many ranked candidates the tract holds
    and how the tract places in each ranking variant. No category breakdown:
    in a tract holding one candidate, a category is close to an address.
    """
    src = load_json("placement_ranked.geojson")
    by_tract = {}
    for f in src.get("features", []):
        p = f.get("properties") or {}
        geoid = str(p.get("geoid") or "")
        if not geoid or geoid == "None" or geoid not in points:
            continue
        t = by_tract.setdefault(geoid, {
            "n_candidates": 0, "n_top100": 0, "n_top50_both": 0,
            "best_rank_composite": None, "best_rank_grounded": None,
            "best_site_reach": 0, "best_greedy_pick_order": None,
            "need_composite": None, "need_grounded": None,
        })
        t["n_candidates"] += 1

        rc, rg = p.get("rank_composite"), p.get("rank_grounded")
        if rc is not None:
            if t["best_rank_composite"] is None or rc < t["best_rank_composite"]:
                t["best_rank_composite"] = int(rc)
            if rc <= 100:
                t["n_top100"] += 1
        if rg is not None and (t["best_rank_grounded"] is None
                               or rg < t["best_rank_grounded"]):
            t["best_rank_grounded"] = int(rg)
        if p.get("in_top50_both"):
            t["n_top50_both"] += 1

        t["best_site_reach"] = max(t["best_site_reach"], int(p.get("reach_raw") or 0))

        g = p.get("greedy_pick_order")
        if g is not None and (t["best_greedy_pick_order"] is None
                              or g < t["best_greedy_pick_order"]):
            t["best_greedy_pick_order"] = int(g)

        # NEED is a tract attribute, identical for every candidate in the tract.
        for k, src_k in (("need_composite", "need_composite"),
                         ("need_grounded", "need_grounded")):
            if t[k] is None and p.get(src_k) is not None:
                t[k] = round(float(p[src_k]), 6)

    feats = []
    for geoid in sorted(by_tract):
        props = {"geoid": geoid}
        props.update(by_tract[geoid])
        props.update(context.get(geoid, {}))
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "Point", "coordinates": points[geoid]}})
    return fc(feats)


def build_universe(points):
    """Full candidate universe as a per-tract count. No per-site geometry."""
    src = load_json("placement_candidates.geojson")
    counts = {}
    for f in src.get("features", []):
        geoid = str(((f.get("properties") or {}).get("geoid")) or "")
        if not geoid or geoid == "None" or geoid not in points:
            continue
        counts[geoid] = counts.get(geoid, 0) + 1

    feats = [{
        "type": "Feature",
        "properties": {"geoid": g, "n_candidates": counts[g]},
        "geometry": {"type": "Point", "coordinates": points[g]},
    } for g in sorted(counts)]
    return fc(feats)


def build_meta():
    """Public meta: greedy headline and agreement stats, no site identifiers.

    The greedy picks keep pick_order, geoid and the de-overlapped
    newly-covered counts -- the numbers the manuscript reports -- but lose the
    name, address and ZIP of the storefront each pick landed on.
    """
    meta = strip_identifiers(load_json("placement_ranked_meta.json"))
    meta["redaction"] = (
        "Public copy of data/clean/placement_ranked_meta.json. Candidate "
        "names, addresses, ZIPs and business categories removed, and the "
        "per-site top-10 listings dropped; wall-clock timings dropped. "
        "Ranking numbers, greedy coverage gains and agreement statistics are "
        "unchanged."
    )
    return meta


def main():
    os.makedirs(DATA_PUBLIC, exist_ok=True)
    points = tract_points()
    context = tract_context()
    log(f"Tract representative points: {len(points):,}")

    opp = build_opportunity(points, context)
    save_json(opp, os.path.join(DATA_PUBLIC, "placement_opportunity_tracts.geojson"))
    log(f"Opportunity tracts: {len(opp['features']):,}")

    uni = build_universe(points)
    save_json(uni, os.path.join(DATA_PUBLIC, "placement_universe_tracts.geojson"))
    log(f"Universe tracts: {len(uni['features']):,}")

    meta = build_meta()
    save_json(meta, os.path.join(DATA_PUBLIC, "placement_ranked_meta.json"))

    # Fail loudly rather than ship an identifier: the whole point of the stage.
    # Feature properties are checked, not the whole document: GeoJSON's own
    # crs.properties.name is structural and has nothing to do with a business.
    checked = [json.dumps(meta)]
    checked += [json.dumps(f["properties"]) for f in opp["features"] + uni["features"]]
    for key in sorted(IDENTIFYING_KEYS):
        for blob in checked:
            if f'"{key}"' in blob:
                raise SystemExit(
                    f"REDACTION FAILED: '{key}' present in public output")
    log("Redaction check passed: no identifying keys in data/public/")


if __name__ == "__main__":
    main()
