# Dallas County Overdose Intelligence & Naloxone Placement — Comprehensive Final Report

*Compiled 2026-07-24. Repository:* `dallas research savespos`*. This is the consolidated deliverable.*

**Every number below carries an inline source and vintage.** Each figure is tagged by data class:

- **[MEASURED]** — a direct count of people or a computed geometric distance. No modeling, interpolation, or smoothing. (2020 Census block populations, Euclidean distances in EPSG:32138, candidate reach counts, supply-point geometry.)
- **[MODELED]** — CDC NCHS model-based (spatially smoothed) overdose rates, and the composite vulnerability scores/tiers derived from them plus normalized socioeconomic layers.
- **[PUBLISHED]** — a figure transcribed verbatim from a cited government report (primarily the DCHHS *Overdose Data to Action: 2024 Annual Surveillance Report*), with page/figure reference.

A documented gap is a finding. Where a figure cannot be produced from open data it is marked **NOT COMPUTABLE** and attributed to its external source rather than estimated.

**Source shorthand** (full inventory in §2):


| Tag             | Source                                                                                | Vintage / as-of                                                                                                           |
| --------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **VSRR**        | CDC VSRR provisional overdose deaths, Socrata `gb4e-yj24`, Dallas County 12-mo-ending | data_as_of 2026-07-05                                                                                                     |
| **NCHS-tract**  | CDC NCHS model-based drug-OD death rates by tract, Socrata `4day-mt2f`                | period 2025-01/2025-12; data_as_of 2026-07-22                                                                             |
| **OD2A-2024**   | DCHHS *Overdose Data to Action: 2024 Annual Surveillance Report* (20 pp)              | mortality = TX DSHS Vital Statistics as of 2025-08-14; rates = CDC WONDER as of 2025-04-02; ED = ESSENCE as of 2025-08-14 |
| **Census-2020** | Census 2020 PL 94-171 block population (P1_001N) + TIGER 2024 tabblocks               | decennial 2020                                                                                                            |
| **ACS**         | Census ACS 2020-2024 5-year (released Jan 2026)                                       | retrieved 2026-07-23                                                                                                      |
| **SVI-2022**    | CDC/ATSDR Social Vulnerability Index 2022 (ACS 2018-2022 vintage)                     | pulled 2026-07-23                                                                                                         |
| **pipeline**    | this repo's `data/clean/` outputs                                                     | generated 2026-07-23 / -24                                                                                                |


---



## 1. Executive summary

Dallas County's naloxone supply is measurably, not theoretically, out of reach for most of its residents. Against the county's exact 2020 decennial block population of **2,613,539** [MEASURED, Census-2020, `block_access_summary.json`], **2,196,211 residents (84.03%) live more than one straight-line mile from any of the 38 geocoded fixed naloxone access points**, **1,506,855 (57.66%) beyond two miles**, and **394,283 (15.09%) beyond five miles** [all MEASURED, `block_access_summary.json`]. The around-the-clock picture is worse: exactly **one** fixed 24/7 supply point exists countywide with usable coordinates — a **Shell station in ZIP 75210** [MEASURED, `naloxone_locations.geojson`] — and **2,254,279 residents (86.25%) live beyond five miles of it**, a countywide 24/7 desert [MEASURED, `block_access_summary.json`]. **902,611 residents across 9,779 populated blocks are "stranded"** — beyond one mile of supply *and* beyond a half-mile (805 m) of any of the 6,974 DART transit stops — meaning they can neither walk to naloxone nor easily transit to it [MEASURED, `block_access_summary.json`]. Mortality remains high and roughly flat at the top of the pandemic-era plateau: **582 provisional drug-overdose deaths in the 12 months ending December 2025** [PUBLISHED/VSRR, `vsrr.json`], a crude **22.35 per 100,000** [MODELED denominator, `baseline_comparison.json`].

What this means for placement policy: because the mortality signal is legally suppressed at fine geography (see §3) and the access deficit is enormous and *measurable*, the defensible optimization target is **reach among currently-unserved residents**, not modeled death counts. A pure coverage-maximizing greedy selection shows the deficit is tractable at the top: the best **25 non-overlapping everyday storefronts would newly cover 231,806 unserved residents** (10 sites → 117,922; 50 sites → 388,927) [MEASURED, `placement_ranked_meta.json`]. The recommended sites are ordinary, already-trusted retail — liquor stores, corner stores, laundromats, salons — clustered where unserved population density is highest (the Lombardy Ln / Webb Chapel corridor in northwest Dallas, and the Vickery Meadow / Park Ln pocket). This report pairs that measured reach with a modeled 5-layer vulnerability index so that a placement program can weight both raw coverage and structural need, while being explicit that the highest-need designation is driven as much by transit and insurance gaps as by (suppressed, modeled) mortality.

---



## 2. Data landscape — full source inventory

Resolution and access class are stated for every source. "Gated" rows are sources that would materially improve the analysis but are not open; each row's final column states what it would unlock.

### Open sources actually used


| Source                               | Endpoint                                   | Cadence                      | Resolution                     | Vintage in repo                                                       | Access class                 | Script                  |
| ------------------------------------ | ------------------------------------------ | ---------------------------- | ------------------------------ | --------------------------------------------------------------------- | ---------------------------- | ----------------------- |
| CDC VSRR provisional OD deaths       | Socrata `gb4e-yj24`                        | Monthly                      | County, 12-mo-ending           | data_as_of 2026-07-05; 72 rows 2020-01→2025-12                        | **Open**                     | `pull_mortality.py`     |
| CDC NCHS OD death rates by tract     | Socrata `4day-mt2f`                        | Periodic (model refresh)     | Tract (model-based agg units)  | data_as_of 2026-07-22; period 2025-01/2025-12; 645 tracts / 150 units | **Open**                     | `pull_mortality.py`     |
| Census 2020 PL 94-171 blocks         | `census.gov` P1_001N + TIGER2024 tabblocks | Decennial                    | Census block                   | 2020; 38,180 blocks / 25,682 populated                                | **Open**                     | `build_block_access.py` |
| Census TIGER/Line tract geometry     | `www2.census.gov/geo/tiger/TIGER2024`      | Annual                       | Census tract                   | TIGER2024 (2020 tracts); 645 Dallas (FIPS 48113)                      | **Open**                     | multiple                |
| CDC/ATSDR SVI                        | ArcGIS `SVI 2022 USA`                      | Biennial                     | Census tract                   | SVI 2022 (ACS 2018-2022); 645 tracts                                  | **Open**                     | `build_context.py`      |
| Census ACS 5-year subject tables     | `api.census.gov/.../acs5/subject`          | Annual                       | Tract + ZCTA                   | ACS 2020-2024 5-yr (rel. Jan 2026); 645 tracts, 165 ZCTAs             | **Keyed** (`CENSUS_API_KEY`) | `build_context.py`      |
| DART GTFS transit stops              | `dart.org/.../google_transit.zip`          | Periodic                     | Point → 0.5 mi centroid buffer | 6,974 stops; 400/645 tracts w/ ≥1 stop                                | **Open**                     | `build_context.py`      |
| HousingForward NTX PIT count         | PDF, `housingforwardntx.org`               | Annual                       | County/CoC (Dallas+Collin)     | 2026 PIT: 3,513 individuals (82% Dallas / 18% Collin)                 | **Open** (PDF scrape)        | `build_context.py`      |
| DSHS / regional Narcan site feed     | ArcGIS `NarcanSites_10152024`              | Ad hoc                       | Point                          | live pull each run                                                    | **Open**                     | `build_naloxone.py`     |
| Local naloxone snapshot              | `data/raw/NaloxoneSites_April2026.csv`     | Static                       | Point                          | Apr 2026                                                              | Local file                   | `build_naloxone.py`     |
| SAMHSA findtreatment.gov OTP locator | `findtreatment.gov/.../v2`                 | Live                         | Point                          | live pull each run                                                    | **Open**                     | `build_naloxone.py`     |
| Dallas 311 blight requests           | Socrata `d7e7-envw`                        | Live                         | Point → tract                  | last 24 mo (2024-07→2026-07); 8,974 rows, 8,838 matched               | **Open**                     | `build_builtenv.py`     |
| Dallas Code Violations               | Socrata `x9pz-kdq9`                        | **FROZEN** (ends 2018-07-31) | Point → tract                  | 361,407 rows (2013-2018 only), 355,549 matched                        | **Open (stale)**             | `build_builtenv.py`     |
| Dallas Certificates of Occupancy     | Socrata `9qet-qt9e`                        | Ongoing                      | Point                          | 23,731 fetched → 2,348 kept                                           | **Open**                     | `build_candidates.py`   |
| TABC alcohol licenses                | `data.texas.gov` `kguh-7q9z`               | Ongoing                      | Address (geocoded)             | 6,101 rows → 2,664 off-premise kept; 0.0% geocode fail                | **Open**                     | `build_candidates.py`   |
| OpenStreetMap Overpass               | Overpass API area 3601837698               | Live                         | Point                          | 782 elements, 782 kept                                                | **Open**                     | `build_candidates.py`   |
| Dallas Public Library branches       | Socrata `2ksy-mdcf`                        | Static-ish                   | Point                          | 30 branches                                                           | **Open**                     | `build_candidates.py`   |
| Google Geocoding / Places            | `maps.googleapis.com`                      | On demand                    | Point                          | fallback only, 0 records needed it; Places never auto-run             | **Keyed/paid, opt-in**       | `enrich_places.py`      |




