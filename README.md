# Dallas County Overdose Intelligence & Naloxone Placement

> **Full findings:** see [`report.md`](report.md) — the comprehensive final report (measured access desert, mortality findings, composite index, ranked placement recommendations, figures, limitations, reproducibility).

## Mission

This repository builds an open, reproducible data pipeline that maps overdose
mortality risk across Dallas County census tracts, inventories existing
naloxone/harm-reduction supply, measures the resulting coverage gaps, and
ranks candidate everyday-storefront locations (gas stations, convenience
stores, liquor stores, barbershops, laundromats, libraries, etc.) where
placing a public naloxone box would close the largest gaps between overdose
risk and existing supply. Every output is traceable to a named, live public
source; nothing is fabricated, interpolated across geographies it wasn't
collected at, or silently imputed. Where a figure genuinely cannot be
reproduced from open data (e.g. drug-specific mortality breakdowns), that is
stated explicitly rather than estimated.

The end product is a static map (`map/index.html`, deployed via Vercel) plus
a versioned `data/clean/` directory that a downstream analyst, journalist, or
public-health team can consume directly.

## Data sources

| Source | Endpoint | Cadence (source) | Resolution | Vintage in this repo | Access | Script |
|---|---|---|---|---|---|---|
| CDC VSRR provisional overdose deaths | `data.cdc.gov` Socrata `gb4e-yj24` | Monthly | County, 12-month-ending | data_as_of 2026-07-05; 72 rows, 2020-01 → 2025-12 | Open | `pull_mortality.py` |
| CDC NCHS drug-OD death rates by tract | `data.cdc.gov` Socrata `4day-mt2f` | Periodic (model refresh) | Census tract (model-based aggregation units) | data_as_of 2026-07-22; period 2025-01/2025-12; 645 Dallas tracts | Open | `pull_mortality.py` |
| Census TIGER/Line tract geometry | `www2.census.gov/geo/tiger/TIGER2024` | Annual (decennial-aligned) | Census tract | TIGER2024 (2020 tracts); 645 Dallas County (FIPS 48113) tracts | Open | `pull_mortality.py`, `build_context.py`, `common.py` |
| CDC/ATSDR Social Vulnerability Index | ArcGIS `SVI/CDC_ATSDR_Social_Vulnerability_Index_2022_USA` | Biennial | Census tract | SVI 2022 (ACS 2018-2022 vintage); 645 tracts | Open | `build_context.py` |
| Census ACS 5-year subject tables | `api.census.gov/data/2024/acs/acs5/subject` | Annual | Tract + ZCTA | ACS 2020-2024 5-year (released Jan 2026); 645 tracts, 165 ZCTAs (750xx-753xx) | **Keyed** (`CENSUS_API_KEY`) | `build_context.py` |
| DART GTFS transit stops | `www.dart.org/transitdata/latest/google_transit.zip` | Periodic (schedule updates) | Point → 0.5mi tract centroid buffer | 6,974 stops; 400/645 tracts with ≥1 stop within 0.5mi | Open | `build_context.py` |
| HousingForward NTX Point-in-Time count | PDF report, `housingforwardntx.org` | Annual | County/CoC (Dallas+Collin) | 2026 PIT: 3,513 individuals (82% Dallas / 18% Collin) | Open (PDF scrape) | `build_context.py` |
| DSHS / regional Narcan site feed | ArcGIS `NarcanSites_10152024` FeatureServer | Ad hoc / as updated | Point | live pull each run | Open | `build_naloxone.py` |
| Local naloxone site snapshot | `data/raw/NaloxoneSites_April2026.csv` | Static snapshot | Point | April 2026 | Local file | `build_naloxone.py` |
| SAMHSA findtreatment.gov OTP locator | `findtreatment.gov/locator/exportsAsJson/v2` | Live | Point | live pull each run | Open | `build_naloxone.py` |
| Google Geocoding API (fallback) | `maps.googleapis.com/maps/api/geocode` | On demand | Point | fallback only; 0 records currently require it | **Keyed** (`GOOGLE_MAPS_API_KEY`), optional | `build_naloxone.py`, `build_candidates.py` |
| Dallas 311 blight requests | Dallas Open Data Socrata `d7e7-envw` | Live, continuously updated | Point → tract | last 24 months (2024-07-23 → 2026-07-23); 8,974 rows fetched, 8,838 matched to tract | Open | `build_builtenv.py` |
| Dallas Code Compliance "Code Violations" | Dallas Open Data Socrata `x9pz-kdq9` | **Frozen** (no updates since 2019-02-06; data ends 2018-07-31) | Point → tract | 361,407 rows fetched (2013-10-01 → 2018-07-31 only), 355,549 matched to tract | Open | `build_builtenv.py` |
| Dallas Certificates of Occupancy | Dallas Open Data Socrata `9qet-qt9e` | Ongoing (permit registry) | Point (nested `geolocation`) | 23,731 rows fetched → 2,348 kept after land-use filter + address collapse | Open | `build_candidates.py` |
| TABC alcohol licenses | `data.texas.gov` Socrata `kguh-7q9z` | Ongoing | Address (geocoded) | 6,101 Dallas County rows → 2,664 off-premise kept | Open (geocoded via Census batch + optional Google fallback) | `build_candidates.py` |
| OpenStreetMap Overpass (Dallas County) | Overpass API, area 3601837698 | Live | Point | 782 elements fetched, 782 kept | Open | `build_candidates.py` |
| Dallas Public Library branches | Dallas Open Data Socrata `2ksy-mdcf` | Static-ish (branch list) | Point | 30 branches | Open | `build_candidates.py` |
| Google Places Nearby Search | `maps.googleapis.com/maps/api/place/nearbysearch` | On demand, **manual only** | Point | not run by default (see Known limitations) | **Keyed, paid**, opt-in only | `enrich_places.py` (never in automated chain) |

