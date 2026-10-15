"""과제명세서 조립 · 출력 검증 · 룰 채점.

LLM에 넣을 프롬프트를 만들고(build_prompt), 돌아온 JSON이 쓸 만한지 두 단계로 본다.

  validate_item : 스키마를 지켰나. 실패하면 그 문항은 버린다. (합격/불합격)
  score_item    : 명세를 얼마나 잘 썼나. 0~1 점수 여러 개. (정도)

jsonschema 패키지에 의존하지 않는다. 설치 없이 돌아가는 게 파이프라인 재현성에 낫다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import paths

STAGES = {"학습", "검사", "기준", "보기", "검사 보기", "인물 보기", "main"}
KINDS = {"scene", "edit", "option"}
OPS = {"move", "remove", "add", "recolor", "rotate", "rescale", "mirror"}
LAYOUTS = {"grid", "row", "free"}
FACINGS = {"정면", "뒷면", "왼쪽", "오른쪽", "위", "아래"}

# 뭉치·세트로 그려지기 쉬워 "낱개"를 못박아야 하는 사물.
CLUMPY = ["열쇠", "빗", "포도", "바나나", "체리", "연필", "크레파스", "성냥", "동전",
          "구슬", "단추", "클립", "젓가락", "꽃", "나뭇잎", "열매"]
# 명세에 들어가면 안 되는 부정 표현.
NEGATIONS = ["넣지 않", "없도록", "제외", "빼고", "않는다", "말고", "금지", "없다"]
# 이미지에 글자를 요구하는 표현.
TEXT_DEMANDS = ["글자", "단어", "문장", "낱말", "숫자", "한글", "번호", "알파벳",
                "라벨", "캡션", "①", "②", "③", "④", "적힌", "써진", "쓰여"]


def load_slots(spec_dir=None) -> dict:
    return json.loads((paths.spec_dir(spec_dir) / "slots.json").read_text(encoding="utf-8"))


def load_schema(spec_dir=None) -> dict:
    return json.loads((paths.spec_dir(spec_dir) / "item_schema.json").read_text(encoding="utf-8"))


def build_prompt(slot: dict, spec_dir=None) -> str:
    """과제명세서 본문에 스키마와 이번 슬롯을 꽂아 최종 프롬프트를 만든다."""
    body = (paths.spec_dir(spec_dir) / "task_spec.md").read_text(encoding="utf-8")
    schema = json.dumps(load_schema(spec_dir), ensure_ascii=False, indent=2)
    return (body.replace("{SCHEMA}", schema)
                .replace("{SLOT}", json.dumps(slot, ensure_ascii=False, indent=2)))


# --------------------------------------------------------------- 스키마 검증

def _check_scene(scene, where: str, errs: list) -> None:
    if not isinstance(scene, dict):
        errs.append(f"{where}.scene: 객체가 아니다"); return
    layout = scene.get("layout")
    if layout not in LAYOUTS:
        errs.append(f"{where}.scene.layout: {layout!r} (허용: {sorted(LAYOUTS)})")
    objects = scene.get("objects")
    if not isinstance(objects, list) or not objects:
        errs.append(f"{where}.scene.objects: 비어 있다"); return
    if len(objects) > 24:
        errs.append(f"{where}.scene.objects: {len(objects)}개는 너무 많다 (최대 24)")
    refs = set()
    for k, obj in enumerate(objects):
        at = f"{where}.scene.objects[{k}]"
        if not isinstance(obj, dict):
            errs.append(f"{at}: 객체가 아니다"); continue
        ref = obj.get("ref")
        if not isinstance(ref, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", ref or ""):
            errs.append(f"{at}.ref: {ref!r} (소문자 영문 식별자여야 한다)")
        elif ref in refs:
            errs.append(f"{at}.ref: {ref!r} 중복")
        else:
            refs.add(ref)
        if not obj.get("name"):
            errs.append(f"{at}.name: 비어 있다")
        if layout == "grid" and "cell" not in obj:
            errs.append(f"{at}: layout=grid인데 cell이 없다")
        if layout == "free" and "pos" not in obj:
            errs.append(f"{at}: layout=free인데 pos가 없다")
        cell = obj.get("cell")
        if cell is not None:
            if not (isinstance(cell, list) and len(cell) == 2
                    and all(isinstance(v, int) and v >= 0 for v in cell)):
                errs.append(f"{at}.cell: {cell!r} ([행, 열] 정수 2개)")
            elif layout == "grid":
                rows, cols = scene.get("rows"), scene.get("cols")
                if isinstance(rows, int) and cell[0] >= rows:
                    errs.append(f"{at}.cell: 행 {cell[0]}이 rows={rows} 범위를 넘는다")
                if isinstance(cols, int) and cell[1] >= cols:
                    errs.append(f"{at}.cell: 열 {cell[1]}이 cols={cols} 범위를 넘는다")
        pos = obj.get("pos")
        if pos is not None and not (isinstance(pos, list) and len(pos) == 2
                                    and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in pos)):
            errs.append(f"{at}.pos: {pos!r} (0~1 실수 2개)")
        facing = obj.get("facing")
        if facing is not None and facing not in FACINGS:
            errs.append(f"{at}.facing: {facing!r} (허용: {sorted(FACINGS)})")
    if layout == "grid":
        for key in ("rows", "cols"):
            if not isinstance(scene.get(key), int):
                errs.append(f"{where}.scene.{key}: layout=grid면 정수로 필요하다")
        seen = {}
        for obj in objects:
            cell = obj.get("cell")
            if isinstance(cell, list) and len(cell) == 2:
                tag = tuple(cell)
                if tag in seen:
                    errs.append(f"{where}.scene: 칸 {tag}에 {seen[tag]}와 {obj.get('ref')}가 겹친다")
                else:
                    seen[tag] = obj.get("ref")
    return


def validate_item(item, slot: dict) -> list[str]:
    """스키마와 슬롯 요구를 지켰는지 본다. 빈 리스트면 합격."""
    errs: list[str] = []
    if not isinstance(item, dict):
        return ["최상위가 JSON 객체가 아니다"]

    allowed = {"slot_id", "question", "answer", "answer_rationale", "panels"}
    for key in set(item) - allowed:
        errs.append(f"모르는 최상위 키: {key!r}")
    if item.get("slot_id") != slot["id"]:
        errs.append(f"slot_id: {item.get('slot_id')!r} != {slot['id']!r}")
    if not isinstance(item.get("question"), str) or len(item.get("question", "")) < 5:
        errs.append("question: 5자 이상 문자열이어야 한다")
    if not str(item.get("answer", "")).strip():
        errs.append("answer: 비어 있다")

    panels = item.get("panels")
    if not isinstance(panels, list) or not panels:
        return errs + ["panels: 비어 있다"]

    by_stage: dict[str, dict] = {}
    for k, panel in enumerate(panels):
        at = f"panels[{k}]"
        if not isinstance(panel, dict):
            errs.append(f"{at}: 객체가 아니다"); continue
        stage, kind = panel.get("stage"), panel.get("kind")
        if stage not in STAGES:
            errs.append(f"{at}.stage: {stage!r} (허용: {sorted(STAGES)})")
        if kind not in KINDS:
            errs.append(f"{at}.kind: {kind!r} (허용: {sorted(KINDS)})")
        # 보기(option)는 장면을 직접 쓰거나 기준에서 파생시킬 수 있다. 파생이면 edit과 같은 검사를 받는다.
        derived = kind == "edit" or (kind == "option" and "edit_of" in panel)
        if kind in ("scene", "option") and not derived:
            _check_scene(panel.get("scene"), at, errs)
        if derived:
            base = panel.get("edit_of")
            if base not in by_stage:
                errs.append(f"{at}.edit_of: {base!r} — 앞에 그 stage 패널이 없다")
            changes = panel.get("changes")
            if kind == "option" and changes is None:
                changes = []                     # 정답 보기는 기준과 같을 수 있다
            if not isinstance(changes, list) or (kind == "edit" and not changes):
                errs.append(f"{at}.changes: 비어 있다")
            else:
                base_refs = _refs_of(by_stage.get(base, {}))
                for j, ch in enumerate(changes):
                    cat = f"{at}.changes[{j}]"
                    if not isinstance(ch, dict):
                        errs.append(f"{cat}: 객체가 아니다"); continue
                    if ch.get("op") not in OPS:
                        errs.append(f"{cat}.op: {ch.get('op')!r} (허용: {sorted(OPS)})")
                    target = ch.get("target")
                    if not target:
                        errs.append(f"{cat}.target: 비어 있다")
                    elif ch.get("op") == "add":
                        if not isinstance(ch.get("object"), dict):
                            errs.append(f"{cat}: op=add인데 object가 없다")
                    elif base_refs and target not in base_refs:
                        errs.append(f"{cat}.target: {target!r}가 {base!r} 패널에 없다 "
                                    f"(있는 것: {sorted(base_refs)})")
        if kind == "option" and not isinstance(panel.get("option_index"), int):
            errs.append(f"{at}.option_index: option 패널에는 정수로 필요하다")
        if stage in STAGES:
            by_stage.setdefault(stage, panel)

    errs += _check_slot_fit(item, slot)
    errs += _check_resolved(item)
    return errs


def _check_resolved(item: dict) -> list[str]:
    """edit을 적용한 뒤의 장면도 검사한다.

    선언된 장면만 보면, 사물을 이미 찬 칸으로 옮기는 changes를 놓친다. 실제로
    그려지는 것은 적용 후 장면이므로 거기서 겹침·범위를 다시 본다.
    """
    from . import render  # 순환 import를 피해 함수 안에서 부른다
    errs: list[str] = []
    try:
        panels = render.resolve_panels(item)
    except Exception as exc:
        return [f"패널 해석 실패: {type(exc).__name__}: {exc}"]
    for k, panel in enumerate(panels):
        if panel.get("kind") != "edit":
            continue
        sub: list[str] = []
        _check_scene(panel["scene"], f"panels[{k}](edit 적용 후)", sub)
        errs += sub
    return errs


def _refs_of(panel: dict) -> set[str]:
    scene = panel.get("scene") or {}
    return {o.get("ref") for o in scene.get("objects", []) if isinstance(o, dict)}


def _check_slot_fit(item: dict, slot: dict) -> list[str]:
    """슬롯이 요구한 패널 구성과 맞는지 본다."""
    errs = []
    panels = item.get("panels", [])
    want_opts, want_derived = 0, None
    want_stages = []
    for p in slot["panels"]:
        if p.get("kind") == "option":
            want_opts = p.get("n", 4)
            if p.get("derived"):
                want_derived = p.get("edit_of")
        else:
            want_stages.append(p["stage"])
    got_stages = [p.get("stage") for p in panels if p.get("kind") != "option"]
    for stage in want_stages:
        if stage not in got_stages:
            errs.append(f"슬롯이 요구한 '{stage}' 패널이 없다")
    got_opts = sum(1 for p in panels if p.get("kind") == "option")
    if want_opts and got_opts != want_opts:
        errs.append(f"보기 패널 {want_opts}장이 필요한데 {got_opts}장이다")
    if not want_opts and got_opts:
        errs.append(f"보기 패널을 요구하지 않은 슬롯인데 {got_opts}장 있다")
    if want_derived:
        # 보기가 기준의 통제된 변형이어야 하는 슬롯. 장면을 새로 쓰면 보기끼리 도형이 달라진다.
        for p in panels:
            if p.get("kind") != "option":
                continue
            idx = p.get("option_index")
            if "edit_of" not in p:
                errs.append(f"보기 {idx}: 이 슬롯은 보기를 '{want_derived}'에서 "
                            f"edit_of로 파생시켜야 한다 (장면 재기술 금지)")
            elif p["edit_of"] != want_derived:
                errs.append(f"보기 {idx}.edit_of: {p['edit_of']!r}가 아니라 "
                            f"{want_derived!r}이어야 한다")
        # 보기가 전부 같은 변형이면 변별이 안 된다.
        sigs = [json.dumps(p.get("changes") or [], ensure_ascii=False, sort_keys=True)
                for p in panels if p.get("kind") == "option"]
        if len(sigs) > 1 and len(set(sigs)) == 1:
            errs.append("보기 전부가 같은 changes다 — 정답이 유일하지 않다")

    edits = [p for p in slot["panels"] if p.get("kind") == "edit"]
    for want in edits:
        made = [p for p in panels if p.get("kind") == "edit" and p.get("stage") == want["stage"]]
        if not made:
            errs.append(f"'{want['stage']}' 패널은 kind=edit으로 써야 한다 (장면 재기술 금지)")
            continue
        n_want = want.get("n_changes")
        n_got = len(made[0].get("changes") or [])
        if n_want and n_got != n_want:
            errs.append(f"'{want['stage']}' 변경 수: {n_want}개여야 하는데 {n_got}개다")
    if slot.get("response") == "choice4" and not re.fullmatch(r"[1-4]", str(item.get("answer", "")).strip()):
        errs.append(f"보기형 슬롯인데 answer가 {item.get('answer')!r} (1~4 숫자여야 한다)")
    return errs


# --------------------------------------------------------------- 룰 채점

def _all_text(item: dict) -> str:
    return json.dumps(item, ensure_ascii=False)


def score_item(item: dict, slot: dict) -> dict:
    """명세를 얼마나 잘 썼는지 0~1 점수. 스키마 합격 문항에만 의미가 있다."""
    scenes, objects = [], []
    for panel in item.get("panels", []):
        scene = panel.get("scene")
        if isinstance(scene, dict):
            scenes.append(scene)
            objects += [o for o in scene.get("objects", []) if isinstance(o, dict)]
    text = _all_text(item)
    n = len(objects) or 1

    placed = sum(1 for o in objects if "cell" in o or "pos" in o)
    appearance = sum(1 for o in objects if (o.get("appearance") or o.get("color")))
    # 이름에 수량어가 섞였으면 개수를 자리로 풀지 않은 것이다.
    numeric = sum(1 for o in objects
                  if re.search(r"\d|두 |세 |네 |다섯|여러|몇", f"{o.get('name','')}{o.get('appearance','')}"))
    clumpy = [o for o in objects if any(c in str(o.get("name", "")) for c in CLUMPY)]
    clumpy_ok = sum(1 for o in clumpy if "낱개" in str(o.get("appearance", "")) or "하나" in str(o.get("appearance", "")))
    directional = [o for o in objects
                   if any(w in str(o.get("name", "")) for w in ("사람", "아이", "남자", "여자", "동물",
                                                                 "개", "고양이", "화살표", "얼굴"))]
    facing_ok = sum(1 for o in directional if o.get("facing"))

    wants_edit = any(p.get("kind") == "edit" for p in slot["panels"])
    made_edit = any(p.get("kind") == "edit" for p in item.get("panels", []))

    opts = [p for p in item.get("panels", []) if p.get("kind") == "option"]
    # 보기들이 통제된 변형이면 사물 구성(ref 집합)이 같아야 한다.
    if len(opts) >= 2:
        ref_sets = [frozenset(_refs_of(p)) for p in opts]
        controlled = sum(1 for s in ref_sets if s == ref_sets[0]) / len(ref_sets)
    else:
        controlled = None

    scores = {
        "S1_좌표_완전성": placed / n,
        "S2_개수를_자리로": 1.0 - min(1.0, numeric / n),
        "S3_외형_기술": appearance / n,
        "S4_낱개_명시": (clumpy_ok / len(clumpy)) if clumpy else None,
        "S5_부정문_없음": 0.0 if any(w in text for w in NEGATIONS) else 1.0,
        "S6_edit_사용": (1.0 if made_edit else 0.0) if wants_edit else None,
        "S7_보기_통제": controlled,
        "S8_글자요구_없음": 0.0 if any(w in text for w in TEXT_DEMANDS) else 1.0,
        "S9_facing_명시": (facing_ok / len(directional)) if directional else None,
        "S10_유일성_근거": 1.0 if str(item.get("answer_rationale", "")).strip() else 0.0,
    }
    live = [v for v in scores.values() if v is not None]
    scores["총점"] = sum(live) / len(live) if live else 0.0
    return scores
