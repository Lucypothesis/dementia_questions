"""실시간 진행 감시기. 돌고 있는 파이프라인을 건드리지 않고 산출물 파일만 센다.

run_all.log는 단계가 끝나야 한 줄 찍는다. 그 사이가 깜깜하므로 여기서는
문항·이미지가 하나씩 쌓이는 것을 직접 세서 속도와 남은 시간을 보여준다.

  python3 src/watch.py                 # 5초마다 갱신
  python3 src/watch.py --interval 15
  python3 src/watch.py --once          # 한 번만 출력 (watch로 감쌀 때)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from . import paths
from . import spec
BAR = 24


def human(sec: float) -> str:
    if sec < 0 or sec != sec or sec == float("inf"):
        return "–"
    sec = int(sec)
    if sec < 60:
        return f"{sec}초"
    if sec < 3600:
        return f"{sec//60}분"
    return f"{sec//3600}시간 {(sec%3600)//60}분"


def bar(done: int, total: int) -> str:
    if not total:
        return " " * BAR
    f = int(BAR * done / total)
    return "█" * f + "░" * (BAR - f)


def scan(run: Path) -> dict:
    """산출물을 센다. 파일 mtime으로 최근 속도를 추정한다."""
    out = {"items": {}, "images": {}, "stage": "", "slots": 0}
    rj = run / "run.json"
    if rj.exists():
        out["slots"] = len(json.loads(rj.read_text(encoding="utf-8")).get("slots", []))

    log = run / "logs" / "run_all.log"
    if log.exists():
        lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.startswith("[")]
        out["stage"] = lines[-1] if lines else ""

    for d in sorted((run / "items").glob("*")):
        if not d.is_dir():
            continue
        js = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
        out["items"][d.name] = {
            "ok": len(js),
            "raw": len(list(d.glob("*.raw.txt"))),
            "err": len(list(d.glob("*.error.txt"))),
            "times": [p.stat().st_mtime for p in js],
            "last": js[-1].name if js else None,
        }
    for d in sorted((run / "images").glob("*/*")):
        if not d.is_dir():
            continue
        pngs = sorted(d.glob("*.png"), key=lambda p: p.stat().st_mtime)
        out["images"][f"{d.parent.name}/{d.name}"] = {
            "n": len(pngs),
            "times": [p.stat().st_mtime for p in pngs],
            "last": pngs[-1].name if pngs else None,
        }
    return out


def rate_eta(times: list[float], done: int, total: int, window: int = 600):
    """최근 window초 안의 생성 속도로 남은 시간을 추정한다."""
    now = time.time()
    recent = [t for t in times if now - t <= window]
    if len(recent) >= 2:
        span = max(recent) - min(recent)
        per = span / (len(recent) - 1) if span > 0 else 0
    elif len(times) >= 2:
        per = (max(times) - min(times)) / (len(times) - 1)
    else:
        return None, None, len(recent)
    if per <= 0:
        return None, None, len(recent)
    return per, per * max(total - done, 0), len(recent)


def render(run: Path, total_panels: int) -> str:
    s = scan(run)
    n_slots = s["slots"] or 50
    now = time.strftime("%H:%M:%S")
    L = [f"[{now}]  {s['stage']}", ""]

    if s["items"]:
        L.append("── 문항 생성 ──────────────────────────────────────────")
        for arm, d in s["items"].items():
            done = d["ok"] + d["raw"] + d["err"]
            per, eta, rec = rate_eta(d["times"], d["ok"], n_slots)
            line = (f"  {arm:20} {bar(done, n_slots)} {done:>3}/{n_slots}"
                    f"   성공 {d['ok']:>2}")
            if d["raw"]:
                line += f" · 파싱실패 {d['raw']}"
            if d["err"]:
                line += f" · 호출실패 {d['err']}"
            L.append(line)
            if per and done < n_slots:
                age = time.time() - max(d["times"]) if d["times"] else 0
                L.append(f"  {'':20} 개당 {human(per)} · 남은 {n_slots-done}개 ≈ {human(eta)}"
                         f" · 마지막 {d['last']} ({human(age)} 전)")
        L.append("")

    if s["images"]:
        L.append("── 이미지 렌더 ────────────────────────────────────────")
        for key, d in s["images"].items():
            per, eta, rec = rate_eta(d["times"], d["n"], total_panels)
            line = f"  {key:28} {bar(d['n'], total_panels)} {d['n']:>3}/{total_panels}"
            L.append(line)
            if per and d["n"] < total_panels:
                age = time.time() - max(d["times"]) if d["times"] else 0
                L.append(f"  {'':28} 장당 {human(per)} · 남은 ≈ {human(eta)}"
                         f" · 마지막 {human(age)} 전")
        L.append("")

    for f, label in [("check.json", "측정1 check"), ("panels.json", "패널 색인"),
                     ("judge.json", "측정2 judge"), ("sheet.html", "검수 시트")]:
        p = run / f
        L.append(f"  {label:12} {'있음 ' + time.strftime('%m-%d %H:%M', time.localtime(p.stat().st_mtime)) if p.exists() else '아직 없음'}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="main")
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    run = paths.out_dir(args.out) / args.run
    if not run.exists():
        raise SystemExit(f"{run} 가 없다")

    # 패널 총수는 슬롯 구조가 정한다. spec에서 직접 센다.
    total_panels = sum(
        sum(p.get("n", 1) if p.get("kind") == "option" else 1 for p in s["panels"])
        for s in spec.load_slots()["slots"])

    if args.once:
        print(render(run, total_panels))
        return
    try:
        while True:
            print("\033[2J\033[H" + render(run, total_panels), flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
