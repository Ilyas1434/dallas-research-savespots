# dallas-naloxone-access

Measured block-level naloxone access, modeled overdose vulnerability, and
coverage-maximizing placement analysis for Dallas County, Texas.

This is the analytic pipeline behind *Measured Naloxone Access Deserts and
Modeled Overdose Vulnerability in Dallas County, Texas: A Block-Level
Geographic Analysis Under Mortality Data Suppression*. Every figure and
headline number in that paper is reproduced by `./scripts/run_all.sh`.

**Full findings:** [`report.md`](report.md). **Figures:** [`figures/`](figures/).
**Outputs:** [`data/clean/`](data/clean/).

## What this does, and why it is built this way

Texas mortality confidentiality rules suppress overdose death counts at fine
geography: 592 of 645 Dallas County census tracts carry suppressed counts, and
no public ZIP-level mortality product exists at all. Prioritizing on that
signal would mean layering inference on inference.

So the analysis inverts the usual approach. **Access is measured; mortality is
one modeled input among five.** Residents are counted exactly at block level,
and the distance from each block to each supply point is geometry rather than
estimation. The pipeline:

1. Measures straight-line distance from all 25,682 populated 2020 census blocks
   to all 38 fixed naloxone access points, in EPSG:32138, with no interpolation
   or smoothing.
2. Builds a five-layer, equal-weighted tract composite (modeled mortality,
   poverty, uninsurance, measured supply distance, inverted transit access)
   with Jenks tiering and a two-variant weight-sensitivity analysis.
3. Ranks 5,428 candidate storefronts by need multiplied by measured reach among
   currently unserved residents, under two need specifications — one of which
   contains no mortality input at all, as a check that the recommendation does
   not depend on the suppressed layer.

Every quantity is classified **measured** (a count of people or a computed
distance), **modeled** (a published model-based rate, survey estimate, or index
derived from them), or **published** (transcribed from a cited report). Where a
figure cannot be produced from open data it is reported as not computable and
attributed to its source, never estimated.

## Headline outputs

Of 2,613,539 county residents (exact 2020 decennial count):

| Measure | Value | Source file |
|---|---|---|
| Beyond 1 mile of any fixed supply | 2,196,211 (84.03%) | `block_access_summary.json` |
| Beyond 2 miles | 1,506,855 (57.66%) | `block_access_summary.json` |
| Stranded (>1 mi from supply **and** >0.5 mi from transit) | 902,611 across 9,779 blocks | `block_access_summary.json` |
| Fixed 24-hour access points countywide | 1 | `naloxone_locations.geojson` |
| Beyond 5 miles of that one 24-hour point | 2,254,279 (86.25%) | `block_access_summary.json` |
| Tier 1 tracts (of 643 ranked) | 170 | `composite_index.geojson` |
| Unserved residents newly reached by 25 optimal sites | 231,806 | `placement_ranked_meta.json` |
| Rank agreement, composite vs. mortality-free need | Spearman ρ = 0.966 | `placement_ranked_meta.json` |

The access measure is deliberately conservative: the 38 supply points credit 13
opioid treatment programs, which dispense methadone to enrolled patients rather
than distributing naloxone to walk-ins, and 7 nightlife venues open only during
business hours. The functional desert is larger than the measured one.

