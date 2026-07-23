# Dallas County Overdose Pipeline — Insights Report (Phase 4C)

_Compiled 2026-07-23. Every figure carries source + vintage. Where a number cannot be produced from open data it is marked **NOT COMPUTABLE** rather than estimated._

## Citation key

| Tag | Source | Vintage / as-of |
|---|---|---|
| **VSRR** | CDC VSRR provisional overdose deaths, Socrata `gb4e-yj24`, Dallas County 12-mo-ending series | data_as_of 2026-07-05; retrieved 2026-07-23 |
| **NCHS-tract** | CDC NCHS model-based drug-OD death rates by tract, Socrata `4day-mt2f` | period 2025-01/2025-12; data_as_of 2026-07-22 |
| **OD2A-2024** | DCHHS *Overdose Data to Action: 2024 Annual Surveillance Report* (Dallas County) | mortality = TX DSHS Vital Statistics as of 2025-08-14; rates = CDC WONDER as of 2025-04-02; ED = ESSENCE as of 2025-08-14 |
| **ACS** | Census ACS 2020-2024 5-year (released Jan 2026), keyed API | retrieved 2026-07-23 |
| **SVI-2022** | CDC/ATSDR Social Vulnerability Index 2022 (ACS 2018-2022 vintage) | pulled 2026-07-23 |
| **pipeline** | this repo's `data/clean/` outputs | generated 2026-07-23 |

Two data universes must not be conflated: **VSRR** (CDC provisional, 12-month-ending, county, counts revise upward) and **OD2A-2024** (TX DSHS Vital Statistics calendar-year counts + CDC WONDER rates). They disagree by construction — e.g. CY2024 deaths = 558 (OD2A-2024) vs 12-mo-ending-Dec-2024 = 566 (VSRR). Neither is "wrong"; they are different products.

---

## 1. Headline mortality

**VSRR 12-month-ending series, 72 monthly points 2020-01 → 2025-12 (VSRR):**

- Latest value: **582** deaths, 12-mo ending 2025-12-31 (VSRR).
- Series range: **352** (12-mo ending 2020-01-31) to **668** (12-mo ending 2024-02-29) (VSRR).
- Trough-to-recent within series: 2020-01 **352** → peak 2024-02 **668** = **+89.8%**; peak **668** → latest **582** = **−12.9%** (VSRR).
- Year-over-year, 12-mo-ending-Dec: 2024 **566** → 2025 **582** = **+2.8%** (VSRR).

**Crude annual rates (VSRR 12-mo-ending-Dec deaths ÷ ACS 2020-2024 county pop 2,604,053) (pipeline `baseline_comparison.json`):**

| Year | Deaths (12-mo end Dec) | Crude rate /100k |
|---|---|---|
| 2020 | 452 | 17.36 |
| 2021 | 564 | 21.66 |
| 2022 | 573 | 22.00 |
| 2023 | 654 | 25.11 |
| 2024 | 566 | 21.74 |
| 2025 | 582 | 22.35 |

- Computable peak-year change: **2020 → 2023 = 452 → 654 = +44.7%** on counts (17.36 → 25.11/100k) (pipeline `baseline_comparison.json`). The pre-pandemic 2018/2019 baselines that published "+61% (2018→2024)" and "+81% (2019→2023)" figures rely on are **NOT COMPUTABLE** from VSRR — the series starts 2020-01 (pipeline `baseline_comparison.json`, `reproducible: partial`).

**DCHHS annual counts (OD2A-2024, DSHS Vital Statistics, Fig 1a):** 2016 **315**, 2017 **324**, 2018 **332**, 2019 **340**, 2020 **439**, 2021 **543**, 2022 **549**, 2023 **628**, 2024 **558** — a **+77.1%** rise 2016→2024 and a **−11.1%** drop 2023→2024 (628→558) (OD2A-2024). DCHHS crude mortality rate rose **12.9 → 20.8/100k, 2018→2024 (+61%)**, vs Texas +49.5% and US +9.9% over the same window (OD2A-2024, CDC WONDER, Fig 1b).

