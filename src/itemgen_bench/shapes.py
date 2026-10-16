"""추상 도형을 코드로 그린다. 디퓨전 자산이 필요 없는 부분이다.

시공간 영역이 요구하는 사물의 85%, 주의력의 52%가 원·삼각형·별 같은 추상 도형이다.
디퓨전은 이런 걸 정확히 못 그린다 — 삼각형의 꼭짓점이 몇 개인지, 정확히 90도 돌았는지,
원이 12개인지 16개인지를 보장하지 못한다. 그래서 디퓨전 단독으로는 시공간 8/10,
주의력 8/10이 탈락했다.

도형은 코드로 그리면 틀릴 수가 없다. 회전은 rotate(), 반전은 transpose(),
개수는 배열 길이다. 자산 생성 단계 자체가 필요 없다.
"""

from __future__ import annotations

import math
import re

# 이름 -> 그리는 함수 키. 더 구체적인 이름을 먼저 둔다. 예를 들어 직사각형을
# 사각형보다 뒤에 두면 정사각형으로, 사분원을 원보다 뒤에 두면 원으로 오인한다.
# '선', '원', '점' 한 글자 부분 문자열은 거북선·원숭이·점퍼 같은 실물을 잘못
# 도형으로 잡으므로 독립된 단어일 때만 허용한다.
ALIASES: list[tuple[str, str]] = [
    (r"정육면체", "cube"),
    (r"비대칭\s*도형|F자", "asym"),
    (r"사분원", "quarter_circle"),
    (r"반원", "semicircle"),
    (r"타원", "ellipse"),
    (r"직사각형", "rect"),
    (r"꺾인\s*막대|L자|ㄱ자|꺾임", "elbow"),
    (r"가로막대|세로막대|막대", "rect"),
    (r"정사각형|네모|사각형|사각|박스", "square"),
    (r"삼각형|세모", "triangle"),
    (r"동그라미|원형|(?:^|\s)원(?:\s|$)", "circle"),
    (r"마름모", "diamond"),
    (r"오각형", "pentagon"),
    (r"육각형", "hexagon"),
    (r"별표|별", "star"),
    (r"화살표|화살", "arrow"),
    (r"십자|열십자", "cross"),
    (r"선분|직선", "line"),
    (r"(?:^|\s)점(?:\s|$)", "dot"),
    (r"하트", "heart"),
]
COMPILED = [(re.compile(p), k) for p, k in ALIASES]

# 색 이름 -> RGB. 명세가 한국어 색 이름을 쓴다.
COLORS = {
    "빨강": (214, 48, 49), "빨간": (214, 48, 49), "red": (214, 48, 49),
    "파랑": (41, 98, 196), "파란": (41, 98, 196), "blue": (41, 98, 196),
    "초록": (58, 150, 74), "녹색": (58, 150, 74), "green": (58, 150, 74),
    "노랑": (243, 196, 53), "노란": (243, 196, 53), "yellow": (243, 196, 53),
    "검정": (34, 38, 42), "검은": (34, 38, 42), "black": (34, 38, 42),
    "흰": (252, 252, 252), "하양": (252, 252, 252), "white": (252, 252, 252),
    "보라": (131, 76, 181), "purple": (131, 76, 181),
    "주황": (230, 126, 34), "orange": (230, 126, 34),
    "분홍": (232, 120, 160), "pink": (232, 120, 160),
    "회색": (140, 146, 152), "gray": (140, 146, 152),
    "갈색": (140, 94, 60), "brown": (140, 94, 60),
}
DEFAULT_FILL = (72, 80, 92)
OUTLINE = (24, 28, 32)


def shape_key(name: str) -> str | None:
    """사물 이름이 추상 도형이면 그리기 키를, 아니면 None."""
    for rx, key in COMPILED:
        if rx.search(name or ""):
            return key
    return None


