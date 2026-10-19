#!/bin/sh
# Install a built wheel into a fresh virtual environment and exercise the CLI outside the source tree.
# Usage: scripts/smoke-test-wheel.sh dist/saarathi-*.whl
set -eu
wheel=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
work=$(mktemp -d "${TMPDIR:-/tmp}/saarathi-smoke.XXXXXX")
trap 'rm -rf "$work"' EXIT
uv venv -q --python "${PYTHON:-3.12}" "$work/venv"
VIRTUAL_ENV="$work/venv" uv pip install -q "$wheel"
bin="$work/venv/bin"
"$bin/saarathi" --help > /dev/null
version=$("$bin/saarathi" version)
expected=$(basename "$wheel" | cut -d- -f2)
[ "$version" = "$expected" ] || { echo "version mismatch: $version != $expected"; exit 1; }
mkdir "$work/repo" && cd "$work/repo"
git init -q -b main && git -c user.name=smoke -c user.email=smoke@localhost commit -q --allow-empty -m init
"$bin/saarathi" init > /dev/null
"$bin/saarathi" policy validate --json | grep -q '"valid": true'
set +e
"$bin/saarathi" doctor --json > "$work/doctor.json"
code=$?
set -e
# Exit 0 when a lead is available, 5 when no provider CLI is installed (expected on CI runners).
[ "$code" -eq 0 ] || [ "$code" -eq 5 ] || { echo "doctor exited $code"; exit 1; }
grep -q '"schema_version": 1' "$work/doctor.json"
echo "smoke test passed: saarathi $version (doctor exit $code)"