**Provisional-lag caveat.** VSRR points are provisional 12-month-ending totals; toxicology and death-certificate reporting materially depress the most recent ~6 months, and counts revise **upward** (pipeline `mortality_meta.json`, `lag_note`). Record-completeness in `deaths_dallas.csv` confirms the lag structure: `pct_records_complete` is 98.9–100% for months through mid-2025 but the newest points (2025-08 → 2025-12) sit at 99.4–98.9% and still climbing, so the 535–585 band across H2-2025 should be read as a floor, not a settled level (pipeline `deaths_dallas.csv`, VSRR). DCHHS likewise labels its 2024 figure "provisional" pending certification (OD2A-2024, Exec Summary).

---

## 2. Fentanyl / polysubstance over time

**The pipeline cannot independently verify any drug-specific figure.** The VSRR county file (`gb4e-yj24`) carries a single all-drug provisional count with no substance breakdown; the NCHS tract product is all-drug (`intent: Drug_OD`) (pipeline `baseline_comparison.json`, `reproducible: no`; `mortality_meta.json`). All figures below are **OD2A-2024** (DSHS Vital Statistics, 2016–2024, as of 2025-08-14), cited as report values:

| Substance/class, 2016→2024 change | Value | Source |
|---|---|---|
| Fentanyl deaths | **+1,550%** | OD2A-2024, Fig 3 |
| Methamphetamine deaths | **+319%** | OD2A-2024, Fig 3 |
| Cocaine deaths | **+148%** | OD2A-2024, Fig 3 |
| Heroin deaths | **−69%** | OD2A-2024, Fig 3 |
| Treatment (Rx) opioid deaths | **−31%** | OD2A-2024, Fig 3 |
| Opioid-only deaths (no other drug) | **+17%** | OD2A-2024, Fig 2 |
| Opioid + stimulant combination | **+260%** | OD2A-2024, Fig 2 |
| Stimulant-only deaths | **+167%** | OD2A-2024, Fig 2 |
| Accidental-manner deaths | **≈+90%** | OD2A-2024, Fig 1c |
| Suicide-manner deaths | **+4%** | OD2A-2024, Fig 1c |

DCHHS narrative: "Fentanyl continues to be the primary driver of mortality, though stimulants also account for a substantial share, underscoring the impact of polysubstance use" (OD2A-2024, Exec Summary). The prior "fentanyl share of opioid deaths 11.4% (2018) → 79.8% (2023)" claim traces to DCHHS but is **NOT COMPUTABLE** from pipeline data (pipeline `baseline_comparison.json`). Drug categories in OD2A-2024 are non-mutually-exclusive — one decedent may appear in multiple substance groups (OD2A-2024, Fig 3 note).

---

## 3. Walk coverage (adapted metric)

**Chicago's original metric — % of overdose DEATH POINTS within 500 m of a naloxone supply location — is NOT COMPUTABLE for Dallas.** No address- or point-level mortality exists; Dallas publishes only tract-level model-based rates (pipeline `walk_coverage.json`, `method_note`; `mortality_meta.json`). The pipeline substitutes three tract-centroid proxies (500 m radius, EPSG:32138, 38 fixed supply points) (pipeline `walk_coverage.json`):

| Adapted metric | Value | Definition |
|---|---|---|
| (a) % tracts w/ geometric centroid within 500 m of supply | **1.71%** | 11 of 645 tracts; centroid proxy, silent on intra-tract distribution |
| (b) % rate-weighted within 500 m | **2.43%** | Σ od_rate over covered tracts (291.0) ÷ county Σ (11,973.4); up-weights high-rate tracts, **not** death- or pop-weighted |
| (c) % county population within 500 m | **1.42%** | 37,003 of 2,604,053 residents (pop assigned to centroid) |

