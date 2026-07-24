"""
Composite tract-level overdose-vulnerability index.

Five layers, min-max normalized and equal-weighted following the CDC/ATSDR SVI
convention, tiered by Jenks natural breaks with Tier 1 highest. Two re-weighted
variants re-run the whole pipeline (renormalized weights, recomputed breaks) and
are compared tract by tract, so the tiering's sensitivity to the weight choice is
reported rather than assumed.

Inputs (data/clean/): tract_overdose.geojson, svi_tracts.geojson,
coverage_gap.json

Outputs (data/clean/): composite_index.geojson, composite_methods.json

Run: ./venv/bin/python scripts/build_composite.py
"""
import json
import os
import datetime

import jenkspy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN = os.path.join(REPO_ROOT, "data", "clean")


def load_json(name):
    with open(os.path.join(CLEAN, name)) as f:
        return json.load(f)


# direction "up": higher raw value means higher vulnerability.
# direction "down": lower raw value means higher vulnerability (inverted below).
LAYERS = [
    {
        "key": "od_death_rate_per_100k",
        "label": "Overdose mortality rate (per 100k)",
        "source": "CDC NCHS drug overdose death rates by census tract (4day-mt2f), model-based",
        "vintage": "2025-01/2025-12 (TTM), pulled 2026-07-22",
        "direction": "up",
    },
    {
        "key": "pct_poverty_acs2024",
        "label": "Poverty rate (% below poverty)",
        "source": "ACS 5-year S1701_C03_001E",
        "vintage": "ACS 2020-2024 5-year (released Jan 2026)",
        "direction": "up",
    },
    {
        "key": "pct_uninsured_acs2024",
        "label": "Uninsured rate (%)",
        "source": "ACS 5-year S2701_C05_001E",
        "vintage": "ACS 2020-2024 5-year (released Jan 2026)",
        "direction": "up",
    },
    {
        "key": "dist_nearest_any_m",
        "label": "Distance to nearest naloxone access point (m)",
        "source": "coverage_gap.json (tract centroid -> nearest supply, EPSG:32138)",
        "vintage": "generated 2026-07-23 from naloxone_locations.geojson (38 supply pts)",
        "direction": "up",
    },
    {
        "key": "dart_stops_halfmile",
        "label": "Transit connectivity (DART stops within 0.5 mi) [inverted]",
        "source": "svi_tracts.geojson dart_stops_halfmile",
        "vintage": "DART GTFS via context module 2026-07-23",
        "direction": "down",
    },
]
LAYER_KEYS = [layer["key"] for layer in LAYERS]

# Base weights; renormalized to sum to 1 before use.
WEIGHT_VARIANTS = {
    "primary_equal": {k: 1.0 for k in LAYER_KEYS},
    "mortality_doubled": {**{k: 1.0 for k in LAYER_KEYS}, "od_death_rate_per_100k": 2.0},
    "distance_doubled": {**{k: 1.0 for k in LAYER_KEYS}, "dist_nearest_any_m": 2.0},
}


def normalize_weights(w):
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def build_records():
    """Join the three sources on geoid; return dict geoid -> record."""
    overdose = {f["properties"]["geoid"]: f for f in load_json("tract_overdose.geojson")["features"]}
    svi = {f["properties"]["geoid"]: f for f in load_json("svi_tracts.geojson")["features"]}
    coverage = {t["geoid"]: t for t in load_json("coverage_gap.json")["tracts"]}

    geoids = list(svi.keys())
    records = {}
    for g in geoids:
        op = overdose[g]["properties"]
        sp = svi[g]["properties"]
        cp = coverage.get(g, {})
        totpop = sp.get("totpop")
        raw = {
            "od_death_rate_per_100k": op.get("od_death_rate_per_100k"),
            "pct_poverty_acs2024": sp.get("pct_poverty_acs2024"),
            "pct_uninsured_acs2024": sp.get("pct_uninsured_acs2024"),
            "dist_nearest_any_m": cp.get("dist_nearest_any_m"),
            "dart_stops_halfmile": sp.get("dart_stops_halfmile"),
        }
        excluded = (totpop is None) or (totpop == 0)
        records[g] = {
            "geoid": g,
            "geometry": svi[g]["geometry"],
            "totpop": totpop,
            "count_suppressed": bool(op.get("count_suppressed")),
            "raw": raw,
            "excluded": excluded,
            "excluded_reason": "zero or null population" if excluded else None,
        }
    return records, geoids


