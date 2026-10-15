#!/usr/bin/env bash
# 한 화면 상태 요약. watch로 감싸면 실시간 대시보드가 된다.
#   bash status.sh            # 한 번
#   watch -n 10 bash status.sh  # 10초마다 갱신
cd "$(dirname "$0")"
RUN=${1:-main}
L=out/$RUN/logs

echo "─── 진행 ─────────────────────────────────────────────"
tail -6 "$L/run_all.log" 2>/dev/null || echo "  로그 없음"

echo
echo "─── 지금 도는 것 ─────────────────────────────────────"
ps -eo etime,cmd --sort=start_time | grep -E "vllm serve|pipeline\.py" | grep -v grep \
  | sed 's/^/  /' | cut -c1-110 || echo "  (없음)"

echo
echo "─── GPU ──────────────────────────────────────────────"
nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu \
  --format=csv,noheader | sed 's/^/  /'

echo
echo "─── 모델 다운로드 ────────────────────────────────────"
du -sh /workspace/hf/hub/models--* 2>/dev/null | sed 's|/workspace/hf/hub/models--|  |' \
  | sort -k2 || echo "  (아직 없음)"
df -h /workspace | tail -1 | awk '{print "  디스크 여유: " $4}'

echo
echo "─── 산출물 ───────────────────────────────────────────"
for d in out/$RUN/items/*/; do
  [ -d "$d" ] || continue
  printf "  문항 %-22s %s개\n" "$(basename "$d")" "$(ls "$d"/*.json 2>/dev/null | wc -l)"
done
for d in out/$RUN/images/*/*/; do
  [ -d "$d" ] || continue
  printf "  이미지 %-21s %s장\n" "$(basename "$(dirname "$d")")/$(basename "$d")" \
    "$(ls "$d"/*.png 2>/dev/null | wc -l)"
done
