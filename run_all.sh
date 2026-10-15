#!/usr/bin/env bash
# 파이프라인 전 구간을 무인으로 끝까지 돌린다.
#
#   bash run_all.sh            # 전부
#   bash run_all.sh --dry-run  # 아무것도 실행하지 않고 점검만
#
# 설계 원칙
#   - 한 단계가 실패해도 멈추지 않는다. 실패를 기록하고 다음으로 넘어간다.
#   - 모든 출력은 logs/ 아래에 남는다. 돌아와서 무엇이 어디서 깨졌는지 본다.
#   - 이미 만든 결과는 건너뛴다. 다시 돌려도 안전하다.

set -uo pipefail
cd "$(dirname "$0")"

PY=${PY:-python3}
ITEMGEN=${ITEMGEN:-itemgen}
VLLM=/workspace/vllm-venv/bin/vllm
RUN=main
# 쓸 GPU 후보. 여러 개를 주면 그중 먼저 비는 카드를 잡는다. 쉼표·공백 둘 다 받는다.
#   GPUS="0 1" bash run_all.sh      # 0번이나 1번 중 비는 쪽
#   GPU=3 bash run_all.sh           # 3번만
GPUS=$(echo "${GPUS:-${GPU:-2}}" | tr ',' ' ')
GPU=""                  # 실제로 고른 카드. pick_gpu가 채운다.
# 카드가 빌 때까지 기다리는 최대 시간(초). 기본 2시간.
GPU_WAIT=${GPU_WAIT:-7200}
LOGS=out/$RUN/logs
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

# /data가 100% 차 있다(21GB). 캐시를 /workspace로 돌린다.
# HF_HUB_CACHE가 환경에 이미 /data/hf/hub로 박혀 있고 이게 HF_HOME보다 우선한다.
# HF_HOME만 바꾸면 조용히 /data로 받다가 공간 부족으로 터진다. 둘 다 덮어쓴다.
export HF_HOME=/workspace/hf
export HF_HUB_CACHE=/workspace/hf/hub
export HF_DATASETS_CACHE=/workspace/hf/datasets
export HF_HUB_ENABLE_HF_TRANSFER=0
export VLLM_API_KEY=dummy
export TOKENIZERS_PARALLELISM=false

mkdir -p "$LOGS"
MAIN_LOG=$LOGS/run_all.log
FAILED=()

# 두 번 띄우면 같은 파일에 동시에 쓰고 GPU도 두 배로 먹는다. 한 번에 하나만 돌게 막는다.
LOCK=out/$RUN/.run_all.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "이미 run_all.sh가 돌고 있다. 중복 실행을 막았다."
  echo "  진행 보기: tail -f $MAIN_LOG"
  echo "  멈추려면:  pkill -f run_all.sh"
  exit 1
fi

say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$MAIN_LOG"; }
fail() { FAILED+=("$1"); say "  !! 실패: $1"; }

SLOT_N=$("$PY" -c "import json;print(len(json.load(open('spec/slots.json'))['slots']))" 2>/dev/null || echo 50)

