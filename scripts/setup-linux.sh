#!/usr/bin/env bash
# Thin wrapper for developers in the repo. End users on a `pip install` should
# just run `rf-agent setup linux` directly.
set -euo pipefail
exec rf-agent setup linux "$@"
