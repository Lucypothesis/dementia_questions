"""compose 렌더러가 쓸 사물 그림(자산)을 만든다.

디퓨전에게 장면 전체를 그리게 하면 개수·배치·회전이 틀어진다. 대신 '사물 하나'만
그리게 하고 배치는 코드가 한다. 디퓨전이 가장 잘하는 일만 시키는 것이다.

같은 사물은 같은 파일을 재사용하므로 패널 간 동일성이 바이트 단위로 보장된다.
학습/검사처럼 같은 장면이 반복되는 문항에서 이게 결정적이다.

  itemgen assets --list              필요한 사물 목록만 뽑는다
  CUDA_VISIBLE_DEVICES=2 itemgen assets --build   실제로 생성한다
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

from . import paths, shapes

PROMPT = (
    "깔끔한 인지검사 자극 그림. 평면 벡터 스타일, 굵고 단순한 검은 외곽선, "
    "선명한 단색, 완전한 순백색 배경, 균일한 조명, 그림자 없음, 원근 왜곡 없음. "
    "화면 가운데에 {name} 낱개 하나만 크게. 다른 사물·글자·배경 요소는 전혀 없다."
)
NEG = "여러 개, 배경, 그림자, 글자, 숫자, 워터마크, 사람 손, 테이블, 잘림"

# 짧은 한국어 명칭만으로는 동음이의어를 잘못 그릴 수 있다. 필요한 경우 모델에
# 전달할 시각 설명을 구체화한다. 파일명과 문항의 사물명은 원래 이름을 유지한다.
ASSET_DESCRIPTIONS = {
    "거북선": ("조선시대의 철갑 전함 거북선 한 척. 낮고 긴 목조 선체, 용머리 모양의 "
               "선수, 등판 같은 둥근 철갑 지붕 위에 여러 개의 뾰족한 쇠못. "
               "동물 거북이가 아니라 역사적인 군함"),
}


def wanted(run: Path, exclude_shapes: bool = True) -> Counter:
    """문항들이 요구하는 실물 사물 목록. 코드로 그릴 수 있는 도형은 뺀다."""
    c: Counter = Counter()
    for f in glob.glob(str(run / "items" / "*" / "*.json")):
        try:
            it = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for p in it.get("panels", []):
            for o in (p.get("scene") or {}).get("objects", []):
                name = (o.get("name") or "").strip()
                if not name:
                    continue
                if exclude_shapes and shapes.shape_key(name):
                    continue
                c[name] += 1
    return c


def safe(name: str) -> str:
    from .render import asset_stem
    return asset_stem(name)


def cutout(img, thresh: int = 242):
    """흰 배경을 투명으로. 프롬프트가 순백 배경을 요구하므로 임계값으로 충분하다.
    rembg/SAM 같은 추가 의존성을 피한다."""
    from PIL import Image
    im = img.convert("RGBA")
    px = im.load()
    w, h = im.size
    # 테두리에서 흘러드는 영역만 지운다. 사물 안쪽의 흰색은 남긴다.
    seen = set()
    stack = [(x, y) for x in range(w) for y in (0, h - 1)] + \
            [(x, y) for y in range(h) for x in (0, w - 1)]
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or not (0 <= x < w and 0 <= y < h):
            continue
        r, g, b, a = px[x, y]
        if r < thresh or g < thresh or b < thresh:
            continue
        seen.add((x, y))
        px[x, y] = (r, g, b, 0)
        stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
    return im


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="compose용 사물 자산 생성")
    ap.add_argument("--run", default="main")
    ap.add_argument("--out", default=None)
    ap.add_argument("--assets", default=None, help="자산 폴더 (기본 저장소/assets)")
    ap.add_argument("--only", default=None, help="이 사물만 생성 (쉼표 구분)")
    ap.add_argument("--list", action="store_true", help="목록만 보고 끝낸다")
    ap.add_argument("--build", action="store_true", help="실제로 생성한다 (GPU 필요)")
    ap.add_argument("--min-count", type=int, default=1, help="이 횟수 이상 등장한 사물만")
    ap.add_argument("--model", default="black-forest-labs/FLUX.2-klein-4B")
    ap.add_argument("--pipeline", default="Flux2KleinPipeline")
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--guidance", type=float, default=1.0)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    run = paths.out_dir(args.out) / args.run
    adir = Path(args.assets) if args.assets else paths.assets_dir()
    names = wanted(run)
    names = Counter({k: v for k, v in names.items() if v >= args.min_count})
    if args.only:
        selected = {x.strip() for x in args.only.split(",") if x.strip()}
        unknown = selected - set(names)
        if unknown:
            raise SystemExit(f"문항에 없는 사물: {sorted(unknown)}")
        names = Counter({k: v for k, v in names.items() if k in selected})

    exists = {n: any((adir / f"{safe(n)}{e}").exists()
                     for e in (".png", ".webp", ".jpg")) for n in names}
    todo = [n for n in names if args.force or not exists[n]]
    print(f"필요한 실물 {len(names)}종 · 이미 있는 것 {sum(exists.values())} · "
          f"생성 대상 {len(todo)}")
    if args.list or not args.build:
        for n, c in names.most_common():
            mark = " " if n in todo else "O"
            print(f"  {mark} {n}  ({c}회)")
        if not args.build:
            print("\n실제로 만들려면 --build (GPU 필요)")
        return

    if not todo:
        print("만들 것이 없다.")
        return

    import torch
    import diffusers
    adir.mkdir(parents=True, exist_ok=True)
    cls = getattr(diffusers, args.pipeline)
    print(f"모델 로딩: {args.model}")
    pipe = cls.from_pretrained(args.model, torch_dtype=torch.bfloat16).to("cuda")

    import hashlib
    import time
    t0 = time.time()
    # 파이프라인마다 받는 인자가 다르다. klein은 negative_prompt를 안 받는다.
    import inspect
    takes = inspect.signature(pipe.__call__).parameters
    for i, name in enumerate(todo, 1):
        seed = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
        description = ASSET_DESCRIPTIONS.get(name, name)
        kw = {"prompt": PROMPT.format(name=description),
              "num_inference_steps": args.steps,
              "height": args.size, "width": args.size,
              "generator": torch.Generator("cuda").manual_seed(seed)}
        if "negative_prompt" in takes:
            kw["negative_prompt"] = NEG
        for cfg in ("guidance_scale", "true_cfg_scale"):
            if cfg in takes:
                kw[cfg] = args.guidance
                break
        img = pipe(**kw).images[0]
        cutout(img).save(adir / f"{safe(name)}.png")
        per = (time.time() - t0) / i
        print(f"  {i}/{len(todo)} {name}  ({per:.1f}초/개, 남은 ≈ {per*(len(todo)-i)/60:.0f}분)")
    print(f"\n자산 {len(todo)}개 -> {adir}")