### Gated / nonexistent sources — documented gaps (each is a finding)


| Source                                                        | What it is                                   | Access class                                                  | What it would unlock                                                                                                                                                                                                          |
| ------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ODMAP** (Overdose Detection Mapping Application Program)    | Real-time suspected-overdose incident feed   | **Agency-only** (law-enforcement / public-health credentials) | Near-real-time incident hot-spotting; would replace modeled tract rates with observed incident points.                                                                                                                        |
| **SWIFS / Dallas County ME case records**                     | Medical-examiner case-level decedent records | **Request-only** (not an open product)                        | Geocodable death *points* (enables the true Chicago-style "% of death points within 500 m of supply", §5), decedent home-ZIP vs incident-ZIP concordance, exact multi-drug toxicology, and un-suppressed sub-tract mortality. |
| **DFR EMS naloxone-administration data** (Dallas Fire-Rescue) | EMS naloxone deployments / reversals         | **Nonexistent publicly**                                      | Field-reversal density as a demand proxy independent of mortality.                                                                                                                                                            |
| **DSHS ZIP-level mortality**                                  | ZIP-resolution overdose deaths               | **Nonexistent publicly**                                      | Direct ZIP burden ranking; currently only *tract* model-based rates exist, and `deaths_by_zip.csv` is header-only by design (pipeline).                                                                                       |
| **DSHS TODA "Suspected Drug Poisoning ED Visits"**            | Monthly syndromic ED report                  | **State-level only** (no county breakdown)                    | County non-fatal ED trend; Dallas ED figures therefore come only from OD2A-2024's ESSENCE analysis (§4).                                                                                                                      |


---



## 3. The Texas suppression problem — why this project measures access, not tract deaths

This section frames the entire methodology. **Texas mortality confidentiality suppression makes fine-grained observed overdose mortality unavailable**, so the project's quantitative backbone is *measured access*, with modeled mortality used only as one input to a vulnerability index.

- **Tract-count suppression.** The CDC NCHS product (`4day-mt2f`) publishes model-based *rates* for all 645 Dallas tracts, but withholds the underlying death *counts* wherever the numerator is 1-9 (NCHS confidentiality rule). **592 of 645 tracts (91.8%) carry** `count_suppressed=true` [MODELED/NCHS-tract, `mortality_meta.json`, `tract_overdose.geojson`]. These are not zero-death tracts; they are low-numerator tracts whose true burden is statistically invisible.
- **Native aggregation geography.** The CDC does not model at raw tract level. It publishes at **150 aggregation units** for Dallas (unions of ~2-10 tracts, averaging ~4.3 tracts/unit), then the pipeline explodes each unit's single modeled rate onto its constituent tracts — visually smearing variance [MODELED, `mortality_units_meta.json`]. Of the **150 native units, 138 (92%) have suppressed underlying counts** [MODELED/NCHS-tract, `mortality_units_meta.json`]. A 95% confidence interval is published for every unit.
- **No ZIP product to suppress.** The prior/published "11 ZIP codes suppressed by DSHS" framing is **superseded**: there is no current public DSHS ZIP-level mortality product at all. Confidentiality suppression now manifests as tract-count suppression (592/645) [PUBLISHED→reinterpreted, `baseline_comparison.md` row "11 ZIP codes suppressed by DSHS", `reproducible: reinterpreted`].
- **Exact zeros.** Because the live product provides rates for all units rather than suppressing small-numerator rates, `rate_suppressed` is true only for TIGER tracts absent from the CDC product — here **0 of 645** [MODELED, `mortality_meta.json`]. The native-unit rate distribution bottoms at an exact modeled rate of **0.0 per 100k** (§4), and the figure-4 caption records 7 native units published as exact zero [MODELED, `mortality_units_meta.json`, figure4 caption].

**What the project measures instead.** Because deaths cannot be located precisely, the defensible optimization signal is **where residents are, relative to where supply is** — a pure geometric measurement over the 2020 census-block population (§5), combined with a transparent 5-layer vulnerability composite (§6) in which modeled mortality is only one of five equally-weighted inputs. Every headline in §1 and §5 is a MEASURED count of people or distance; every mortality-rate figure is flagged MODELED.

---



## 4. Mortality findings

Two mortality universes must not be conflated. **VSRR** (CDC provisional, 12-month-ending, county, counts revise upward) and **OD2A-2024** (TX DSHS Vital Statistics calendar-year counts + CDC WONDER rates) disagree by construction — e.g. CY2024 deaths = **558** (OD2A-2024) vs 12-mo-ending-Dec-2024 = **566** (VSRR). Neither is wrong; they are different products.

### 4.1 VSRR county series (2020-2025) and crude rates

72 monthly 12-month-ending points, 2020-01→2025-12 [PUBLISHED/VSRR, `vsrr.json`, `mortality_meta.json`]:

- Latest: **582** deaths, 12-mo ending 2025-12-31 [VSRR].
- Series range: **352** (12-mo ending 2020-01-31) to **668** (12-mo ending 2024-02-29) [VSRR].
- Trough→peak→latest: 352 → 668 (**+89.8%**) → 582 (**−12.9%** off peak) [VSRR].
- YoY 12-mo-ending-Dec: 2024 **566** → 2025 **582** = **+2.8%** [VSRR].

Crude annual rates (VSRR 12-mo-ending-Dec deaths ÷ ACS 2020-2024 county pop 2,604,053; denominator is MODELED survey estimate) [`baseline_comparison.json`]:


