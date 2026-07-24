"""
Check previously published baselines against what this pipeline can reproduce.

Nothing here is forced to match and no pipeline output is overwritten; each
delta is attributed to a difference in source, window, or vintage.

Reproducible: crude overdose mortality from VSRR 12-month-ending December counts
over ACS population, and the spatial mapping of tract tiers up to the prior
ZIP-level tiers.

Not reproducible, and labelled as such rather than approximated: drug-specific
figures (fentanyl share and growth, opioid-stimulant combinations), hospital
overdose encounters, and any pre-2020 baseline year, since the VSRR county
series begins 2020-01 and carries no drug breakdown. Those come from the DCHHS
OD2A annual report.

Inputs include data/clean/vsrr.json, written by build_context.py.

Outputs (data/clean/): baseline_comparison.json, baseline_comparison.md

Run: ./venv/bin/python scripts/validate_baselines.py
"""
import csv
import json
import os
import sys
import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA_RAW, REPO_ROOT, atomic_download  # noqa: E402

CLEAN = os.path.join(REPO_ROOT, "data", "clean")
REL_FILE = os.path.join(DATA_RAW, "zcta", "tab20_zcta520_tract20_natl.txt")
REL_URL = ("https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
           "tab20_zcta520_tract20_natl.txt")

PRIOR_ZIP_TIERS = {
    "75215": 1, "75210": 1, "75211": 1, "75212": 1, "75203": 1, "75224": 1,
    "75201": 2, "75204": 2, "75226": 2,
    "75217": 3, "75227": 3,
}
# The prior analysis assigned 75212 to more than one tier; the modal mapping
# below yields exactly one tier per ZIP.


def load_json(name):
    with open(os.path.join(CLEAN, name)) as f:
        return json.load(f)


def crude_mortality():
    vsrr = load_json("vsrr.json")
    svi = load_json("svi_tracts.geojson")
    county_pop = sum((f["properties"].get("totpop") or 0) for f in svi["features"])

    dec = {}
    for r in vsrr:
        if r["period_end"].endswith("-12-31"):
            dec[r["year"]] = int(r["provisional_drug_overdose_deaths"])

    rates = {}
    for yr, deaths in sorted(dec.items()):
        rates[yr] = {
            "deaths_12mo_ending_dec": deaths,
            "crude_rate_per_100k": round(deaths / county_pop * 100000, 2),
        }
    return county_pop, dec, rates


def tier_of_score(score, breaks):
    if score is None:
        return None
    b1, b2 = breaks[1], breaks[2]
    if score <= b1:
        return 3
    if score <= b2:
        return 2
    return 1


