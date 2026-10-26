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

# 사람이 터미널에서 실행했으면 성공 결과를 곧바로 브라우저에 제공한다.
# CI 같은 비대화형 실행에서는 자동으로 종료한다. SERVE=0으로 끄거나,
# SERVE=1로 비대화형 환경에서도 강제할 수 있다.
SERVE=${SERVE:-auto}
SERVE_PORT=${SERVE_PORT:-8012}
SHOULD_SERVE=0
case "$SERVE" in
  1|true|yes) SHOULD_SERVE=1 ;;
  0|false|no) SHOULD_SERVE=0 ;;
  auto) [ -t 1 ] && SHOULD_SERVE=1 ;;
  *) echo "SERVE는 auto, 1, 0 중 하나여야 합니다: $SERVE" >&2; exit 2 ;;
esac

if [ "$SHOULD_SERVE" -eq 1 ]; then
  echo "검수 HTML 서빙: http://127.0.0.1:$SERVE_PORT/sheet.html"
  echo "종료하려면 Ctrl+C를 누르세요."
  exec "${ITEMGEN:-itemgen}" serve --run "$RUN" --port "$SERVE_PORT"
fi
