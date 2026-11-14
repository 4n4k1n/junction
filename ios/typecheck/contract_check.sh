#!/bin/bash
# Backend <-> iOS contract check: decodes the recorded backend responses with the app's Swift models.
# Regenerate the recording first: (cd backend && .venv/bin/python scripts/example_clap_session.py)
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$(mktemp -d)"
cp "$HERE/contract_main.swift" "$OUT/main.swift"
swiftc -o "$OUT/contract" "$HERE/../LearnLens/Models/BuildModels.swift" "$OUT/main.swift"
"$OUT/contract" "$HERE/../../docs/examples/clap-light-session.json"
