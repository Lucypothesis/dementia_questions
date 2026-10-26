"""파이프라인 자기검증. 외부 모델 없이 돈다.

검증기가 나쁜 출력을 실제로 잡는지(음성 검사), edit이 동일성을 보장하는지,
시트 JS가 파싱되는지 확인한다.

  python src/selftest.py
"""

from __future__ import annotations

import copy
import json
import sys
from collections import Counter
from pathlib import Path

from . import render as rnd
from . import spec

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "OK  " if cond else "실패"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail and not cond else ""))


def good_item(slot_id="MEM_03"):
    return {
        "slot_id": slot_id,
        "question": "위치가 바뀐 물건은 무엇인가요?",
        "answer": "열쇠",
        "answer_rationale": "열쇠만 칸이 바뀌었고 나머지 셋은 그대로다.",
        "panels": [
            # 위치 변화 문항은 옮길 빈 칸이 있어야 한다. 2x3에 사물 4개를 두어 두 칸을 비운다.
            {"stage": "학습", "kind": "scene", "scene": {
                "layout": "grid", "rows": 2, "cols": 3, "background": "흰 바닥",
                "objects": [
                    {"ref": "cup", "name": "머그컵", "appearance": "손잡이 달린 흰색 낱개", "cell": [0, 0]},
                    {"ref": "key", "name": "열쇠", "appearance": "열쇠고리 없는 금속 낱개 하나", "cell": [0, 1], "is_target": True},
                    {"ref": "apple", "name": "사과", "color": "빨강", "appearance": "둥근 낱개 하나", "cell": [1, 0]},
                    {"ref": "glass", "name": "안경", "appearance": "검은 뿔테 하나", "cell": [1, 1]}]}},
            {"stage": "검사", "kind": "edit", "edit_of": "학습",
             "changes": [{"op": "move", "target": "key", "to_cell": [1, 2]}]}]}