**Method note / plain statement:** none of the three is "% of deaths near supply." (a) says nothing about where residents or deaths sit inside a tract; (b) is a rate-weighted areal proxy, not a death count; (c) collapses each tract's population onto its centroid (pipeline `walk_coverage.json`, `method_note`). The honest headline is that **~1–2.5%** of Dallas County — by any of the three framings — sits within a 500 m walk of naloxone supply, and the true death-weighted figure cannot be produced without point-level mortality.

---

## 4. % of deaths in decedent's home ZIP — NOT COMPUTABLE

This requires medical-examiner **case-level** records (decedent residence ZIP vs incident ZIP), which are not an open product. DSHS/CDC publish overdose mortality only at **county** (VSRR) and **model-based tract** (NCHS) resolution; ZIP-level mortality "does not exist as a public product for Dallas County" (pipeline `deaths_by_zip.README.md`; `deaths_by_zip.csv` is header-only by design). OD2A-2024 reports *residence-ZIP* death **rankings** (75217, 75216, 75215 highest, 2016–2024) but publishes no home-vs-incident concordance rate (OD2A-2024, Fig 9 / Conclusion).

**What a SWIFS (Southwestern Institute of Forensic Sciences / Dallas County ME) records request would unlock:** case-level decedent residence ZIP, incident location, exact toxicology (multi-drug combinations), and geocodable death points — which would make computable (i) the true Chicago-style % of death points within 500 m of supply (§3), (ii) the home-ZIP vs incident-ZIP split, and (iii) un-suppressed sub-tract mortality currently hidden by NCHS count suppression (§10). This is request-only, not open data.

---

## 5. Top gap areas ranked (composite Tier-1 band, by ZIP)

Ranked by population-weighted mean composite score, ZCTA → constituent-tract mapping via Census 2020 ZCTA–tract relationship file (`tab20_zcta520_tract20_natl`, land-area-part apportionment), scores from `composite_index.geojson` (pipeline; equal-weight index, 5 layers × 0.2, Jenks 3-tier: 170 Tier-1 / 303 Tier-2 / 170 Tier-3, of 643 ranked + 2 excluded zero-pop). Driver-share = each layer's weighted contribution to the ZIP's mean composite (normalized within the ZIP). Higher DART-transit share = **fewer** transit stops (layer is inverted). Raw values shown for context.

| Rank | ZIP | Area (informal) | Mean composite | Tier-1 tracts | Dominant drivers (share of composite) | Raw: OD rate /100k · pov% · unins% · mi-to-supply |
|---|---|---|---|---|---|---|
| 1 | 75217 | Pleasant Grove / SE Dallas | 0.471 | 16 of 22 | transit-poverty **39%** · uninsured **20%** · poverty 16% | 23.1 · 23 · 33 · 2.3 |
| 2 | 75210 | South Dallas (Fair Park E) | 0.467 | 3 of 4 | **mortality 39%** · transit 30% · uninsured 15% | 59.1 · 18 · 24 · 1.1 |
| 3 | 75215 | South Dallas / Cedars | 0.463 | 4 of 6 | transit 36% · mortality 26% · uninsured 19% | 39.0 · 24 · 31 · 0.6 |
| 4 | 75216 | Oak Cliff (S) | 0.419 | 8 of 19 | transit 34% · **poverty 23%** · mortality 18% | 24.9 · 30 · 28 · 1.1 |
| 5 | 75203 | Oak Cliff (N) | 0.436 | 2 of 10 | uninsured 22% · poverty 20% · mortality 19% | 26.2 · 27 · 34 · 1.9 |
| 6 | 75227 | Buckner Terrace / SE | 0.410 | 4 of 13 | transit **40%** · uninsured 22% · mortality 15% | 20.5 · 16 · 31 · 2.2 |
| 7 | 75224 | Oak Cliff (SW) | 0.408 | 4 of 11 | transit **40%** · mortality 19% · poverty 16% | 25.5 · 20 · 24 · 1.8 |
| 8 | 75211 | Oak Cliff (W) | 0.404 | 6 of 19 | transit 39% · uninsured 22% · mortality 16% | 21.4 · 18 · 31 · 1.9 |
| 9 | 75212 | West Dallas | 0.392 | 2 of 7 | transit **41%** · uninsured 20% · poverty 16% | 17.4 · 20 · 27 · 2.0 |
| 10 | 75228 | E Dallas / Casa View | 0.391 | 2 of 22 | transit **41%** · uninsured 20% · mortality 17% | 21.8 · 18 · 28 · 1.4 |

