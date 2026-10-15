"""구조화된 scene을 (1) 산문 프롬프트와 (2) 실제 이미지로 바꾼다.

패널 해석이 핵심이다. kind=edit 패널은 앞 패널의 scene을 깊은 복사한 뒤 changes만
적용한다. 그래서 두 패널은 변경한 사물 말고는 완전히 같아진다 — 생성 모델에게
"똑같이 그려달라"고 부탁하지 않는다.

렌더러는 두 종류다.
  compose  : 자산 이미지를 좌표에 붙인다. 결정론적. 회전·반전·격자가 정확하다.
  diffusion: 산문 프롬프트를 T2I에 넣는다. 자연스럽지만 통제가 안 된다.
둘을 같은 scene에 돌려 비교하는 것이 이 벤치마크의 한 축이다.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from . import paths

# 자산(사물 그림)은 compose 렌더러만 쓴다. 없으면 자리표시로 그린다.
ASSETS = paths.REPO_ROOT / "assets"

STAGE_HINT = {
    "학습": "먼저 제시하는 학습 이미지다.",
    "검사": "학습 뒤에 제시하는 검사 이미지다.",
    "기준": "보기와 비교할 기준 이미지다.",
    "보기": "보기 카드 한 장이다.",
    "검사 보기": "검사 단계의 보기 카드 한 장이다.",
    "인물 보기": "인물이 나오는 보기 카드 한 장이다.",
    "main": "문항의 단일 자극 이미지다.",
}

STYLE_KO = (
    "깔끔한 인지검사 자극 그림. 평면 벡터 스타일, 굵고 단순한 외곽선, "
    "선명하게 구분되는 색, 완전한 흰 배경, 균일한 조명, 그림자 없음, 원근 왜곡 없음. "
    "모든 사물이 가려지지 않고 서로 떨어져 있으며, 가운데 배치, 여백 넉넉하게."
)
NO_TEXT_KO = "글자·숫자·한글·라벨·캡션·워터마크를 전혀 넣지 않는다."

ORDINAL = ["첫째 줄", "둘째 줄", "셋째 줄", "넷째 줄", "다섯째 줄", "여섯째 줄"]
COLUMNAL = ["첫째 칸", "둘째 칸", "셋째 칸", "넷째 칸", "다섯째 칸", "여섯째 칸"]


# ------------------------------------------------------------ 패널 해석

def apply_changes(scene: dict, changes: list[dict]) -> dict:
    """scene을 복제하고 changes만 적용한다. 원본은 건드리지 않는다."""
    out = copy.deepcopy(scene)
    index = {o["ref"]: o for o in out["objects"] if isinstance(o, dict) and "ref" in o}
    for ch in changes:
        op, target = ch.get("op"), ch.get("target")
        if op == "add":
            out["objects"].append(copy.deepcopy(ch["object"]))
            continue
        obj = index.get(target)
        if obj is None:
            raise KeyError(f"changes의 target {target!r}가 장면에 없다")
        if op == "remove":
            out["objects"] = [o for o in out["objects"] if o.get("ref") != target]
        elif op == "move":
            if "to_cell" in ch:
                obj["cell"] = list(ch["to_cell"]); obj.pop("pos", None)
            elif "to_pos" in ch:
                obj["pos"] = list(ch["to_pos"]); obj.pop("cell", None)
            else:
                raise ValueError("op=move인데 to_cell/to_pos가 없다")
        elif op == "recolor":
            obj["color"] = ch["to_color"]
        elif op == "rotate":
            obj["rotation"] = ch.get("to_rotation", 0)
        elif op == "rescale":
            obj["scale"] = ch.get("to_scale", 1.0)
        elif op == "mirror":
            obj["mirrored"] = not obj.get("mirrored", False)
        else:
            raise ValueError(f"모르는 op: {op!r}")
    return out


def resolve_panels(item: dict) -> list[dict]:
    """edit 패널을 실제 scene으로 풀어 모든 패널에 scene이 들어 있게 만든다."""
    out, by_stage = [], {}
    for panel in item["panels"]:
        panel = dict(panel)
        # edit 패널과 '파생 보기'가 같은 경로를 탄다. 보기는 changes가 비면 기준 그대로(정답 보기).
        if panel.get("kind") == "edit" or (panel.get("kind") == "option" and "edit_of" in panel):
            base = by_stage.get(panel["edit_of"])
            if base is None:
                raise KeyError(f"edit_of={panel['edit_of']!r} 패널을 앞에서 못 찾았다")
            panel["scene"] = apply_changes(base, panel.get("changes") or [])
        scene = panel.get("scene")
        if scene is None:
            raise ValueError(f"{panel.get('stage')!r} 패널에 scene이 없다")
        by_stage.setdefault(panel["stage"], scene)
        out.append(panel)
    return out


def panel_name(item_id: str, index: int, panel: dict) -> str:
    stage = str(panel.get("stage", "main")).replace(" ", "")
    opt = panel.get("option_index")
    tail = f"_opt{opt}" if opt else ""
    return f"{item_id}_p{index:02d}_{stage}{tail}"


# ------------------------------------------------------------ 산문 생성

def _where(scene: dict, obj: dict) -> str:
    if "cell" in obj and scene.get("layout") == "grid":
        r, c = obj["cell"]
        rows, cols = scene.get("rows", 1), scene.get("cols", 1)
        if rows == 1:
            return COLUMNAL[c] if c < len(COLUMNAL) else f"{c+1}번째 칸"
        if cols == 1:
            return ORDINAL[r] if r < len(ORDINAL) else f"{r+1}번째 줄"
        vert = ["위", "가운데", "아래"][0 if r == 0 else (2 if r == rows - 1 else 1)] if rows <= 3 else f"{r+1}행"
        horz = ["왼쪽", "가운데", "오른쪽"][0 if c == 0 else (2 if c == cols - 1 else 1)] if cols <= 3 else f"{c+1}열"
        return f"{horz} {vert}"
    if "pos" in obj:
        x, y = obj["pos"]
        horz = "왼쪽" if x < 0.34 else ("오른쪽" if x > 0.66 else "가운데")
        vert = "위" if y < 0.34 else ("아래" if y > 0.66 else "중간")
        return f"{horz} {vert}"
    return "가운데"


def describe_object(scene: dict, obj: dict) -> str:
    bits = []
    if obj.get("color"):
        bits.append(obj["color"])
    if obj.get("appearance"):
        bits.append(obj["appearance"])
    desc = " ".join(bits)
    out = f"{_where(scene, obj)}에 {desc} {obj['name']} 하나".replace("  ", " ")
    extra = []
    if obj.get("rotation"):
        extra.append(f"시계 방향 {obj['rotation']}도 돌아간 상태")
    if obj.get("mirrored"):
        extra.append("좌우가 뒤집힌 상태")
    if obj.get("facing"):
        extra.append(f"{obj['facing']}을 향한 상태")
    if obj.get("scale") and obj["scale"] != 1.0:
        extra.append("크게" if obj["scale"] > 1 else "작게")
    return out + (f" ({', '.join(extra)})" if extra else "")


def scene_to_prose(scene: dict) -> str:
    """scene을 한국어 산문으로 바꾼다. 모든 모델에 같은 템플릿이 적용되므로
    산문 문체가 모델 간 교란변수가 되지 않는다."""
    head = ""
    if scene.get("layout") == "grid":
        head = f"사물을 {scene.get('rows')}행 {scene.get('cols')}열 격자로 놓는다. "
    elif scene.get("layout") == "row":
        head = "사물을 한 줄로 나란히 놓는다. "
    body = ". ".join(describe_object(scene, o) for o in scene["objects"]) + "."
    tail = f" 배경은 {scene.get('background', '흰 바닥')}이다."
    sep = " 사물끼리 닿지 않고 넉넉히 떨어져 있다."
    return head + body + sep + tail


def build_diffusion_prompt(panel: dict) -> str:
    parts = [STYLE_KO,
             STAGE_HINT.get(panel.get("stage"), STAGE_HINT["main"]),
             f"내용: {scene_to_prose(panel['scene'])}",
             NO_TEXT_KO]
    return "\n\n".join(parts)


def panel_seed(name: str) -> int:
    """패널 이름으로 결정론적 시드를 만든다. 같은 패널은 늘 같은 그림이 나온다."""
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)


# ------------------------------------------------------------ 합성 렌더러

_FONT_PATHS = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumSquareR.ttf",
]


def _font(size: int):
    """한글을 그릴 수 있는 폰트. 없으면 기본 폰트로 떨어진다(한글은 깨진다)."""
    from PIL import ImageFont
    for path in _FONT_PATHS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _asset(name: str):
    """자산 은행에서 사물 그림을 찾는다. 없으면 None."""
    from PIL import Image
    for ext in (".png", ".webp", ".jpg"):
        path = ASSETS / f"{name}{ext}"
        if path.exists():
            return Image.open(path).convert("RGBA")
    return None


def render_compose(scene: dict, size: int = 768):
    """자산을 좌표에 붙여 그린다. 자산이 없는 사물은 이름을 적은 자리표시로 둔다.

    회전·반전·격자·개수가 코드로 결정되므로 명세와 100% 일치한다. 이것이
    diffusion 렌더러의 대조군이다.
    """
    from PIL import Image, ImageDraw

    canvas = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(canvas)
    rows = scene.get("rows") or 1
    cols = scene.get("cols") or (len(scene["objects"]) if scene.get("layout") == "row" else 1)
    pad = int(size * 0.06)
    cell_w = (size - 2 * pad) / max(cols, 1)
    cell_h = (size - 2 * pad) / max(rows, 1)

    for k, obj in enumerate(scene["objects"]):
        if "cell" in obj:
            r, c = obj["cell"]
        elif scene.get("layout") == "row":
            r, c = 0, k
        elif "pos" in obj:
            r = c = None
        else:
            r, c = 0, k % max(cols, 1)

        if r is None:
            cx, cy = obj["pos"][0] * size, obj["pos"][1] * size
        else:
            cx = pad + cell_w * (c + 0.5)
            cy = pad + cell_h * (r + 0.5)

        box = int(min(cell_w, cell_h) * 0.78 * float(obj.get("scale", 1.0)))
        art = _asset(obj["name"])
        if art is not None:
            art = art.resize((box, box), Image.LANCZOS)
            if obj.get("mirrored"):
                art = art.transpose(Image.FLIP_LEFT_RIGHT)
            if obj.get("rotation"):
                art = art.rotate(-int(obj["rotation"]), expand=True, resample=Image.BICUBIC)
            canvas.paste(art, (int(cx - art.width / 2), int(cy - art.height / 2)), art)
        else:
            half = box // 2
            draw.rounded_rectangle([cx - half, cy - half, cx + half, cy + half],
                                   radius=10, outline="#888", width=2, fill="#f2f4f6")
            label = str(obj.get("name", ""))[:8]
            draw.text((cx, cy), label, fill="#444", anchor="mm", font=_font(max(12, box // 8)))
            if obj.get("rotation") or obj.get("mirrored"):
                mark = f"{obj.get('rotation', 0)}°{'/반전' if obj.get('mirrored') else ''}"
                draw.text((cx, cy + half - 14), mark, fill="#999", anchor="mm",
                          font=_font(max(10, box // 12)))

    for ann in scene.get("annotations", []) or []:
        _draw_annotation(draw, ann, size)
    return canvas


def _draw_annotation(draw, ann: dict, size: int) -> None:
    """화살표·상자·번호를 코드로 합성한다. 생성 모델에 맡기지 않는 부분이다."""
    kind = ann.get("type")
    if kind == "arrow" and ann.get("from") and ann.get("to"):
        x1, y1 = [v * size for v in ann["from"]]
        x2, y2 = [v * size for v in ann["to"]]
        draw.line([x1, y1, x2, y2], fill="#d03030", width=4)
        draw.ellipse([x2 - 7, y2 - 7, x2 + 7, y2 + 7], fill="#d03030")
    elif kind == "box" and ann.get("from") and ann.get("to"):
        x1, y1 = [v * size for v in ann["from"]]
        x2, y2 = [v * size for v in ann["to"]]
        draw.rectangle([x1, y1, x2, y2], outline="#2b6b8c", width=4)
    elif kind == "divider":
        draw.line([size / 2, 0, size / 2, size], fill="#bbb", width=3)
    elif kind == "number" and ann.get("text"):
        draw.text((size * 0.06, size * 0.06), str(ann["text"]), fill="#222",
                  font=_font(int(size * 0.07)))
