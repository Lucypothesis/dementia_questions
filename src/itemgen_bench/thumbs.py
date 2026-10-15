"""검수용 축소본 만들기.

생성 원본은 768x768 PNG로 장당 평균 388KB다. 1,700장이면 655MB라 저장소에 넣기
어렵다. 시트는 목록에서 96px로 보여주고 클릭했을 때만 크게 띄우므로, 256px JPEG
축소본이면 판별에 충분하면서 전체가 15MB로 줄어든다.

원본을 지우지 않는다. images/ 옆에 thumbs/ 를 만들고 시트만 그쪽을 보게 한다.

  itemgen thumbs --run main --size 256
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import paths


def build(run: Path, size: int = 256, quality: int = 82, force: bool = False) -> dict:
    from PIL import Image

    src_root, dst_root = run / "images", run / "thumbs"
    if not src_root.exists():
        raise SystemExit(f"이미지 폴더가 없다: {src_root}")

    pngs = sorted(src_root.rglob("*.png"))
    made = skipped = 0
    total_src = total_dst = 0
    for src in pngs:
        rel = src.relative_to(src_root).with_suffix(".jpg")
        dst = dst_root / rel
        total_src += src.stat().st_size
        if dst.exists() and not force:
            skipped += 1
            total_dst += dst.stat().st_size
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(src).convert("RGB")
        im.thumbnail((size, size), Image.LANCZOS)
        im.save(dst, "JPEG", quality=quality, optimize=True)
        made += 1
        total_dst += dst.stat().st_size
    return {"n": len(pngs), "made": made, "skipped": skipped,
            "src_mb": total_src / 1e6, "dst_mb": total_dst / 1e6}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="검수용 축소본 생성")
    ap.add_argument("--run", default="main")
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--quality", type=int, default=82)
    ap.add_argument("--out", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    run = paths.out_dir(args.out) / args.run
    r = build(run, args.size, args.quality, args.force)
    print(f"{r['n']}장 · 생성 {r['made']} / 재사용 {r['skipped']}")
    print(f"원본 {r['src_mb']:.0f}MB -> 축소본 {r['dst_mb']:.0f}MB "
          f"({r['dst_mb']/max(r['src_mb'],1e-9)*100:.1f}%)")
    print(f"위치: {run / 'thumbs'}")