All CDC Socrata/ArcGIS/Census/Dallas Open Data/TABC/OSM/SAMHSA/DART/HUD-adjacent
endpoints above are read-only public APIs; none require authentication except
where marked **Keyed**.

## Keyed vs. open sources — `.env` setup

Two API keys are used, both loaded from a repo-root `.env` file (never
committed — see `.gitignore`):

```
CENSUS_API_KEY=your_census_api_key_here
GOOGLE_MAPS_API_KEY=your_google_maps_api_key_here
```

- **`CENSUS_API_KEY`** — required by `build_context.py` for all Census ACS
  5-year subject-table calls. Get a free key at
  <https://api.census.gov/data/key_signup.html>. Without it, `build_context.py`
  logs `FATAL` and exits 1 — this is by design (Census has required a key for
  API access since May 2026 per the script's own logging). Because this
  script is keyed, it is **excluded from the public daily/CI refresh chain**
  (`refresh.sh` / the daily GitHub Actions job) per the no-secrets rule; its
  committed outputs (`svi_tracts.geojson`, `context_meta.json`,
  `uninsured_zcta.csv`) are refreshed manually or via the monthly CI job
  running on a machine/session where the key happens to be present — in
  practice, this means a human runs it locally when the ACS/SVI vintage
  updates (annually) or DART/PIT data refreshes (semi-annually).

- **`GOOGLE_MAPS_API_KEY`** — an *optional* fallback geocoder used by
  `build_naloxone.py` (address → lat/lon for site rows Census geocoding
  can't match) and `build_candidates.py` (TABC license addresses that the
  Census batch geocoder can't match). Both call sites check
  `os.environ.get("GOOGLE_MAPS_API_KEY")` and simply skip the Google fallback
  (logging a note) if it's unset — **neither script hard-fails without it**.
  As of the last run, 0 naloxone records and effectively 0 TABC records
  (0.0% geocode failure rate) needed this fallback, so both scripts are safe
  to run keyless. Get a key at
  <https://console.cloud.google.com/google/maps-apis> (enable the Geocoding
  API; billing applies).

- **Never commit `.env`.** It's already in `.gitignore`. If you rotate a key,
  just edit `.env` locally — nothing downstream needs a code change.

- `enrich_places.py` (Google Places Nearby Search, **paid**) is intentionally
  never wired into `run_all.sh`, `refresh.sh`, or CI. It only runs when
  invoked manually with explicit `--tracts` (and a request cap via
  `--max-requests`), specifically to backfill thin OSM coverage for
  barbershop/beauty and laundromat categories. See its docstring for cost
  notes before running it.

## How to run

### One-time setup

```bash
cd "dallas research savespos"
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # or create .env manually; see keys above
```

(`requirements.txt` pins `requests`, `pandas`, `geopandas`, `shapely`,
`matplotlib`, `jenkspy`, `pyproj`, `python-dotenv`. `geopandas>=0.14` ships
manylinux wheels with GEOS/GDAL/PROJ bundled via `pyogrio`, so it installs
cleanly on Linux CI runners with no system packages required.)

### Full pipeline (dependency order)

```bash
./scripts/run_all.sh
```

Runs, in order: `pull_mortality` → `build_context` → `build_naloxone` →
`build_coverage_gap` → `build_walk_coverage` → `build_builtenv` →
`build_candidates` → `build_composite` → `validate_baselines`. Uses
`./venv/bin/python` if the venv exists, else falls back to `python3`. Loads
`.env` if present. A step whose script file is missing, or whose required
env var (`CENSUS_API_KEY` for `build_context`) is unset, is **skipped with a
warning**, not treated as a crash — downstream steps that don't depend on it
still run. The script exits non-zero only if a step that *did* run actually
failed; a summary of skipped/failed steps prints at the end.

### Daily keyless refresh

```bash
./refresh.sh
```

Runs only: `pull_mortality` → `build_naloxone` → `build_coverage_gap` →
`build_walk_coverage` → `build_composite`. No API keys required. See
"Automation cadence" below for why exactly these five.

### Individual scripts

Every script can be run standalone from the repo root (each resolves its own
paths from `__file__`, so it also works from any cwd):

```bash
./venv/bin/python scripts/pull_mortality.py
./venv/bin/python scripts/build_context.py        # needs CENSUS_API_KEY in .env
./venv/bin/python scripts/build_naloxone.py
./venv/bin/python scripts/build_coverage_gap.py
./venv/bin/python scripts/build_walk_coverage.py
./venv/bin/python scripts/build_builtenv.py
./venv/bin/python scripts/build_candidates.py
./venv/bin/python scripts/build_composite.py
./venv/bin/python scripts/validate_baselines.py

# manual, keyed, paid, opt-in only:
./venv/bin/python scripts/enrich_places.py --smoke-test
./venv/bin/python scripts/enrich_places.py --tracts 48113004300,48113020402 --max-requests 50
```

None of the automated-chain scripts take CLI arguments; only `enrich_places.py`
does (`--tracts`, `--max-requests`, `--smoke-test`), and it is never called
automatically.

## Automation cadence

Two GitHub Actions jobs (`.github/workflows/refresh.yml`), both keyless
(**no repository secrets are configured or used**):

| Job | Schedule (UTC) | Chain | Rationale |
|---|---|---|---|
| `daily-refresh` | `17 8 * * *` (daily, ~02-03am US Central) | `refresh.sh` (5 keyless steps) | CDC VSRR/tract-rate data and the ArcGIS Narcan feed can update any day; re-deriving `coverage_gap`, `walk_coverage`, and `composite_index` from freshly-pulled layers is cheap, local, and keeps the map current without needing secrets. |
| `monthly-full-refresh` | `43 9 1 * *` (1st of month) | `scripts/run_all.sh` (full 9-step chain, minus `build_context`) | Rebuilds the slower-moving open layers — 311/code-violations blight index (`build_builtenv`), CO/TABC/OSM/library candidate universe (`build_candidates`) — plus re-runs `validate_baselines`. `build_context` auto-skips (missing `CENSUS_API_KEY`, by design — see below) since this job also carries no secrets. |

Both jobs also accept `workflow_dispatch` for a manual run (`mode: daily` or
`monthly`), and commit any resulting `data/clean/*` changes back to the
repo as `github-actions[bot]`, only if something actually changed
(`git diff --cached --quiet` gate).

**Cadence reasoning by layer:**

- **Mortality (VSRR + tract rates)** — CDC republishes monthly; polling daily
  is harmless and ensures same-day pickup. Daily.
- **Naloxone supply (ArcGIS + findtreatment.gov)** — sites/hours can change
  any day. Daily.
- **Coverage gap / walk coverage / composite index** — pure local
  recomputation from the layers above (+ whatever context/SVI/built-env data
  currently sits in `data/clean/`, even if not rebuilt that day). Cheap, so
  daily is fine and keeps the map's headline gap metrics current.
- **Census/ACS/SVI context, DART, PIT** (`build_context.py`) — genuinely
  annual/biennial in the source data, and this script is **keyed**
  (`CENSUS_API_KEY`), so it is deliberately excluded from *both* automated
  CI jobs per the no-secrets rule. It is re-run manually, weekly-to-monthly
  at most, whenever a human with the key wants to refresh it.
- **Built environment (311 + code violations) and placement candidates
  (CO + TABC + OSM + libraries)** — the underlying registries move on a
  weeks-to-months timescale (new permits, license renewals, occasional 311
  category shifts). Monthly is more than sufficient and keeps Socrata/OSM
  load light. Monthly-only.
- **`validate_baselines`** — a sanity check against previously-published
  figures; only meaningful right after a fuller rebuild, so it rides along
  with the monthly job, not the daily one.
- **`enrich_places.py`** — never automated (keyed, paid); always a manual,
  explicit, capped invocation.

**Every keyless-chain script tolerates a missing `GOOGLE_MAPS_API_KEY`** by
design (`os.environ.get()` returns `None`, and both `geocode_google()` in
`build_naloxone.py` and the Google-fallback branch in `build_candidates.py`
check for a falsy key and skip cleanly with a log line) — so no script in
either automated chain can hard-fail for lack of a Google key. The one
script that *does* hard-exit without its key, `build_context.py`
(`CENSUS_API_KEY`), is the one script kept out of both CI jobs.

## Output file dictionary (`data/clean/`)

| File | Produced by | Schema summary |
|---|---|---|
| `deaths_dallas.csv` | `pull_mortality.py` | County-level monthly series: `month_ending, provisional_od_deaths_12mo_ending, count_suppressed, pct_records_pending, pct_records_complete, data_as_of, pulled_at`. 72 rows, 2020-01 → 2025-12. |
| `tract_overdose.geojson` | `pull_mortality.py` | 645 Dallas tract features: `geoid, od_death_rate_per_100k, count_suppressed, rate_suppressed, period, source, data_as_of` + geometry. 592/645 tracts have `count_suppressed=true` (NCHS 1-9 suppression) but all 645 carry a modeled rate. |
| `deaths_by_zip.csv` | `pull_mortality.py` | **Header-only, intentionally.** `deaths_by_zip.README.md` explains why: no public ZIP-level mortality product exists for Dallas; fabricating one via an ungated crosswalk was rejected. |
| `mortality_meta.json` | `pull_mortality.py` | Run metadata: source URLs, row/vintage counts, suppression counts, caveats. |
| `svi_tracts.geojson` | `build_context.py` | 645 tract features: `geoid, svi_overall, svi_theme1-4, pct_poverty_svi, pct_uninsured_svi, pct_poverty_acs2024, pct_uninsured_acs2024, dart_stops_halfmile, totpop` + geometry. |
| `uninsured_zcta.csv` | `build_context.py` | `zcta, NAME, pct_uninsured_acs2024` — 165 ZCTAs (750xx-753xx). |
| `context_meta.json` | `build_context.py` | TIGER/SVI/ACS/VSRR/DART/PIT source URLs, vintages, row counts, null counts, Dallas's statewide SVI rank (71 of 254 TX counties). |
| `naloxone_locations.geojson` | `build_naloxone.py` | 39 point/mobile features merging ArcGIS + local CSV + SAMHSA OTPs; `category` (recovery_services, harm_reduction, nightlife_venue, storefront_dispenser, health_clinic, otp_methadone), source, `open_24h` where knowable. Top-level `properties` carries run metadata (category counts, geocode failure rate, sources). |
| `coverage_gap.json` | `build_coverage_gap.py` | Per-tract distance (metres/miles) from tract centroid to nearest naloxone supply — any / 24-7 / storefront — plus a `summary` block (means, medians, `pct_tracts_within_1mi_any` = 16.6%, `pct_tracts_within_2mi_any` = 43.4%). |
| `walk_coverage.json` | `build_walk_coverage.py` | Three honestly-labeled 500m-coverage proxies: (a) % tracts with centroid within 500m of supply = 1.71%, (b) rate-weighted version = 2.43%, (c) population-weighted version = 1.42%. |
| `builtenv_index.json` | `build_builtenv.py` | `meta` (sources, caveats, vacancy-proxy method) + `tracts` keyed by GEOID: `blight_311_24mo` (+ by category), `violations_alltime_2013_2018` (+ by category), `vacancy_proxy_addresses`. |
| `placement_candidates.geojson` / `.csv` | `build_candidates.py` | 5,538 candidate storefronts after cross-source dedupe: `name, address, zip, category, source, lat, lon, geoid, open_24h, n_source_rows_merged`. Categories: liquor_store (2,578), convenience_corner_store (1,164), barbershop_beauty (1,155), grocery_food_mart (218), other_storefront (269), gas_station (36), laundromat (79), library (39). |
| `placement_candidates_meta.json` | `build_candidates.py` | Per-source row counts (CO 2,341 / TABC 2,662 / OSM 663 / DallasLibraries 30), geocode failure rate (0.0%), category breakdown. |
| `composite_index.geojson` | `build_composite.py` | 645 tract features (643 ranked, 2 excluded for zero population): raw + normalized values per layer, `composite_score`, `tier` (1=highest vulnerability .. 3=lowest), `tier_label`, `data_completeness`. |
| `composite_methods.json` | `build_composite.py` | Full methodology: 5 equal-weighted (0.2 each) layers (mortality rate, poverty %, uninsured %, distance to nearest naloxone, DART stop density — inverted), min-max normalization, Jenks 3-tier breaks (Tier 1 = 170 tracts, Tier 2 = 303, Tier 3 = 170 under equal weights), a weight-sensitivity analysis (mortality-doubled / distance-doubled variants), and excluded layers (non-fatal OD ED visits, homelessness — both county-only, not allocated to tracts). |
| `baseline_comparison.json` / `.md` | `validate_baselines.py` | Checks prior published SaveSpots figures against what this pipeline can independently reproduce. Reproducible: crude mortality rate by year (e.g. 2025 = 22.35/100k on 582 deaths / 2,604,053 population) and tract→ZIP tier mapping. Explicitly marked **not reproducible**: fentanyl share/growth, opioid-stimulant combo deaths, hospital OD visits, and any pre-2020 baseline year (VSRR starts 2020-01) — these come from the DCHHS OD2A annual report, a source outside this pipeline's live feeds. |
| `vsrr.json` | `pull_mortality.py` | Raw CDC VSRR Socrata rows as pulled (72 records) — retained for audit alongside the cleaned `deaths_dallas.csv`. |

## Known limitations

- **County-only resolution for non-fatal overdose and homelessness data.**
  Non-fatal overdose ED visits and the HousingForward NTX Point-in-Time
  homeless count are both published only at the county (or Dallas+Collin CoC)
  level. Neither is spatially allocated down to tracts — doing so would
  fabricate precision the source data doesn't support — so both are
  explicitly **excluded** from `composite_index.geojson`'s layers (see
  `composite_methods.json` → `excluded_layers`) and reported only at their
  native county resolution (`context_meta.json` → `pit`).
