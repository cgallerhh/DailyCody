#!/usr/bin/env zsh
set -euo pipefail

cd "$(dirname "$0")/.."

python3 scripts/export_application_wiki_snapshot.py