def compute_minmax(records, ranked_geoids):
    """Per-layer min/max over ranked (non-excluded) tracts, nulls skipped."""
    stats = {}
    for k in LAYER_KEYS:
        vals = [records[g]["raw"][k] for g in ranked_geoids if records[g]["raw"][k] is not None]
        stats[k] = {"min": min(vals), "max": max(vals), "n": len(vals)}
    return stats


def normalize_value(val, k, stats):
    """Min-max to [0,1] with direction applied (1 = most vulnerable)."""
    if val is None:
        return None
    lo, hi = stats[k]["min"], stats[k]["max"]
    if hi == lo:
        return 0.0
    scaled = (val - lo) / (hi - lo)
    direction = next(layer["direction"] for layer in LAYERS if layer["key"] == k)
    return 1.0 - scaled if direction == "down" else scaled


def composite_for(norm, weights):
    """Weighted mean over layers with a non-null normalized value
    (i.e. rescaled by available weight share). Returns (score, n_available)."""
    num = 0.0
    wsum = 0.0
    n = 0
    for k in LAYER_KEYS:
        nv = norm.get(k)
        if nv is None:
            continue
        num += weights[k] * nv
        wsum += weights[k]
        n += 1
    if wsum == 0:
        return None, 0
    return num / wsum, n


def assign_tiers(scores_by_geoid):
    """Jenks 3-class on scores. Returns (tier_map geoid->1/2/3, breaks list).
    Tier 1 = highest vulnerability (highest composite)."""
    geoids = list(scores_by_geoid.keys())
    values = [scores_by_geoid[g] for g in geoids]
    breaks = jenkspy.jenks_breaks(values, n_classes=3)
    b1, b2 = breaks[1], breaks[2]

    def tier_of(s):
        if s <= b1:
            jenks_class = 0
        elif s <= b2:
            jenks_class = 1
        else:
            jenks_class = 2
        return 3 - jenks_class  # highest Jenks class becomes Tier 1

    tier_map = {g: tier_of(scores_by_geoid[g]) for g in geoids}
    return tier_map, breaks


TIER_LABELS = {1: "Tier 1 (highest vulnerability)",
               2: "Tier 2 (moderate vulnerability)",
               3: "Tier 3 (lower vulnerability)"}