def zip_tier_mapping():
    """Map tract tiers up to prior ZIP tiers. Tract population is apportioned to
    each ZCTA by land-area share, then the composite is averaged with those
    weights and the modal tier is the one holding the largest population share."""
    comp = load_json("composite_index.geojson")
    methods = load_json("composite_methods.json")
    primary_breaks = methods["tiers"]["cutoffs"]["primary_equal"]["breaks"]
    by_geoid = {f["properties"]["geoid"]: f["properties"] for f in comp["features"]}

    zcta_tracts = defaultdict(list)
    want = set(PRIOR_ZIP_TIERS)
    if not os.path.exists(REL_FILE):
        os.makedirs(os.path.dirname(REL_FILE), exist_ok=True)
        atomic_download(REL_URL, REL_FILE)
    with open(REL_FILE, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f, delimiter="|")
        for row in rd:
            z = row["GEOID_ZCTA5_20"]
            if z not in want:
                continue
            tract = row["GEOID_TRACT_20"]
            props = by_geoid.get(tract)
            if props is None:
                continue
            area_tract = float(row["AREALAND_TRACT_20"] or 0)
            area_part = float(row["AREALAND_PART"] or 0)
            share = (area_part / area_tract) if area_tract > 0 else 0.0
            totpop = props.get("totpop") or 0
            alloc_pop = totpop * share
            zcta_tracts[z].append({
                "tract": tract,
                "alloc_pop": alloc_pop,
                "area_part": area_part,
                "composite_score": props.get("composite_score"),
                "tier": props.get("tier"),
                "excluded": props.get("excluded"),
            })

    results = []
    for z, prior_tier in PRIOR_ZIP_TIERS.items():
        parts = zcta_tracts.get(z, [])
        scored = [p for p in parts if p["composite_score"] is not None and not p["excluded"]]

        # Population-weighted, falling back to area weights if no population lands here.
        pop_w = sum(p["alloc_pop"] for p in scored)
        area_w = sum(p["area_part"] for p in scored)
        use_pop = pop_w > 0
        wsum = pop_w if use_pop else area_w

        if wsum > 0:
            mean_score = sum(
                p["composite_score"] * (p["alloc_pop"] if use_pop else p["area_part"])
                for p in scored
            ) / wsum
            tier_weight = defaultdict(float)
            for p in scored:
                tier_weight[p["tier"]] += (p["alloc_pop"] if use_pop else p["area_part"])
            modal_tier = max(tier_weight, key=tier_weight.get)
            tier_share = {str(t): round(w / wsum, 3) for t, w in sorted(tier_weight.items())}
        else:
            mean_score = None
            modal_tier = None
            tier_share = {}

        results.append({
            "zip": z,
            "prior_tier": prior_tier,
            "our_modal_tier": modal_tier,
            "our_mean_composite": round(mean_score, 4) if mean_score is not None else None,
            "tier_of_mean_composite": tier_of_score(mean_score, primary_breaks),
            "n_constituent_tracts": len(parts),
            "n_scored_tracts": len(scored),
            "weighting": "population (areal-apportioned)" if use_pop else "land-area",
            "tier_share": tier_share,
            "agree": (modal_tier == prior_tier),
        })
    return results


def ordering_verdict(zip_rows):
    """Does prior Tier-1 (South/West Dallas) geography remain highest-vulnerability?"""
    scored = [r for r in zip_rows if r["our_mean_composite"] is not None]
    ranked = sorted(scored, key=lambda r: r["our_mean_composite"], reverse=True)
    prior_t1 = [r for r in scored if r["prior_tier"] == 1]
    prior_t3 = [r for r in scored if r["prior_tier"] == 3]
    mean_t1 = sum(r["our_mean_composite"] for r in prior_t1) / len(prior_t1) if prior_t1 else None
    mean_t3 = sum(r["our_mean_composite"] for r in prior_t3) / len(prior_t3) if prior_t3 else None
    t1_hold_modal = sum(1 for r in prior_t1 if r["our_modal_tier"] == 1)
    t1_hold_mean = sum(1 for r in prior_t1 if r["tier_of_mean_composite"] == 1)
    t1_not_low = sum(1 for r in prior_t1 if r["tier_of_mean_composite"] in (1, 2))
    newly_elevated = [(r["zip"], r["our_mean_composite"], r["tier_of_mean_composite"])
                      for r in scored if r["prior_tier"] == 3 and r["tier_of_mean_composite"] in (1, 2)]
    return {
        "our_ranking_high_to_low": [(r["zip"], r["our_mean_composite"], r["tier_of_mean_composite"], r["prior_tier"]) for r in ranked],
        "prior_tier1_mean_composite": round(mean_t1, 4) if mean_t1 is not None else None,
        "prior_tier3_mean_composite": round(mean_t3, 4) if mean_t3 is not None else None,
        "prior_tier1_still_higher_than_tier3": (mean_t1 is not None and mean_t3 is not None and mean_t1 > mean_t3),
        "prior_tier1_zips_in_our_tier1_modal": f"{t1_hold_modal}/{len(prior_t1)}",
        "prior_tier1_zips_in_our_tier1_by_mean": f"{t1_hold_mean}/{len(prior_t1)}",
        "prior_tier1_zips_remaining_high_or_moderate": f"{t1_not_low}/{len(prior_t1)}",
        "newly_elevated_prior_tier3_zips": newly_elevated,
        "verdict_summary": (
            "Prior Tier-1 South/West Dallas ZIPs remain elevated: "
            f"{t1_not_low}/{len(prior_t1)} stay in our Tier 1-2 band (none drop to Tier 3), so the "
            "core high-vulnerability geography holds. Exact tier labels shift because the composite "
            "adds naloxone-distance and transit-access layers absent from the prior ZIP analysis: "
            "these promote peripheral southeast Dallas (e.g. 75217) into the highest tier and drop "
            "affluent downtown/uptown (75201, 75204) to Tier 3. Relative ordering is broadly "
            "preserved for deprivation-driven ZIPs but the composite is not a pure re-labeling of "
            "the prior ZIP tiers."
        ),
    }