**Structural finding (Tier-1 aggregate, 170 tracts, pipeline `composite_index.geojson`):** across all Tier-1 tracts the mean layer decomposition is **transit-inaccessibility 38.2%**, uninsured 17.2%, mortality 15.2%, distance-to-supply 15.2%, poverty 14.2% of the composite (mean normalized values: dart 0.929, uninsured 0.419, OD-rate 0.371, distance 0.370, poverty 0.346). **The composite's Tier-1 designation is driven first by low transit access and health-coverage gaps, not primarily by raw modeled mortality** — a consequence of the equal-weight design and the inverted, heavily-clustered transit layer (raw Tier-1 means: OD 24.0/100k, poverty 21.4%, uninsured 29.1%, 3.83 mi to supply, 5.9 DART stops/½-mi). This should be disclosed to any reader who reads "Tier 1" as "highest death rate": the two highest-mortality ZIPs (75210 at 59.1/100k, 75215 at 39.0/100k) rank 2nd–3rd, while transit-starved peripheral SE Dallas (75217) ranks 1st on a lower 23.1/100k raw rate. 149 of 170 Tier-1 tracts are themselves count-suppressed (pipeline `composite_index.geojson`), so their raw rates are modeled, not observed.

**Concordance with prior ZIP tiers (pipeline `baseline_comparison.json`):** the composite adds naloxone-distance and transit layers absent from the prior deprivation-only ZIP analysis. Effect: **6 of 6 prior Tier-1 South/West Dallas ZIPs stay in the Tier 1–2 band** (none drop to Tier 3), but prior Tier-3 **75217 is promoted to Tier 1** (0.471) and prior Tier-3 **75227 to Tier 2** (0.410), while affluent downtown/uptown **75201 (0.233) and 75204 (0.257) fall to Tier 3**. DCHHS-independent confirmation: OD2A-2024 names 75217, 75216, 75215 as the top residence-ZIP death counts 2016–2024 (OD2A-2024, Fig 9) — 75217, 75215, 75216 are ranks 1, 3, 4 here.

---

## 6. Demographics (OD2A-2024 only)

All figures **OD2A-2024** (DSHS Vital Statistics, 2016–2024, as of 2025-08-14); the pipeline holds no decedent demographics. Disparities named exactly as DCHHS reports them:

- **Overall burden concentrated in:** males, the White population, and adults **aged 35–64** ("highest numbers of overdose deaths occurred among males, the white population, and individuals aged 35–64 years") (OD2A-2024, Fig 4–6, Demographic Trends).
- **Fentanyl:** 2016–2024 highest among **White individuals, males, aged 25–34**; but in **2024, Hispanic males had the highest number of fentanyl deaths across all combined sex/race categories**, with Hispanic individuals aged 25–34 highest overall, followed by White 35–44 (OD2A-2024, Fig 11a–c).
- **Cocaine:** highest among **Black males, particularly aged 55–64**; in 2024 Black individuals 55–64 had the highest count, then Black 65+. Hispanic-female cocaine counts suppressed (<10) both years (OD2A-2024, Fig 12a–c).
- **Methamphetamine:** highest among **White individuals, males, aged 55–64**; deaths declined overall 2023→2024 but **increased among Hispanic males** (OD2A-2024, Fig 13a–c).
- **Education:** deaths most prevalent among **high-school-graduate/GED (43.1%)**, then 9th–12th-grade-no-diploma (17.8%) and some-college-no-degree (15.7%) (OD2A-2024, Fig 7).
- **Marital status:** highest among never-married, then divorced (OD2A-2024, Fig 8).
- **Manner:** accidental deaths dominate and rose ≈90% (2016–2024); suicide-manner up 4% (OD2A-2024, Fig 1c).
- **Non-fatal ED disparity (2023→2024):** opioid ED visits **+2.8% Hispanic**, **−10.5% non-Hispanic Black**, **−3% non-Hispanic White**; males had ~**2.1×** the opioid ED visits of females; ages 18–44 = **69.2%** of 2024 opioid ED visits (OD2A-2024, Fig 17, Exec Summary).

