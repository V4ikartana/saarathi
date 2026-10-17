#!/bin/sh
# Copy the starter into a fresh Git repository and run Saarathi against it.
# Usage: examples/health-widget/demo.sh [claude-codex|codex-only] [target-dir]
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
variant=${1:-claude-codex}
target=${2:-$(mktemp -d "${TMPDIR:-/tmp}/saarathi-demo.XXXXXX")}
cp -R "$here/starter/." "$target/"
cd "$target"
git init -q -b main
git add .
git -c user.name=demo -c user.email=demo@localhost commit -q -m "Starter project"
saarathi init --name health-widget-demo
cp "$here/policy.$variant.yaml" .saarathi/policies.yaml
saarathi doctor || echo "doctor reported problems; fix them before running."
echo "Demo repository: $target"
echo "Next: cd $target && saarathi run $here/brief.md"
