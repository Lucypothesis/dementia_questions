"""자동 채점(judge)이 사람 판정과 얼마나 맞는지 잰다.

측정 2의 모든 수치는 VLM judge가 매긴 것이다. 사람과 얼마나 일치하는지 모르면
그 수치를 결과로 쓸 수 없다. 표본을 사람이 보고, 둘을 맞춰본다.

  itemgen calibrate sample --n 120      표본 추출 + 전용 검수 시트 생성
  (시트에서 사람이 판정 -> 'JSON 내려받기')
  itemgen calibrate score --human human_verdicts.json

표본은 judge 판정 양쪽에서 절반씩 뽑는다. 전체의 80%가 '못씀'이라 무작위로 뽑으면
'쓸만함' 쪽 표본이 20%밖에 안 돼 그 방향의 정확도를 못 잰다. 대신 층화 때문에
단순 일치율은 전체 분포를 반영하지 않으므로, 분포와 무관한 민감도·특이도를 같이 본다.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

from . import paths, spec

# 사람 판정 3단계를 judge의 2단계(usable)에 맞춘다.
# 'minor'(수정하면 쓸만)는 '그대로는 못 쓴다'로 본다. judge의 usable 정의와 같다.
HUMAN_TO_BOOL = {"ok": True, "minor": False, "bad": False}


def cell_id(rec: dict) -> str:
    return f"{rec['slot_id']}__{rec['llm']}__{rec['renderer']}"


def all_cells(run: Path) -> list[dict]:
    """judge.json을 칸 단위로 접는다. 칸은 패널이 전부 통과해야 '쓸만'이다."""
    rows = json.loads((run / "judge.json").read_text(encoding="utf-8"))
    by_cell: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cell[cell_id(r)].append(r)
    out = []
    for cid, recs in by_cell.items():
        vs = [r["verdict"].get("usable") for r in recs]
        if any(v is None for v in vs):
            continue
        out.append({"cell": cid, "slot_id": recs[0]["slot_id"], "llm": recs[0]["llm"],
                    "renderer": recs[0]["renderer"], "judge": all(vs), "n_panels": len(recs)})
    return out


# ------------------------------------------------------------------ 표본 추출

def sample(run: Path, n: int, seed: int, spec_dir=None) -> list[dict]:
    rows = json.loads((run / "judge.json").read_text(encoding="utf-8"))
    # 칸(슬롯 x arm x 렌더러) 단위로 묶는다. 사람은 칸을 보고 판정한다.
    by_cell: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cell[cell_id(r)].append(r)

    cells = []
    for cid, recs in by_cell.items():
        vs = [r["verdict"].get("usable") for r in recs]
        if any(v is None for v in vs):
            continue                      # 판정 못 한 칸은 제외
        cells.append({"cell": cid, "slot_id": recs[0]["slot_id"],
                      "llm": recs[0]["llm"], "renderer": recs[0]["renderer"],
                      # 칸 전체가 통과해야 쓸 수 있다. 패널 하나라도 틀리면 못 쓴다.
                      "judge": all(vs), "n_panels": len(recs)})

    rng = random.Random(seed)
    pos = [c for c in cells if c["judge"]]
    neg = [c for c in cells if not c["judge"]]
    half = n // 2
    take_pos = min(half, len(pos))
    take_neg = min(n - take_pos, len(neg))
    picked = rng.sample(pos, take_pos) + rng.sample(neg, take_neg)
    rng.shuffle(picked)
    return picked


# ------------------------------------------------------------------ 채점

def _kappa(a: list[bool], b: list[bool]) -> float:
    """Cohen's kappa. 우연히 맞을 확률을 빼고 남은 일치도."""
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def score(sample_rows: list[dict], human: dict[str, str]) -> dict:
    pairs = []
    for c in sample_rows:
        h = human.get(c["cell"])
        if h is None or h not in HUMAN_TO_BOOL:
            continue
        pairs.append((c["judge"], HUMAN_TO_BOOL[h], c))
    if not pairs:
        raise SystemExit("표본과 겹치는 사람 판정이 없다. 전용 시트에서 판정했는지 확인하라.")

    J = [p[0] for p in pairs]
    H = [p[1] for p in pairs]
    tp = sum(1 for j, h, _ in pairs if j and h)
    fp = sum(1 for j, h, _ in pairs if j and not h)
    fn = sum(1 for j, h, _ in pairs if not j and h)
    tn = sum(1 for j, h, _ in pairs if not j and not h)
    sens = tp / (tp + fn) if (tp + fn) else float("nan")   # 사람이 쓸만하다 한 것 중 judge도 그런 비율
    spec_ = tn / (tn + fp) if (tn + fp) else float("nan")  # 사람이 못쓴다 한 것 중 judge도 그런 비율
    return {"n": len(pairs), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "agree": (tp + tn) / len(pairs), "kappa": _kappa(J, H),
            "sensitivity": sens, "specificity": spec_,
            "mismatch": [c for j, h, c in pairs if j != h]}