---

## 7. Spanish-language access

**Demand side (ACS 2020-2024 5-year, table B03003, retrieved 2026-07-23):** Dallas County is **41.3% Hispanic/Latino** (1,081,496 of 2,621,179) (ACS). DCHHS independently reports "40% of Dallas County households speak a language other than English, ~1 in 5 of those with limited-English proficiency — the highest percentage in the region" (OD2A-2024, Introduction). *(Method note: I used ACS B03003 directly at county and ZCTA level via the keyed API rather than the SVI minority theme, because B03003 gives an ethnicity count, whereas SVI Theme 3 encodes a composite minority-status percentile, not a Hispanic share.)*

**Intersection — Tier-1 gap ZIPs by Hispanic share (ACS B03003, ZCTA level; composite from §5):**

| ZIP | Mean composite | % Hispanic | Rank in §5 |
|---|---|---|---|
| 75211 | 0.404 | **78.7%** | 8 |
| 75212 | 0.392 | **67.9%** | 9 |
| 75203 | 0.436 | **65.8%** | 5 |
| 75227 | 0.410 | **64.4%** | 6 |
| 75217 | 0.471 | **71.5%** | 1 |
| 75224 | 0.408 | 59.1% | 7 |
| 75228 | 0.391 | 54.8% | 10 |
| 75210 | 0.467 | 51.7% | 2 |
| 75216 | 0.419 | 47.2% | 4 |
| 75215 | 0.463 | 34.3% | 3 |

Seven of the ten top gap ZIPs are majority-Hispanic; **75211 (78.7%), 75217 (71.5%), 75212 (67.9%)** combine highest-vulnerability tiers with the county's densest Hispanic populations — precisely where OD2A-2024's 2024 fentanyl finding (Hispanic males now the leading fentanyl-death group) lands (OD2A-2024, Fig 11a; §6).

**Supply side — Spanish-language documentation is ABSENT as a finding.** The naloxone inventory carries **no language field of any kind**: property keys are `name, address, city, zip, type, category, access_247, hours_raw, source, verified_on, geocode_method, mobile, mobile_note` — no `language`, `spanish`, or `bilingual` attribute exists in any of the 39 sites (pipeline `naloxone_locations.geojson`). The upstream DSHS/ArcGIS and findtreatment.gov sources the sites were drawn from likewise expose no Spanish-service flag (pipeline `naloxone_locations.geojson`, `source: arcgis|findtreatment`). **Zero Spanish-first supply-side documentation is discoverable in the assembled data** — a coverage gap that cannot be measured because the field does not exist upstream, not because it was dropped.

---

## 8. Invisible-risk areas

**Nightlife corridors WITH venue dispensers (pipeline `naloxone_locations.geojson`, 7 nightlife-category + adjacent storefront sites):**

- **Deep Ellum (75226):** Club Dada, Reno's Bar (nightlife); Deep Ellum Community Center (harm-reduction freestanding dispenser, M–F 12p–7p); Puff n Stuff (storefront dispenser) — 4 sites in-ZIP.
- **Cedar Springs / Oak Lawn strip (75219):** Station 4, JR's Bar and Grill, TMC, Sue Ellen's (nightlife); Pandacorn Café (storefront dispenser) — 5 sites, the densest single ZIP (6 total incl. Phoenix House recovery).
- Both are **evening-hours-only**: every nightlife site is `access_247: False` (pipeline `naloxone_locations.geojson`). Deep Ellum CC closes 7p — before peak nightlife risk.

**Corridors with NO nearby fixed supply (verified absence, pipeline `naloxone_locations.geojson` ZIP distribution):**

