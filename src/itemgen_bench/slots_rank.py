"""슬롯을 생성 품질로 줄 세운다. 쓸 만한 유형만 골라 데이터셋을 구성할 때 쓴다.

어떤 문항 유형은 어떤 모델·렌더러로도 잘 안 나온다(회전·전개도·대칭 같은 것).
그런 유형을 끌고 가면 전체 수율이 떨어지고 검수 비용만 늘어난다. 여기서
슬롯별 성적을 보고 자를 지점을 정한다.

기본은 비교용 arm을 빼고 센다. 상용 모델은 오픈 모델과의 대조군일 뿐이고,
readonly 보존 arm은 하네스가 불리했던 기록이므로 품질 판단에 넣으면 왜곡된다.

  itemgen slots --run main
  itemgen slots --run main --exclude gpt6_sol,qwen38_27b_noparser --cut 0.5
  itemgen slots --run main --cut 0.4 --write spec/slots_selected.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from . import paths, spec

# 품질 판단에서 기본으로 빼는 arm. 쉼표로 덮어쓸 수 있다.
DEFAULT_EXCLUDE = ["gpt6_sol", "qwen38_27b_noparser"]


def collect(run: Path, exclude: list[str], spec_dir=None,
            human: str | None = None, renderers: list[str] | None = None,
            accept: tuple[str, ...] = ("ok",)):
    """칸별 성패를 모은다.

    human을 주면 사람 판정을 정답으로 쓴다. judge는 캘리브레이션에서 민감도 46%로
    나왔다 — 사람이 쓸만하다 한 것의 절반을 놓친다. 체리피킹처럼 '무엇을 남길지'를
    정하는 일에는 사람 판정이 맞다.
    """
    slots = {s["id"]: s for s in spec.load_slots(spec_dir)["slots"]}
    cells: dict[tuple, list] = defaultdict(list)

    if human:
        for r in json.loads(Path(human).read_text(encoding="utf-8")):
            if r["llm"] in exclude:
                continue
            if renderers and r["renderer"] not in renderers:
                continue
            # 사람 판정은 칸 하나당 하나다. 패널 단위가 아니므로 그대로 담는다.
            cells[(r["slot_id"], r["llm"], r["renderer"])].append(r["verdict"] in accept)
    else:
        for r in json.loads((run / "judge.json").read_text(encoding="utf-8")):
            if r["llm"] in exclude:
                continue
            if renderers and r["renderer"] not in renderers:
                continue
            v = r["verdict"].get("usable")
            if v is None:
                continue
            cells[(r["slot_id"], r["llm"], r["renderer"])].append(v)

    per_slot: dict[str, dict] = {}
    for (sid, llm, ren), vs in cells.items():
        d = per_slot.setdefault(sid, {"panel_ok": 0, "panel_n": 0,
                                      "item_ok": 0, "item_n": 0, "by_ren": defaultdict(lambda: [0, 0])})
        d["panel_ok"] += sum(vs); d["panel_n"] += len(vs)
        full = all(vs)
        d["item_ok"] += full; d["item_n"] += 1
        d["by_ren"][ren][0] += full; d["by_ren"][ren][1] += 1

    out = []
    for sid, d in per_slot.items():
        s = slots.get(sid, {})
        best = max(((r, a / b) for r, (a, b) in d["by_ren"].items() if b), key=lambda x: x[1],
                   default=("-", 0.0))
        out.append({
            "slot_id": sid, "domain": s.get("domain", "?"), "subtype": s.get("subtype", "?"),
            "item_rate": d["item_ok"] / d["item_n"] if d["item_n"] else 0.0,
            "panel_rate": d["panel_ok"] / d["panel_n"] if d["panel_n"] else 0.0,
            "item_ok": d["item_ok"], "item_n": d["item_n"],
            "best_renderer": best[0], "best_rate": best[1],
        })
    out.sort(key=lambda x: (-x["item_rate"], -x["panel_rate"], x["slot_id"]))
    return out


def _bar(v: float, w: int = 14) -> str:
    return "█" * round(w * v) + "·" * (w - round(w * v))


def report(ranked: list[dict], cut: float | None) -> None:
    print(f"슬롯 {len(ranked)}개 · 문항 단위 = 그 슬롯의 패널이 전부 통과한 비율\n")
    print(f"  {'슬롯':10}{'영역':8}{'유형':18}{'문항':>7}  {'':14} {'패널':>6}  최고 렌더러")
    for r in ranked:
        mark = " " if cut is None else ("+" if r["item_rate"] >= cut else "-")
        print(f"{mark} {r['slot_id']:10}{r['domain']:8}{r['subtype'][:16]:18}"
              f"{r['item_ok']}/{r['item_n']:<4}{_bar(r['item_rate'])} "
              f"{r['panel_rate']:>5.0%}  {r['best_renderer'][:18]} {r['best_rate']:.0%}")

    print("\n=== 자를 지점 후보 ===")
    print(f"  {'기준':>8}{'남는 슬롯':>10}{'영역별 분포':>30}")
    for c in [0.0, 0.1, 0.2, 0.25, 1 / 3, 0.5, 0.75]:
        keep = [r for r in ranked if r["item_rate"] >= c and c > 0 or (c == 0 and r["item_rate"] > 0)]
        if c == 0:
            keep = [r for r in ranked if r["item_rate"] > 0]
            label = ">0"
        else:
            label = f"{c:.0%}"
        dom = defaultdict(int)
        for r in keep:
            dom[r["domain"]] += 1
        dist = " ".join(f"{k[:2]}{v}" for k, v in sorted(dom.items()))
        print(f"  {label:>8}{len(keep):>10}{dist:>30}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="슬롯을 생성 품질로 줄 세운다")
    ap.add_argument("--run", default="main")
    ap.add_argument("--exclude", default=",".join(DEFAULT_EXCLUDE),
                    help="품질 집계에서 뺄 arm (쉼표 구분)")
    ap.add_argument("--cut", type=float, default=None, help="이 비율 이상만 +로 표시")
    ap.add_argument("--human", default=None,
                    help="사람 판정 JSON. 주면 judge 대신 이걸 정답으로 쓴다")
    ap.add_argument("--renderers", default=None, help="이 렌더러만 (쉼표 구분)")
    ap.add_argument("--accept", default="ok",
                    help="사람 판정 중 합격으로 칠 것 (ok / ok,minor)")
    ap.add_argument("--write", default=None, help="자른 결과를 slots.json 형식으로 저장")
    ap.add_argument("--out", default=None)
    ap.add_argument("--spec-dir", default=None)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    run = paths.out_dir(args.out) / args.run
    exclude = [x.strip() for x in args.exclude.split(",") if x.strip()]
    print(f"제외한 arm: {exclude or '(없음)'}\n")
    rens = [x.strip() for x in args.renderers.split(",")] if args.renderers else None
    accept = tuple(x.strip() for x in args.accept.split(","))
    if args.human:
        print(f"정답: 사람 판정 ({Path(args.human).name}) · 합격 기준 {accept}")
    else:
        print("정답: VLM judge (캘리브레이션 kappa 0.257 — 사람보다 엄격함)")
    if rens:
        print(f"렌더러: {rens}")
    print()
    ranked = collect(run, exclude, args.spec_dir, args.human, rens, accept)
    report(ranked, args.cut)

    if args.write:
        if args.cut is None:
            raise SystemExit("--write 를 쓰려면 --cut 도 줘야 한다")
        keep = {r["slot_id"] for r in ranked if r["item_rate"] >= args.cut}
        src = spec.load_slots(args.spec_dir)
        src["slots"] = [s for s in src["slots"] if s["id"] in keep]
        src["note"] = (src.get("note", "") +
                       f" | 생성 품질 {args.cut:.0%} 이상만 남김 "
                       f"({len(keep)}/{len(ranked)}슬롯, 제외 arm: {','.join(exclude)})")
        Path(args.write).write_text(json.dumps(src, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
        print(f"\n{len(keep)}슬롯 -> {args.write}")