- **CDC tract-level suppression.** 592 of 645 Dallas tracts have
  `count_suppressed=true` in `tract_overdose.geojson` (NCHS confidentiality
  rule for numerators of 1-9). The CDC's model-based product still supplies
  a rate estimate for every tract, so `rate_suppressed` is false for all 645
  — but the underlying death **counts** for those 592 tracts should not be
  treated as precise.
- **Code-violations dataset is a frozen archive.** Dallas Open Data's "Code
  Violations" dataset (`x9pz-kdq9`) has not been updated since
  2019-02-06 and its records stop at 2018-07-31 — it contains **zero** rows
  in any recent window. `build_builtenv.py` reports it honestly as
  `violations_alltime_2013_2018` (lifetime totals over its real 2013-2018
  coverage) rather than pretending it reflects "last 24 months." The
  vacancy proxy derived from it (9,444 flagged addresses) is therefore a
  **historical (pre-2018) neglect-pattern signal, not a current vacancy
  indicator** — use with caution. Current-window blight signal relies on
  the live 311 dataset (`d7e7-envw`) instead.
- **Provisional mortality reporting lag (~6-7 months).** CDC VSRR counts are
  provisional; the most recent ~6 months of the 12-month-ending series are
  subject to material upward revision as toxicology and death-certificate
  processing completes. Treat the most recent few points on
  `deaths_dallas.csv` as undercounts.