def main() -> None:
    slots = {s["id"]: s for s in spec.load_slots()["slots"]}

    print("\n[1] 슬롯 정의")
    check("슬롯 50개", len(slots) == 50, f"{len(slots)}개")
    doms = {}
    for s in slots.values():
        doms[s["domain"]] = doms.get(s["domain"], 0) + 1
    check("영역 5개 x 10개", sorted(doms.values()) == [10] * 5, str(doms))
    check("슬롯 id 유일", len(slots) == len(spec.load_slots()["slots"]))
    bad_stage = [s["id"] for s in slots.values()
                 for p in s["panels"] if p["stage"] not in spec.STAGES]
    check("슬롯 stage가 전부 스키마 enum 안", not bad_stage, str(bad_stage))
    targets = [s["target_answer"] for s in slots.values() if "target_answer" in s]
    check("객관식 목표 정답 21개", len(targets) == 21, str(len(targets)))
    check("목표 정답 분포 5·5·5·6",
          sorted(Counter(targets).values()) == [5, 5, 5, 6], str(Counter(targets)))

    print("\n[2] 정상 문항은 통과해야 한다")
    item = good_item()
    errs = spec.validate_item(item, slots["MEM_03"])
    check("정상 문항 검증 통과", not errs, str(errs))
    scores = spec.score_item(item, slots["MEM_03"])
    check("S6_edit_사용 = 1.0", scores["S6_edit_사용"] == 1.0, str(scores["S6_edit_사용"]))
    check("S1_좌표_완전성 = 1.0", scores["S1_좌표_완전성"] == 1.0)

    print("\n[3] 음성 검사 — 나쁜 출력을 잡아야 한다")
    cases = {
        "ref 중복": lambda it: it["panels"][0]["scene"]["objects"][1].update({"ref": "cup"}),
        "격자 범위 초과": lambda it: it["panels"][0]["scene"]["objects"][0].update({"cell": [5, 0]}),
        "같은 칸 중복": lambda it: it["panels"][0]["scene"]["objects"][1].update({"cell": [0, 0]}),
        "cell 누락": lambda it: it["panels"][0]["scene"]["objects"][0].pop("cell"),
        "없는 ref를 edit": lambda it: it["panels"][1]["changes"][0].update({"target": "nope"}),
        "edit_of가 앞에 없음": lambda it: it["panels"][1].update({"edit_of": "기준"}),
        "모르는 op": lambda it: it["panels"][1]["changes"][0].update({"op": "teleport"}),
        "slot_id 불일치": lambda it: it.update({"slot_id": "MEM_99"}),
        "answer 비었음": lambda it: it.update({"answer": ""}),
        "모르는 최상위 키": lambda it: it.update({"image_spec": "산문으로 쓴 명세"}),
        "검사를 edit 아닌 scene으로": lambda it: it["panels"].__setitem__(1, {
            "stage": "검사", "kind": "scene", "scene": copy.deepcopy(it["panels"][0]["scene"])}),
    }
    for label, mutate in cases.items():
        it = good_item()
        mutate(it)
        errs = spec.validate_item(it, slots["MEM_03"])
        check(f"잡아냄: {label}", bool(errs), "검증을 그냥 통과했다")

    print("\n[4] 룰 채점이 나쁜 명세에 낮은 점수를 줘야 한다")
    it = good_item()
    it["panels"][0]["scene"]["objects"][0]["name"] = "사과 세 개"
    it["panels"][0]["scene"]["background"] = "다른 물건은 넣지 않는다"
    sc = spec.score_item(it, slots["MEM_03"])
    check("S2_개수를_자리로 < 1", sc["S2_개수를_자리로"] < 1.0, str(sc["S2_개수를_자리로"]))
    check("S5_부정문_없음 = 0", sc["S5_부정문_없음"] == 0.0, str(sc["S5_부정문_없음"]))
    it2 = good_item()
    it2["panels"][0]["scene"]["objects"][1]["appearance"] = "열쇠"      # 낱개 표현 제거
    sc2 = spec.score_item(it2, slots["MEM_03"])
    check("S4_낱개_명시 < 1", sc2["S4_낱개_명시"] < 1.0, str(sc2["S4_낱개_명시"]))

    print("\n[5] edit이 동일성을 보장해야 한다 (설계의 핵심 주장)")
    panels = rnd.resolve_panels(good_item())
    a, b = panels[0]["scene"], panels[1]["scene"]
    check("두 패널의 사물 수 같음", len(a["objects"]) == len(b["objects"]))
    index_a = {o["ref"]: o for o in a["objects"]}
    index_b = {o["ref"]: o for o in b["objects"]}
    check("ref 집합 동일", set(index_a) == set(index_b))
    moved = [r for r in index_a if index_a[r] != index_b[r]]
    check("달라진 사물이 정확히 1개", moved == ["key"], str(moved))
    untouched = all(index_a[r] == index_b[r] for r in index_a if r != "key")
    check("나머지 사물은 완전히 동일", untouched)
    check("원본 패널이 변형되지 않음",
          good_item()["panels"][0]["scene"]["objects"][1]["cell"] == [0, 1])

    print("\n[5b] 파생 보기 — 보기 4장이 같은 도형의 변환판이어야 한다")
    vis01 = slots["VIS_01"]
    opt_spec = [p for p in vis01["panels"] if p.get("kind") == "option"][0]
    check("VIS_01 슬롯이 derived", opt_spec.get("derived") is True)
    from . import models as _m
    v = _m.extract_json(_m.StubLLM().generate(spec.build_prompt(vis01)))
    errs = spec.validate_item(v, vis01)
    check("파생 보기 문항이 검증 통과", not errs, str(errs))
    opts = [p for p in v["panels"] if p.get("kind") == "option"]
    check("보기 4장 전부 edit_of를 씀", all("edit_of" in p for p in opts))
    scenes = [p["scene"] for p in rnd.resolve_panels(v) if p.get("kind") == "option"]
    ref_sets = [frozenset(o["ref"] for o in s["objects"]) for s in scenes]
    check("보기 4장의 사물 구성이 동일", len(set(ref_sets)) == 1, str(ref_sets))
    rots = [[o.get("rotation", 0) for o in s["objects"]] for s in scenes]
    check("보기마다 변환은 다름", len({tuple(r) for r in rots}) == len(rots), str(rots))

    # 장면을 새로 쓴 보기는 거부되어야 한다
    bad = json.loads(json.dumps(v))
    for p in bad["panels"]:
        if p.get("kind") == "option":
            p.pop("edit_of", None); p.pop("changes", None)
            p["scene"] = {"layout": "row", "objects": [
                {"ref": "x", "name": "삼각형", "pos": [0.5, 0.5]}]}
    check("보기를 장면 재기술하면 거부", bool(spec.validate_item(bad, vis01)))

    # 보기 전부가 같은 변형이면 거부되어야 한다
    same = json.loads(json.dumps(v))
    first = next(p.get("changes") for p in same["panels"]
                 if p.get("kind") == "option" and p.get("changes"))
    for p in same["panels"]:
        if p.get("kind") == "option":
            p["changes"] = json.loads(json.dumps(first))
    check("보기가 전부 같은 변형이면 거부", bool(spec.validate_item(same, vis01)))

    print("\n[6] 렌더러 — 같은 scene은 같은 픽셀, 다른 scene은 다른 픽셀")
    from PIL import Image
    import io as _io
    def png(scene):
        buf = _io.BytesIO(); rnd.render_compose(scene, 256).save(buf, "PNG"); return buf.getvalue()
    check("동일 scene -> 동일 바이트", png(a) == png(a))
    check("edit된 scene -> 다른 바이트", png(a) != png(b))
    rot = copy.deepcopy(a)
    rot["objects"][0]["rotation"] = 90
    check("회전 반영됨", png(a) != png(rot))

    print("\n[6b] 도형 분류와 자산 파일명")
    from . import shapes
    check("거북선을 선으로 오인하지 않음", shapes.shape_key("거북선") is None)
    check("명시 color가 이름의 예전 색보다 우선",
          shapes.pick_color({"name": "빨간 화살표", "color": "파랑"}) == shapes.COLORS["파랑"])
    check("비대칭 도형은 코드 F형으로 분류", shapes.shape_key("비대칭 도형") == "asym")
    check("직사각형 분류", shapes.shape_key("직사각형") == "rect")
    check("반원 분류", shapes.shape_key("반원") == "semicircle")
    check("사분원 분류", shapes.shape_key("사분원 조각") == "quarter_circle")
    check("정육면체 분류", shapes.shape_key("정육면체") == "cube")
    cube = shapes.render_object({"name": "정육면체",
                                 "appearance": "위면 빨강, 앞면 보라, 오른쪽면 노랑"}, 128)
    check("면별 정육면체 렌더", cube is not None and cube.getbbox() is not None)
    check("공백 자산 파일명 정규화",
          rnd.asset_stem("깨진 달걀의 내용물") == "깨진_달걀의_내용물")

    print("\n[6c] judge 재사용 키")
    # 같은 패널명·렌더러라도 LLM arm이 다르면 별도 결과여야 한다.
    from . import pipeline
    base = {"renderer": "compose", "name": "MEM_01_p01_학습"}
    akey = pipeline.judge_result_key({**base, "llm": "qwen38_27b"})
    bkey = pipeline.judge_result_key({**base, "llm": "mistral_small_24b"})
    check("LLM이 다른 judge 결과는 키가 다름", akey != bkey)
    picked = pipeline.pick_slot_ids(list(slots.values()), "EXE_03,VIS_04")
    check("slot id 지정 순서를 보존", [s["id"] for s in picked] == ["EXE_03", "VIS_04"])

    print("\n[7] 산문 생성")
    prose = rnd.scene_to_prose(a)
    check("격자 문구 포함", f"{a['rows']}행 {a['cols']}열" in prose, prose[:60])
    check("사물 전부 등장", all(o["name"] in prose for o in a["objects"]))
    check("좌표어 포함", any(w in prose for w in ("왼쪽", "오른쪽", "가운데")), prose[:80])
    check("사물마다 자리말이 붙음",
          prose.count("에 ") >= len(a["objects"]), prose[:80])
    check("부정문 없음", not any(w in prose for w in spec.NEGATIONS), prose)

    print("\n[8] 과제명세서 조립")
    prompt = spec.build_prompt(slots["MEM_03"])
    check("스키마 치환됨", "{SCHEMA}" not in prompt and "item_schema" not in prompt[:50])
    check("슬롯 치환됨", "{SLOT}" not in prompt and "MEM_03" in prompt)
    check("edit 규칙 포함", "kind: \"edit\"" in prompt or 'kind": "edit' in prompt)
    check("프롬프트 끝에 임시 입력 없음", "ㄴㅇㄹㄴㅇ" not in prompt)
    from . import models as mdl
    stub = mdl.StubLLM()
    parsed = mdl.extract_json(stub.generate(prompt))
    check("StubLLM이 프롬프트에서 슬롯을 읽어냄", parsed and parsed["slot_id"] == "MEM_03")

    print("\n[9] 50슬롯 전체를 stub으로 통과시켜 본다")
    bad = []
    for sid, slot in slots.items():
        raw = stub.generate(spec.build_prompt(slot))
        it = mdl.extract_json(raw)
        if it is None:
            bad.append((sid, "JSON 파싱 실패")); continue
        errs = spec.validate_item(it, slot)
        if errs:
            bad.append((sid, errs[:2]))
            continue
        try:
            rnd.resolve_panels(it)
        except Exception as exc:
            bad.append((sid, f"패널 해석 실패: {exc}"))
    check(f"50슬롯 전부 생성-검증-해석 통과", not bad, json.dumps(bad[:5], ensure_ascii=False))
    wrong_target = mdl.extract_json(stub.generate(spec.build_prompt(slots["MEM_02"])))
    wrong_target["answer"] = "1"
    target_errs = spec.validate_item(wrong_target, slots["MEM_02"])
    check("목표와 다른 정답 번호를 거부",
          any("target_answer=3" in error for error in target_errs), str(target_errs))

    print("\n[10] 검수 시트 JS 구문")
    from . import sheet
    js = sheet.JS + sheet.PRESENTATION_JS
    for n, line in enumerate(js.split("\n"), 1):
        if line.count("'") % 2 or line.count('"') % 2:
            check(f"JS {n}행 따옴표 짝 맞음", False, repr(line[:70])); break
    else:
        check("JS 모든 행 따옴표 짝 맞음", True)
    check("JS에 날 줄바꿈 리터럴 없음", "\\n" in js and "lines.join('\n')" not in js)

    print("\n[10b] 최종 객관식 정답 위치 재배열")
    choice = {
        "slot_id": "TEST_01",
        "question": "정답을 고르세요.",
        "answer": "1",
        "answer_rationale": "보기 1은 정답, 보기 2·3·4는 오답이라 정답은 1번이다.",
        "panels": [
            {"stage": "기준", "kind": "scene", "payload": "base"},
            *[{"stage": "보기", "kind": "option", "option_index": n,
               "payload": f"old-{n}"} for n in range(1, 5)],
        ],
    }
    balanced, mapping = spec.rebalance_item(choice, 3)
    options = [p for p in balanced["panels"] if p.get("kind") == "option"]
    check("정답 1번을 3번으로 이동", balanced["answer"] == "3")
    check("정답 그림도 3번으로 함께 이동",
          next(p for p in options if p["option_index"] == 3)["payload"] == "old-1")
    check("보기 번호가 1~4 순서를 유지",
          [p["option_index"] for p in options] == [1, 2, 3, 4])
    check("해설의 보기 번호를 동시에 치환",
          balanced["answer_rationale"] ==
          "보기 3은 정답, 보기 2·1·4는 오답이라 정답은 3번이다.",
          balanced["answer_rationale"])
    check("원본 문항은 변형하지 않음",
          choice["answer"] == "1" and mapping == {1: 3, 2: 2, 3: 1, 4: 4})

    print(f"\n{'='*60}\n통과 {len(PASS)} / 실패 {len(FAIL)}")
    if FAIL:
        print("실패 항목:")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("전부 통과")


if __name__ == "__main__":
    main()