def main():
    records, geoids = build_records()
    ranked_geoids = [g for g in geoids if not records[g]["excluded"]]
    excluded_geoids = [g for g in geoids if records[g]["excluded"]]

    stats = compute_minmax(records, ranked_geoids)

    # Normalization is weight-independent, so it is done once for all variants.
    for g in ranked_geoids:
        rec = records[g]
        rec["norm"] = {k: normalize_value(rec["raw"][k], k, stats) for k in LAYER_KEYS}
        n_avail = sum(1 for k in LAYER_KEYS if rec["norm"][k] is not None)
        rec["data_completeness"] = "complete" if n_avail == len(LAYER_KEYS) else f"partial ({n_avail}/{len(LAYER_KEYS)})"

    variant_results = {}
    for vname, base_w in WEIGHT_VARIANTS.items():
        w = normalize_weights(base_w)
        scores = {}
        for g in ranked_geoids:
            s, _ = composite_for(records[g]["norm"], w)
            scores[g] = s
        tier_map, breaks = assign_tiers(scores)
        variant_results[vname] = {
            "weights": w,
            "scores": scores,
            "tier_map": tier_map,
            "breaks": breaks,
        }

    primary = variant_results["primary_equal"]

    sensitivity = {}
    for vname, res in variant_results.items():
        if vname == "primary_equal":
            continue
        same = 0
        flips_12 = 0
        flips_13 = 0
        flip_list = []
        for g in ranked_geoids:
            pt = primary["tier_map"][g]
            vt = res["tier_map"][g]
            if pt == vt:
                same += 1
            else:
                pair = {pt, vt}
                if pair == {1, 2}:
                    flips_12 += 1
                elif pair == {1, 3}:
                    flips_13 += 1
                flip_list.append({"geoid": g, "primary_tier": pt, "variant_tier": vt})
        n = len(ranked_geoids)
        sensitivity[vname] = {
            "n_ranked": n,
            "pct_identical_tier": round(100.0 * same / n, 2),
            "n_identical": same,
            "n_flips": n - same,
            "tier1_tier2_flips": flips_12,
            "tier1_tier3_flips": flips_13,
            "flips": flip_list,
        }

    features = []
    tier_counts = {1: 0, 2: 0, 3: 0}
    for g in geoids:
        rec = records[g]
        props = {
            "geoid": g,
            "count_suppressed": rec["count_suppressed"],
            "totpop": rec["totpop"],
        }
        # Raw layer values are always emitted, including for excluded tracts.
        for k in LAYER_KEYS:
            props[f"raw_{k}"] = rec["raw"][k]

        if rec["excluded"]:
            props.update({
                "excluded": True,
                "excluded_reason": rec["excluded_reason"],
                "composite_score": None,
                "tier": None,
                "tier_label": None,
                "data_completeness": "excluded",
            })
            for k in LAYER_KEYS:
                props[f"norm_{k}"] = None
        else:
            score = primary["scores"][g]
            tier = primary["tier_map"][g]
            tier_counts[tier] += 1
            props.update({
                "excluded": False,
                "excluded_reason": None,
                "composite_score": round(score, 6),
                "tier": tier,
                "tier_label": TIER_LABELS[tier],
                "data_completeness": rec["data_completeness"],
            })
            for k in LAYER_KEYS:
                nv = rec["norm"][k]
                props[f"norm_{k}"] = round(nv, 6) if nv is not None else None

        features.append({"type": "Feature", "geometry": rec["geometry"], "properties": props})

    out_geojson = {"type": "FeatureCollection", "features": features}
    with open(os.path.join(CLEAN, "composite_index.geojson"), "w") as f:
        json.dump(out_geojson, f)

    layer_null_counts = {
        k: sum(1 for g in ranked_geoids if records[g]["raw"][k] is None) for k in LAYER_KEYS
    }
    partial_tracts = [g for g in ranked_geoids if records[g]["data_completeness"] != "complete"]
    suppressed_ranked = sum(1 for g in ranked_geoids if records[g]["count_suppressed"])

    methods = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "phase": "Phase 3 - Composite Vulnerability Index",
        "unit": {
            "level": "census tract (2020 census tract boundaries, TIGER2024)",
            "n_total": len(geoids),
            "n_ranked": len(ranked_geoids),
            "n_excluded": len(excluded_geoids),
            "justification": (
                "Census tract is the finest openly available mortality resolution for "
                "Dallas: the CDC NCHS drug-overdose tract product publishes model-based "
                "rates at tract level, and no ZIP-level mortality is published publicly for "
                "Dallas (DSHS no longer releases ZIP-level mortality). ACS poverty/uninsured "
                "and the SVI are natively tract-level, and DART stop counts are aggregated "
                "to tracts. Tract is therefore the common native unit for every layer."
            ),
        },
        "layers": [
            {
                "key": layer["key"],
                "label": layer["label"],
                "source": layer["source"],
                "vintage": layer["vintage"],
                "direction": layer["direction"],
                "direction_note": (
                    "higher raw value = higher vulnerability" if layer["direction"] == "up"
                    else "lower raw value = higher vulnerability (inverted in normalization)"
                ),
                "minmax": stats[layer["key"]],
                "null_count_ranked": layer_null_counts[layer["key"]],
            }
            for layer in LAYERS
        ],
        "excluded_layers": [
            {
                "layer": "non-fatal overdose ED visits",
                "reason": "available only at county level (no tract resolution); county figures "
                          "NOT allocated to tracts to avoid ecological fabrication.",
            },
            {
                "layer": "homelessness / PIT count",
                "reason": "available only at county/CoC level; not allocated to tracts.",
            },
        ],
        "normalization": {
            "method": "min-max to [0,1] across ranked tracts, per layer",
            "null_handling": "nulls excluded from per-layer min/max; a tract with a null layer "
                             "value receives its composite as a weighted mean over its non-null "
                             "layers (weight share rescaled) and is flagged data_completeness=partial.",
            "inverted_layers": ["dart_stops_halfmile"],
            "zero_pop_handling": "tracts with totpop 0 or null are EXCLUDED from ranking entirely "
                                 "(excluded=true, no score/tier).",
        },
        "weights": {
            "convention": "EQUAL weights (0.2 each) for the primary index, following CDC SVI "
                          "equal-weight convention; disclosed as an analyst choice.",
            "primary_equal": normalize_weights(WEIGHT_VARIANTS["primary_equal"]),
            "mortality_doubled": normalize_weights(WEIGHT_VARIANTS["mortality_doubled"]),
            "distance_doubled": normalize_weights(WEIGHT_VARIANTS["distance_doubled"]),
        },
        "composite_formula": "composite = sum(w_i * norm_i) / sum(w_i) over layers with a "
                             "non-null normalized value (equals a plain weighted sum for complete tracts).",
        "tiers": {
            "method": "Jenks natural breaks, 3 classes, on the composite score of ranked tracts",
            "labels": TIER_LABELS,
            "convention": "Tier 1 = highest vulnerability (highest composite).",
            "cutoffs": {
                vname: {
                    "breaks": res["breaks"],
                    "tier1_lower_bound": res["breaks"][2],
                    "tier2_range": [res["breaks"][1], res["breaks"][2]],
                    "tier3_upper_bound": res["breaks"][1],
                    "tier_counts": {
                        str(t): sum(1 for g in ranked_geoids if res["tier_map"][g] == t)
                        for t in (1, 2, 3)
                    },
                }
                for vname, res in variant_results.items()
            },
        },
        "sensitivity": {
            "description": "Each variant re-runs the full pipeline (renormalized weights, "
                           "recomputed Jenks tiers) and is compared tract-by-tract to the primary "
                           "equal-weight tiers.",
            "agreement": {v: {kk: vv for kk, vv in s.items() if kk != "flips"}
                          for v, s in sensitivity.items()},
            "flips_detail": {v: s["flips"] for v, s in sensitivity.items()},
        },
        "tier_assignments": {
            vname: {g: res["tier_map"][g] for g in ranked_geoids}
            for vname, res in variant_results.items()
        },
        "excluded_tracts": [
            {"geoid": g, "totpop": records[g]["totpop"], "reason": records[g]["excluded_reason"]}
            for g in excluded_geoids
        ],
        "data_quality": {
            "suppressed_count_tracts_total": sum(1 for g in geoids if records[g]["count_suppressed"]),
            "suppressed_count_tracts_ranked": suppressed_ranked,
            "layer_null_counts_ranked": layer_null_counts,
            "partial_completeness_tracts": [
                {"geoid": g, "completeness": records[g]["data_completeness"],
                 "null_layers": [k for k in LAYER_KEYS if records[g]["raw"][k] is None]}
                for g in partial_tracts
            ],
            "primary_tier_counts": {str(t): tier_counts[t] for t in (1, 2, 3)},
        },
    }
    with open(os.path.join(CLEAN, "composite_methods.json"), "w") as f:
        json.dump(methods, f, indent=2)

    print("=== COMPOSITE INDEX BUILT ===")
    print(f"tracts total={len(geoids)} ranked={len(ranked_geoids)} excluded={len(excluded_geoids)}")
    print(f"excluded geoids: {excluded_geoids}")
    print(f"primary tier counts: {tier_counts} sum={sum(tier_counts.values())}")
    print(f"primary Jenks breaks: {primary['breaks']}")
    print(f"partial-completeness tracts: {partial_tracts}")
    for v, s in sensitivity.items():
        print(f"sensitivity[{v}]: identical={s['pct_identical_tier']}% "
              f"flips={s['n_flips']} T1<->T2={s['tier1_tier2_flips']} T1<->T3={s['tier1_tier3_flips']}")


if __name__ == "__main__":
    main()