- **Lower Greenville (75206): 0 sites** — ZIP absent entirely from the inventory.
- **Uptown (75204): 1 site**, and it is Dallas 24 Hour Club (a recovery-support facility, M–F 8a–10p), **not** a nightlife dispenser.
- **Design District (75207): 1 site.**
- These high-traffic entertainment/bar districts have effectively no walk-up naloxone despite dense late-night alcohol/drug exposure — an invisible-risk gap the tract-mortality layer cannot flag because it is driven by transient (non-resident) crowds, not resident deaths.

**Only ONE fixed 24/7 access point exists county-wide.** Of 39 sites, 2 are `access_247: True`, but one is the **DFW Harm Reduction Access Movement mobile unit** (no fixed coordinates, excluded from distance analysis) — leaving a **single fixed 24/7 point: a Shell station in 75210** (pipeline `naloxone_locations.geojson`; `coverage_gap.json` `n_supply_247: 1`). Mean tract distance to that lone 24/7 point is **9.85 mi** (median 10.07, max 20.7) (pipeline `coverage_gap.json`).

**Suppressed-count tracts as undercounted-risk zones.** **592 of 645 tracts (91.8%)** carry `count_suppressed: true` — NCHS withholds counts of 1–9 for confidentiality, publishing only modeled rates (pipeline `mortality_meta.json`, `tract_overdose.geojson`). These are not zero-death tracts; they are low-numerator tracts whose true burden is statistically invisible. 149 of 170 Tier-1 tracts are among them (§5), so the highest-priority placement zones rest on modeled — not observed — mortality.

---

## 9. Comparison vs published baselines

Reproduced from pipeline `baseline_comparison.md` (Phase 3.5; denominator ACS 2020-2024 pop 2,604,053):

| Prior/published metric | Prior value | Pipeline value | Reproducible | Note |
|---|---|---|---|---|
| Crude OD rate 2018→2024 (+61%) | 12.9 → 20.8 /100k | 2024 = **21.74** /100k; 2018 **N/A** | **partial** | VSRR starts 2020-01; 2018 baseline uncomputable. 21.74 vs prior 20.8 differ on denominator vintage + provisional revision (VSRR) |
| Death rate +81%, 2019→2023 | +81% | 2020→2023 = **+44.7%** (452→654) | **partial** | 2019 predates VSRR; 2020 already above 2019 baseline |
| Fentanyl share of opioid deaths, 11.4%(2018)→79.8%(2023) | 11.4% → 79.8% | **not reproducible** | **no** | VSRR has no drug breakdown → OD2A-2024 |
| Fentanyl deaths +1,550%, 2016→2024 | +1,550% | **not reproducible** | **no** | No drug-specific/pre-2020 data → OD2A-2024 |
| Opioid-stimulant combo +260%, 2018→2024 | +260% | **not reproducible** | **no** | No combination breakdown → OD2A-2024 |
| Hospital OD encounters 2,419(2018)→3,818(2023) | 2,419 → 3,818 | **not reproducible** | **no** | Non-fatal county-level, outside pipeline → OD2A-2024 (ESSENCE reports 2,419→3,863→3,663 for 2018/2023/2024) |
| "11 ZIP codes suppressed by DSHS" | 11 ZIPs | **reinterpreted**: no public DSHS ZIP mortality exists; suppression now appears as **592/645 tract count-suppressions** | **reinterpreted** | Confidentiality suppression migrated from ZIP to NCHS tract product |

All values above: pipeline `baseline_comparison.json`; cross-source figures OD2A-2024 (§1, §2). Crude-rate table = §1.

---

## 10. Data limitations

