#!/usr/bin/env python3
"""
Placement ranking (NEED x REACH).

Ranks candidate storefronts by combining:
  REACH (measured) -- currently unserved residents within a half-mile walk.
  NEED (two variants) -- the five-layer composite index, and a mortality-free
  "grounded" index, so the ranking can be checked against a specification that
  contains no suppressed mortality input at all.

Inputs (data/clean/): placement_candidates.geojson, block_access.csv,
composite_index.geojson, svi_tracts.geojson, block_access_tracts.geojson

Outputs (data/clean/): placement_ranked.geojson, placement_ranked.csv,
placement_ranked_meta.json

Run: ./venv/bin/python scripts/rank_candidates.py
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import geopandas as gpd
from scipy.spatial import cKDTree
from scipy.stats import spearmanr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA_CLEAN as CLEAN, pipeline_date

MILE_M = 1609.344          # "currently unserved" threshold on nearest supply
HALF_MILE_WALK_M = 805.0   # walk radius around a candidate storefront
PROJ_CRS = "EPSG:32138"    # NAD83 / Texas North Central, metres
N_GREEDY_PICKS = 50
EXPECTED_RANKED_TRACTS = 643

REACH_DEFINITION = (
    "REACH for each candidate is the sum of block population over 2020 census "
    "blocks that are (a) currently unserved (distance to nearest naloxone supply "
    "> 1609.344 m, i.e. > 1 mile) AND (b) within 805 m of the candidate storefront. "
    "This is 'unserved residents within a half-mile walk of this storefront.' "
    "Computed with a cKDTree spatial join in EPSG:32138 (both candidate points and "
    "block internal points projected to meters)."
)


def log(msg, t0):
    print(f"[{time.time()-t0:7.2f}s] {msg}", flush=True)


def main():
    t0 = time.time()
    timings = {}

    log("Loading inputs...", t0)
    cand = gpd.read_file(os.path.join(CLEAN, "placement_candidates.geojson"))
    blocks = pd.read_csv(os.path.join(CLEAN, "block_access.csv"))
    comp = gpd.read_file(os.path.join(CLEAN, "composite_index.geojson"))
    svi = gpd.read_file(os.path.join(CLEAN, "svi_tracts.geojson"))
    bat = gpd.read_file(os.path.join(CLEAN, "block_access_tracts.geojson"))
    timings["load"] = round(time.time() - t0, 2)
    log(f"Loaded: {len(cand)} candidates, {len(blocks)} blocks, {len(comp)} tracts", t0)

    n_total = len(cand)
    DIST_COL = "dist_supply_m"

    # Excluded: candidates with a null geoid (outside county tracts) and those in
    # a zero-population tract, which carries no NEED score.
    for g in (comp, svi, bat):
        g["geoid"] = g["geoid"].astype(str)
    cand["geoid"] = cand["geoid"].astype("string")  # keep <NA> distinct from 'nan'

    ranked_tracts = set(comp.loc[~comp["excluded"], "geoid"])
    excluded_tracts = set(comp.loc[comp["excluded"], "geoid"])
    if len(ranked_tracts) != EXPECTED_RANKED_TRACTS:
        log(f"  NOTE: {len(ranked_tracts)} ranked tracts "
            f"(published vintage had {EXPECTED_RANKED_TRACTS})", t0)

    null_geoid_mask = cand["geoid"].isna()
    n_null_geoid = int(null_geoid_mask.sum())

    has_geoid = cand.loc[~null_geoid_mask].copy()
    has_geoid["geoid"] = has_geoid["geoid"].astype(str)
    in_excluded_tract_mask = has_geoid["geoid"].isin(excluded_tracts)
    n_excluded_tract = int(in_excluded_tract_mask.sum())

    r = has_geoid.loc[has_geoid["geoid"].isin(ranked_tracts)].copy().reset_index(drop=True)
    n_ranked = len(r)
    log(f"Excluded {n_null_geoid} null-geoid + {n_excluded_tract} zero-pop-tract "
        f"= {n_null_geoid + n_excluded_tract}; ranking {n_ranked}", t0)
    assert n_ranked == n_total - n_null_geoid - n_excluded_tract

    log("Computing REACH via cKDTree...", t0)
    t = time.time()
    unserved = blocks.loc[blocks[DIST_COL] > MILE_M].copy().reset_index(drop=True)
    log(f"  unserved populated blocks: {len(unserved)} "
        f"({int(unserved['pop'].sum())} residents)", t0)

    bpts = gpd.GeoSeries(
        gpd.points_from_xy(unserved["intpt_lon"], unserved["intpt_lat"]),
        crs="EPSG:4326").to_crs(PROJ_CRS)
    block_xy = np.column_stack([bpts.x.values, bpts.y.values])
    block_pop = unserved["pop"].to_numpy()

    cpts = r.geometry.to_crs(PROJ_CRS)
    cand_xy = np.column_stack([cpts.x.values, cpts.y.values])

    tree = cKDTree(block_xy)
    neighbor_lists = tree.query_ball_point(cand_xy, r=HALF_MILE_WALK_M)
    reach_raw = np.array([int(block_pop[idx].sum()) if idx else 0
                          for idx in neighbor_lists], dtype=np.int64)
    r["reach_raw"] = reach_raw
    timings["reach"] = round(time.time() - t, 2)
    log(f"  REACH done. max={reach_raw.max()} mean={reach_raw.mean():.1f} "
        f"nonzero={(reach_raw>0).sum()}", t0)

    log("Building NEED variants...", t0)

    comp_score = comp.set_index("geoid")["composite_score"]
    r["need_composite"] = r["geoid"].map(comp_score).astype(float)

    tr = pd.DataFrame({"geoid": sorted(ranked_tracts)})
    svi_cols = ["pct_poverty_acs2024", "pct_uninsured_acs2024", "svi_overall"]
    tr = tr.merge(svi[["geoid"] + svi_cols], on="geoid", how="left")
    tr = tr.merge(bat[["geoid", "pct_pop_beyond_1mi"]], on="geoid", how="left")
    grounded_layers = svi_cols + ["pct_pop_beyond_1mi"]

    norm = pd.DataFrame(index=tr.index)
    for col in grounded_layers:
        v = tr[col].astype(float)
        lo, hi = v.min(), v.max()
        norm[col] = (v - lo) / (hi - lo) if hi > lo else 0.0
    # Equal weight over available layers only; a null layer is dropped from that
    # tract's mean rather than imputed, and flagged via data_completeness.
    tr["need_grounded"] = norm.mean(axis=1, skipna=True)
    tr["n_layers_available"] = norm.notna().sum(axis=1).astype(int)
    tr["data_completeness"] = tr["n_layers_available"].map(
        lambda n: "complete" if n == len(grounded_layers) else f"partial_{n}of{len(grounded_layers)}")

    gmap = tr.set_index("geoid")["need_grounded"]
    dcmap = tr.set_index("geoid")["data_completeness"]
    r["need_grounded"] = r["geoid"].map(gmap).astype(float)
    r["data_completeness"] = r["geoid"].map(dcmap)
    n_partial = int((tr["n_layers_available"] < len(grounded_layers)).sum())
    log(f"  grounded index built; {n_partial} tracts with partial layers", t0)

    r["tier"] = r["geoid"].map(comp.set_index("geoid")["tier"])

    log("Normalizing and scoring...", t0)

    def minmax(s):
        lo, hi = s.min(), s.max()
        return (s - lo) / (hi - lo) if hi > lo else pd.Series(0.0, index=s.index)

    nNEEDc = minmax(r["need_composite"])
    nNEEDg = minmax(r["need_grounded"])
    nREACH = minmax(r["reach_raw"].astype(float))

    r["rank_score_composite"] = nNEEDc * nREACH
    r["rank_score_grounded"] = nNEEDg * nREACH

    # Rank 1 = best; ties broken by raw reach descending.
    def make_rank(score_col):
        order = r.sort_values([score_col, "reach_raw"], ascending=[False, False]).index
        rank = pd.Series(np.arange(1, len(order) + 1), index=order)
        return rank.reindex(r.index).astype(int)

    r["rank_composite"] = make_rank("rank_score_composite")
    r["rank_grounded"] = make_rank("rank_score_grounded")

    top50_c = set(r.loc[r["rank_composite"] <= 50].index)
    top50_g = set(r.loc[r["rank_grounded"] <= 50].index)
    top100_c = set(r.loc[r["rank_composite"] <= 100].index)
    top100_g = set(r.loc[r["rank_grounded"] <= 100].index)
    both_top50 = top50_c & top50_g
    r["in_top50_both"] = r.index.isin(both_top50)

    rho, pval = spearmanr(r["rank_composite"], r["rank_grounded"])
    overlap50 = len(both_top50)
    overlap100 = len(top100_c & top100_g)
    log(f"  Spearman rho={rho:.4f}, top50 overlap={overlap50}, "
        f"top100 overlap={overlap100}", t0)

    # Rank-agnostic greedy coverage: repeatedly take the candidate covering the
    # most not-yet-covered unserved residents. Its ordering differs from the
    # NEED x REACH ranking above and is reported separately.
    log("Running pure-reach greedy coverage...", t0)
    t = time.time()
    cover = [set(idx) for idx in neighbor_lists]
    covered = np.zeros(len(unserved), dtype=bool)
    remaining = set(range(n_ranked))

    r["greedy_pick_order"] = pd.Series(pd.NA, index=r.index, dtype="Int64")
    greedy_picks = []
    cum_new = 0
    ridx = r.index.to_numpy()  # positional -> DataFrame index
    for pick in range(1, N_GREEDY_PICKS + 1):
        best_i, best_gain, best_blocks = -1, -1, None
        for i in remaining:
            new_blocks = [b for b in cover[i] if not covered[b]]
            gain = int(block_pop[new_blocks].sum()) if new_blocks else 0
            if gain > best_gain:
                best_i, best_gain, best_blocks = i, gain, new_blocks
        if best_i < 0 or best_gain <= 0:
            log(f"  greedy stopped early at pick {pick} (no positive gain)", t0)
            break
        for b in best_blocks:
            covered[b] = True
        remaining.discard(best_i)
        cum_new += best_gain
        dfidx = ridx[best_i]
        r.at[dfidx, "greedy_pick_order"] = pick
        greedy_picks.append({
            "pick_order": pick,
            "name": r.at[dfidx, "name"],
            "address": r.at[dfidx, "address"],
            "zip": str(r.at[dfidx, "zip"]),
            "category": r.at[dfidx, "category"],
            "geoid": r.at[dfidx, "geoid"],
            "newly_covered_unserved_residents": best_gain,
            "cumulative_newly_covered": cum_new,
            "rank_composite": int(r.at[dfidx, "rank_composite"]),
            "rank_grounded": int(r.at[dfidx, "rank_grounded"]),
        })
    timings["greedy"] = round(time.time() - t, 2)

    def cum_at(n):
        return int(greedy_picks[n - 1]["cumulative_newly_covered"]) if len(greedy_picks) >= n else None
    coverage_gain_summary = {
        "after_10_sites": cum_at(10),
        "after_25_sites": cum_at(25),
        "after_50_sites": cum_at(50),
        "total_unserved_residents": int(unserved["pop"].sum()),
    }
    log(f"  greedy: 10={cum_at(10)} 25={cum_at(25)} 50={cum_at(50)}", t0)

    cums = [p["cumulative_newly_covered"] for p in greedy_picks]
    assert all(cums[i] <= cums[i + 1] for i in range(len(cums) - 1)), "greedy not monotone"
    assert all(p["newly_covered_unserved_residents"] > 0 for p in greedy_picks)

    per_cat = {}
    for catv, grp in r.groupby("category"):
        top = grp.sort_values(["rank_score_composite", "reach_raw"],
                              ascending=[False, False]).head(10)
        per_cat[catv] = [{
            "name": row["name"], "address": row["address"], "zip": str(row["zip"]),
            "geoid": row["geoid"], "reach_raw": int(row["reach_raw"]),
            "rank_composite": int(row["rank_composite"]),
            "rank_score_composite": round(float(row["rank_score_composite"]), 6),
        } for _, row in top.iterrows()]

    # Independent brute-force recomputation of the top pick's reach, as a check
    # on the KD-tree radius join.
    top_idx = r.sort_values(["rank_composite"]).index[0]
    tp = r.loc[top_idx]
    tp_pt = gpd.GeoSeries([tp.geometry], crs="EPSG:4326").to_crs(PROJ_CRS).iloc[0]
    d = np.hypot(block_xy[:, 0] - tp_pt.x, block_xy[:, 1] - tp_pt.y)
    manual_reach = int(block_pop[d <= HALF_MILE_WALK_M].sum())
    sanity_ok = manual_reach == int(tp["reach_raw"])
    log(f"  SANITY top pick '{tp['name']}': KDTree reach={int(tp['reach_raw'])} "
        f"vs manual brute-force={manual_reach} -> {'OK' if sanity_ok else 'MISMATCH'}", t0)
    assert sanity_ok, "KDTree reach join sanity check failed"

    log("Writing outputs...", t0)
    out_cols = ["name", "address", "zip", "category", "geoid", "tier",
                "reach_raw", "need_composite", "need_grounded",
                "rank_score_composite", "rank_score_grounded",
                "rank_composite", "rank_grounded", "in_top50_both",
                "greedy_pick_order", "data_completeness"]
    out = r[out_cols + ["geometry"]].copy()

    gj_path = os.path.join(CLEAN, "placement_ranked.geojson")
    out.to_file(gj_path, driver="GeoJSON")

    csv_path = os.path.join(CLEAN, "placement_ranked.csv")
    out.drop(columns="geometry").sort_values("rank_composite").to_csv(csv_path, index=False)

    top10_comp = r.sort_values("rank_composite").head(10)
    top10_list = [{
        "rank_composite": int(row["rank_composite"]), "name": row["name"],
        "address": row["address"], "zip": str(row["zip"]),
        "category": row["category"], "geoid": row["geoid"],
        "reach_raw": int(row["reach_raw"]),
        "rank_score_composite": round(float(row["rank_score_composite"]), 6),
        "rank_grounded": int(row["rank_grounded"]),
    } for _, row in top10_comp.iterrows()]

    meta = {
        "generated_date": pipeline_date().isoformat(),
        "module": "PLACEMENT RANKING (NEED x REACH)",
        "method_statement": {
            "reach_definition": REACH_DEFINITION,
            "unserved_threshold_m": MILE_M,
            "walk_radius_m": HALF_MILE_WALK_M,
            "projection": PROJ_CRS,
            "block_distance_column_used": DIST_COL,
            "block_distance_column_note": (
                "block_access.csv has no literal 'dist_nearest_any_m'; the spec's "
                "'distance to nearest any supply' is dist_supply_m (verified: "
                "dist_supply_m<=1609.344 matches within_1mi_supply exactly, and its "
                "pop-weighted tract mean tracks composite raw_dist_nearest_any_m)."),
        },
        "need_variants": {
            "need_composite": (
                "Existing 5-layer composite_score from composite_index.geojson. "
                "INCLUDES CDC model-based (modeled) overdose mortality as one layer."),
            "need_grounded": (
                f"Equal-weight mean of min-max-normalized layers across the "
                f"{len(ranked_tracts)} ranked tracts: pct_poverty_acs2024, "
                "pct_uninsured_acs2024, svi_overall, pct_pop_beyond_1mi. Contains NO "
                "mortality input. Nulls handled by averaging over available layers; "
                "data_completeness flags partials."),
            "grounded_layers": grounded_layers,
            "grounded_partial_completeness_tracts": n_partial,
        },
        "normalization": {
            "need_and_reach": (
                "NEED and REACH each min-max normalized to 0-1 across the ranked "
                "candidates. rank_score_composite = norm(need_composite) x norm(reach_raw); "
                "rank_score_grounded = norm(need_grounded) x norm(reach_raw)."),
            "reach_raw_emitted": (
                "reach_raw (integer residents) emitted alongside so no information is "
                "lost by normalization."),
            "skew_note": (
                f"REACH is heavily right-skewed: mean={float(reach_raw.mean()):.1f}, "
                f"median={float(np.median(reach_raw)):.1f}, max={int(reach_raw.max())}, "
                f"{int((reach_raw==0).sum())} of {n_ranked} candidates have reach_raw=0. "
                "Normalization is on the raw values (not log/rank transformed)."),
            "tie_break": "Ranks: 1=best; ties broken by reach_raw descending.",
        },
        "agreement_stats": {
            "spearman_rho_composite_vs_grounded": round(float(rho), 6),
            "spearman_pvalue": float(pval),
            "top50_overlap_count": overlap50,
            "top100_overlap_count": overlap100,
            "n_ranked_candidates": n_ranked,
        },
        "greedy_coverage_gain": {
            "method": (
                "Rank-agnostic pure-reach greedy: iteratively pick the candidate "
                f"covering the most currently-uncovered unserved residents (within "
                f"{HALF_MILE_WALK_M:.0f} m), remove those blocks from the pool, repeat "
                f"{N_GREEDY_PICKS} times. De-overlapped, pure block-count arithmetic. "
                "Each pick's both-variant ranks recorded."),
            "cumulative_summary": coverage_gain_summary,
            "picks": greedy_picks,
        },
        "per_category_top10_by_composite": per_cat,
        "exclusions": {
            "total_candidates": n_total,
            "excluded_total": n_null_geoid + n_excluded_tract,
            "null_geoid_outside_county_tracts": n_null_geoid,
            "in_excluded_tract_zero_or_null_population": n_excluded_tract,
            "ranked_candidates": n_ranked,
        },
        "sanity_check": {
            "top_pick_name": tp["name"],
            "kdtree_reach": int(tp["reach_raw"]),
            "brute_force_reach": manual_reach,
            "match": bool(sanity_ok),
        },
        "top10_by_composite": top10_list,
        "timings_sec": timings,
    }

    meta_path = os.path.join(CLEAN, "placement_ranked_meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)

    chk = gpd.read_file(gj_path)
    assert len(chk) == n_ranked, f"geojson has {len(chk)} feats, expected {n_ranked}"
    assert chk["rank_composite"].notna().all()
    assert chk["rank_grounded"].notna().all()
    assert sorted(chk["rank_composite"]) == list(range(1, n_ranked + 1))
    assert sorted(chk["rank_grounded"]) == list(range(1, n_ranked + 1))
    timings["total"] = round(time.time() - t0, 2)
    log(f"VALIDATED. geojson feats={len(chk)}. total={timings['total']}s", t0)

    print("\n===== FINAL SUMMARY =====")
    print(f"Ranked candidates: {n_ranked} (of {n_total}; excluded "
          f"{n_null_geoid} null-geoid + {n_excluded_tract} zero-pop-tract)")
    print(f"Total currently-unserved residents (>1mi from supply): "
          f"{coverage_gain_summary['total_unserved_residents']:,}")
    print("\nTop 10 by COMPOSITE variant (name | category | reach_raw):")
    for x in top10_list:
        print(f"  {x['rank_composite']:2d}. {x['name']}  [{x['category']}]  "
              f"reach={x['reach_raw']}  (grounded rank {x['rank_grounded']})")
    print("\nGreedy coverage-gain (newly-served unserved residents, de-overlapped):")
    print(f"  after 10 sites: {cum_at(10):,}")
    print(f"  after 25 sites: {cum_at(25):,}")
    print(f"  after 50 sites: {cum_at(50):,}")
    print(f"\nSpearman rho (composite vs grounded rank): {rho:.4f}")
    print(f"Top-50 overlap: {overlap50}/50   Top-100 overlap: {overlap100}/100")
    print(f"\nFiles:\n  {gj_path}\n  {csv_path}\n  {meta_path}")


if __name__ == "__main__":
    main()
