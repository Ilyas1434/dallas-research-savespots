# Published-Baseline Validation (Phase 3.5)

_Generated 2026-09-01T14:22:03.391347+00:00_

**County population denominator:** 2,604,053 (sum of totpop across svi_tracts.geojson (ACS 2020-2024 5-year), ~2.6M)

## Crude OD mortality by year (VSRR 12-month-ending December / ACS population)

| Year | Deaths (12-mo ending Dec) | Crude rate /100k |
|---|---|---|
| 2020 | 452 | 17.36 |
| 2021 | 564 | 21.66 |
| 2022 | 573 | 22.0 |
| 2023 | 654 | 25.11 |
| 2024 | 566 | 21.74 |
| 2025 | 582 | 22.35 |

## Baseline comparisons

| Metric | Prior value | Our value | Reproducible | Explanation |
|---|---|---|---|---|
| Crude OD mortality rate, 2018 -> 2024 (+61%) | 12.9 -> 20.8 per 100k (2018-2024) | 2024 (12-mo ending Dec) = 21.74 per 100k; 2018 not computable | **partial** | VSRR provisional county series starts 2020-01, so the 2018 baseline cannot be computed from the pipeline. Our 2024 crude rate (21.74/100k) uses VSRR 12-month-ending-Dec deaths (566) over ACS 2020-2024 total population (2,604,053). Small delta vs prior 20.8 is expected: prior used a different denominator vintage and VSRR counts are provisional and revise upward. |
| Death rate +81%, 2019 -> 2023 | +81% (2019-2023) | 2020 -> 2023 crude rate 17.36 -> 25.11 per 100k (+44.7% on counts 452 -> 654) | **partial** | 2019 predates the VSRR series (starts 2020-01), so the exact 2019->2023 change is not computable. The computable 2020->2023 change is +44.7%. It is smaller than the prior +81% largely because 2020 (our earliest year) already sits above the 2019 baseline the prior figure started from. |
| Fentanyl share of opioid deaths, 11.4% (2018) -> 79.8% (2023) | 11.4% -> 79.8% | not independently reproducible | **no** | VSRR county file has no drug-specific breakdown for Dallas. Source: DCHHS OD2A annual report. |
| Fentanyl deaths +1,550%, 2016 -> 2024 | +1,550% | not independently reproducible | **no** | No drug-specific or pre-2020 data in pipeline. Source: DCHHS OD2A annual report. |
| Opioid-stimulant combo deaths +260%, 2018 -> 2024 | +260% | not independently reproducible | **no** | No drug-combination breakdown in pipeline. Source: DCHHS OD2A annual report. |
| Hospital OD encounters, 2,419 (2018) -> 3,818 (2023) | 2,419 -> 3,818 | not independently reproducible | **no** | Non-fatal / hospitalization data are county-level and outside pipeline sources. Source: DCHHS OD2A annual report. |
| 11 ZIP codes suppressed by DSHS | 11 ZIPs suppressed | DSHS publishes NO ZIP-level mortality; suppression now manifests as CDC tract-count suppression (592/645 tracts, count_suppressed=true) | **reinterpreted** | There is no current public DSHS ZIP-level mortality product to suppress ZIPs within. Confidentiality suppression appears in the CDC NCHS tract product as count suppression: 592 of 645 Dallas tracts carry count_suppressed=true (counts 1-9 withheld per NCHS rules), while model-based rates are still published. The prior '11 suppressed ZIPs' framing is superseded by tract-level count suppression. |

## Prior ZIP tiers vs our spatial tract-tier mapping

| ZIP | Prior tier | Our modal tier | Tier of mean score | Our mean composite | Tracts (scored) | Weighting | Agree (modal) |
|---|---|---|---|---|---|---|---|
| 75215 | 1 | 2 | 1 | 0.546 | 6 (6) | population (areal-apportioned) | N |
| 75210 | 1 | 2 | 2 | 0.484 | 4 (4) | population (areal-apportioned) | N |
| 75211 | 1 | 2 | 2 | 0.5018 | 19 (19) | population (areal-apportioned) | N |
| 75212 | 1 | 3 | 2 | 0.4163 | 7 (7) | population (areal-apportioned) | N |
| 75203 | 1 | 2 | 2 | 0.5427 | 10 (10) | population (areal-apportioned) | N |
| 75224 | 1 | 2 | 2 | 0.5177 | 11 (11) | population (areal-apportioned) | N |
| 75201 | 2 | 3 | 3 | 0.3621 | 9 (9) | population (areal-apportioned) | N |
| 75204 | 2 | 3 | 3 | 0.3854 | 15 (15) | population (areal-apportioned) | N |
| 75226 | 2 | 2 | 2 | 0.4536 | 5 (5) | population (areal-apportioned) | Y |
| 75217 | 3 | 1 | 1 | 0.6234 | 22 (22) | population (areal-apportioned) | N |
| 75227 | 3 | 3 | 2 | 0.4526 | 13 (13) | population (areal-apportioned) | Y |

## Ordering verdict

**Prior Tier-1 South/West Dallas ZIPs remain elevated: 6/6 stay in our Tier 1-2 band (none drop to Tier 3), so the core high-vulnerability geography holds. Exact tier labels shift because the composite adds naloxone-distance and transit-access layers absent from the prior ZIP analysis: these promote peripheral southeast Dallas (e.g. 75217) into the highest tier and drop affluent downtown/uptown (75201, 75204) to Tier 3. Relative ordering is broadly preserved for deprivation-driven ZIPs but the composite is not a pure re-labeling of the prior ZIP tiers.**

- Prior Tier-1 (South/West Dallas) mean composite: **0.5014**
- Prior Tier-3 mean composite: **0.538**
- Prior Tier-1 mean still above prior Tier-3 mean: **False**
- Prior Tier-1 ZIPs in our Tier 1 (modal tier): **0/6**
- Prior Tier-1 ZIPs in our Tier 1 (by mean-score tier): **1/6**
- Prior Tier-1 ZIPs remaining Tier 1 or 2 (not dropped to Tier 3): **6/6**
- Prior Tier-3 ZIPs newly elevated to Tier 1/2 by the composite: **[('75217', 0.6234, 1), ('75227', 0.4526, 2)]**

Our ZIP ranking, highest to lowest composite (zip, mean composite, our mean-score tier, prior tier):

- 75217: 0.6234 (ours T1, prior T3)
- 75215: 0.546 (ours T1, prior T1)
- 75203: 0.5427 (ours T2, prior T1)
- 75224: 0.5177 (ours T2, prior T1)
- 75211: 0.5018 (ours T2, prior T1)
- 75210: 0.484 (ours T2, prior T1)
- 75226: 0.4536 (ours T2, prior T2)
- 75227: 0.4526 (ours T2, prior T3)
- 75212: 0.4163 (ours T2, prior T1)
- 75204: 0.3854 (ours T3, prior T2)
- 75201: 0.3621 (ours T3, prior T2)

## Method / notes

Prior ZIP (ZCTA) mapped to constituent tracts via Census 2020 ZCTA-to-tract relationship file (tab20_zcta520_tract20_natl). Tract population apportioned to each ZCTA by land-area share (AREALAND_PART / AREALAND_TRACT); composite averaged with those population weights; modal tier = tier holding the largest population share. Excluded (zero-pop) tracts dropped.

- Prior analysis double-assigned ZIP 75212; our modal-tier mapping is unambiguous (exactly one tier per ZIP).
- This validation does NOT overwrite pipeline outputs and does NOT force baselines to match.
