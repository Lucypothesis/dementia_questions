"""end-to-end 오케스트레이터.

  1 gen    슬롯 50개 x LLM N개 -> 문항 JSON
  2 check  스키마 검증 + 룰 채점           (측정 1: LLM만의 능력)
  3 render 합격 문항 x 렌더러 D개 -> 이미지
  4 judge  명세-이미지 대조 자동 채점       (측정 2: 렌더러 능력)
  5 sheet  사람 검수용 N-way 시트

단계마다 out/<run>/ 아래에 결과를 남기고, 이미 있으면 건너뛴다. 중간에 끊겨도
이어서 돌 수 있고 비싼 단계를 두 번 돌리지 않는다.

  python src/pipeline.py --run pilot --slots 10 --stages gen,check
  python src/pipeline.py --run pilot --stages render,judge,sheet
  python src/pipeline.py --run pilot --force render
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import os
import traceback
import warnings
from pathlib import Path

# /workspace/VLMs/.venv의 requests 2.28은 urllib3 1.26과 버전표가 어긋나 매번 경고를 뱉는다.
# 실제 호출에는 영향이 없고, 공유 venv를 손대면 test_012가 깨질 수 있으므로 여기서만 끈다.
warnings.filterwarnings("ignore", message=".*doesn't match a supported version.*")

from . import models
from . import paths
from . import render as rnd
from . import spec
ALL_STAGES = ["plan", "gen", "check", "render", "judge", "sheet"]
SPEC_DIR = None   # main에서 --spec-dir 로 채운다


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def pick_slots(slots: list[dict], limit: int | None) -> list[dict]:
    """영역별로 고르게 n개씩 뽑는다. 파일럿에서 한 영역만 보게 되는 걸 막는다."""
    if not limit:
        return slots
    per, out, seen = {}, [], {}
    for slot in slots:
        d = slot["domain"]
        seen[d] = seen.get(d, 0)
    quota = max(1, limit // max(len(seen), 1))
    for slot in slots:
        d = slot["domain"]
        if per.get(d, 0) < quota:
            per[d] = per.get(d, 0) + 1
            out.append(slot)
    return out[:limit] if limit < len(out) else out


# ----------------------------------------------------------------- 1 gen

def stage_gen(run: Path, slots: list[dict], cfg: dict, force: bool) -> None:
    for entry in cfg["llms"]:
        name = entry["name"]
        # readonly arm은 '보존된 결과'다. 모델을 호출하지 않는다. 다른 설정으로 이미
        # 받아둔 비교 조건을 실수로 덮어쓰지 않게 막는다. check·render·sheet에는 그대로 나온다.
        if entry.get("readonly"):
            have = len(list((run / "items" / name).glob("*.json")))
            log(f"gen[{name}] 건너뜀 — readonly 보존 arm (문항 {have}개)")
            continue
        out = run / "items" / name
        out.mkdir(parents=True, exist_ok=True)
        llm = models.build("llm", entry)
        made = skipped = unparsed = errored = 0
        t_start, done = time.time(), 0
        todo = sum(1 for s in slots if not (out / f"{s['id']}.json").exists() or force)
        log(f"gen[{name}] 시작 — 돌릴 슬롯 {todo}/{len(slots)}")
        for slot in slots:
            path = out / f"{slot['id']}.json"
            if path.exists() and not force:
                skipped += 1
                continue
            prompt = spec.build_prompt(slot, SPEC_DIR)
            t0 = time.time()
            try:
                raw = llm.generate(prompt)
            except Exception as exc:
                errored += 1
                result = f"호출실패 {type(exc).__name__}"
                (out / f"{slot['id']}.error.txt").write_text(
                    f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}", encoding="utf-8")
            else:
                item = models.extract_json(raw)
                if item is None:
                    unparsed += 1
                    result = f"파싱실패 ({len(raw):,}자)"
                    (out / f"{slot['id']}.raw.txt").write_text(raw, encoding="utf-8")
                else:
                    made += 1
                    result = "OK"
                    path.write_text(json.dumps(item, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
            # 슬롯 하나가 끝날 때마다 남긴다. arm이 끝나야 한 줄 찍으면 그 사이가 깜깜하다.
            done += 1
            per = (time.time() - t_start) / done
            eta = per * (todo - done)
            log(f"  gen[{name}] {done}/{todo} {slot['id']:8} {result:22}"
                f" {time.time()-t0:5.1f}초 · 누적 OK {made} 파싱실패 {unparsed} 호출실패 {errored}"
                f" · 남은 ≈ {eta/60:.0f}분")
        log(f"gen[{name}] 생성 {made} / 재사용 {skipped} / "
            f"파싱실패 {unparsed} / 호출실패 {errored}")


# --------------------------------------------------------------- 2 check

def stage_check(run: Path, slots: list[dict], cfg: dict, force: bool) -> None:
    by_id = {s["id"]: s for s in slots}
    report = {}
    for entry in cfg["llms"]:
        name = entry["name"]
        src = run / "items" / name
        rows = []
        for slot in slots:
            path = src / f"{slot['id']}.json"
            if not path.exists():
                rows.append({"slot_id": slot["id"], "parsed": False,
                             "errors": ["출력 파일이 없다"], "scores": None})
                continue
            item = json.loads(path.read_text(encoding="utf-8"))
            errs = spec.validate_item(item, by_id[slot["id"]])
            scores = spec.score_item(item, by_id[slot["id"]]) if not errs else None
            rows.append({"slot_id": slot["id"], "parsed": True,
                         "valid": not errs, "errors": errs, "scores": scores})
        valid = [r for r in rows if r.get("valid")]
        agg = {}
        if valid:
            keys = [k for k in valid[0]["scores"] if k != "총점"]
            for k in keys:
                vals = [r["scores"][k] for r in valid if r["scores"].get(k) is not None]
                agg[k] = round(sum(vals) / len(vals), 3) if vals else None
            agg["총점"] = round(sum(r["scores"]["총점"] for r in valid) / len(valid), 3)
        report[name] = {"n_slots": len(slots), "parsed": sum(r["parsed"] for r in rows),
                        "valid": len(valid), "valid_rate": round(len(valid) / len(slots), 3),
                        "scores": agg, "rows": rows}
        log(f"check[{name}] 스키마 합격 {len(valid)}/{len(slots)} "
            f"({len(valid)/len(slots):.0%}) · 룰 총점 {agg.get('총점')}")
    path = run / "check.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"check 보고서 -> {path}")


# -------------------------------------------------------------- 3 render

def stage_render(run: Path, slots: list[dict], cfg: dict, force: bool) -> None:
    index = []
    # 렌더러를 바깥 고리에 둔다. 안쪽에 두면 LLM arm마다 모델을 다시 올려서
    # 20B 디퓨전은 로딩에만 수십 분이 샌다.
    for rcfg in cfg["renderers"]:
        renderer = models.build("renderer", rcfg)
        rname = rcfg["name"]
        for entry in cfg["llms"]:
            llm_name = entry["name"]
            out = run / "images" / llm_name / rname
            out.mkdir(parents=True, exist_ok=True)
            made = skipped = failed = 0
            t_start = time.time()
            for slot in slots:
                path = run / "items" / llm_name / f"{slot['id']}.json"
                if not path.exists():
                    continue
                item = json.loads(path.read_text(encoding="utf-8"))
                if spec.validate_item(item, slot):
                    continue                     # 스키마 불합격은 렌더하지 않는다
                try:
                    panels = rnd.resolve_panels(item)
                except Exception as exc:
                    failed += 1
                    log(f"  render[{llm_name}/{rname}] {slot['id']} 패널 해석 실패: {exc}")
                    continue
                for k, panel in enumerate(panels, start=1):
                    name = rnd.panel_name(slot["id"], k, panel)
                    img = out / f"{name}.png"
                    rec = {"llm": llm_name, "renderer": rname, "slot_id": slot["id"],
                           "panel_index": k, "stage": panel["stage"],
                           "option_index": panel.get("option_index"),
                           "name": name, "image": str(img.relative_to(run)),
                           "prose": rnd.scene_to_prose(panel["scene"]),
                           "n_objects": len(panel["scene"]["objects"])}
                    index.append(rec)
                    if img.exists() and not force:
                        skipped += 1
                        continue
                    try:
                        renderer.render(panel, img, rnd.panel_seed(name))
                        made += 1
                        # 10장마다 진행을 남긴다. 장당 수십 초라 한 장씩은 너무 시끄럽다.
                        if made % 10 == 0:
                            per = (time.time() - t_start) / made
                            log(f"  render[{llm_name}/{rname}] {made}장 · "
                                f"장당 {per:.1f}초 · 최근 {name}")
                    except Exception as exc:
                        failed += 1
                        log(f"  render[{llm_name}/{rname}] {name} 실패: {type(exc).__name__}: {exc}")
            log(f"render[{llm_name}/{rname}] 생성 {made} / 재사용 {skipped} / 실패 {failed}")
    # run_all.sh는 렌더러를 하나씩 따로 돌린다(--renderers). 그때마다 색인을 통째로
    # 덮어쓰면 마지막 렌더러 것만 남아 judge·sheet가 나머지를 못 본다. 실제로 1,668장을
    # 그려놓고 색인에는 426장만 남아 있었다. 기존 색인과 합친다.
    path = run / "panels.json"
    merged = {}
    if path.exists():
        for rec in json.loads(path.read_text(encoding="utf-8")):
            merged[(rec["llm"], rec["renderer"], rec["name"])] = rec
    for rec in index:
        merged[(rec["llm"], rec["renderer"], rec["name"])] = rec
    # 이미지가 실제로 있는 것만 남긴다 (지웠거나 실패한 칸을 들고 있지 않게).
    rows = [r for r in merged.values() if (run / r["image"]).exists()]
    rows.sort(key=lambda r: (r["renderer"], r["llm"], r["slot_id"], r["panel_index"]))
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"패널 색인 {len(rows)}건 (이번 {len(index)}건 병합) -> {path}")


# --------------------------------------------------------------- 4 judge

def stage_judge(run: Path, slots: list[dict], cfg: dict, force: bool) -> None:
    panels = json.loads((run / "panels.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in slots}
    out_path = run / "judge.json"
    done = {}
    if out_path.exists() and not force:
        done = {r["name"] + "|" + r["renderer"]: r
                for r in json.loads(out_path.read_text(encoding="utf-8"))}
    judge = models.build("judge", cfg["judge"])
    todo = [r for r in panels if (r["name"] + "|" + r["renderer"]) not in done]
    rows = [done[r["name"] + "|" + r["renderer"]] for r in panels
            if (r["name"] + "|" + r["renderer"]) in done]
    log(f"judge[{cfg['judge'].get('name')}] 채점 대상 {len(todo)} / 재사용 {len(rows)}")

    def one(rec):
        item = json.loads((run / "items" / rec["llm"] / f"{rec['slot_id']}.json")
                          .read_text(encoding="utf-8"))
        panel = rnd.resolve_panels(item)[rec["panel_index"] - 1]
        img = run / rec["image"]
        try:
            v = judge.judge(panel, img) if img.exists() else {"usable": None, "why": "이미지 없음"}
        except Exception as exc:
            v = {"usable": None, "why": f"{type(exc).__name__}: {exc}"}
        return {**rec, "verdict": v}

    # API judge는 네트워크 대기가 대부분이라 병렬이 그대로 이득이다. stub은 1로 둔다.
    workers = int(cfg["judge"].get("workers", 8)) if cfg["judge"].get("adapter") != "stub" else 1
    fresh = 0
    if todo:
        from concurrent.futures import ThreadPoolExecutor
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for r in pool.map(one, todo):
                rows.append(r); fresh += 1
                # 중간에 끊겨도 잃지 않게 주기적으로 저장한다. 1,600장을 다시 돌리면 비싸다.
                if fresh % 100 == 0:
                    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
                    per = (time.time() - t0) / fresh
                    log(f"  judge {fresh}/{len(todo)} · 장당 {per:.2f}초 · "
                        f"남은 ≈ {per*(len(todo)-fresh)/60:.0f}분")
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"judge 신규 {fresh} / 재사용 {len(rows)-fresh}")
    for rname in sorted({r["renderer"] for r in rows}):
        for lname in sorted({r["llm"] for r in rows}):
            sub = [r for r in rows if r["renderer"] == rname and r["llm"] == lname]
            if not sub:
                continue
            ok = sum(1 for r in sub if r["verdict"].get("usable") is True)
            log(f"  {lname:12} x {rname:10} 패널 사용가능 {ok}/{len(sub)} ({ok/len(sub):.0%})")


# --------------------------------------------------------------- 5 sheet

def stage_sheet(run: Path, slots: list[dict], cfg: dict, force: bool) -> None:
    from . import sheet
    # 이미지를 base64로 박으면 26MB가 된다. HTTP로 띄워 볼 거면 박을 이유가 없다.
    # 브라우저가 images/ 아래 파일을 느긋하게 받아가는 쪽이 훨씬 빠르다.
    # SHEET_EMBED=1 을 주면 파일 하나로 떨어지는 옛 방식(공유용)으로 만든다.
    embed = os.environ.get("SHEET_EMBED", "") == "1"
    out = sheet.build(run, slots, embed=embed)
    log(f"검수 시트 -> {out} ({out.stat().st_size/1e6:.1f}MB, "
        f"{'이미지 내장' if embed else '이미지 참조'})")


# ---------------------------------------------------------------- 0 plan

def stage_plan(run: Path, slots: list[dict], cfg: dict, force: bool) -> None:
    """돈과 GPU를 쓰기 전에 규모를 계산해 보여준다. 아무것도 실행하지 않는다."""
    est = cfg.get("estimates", {}) or {}
    llms = [e["name"] for e in cfg["llms"]]
    rends = [r["name"] for r in cfg["renderers"]]

    # 패널 수는 슬롯 구조가 정한다 (모델이 바꿀 수 없다).
    per_slot = {}
    for s in slots:
        n = sum(p.get("n", 1) if p.get("kind") == "option" else 1 for p in s["panels"])
        per_slot[s["id"]] = n
    panels = sum(per_slot.values())

    print("\n" + "=" * 66)
    print(f"슬롯 {len(slots)}개 · LLM {len(llms)}개 · 렌더러 {len(rends)}개")
    print(f"LLM arm:    {', '.join(llms)}")
    print(f"렌더러 arm: {', '.join(rends)}")
    print("=" * 66)

    print(f"\n[문항 생성] LLM 호출 {len(slots)} x {len(llms)} = {len(slots)*len(llms)}회")
    inp = sum(len(spec.build_prompt(s, SPEC_DIR)) for s in slots) / 4
    out = inp * 0.25                       # 출력은 입력의 1/4 안팎 (stub 실측 기반)
    total_cost = 0.0
    for name in llms:
        e = est.get(name, {})
        if "price_in" in e:
            c = inp / 1e6 * e["price_in"] + out / 1e6 * e["price_out"]
            total_cost += c
            print(f"  {name:18} ${c:6.2f}   (입력 {inp/1000:.0f}k · 출력 {out/1000:.0f}k 토큰)")
        else:
            print(f"  {name:18} 로컬 (API 비용 없음)")
    print(f"  {'합계':18} ${total_cost:6.2f}")

    print(f"\n[렌더] 패널 {panels}장 x LLM {len(llms)} = {panels*len(llms)}장 / 렌더러")
    total_sec = 0.0
    for name in rends:
        sec = est.get(name, {}).get("sec_per_image")
        n = panels * len(llms)
        if sec:
            t = n * sec
            total_sec += t
            print(f"  {name:18} {n:5}장  약 {t/3600:5.1f}시간  ({sec}초/장 가정)")
        else:
            print(f"  {name:18} {n:5}장  (속도 추정값 없음)")
    print(f"  {'합계':18} {sum(panels*len(llms) for _ in rends):5}장  약 {total_sec/3600:5.1f}시간")

    cells = len(slots) * len(llms) * len(rends)
    print(f"\n[검수] 시트 칸 {len(slots)} x {len(llms)} x {len(rends)} = {cells}칸")
    print(f"  칸당 30초로 봐도 사람 전수 검수는 약 {cells*30/3600:.1f}시간이다.")
    print(f"  judge를 사람 표본과 맞춘 뒤 자동 채점에 맡기고, 사람은 표본만 본다.")

    print(f"\n[영역별 패널]")
    by_dom = {}
    for s in slots:
        by_dom[s["domain"]] = by_dom.get(s["domain"], 0) + per_slot[s["id"]]
    for d, n in sorted(by_dom.items(), key=lambda x: -x[1]):
        print(f"  {d:8} {n:3}장")
    print()


STAGE_FN = {"plan": stage_plan, "gen": stage_gen, "check": stage_check,
            "render": stage_render, "judge": stage_judge, "sheet": stage_sheet}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="pilot", help="out/ 아래 실행 이름")
    ap.add_argument("--slots", type=int, default=None,
                    help="영역별로 고르게 이만큼만 쓴다 (파일럿용). 생략하면 50개 전부")
    ap.add_argument("--stages", default="gen,check,render,judge,sheet")
    ap.add_argument("--force", default="", help="이 단계는 결과가 있어도 다시 돈다")
    ap.add_argument("--config", default=None,
                    help="models.yaml 경로. 생략하면 현재 디렉터리 config/ 다음 저장소 config/")
    ap.add_argument("--spec-dir", default=None,
                    help="task_spec.md·slots.json이 있는 폴더. 생략하면 현재 디렉터리 spec/ 다음 저장소 spec/")
    ap.add_argument("--out", default=None, help="결과를 쌓을 폴더. 생략하면 ./out")
    ap.add_argument("--llms", default="", help="이 LLM arm만 쓴다 (쉼표 구분). 생략하면 전부")
    ap.add_argument("--renderers", default="", help="이 렌더러 arm만 쓴다 (쉼표 구분). 생략하면 전부")
    args = ap.parse_args()

    global SPEC_DIR
    SPEC_DIR = args.spec_dir
    cfg = models.load_config(args.config)
    for key, flag in (("llms", args.llms), ("renderers", args.renderers)):
        if not flag:
            continue
        want = [x.strip() for x in flag.split(",") if x.strip()]
        have = {e["name"] for e in cfg[key]}
        unknown = set(want) - have
        if unknown:
            raise SystemExit(f"{key}에 없는 이름: {sorted(unknown)} (있는 것: {sorted(have)})")
        cfg[key] = [e for e in cfg[key] if e["name"] in want]
    slots = pick_slots(spec.load_slots(args.spec_dir)["slots"], args.slots)
    run = paths.out_dir(args.out) / args.run
    run.mkdir(parents=True, exist_ok=True)

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    forced = {s.strip() for s in args.force.split(",") if s.strip()}
    for s in stages:
        if s not in STAGE_FN:
            raise SystemExit(f"모르는 단계: {s!r} (가능: {ALL_STAGES})")

    log(f"run={args.run} 슬롯 {len(slots)}개 "
        f"LLM {len(cfg['llms'])}개 렌더러 {len(cfg['renderers'])}개 단계 {stages}")
    (run / "run.json").write_text(json.dumps(
        {"slots": [s["id"] for s in slots], "config": cfg,
         "built": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    for s in stages:
        STAGE_FN[s](run, slots, cfg, s in forced)
    log("완료")


if __name__ == "__main__":
    main()