def pick_color(obj: dict):
    # recolor는 명시적인 color만 바꾼다. 이름/appearance에 예전 색이 남아 있어도
    # 새 color가 이겨야 한다. 예: color=파랑, name=빨간 화살표.
    explicit = str(obj.get("color") or "").strip()
    for word, rgb in COLORS.items():
        if word in explicit:
            return rgb
    text = f"{obj.get('appearance') or ''} {obj.get('name') or ''}"
    for word, rgb in COLORS.items():
        if word in text:
            return rgb
    return DEFAULT_FILL


def _named_color(text: str, label: str, fallback):
    """'위면 빨강'처럼 면 이름 뒤에 오는 색을 찾는다."""
    m = re.search(rf"{label}\s*({'|'.join(map(re.escape, COLORS))})", text or "")
    return COLORS.get(m.group(1), fallback) if m else fallback


def _poly(n: int, box: int, rot_deg: float = -90.0, inner: float | None = None):
    """정n각형. inner를 주면 별 모양이 된다."""
    r = box * 0.46
    cx = cy = box / 2
    pts = []
    steps = n * (2 if inner else 1)
    for i in range(steps):
        a = math.radians(rot_deg + 360 * i / steps)
        rad = r if (inner is None or i % 2 == 0) else r * inner
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts


