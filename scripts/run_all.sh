#!/usr/bin/env bash
#
# run_all.sh -- FULL Dallas County overdose-intelligence pipeline, in
# dependency order. Intended for local/manual runs and the monthly CI job
# (see .github/workflows/refresh.yml). For the keyless daily chain, use
# ./refresh.sh at the repo root instead.
#
# Dependency order:
#   pull_mortality      -> data/clean/{deaths_dallas,tract_overdose,deaths_by_zip}
#   build_context        -> data/clean/{svi_tracts,context_meta,uninsured_zcta}   [KEYED: CENSUS_API_KEY]
#   build_naloxone       -> data/clean/naloxone_locations.geojson
#   build_coverage_gap   -> data/clean/coverage_gap.json      (needs tract_overdose + naloxone)
#   build_walk_coverage  -> data/clean/walk_coverage.json     (needs tract_overdose + naloxone + svi_tracts)
#   build_builtenv       -> data/clean/builtenv_index.json
#   build_candidates     -> data/clean/placement_candidates.{geojson,csv}
#   build_composite      -> data/clean/composite_index.geojson (needs tract_overdose + svi_tracts + coverage_gap)
#   validate_baselines   -> validation report over the above (local-data-only)
#
# Behavior:
#   - set -euo pipefail is active, but individual step failures are CAUGHT
#     by run_step() so later steps that don't depend on the failed one can
#     still run. Failures are collected and reported at the end; the script
#     exits non-zero if ANY step actually failed.
#   - A script that is REQUIRED but not yet present on disk (e.g. a module
#     still being built by a parallel workstream) is reported as a WARNING
#     and SKIPPED -- this is not treated as a failure.
#   - build_context is KEYED (requires CENSUS_API_KEY in .env). If the key
#     is not available (e.g. this is invoked from a no-secrets CI job), the
#     step is cleanly SKIPPED with a warning rather than attempted and
#     failed. This lets the exact same script serve as the "full pipeline
#     minus keyed steps" chain referenced by the monthly CI job.
#
# Usage:
#   ./scripts/run_all.sh        (run from repo root, or anywhere -- paths
#                                 inside each script are anchored to the
#                                 repo root via __file__, not cwd)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Load .env if present (CENSUS_API_KEY, GOOGLE_MAPS_API_KEY). Never fails if
# absent -- keyed steps below degrade to a clean skip/fallback instead.
if [ -f "${REPO_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

# Prefer the project venv; fall back to python3 on the PATH.
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

# run_step_keyed NAME SCRIPT_PATH ENV_VAR_NAME
# Like run_step, but cleanly skips (as a WARNING, not a failure) if the
# named environment variable is not set, instead of invoking a script that
# is known to hard-exit without its API key.
run_step_keyed() {
  local name="$1"
  local path="$2"
  local var_name="$3"

  if [ -z "${!var_name:-}" ]; then
    echo ""
    echo "=================================================================="
    echo "STEP: ${name}  (${path})  [KEYED: ${var_name}]"
    echo "=================================================================="
    echo "WARNING: ${var_name} not set -- skipping '${name}' (keyed step, see README)."
    SKIPPED_STEPS+=("${name} (missing env: ${var_name})")
    return 0
  fi

  run_step "${name}" "${path}"
}

run_step       "pull_mortality"     "${SCRIPT_DIR}/pull_mortality.py"
run_step_keyed "build_context"      "${SCRIPT_DIR}/build_context.py"      "CENSUS_API_KEY"
run_step       "build_naloxone"     "${SCRIPT_DIR}/build_naloxone.py"
run_step       "build_coverage_gap" "${SCRIPT_DIR}/build_coverage_gap.py"
run_step       "build_walk_coverage" "${SCRIPT_DIR}/build_walk_coverage.py"
run_step       "build_builtenv"     "${SCRIPT_DIR}/build_builtenv.py"
run_step       "build_candidates"   "${SCRIPT_DIR}/build_candidates.py"
run_step       "build_composite"    "${SCRIPT_DIR}/build_composite.py"
run_step       "validate_baselines" "${SCRIPT_DIR}/validate_baselines.py"

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
  echo "run_all.sh: FAILED"
  exit 1
fi

echo "All executed steps succeeded."
echo "run_all.sh: OK"
