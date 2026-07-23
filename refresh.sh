#!/usr/bin/env bash
#
# refresh.sh -- DAILY, keyless-safe data refresh chain for the Dallas County
# overdose-intelligence pipeline. This is the chain the public GitHub Action
# runs on a daily cron with NO secrets configured (see
# .github/workflows/refresh.yml). For the full pipeline (including keyed and
# monthly/annual-cadence steps), see ./scripts/run_all.sh instead.
#
# CADENCE REASONING (why these 5 steps, and why daily is appropriate/safe):
#   - pull_mortality      : CDC VSRR provisional counts + tract OD rates are
#                            republished on a rolling monthly basis by CDC;
#                            polling daily is harmless (cheap Socrata reads)
#                            and guarantees we pick up a monthly refresh the
#                            same day it lands, with no key required.
#   - build_naloxone      : ArcGIS "NarcanSites" live feed + findtreatment.gov
#                            OTP locator can change any day (new sites,
#                            hours, closures) -- open endpoints, no key
#                            required. (Google geocoding fallback is OPTIONAL
#                            or unused currently and is skipped cleanly if
#                            GOOGLE_MAPS_API_KEY is absent -- see README.)
#   - build_coverage_gap  : Pure re-derivation from tract_overdose.geojson +
#                            naloxone_locations.geojson (both refreshed
#                            above). Cheap, local, no network dependency of
#                            its own -- safe/free to run daily.
#   - build_walk_coverage : Same as above -- local recomputation from the
#                            two live-refreshed layers (+ svi_tracts.geojson
#                            if present, which is committed and updated on
#                            its own slower cadence). Cheap, local.
#   - build_composite     : Local-data-only recomposition of the composite
#                            vulnerability index from whatever
#                            tract_overdose / svi_tracts / coverage_gap
#                            currently sit in data/clean/ -- so a daily run
#                            reflects the freshly pulled mortality + supply
#                            layers even though the slower-moving inputs
#                            (ACS/SVI context, built-environment, candidate
#                            storefronts) are not rebuilt every day.
#
#   EXCLUDED from this daily chain, and why:
#   - build_context   : KEYED (requires CENSUS_API_KEY) -- excluded per the
#                        no-secrets rule for the public daily chain. Its
#                        inputs (ACS 5-year estimates, CDC SVI, DART GTFS,
#                        PIT count) are annual/semi-annual in the source
#                        data itself, so committed context_meta.json /
#                        svi_tracts.geojson output is refreshed manually or
#                        via the monthly job (see scripts/run_all.sh),
#                        weekly/monthly at most -- never needs to be daily.
#   - build_builtenv  : Dallas 311 + code-violations data move on a
#                        weeks-to-months timescale, not daily -- monthly
#                        cadence is more than sufficient and keeps Socrata
#                        load light. Run via scripts/run_all.sh.
#   - build_candidates: Certificates of Occupancy / TABC licenses / OSM POIs
#                        / library branches all change slowly (new
#                        businesses, license renewals) -- monthly cadence.
#                        Run via scripts/run_all.sh. (Its Google-geocode
#                        fallback is also keyed and must never run in the
#                        no-secrets daily chain.)
#   - validate_baselines: Only meaningful to re-run right after a fuller
#                        rebuild (build_builtenv/build_candidates/
#                        build_context); not part of the keyless daily set.
#                        Included in scripts/run_all.sh.
#   - enrich_places   : NEVER automated (keyed, paid, must be invoked
#                        manually with explicit --tracts). Not referenced
#                        here or in run_all.sh.
#
# Usage:
#   ./refresh.sh   (run from repo root, or anywhere -- each script anchors
#                    its own paths to the repo root)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
cd "${REPO_ROOT}"

# Load .env if present. Not required for this chain (no keyed steps run
# here), but harmless if it exists -- e.g. GOOGLE_MAPS_API_KEY, if set, is
# used as an optional geocode fallback by build_naloxone with no effect on
# whether this chain succeeds.
if [ -f "${REPO_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

if [ -x "${REPO_ROOT}/venv/bin/python" ]; then
  PYTHON="${REPO_ROOT}/venv/bin/python"
else
  PYTHON="python3"
fi

echo "Using interpreter: ${PYTHON}"
echo "Repo root: ${REPO_ROOT}"

FAILED_STEPS=()
SKIPPED_STEPS=()

# run_step NAME SCRIPT_PATH
run_step() {
  local name="$1"
  local path="$2"

  echo ""
  echo "=================================================================="
  echo "STEP: ${name}  (${path})"
  echo "=================================================================="

  if [ ! -f "${path}" ]; then
    echo "WARNING: ${path} not found -- skipping '${name}' (module not yet built?)"
    SKIPPED_STEPS+=("${name} (missing script: ${path})")
    return 0
  fi

  if "${PYTHON}" "${path}"; then
    echo "-- OK: ${name}"
  else
    echo "-- FAILED: ${name}" >&2
    FAILED_STEPS+=("${name}")
  fi
}

run_step "pull_mortality"      "${REPO_ROOT}/scripts/pull_mortality.py"
run_step "build_naloxone"      "${REPO_ROOT}/scripts/build_naloxone.py"
run_step "build_coverage_gap"  "${REPO_ROOT}/scripts/build_coverage_gap.py"
run_step "build_walk_coverage" "${REPO_ROOT}/scripts/build_walk_coverage.py"
run_step "build_composite"     "${REPO_ROOT}/scripts/build_composite.py"

echo ""
echo "=================================================================="
echo "SUMMARY"
echo "=================================================================="

if [ "${#SKIPPED_STEPS[@]}" -gt 0 ]; then
  echo "Skipped (${#SKIPPED_STEPS[@]}):"
  for s in "${SKIPPED_STEPS[@]}"; do
    echo "  - ${s}"
  done
fi

if [ "${#FAILED_STEPS[@]}" -gt 0 ]; then
  echo "Failed (${#FAILED_STEPS[@]}):"
  for s in "${FAILED_STEPS[@]}"; do
    echo "  - ${s}"
  done
  echo ""
  echo "refresh.sh: FAILED"
  exit 1
fi

echo "All executed steps succeeded."
echo "refresh.sh: OK"
