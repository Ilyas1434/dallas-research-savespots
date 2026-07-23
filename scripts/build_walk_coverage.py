#!/usr/bin/env python3
"""
build_walk_coverage.py

Adapts the Chicago "% of overdose death POINTS within 500m of naloxone supply"
metric to Dallas, which only has TRACT-level overdose rates (no point-level
deaths). We compute three honestly-labeled proxies:

  (a) % of Dallas County tracts whose GEOMETRIC CENTROID falls within 500m of
      any naloxone supply point.
  (b) Rate-weighted version: sum(od_death_rate_per_100k) over tracts covered
      by (a), divided by sum(od_death_rate_per_100k) over all tracts.
      Documented as a proxy -- it is NOT population- or death-count-weighted.
  (c) % of county POPULATION within 500m of supply, using tract population
      (totpop) from data/clean/svi_tracts.geojson if present. Skipped (with a
      note) if that file is absent.

Depends on:
  data/clean/tract_overdose.geojson    (mortality module; geoid + od rate)
  data/clean/naloxone_locations.geojson (scripts/build_naloxone.py)
  data/clean/svi_tracts.geojson        (optional, for population weighting)

If tract_overdose.geojson is missing, this script exits gracefully, noting
the missing dependency (no rate-weighted metric is possible without it).

Run standalone from repo root:
    ./venv/bin/python scripts/build_walk_coverage.py

Output:
    data/clean/walk_coverage.json
"""

import json
import sys
from pathlib import Path

try:
    import geopandas as gpd
except ImportError:
    print("FATAL: geopandas required. pip install -r requirements.txt", file=sys.stderr)
    raise

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_CLEAN = REPO_ROOT / "data" / "clean"

TRACT_OVERDOSE_PATH = DATA_CLEAN / "tract_overdose.geojson"
NALOXONE_PATH = DATA_CLEAN / "naloxone_locations.geojson"
SVI_PATH = DATA_CLEAN / "svi_tracts.geojson"

PROJECTED_CRS = "EPSG:32138"  # NAD83 / Texas North Central, unit = metre
WALK_RADIUS_M = 500.0

METHOD_NOTE = (
    "Chicago's original metric is % of overdose DEATH POINTS within 500m of a naloxone "
    "supply location. Dallas County only publishes tract-level (not address-level) "
    "overdose rates, so no death points exist to test. This output instead reports: "
    "(a) the % of Dallas County census tracts whose GEOMETRIC centroid (population-"
    "weighted centroid not available) falls within 500m of any naloxone supply point -- "
    "a coarse areal proxy that says nothing about where residents/deaths are distributed "
    "within a tract; (b) a rate-weighted variant summing od_death_rate_per_100k over "
    "covered tracts and dividing by the county-wide sum, which up-weights tracts with "
    "higher modeled overdose rates but is NOT population- or death-count-weighted and "
    "should not be read as '% of deaths near supply'; (c) % of county population within "
    "500m of supply (population assigned to the tract centroid, not distributed within "
    "the tract), computed only if data/clean/svi_tracts.geojson provides tract population "
    "-- otherwise skipped and noted. All three are approximations made necessary by the "
    "lack of point-level mortality data and should be reported as such."
)


def log(msg):
    print(msg, flush=True)


def write_stub(reason):
    out = {
        "generated": "2026-07-23",
        "status": "failed",
        "reason": reason,
        "method_note": METHOD_NOTE,
    }
    out_path = DATA_CLEAN / "walk_coverage.json"
    out_path.write_text(json.dumps(out, indent=2))
    log(f"[output] wrote failure stub -> {out_path.relative_to(REPO_ROOT)}")