| Year | Deaths (12-mo end Dec) [VSRR] | Crude rate /100k [MODELED denom] |
| ---- | ----------------------------- | -------------------------------- |
| 2020 | 452                           | 17.36                            |
| 2021 | 564                           | 21.66                            |
| 2022 | 573                           | 22.00                            |
| 2023 | 654                           | 25.11                            |
| 2024 | 566                           | 21.74                            |
| 2025 | 582                           | 22.35                            |


Computable peak change: **2020 → 2023 = 452 → 654 = +44.7%** on counts (17.36 → 25.11/100k) [VSRR + MODELED denom, `baseline_comparison.json`].

### 4.2 CDC native-unit rate distribution (MODELED)

Across the 150 native aggregation units [MODELED/NCHS-tract, `mortality_units_meta.json`]:

- **min 0.0 · Q1 13.75 · median 16.9 · Q3 21.425 · max 64.7 · mean 18.126** per 100,000 (n=150).
- Tract-exploded product spans the same **0.0 to 64.7 per 100k** across 645 tracts; all 645 carry a modeled rate; 592 have suppressed counts [MODELED, `mortality_meta.json`].
- A 95% confidence interval is published for every native unit [MODELED, figure4 caption].



### 4.3 OD2A published facts (PUBLISHED, with page refs)

All from **OD2A-2024** (DCHHS; mortality = TX DSHS Vital Statistics as of 2025-08-14) [`od2a_extract.json`]:

**Annual death counts, Fig 1a (p4):** 2016 **315** · 2017 **324** · 2018 **332** · 2019 **340** · 2020 **439** · 2021 **543** · 2022 **549** · 2023 **628** · 2024 **558** — **+77.1% 2016→2024** (p4), **−11.1% 2023→2024** (628→558). Window total 2016-2024 = **4,028 deaths** (p9); **60.9% (2,455) within the City of Dallas** (p9, Fig 10).

**Crude mortality rate, Fig 1b (p4):** Dallas County **12.9 → 20.8 per 100k, 2018→2024 (+61%)**, vs Texas **+49.5%** and US **+9.9%** over the same window (CDC WONDER as of 2025-04-02).

**Substance-specific change 2016→2024, Figs 2-3 (pp5-6):** fentanyl **+1,550%** (Fig 3, p6) · methamphetamine **+319%** · cocaine **+148%** · heroin **−69%** · treatment/Rx opioids **−31%** · opioid-only **+17%** (Fig 2, p5) · **opioid+stimulant combination +260%** · stimulant-only **+167%** · accidental-manner **≈+90%** · suicide-manner **+4%** (Fig 1c, p5). Drug categories are non-mutually-exclusive.

**Demographics (pp3, 7-13, 19):** highest overall burden among **males, White individuals, adults aged 35-64** (p3; conclusion p19 names ages 25-54). Deaths by sex, Fig 4 (p7): males rose 210→413, females 105→145 (2016→2024). Fentanyl, Figs 11a-c (p10): 2016-2024 highest among **White males aged 25-34**, but **in 2024 Hispanic males had the highest number of fentanyl-related deaths across all combined sex/race categories** (the key 2024 demographic shift). Cocaine highest among **Black males aged 55-64** (p12). Meth highest among **White males aged 55-64** but **increasing among Hispanic males** (p13). Education (Fig 7, p8): **HS grad/GED 43.1%**, 9th-12th no diploma 17.8%, some-college 15.7%. Marital: never-married then divorced (p8).

**Geography (pp9, 18-19):** top residence ZIPs by OD deaths 2016-2024 = **75217, 75216, 75215** (Fig 9, p9; reaffirmed conclusion p19). Top residence ZIPs by opioid ED visits 2024 = **75235, 75243, 75228, 75042, 75217, 75230** (Fig 21, p18).

**Emergency department (pp15-17):** all-drug OD ED visits by year — 2018 **2,419** → 2023 **3,863** → 2024 **3,663** (**+51.4% 2018→2024**, **−5.2% 2023→2024**) (Fig 14, p16). Opioid-related ED visits 2024 = **793** (**−5.7%** vs 841 in 2023) (Fig 15a, p16); ages 18-44 = **69.2%** of 2024 opioid ED visits (p17); male:female ratio **2.1:1** (p17). 2023→2024 opioid ED change by race: **+2.8% Hispanic, −10.5% non-Hispanic Black, −3% non-Hispanic White** (p3).

### 4.4 Provisional-lag caveat

VSRR points are provisional 12-month-ending totals; toxicology and death-certificate reporting materially depress the most recent **~6 months** (≈7-month reporting lag), and counts revise **upward** [`mortality_meta.json`, `lag_note`]. The H2-2025 band (535-585) should be read as a floor, not a settled level. DCHHS likewise labels its 2024 figure provisional pending certification [OD2A-2024, Exec Summary].

---



## 5. Measured access desert



### 5.1 Methodology (100% MEASURED, no modeling)

Every value is a count of people (Census 2020 PL P1_001N block population) or a straight-line (Euclidean) distance in meters, computed in **EPSG:32138** (NAD83 / Texas North Central, unit = metre) [`block_access_summary.json`, method statement]. Block locations are the Census Bureau internal points (INTPTLAT20/INTPTLON20); nearest-neighbor distances via `scipy.cKDTree`. No modeling, estimation, interpolation, or smoothing. A resident is "beyond X mi" if their block's internal point is farther than X miles from the nearest relevant supply site.

Inputs and universe [MEASURED, `block_access_summary.json`]: **38,180 total blocks**, **25,682 populated**; **38 supply points** with coordinates (**1** with `access_247`, **13** OTP/methadone); **6,974 DART stops**. One 24/7 harm-reduction site — **DFW Harm Reduction Access Movement**, a mobile unit — has no fixed coordinates and is **excluded/unmeasurable**. Thresholds: 1 mi = 1,609.344 m; 2 mi = 3,218.688 m; 5 mi = 8,046.72 m; DART = 805 m. County population from 2020 blocks = **2,613,539** (vs ACS 2024 reference 2,604,053; delta +9,486, the 2020 decennial being an exact count and ACS a later estimate).

### 5.2 Results — per-threshold table (MEASURED)


| Metric                               | Residents     | % of county    | Source                      |
| ------------------------------------ | ------------- | -------------- | --------------------------- |
| Beyond 1 mi of any supply            | **2,196,211** | **84.03%**     | `block_access_summary.json` |
| Beyond 2 mi of any supply            | **1,506,855** | **57.66%**     | `block_access_summary.json` |
| Beyond 5 mi of any supply            | **394,283**   | **15.09%**     | `block_access_summary.json` |
| Beyond 5 mi of the 1 fixed 24/7 site | **2,254,279** | **86.25%**     | `block_access_summary.json` |
| **Stranded** (see below)             | **902,611**   | (9,779 blocks) | `block_access_summary.json` |




### 5.3 Stranded definition and count (MEASURED)

**Stranded = pop>0 AND nearest supply >1,609.344 m (1 mi) AND nearest DART stop >805 m (½ mi).** **902,611 residents across 9,779 populated blocks** meet all three conditions [MEASURED, `block_access_summary.json`] — they can neither walk to naloxone nor readily reach it by transit. Example concentrations from the tract rollup (all 100% beyond 1 mi and fully stranded within): tract 48113007601 = 1,894 pop / 1,894 stranded; 48113007102 = 5,441 pop / 4,885 stranded; 48113007812 = 3,212 / 3,072 [MEASURED, `block_access_summary.json` tract_rollup].

### 5.4 The one-24/7-point finding and the unmeasurable-mobile caveat

