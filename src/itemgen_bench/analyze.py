"""judge 결과를 실패 유형별로 집계한다.

judge는 패널마다 네 플래그(objects/count/layout/no_text)와 한 문장짜리 이유를 남긴다.
플래그는 "무엇이 틀렸나"를 거칠게만 알려주므로, 이유 문장을 규칙으로 분류해
"어떻게 틀렸나"까지 본다. 한 문장이 여러 유형에 걸릴 수 있어 다중 라벨이다.

  itemgen analyze --run main
  itemgen analyze --run main --by domain
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from . import paths, spec

# 이유 문장을 유형으로 가르는 규칙. 순서는 의미 없다(다중 라벨).
# test_012에서 사람이 손으로 분류했던 범주를 자동화한 것이다.
RULES: list[tuple[str, str]] = [
    ("개수",      r"\d+\s*(개|마리|장|송이|줄|칸)(가|이|는|만|뿐)?\s*(아니|아닌|대신)|개수|수가 |몇 개|더 많|더 적|뿐이"),
    ("배치·격자",  r"행|열|격자|배치|위치|왼쪽|오른쪽|위쪽|아래쪽|가운데|순서|나란|간격|겹치|붙어"),
    ("사물 종류",  r"대신|없고|없으며|없습니다|아닌 |다른 사물|엉뚱|포함되지|빠져|누락"),
    ("색",        r"색|빨강|빨간|파랑|파란|초록|노랑|노란|검정|검은|흰|보라|주황|회색"),
    ("방향·회전",  r"방향|회전|돌아|꼭짓점|뒤집|반전|기울|향하|좌우|상하"),
    ("크기",      r"크기|크게|작게|더 크|더 작|비율|같은 크기"),
    ("중복·추가",  r"하나 더|더 있|추가|중복|두 개 더|여러 개가"),
    ("그림체",    r"윤곽선|두껍|얇|입체|3D|삼차원|원근|그림자|질감|사실적|스타일|배경"),
    ("글자 포함",  r"글자|문자|숫자가|텍스트|라벨|워터마크"),
]
COMPILED = [(name, re.compile(pat)) for name, pat in RULES]
FLAGS = ["objects_ok", "count_ok", "layout_ok", "no_text"]


def classify(why: str) -> list[str]:
    hits = [name for name, rx in COMPILED if rx.search(why or "")]
    return hits or ["기타"]


def load(run: Path, spec_dir=None):
    rows = json.loads((run / "judge.json").read_text(encoding="utf-8"))
    slots = {s["id"]: s for s in spec.load_slots(spec_dir)["slots"]}
    for r in rows:
        r["domain"] = slots.get(r["slot_id"], {}).get("domain", "?")
        r["subtype"] = slots.get(r["slot_id"], {}).get("subtype", "?")
    return rows


def _bar(n: int, top: int, width: int = 28) -> str:
    return "█" * max(1, round(width * n / top)) if n else ""


def report(rows, by: str = "renderer") -> None:
    bad = [r for r in rows if r["verdict"].get("usable") is False]
    ok = sum(1 for r in rows if r["verdict"].get("usable") is True)
    print(f"패널 {len(rows)}건 · 사용가능 {ok} ({ok/len(rows):.0%}) · 못씀 {len(bad)}")

    print("\n=== 어떤 검사에서 떨어졌나 (못씀 기준, 중복 집계) ===")
    for f in FLAGS:
        n = sum(1 for r in bad if r["verdict"].get(f) is False)
        print(f"  {f:12} {n:>5} ({n/len(bad):>4.0%}) {_bar(n, len(bad))}")

    print("\n=== 실패 유형 (이유 문장 분류, 다중 라벨) ===")
    c = Counter(t for r in bad for t in classify(r["verdict"].get("why", "")))
    top = c.most_common(1)[0][1]
    for name, n in c.most_common():
        print(f"  {name:10} {n:>5} ({n/len(bad):>4.0%}) {_bar(n, top)}")

    key = {"renderer": "renderer", "llm": "llm", "domain": "domain"}[by]
    groups = sorted({r[key] for r in bad})
    types = [t for t, _ in c.most_common(6)]
    print(f"\n=== {by}별 실패 유형 비중 (각 열은 그 {by}의 못씀 대비 %) ===")
    print(f"  {'':24}" + "".join(f"{t[:7]:>9}" for t in types) + f"{'못씀':>8}")
    for g in groups:
        sub = [r for r in bad if r[key] == g]
        cc = Counter(t for r in sub for t in classify(r["verdict"].get("why", "")))
        line = f"  {g[:24]:24}"
        for t in types:
            line += f"{cc[t]/len(sub)*100:>8.0f}%"
        print(line + f"{len(sub):>8}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="judge 결과 실패 유형 집계")
    ap.add_argument("--run", default="main")
    ap.add_argument("--by", default="renderer", choices=["renderer", "llm", "domain"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--spec-dir", default=None)
    ap.add_argument("--dump", default=None, help="분류 결과를 JSON으로 저장")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    run = paths.out_dir(args.out) / args.run
    rows = load(run, args.spec_dir)
    report(rows, args.by)
    if args.dump:
        out = [{**{k: r[k] for k in ("llm", "renderer", "slot_id", "domain", "name")},
                "usable": r["verdict"].get("usable"),
                "why": r["verdict"].get("why", ""),
                "types": classify(r["verdict"].get("why", ""))} for r in rows]
        Path(args.dump).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n분류 결과 -> {args.dump}")