def main():
    DATA_CLEAN.mkdir(parents=True, exist_ok=True)

    log("=" * 70)
    log("Load dependencies")
    log("=" * 70)

    if not TRACT_OVERDOSE_PATH.exists():
        reason = (
            f"{TRACT_OVERDOSE_PATH.relative_to(REPO_ROOT)} not found -- mortality module "
            "dependency missing. Cannot compute tract-centroid or rate-weighted coverage "
            "without tract geometry + od_death_rate_per_100k. Exiting gracefully."
        )
        log(f"[FATAL] {reason}")
        write_stub(reason)
        return

    if not NALOXONE_PATH.exists():
        reason = (
            f"{NALOXONE_PATH.relative_to(REPO_ROOT)} not found -- run "
            "scripts/build_naloxone.py first."
        )
        log(f"[FATAL] {reason}")
        write_stub(reason)
        return

    tracts = gpd.read_file(TRACT_OVERDOSE_PATH)
    log(f"[tracts] loaded {len(tracts)} Dallas County tracts w/ od_death_rate_per_100k")

    supply = gpd.read_file(NALOXONE_PATH)
    n_supply_total = len(supply)
    supply = supply[supply.geometry.notna()].copy()
    log(f"[supply] loaded {n_supply_total} naloxone features, {len(supply)} with usable geometry")

    have_pop = SVI_PATH.exists()
    pop_by_geoid = {}
    if have_pop:
        svi = gpd.read_file(SVI_PATH)
        if "totpop" in svi.columns:
            pop_by_geoid = dict(zip(svi["geoid"].astype(str), svi["totpop"]))
            log(f"[population] loaded totpop for {len(pop_by_geoid)} tracts from "
                f"{SVI_PATH.relative_to(REPO_ROOT)}")
        else:
            have_pop = False
            log(f"[population] {SVI_PATH.relative_to(REPO_ROOT)} present but has no 'totpop' "
                f"column -- skipping population-weighted metric")
    else:
        log(f"[population] {SVI_PATH.relative_to(REPO_ROOT)} not found -- skipping "
            f"population-weighted metric (c)")

    log("")
    log("=" * 70)
    log(f"Project to {PROJECTED_CRS} and test tract centroids against {WALK_RADIUS_M:.0f}m radius")
    log("=" * 70)
    tracts_proj = tracts.to_crs(PROJECTED_CRS)
    supply_proj = supply.to_crs(PROJECTED_CRS)
    tracts_proj["centroid"] = tracts_proj.geometry.centroid

    if len(supply_proj):
        try:
            supply_union = supply_proj.geometry.union_all()
        except AttributeError:
            supply_union = supply_proj.geometry.unary_union
    else:
        supply_union = None

    covered_geoids = []
    tract_rows = []
    for _, row in tracts_proj.iterrows():
        geoid = str(row.get("geoid"))
        centroid = row["centroid"]
        if supply_union is not None:
            dist = centroid.distance(supply_union)
        else:
            dist = None
        covered = (dist is not None and dist <= WALK_RADIUS_M)
        if covered:
            covered_geoids.append(geoid)
        rate = row.get("od_death_rate_per_100k")
        tract_rows.append({
            "geoid": geoid,
            "dist_centroid_to_nearest_supply_m": round(dist, 1) if dist is not None else None,
            "within_500m": covered,
            "od_death_rate_per_100k": rate,
            "totpop": pop_by_geoid.get(geoid) if have_pop else None,
        })

    n_tracts = len(tract_rows)
    n_covered = len(covered_geoids)
    pct_tracts_covered = round(100 * n_covered / n_tracts, 2) if n_tracts else None

    log(f"[metric a] tracts within 500m of any supply: {n_covered} / {n_tracts} = {pct_tracts_covered}%")

    log("")
    log("=" * 70)
    log("Metric (b): rate-weighted coverage")
    log("=" * 70)
    rates = [(r["geoid"], r["od_death_rate_per_100k"]) for r in tract_rows]
    valid_rates = [(g, v) for g, v in rates if v is not None]
    total_rate = sum(v for _, v in valid_rates)
    covered_rate = sum(v for g, v in valid_rates if g in set(covered_geoids))
    pct_rate_weighted = round(100 * covered_rate / total_rate, 2) if total_rate else None
    log(f"[metric b] sum(rate) covered={covered_rate:.1f} / total={total_rate:.1f} "
        f"= {pct_rate_weighted}% (proxy, NOT population/death-count weighted)")
    n_tracts_with_rate = len(valid_rates)
    if n_tracts_with_rate < n_tracts:
        log(f"[metric b] note: {n_tracts - n_tracts_with_rate} tract(s) missing "
            f"od_death_rate_per_100k, excluded from rate-weighted sums")

    log("")
    log("=" * 70)
    log("Metric (c): population-weighted coverage")
    log("=" * 70)
    pct_pop_weighted = None
    pop_summary = None
    if have_pop:
        pops = [(r["geoid"], r["totpop"]) for r in tract_rows]
        valid_pops = [(g, p) for g, p in pops if p is not None]
        total_pop = sum(p for _, p in valid_pops)
        covered_pop = sum(p for g, p in valid_pops if g in set(covered_geoids))
        pct_pop_weighted = round(100 * covered_pop / total_pop, 2) if total_pop else None
        pop_summary = {
            "total_population": int(total_pop),
            "population_within_500m": int(covered_pop),
            "n_tracts_with_pop": len(valid_pops),
        }
        log(f"[metric c] population covered={int(covered_pop):,} / total={int(total_pop):,} "
            f"= {pct_pop_weighted}%")
        log("[metric c] NOTE: population is assigned at the tract centroid (all-or-nothing "
            "per tract), not distributed within the tract's area -- coarse proxy.")
    else:
        log("[metric c] SKIPPED -- no tract population data available "
            "(data/clean/svi_tracts.geojson absent or missing 'totpop')")

    out = {
        "generated": "2026-07-23",
        "status": "ok",
        "method_note": METHOD_NOTE,
        "walk_radius_m": WALK_RADIUS_M,
        "crs_used": PROJECTED_CRS,
        "n_tracts": n_tracts,
        "n_supply_points_used": len(supply_proj),
        "metrics": {
            "a_pct_tracts_centroid_within_500m": pct_tracts_covered,
            "b_pct_rate_weighted_within_500m": pct_rate_weighted,
            "c_pct_population_within_500m": pct_pop_weighted,
        },
        "metric_b_detail": {
            "sum_rate_covered": round(covered_rate, 2),
            "sum_rate_total": round(total_rate, 2),
            "n_tracts_with_rate": n_tracts_with_rate,
        },
        "metric_c_detail": pop_summary,
        "tracts": tract_rows,
    }

    out_path = DATA_CLEAN / "walk_coverage.json"
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=2))
    tmp.replace(out_path)
    log(f"[output] wrote {out_path.relative_to(REPO_ROOT)}")

    check = json.loads(out_path.read_text())
    assert check["status"] == "ok"
    assert len(check["tracts"]) == n_tracts
    log(f"[validate] reloaded OK, {len(check['tracts'])} tract records")

    log("")
    log("=" * 70)
    log("SUMMARY")
    log("=" * 70)
    log(f"(a) tracts w/ centroid within 500m of any supply: {pct_tracts_covered}%")
    log(f"(b) rate-weighted proxy within 500m: {pct_rate_weighted}%")
    log(f"(c) population within 500m: {pct_pop_weighted}% " +
        ("" if have_pop else "(SKIPPED - no population data)"))


if __name__ == "__main__":
    main()
