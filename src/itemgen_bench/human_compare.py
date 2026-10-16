"""두 렌더러의 사람 판정을 같은 셀끼리 비교하고 제작용 정책을 만든다.

벤치마크 결과(50슬롯 전체)와 결과를 보고 고른 제작 정책을 한 파일에 섞지 않는다.
JSON/CSV는 전체 비교를 보존하고, 별도 policy/spec 파일은 실제 제작 수율을 높일 때 쓴다.

  itemgen compare --run main \
    --compose-human out/main/human_verdicts_v02.json \
    --baseline-human out/main/human_verdicts.json \
    --write-prefix out/main/human_comparison_v02 \
    --write-policy spec/renderer_policy_final.json \
    --write-spec spec/slots_selected_final.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from . import paths, spec


DEFAULT_LLMS = ("qwen38_27b", "mistral_small_24b")


def _rate(ok: int, n: int) -> float:
    return round(ok / n, 6) if n else 0.0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load(path: Path, renderer: str, llms: tuple[str, ...],
          accept: tuple[str, ...]) -> dict[tuple[str, str], dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit(f"사람 판정 파일이 배열이 아니다: {path}")
    out: dict[tuple[str, str], dict] = {}
    for row in rows:
        if row.get("renderer") != renderer or row.get("llm") not in llms:
            continue
        key = (row.get("slot_id"), row.get("llm"))
        if not all(isinstance(x, str) and x for x in key):
            raise SystemExit(f"slot_id/llm이 없는 행: {row}")
        if key in out:
            raise SystemExit(f"중복 사람 판정: {key} ({path})")
        out[key] = {
            "verdict": row.get("verdict"),
            "usable": row.get("verdict") in accept,
        }
    if not out:
        raise SystemExit(f"{renderer} / {llms} 판정이 없다: {path}")
    return out


def _mcnemar_exact(compose_only: int, baseline_only: int) -> float:
    """불일치 쌍에 대한 양측 exact binomial McNemar p-value."""
    n = compose_only + baseline_only
    if n == 0:
        return 1.0
    lo = min(compose_only, baseline_only)
    p = 2 * sum(math.comb(n, k) for k in range(lo + 1)) / (2 ** n)
    return round(min(1.0, p), 12)


def _summaries(data: dict[tuple[str, str], dict], renderer: str,
               slot_domain: dict[str, str], llms: tuple[str, ...]) -> list[dict]:
    rows: list[dict] = []

    def add(scope: str, group: str, keys) -> None:
        vals = [data[k]["usable"] for k in keys]
        ok, n = sum(vals), len(vals)
        rows.append({"scope": scope, "group": group, "renderer": renderer,
                     "usable": ok, "n": n, "rate": _rate(ok, n)})

    keys = sorted(data)
    add("overall", "all", keys)
    for llm in llms:
        add("llm", llm, [k for k in keys if k[1] == llm])
    domains = list(dict.fromkeys(slot_domain.values()))
    for domain in domains:
        add("domain", domain, [k for k in keys if slot_domain[k[0]] == domain])
    return rows


def _judge_agreement(run: Path, human: dict[tuple[str, str], dict],
                     renderer: str, llms: tuple[str, ...]) -> dict | None:
    path = run / "judge.json"
    if not path.exists():
        return None
    grouped: dict[tuple[str, str], list[bool | None]] = defaultdict(list)
    for row in json.loads(path.read_text(encoding="utf-8")):
        if row.get("renderer") == renderer and row.get("llm") in llms:
            grouped[(row["slot_id"], row["llm"])].append(
                row.get("verdict", {}).get("usable"))
    auto = {k: bool(grouped.get(k)) and all(v is True for v in grouped[k]) for k in human}
    tp = sum(human[k]["usable"] and auto[k] for k in human)
    fp = sum(not human[k]["usable"] and auto[k] for k in human)
    fn = sum(human[k]["usable"] and not auto[k] for k in human)
    tn = sum(not human[k]["usable"] and not auto[k] for k in human)
    n = len(human)
    po = (tp + tn) / n
    ph = (tp + fn) / n
    pa = (tp + fp) / n
    pe = ph * pa + (1 - ph) * (1 - pa)
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": round(po, 6),
        "precision": round(tp / (tp + fp), 6) if tp + fp else None,
        "recall": round(tp / (tp + fn), 6) if tp + fn else None,
        "specificity": round(tn / (tn + fp), 6) if tn + fp else None,
        "cohen_kappa": round((po - pe) / (1 - pe), 6) if pe != 1 else None,
    }


def compare(compose_path: Path, baseline_path: Path, compose_renderer: str,
            baseline_renderer: str, llms: tuple[str, ...],
            compose_accept: tuple[str, ...], baseline_accept: tuple[str, ...],
            spec_dir=None, run: Path | None = None) -> tuple[dict, list[dict], dict]:
    slots_doc = spec.load_slots(spec_dir)
    slots = slots_doc["slots"]
    slot_domain = {s["id"]: s["domain"] for s in slots}
    compose = _load(compose_path, compose_renderer, llms, compose_accept)
    baseline = _load(baseline_path, baseline_renderer, llms, baseline_accept)
    if set(compose) != set(baseline):
        only_c = sorted(set(compose) - set(baseline))
        only_b = sorted(set(baseline) - set(compose))
        raise SystemExit(f"짝이 맞지 않는다: compose만 {only_c}, baseline만 {only_b}")
    unknown = sorted({sid for sid, _ in compose} - set(slot_domain))
    if unknown:
        raise SystemExit(f"slots.json에 없는 slot_id: {unknown}")

    keys = sorted(compose)
    summaries = (_summaries(compose, compose_renderer, slot_domain, llms) +
                 _summaries(baseline, baseline_renderer, slot_domain, llms))
    both = sum(compose[k]["usable"] and baseline[k]["usable"] for k in keys)
    compose_only = sum(compose[k]["usable"] and not baseline[k]["usable"] for k in keys)
    baseline_only = sum(not compose[k]["usable"] and baseline[k]["usable"] for k in keys)
    neither = len(keys) - both - compose_only - baseline_only

    domains = list(dict.fromkeys(slot_domain.values()))
    policy_by_domain: dict[str, str] = {}
    for domain in domains:
        dkeys = [k for k in keys if slot_domain[k[0]] == domain]
        c_ok = sum(compose[k]["usable"] for k in dkeys)
        b_ok = sum(baseline[k]["usable"] for k in dkeys)
        policy_by_domain[domain] = compose_renderer if c_ok >= b_ok else baseline_renderer

    def chosen_for(key):
        renderer = policy_by_domain[slot_domain[key[0]]]
        return compose[key] if renderer == compose_renderer else baseline[key]

    policy_ok = sum(chosen_for(k)["usable"] for k in keys)
    selected: list[str] = []
    for slot in slots:
        sid = slot["id"]
        skeys = [(sid, llm) for llm in llms]
        if all(k in compose and chosen_for(k)["usable"] for k in skeys):
            selected.append(sid)

    cells = []
    for sid, llm in keys:
        chosen = policy_by_domain[slot_domain[sid]]
        cells.append({
            "slot_id": sid, "domain": slot_domain[sid], "llm": llm,
            "compose_verdict": compose[(sid, llm)]["verdict"],
            "compose_usable": compose[(sid, llm)]["usable"],
            "baseline_verdict": baseline[(sid, llm)]["verdict"],
            "baseline_usable": baseline[(sid, llm)]["usable"],
            "policy_renderer": chosen,
            "policy_usable": chosen_for((sid, llm))["usable"],
        })

    report = {
        "version": "1.0",
        "inputs": {
            "compose_human": str(compose_path),
            "compose_human_sha256": _sha256(compose_path),
            "baseline_human": str(baseline_path),
            "baseline_human_sha256": _sha256(baseline_path),
        },
        "criteria": {
            "llms": list(llms),
            compose_renderer: {"accepted_verdicts": list(compose_accept)},
            baseline_renderer: {"accepted_verdicts": list(baseline_accept)},
        },
        "coverage": {"paired_cells": len(keys), "slots": len({k[0] for k in keys})},
        "summaries": summaries,
        "paired": {
            "both_usable": both,
            "compose_only": compose_only,
            "baseline_only": baseline_only,
            "neither_usable": neither,
            "absolute_difference": _rate(compose_only - baseline_only, len(keys)),
            "mcnemar_exact_two_sided_p": _mcnemar_exact(compose_only, baseline_only),
        },
        "judge_vs_human_compose": (
            _judge_agreement(run, compose, compose_renderer, llms) if run else None),
        "production_policy": {
            "status": "data-derived; production only, not an unbiased benchmark",
            "selection_rule": "domain winner; require every listed LLM to be human-usable",
            "renderer_by_domain": policy_by_domain,
            "observed_usable": policy_ok,
            "observed_n": len(keys),
            "observed_rate": _rate(policy_ok, len(keys)),
            "selected_slots": selected,
            "selected_slot_count": len(selected),
        },
    }
    return report, cells, slots_doc


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_outputs(prefix: Path, report: dict, cells: list[dict]) -> None:
    _write_json(prefix.with_suffix(".json"), report)
    summary_path = prefix.with_suffix(".csv")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8", newline="") as f:
        fields = ["scope", "group", "renderer", "usable", "n", "rate"]
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(report["summaries"])
    cells_path = prefix.parent / f"{prefix.name}_cells.csv"
    with cells_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cells[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(cells)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="동일 셀의 두 렌더러 사람 판정을 비교한다")
    ap.add_argument("--run", default="main")
    ap.add_argument("--out", default=None)
    ap.add_argument("--spec-dir", default=None)
    ap.add_argument("--compose-human", default=None)
    ap.add_argument("--baseline-human", default=None)
    ap.add_argument("--compose-renderer", default="compose")
    ap.add_argument("--baseline-renderer", default="flux2_klein_4b")
    ap.add_argument("--llms", default=",".join(DEFAULT_LLMS))
    ap.add_argument("--compose-accept", default="ok")
    ap.add_argument("--baseline-accept", default="ok,minor")
    ap.add_argument("--write-prefix", default=None)
    ap.add_argument("--write-policy", default=None)
    ap.add_argument("--write-spec", default=None)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    run = paths.out_dir(args.out) / args.run
    compose_path = Path(args.compose_human) if args.compose_human else run / "human_verdicts_v02.json"
    baseline_path = Path(args.baseline_human) if args.baseline_human else run / "human_verdicts.json"
    llms = tuple(x.strip() for x in args.llms.split(",") if x.strip())
    compose_accept = tuple(x.strip() for x in args.compose_accept.split(",") if x.strip())
    baseline_accept = tuple(x.strip() for x in args.baseline_accept.split(",") if x.strip())
    report, cells, slots_doc = compare(
        compose_path, baseline_path, args.compose_renderer, args.baseline_renderer,
        llms, compose_accept, baseline_accept, args.spec_dir, run)

    prefix = Path(args.write_prefix) if args.write_prefix else run / "human_comparison_v02"
    write_outputs(prefix, report, cells)
    print(f"사람 판정 짝비교 {report['coverage']['paired_cells']}칸")
    for row in report["summaries"]:
        if row["scope"] == "overall":
            print(f"  {row['renderer']}: {row['usable']}/{row['n']} ({row['rate']:.1%})")
    paired = report["paired"]
    print(f"  compose만 {paired['compose_only']} · baseline만 {paired['baseline_only']} "
          f"· McNemar p={paired['mcnemar_exact_two_sided_p']:.4f}")
    policy = report["production_policy"]
    print(f"제작 정책: {policy['observed_usable']}/{policy['observed_n']} "
          f"({policy['observed_rate']:.1%}) · 슬롯 {policy['selected_slot_count']}개")
    print(f"결과: {prefix.with_suffix('.json')}, {prefix.with_suffix('.csv')}, "
          f"{prefix.parent / (prefix.name + '_cells.csv')}")

    if args.write_policy:
        value = {
            "version": "1.0",
            "purpose": "production only; derived after evaluation",
            "llms": list(llms),
            "accepted_verdicts": {
                args.compose_renderer: list(compose_accept),
                args.baseline_renderer: list(baseline_accept),
            },
            **policy,
        }
        _write_json(Path(args.write_policy), value)
        print(f"정책: {args.write_policy}")

    if args.write_spec:
        keep = set(policy["selected_slots"])
        selected_doc = dict(slots_doc)
        selected_doc["slots"] = [s for s in slots_doc["slots"] if s["id"] in keep]
        selected_doc["note"] = (slots_doc.get("note", "") +
            " | 제작용: 영역별 사람 판정 우세 렌더러를 쓰고 Qwen/Mistral 모두 "
            f"사용 가능한 슬롯만 선택 ({len(keep)}/{len(slots_doc['slots'])})")
        _write_json(Path(args.write_spec), selected_doc)
        print(f"슬롯: {args.write_spec}")


if __name__ == "__main__":
    main()