Exactly **one fixed 24/7 access point** has usable coordinates — a **Shell station in ZIP 75210** [MEASURED, `naloxone_locations.geojson`]. Tract-centroid distance to that lone point averages **9.85 mi** (median 10.07, max 20.7) [MEASURED, `coverage_gap.json`, `dist_nearest_247_m`]. **Caveat (documented in the data):** the 86.25% 24/7 desert is measured against 1 point; the excluded **DFW Harm Reduction Access Movement mobile unit** is also `access_247` but lacks fixed coordinates — "if any 24/7 site among [the unmeasurable] were geocoded this desert could shrink" [`block_access_summary.json`, caveat]. A moving unit cannot be represented as a fixed catchment; this is a true limitation, not an omission.

### 5.5 Walk-coverage adaptation note

Chicago's original metric — % of overdose **death points** within 500 m of supply — is **NOT COMPUTABLE** for Dallas: no address/point-level mortality exists (§3). The pipeline substitutes three tract-centroid proxies (500 m, EPSG:32138, 38 supply points) [MEASURED geometry over MODELED-suppressed mortality, `walk_coverage.json`]: **(a) 1.71%** of tracts have a geometric centroid within 500 m of supply (11/645); **(b) 2.43%** rate-weighted (Σ od_rate over covered tracts 291.0 ÷ county Σ 11,973.4); **(c) 1.42%** of county population within 500 m (37,003 of 2,604,053). None is "% of deaths near supply"; the honest headline is that **~1-2.5%** of Dallas County sits within a 500 m walk of naloxone by any framing, and the true death-weighted figure requires SWIFS point data.

Companion tract-centroid distance summary [MEASURED, `coverage_gap.json`]: to nearest *any* supply — mean **2.75 mi**, median 2.34, max 10.2; **16.6%** of tracts within 1 mi, **43.4%** within 2 mi. To nearest *storefront dispenser* — mean 5.24 mi, median 4.29.

---



## 6. Composite vulnerability index & validation



### 6.1 Five layers, equal weights (MODELED index)

Unit = census tract (TIGER2024, 2020 boundaries), **645 total, 643 ranked, 2 excluded** (zero population) [`composite_methods.json`]. Tract is the finest openly available common unit — the CDC OD product, ACS poverty/uninsured, and SVI are all natively tract-level. Five min-max-normalized layers, **equal weights (0.20 each)**, following CDC SVI convention (disclosed as an analyst choice):


| Layer                               | Source               | Vintage                      | Class            | Range (min→max)  |
| ----------------------------------- | -------------------- | ---------------------------- | ---------------- | ---------------- |
| OD mortality rate /100k             | NCHS `4day-mt2f`     | 2025-01/12; as-of 2026-07-22 | **MODELED**      | 0.0 → 64.7       |
| Poverty %                           | ACS S1701_C03_001E   | ACS 2020-2024                | MODELED (survey) | 0.0 → 61.8       |
| Uninsured %                         | ACS S2701_C05_001E   | ACS 2020-2024                | MODELED (survey) | 0.0 → 69.5       |
| Distance to nearest supply (m)      | `coverage_gap.json`  | 2026-07-23                   | **MEASURED**     | 142.9 → 16,419.9 |
| DART stops within 0.5 mi (inverted) | `svi_tracts.geojson` | DART GTFS 2026-07-23         | MEASURED         | 0 → 84           |


Composite = Σ(wᵢ·normᵢ)/Σ(wᵢ) over non-null layers. Nulls: excluded from per-layer min/max; partial tracts rescaled and flagged (`data_completeness=partial`). Excluded layers (documented, to avoid ecological fabrication): **non-fatal OD ED visits** and **homelessness/PIT** — both county-only, not allocated to tracts [`composite_methods.json`, excluded_layers].

### 6.2 Jenks tiers (MODELED)

Jenks natural breaks, 3 classes, on the composite of ranked tracts. Tier 1 = highest vulnerability. Primary equal-weight cutoffs: **Tier 3|2 = 0.33808, Tier 2|1 = 0.43439** (Tier-1 lower bound) [`composite_methods.json`]. **Tier counts: 170 / 303 / 170** (Tier 1 / 2 / 3) [`composite_methods.json`, figure2 caption].

**Structural finding (Tier-1 aggregate, 170 tracts):** mean layer decomposition is **transit-inaccessibility 38.2%, uninsured 17.2%, mortality 15.2%, distance-to-supply 15.2%, poverty 14.2%** of the composite [MODELED, `insights.md` from `composite_index.geojson`]. The Tier-1 designation is driven first by low transit access and coverage gaps, **not primarily by raw modeled mortality** — and **149 of 170 Tier-1 tracts are themselves count-suppressed**, so their raw rates are modeled, not observed. Any reader interpreting "Tier 1" as "highest death rate" should be corrected.

### 6.3 Sensitivity (MODELED)

Each variant re-runs the full pipeline (renormalized weights, recomputed Jenks tiers), compared tract-by-tract to primary [`composite_methods.json`, sensitivity]:


| Variant                           | % identical tier | n flips (of 643) | Tier1↔Tier2 | Tier1↔Tier3 |
| --------------------------------- | ---------------- | ---------------- | ----------- | ----------- |
| Mortality-doubled (0.333/0.167×4) | **86.94%** (559) | 84               | 39          | **0**       |
| Distance-doubled (0.333/0.167×4)  | **78.54%** (505) | 138              | 78          | **0**       |


**Distance-doubled is FLAGGED (>15% of tracts flip; 21.46%).** Every flip is between *adjacent* tiers; **zero Tier1↔Tier3 flips under either variant** [`composite_methods.json`; figure2 caption]. Interpretation: the Tier-1 set is **materially sensitive to the distance-weighting choice** — the equal-weight ranking is one defensible configuration, not a robust one — but the coarse high/low separation is stable (no high-vulnerability tract ever collapses to lowest, or vice versa). Under distance-doubling Tier counts shift to 130/333/180; under mortality-doubling to 159/281/203.

### 6.4 Phase 3.5 baseline comparison (reproduced in full)

Crude OD mortality by year already in §4.1. Baseline comparison table, reproduced verbatim [PUBLISHED prior vs pipeline, `baseline_comparison.md`]:


| Metric                                                  | Prior value       | Our value                                                                               | Reproducible      | Explanation                                                                                                               |
| ------------------------------------------------------- | ----------------- | --------------------------------------------------------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Crude OD rate 2018→2024 (+61%)                          | 12.9 → 20.8 /100k | 2024 = **21.74** /100k; 2018 not computable                                             | **partial**       | VSRR starts 2020-01; 2018 baseline uncomputable. Small delta vs 20.8 = denominator vintage + provisional upward revision. |
| Death rate +81%, 2019→2023                              | +81%              | 2020→2023 = **+44.7%** (452→654; 17.36→25.11)                                           | **partial**       | 2019 predates VSRR; 2020 already above the 2019 baseline.                                                                 |
| Fentanyl share of opioid deaths 11.4%(2018)→79.8%(2023) | 11.4% → 79.8%     | not reproducible                                                                        | **no**            | VSRR has no drug breakdown. Source: DCHHS OD2A.                                                                           |
| Fentanyl deaths +1,550%, 2016→2024                      | +1,550%           | not reproducible                                                                        | **no**            | No drug-specific/pre-2020 data. Source: OD2A.                                                                             |
| Opioid-stimulant combo +260%, 2018→2024                 | +260%             | not reproducible                                                                        | **no**            | No combination breakdown. Source: OD2A.                                                                                   |
| Hospital OD encounters 2,419(2018)→3,818(2023)          | 2,419 → 3,818     | not reproducible                                                                        | **no**            | Non-fatal, county-level. OD2A/ESSENCE reports 2,419→3,863→3,663 for 2018/2023/2024.                                       |
| 11 ZIP codes suppressed by DSHS                         | 11 ZIPs           | No public DSHS ZIP mortality exists; suppression = **592/645 tract count-suppressions** | **reinterpreted** | Confidentiality migrated from ZIP to NCHS tract product.                                                                  |