def _print(r: dict) -> None:
    print(f"표본 {r['n']}칸 비교\n")
    print("              사람:쓸만  사람:못씀")
    print(f"  judge:쓸만  {r['tp']:>8}  {r['fp']:>8}")
    print(f"  judge:못씀  {r['fn']:>8}  {r['tn']:>8}")
    print(f"\n  단순 일치율  {r['agree']:.1%}   (층화 표본이라 전체 분포와 다름)")
    print(f"  Cohen kappa  {r['kappa']:.3f}   {_kappa_label(r['kappa'])}")
    print(f"  민감도       {r['sensitivity']:.1%}   사람이 쓸만하다 한 것을 judge가 잡아낸 비율")
    print(f"  특이도       {r['specificity']:.1%}   사람이 못쓴다 한 것을 judge가 걸러낸 비율")
    if r["mismatch"]:
        print(f"\n  불일치 {len(r['mismatch'])}칸 (앞 10개)")
        for c in r["mismatch"][:10]:
            print(f"    {c['cell']}  judge={'쓸만' if c['judge'] else '못씀'}")


def _kappa_label(k: float) -> str:
    for t, s in [(0.81, "거의 완전 일치"), (0.61, "상당한 일치"), (0.41, "중간 일치"),
                 (0.21, "약한 일치"), (0.0, "거의 우연 수준")]:
        if k >= t:
            return s
    return "우연보다 나쁨"


# ------------------------------------------------------------------ CLI

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="judge 캘리브레이션")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="표본 추출 + 전용 검수 시트")
    s.add_argument("--run", default="main")
    s.add_argument("--n", type=int, default=120)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--out", default=None)
    s.add_argument("--spec-dir", default=None)

    c = sub.add_parser("score", help="사람 판정과 대조")
    c.add_argument("--run", default="main")
    c.add_argument("--human", required=True, help="시트에서 내려받은 human_verdicts.json")
    c.add_argument("--out", default=None)

    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    run = paths.out_dir(args.out) / args.run

    if args.cmd == "sample":
        picked = sample(run, args.n, args.seed, args.spec_dir)
        path = run / "calibration_sample.json"
        path.write_text(json.dumps(picked, ensure_ascii=False, indent=2), encoding="utf-8")
        dist = Counter((c["llm"], c["renderer"]) for c in picked)
        pos = sum(1 for c in picked if c["judge"])
        print(f"표본 {len(picked)}칸 (judge 쓸만 {pos} / 못씀 {len(picked)-pos})")
        print(f"  조합 {len(dist)}종, 칸당 최소 {min(dist.values())} 최대 {max(dist.values())}")
        print(f"  -> {path}")

        from . import sheet
        slots = spec.load_slots(args.spec_dir)["slots"]
        only = {c["cell"] for c in picked}
        out = sheet.build(run, slots, embed=False, only=only,
                          filename="sheet_calibration.html",
                          title_suffix="캘리브레이션 표본")
        print(f"  -> {out}")
        print("\n시트에서 칸마다 판정한 뒤 'JSON 내려받기'를 누르고,")
        print("  itemgen calibrate score --human <내려받은 파일>")
        return

    raw = json.loads(Path(args.human).read_text(encoding="utf-8"))
    human = {r["cell"]: r["verdict"] for r in raw}

    # 표본 파일에 얽매이지 않는다. 사람이 판정한 칸이 무엇이든 judge와 맞춰본다.
    # 시트를 필터해서 본 경우(예: 렌더러 하나만) 그 범위가 곧 비교 대상이 된다.
    cells = all_cells(run)
    picked = [c for c in cells if c["cell"] in human]
    if not picked:
        raise SystemExit("사람 판정과 겹치는 칸이 없다.")
    cov = Counter((c["llm"], c["renderer"]) for c in picked)
    print(f"비교 대상 {len(picked)}칸 — 조합 {len(cov)}종")
    for k, n in sorted(cov.items()):
        print(f"  {k[0]:22} x {k[1]:26} {n}")
    print()
    _print(score(picked, human))
