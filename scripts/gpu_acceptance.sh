#!/usr/bin/env bash
# Qwen 로컬 모델로 문항 1개를 실제 생성하고 compose 렌더·검수 시트까지 확인한다.
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

RUN=${RUN:-gpu_acceptance}
SLOT_IDS=${SLOT_IDS:-MEM_01}
LLMS=qwen38_27b
RENDERERS=compose
export RUN SLOT_IDS LLMS RENDERERS

bash "$ROOT/run_all.sh"

test -s "$ROOT/out/$RUN/items/qwen38_27b/${SLOT_IDS%%,*}.json"
test -s "$ROOT/out/$RUN/check.json"
test -s "$ROOT/out/$RUN/panels.json"
test -s "$ROOT/out/$RUN/sheet.html"

echo "GPU ACCEPTANCE PASS: 실제 LLM 생성 -> 검증 -> compose 렌더 -> 검수 HTML"