**Prior ZIP tiers vs our spatial tract-tier mapping** (ZCTA→tract via Census 2020 `tab20_zcta520_tract20_natl`, land-area apportionment; modal tier = tier holding largest population share) [MODELED, `baseline_comparison.json`]:


| ZIP   | Prior tier | Our modal tier | Tier of mean score | Mean composite | Tracts | Agree (modal) |
| ----- | ---------- | -------------- | ------------------ | -------------- | ------ | ------------- |
| 75215 | 1          | 2              | 1                  | 0.4628         | 6      | N             |
| 75210 | 1          | 1              | 1                  | 0.4674         | 4      | **Y**         |
| 75211 | 1          | 2              | 2                  | 0.4045         | 19     | N             |
| 75212 | 1          | 2              | 2                  | 0.3916         | 7      | N             |
| 75203 | 1          | 2              | 1                  | 0.4360         | 10     | N             |
| 75224 | 1          | 2              | 2                  | 0.4078         | 11     | N             |
| 75201 | 2          | 3              | 3                  | 0.2325         | 9      | N             |
| 75204 | 2          | 3              | 3                  | 0.2573         | 15     | N             |
| 75226 | 2          | 2              | 2                  | 0.3532         | 5      | **Y**         |
| 75217 | 3          | **1**          | 1                  | 0.4713         | 22     | N             |
| 75227 | 3          | 2              | 2                  | 0.4100         | 13     | N             |


**Ordering verdict** [`baseline_comparison.md`]: **the prior Tier-1 geography holds** — 6/6 prior Tier-1 South/West Dallas ZIPs stay in the Tier 1-2 band (none drop to Tier 3), so the core high-vulnerability geography is preserved. Exact labels shift because the composite adds naloxone-distance and transit layers absent from the prior deprivation-only ZIP analysis: **75217 is promoted (prior T3 → our T1, 0.4713, now the single highest-scoring ZIP)**, 75227 promoted to T2, while affluent **75201 (0.2325) and 75204 (0.2573) fall to T3**. **75212 is resolved**: the prior analysis double-assigned it; our modal mapping is unambiguous (one tier per ZIP). DCHHS-independent confirmation: OD2A-2024 names 75217/75216/75215 as top residence-ZIP death counts (Fig 9) — 75217, 75215, 75216 are ranks 1, 3, 4 in our ordering.

---



## 7. Placement recommendations



### 7.1 NEED × REACH method

**REACH (MEASURED)** for each candidate = sum of 2020 block population over blocks that are (a) currently unserved (>1,609.344 m from nearest supply) AND (b) within 805 m (½-mi walk) of the candidate — "unserved residents within a half-mile walk of this storefront," via cKDTree in EPSG:32138 [`placement_ranked_meta.json`]. REACH is right-skewed: mean 2,994.3, median 2,774.5, max 17,437; 773 of 5,428 candidates have reach 0.

**NEED — two variants** [`placement_ranked_meta.json`]: **need_composite** = the 5-layer index (§6), which *includes* modeled mortality; **need_grounded** = equal-weight mean of min-max-normalized `pct_poverty_acs2024`, `pct_uninsured_acs2024`, `svi_overall`, `pct_pop_beyond_1mi` — **contains NO mortality input**. Each is min-max normalized and multiplied by normalized REACH; `reach_raw` is emitted alongside so no information is lost. Ties broken by reach descending. **5,428 candidates ranked** (of 5,538; 110 excluded — 100 null-geoid outside county, 10 zero/null-pop tract).

**Agreement (MEASURED/MODELED):** **Spearman ρ = 0.9659** (p≈0), **top-50 overlap 45/50**, top-100 overlap 91/100 [`placement_ranked_meta.json`]. The two need definitions produce nearly the same priority order — meaning the placement recommendation does **not** hinge on the suppressed, modeled mortality layer, which is the point.

### 7.2 Top-25 storefronts by greedy coverage gain (MEASURED)

Rank-agnostic pure-reach greedy: iteratively pick the site covering the most currently-uncovered unserved residents within 805 m, remove those blocks, repeat. De-overlapped, pure block-count arithmetic [`placement_ranked_meta.json`]. "Newly covered" is the marginal (de-overlapped) reach of each pick.

