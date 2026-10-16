#!/usr/bin/env bash
# wheel만 설치한 빈 Python 환경에서 CPU 인수 테스트를 수행한다.
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WHEEL=${1:-}
if [ -z "$WHEEL" ]; then
  for candidate_dir in "$ROOT/dist" "$ROOT/package"; do
    [ -d "$candidate_dir" ] || continue
    WHEEL=$(find "$candidate_dir" -maxdepth 1 -name 'itemgen_bench-*.whl' -print -quit)
    [ -n "$WHEEL" ] && break
  done
fi
[ -n "$WHEEL" ] && [ -f "$WHEEL" ] || {
  echo "wheel을 찾지 못했다. 사용법: bash scripts/acceptance_test.sh dist/<파일>.whl" >&2
  exit 1
}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/itemgen-acceptance.XXXXXX")
cleanup() { rm -rf -- "$WORK"; }
trap cleanup EXIT

python3 -m venv "$WORK/venv"
"$WORK/venv/bin/python" -m pip install --disable-pip-version-check "$WHEEL"
mkdir -p "$WORK/empty"
cd "$WORK/empty"

"$WORK/venv/bin/itemgen" doctor
"$WORK/venv/bin/itemgen" selftest
"$WORK/venv/bin/itemgen" demo
test -s "$WORK/empty/out/demo/sheet.html"

echo "ACCEPTANCE PASS: wheel 단독 설치, doctor, selftest, demo, HTML 생성"