# 이 arm의 문항이 이미 다 있나. 있으면 vLLM을 띄울 이유가 없다.
# 안 그러면 끝난 arm 때문에 GPU를 2시간 기다리다 뒤 단계가 통째로 밀린다.
arm_done() {   # arm_done <arm 이름>
  local n
  n=$(ls "out/$RUN/items/$1"/*.json 2>/dev/null | wc -l)
  [ "$n" -ge "$SLOT_N" ]
}

run() {   # run <이름> <로그파일> <명령...>
  local label=$1 log=$2; shift 2
  say "▶ $label"
  if [ $DRY = 1 ]; then say "  (dry-run) $*"; return 0; fi
  if "$@" >>"$log" 2>&1; then say "  완료: $label"; else fail "$label  (로그: $log)"; fi
}

# vLLM 서버를 띄우고 준비될 때까지 기다린다. 못 뜨면 1을 돌려준다.
serve_wait() {  # serve_wait <모델> <포트> <로그> [추가인자...]
  local model=$1 port=$2 log=$3; shift 3
  # 27B를 bf16으로 올리면 가중치만 ~54GB다. 후보 중 그만큼 빈 카드를 고른다.
  pick_gpu 60 || { say "  GPU 여유 부족으로 기동 포기"; return 1; }
  # --gpu-memory-utilization은 '전체' 메모리 대비 비율이지 '여유' 대비가 아니다.
  # 게다가 vLLM이 실제로 메모리를 확인하는 건 기동 수십 초 뒤다. 그 사이에 앞 단계가
  # 메모리를 늦게 반환하거나 남의 작업이 들어오면 여유가 줄어 그대로 죽는다.
  # (실제로 79GB를 보고 0.90을 줬는데 45초 뒤 62GB가 되어 Mistral이 죽었다.)
  # 안전분을 10GB로 크게 잡고 상한도 0.85로 낮춘다. 24~27B 가중치가 48~54GB라
  # 0.85(=67GB)면 KV 캐시까지 충분하다.
  local total_gb util
  total_gb=$(( $(nvidia-smi -i "$GPU" --query-gpu=memory.total --format=csv,noheader,nounits) / 1024 ))
  util=$(awk -v f="$(free_gb "$GPU")" -v t="$total_gb" \
         'BEGIN{u=(f-10)/t; if(u>0.85)u=0.85; printf "%.2f", u}')
  say "  vLLM 기동: $model (포트 $port, GPU $GPU, util $util) ${*:+추가인자: $*}"
  # 인자를 함부로 늘리지 않는다. vllm 0.19.1에는 --disable-log-requests가 없어서
  # 넣었다가 기동이 20초 만에 죽었다. 버전마다 있는 인자가 다르다.
  CUDA_VISIBLE_DEVICES=$GPU "$VLLM" serve "$model" \
      --max-model-len 32768 --gpu-memory-utilization "$util" \
      --port "$port" "$@" >>"$log" 2>&1 &
  VLLM_PID=$!
  # 가중치 내려받기까지 포함해 최대 90분 기다린다 (55GB 다운로드가 있을 수 있다).
  for i in $(seq 1 540); do
    if ! kill -0 $VLLM_PID 2>/dev/null; then
      say "  vLLM 프로세스가 죽었다 (로그: $log)"; return 1
    fi
    # VLLM_API_KEY는 서버가 '요구하는' 키다. 헤더 없이 물어보면 서버가 다 떠 있어도
    # 401만 돌아와 영원히 준비 안 된 것처럼 보인다. 실제로 그렇게 두 arm을 날렸다.
    if "$PY" - "$port" "${VLLM_API_KEY:-}" <<'EOF' 2>/dev/null
import sys, urllib.request
req = urllib.request.Request(f"http://127.0.0.1:{sys.argv[1]}/v1/models")
if len(sys.argv) > 2 and sys.argv[2]:
    req.add_header("Authorization", f"Bearer {sys.argv[2]}")
urllib.request.urlopen(req, timeout=5)
EOF
    then say "  vLLM 준비됨 ($((i*10))초)"; return 0; fi
    sleep 10
  done
  say "  vLLM 90분 안에 안 떴다"; return 1
}

free_gb() {   # free_gb <카드번호>
  local used total
  used=$(nvidia-smi -i "$1" --query-gpu=memory.used --format=csv,noheader,nounits)
  total=$(nvidia-smi -i "$1" --query-gpu=memory.total --format=csv,noheader,nounits)
  echo $(( (total - used) / 1024 ))
}

# 후보 중 need_gb 이상 빈 카드를 고른다. 없으면 기다린다. 고른 번호를 GPU에 넣는다.
# 다른 사람 작업이 들어왔다 나가므로 단계마다 다시 고른다.
pick_gpu() {   # pick_gpu <필요GB> [최대대기초]
  local need=$1 limit=${2:-$GPU_WAIT} waited=0 cand f best bestfree
  while [ "$waited" -lt "$limit" ]; do
    best=""; bestfree=0
    for cand in $GPUS; do
      f=$(free_gb "$cand")
      if [ "$f" -gt "$bestfree" ]; then bestfree=$f; best=$cand; fi
    done
    if [ "$bestfree" -ge "$need" ]; then
      GPU=$best
      say "  GPU $GPU 선택 (여유 ${bestfree}GB / 필요 ${need}GB)"
      return 0
    fi
    [ $((waited % 300)) = 0 ] && say "  후보 [$GPUS] 최대 여유 ${bestfree}GB < ${need}GB 필요 — 대기 $((waited/60))분"
    sleep 20; waited=$((waited + 20))
  done
  say "  후보 [$GPUS]가 $((limit/60))분 안에 안 비었다"
  return 1
}

# 이미 고른 카드가 다시 빌 때까지 기다린다 (vLLM을 내린 직후 메모리 반환 대기용).
wait_gpu_free() {   # wait_gpu_free <필요GB> <최대대기초>
  local need=$1 limit=${2:-300} waited=0 free
  [ -n "$GPU" ] || return 0
  while [ "$waited" -lt "$limit" ]; do
    free=$(free_gb "$GPU")
    [ "$free" -ge "$need" ] && { say "  GPU $GPU 여유 ${free}GB — 진행"; return 0; }
    [ $((waited % 60)) = 0 ] && say "  GPU $GPU 여유 ${free}GB (${need}GB 필요) — 대기 ${waited}초"
    sleep 10; waited=$((waited + 10))
  done
  say "  GPU $GPU 가 ${limit}초 안에 안 비었다"
  return 1
}

stop_vllm() {
  [ -n "${VLLM_PID:-}" ] || return 0
  kill $VLLM_PID 2>/dev/null
  for _ in $(seq 1 30); do kill -0 $VLLM_PID 2>/dev/null || break; sleep 2; done
  kill -9 $VLLM_PID 2>/dev/null
  VLLM_PID=
  # vLLM은 'VLLM::EngineCore'라는 별도 이름의 자식을 남긴다. 부모만 죽이면
  # 이 녀석이 GPU 메모리 50GB를 계속 물고 있어 다음 모델이 못 뜬다.
  pkill -9 -f "VLLM::EngineCore" 2>/dev/null
  pkill -9 -f "multiprocessing.resource_tracker" 2>/dev/null
  wait_gpu_free 60 180 || say "  !! GPU가 덜 비었다. 다음 단계가 실패할 수 있다."
  say "  vLLM 내림"
}
trap stop_vllm EXIT

# ---------------------------------------------------------------- 사전 점검
say "=============================================================="
say "run=$RUN  GPU=$GPU  HF_HOME=$HF_HOME  dry-run=$DRY"
say "=============================================================="
AVAIL=$(df --output=avail -BG /workspace | tail -1 | tr -dc '0-9')
say "디스크 여유: ${AVAIL}GB (최소 250GB 필요)"
[ "$AVAIL" -lt 250 ] && say "  !! 공간 부족. 다운로드가 실패할 수 있다."
if [ -z "${OPENAI_API_KEY:-}" ]; then
  say "  !! OPENAI_API_KEY 없음 — 1단계(gpt6_sol)는 건너뛴다"
else
  say "OPENAI_API_KEY 설정됨 (${#OPENAI_API_KEY}자)"
fi
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader | tee -a "$MAIN_LOG"

# -------------------------------------------------------- 1 상용 arm (보험)
# 이미 돌렸으면 즉시 건너뛴다. 안 돌렸으면 여기서 채운다.
if [ -n "${OPENAI_API_KEY:-}" ]; then
  run "1 gpt6_sol gen+check" "$LOGS/gen_gpt6_sol.log" \
      "$ITEMGEN" run --run $RUN --stages gen,check --llms gpt6_sol
fi

# ------------------------------------------------------------ 2 Qwen3.8-27B
say "▶ 2 qwen38_27b"
if arm_done qwen38_27b; then
  say "  건너뜀: 문항 ${SLOT_N}/${SLOT_N} 이미 있음 (vLLM 안 띄움)"
elif [ $DRY = 1 ]; then
  say "  (dry-run) vllm serve Qwen/Qwen3.8-27B -> gen,check"
elif serve_wait "Qwen/Qwen3.8-27B" 8001 "$LOGS/vllm_qwen38.log" \
       --reasoning-parser qwen3; then
  run "2 qwen38_27b gen+check" "$LOGS/gen_qwen38.log" \
      "$ITEMGEN" run --run $RUN --stages gen,check --llms qwen38_27b
  stop_vllm
else
  fail "2 qwen38_27b vLLM 기동 (로그: $LOGS/vllm_qwen38.log)"
  stop_vllm
fi

# ---------------------------------------------------- 3 Mistral-Small-24B
say "▶ 3 mistral_small_24b"
if arm_done mistral_small_24b; then
  say "  건너뜀: 문항 ${SLOT_N}/${SLOT_N} 이미 있음 (vLLM 안 띄움)"
elif [ $DRY = 1 ]; then
  say "  (dry-run) vllm serve mistralai/Mistral-Small-24B-Instruct-2501 -> gen,check"
elif serve_wait "mistralai/Mistral-Small-24B-Instruct-2501" 8003 "$LOGS/vllm_mistral.log"; then
  run "3 mistral_small_24b gen+check" "$LOGS/gen_mistral.log" \
      "$ITEMGEN" run --run $RUN --stages gen,check --llms mistral_small_24b
  stop_vllm
else
  fail "3 mistral_small_24b vLLM 기동 (로그: $LOGS/vllm_mistral.log)"
  stop_vllm
fi

# ------------------------------------------------------------------ 4 비교
run "4 check 전체 비교" "$LOGS/check_all.log" \
    "$ITEMGEN" run --run $RUN --stages check

# ------------------------------------------------------------------ 5 렌더
# 캐시에 있거나 작은 것부터. 앞의 것이 깨져도 뒤가 돈다.
for R in z_image_turbo flux2_klein_4b qwen_image_2512_lightning qwen_image_2512; do
  if [ $DRY = 1 ]; then say "  (dry-run) render $R"; continue; fi
  say "▶ 5 render $R"
  # LLM 단계가 끝날 즈음엔 카드 상황이 바뀌어 있다. 렌더마다 다시 고른다.
  # 가장 큰 Qwen-Image-2512(20B)가 bf16 48GB+ 라 그 기준으로 잡는다.
  pick_gpu 50 || { fail "5 render $R  GPU 확보 실패"; continue; }
  if CUDA_VISIBLE_DEVICES=$GPU "$ITEMGEN" run --run $RUN --stages render \
        --renderers "$R" >>"$LOGS/render_$R.log" 2>&1; then
    say "  완료: render $R"
  else
    fail "5 render $R  (로그: $LOGS/render_$R.log)"
  fi
done

# ------------------------------------------------------- 6 자동채점 + 시트
run "6 judge" "$LOGS/judge.log" "$ITEMGEN" run --run $RUN --stages judge
run "6 sheet" "$LOGS/sheet.log" "$ITEMGEN" run --run $RUN --stages sheet

# ------------------------------------------------------------------- 요약
say "=============================================================="
if [ ${#FAILED[@]} -eq 0 ]; then
  say "전부 성공"
else
  say "실패 ${#FAILED[@]}건:"
  for f in "${FAILED[@]}"; do say "  - $f"; done
fi
say "결과: out/$RUN/   시트: out/$RUN/sheet.html   로그: $LOGS/"
say "=============================================================="