*Category is licence-derived (TABC off-premise permit class / county land-use code), not verified storefront type — "liquor" means the site holds a TABC off-premise permit, so restaurants with off-premise beer/wine permits (Royal Thai Restaurant, Olivella's) appear as "liquor". Categories drive candidate discovery only; ranks and coverage are category-independent (see §9 limitations).*


| #   | Name                          | Category          | ZIP   | Newly covered | Rank (composite) | Rank (grounded) | Cumulative  |
| --- | ----------------------------- | ----------------- | ----- | ------------- | ---------------- | --------------- | ----------- |
| 1   | Burmese Asian Grocery Store   | convenience       | 75231 | 17,437        | 7                | 3               | 17,437      |
| 2   | Odaa Beauty Supply LLC        | convenience       | 75254 | 14,423        | 90               | 48              | 31,860      |
| 3   | Super Fuels Lombardy LLC      | liquor            | 75220 | 14,289        | 1                | 5               | 46,149      |
| 4   | Corner Food #9                | liquor            | 75038 | 13,247        | 59               | 49              | 59,396      |
| 5   | Lavanderia                    | laundromat        | 75240 | 12,753        | 30               | 44              | 72,149      |
| 6   | French Nails Luxury Spa & Bar | barbershop/beauty | —     | 9,769         | 238              | 224             | 81,918      |
| 7   | Royal Thai Restaurant         | liquor            | 75206 | 9,683         | 513              | 340             | 91,601      |
| 8   | Daily Mart                    | liquor            | 75227 | 9,113         | 95               | 107             | 100,714     |
| 9   | Chilly Mart                   | liquor            | 75063 | 8,697         | 501              | 509             | 109,411     |
| 10  | Trak 10                       | liquor            | 75042 | 8,511         | 88               | 86              | **117,922** |
| 11  | Fox                           | gas station       | —     | 8,288         | 145              | 250             | 126,210     |
| 12  | SHS Nursery Inc               | liquor            | 75061 | 8,234         | 158              | 152             | 134,444     |
| 13  | Olivella's                    | liquor            | 75205 | 8,147         | 961              | 941             | 142,591     |
| 14  | 4243 W Pioneer Drive          | liquor            | 75061 | 8,126         | 152              | 122             | 150,717     |
| 15  | WinCo Foods                   | liquor            | 75041 | 7,724         | 178              | 200             | 158,441     |
| 16  | Wrap and Go                   | convenience       | 75211 | 7,701         | 331              | 222             | 166,142     |
| 17  | A-List CBD                    | convenience       | 75240 | 7,692         | 239              | 283             | 173,834     |
| 18  | Rochelle Food Mart            | liquor            | 75062 | 7,510         | 340              | 327             | 181,344     |
| 19  | Souls Boutique                | convenience       | 75224 | 7,486         | 182              | 186             | 188,830     |
| 20  | At Home Stores, LLC           | grocery           | 75231 | 7,482         | 211              | 117             | 196,312     |
| 21  | Finley Food Mart              | liquor            | 75062 | 7,337         | 268              | 220             | 203,649     |
| 22  | 7-Eleven #21261A              | liquor            | 75051 | 7,299         | 91               | 162             | 210,948     |
| 23  | Therapeutic Spa               | barbershop/beauty | 75240 | 7,065         | 836              | 1,609           | 218,013     |
| 24  | Tortas Las Tortugas           | liquor            | 75234 | 6,905         | 247              | 223             | 224,918     |
| 25  | Family Dollar #23653          | liquor            | 75211 | 6,888         | 256              | 214             | **231,806** |




### 7.3 Greedy coverage-gain summary (MEASURED)


| Sites   | Newly covered unserved residents | Share of 2,196,211 total unserved |
| ------- | -------------------------------- | --------------------------------- |
| Best 10 | **117,922**                      | 5.4%                              |
| Best 25 | **231,806**                      | 10.6%                             |
| Best 50 | **388,927**                      | 17.7%                             |


[`placement_ranked_meta.json`, cumulative_summary]. Sanity check: top pick's cKDTree reach (14,289) equals brute-force reach (14,289) [MEASURED, `placement_ranked_meta.json`, sanity_check].

### 7.4 Per-category top picks (by composite rank, MEASURED reach)

Categories below are licence-derived, not verified storefront types (see §7.2 note and §9 limitations) — hence oddities like Shoe Palace under "grocery" (land-use size class). Best single site per category [`placement_ranked_meta.json`, per_category_top10_by_composite]: **liquor** — Super Fuels Lombardy LLC (75220, reach 14,289, rank 1); **convenience** — 7-Eleven #38111A (75220, reach 11,959, rank 6); **barbershop/beauty** — Liras Barber Shop (75220, reach 14,066, rank 3); **grocery** — Shoe Palace (75220, reach 11,446, rank 13); **laundromat** — Coin Laundry (75220, reach 13,940, rank 12); **library** — Vickery Meadow Branch Library (75231, reach 16,110, rank 19); **gas station** — Shamrock (75240, reach 10,912, rank 55); **other storefront** — #1 Champion Billiards & Games (75220, reach 10,074, rank 62).

### 7.5 The Lombardy Ln cluster note

The composite-ranked top of the list is dominated by one micro-corridor: **the Lombardy Ln / Webb Chapel corridor in ZIP 75220** (tracts 48113007205/007206/009804) holds composite ranks 1-5, 9-11, 14-18 and several category leaders (Super Fuels Lombardy, La Michoacana #30, Liras Barber Shop, Fiesta Mart #77, Chilly Mart #11) [MEASURED, `placement_ranked_meta.json`]. A second dense pocket is **Park Ln / Vickery Meadow (75231)** (tract 48113007815), home to the single highest-reach candidate (Burmese Asian Grocery, reach 17,437) and the Vickery Meadow Branch Library. These two pockets combine very high unserved-population density with high vulnerability — the strongest placement targets in the county.

### 7.6 Nightlife corridors (invisible-risk gap)

Existing venue dispensers cluster in two entertainment ZIPs [MEASURED, `naloxone_locations.geojson`; `insights.md` §8]: **Deep Ellum (75226)** — Club Dada, Reno's Bar (nightlife) + Deep Ellum Community Center (harm-reduction, M-F 12p-7p) + Puff n Stuff (storefront dispenser); **Cedar Springs / Oak Lawn (75219)** — Station 4, JR's Bar & Grill, TMC, Sue Ellen's + Pandacorn Café. Every nightlife site is `access_247: False`; the Deep Ellum center closes at 7p, before peak nightlife risk. By contrast **Lower Greenville (75206) has 0 inventory sites**, **Uptown (75204) has 1** (Dallas 24 Hour Club, a recovery facility, not a nightlife dispenser), and **Design District (75207) has 1** [MEASURED, `naloxone_locations.geojson`]. These high-traffic bar districts have effectively no walk-up naloxone — an invisible-risk gap the tract-mortality layer cannot flag because it is driven by transient non-resident crowds, not resident deaths.

### 7.7 Spanish-language access gap (cited)

**Demand side (ACS B03003, retrieved 2026-07-23):** Dallas County is **41.3% Hispanic/Latino** (1,081,496 of 2,621,179) [ACS, `insights.md` §7]. DCHHS independently reports **"40% of Dallas County households speak a language other than English, ~1 in 5 of those with limited-English proficiency — the highest percentage in the region"** [PUBLISHED, OD2A-2024 p3, `od2a_extract.json`]. Seven of the ten top gap ZIPs are majority-Hispanic; **75211 (78.7%), 75217 (71.5%), 75212 (67.9%)** combine highest-vulnerability tiers with the densest Hispanic populations — precisely where OD2A-2024's 2024 fentanyl finding (Hispanic males now the leading fentanyl-death group, p10) lands.

**Supply side — Spanish-language documentation is ABSENT as a finding.** The naloxone inventory carries **no language field of any kind**: the 39 sites expose only `name, address, city, zip, type, category, access_247, hours_raw, source, verified_on, geocode_method, mobile` — no `language`, `spanish`, or `bilingual` attribute [MEASURED, `naloxone_locations.geojson`]. The upstream DSHS/ArcGIS and findtreatment.gov sources expose no Spanish-service flag either. **Zero Spanish-first supply-side documentation is discoverable** — a gap that cannot be measured because the field does not exist upstream, not because it was dropped.

### 7.8 Texas legal context (PUBLISHED statutory context, external to pipeline)

These are statutory/legal-context items (not derived from pipeline data; included for policy completeness):

- **SB 1462 (84th Legislature, 2015) standing order → placement is legal.** Texas authorizes naloxone dispensing/possession under a physician standing order, so a third party (a business hosting a naloxone box) may legally possess and distribute naloxone. Storefront placement as recommended here is lawful under this framework.
- **"Jessica Sosa Act" limits.** Texas has expanded opioid-antagonist and overdose-education provisions but has **not** enacted a general Good-Samaritan / broad harm-reduction regime; the practical limit is that expansions have centered on education and standing-order dispensing rather than authorizing supervised-use or syringe services. (Statutory item; not independently verified in repo data — treat the exact scope as requiring legal review.)
- **Fentanyl test strips remain paraphernalia.** Texas has not clearly exempted fentanyl test strips from the drug-paraphernalia statute (Health & Safety Code), so FTS distribution remains legally constrained — a harm-reduction tool unavailable to complement naloxone placement.
- **No SSP in Dallas County.** Texas law does not authorize syringe services programs (SSPs); none operates legally in Dallas County, removing a standard channel through which naloxone and test strips reach people who use drugs. Placement in everyday storefronts is partly a workaround for this absent infrastructure.

---



## 8. Figures

All four figures are committed as PNG + PDF with caption sidecars.

### Figure 1 — The measured naloxone access desert

Measured naloxone access desert in Dallas County

> Figure 1. The measured naloxone access desert in Dallas County. Each of the 645 census tracts is shaded by the share of its 2020 census-block population living more than one mile (straight-line) from any of the 38 fixed naloxone access points, using five fixed classes (< 5%, 5-50%, 50-95%, 95-< 100%, and 100% - a total desert). Values are measured, not modelled: every block's population (2020 PL 94-171 count) is assigned to the nearest supply point in EPSG:32138, with no interpolation or smoothing. Point symbols mark the 38 fixed supply sites by category; one mobile harm-reduction unit without fixed coordinates is omitted. The dashed outline is the union of 1-mile catchments around those sites, clipped to the county. County-wide, 2,196,211 residents (84.0%) live beyond one mile of supply and 902,611 are stranded (beyond one mile of supply and beyond a half-mile of transit). The 84.0% figure is conservative: the 38-point inventory credits sites with limited functional access — 13 OTP/methadone clinics that dispense chiefly to enrolled patients rather than walk-ins, and 7 nightlife venues whose dispensers are reachable only during venue hours, typically before peak overdose risk. Excluding them would enlarge the measured desert. Sources: Census 2020 PL 94-171 block populations (P1_001N) + TIGER 2024 tabblocks; TX DSHS NarcanSites (Apr 2026), SAMHSA findtreatment.gov & ArcGIS naloxone inventory (verified 2024-2026); TIGER/Line 2024; EPSG:32138.



### Figure 2 — Composite overdose-vulnerability index

Composite overdose-vulnerability index

> Figure 2. Composite overdose-vulnerability index for Dallas County census tracts. Fill colour is a composite vulnerability score, the equal-weighted mean (0.20 each) of five min-max-normalised layers: overdose mortality rate, poverty rate (ACS 2020-2024), uninsured rate (ACS 2020-2024), distance to the nearest naloxone access point, and inverted transit connectivity (DART stops within 0.5 mi). 643 tracts are ranked; two zero-population tracts are excluded. Tracts fall into three Jenks natural-break tiers (Tier 3|2 cut = 0.338; Tier 2|1 cut = 0.434, both marked on the colour bar); Tier 1 (highest vulnerability, 170 tracts) is outlined in heavy black. CDC suppression is near-universal: 592 of all 645 tracts (91.8%) carry suppressed death counts, 591 of them among the 643 ranked tracts (the remaining suppressed tract is a zero-population exclusion). Fine dotted hatching therefore marks the exception — the 52 ranked tracts whose counts are NOT suppressed; every unhatched ranked tract relies on a model-based rate. Sensitivity: the tiering is robust to the mortality weight but sensitive to the distance weight — doubling mortality weight leaves 87% of tracts in the same tier, while doubling distance weight flips 21.5% of tracts, all between adjacent tiers, with zero Tier 1↔Tier 3 flips. Sources: CDC NCHS 4day-mt2f (2025-01/2025-12, as of 2026-07-22); ACS 2020-2024 5-year; SVI 2022; naloxone inventory; DART GTFS 2026-07-23; TIGER 2024; EPSG:32138.



### Figure 3 — Ranked naloxone placement strategy

Ranked naloxone placement strategy

> Figure 3. Ranked naloxone placement strategy for Dallas County. Candidate storefronts are ranked by NEED × REACH. REACH is the number of currently unserved residents (2020 blocks beyond one mile of existing supply) within a half-mile walk of a candidate; NEED is tract vulnerability. Two NEED variants — the five-layer composite index and a mortality-free 'grounded' index — each multiplied by normalised REACH, agree closely (Spearman ρ = 0.966; 45 of the top 50 shared). The map shows the top 50 of 5,428 ranked candidates by composite score, sized by REACH. Two distinct rankings appear on the figure and their orders differ: the numbered labels give the five highest candidates under the composite ranking (1 = Super Fuels Lombardy), while the inset table summarises a separate rank-agnostic greedy coverage analysis (whose first pick is Burmese Asian Grocery Store): the best 10, 25, and 50 non-overlapping sites would newly reach 117,922, 231,806, and 388,927 of the 2,196,211 unserved residents. Candidate categories are licence-derived (TABC off-premise permit classes and county land-use codes), not verified storefront types; they inform candidate discovery only and do not affect NEED × REACH scores. Sources: Dallas business licences + TABC off-premise + OSM (2026-07); Census 2020 blocks + TIGER 2024; CDC NCHS 4day-mt2f; ACS 2020-2024; SVI 2022; naloxone inventory; TIGER 2024; EPSG:32138.



### Figure 4 — Overdose mortality at the CDC's native reporting units

Overdose mortality at CDC native reporting units

> Figure 4. Overdose mortality at the CDC's native reporting units, Dallas County. Each of the 150 CDC aggregation units (unions of 2-10 census tracts) is shaded by its model-based overdose death rate (deaths per 100,000, 12-month period ending December 2025), classified into five Jenks natural-break ranges. Rates span 0 to 64.7 per 100,000 (quartiles 13.8, 16.9, 21.4). Diagonal hatching marks the 138 units (92%) whose underlying death counts are CDC-suppressed. Texas mortality suppression prevents the CDC from publishing raw tract-level counts, so the tract product releases model-based rates aggregated to these native units; a 95% confidence interval is published for each unit. This is the honest mortality geography — the finest resolution the suppression regime permits without fabricating counts. Sources: CDC NCHS 4day-mt2f (2025-01/2025-12, as of 2026-07-22); TIGER 2024; EPSG:32138.

---



## 9. Limitations & data gaps

- **Suppression scale.** 592/645 tracts (91.8%) and 138/150 native units (92%) have CDC-suppressed counts; 149/170 Tier-1 tracts are suppressed. The highest-priority placement zones therefore rest on modeled, not observed, mortality [MODELED, `mortality_meta.json`, `mortality_units_meta.json`].
- **Provisional lag (~6-7 months).** VSRR's most recent ~6 monthly points revise upward; H2-2025 counts (535-585) are floors, not settled [VSRR, `mortality_meta.json`].
- **Code violations frozen 2018.** The Dallas code-violations archive (`x9pz-kdq9`) ends 2018-07-31; 0 records in any recent window. Current blight signal relies solely on live 311 (`d7e7-envw`, 8,838 records matched). The 355,549 lifetime violations are `violations_alltime_2013_2018`, not current; the vacancy proxy (9,444 addresses) is a pre-2018 heuristic, not a current vacancy registry [`builtenv_index.json`].
- **Mobile org unmeasurable.** The DFW Harm Reduction Access Movement 24/7 mobile unit has no fixed coordinates and is excluded from distance analysis; the 86.25% 24/7 desert is measured against 1 fixed point and could shrink if the mobile unit's routes were geocoded [`block_access_summary.json`].
- **Straight-line vs network distance.** All access distances are Euclidean (EPSG:32138); real walking/driving distances are longer, so the deserts are conservative lower bounds on hardship [`block_access_summary.json`, `coverage_gap.json`].
- **Supply inventory over-credits functional access — the 84% desert is conservative.** The 38 fixed supply points include 13 OTP/methadone clinics (naloxone dispensed chiefly to enrolled patients, not walk-ins) and 7 nightlife venues (dispensers reachable only during venue hours, which end before peak overdose risk; every nightlife site is `access_247: False`). Counting them as supply shrinks the measured desert; excluding them would push the 84.0% beyond-1-mile figure higher [MEASURED, `naloxone_locations.geojson`, `block_access_summary.json`].
- **Candidate categories are licence-derived, not verified storefront types.** The `category` field on the 5,428 candidates comes from TABC off-premise permit classes and county land-use codes — a restaurant holding an off-premise beer/wine permit is classed `liquor_store` (e.g. Royal Thai Restaurant, Olivella's), and land-use size thresholds misfile some retailers (e.g. Shoe Palace as `grocery_food_mart`). Categories drive candidate *discovery* only; NEED × REACH scores are category-independent, so rankings are unaffected — but per-category summaries inherit this noise [`build_candidates.py`, `placement_ranked.geojson`].
- **Non-computable metrics.** Home-ZIP vs incident-ZIP death concordance, and the true Chicago-style % of death points within 500 m of supply, both require SWIFS case-level records (request-only) and are **NOT COMPUTABLE** from open data [`walk_coverage.json`, `deaths_by_zip.README.md`].
- **County-only layers.** Non-fatal ED visits (ESSENCE) and homelessness (2026 PIT = 3,513 individuals, 82% Dallas / 18% Collin) are county/CoC-level and deliberately **not** allocated to tracts (avoids ecological fabrication) [`context_meta.json`, `composite_methods.json`].
- **OSM thinness.** OSM POI coverage for barbershop/beauty and laundromat is thin; `enrich_places.py` (paid Google Places) exists to backfill but is never auto-run [README].
- **Composite distance-weight sensitivity.** Doubling the distance weight flips 21.46% of tracts (FLAGGED >15%), so the Tier-1 set is one defensible configuration, not robust — though zero Tier1↔Tier3 flips occur [`composite_methods.json`].
- **Modeled tract rates smeared.** 150 native units expanded to 645 tracts, so one modeled rate is applied across ~4.3 tracts/unit — variance is visually smeared at tract level [`mortality_units_meta.json`].

---



## 10. Reproducibility



### 10.1 Scripts (what each builds)


| Script                     | Builds                                                                                                               |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `pull_mortality.py`        | `deaths_dallas.csv`, `tract_overdose.geojson`, `deaths_by_zip.csv` (header-only), `mortality_meta.json` |
| `build_context.py` (keyed) | `svi_tracts.geojson`, `uninsured_zcta.csv`, `vsrr.json`, `context_meta.json`                                        |
| `build_naloxone.py`        | `naloxone_locations.geojson` (39 sites)                                                                              |
| `build_coverage_gap.py`    | `coverage_gap.json` (tract-centroid distances)                                                                       |
| `build_walk_coverage.py`   | `walk_coverage.json` (3 proxies)                                                                                     |
| `build_block_access.py`    | `block_access.csv`, `block_access_summary.json`, `block_access_tracts.geojson` (the measured desert)                 |
| `build_builtenv.py`        | `builtenv_index.json` (311 + frozen code-violations + vacancy proxy)                                                 |
| `build_candidates.py`      | `placement_candidates.geojson/.csv`, `placement_candidates_meta.json` (5,538 candidates)                             |
| `build_composite.py`       | `composite_index.geojson`, `composite_methods.json`                                                                  |
| `rank_candidates.py`       | `placement_ranked.geojson/.csv`, `placement_ranked_meta.json` (NEED×REACH)                                           |
| `build_mortality_units.py` | `mortality_units.geojson`, `mortality_units_meta.json` (150 native units)                                            |
| `validate_baselines.py`    | `baseline_comparison.json/.md`                                                                                       |
| `export_figures.py`        | `figures/figure1-4.{png,pdf}` + captions                                                                             |
| `enrich_places.py`         | (manual/paid) Google Places backfill — never auto-run                                                                |




### 10.2 Refresh cadence

- **Daily** (GitHub Action, `17 8 * * *` UTC, keyless): `scripts/run_all.sh daily` — `pull_mortality → build_naloxone → build_mortality_units → build_coverage_gap → build_walk_coverage → build_composite` (via `refresh.sh`). CDC data and the Narcan feed can update any day.
- **Monthly** (`43 9 1 * *`): `scripts/run_all.sh monthly` — the daily chain plus `pull_od2a`, `build_builtenv`, `build_candidates`, and `validate_baselines`. `build_context` auto-skips without `CENSUS_API_KEY`.
- **Full rebuild (local):** `scripts/run_all.sh` adds `build_block_access`, `rank_candidates`, and `export_figures`; these pull a statewide TIGER block file and emit the manuscript figures, so they are kept out of CI.
- **Manual (annual/biennial):** `build_context.py` (keyed) whenever ACS/SVI/DART/PIT vintages update. Both CI cadences carry **no secrets**; keyed steps run only where the key is present.
- **Pinned reruns:** `PIPELINE_DATE=YYYY-MM-DD` re-creates an archived run's snapshot paths and `generated` stamps exactly.



### 10.3 Data dictionary (`data/clean/`)


| File                                               | Contents                                                                 |
| -------------------------------------------------- | ------------------------------------------------------------------------ |
| `deaths_dallas.csv`                                | 72-row county monthly 12-mo-ending VSRR series (2020-01→2025-12)         |
| `vsrr.json`                                        | raw CDC VSRR Socrata rows (72), written by `build_context.py`            |
| `tract_overdose.geojson`                           | 645 tracts: modeled rate, `count_suppressed`, `rate_suppressed`, period  |
| `mortality_meta.json`                              | mortality run metadata, suppression counts, caveats                      |
| `mortality_units.geojson` / `_meta.json`           | 150 CDC native aggregation units + rate quartiles                        |
| `deaths_by_zip.csv`                                | header-only by design (no public ZIP mortality)                          |
| `svi_tracts.geojson`                               | 645 tracts: SVI themes, ACS poverty/uninsured, DART stops, totpop        |
| `uninsured_zcta.csv`                               | 165 ZCTAs: pct uninsured (ACS 2020-2024)                                 |
| `context_meta.json`                                | context source URLs, vintages, null counts, SVI county rank (71/254)     |
| `naloxone_locations.geojson`                       | 39 supply sites (1 fixed 24/7, 13 OTP, 10 storefront, 7 nightlife, etc.) |
| `coverage_gap.json`                                | per-tract centroid→supply distances (any / 24-7 / storefront) + summary  |
| `walk_coverage.json`                               | 3 honestly-labeled 500 m coverage proxies (1.71 / 2.43 / 1.42%)          |
| `block_access.csv`                                 | per-block distances + within-threshold flags (25,682 populated blocks)   |
| `block_access_summary.json`                        | headline measured desert + per-tract rollup                              |
| `block_access_tracts.geojson`                      | tract shares of pop beyond 1 mi (Figure 1 fill)                          |
| `builtenv_index.json`                              | per-tract blight (311 + frozen violations) + vacancy proxy               |
| `placement_candidates.geojson/.csv` + `_meta.json` | 5,538 candidate storefronts, category/source breakdown                   |
| `placement_ranked.geojson/.csv` + `_meta.json`     | NEED×REACH ranking, greedy picks, per-category tops                      |
| `composite_index.geojson`                          | 645 tracts: raw+normalized layers, composite_score, tier                 |
| `composite_methods.json`                           | full index methodology, weights, Jenks cutoffs, sensitivity              |
| `baseline_comparison.json/.md`                     | prior-figure validation + ZIP-tier mapping                               |




### 10.4 How to re-run

```bash
cd "dallas research savespos"
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env          # add CENSUS_API_KEY (build_context only)
./scripts/run_all.sh          # full dependency-ordered chain
./refresh.sh                  # daily keyless subset
```

Every script resolves its own paths from `__file__` and can run standalone from any cwd. Only `enrich_places.py` takes CLI args and is never auto-invoked. The map (`map/index.html`, Leaflet) deploys via Vercel and always fetches the latest committed `data/clean/*`.

---

*End of report. Every figure is traceable to the named source + vintage; every value is tagged MEASURED, MODELED, or PUBLISHED. Any claim not reproducible from open pipeline data is marked NOT COMPUTABLE and attributed to its external source (OD2A-2024) rather than estimated.*