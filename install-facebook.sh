#!/usr/bin/env bash
set -euo pipefail
# Compatibility entry point: both features share the same optional browser.
bash "$(dirname "${BASH_SOURCE[0]}")/install-browser.sh"