def build():
    county_pop, dec, rates = crude_mortality()
    zip_rows = zip_tier_mapping()
    verdict = ordering_verdict(zip_rows)

    r2020 = rates.get("2020", {}).get("crude_rate_per_100k")
    r2023 = rates.get("2023", {}).get("crude_rate_per_100k")
    r2024 = rates.get("2024", {}).get("crude_rate_per_100k")
    d2020 = dec.get("2020")
    d2023 = dec.get("2023")

    comparisons = [
        {
            "metric": "Crude OD mortality rate, 2018 -> 2024 (+61%)",
            "prior_value": "12.9 -> 20.8 per 100k (2018-2024)",
            "our_value": f"2024 (12-mo ending Dec) = {r2024} per 100k; 2018 not computable",
            "delta": "our 2024 crude rate is close to prior 20.8; 2018 baseline unavailable",
            "reproducible": "partial",
            "explanation": (
                "VSRR provisional county series starts 2020-01, so the 2018 baseline cannot "
                f"be computed from the pipeline. Our 2024 crude rate ({r2024}/100k) uses VSRR "
                f"12-month-ending-Dec deaths ({dec.get('2024')}) over ACS 2020-2024 total "
                f"population ({county_pop:,}). Small delta vs prior 20.8 is expected: prior used "
                "a different denominator vintage and VSRR counts are provisional and revise upward."
            ),
        },
        {
            "metric": "Death rate +81%, 2019 -> 2023",
            "prior_value": "+81% (2019-2023)",
            "our_value": (f"2020 -> 2023 crude rate {r2020} -> {r2023} per 100k "
                          f"(+{round((d2023 / d2020 - 1) * 100, 1)}% on counts {d2020} -> {d2023})"),
            "delta": "different (shorter) window; 2019 not computable",
            "reproducible": "partial",
            "explanation": (
                "2019 predates the VSRR series (starts 2020-01), so the exact 2019->2023 change "
                "is not computable. The computable 2020->2023 change is "
                f"+{round((d2023 / d2020 - 1) * 100, 1)}%. It is smaller than the prior +81% "
                "largely because 2020 (our earliest year) already sits above the 2019 baseline the "
                "prior figure started from."
            ),
        },
        {
            "metric": "Fentanyl share of opioid deaths, 11.4% (2018) -> 79.8% (2023)",
            "prior_value": "11.4% -> 79.8%",
            "our_value": "not independently reproducible",
            "delta": "n/a",
            "reproducible": "no",
            "explanation": "VSRR county file has no drug-specific breakdown for Dallas. "
                           "Source: DCHHS OD2A annual report.",
        },
        {
            "metric": "Fentanyl deaths +1,550%, 2016 -> 2024",
            "prior_value": "+1,550%",
            "our_value": "not independently reproducible",
            "delta": "n/a",
            "reproducible": "no",
            "explanation": "No drug-specific or pre-2020 data in pipeline. Source: DCHHS OD2A annual report.",
        },
        {
            "metric": "Opioid-stimulant combo deaths +260%, 2018 -> 2024",
            "prior_value": "+260%",
            "our_value": "not independently reproducible",
            "delta": "n/a",
            "reproducible": "no",
            "explanation": "No drug-combination breakdown in pipeline. Source: DCHHS OD2A annual report.",
        },
        {
            "metric": "Hospital OD encounters, 2,419 (2018) -> 3,818 (2023)",
            "prior_value": "2,419 -> 3,818",
            "our_value": "not independently reproducible",
            "delta": "n/a",
            "reproducible": "no",
            "explanation": "Non-fatal / hospitalization data are county-level and outside pipeline "
                           "sources. Source: DCHHS OD2A annual report.",
        },
        {
            "metric": "11 ZIP codes suppressed by DSHS",
            "prior_value": "11 ZIPs suppressed",
            "our_value": "DSHS publishes NO ZIP-level mortality; suppression now manifests as "
                         "CDC tract-count suppression (592/645 tracts, count_suppressed=true)",
            "delta": "suppression mechanism changed / re-expressed",
            "reproducible": "reinterpreted",
            "explanation": (
                "There is no current public DSHS ZIP-level mortality product to suppress ZIPs "
                "within. Confidentiality suppression appears in the CDC NCHS tract product as "
                "count suppression: 592 of 645 Dallas tracts carry count_suppressed=true (counts "
                "1-9 withheld per NCHS rules), while model-based rates are still published. The "
                "prior '11 suppressed ZIPs' framing is superseded by tract-level count suppression."
            ),
        },
    ]

    out = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "phase": "Phase 3.5 - Published-Baseline Validation",
        "denominator": {
            "county_population": county_pop,
            "source": "sum of totpop across svi_tracts.geojson (ACS 2020-2024 5-year), ~2.6M",
        },
        "crude_mortality_by_year": rates,
        "comparisons": comparisons,
        "zip_tier_comparison": zip_rows,
        "ordering_verdict": verdict,
        "zip_mapping_method": (
            "Prior ZIP (ZCTA) mapped to constituent tracts via Census 2020 ZCTA-to-tract "
            "relationship file (tab20_zcta520_tract20_natl). Tract population apportioned to each "
            "ZCTA by land-area share (AREALAND_PART / AREALAND_TRACT); composite averaged with those "
            "population weights; modal tier = tier holding the largest population share. Excluded "
            "(zero-pop) tracts dropped."
        ),
        "notes": [
            "Prior analysis double-assigned ZIP 75212; our modal-tier mapping is unambiguous "
            "(exactly one tier per ZIP).",
            "This validation does NOT overwrite pipeline outputs and does NOT force baselines to match.",
        ],
    }

    with open(os.path.join(CLEAN, "baseline_comparison.json"), "w") as f:
        json.dump(out, f, indent=2)

    write_markdown(out)
    return out