## Quick start

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env          # optional; see "API keys" below
./scripts/run_all.sh          # full rebuild
```

`geopandas` ships manylinux wheels bundling GEOS/GDAL/PROJ via `pyogrio`, so no
system packages are required. `pull_od2a.py` additionally needs `pdftotext`
(poppler) on `PATH`.

## Pipeline

`./scripts/run_all.sh [full|daily|monthly]` runs the stages below in dependency
order. A failing stage does not halt the run; independent stages still execute,
and the script exits non-zero if anything that ran failed. A stage missing its
required key is skipped with a warning, not treated as a failure.

| Stage | Profiles | Key | Produces |
|---|---|---|---|
| `pull_mortality` | full, daily, monthly | — | `deaths_dallas.csv`, `tract_overdose.geojson`, `deaths_by_zip.csv`, `mortality_meta.json` |
| `pull_od2a` | full, monthly | — | `od2a_extract.json`, `od2a_zips.geojson` |
| `build_context` | full, monthly | `CENSUS_API_KEY` | `svi_tracts.geojson`, `uninsured_zcta.csv`, `vsrr.json`, `context_meta.json` |
| `build_naloxone` | full, daily, monthly | — | `naloxone_locations.geojson` |
| `build_mortality_units` | full, daily, monthly | — | `mortality_units.geojson`, `mortality_units_meta.json` |
| `build_coverage_gap` | full, daily, monthly | — | `coverage_gap.json` |
| `build_walk_coverage` | full, daily, monthly | — | `walk_coverage.json` |
| `build_builtenv` | full, monthly | — | `builtenv_index.json` |
| `build_candidates` | full, monthly | — | `placement_candidates.{geojson,csv}`, `placement_candidates_meta.json` |
| `build_composite` | full, daily, monthly | — | `composite_index.geojson`, `composite_methods.json` |
| `build_block_access` | full | first run only | `block_access.csv`, `block_access_summary.json`, `block_access_tracts.geojson` |
| `rank_candidates` | full | — | `placement_ranked.{geojson,csv}`, `placement_ranked_meta.json` |
| `validate_baselines` | full, monthly | — | `baseline_comparison.{json,md}` |
| `export_figures` | full | — | `figures/figure[1-4].{png,pdf}` + captions |

`./refresh.sh` is a thin alias for the `daily` profile.

`enrich_places.py` is deliberately outside every profile. It calls the paid
Google Places API and only runs when invoked by hand with explicit `--tracts`
and a `--max-requests` cap. See its docstring for cost notes.

### Reproducing a published run

Snapshot filenames and `generated` stamps come from `pipeline_date()`, which
defaults to today. Pin it to re-create an archived run exactly:

```bash
PIPELINE_DATE=2026-07-23 ./scripts/run_all.sh
```

### API keys

Both keys live in a repo-root `.env`, which is gitignored.

- **`CENSUS_API_KEY`** — required by `build_context.py` for ACS subject-table
  calls ([free signup](https://api.census.gov/data/key_signup.html)).
  `build_block_access.py` needs it only on a first run with no
  `data/raw/census/` snapshot committed; the 2020 decennial count never
  revises, so the snapshot is reused thereafter. Because `build_context` is
  keyed it is excluded from CI, and its outputs are refreshed by hand when the
  ACS or SVI vintage updates.
- **`GOOGLE_MAPS_API_KEY`** — optional geocoding fallback in
  `build_naloxone.py` and `build_candidates.py`. Both check for the key and
  skip cleanly when it is absent; neither hard-fails. As of the last full run,
  0 records needed it (0.0% Census geocode failure rate).

### Automation

Two keyless GitHub Actions cadences, with no repository secrets configured:
`daily` at 08:17 UTC and `monthly` on the 1st at 09:43 UTC. Both commit changed
`data/clean/` files back to the default branch, and only when something
actually changed.

The `full`-only stages stay out of CI on purpose: `build_block_access` pulls a
statewide TIGER block file, and `export_figures` produces manuscript artefacts.
Both belong in a deliberate local rebuild.

## Data sources

| Source | Endpoint | Resolution | Vintage in this repo | Access |
|---|---|---|---|---|
| CDC VSRR provisional overdose deaths | Socrata `gb4e-yj24` | County, 12-month-ending | as of 2026-07-05; 72 rows, 2020-01 → 2025-12 | open |
| CDC NCHS model-based OD rates by tract | Socrata `4day-mt2f` | 150 CDC aggregation units → 645 tracts | as of 2026-07-22; period 2025-01/2025-12 | open |
| Census 2020 P.L. 94-171 block population | `api.census.gov` (`P1_001N`) | Census block | 2020 decennial; 38,180 blocks, 25,682 populated | keyed |
| Census TIGER/Line 2024 | `www2.census.gov` | Tract, block, county | TIGER2024 (2020 boundaries) | open |
| ACS 5-year subject tables | `api.census.gov` | Tract + ZCTA | ACS 2020–2024, released Jan 2026 | keyed |
| CDC/ATSDR Social Vulnerability Index | ArcGIS | Tract | SVI 2022 | open |
| DART GTFS | `dart.org` | Stop point | 6,974 stops, July 2026 | open |
| Texas DSHS NarcanSites | ArcGIS FeatureServer | Point | live pull each run | open |
| Local naloxone inventory | `data/raw/NaloxoneSites_April2026.csv` | Point | April 2026 snapshot | local |
| SAMHSA findtreatment.gov OTP locator | `findtreatment.gov` | Point | live pull each run | open |
| Dallas Certificates of Occupancy | Socrata `9qet-qt9e` | Point | 23,731 rows → 2,348 kept | open |
| TABC licenses | Socrata `kguh-7q9z` | Address (geocoded) | 6,101 rows → 2,664 off-premise | open |
| OpenStreetMap Overpass | Overpass API | Point | 782 elements | open |
| Dallas Public Library branches | Socrata `2ksy-mdcf` | Point | 30 branches | open |
| Dallas 311 blight requests | Socrata `d7e7-envw` | Point → tract | trailing 24 months | open |
| Dallas code violations | Socrata `x9pz-kdq9` | Point → tract | **frozen archive**, 2013-10 → 2018-07 | open |
| HousingForward NTX point-in-time count | PDF | County / CoC | 2026 PIT: 3,513 individuals | open |
| DCHHS OD2A 2024 annual report | PDF | County | published figures, transcribed | open |

## Limitations

Carried into the interpretation of every finding, and recorded in the emitted
metadata alongside the numbers themselves.

- **Suppression is extensive.** 592 of 645 tracts and 138 of 150 CDC reporting
  units have suppressed underlying death counts, including 149 of the 170 Tier
  1 tracts. Tier 1 means highest convergence of vulnerability across five
  dimensions, **not** highest death rate; mortality contributes 15.2% of the
  Tier 1 mean composite, less than transit inaccessibility at 38.2%.
- **Modeled tract rates are smeared.** One CDC rate covers an average of 4.3
  tracts. Tract-level variance in the mortality layer is an artifact of
  disaggregation, not an observed pattern. `mortality_units.geojson` publishes
  the same data at its true resolution; Figure 4 is the honest version.
- **Composite tiering is sensitive to distance weighting.** Doubling the
  mortality weight leaves 86.94% of tracts in tier; doubling the distance
  weight leaves 78.54%, above a 15% flip threshold. No tract moves between Tier
  1 and Tier 3 under either variant, so the coarse separation is stable even
  where exact membership is not.
- **Distances are Euclidean.** Real travel is longer, particularly across
  highway severance, the Trinity River floodplain, and the Great Trinity
  Forest. Reported deserts are conservative lower bounds.
- **One 24-hour mobile unit is unmeasurable.** It operates without fixed
  coordinates and is excluded with null geometry rather than given a
  placeholder point. Were its routes geocoded, the 24-hour desert would shrink
  by an amount that cannot be estimated.
- **Provisional counts revise upward.** The most recent ~6 months of the VSRR
  series are depressed by roughly a 7-month reporting lag; read them as floors.
- **Candidate categories are license-derived**, from TABC permit classes and
  county land-use codes, not field-verified. A restaurant holding an
  off-premise beer and wine permit appears under a beverage category. They
  inform candidate discovery and do not affect need × reach scores. Final
  siting requires ground verification.
- **The code-violations dataset is a frozen archive.** It has not updated since
  2019-02-06 and its records stop 2018-07-31, so it is reported as
  `violations_alltime_2013_2018` and its vacancy proxy is a historical
  neglect-pattern signal, not a current vacancy indicator.
- **County-only layers are excluded, not allocated.** Non-fatal overdose ED
  visits and point-in-time homelessness are published only at county or CoC
  level; pushing them down to tracts would be ecological fabrication.
- **No ZIP-level mortality exists.** `deaths_by_zip.csv` is header-only by
  design rather than backed into existence through an ungated crosswalk. See
  `data/clean/deaths_by_zip.README.md`.
- **No original primary data were collected.** Cultural and implementation
  analysis rests on published literature and the 2024 county needs assessment,
  not author fieldwork.

## Privacy

This project publishes only aggregated, tract-level-or-coarser health data. No
point-level or individual overdose records are collected, stored, or displayed
anywhere in this repository or the map. NCHS confidentiality suppression is
preserved and surfaced, never overridden or estimated around. The only
point-level data here are public commercial and institutional addresses —
clinics, stores, libraries — never residences or individuals. Any contributor
adding a source must maintain this rule.

## Map

`map/index.html` is a self-contained Leaflet page deployed as a static site via
Vercel. `vercel.json` rewrites `/` to it and marks `data/clean/*` responses
`must-revalidate` so the map always reads the latest committed data rather than
a stale CDN copy. A Vercel project connected to this repository picks up each
automated refresh with no manual redeploy.

## Citation

See [`CITATION.cff`](CITATION.cff). Please cite both the repository and the
accompanying paper.

## License

Code is MIT. Derived data under `data/clean/` is CC BY 4.0. Upstream inputs
remain under their publishers' terms; OpenStreetMap data is ODbL. See
[`LICENSE`](LICENSE).