def draw(key: str, box: int, fill, line_w: int = 0):
    """도형 하나를 RGBA 이미지로 그린다. 바깥 여백은 투명이다."""
    from PIL import Image, ImageDraw

    # 4배로 그린 뒤 줄여 계단을 없앤다. 도형 문항은 외곽선 품질이 판단에 영향을 준다.
    S = 4
    b = box * S
    im = Image.new("RGBA", (b, b), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = (line_w or max(2, box // 28)) * S
    pad = b * 0.07
    inset = [pad, pad, b - pad, b - pad]

    if key == "circle":
        d.ellipse(inset, fill=fill, outline=OUTLINE, width=w)
    elif key == "semicircle":
        # 기본 방향은 평평한 변이 위쪽이다. 회전은 render_object에서 적용한다.
        d.pieslice(inset, 0, 180, fill=fill, outline=OUTLINE, width=w)
        d.line([pad, b / 2, b - pad, b / 2], fill=OUTLINE, width=w)
    elif key == "quarter_circle":
        # 기본 방향의 직각 꼭짓점은 왼쪽 아래다.
        d.pieslice(inset, 270, 360, fill=fill, outline=OUTLINE, width=w)
        d.line([b / 2, b / 2, b / 2, pad], fill=OUTLINE, width=w)
        d.line([b / 2, b / 2, b - pad, b / 2], fill=OUTLINE, width=w)
    elif key == "ellipse":
        d.ellipse([pad, b * 0.24, b - pad, b * 0.76], fill=fill, outline=OUTLINE, width=w)
    elif key == "square":
        d.rectangle(inset, fill=fill, outline=OUTLINE, width=w)
    elif key == "rect":
        d.rectangle([pad, b * 0.3, b - pad, b * 0.7], fill=fill, outline=OUTLINE, width=w)
    elif key == "triangle":
        d.polygon(_poly(3, b), fill=fill, outline=OUTLINE, width=w)
    elif key == "diamond":
        d.polygon(_poly(4, b), fill=fill, outline=OUTLINE, width=w)
    elif key == "pentagon":
        d.polygon(_poly(5, b), fill=fill, outline=OUTLINE, width=w)
    elif key == "hexagon":
        d.polygon(_poly(6, b), fill=fill, outline=OUTLINE, width=w)
    elif key == "star":
        d.polygon(_poly(5, b, inner=0.42), fill=fill, outline=OUTLINE, width=w)
    elif key == "heart":
        cx, r = b / 2, b * 0.24
        d.ellipse([cx - r * 1.7, b * 0.22, cx + r * 0.3, b * 0.22 + r * 2], fill=fill)
        d.ellipse([cx - r * 0.3, b * 0.22, cx + r * 1.7, b * 0.22 + r * 2], fill=fill)
        d.polygon([(cx - r * 1.7, b * 0.5), (cx + r * 1.7, b * 0.5), (cx, b * 0.88)], fill=fill)
    elif key == "cross":
        t = b * 0.17
        d.rectangle([b / 2 - t, pad, b / 2 + t, b - pad], fill=fill, outline=OUTLINE, width=w)
        d.rectangle([pad, b / 2 - t, b - pad, b / 2 + t], fill=fill, outline=OUTLINE, width=w)
    elif key == "elbow":
        t = b * 0.3
        d.polygon([(pad, pad), (pad + t, pad), (pad + t, b - pad - t),
                   (b - pad, b - pad - t), (b - pad, b - pad), (pad, b - pad)],
                  fill=fill, outline=OUTLINE, width=w)
    elif key == "asym":
        # F형은 거울 반전이 어떤 회전과도 같아지지 않아 회전/반전 변별에 적합하다.
        t = b * 0.18
        d.polygon([(pad, pad), (b - pad, pad), (b - pad, pad + t),
                   (pad + t, pad + t), (pad + t, b * 0.43),
                   (b * 0.72, b * 0.43), (b * 0.72, b * 0.43 + t),
                   (pad + t, b * 0.43 + t), (pad + t, b - pad),
                   (pad, b - pad)], fill=fill, outline=OUTLINE, width=w)
    elif key == "arrow":
        # 오른쪽을 향한 화살표. 회전은 바깥에서 준다.
        y, t, head = b / 2, b * 0.13, b * 0.34
        d.polygon([(pad, y - t), (b - pad - head, y - t), (b - pad - head, y - t * 2.1),
                   (b - pad, y), (b - pad - head, y + t * 2.1), (b - pad - head, y + t),
                   (pad, y + t)], fill=fill, outline=OUTLINE, width=w)
    elif key == "line":
        d.line([pad, b / 2, b - pad, b / 2], fill=fill, width=max(w * 2, int(b * 0.07)))
    elif key == "dot":
        r = b * 0.16
        d.ellipse([b / 2 - r, b / 2 - r, b / 2 + r, b / 2 + r], fill=fill)
    else:
        return None

    return im.resize((box, box), Image.LANCZOS)


def draw_cube(box: int, obj: dict):
    """면별 색을 보존한 단순 등각 정육면체를 그린다."""
    from PIL import Image, ImageDraw

    S = 4
    b = box * S
    im = Image.new("RGBA", (b, b), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = max(2, box // 28) * S
    text = f"{obj.get('appearance') or ''} {obj.get('name') or ''}"
    base = pick_color(obj)
    top = _named_color(text, "위면", base)
    front = _named_color(text, "앞면", base)
    right = _named_color(text, "오른쪽면", base)
    p_top = [(b * .20, b * .32), (b * .50, b * .14),
             (b * .80, b * .32), (b * .50, b * .50)]
    p_front = [(b * .20, b * .32), (b * .50, b * .50),
               (b * .50, b * .86), (b * .20, b * .67)]
    p_right = [(b * .50, b * .50), (b * .80, b * .32),
               (b * .80, b * .67), (b * .50, b * .86)]
    for pts, color in ((p_top, top), (p_front, front), (p_right, right)):
        d.polygon(pts, fill=color, outline=OUTLINE, width=w)
    return im.resize((box, box), Image.LANCZOS)


def render_object(obj: dict, box: int):
    """사물 하나를 그린다. 추상 도형이 아니면 None을 돌려준다."""
    key = shape_key(obj.get("name", ""))
    if key is None:
        return None
    im = draw_cube(box, obj) if key == "cube" else draw(key, box, pick_color(obj))
    if im is None:
        return None
    from PIL import Image
    if obj.get("mirrored"):
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if obj.get("rotation"):
        # 음수로 주어야 '시계 방향'이 된다. 명세가 시계 방향 기준이다.
        im = im.rotate(-int(obj["rotation"]), expand=False, resample=Image.BICUBIC)
    return im
