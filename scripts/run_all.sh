#!/usr/bin/env bash
#
# Pipeline runner.
#
#   ./scripts/run_all.sh [full|daily|monthly]     (default: full)
#
# Profiles
#   full     Every stage, in dependency order. The local rebuild that
#            reproduces every published figure and headline number.
#   daily    Keyless stages whose upstream data can change on any day: the CDC
#            mortality pull, the naloxone supply feed, and the local layers
#            derived from them. Invoked by ./refresh.sh and the daily CI job.
#   monthly  daily, plus the slower-moving open registries (built environment,
#            candidate universe) and the baseline validation. Invoked by the
#            monthly CI job.
#
# The heavy stages -- block access, placement ranking, and figure export -- run
# only under `full`. They need a statewide TIGER block download and produce the
# manuscript artefacts, so they are a deliberate local step rather than CI work.
#
# A stage that fails does not stop the run: later stages that do not depend on
# it still execute, failures are collected, and the script exits non-zero if any
# stage that actually ran failed. A stage whose script is missing, or whose
# required key is unset, is skipped with a warning rather than counted a failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PROFILE="${1:-full}"
case "${PROFILE}" in
  full|daily|monthly) ;;
  *) echo "Usage: $0 [full|daily|monthly]" >&2; exit 2 ;;
esac

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

echo "Profile:     ${PROFILE}"
echo "Interpreter: ${PYTHON}"
echo "Repo root:   ${REPO_ROOT}"

FAILED_STEPS=()
SKIPPED_STEPS=()

# run_step NAME PROFILES [ENV_VAR]
#   PROFILES is a comma-separated list of profiles the stage belongs to.
#   ENV_VAR, if given, names a required key; the stage is skipped when unset.
run_step() {
  local name="$1"
  local profiles="$2"
  local var_name="${3:-}"
  local path="${SCRIPT_DIR}/${name}.py"

  case ",${profiles}," in
    *",${PROFILE},"*) ;;
    *) return 0 ;;
  esac

  echo ""
  echo "=================================================================="
  echo "STEP: ${name}${var_name:+  [requires ${var_name}]}"
  echo "=================================================================="

  if [ ! -f "${path}" ]; then
    echo "WARNING: ${path} not found -- skipping."
    SKIPPED_STEPS+=("${name} (missing script)")
    return 0
  fi

  if [ -n "${var_name}" ] && [ -z "${!var_name:-}" ]; then
    echo "WARNING: ${var_name} not set -- skipping (see README)."
    SKIPPED_STEPS+=("${name} (missing env: ${var_name})")
    return 0
  fi

  if "${PYTHON}" "${path}"; then
    echo "-- OK: ${name}"
  else
    echo "-- FAILED: ${name}" >&2
    FAILED_STEPS+=("${name}")
  fi
}

#          stage                  profiles              required key
run_step   pull_mortality         full,daily,monthly
run_step   pull_od2a              full,monthly
run_step   build_context          full,monthly          CENSUS_API_KEY
run_step   build_naloxone         full,daily,monthly
run_step   build_mortality_units  full,daily,monthly
run_step   build_coverage_gap     full,daily,monthly
run_step   build_walk_coverage    full,daily,monthly
run_step   build_builtenv         full,monthly
run_step   build_candidates       full,monthly
run_step   build_composite        full,daily,monthly
run_step   build_block_access     full
run_step   rank_candidates        full
run_step   validate_baselines     full,monthly
run_step   export_figures         full

echo ""
echo "=================================================================="
echo "SUMMARY (profile: ${PROFILE})"
echo "=================================================================="

if [ "${#SKIPPED_STEPS[@]}" -gt 0 ]; then
  echo "Skipped (${#SKIPPED_STEPS[@]}):"
  printf '  - %s\n' "${SKIPPED_STEPS[@]}"
fi

if [ "${#FAILED_STEPS[@]}" -gt 0 ]; then
  echo "Failed (${#FAILED_STEPS[@]}):"
  printf '  - %s\n' "${FAILED_STEPS[@]}"
  echo ""
  echo "run_all.sh: FAILED"
  exit 1
fi

echo "All executed steps succeeded."
echo "run_all.sh: OK"