- **Count suppression:** 592 of 645 tracts (91.8%) have NCHS-suppressed counts (1–9 withheld); only modeled rates are published (pipeline `mortality_meta.json`, `tract_overdose.geojson`). **No public DSHS ZIP-level mortality product exists** — `deaths_by_zip.csv` is header-only by design (pipeline `deaths_by_zip.README.md`).
- **Mortality lag:** VSRR is provisional 12-month-ending; the most recent **~6 months** (≈ 7-month reporting lag) revise upward; H2-2025 counts (535–585) are floors (pipeline `mortality_meta.json`, `deaths_dallas.csv` completeness 98.9–100%).
- **Modeled tract rates:** the NCHS product is spatially smoothed — 150 Texas aggregation units are expanded to 645 Dallas tracts, so one modeled rate is applied to every constituent tract of an aggregation unit (pipeline `mortality_meta.json`, `dallas_agg_units: 150 → cdc_dallas_tracts: 645`). Rates are model-based, not observed death rates.
- **Code-violations frozen 2018:** the Dallas code-violations archive (`x9pz-kdq9`) stops **2018-07-31** (rowsUpdatedAt 2019-02-06); it contains **zero** records in the specified 24-month window. Current blight signal therefore relies solely on 311 requests (`d7e7-envw`, live through 2026-07-22, 8,838 blight records matched to tract) (pipeline `builtenv_index.json`, caveats). The 355,549 lifetime violations are labeled `violations_alltime_2013_2018`, not current.
- **Vacancy proxy is heuristic:** Dallas has no vacant-structure registry; vacancy is a 2013–2018 code-violation heuristic (High-Weeds + Illegal-Dumping/Junk-Vehicle co-occurrence), 9,444 addresses flagged — stale by construction (pipeline `builtenv_index.json`, `vacancy_proxy.caveat`).
- **Geocoding:** 0% geocode failure for TABC off-premise candidates (2,664/2,664) and for naloxone sites (38/38 fixed via source coords; 1 mobile not-applicable) (pipeline `placement_candidates_meta.json`, `naloxone_locations.geojson`).
- **Substitutions made necessary by source gaps:** (i) business-license inventory substituted by TX **Comptroller/CO** land-use + TABC off-premise + OSM + libraries (5,538 candidates: 2,578 liquor, 1,164 convenience, 1,155 barbershop/beauty, etc.) (pipeline `placement_candidates_meta.json`); (ii) blight = **311-only** (code-violations frozen); (iii) walk coverage = **tract-rate proxy** (no death points, §3); (iv) ZIP mortality = tract-to-ZIP spatial approximation, never an independent ZIP measurement.
- **County-only layers not spatially allocated:** non-fatal ED (ESSENCE) and homelessness (2026 PIT = 3,513 individuals, 82% Dallas / 18% Collin) are county/CoC-level and are **not** distributed to tracts (pipeline `context_meta.json`, `pit.note`; OD2A-2024).
- **Coverage geometry:** distances use each tract's **geometric** centroid (population-weighted centroid unavailable), so intra-tract population distribution is ignored (pipeline `coverage_gap.json`, `walk_coverage.json`, `method_note`).
- **Distance-weight sensitivity FLAG:** doubling the naloxone-distance weight re-runs the full pipeline and flips **21.46% of tracts** (138 of 643; 78.54% identical-tier agreement; 78 Tier1↔Tier2 flips, 0 Tier1↔Tier3) (pipeline `composite_methods.json`, `sensitivity.distance_doubled`). By contrast doubling the mortality weight flips only 13.06% (86.94% agreement). The Tier-1 set is **materially sensitive to the distance-weighting choice** — the equal-weight ranking in §5 is one defensible configuration, not a robust one. (SVI context: Dallas County overall SVI = **0.8925**, rank 71 of 254 TX counties, most-vulnerable-first — SVI-2022.)
- **DSHS TODA (external source #2):** the DSHS Texas Overdose Data Action monthly "Suspected Drug Poisoning ED Visits" reports are **state-level only** — no county/Dallas breakdown is published on the resources page (retrieved 2026-07-23). Dallas ED-visit figures therefore come exclusively from OD2A-2024's ESSENCE analysis (§6), not TODA.

---

_End of report. Figures are traceable to the named source + vintage in the citation key. Any claim not reproducible from open pipeline data is marked NOT COMPUTABLE and attributed to its external report (OD2A-2024) rather than estimated._
