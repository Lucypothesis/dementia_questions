"""선택 명세에 따라 여러 run의 셀을 하나의 최종 검수 시트로 합친다.

생성·렌더링 결과를 다시 만들지 않고, 사람이 고른 (slot, LLM, renderer) 셀만
원본 run에서 복사한다. 명세와 사람 판정 근거도 함께 검증하므로 체리피킹 결과가
어디서 왔는지 나중에 되짚을 수 있다.

  itemgen finalize --policy spec/renderer_policy_final_50.json --run final50 --embed
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from . import paths, render, sheet, spec


def _read_json(path: Path):
    if not path.exists():
        raise SystemExit(f"파일이 없다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _under(root: Path, relative: str) -> Path:
    """명세의 상대 경로가 저장소 밖을 가리키지 못하게 한다."""
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit(f"저장소 밖 경로는 쓸 수 없다: {relative}") from exc
    return path


def _human_ok(root: Path, evidence: str, slot_id: str,
              llm: str, renderer: str, cache: dict[Path, list[dict]]) -> bool:
    path = _under(root, evidence)
    rows = cache.setdefault(path, _read_json(path))
    return any(row.get("slot_id") == slot_id
               and row.get("llm") == llm
               and row.get("renderer") == renderer
               and row.get("verdict") == "ok" for row in rows)


def _validate_final_review(root: Path, policy: dict,
                           selected: set[tuple[str, str, str]]) -> dict[str, int] | None:
    """최종 사람 판정이 선택된 50개 셀과 정확히 대응하는지 검증한다."""
    meta = policy.get("final_human_review")
    if not meta:
        return None
    rows = _read_json(_under(root, meta["source"]))
    if not isinstance(rows, list):
        raise SystemExit("final_human_review 파일은 JSON 배열이어야 한다")

    keys = [(row.get("slot_id"), row.get("llm"), row.get("renderer")) for row in rows]
    if len(keys) != len(set(keys)):
        raise SystemExit("final_human_review에 중복 셀이 있다")
    if len(rows) != int(meta.get("coverage", -1)):
        raise SystemExit(
            f"최종 판정 수가 정책과 다르다: 실제 {len(rows)} / "
            f"정책 {meta.get('coverage')}")
    actual = set(keys)
    if actual != selected:
        missing = sorted(selected - actual)
        extra = sorted(actual - selected)
        raise SystemExit(f"최종 판정 셀 불일치 — 누락: {missing} / 초과: {extra}")

    counts = Counter(str(row.get("verdict", "")) for row in rows)
    declared = {str(key): int(value)
                for key, value in (meta.get("verdict_counts") or {}).items()}
    normalized = {key: counts.get(key, 0) for key in set(counts) | set(declared)}
    expected_counts = {key: declared.get(key, 0) for key in normalized}
    if normalized != expected_counts:
        raise SystemExit(f"최종 판정 집계 불일치: 실제 {normalized} / 정책 {expected_counts}")

    accepted = set(meta.get("accepted_verdicts") or [])
    rejected = [row["slot_id"] for row in rows if row.get("verdict") not in accepted]
    if rejected:
        raise SystemExit(f"최종 정책이 허용하지 않은 판정: {rejected}")
    actual_minor = sorted(row["slot_id"] for row in rows if row.get("verdict") == "minor")
    declared_minor = sorted(meta.get("accepted_minor_slots") or [])
    if actual_minor != declared_minor:
        raise SystemExit(
            f"minor 슬롯 불일치: 실제 {actual_minor} / 정책 {declared_minor}")
    return {key: counts.get(key, 0) for key in ("ok", "minor", "bad")}


def build(policy_path: Path, run_name: str, *, embed: bool = False,
          filename: str = "sheet_final50.html") -> tuple[Path, dict]:
    root = paths.project_root().resolve()
    policy = _read_json(policy_path)
    cells = policy.get("selected_cells") or []
    slots_doc = _read_json(paths.spec_dir() / "slots.json")
    slots = slots_doc["slots"] if isinstance(slots_doc, dict) else slots_doc
    by_id = {slot["id"]: slot for slot in slots}
    answer_targets = {
        slot["id"]: int(slot["target_answer"])
        for slot in slots if slot.get("target_answer") is not None
    }

    ids = [cell.get("slot_id") for cell in cells]
    duplicates = sorted(slot_id for slot_id, n in Counter(ids).items() if n > 1)
    if duplicates:
        raise SystemExit(f"중복 슬롯: {', '.join(duplicates)}")
    expected = set(by_id)
    actual = set(ids)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "없음"
        extra = ", ".join(sorted(actual - expected)) or "없음"
        raise SystemExit(f"슬롯 집합 불일치 — 누락: {missing} / 초과: {extra}")

    selected_keys = {(cell["slot_id"], cell["llm"], cell["renderer"])
                     for cell in cells}
    final_review_counts = _validate_final_review(root, policy, selected_keys)

    distribution = Counter(by_id[slot_id]["domain"] for slot_id in ids)
    expected_distribution = policy.get("distribution") or {}
    if dict(distribution) != expected_distribution:
        raise SystemExit(f"영역 분포 불일치: 실제 {dict(distribution)} / "
                         f"명세 {expected_distribution}")

    destination = paths.out_dir() / run_name
    destination.mkdir(parents=True, exist_ok=True)
    selected_panels: list[dict] = []
    only: set[str] = set()
    evidence_cache: dict[Path, list[dict]] = {}
    copied_images = copied_thumbs = 0
    answer_distribution: Counter[int] = Counter()
    balanced_slots: set[str] = set()

    for cell in cells:
        slot_id = cell["slot_id"]
        llm = cell["llm"]
        renderer = cell["renderer"]
        source = _under(root, f"out/{cell['source_run']}")
        key = f"{slot_id}__{llm}__{renderer}"
        only.add(key)

        if cell.get("review_status") == "human-ok":
            evidence = cell.get("human_evidence")
            if not evidence or not _human_ok(
                    root, evidence, slot_id, llm, renderer, evidence_cache):
                raise SystemExit(f"사람 ok 근거가 맞지 않는다: {key} ({evidence})")

        item_src = source / "items" / llm / f"{slot_id}.json"
        if not item_src.exists():
            raise SystemExit(f"문항 JSON이 없다: {item_src}")
        item = _read_json(item_src)
        option_panels = [panel for panel in item.get("panels", [])
                         if panel.get("kind") == "option"]
        single_choice = bool(option_panels) and bool(
            re.fullmatch(r"[1-4]", str(item.get("answer", "")).strip()))
        if single_choice:
            if slot_id not in answer_targets:
                raise SystemExit(f"정답 위치 목표가 빠졌다: {slot_id}")
            try:
                item, option_mapping = spec.rebalance_item(
                    item, answer_targets[slot_id])
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            balanced_slots.add(slot_id)
            answer_distribution[int(item["answer"])] += 1
        elif slot_id in answer_targets:
            raise SystemExit(f"단일정답 문항이 아닌데 위치 목표가 있다: {slot_id}")
        else:
            option_mapping = {}

        item_dst = destination / "items" / llm / item_src.name
        item_dst.parent.mkdir(parents=True, exist_ok=True)
        item_dst.write_text(
            json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")

        source_panels = _read_json(source / "panels.json")
        matched = [dict(row) for row in source_panels
                   if row.get("slot_id") == slot_id
                   and row.get("llm") == llm
                   and row.get("renderer") == renderer]
        if not matched:
            raise SystemExit(f"렌더 패널이 없다: {key} in {source}")
        item_panel_by_option = {
            panel["option_index"]: (index, panel)
            for index, panel in enumerate(item["panels"], start=1)
            if panel.get("kind") == "option"
        }

        for row in matched:
            old_image_rel = row["image"]
            old_option = row.get("option_index")
            if old_option in option_mapping:
                new_option = option_mapping[old_option]
                new_panel_index, new_panel = item_panel_by_option[new_option]
                row["option_index"] = new_option
                row["panel_index"] = new_panel_index
                row["name"] = render.panel_name(slot_id, new_panel_index, new_panel)
                row["image"] = str(
                    Path(old_image_rel).with_name(row["name"] + ".png"))

            selected_panels.append(row)
            image_src = source / row["image"]
            if option_mapping:
                image_src = source / old_image_rel
            if not image_src.exists():
                raise SystemExit(f"렌더 이미지가 없다: {image_src}")
            image_dst = destination / row["image"]
            image_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image_src, image_dst)
            copied_images += 1

            old_thumb_rel = old_image_rel.replace("images/", "thumbs/", 1)
            old_thumb_rel = str(Path(old_thumb_rel).with_suffix(".jpg"))
            thumb_rel = row["image"].replace("images/", "thumbs/", 1)
            thumb_rel = str(Path(thumb_rel).with_suffix(".jpg"))
            thumb_src = source / old_thumb_rel
            if thumb_src.exists():
                thumb_dst = destination / thumb_rel
                thumb_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(thumb_src, thumb_dst)
                copied_thumbs += 1

    if balanced_slots != set(answer_targets):
        missing = ", ".join(sorted(set(answer_targets) - balanced_slots)) or "없음"
        extra = ", ".join(sorted(balanced_slots - set(answer_targets))) or "없음"
        raise SystemExit(f"정답 위치 적용 불일치 — 누락: {missing} / 초과: {extra}")

    selected_panels.sort(key=lambda row: (
        row["slot_id"], row["llm"], row["renderer"], row["panel_index"]))
    (destination / "panels.json").write_text(
        json.dumps(selected_panels, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "selection.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")

    output = sheet.build(
        destination, slots, embed=embed, only=only, filename=filename,
        title_suffix="최종 50문제 · 영역별 10문제", size=512, quality=84,
        default_all_renderers=True)
    source_human_ok = sum(c.get("review_status") == "human-ok" for c in cells)
    final_counts = final_review_counts or {"ok": source_human_ok, "minor": 0, "bad": 0}
    summary = {
        "slots": len(cells),
        "distribution": dict(distribution),
        "panels": len(selected_panels),
        "images": copied_images,
        "thumbs": copied_thumbs,
        "source_human_ok": source_human_ok,
        "human_ok": final_counts["ok"],
        "accepted_minor": final_counts["minor"],
        "human_bad": final_counts["bad"],
        "pending_user_review": 0 if final_review_counts else len(cells) - source_human_ok,
        "answer_distribution": {
            str(answer): answer_distribution[answer] for answer in range(1, 5)
        },
    }
    (destination / "finalize_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, summary


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="선택된 셀을 최종 HTML로 조립")
    ap.add_argument("--policy", default="spec/renderer_policy_final_50.json")
    ap.add_argument("--run", default="final50")
    ap.add_argument("--filename", default="sheet_final50.html")
    ap.add_argument("--embed", action="store_true", help="이미지를 HTML 안에 내장")
    args = ap.parse_args(argv)

    policy_path = Path(args.policy).expanduser().resolve()
    output, summary = build(
        policy_path, args.run, embed=args.embed, filename=args.filename)
    print(f"최종 시트: {output}")
    print(f"슬롯 {summary['slots']} · 패널 {summary['panels']} · "
          f"최종 판정 ok {summary['human_ok']} / "
          f"허용 minor {summary['accepted_minor']} / bad {summary['human_bad']}")
    print("영역: " + " · ".join(
        f"{domain} {count}" for domain, count in summary["distribution"].items()))
