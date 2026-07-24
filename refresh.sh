#!/usr/bin/env bash
#
# Daily keyless refresh. Thin entrypoint over the `daily` profile of
# scripts/run_all.sh, which is where the stage list and its rationale live.
#
# The daily chain re-pulls the two feeds that can change on any day -- CDC
# mortality and the naloxone supply inventory -- then re-derives the local
# layers that depend on them. It needs no API keys, which is what lets the
# public CI job run it with no repository secrets configured.

set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/run_all.sh" daily