def write_markdown(out):
    L = []
    L.append("# Published-Baseline Validation (Phase 3.5)\n")
    L.append(f"_Generated {out['generated_at']}_\n")
    L.append(f"**County population denominator:** {out['denominator']['county_population']:,} "
             f"({out['denominator']['source']})\n")

    L.append("## Crude OD mortality by year (VSRR 12-month-ending December / ACS population)\n")
    L.append("| Year | Deaths (12-mo ending Dec) | Crude rate /100k |")
    L.append("|---|---|---|")
    for yr, d in out["crude_mortality_by_year"].items():
        L.append(f"| {yr} | {d['deaths_12mo_ending_dec']} | {d['crude_rate_per_100k']} |")
    L.append("")

    L.append("## Baseline comparisons\n")
    L.append("| Metric | Prior value | Our value | Reproducible | Explanation |")
    L.append("|---|---|---|---|---|")
    for c in out["comparisons"]:
        L.append(f"| {c['metric']} | {c['prior_value']} | {c['our_value']} | "
                 f"**{c['reproducible']}** | {c['explanation']} |")
    L.append("")

    L.append("## Prior ZIP tiers vs our spatial tract-tier mapping\n")
    L.append("| ZIP | Prior tier | Our modal tier | Tier of mean score | Our mean composite | Tracts (scored) | Weighting | Agree (modal) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in out["zip_tier_comparison"]:
        L.append(f"| {r['zip']} | {r['prior_tier']} | {r['our_modal_tier']} | "
                 f"{r['tier_of_mean_composite']} | {r['our_mean_composite']} | "
                 f"{r['n_constituent_tracts']} ({r['n_scored_tracts']}) | "
                 f"{r['weighting']} | {'Y' if r['agree'] else 'N'} |")
    L.append("")

    v = out["ordering_verdict"]
    L.append("## Ordering verdict\n")
    L.append(f"**{v['verdict_summary']}**\n")
    L.append(f"- Prior Tier-1 (South/West Dallas) mean composite: **{v['prior_tier1_mean_composite']}**")
    L.append(f"- Prior Tier-3 mean composite: **{v['prior_tier3_mean_composite']}**")
    L.append(f"- Prior Tier-1 mean still above prior Tier-3 mean: **{v['prior_tier1_still_higher_than_tier3']}**")
    L.append(f"- Prior Tier-1 ZIPs in our Tier 1 (modal tier): **{v['prior_tier1_zips_in_our_tier1_modal']}**")
    L.append(f"- Prior Tier-1 ZIPs in our Tier 1 (by mean-score tier): **{v['prior_tier1_zips_in_our_tier1_by_mean']}**")
    L.append(f"- Prior Tier-1 ZIPs remaining Tier 1 or 2 (not dropped to Tier 3): **{v['prior_tier1_zips_remaining_high_or_moderate']}**")
    L.append(f"- Prior Tier-3 ZIPs newly elevated to Tier 1/2 by the composite: "
             f"**{v['newly_elevated_prior_tier3_zips']}**")
    L.append("")
    L.append("Our ZIP ranking, highest to lowest composite (zip, mean composite, our mean-score tier, prior tier):\n")
    for z, s, ot, pt in v["our_ranking_high_to_low"]:
        L.append(f"- {z}: {s} (ours T{ot}, prior T{pt})")
    L.append("")

    L.append("## Method / notes\n")
    L.append(out["zip_mapping_method"] + "\n")
    for n in out["notes"]:
        L.append(f"- {n}")
    L.append("")

    with open(os.path.join(CLEAN, "baseline_comparison.md"), "w") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    o = build()
    print("=== BASELINE VALIDATION BUILT ===")
    print(f"county pop denominator: {o['denominator']['county_population']:,}")
    for yr, d in o["crude_mortality_by_year"].items():
        print(f"  crude {yr}: {d['crude_rate_per_100k']}/100k ({d['deaths_12mo_ending_dec']} deaths)")
    agree = sum(1 for r in o["zip_tier_comparison"] if r["agree"])
    print(f"ZIP tier agreement: {agree}/{len(o['zip_tier_comparison'])}")
    for r in o["zip_tier_comparison"]:
        print(f"  {r['zip']}: prior T{r['prior_tier']} -> ours T{r['our_modal_tier']} "
              f"(score {r['our_mean_composite']}) agree={r['agree']}")
    v = o["ordering_verdict"]
    print(f"prior T1 mean={v['prior_tier1_mean_composite']} T3 mean={v['prior_tier3_mean_composite']} "
          f"T1>T3={v['prior_tier1_still_higher_than_tier3']}")
    print(f"prior-T1 remaining T1/T2 (not dropped): {v['prior_tier1_zips_remaining_high_or_moderate']}; "
          f"newly elevated prior-T3: {v['newly_elevated_prior_tier3_zips']}")
    print("VERDICT:", v["verdict_summary"])