- **No public ZIP-level mortality product.** DSHS/CDC do not publish
  ZIP-level overdose mortality for Dallas. `deaths_by_zip.csv` is
  intentionally header-only rather than backed into existence via an
  ungated tract→ZCTA population crosswalk (see
  `data/clean/deaths_by_zip.README.md`). Any ZIP-level comparison must be
  produced as an explicit tract→ZIP spatial join of `tract_overdose.geojson`
  and labeled as an approximation, never as an independent measurement.
- **Historical baselines only partially reproducible.** `validate_baselines.py`
  can reproduce crude mortality rates and tier mappings from 2020 onward
  (VSRR's series start) but cannot reproduce fentanyl-share, fentanyl-growth,
  opioid-stimulant-combo, or hospital-OD figures (drug-specific data isn't in
  any live feed this pipeline pulls — those come from the DCHHS OD2A annual
  report) or any pre-2020 baseline year. See `baseline_comparison.md` for
  the full comparison table with explanations.
- **Vacancy and `open_24h` are heuristics, not verified facts** — documented
  as such in `build_builtenv.py` and `build_candidates.py` respectively;
  never presented as ground truth.
- **Google-keyed fallbacks are currently unused in practice** (0.0% geocode
  failure rate as of the last full run) but the code paths exist and are
  exercised only if the open geocoders (Census) fail to match an address.

## Map deploy (Vercel)

The map (`map/index.html`, a self-contained Leaflet page) is deployed as a
static site via Vercel. `vercel.json` at the repo root rewrites `/` to
`/map/index.html` and sets `data/clean/*` responses to
`Cache-Control: public, max-age=0, must-revalidate` so the map always
fetches the latest committed data on load rather than serving a stale CDN
cache.

To deploy/redeploy:

```bash
npx vercel        # first deploy / link project
npx vercel --prod # promote to production
```

Because the daily/monthly GitHub Actions jobs commit straight to `data/clean/`
on the default branch, a Vercel project connected to this repo (auto-deploy
on push) picks up each automated refresh without any manual redeploy step.

## Privacy rule

**This project publishes only aggregated, tract-level-or-coarser data. No
point-level, individual overdose death records are collected, stored, or
displayed anywhere in this repository or the map.** The finest resolution at
which mortality is ever reported is the census tract (a CDC-modeled
aggregation unit covering hundreds to low thousands of residents), and NCHS
confidentiality suppression (counts of 1-9 → `count_suppressed=true`) is
preserved and surfaced, never overridden or estimated around. Point-level
data in this repo (naloxone site locations, candidate storefronts) refers
exclusively to **public commercial/institutional addresses** (pharmacies,
clinics, stores, libraries) — never to residences or individuals. Any
future contributor adding a data source must maintain this rule: aggregate
only, no individual-level health records, ever.
