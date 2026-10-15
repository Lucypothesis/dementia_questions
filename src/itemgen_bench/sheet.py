"""사람 검수용 N-way 시트.

슬롯 하나가 한 카드다. 카드 안에서 (LLM x 렌더러) 조합을 가로로 늘어놓고,
조합마다 자동 판정과 사람 판정을 나란히 둔다.

JS 문자열 안의 줄바꿈은 반드시 \\n으로 쓴다. 파이썬이 \\n을 실제 줄바꿈으로 바꾸면
JS 문자열 리터럴이 깨져서 스크립트 전체가 파싱 실패하고, 버튼이 전부 죽는다.
"""

from __future__ import annotations

import base64
import html
import io
import json
import time
from collections import defaultdict
from pathlib import Path

VERDICTS = [
    ("ok", "쓸만함", "문항 자극으로 쓸 수 있다"),
    ("minor", "수정하면", "작은 손질로 쓸 수 있다"),
    ("bad", "못씀", "자극이 명세와 다르거나 정답이 흐려졌다"),
]

CSS = """
*{box-sizing:border-box}
body{font-family:system-ui,'Noto Sans KR',sans-serif;margin:0;padding:20px;
     background:#f5f6f8;color:#1b2228;font-size:14px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#5c6672;font-size:13px;margin-bottom:14px}
.stats{display:flex;gap:16px;flex-wrap:wrap;background:#fff;border:1px solid #dfe3e8;
       border-radius:9px;padding:10px 14px;margin-bottom:10px;font-size:13px}
.stats span{color:#5c6672}
.stats b{color:#1b2228;font-variant-numeric:tabular-nums;font-size:15px}
.bar{position:sticky;top:0;z-index:10;background:#f5f6f8;padding:10px 0 12px;
     border-bottom:1px solid #e0e4e9;margin-bottom:16px;display:flex;gap:8px;
     flex-wrap:wrap;align-items:center}
.bar select,.bar input,.bar button{font:inherit;padding:6px 10px;border:1px solid #ccd2d9;
     border-radius:6px;background:#fff}
.bar button{cursor:pointer}
.bar .count{color:#5c6672;font-size:13px;margin-left:auto}
/* ---- 조합 고르기 ---- */
.picker{background:#fff;border:1px solid #dfe3e8;border-radius:9px;padding:10px 14px;
        margin-bottom:10px;display:flex;gap:22px;flex-wrap:wrap;align-items:flex-start}
.pgroup{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.pgroup>b{font-size:12px;color:#49535e;margin-right:2px;white-space:nowrap}
/* 숨긴 체크박스(display:none) + label 방식은 환경에 따라 클릭이 안 먹는다.
   버튼에 직접 핸들러를 달고 상태는 data-on 속성으로 들고 간다. */
.pgroup .chip{font:inherit;font-size:12.5px;cursor:pointer;background:#f4f6f8;
              border:1px solid #dde2e7;border-radius:999px;padding:4px 11px;
              user-select:none;white-space:nowrap;color:#49535e}
.pgroup .chip[data-on="1"]{background:#2b6b8c;color:#fff;border-color:#2b6b8c}
.pgroup.r .chip[data-on="1"]{background:#8c6b4a;border-color:#8c6b4a}
.pgroup .mini{font:inherit;font-size:11.5px;padding:3px 8px;border:1px solid #ccd2d9;
              border-radius:6px;background:#fff;cursor:pointer;color:#5c6672}
.item{background:#fff;border:1px solid #dfe3e8;border-radius:10px;padding:14px;margin-bottom:14px}
.head{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
.id{font-weight:700;font-size:15px}
.tag{background:#eef1f4;border-radius:999px;padding:2px 9px;font-size:12px;color:#49535e}
.q{font-size:12.5px;color:#49535e;line-height:1.6;margin-bottom:10px}
.q b{color:#1b2228}
.cells{display:flex;gap:12px;overflow-x:auto;padding-bottom:6px}
.cell{flex:0 0 auto;width:300px;border:1px solid #e6eaee;border-radius:8px;padding:10px}
.cellhead{display:flex;gap:6px;align-items:center;margin-bottom:7px;flex-wrap:wrap}
.who{font-size:11.5px;font-weight:700;border-radius:5px;padding:2px 7px;
     background:#2b6b8c;color:#fff;white-space:nowrap}
.who.r{background:#8c6b4a}
.auto{font-size:11.5px;border-radius:5px;padding:2px 7px;white-space:nowrap}
.auto.y{background:#e3f3e7;color:#1f6b33}
.auto.n{background:#fbe6e6;color:#8c2b2b}
.auto.u{background:#eef1f4;color:#5c6672}
.strip{display:flex;flex-direction:column;gap:8px}
.shot{display:grid;grid-template-columns:96px minmax(0,1fr);gap:9px;align-items:start}
.shot img{width:96px;height:96px;object-fit:contain;background:#fafbfc;
          border:1px solid #dfe3e8;border-radius:6px;cursor:zoom-in}
.cap{font-size:11px;color:#5c6672;line-height:1.45}
.cap b{display:block;color:#1b2228}
.qa{font-size:12px;color:#49535e;line-height:1.5;background:#f7f9fb;border:1px solid #e4e8ec;
    border-radius:6px;padding:7px 9px;margin-bottom:8px}
.qa b{color:#2b6b8c;margin-right:3px}
.verdict{display:flex;gap:5px;margin-top:9px}
.verdict button{font:inherit;font-size:12px;flex:1 1 0;padding:6px 4px;border:1px solid #ccd2d9;
                border-radius:6px;background:#fff;cursor:pointer;color:#49535e}
.verdict button.on[data-w="ok"]{background:#3f9d58;color:#fff;border-color:#3f9d58}
.verdict button.on[data-w="minor"]{background:#c98a28;color:#fff;border-color:#c98a28}
.verdict button.on[data-w="bad"]{background:#cf4040;color:#fff;border-color:#cf4040}
.note{width:100%;margin-top:6px;font:inherit;font-size:12px;padding:5px 8px;
      border:1px solid #ccd2d9;border-radius:6px}
dialog{border:none;border-radius:10px;padding:0;background:transparent;max-width:95vw}
dialog img{max-width:92vw;max-height:88vh;background:#fff;border-radius:10px;display:block}
dialog::backdrop{background:rgba(0,0,0,.72)}
#jserr{display:none;background:#fdecec;border:1px solid #e3b4b4;color:#8c2b2b;
       padding:10px 14px;border-radius:8px;margin-bottom:12px;font-size:13px;
       white-space:pre-wrap;font-family:ui-monospace,monospace}
"""

JS = """
window.onerror = function(msg, src, line, col){
  var box = document.getElementById('jserr');
  if (box) { box.style.display = 'block';
             box.textContent = '화면 스크립트 오류 — ' + msg + ' (' + line + ':' + col + ')'; }
};
const KEY = 'itemgen-sheet-' + document.title;
const CELLS = window.__CELLS__;
let store = JSON.parse(localStorage.getItem(KEY) || '{}');
function rec(id){
  if (!store[id]) store[id] = {w:'', n:''};
  if (store[id].n === undefined) store[id].n = '';
  return store[id];
}
const save = () => localStorage.setItem(KEY, JSON.stringify(store));

function paint(cellId){
  const box = document.getElementById(cellId);
  if (!box) return;
  const r = rec(cellId);
  box.querySelectorAll('.verdict button').forEach(b =>
    b.classList.toggle('on', !!r.w && b.dataset.w === r.w));
  const note = box.querySelector('.note');
  if (note && note.value !== r.n) note.value = r.n;
}

document.querySelectorAll('.verdict button').forEach(btn => {
  btn.onclick = () => {
    const cell = btn.closest('.cell'), r = rec(cell.id);
    r.w = r.w === btn.dataset.w ? '' : btn.dataset.w;
    save(); paint(cell.id); refresh();
  };
});
document.querySelectorAll('.note').forEach(input => {
  input.oninput = () => { rec(input.closest('.cell').id).n = input.value; save(); };
});

// 어떤 arm·렌더러를 볼지는 체크박스로 고른다. 16개 조합을 하나씩 고르는 것보다
// 축별로 켜고 끄는 쪽이 빠르다. 고른 상태는 localStorage에 남겨 새로고침을 견딘다.
const PICK_KEY = KEY + '::pick';
function picked(group){
  const out = new Set();
  document.querySelectorAll('#' + group + ' .chip').forEach(c => {
    if (c.dataset.on === '1') out.add(c.dataset.value);
  });
  return out;
}
function savePick(){
  localStorage.setItem(PICK_KEY, JSON.stringify(
    {llm: Array.from(picked('p-llm')), ren: Array.from(picked('p-ren'))}));
}
function loadPick(){
  let s;
  try { s = JSON.parse(localStorage.getItem(PICK_KEY) || 'null'); } catch (e) { s = null; }
  if (!s) return;
  [['p-llm', s.llm], ['p-ren', s.ren]].forEach(([g, want]) => {
    if (!want) return;
    document.querySelectorAll('#' + g + ' .chip').forEach(c => {
      c.dataset.on = (want.indexOf(c.dataset.value) !== -1) ? '1' : '0';
    });
  });
}

function applyFilter(){
  const dom = document.getElementById('f-domain').value;
  const ver = document.getElementById('f-verdict').value;
  const q = document.getElementById('f-text').value.trim().toLowerCase();
  const L = picked('p-llm'), R = picked('p-ren');
  let shown = 0;
  document.querySelectorAll('.item').forEach(card => {
    let anyCell = false;
    card.querySelectorAll('.cell').forEach(cell => {
      const okCombo = L.has(cell.dataset.llm) && R.has(cell.dataset.renderer);
      const okVer = !ver || (rec(cell.id).w || 'none') === ver;
      const on = okCombo && okVer;
      cell.style.display = on ? '' : 'none';
      if (on) anyCell = true;
    });
    const ok = anyCell
      && (!dom || card.dataset.domain === dom)
      && (!q || card.dataset.search.includes(q));
    card.style.display = ok ? '' : 'none';
    if (ok) shown++;
  });
  document.querySelector('.count').textContent =
    shown + ' / ' + document.querySelectorAll('.item').length + ' 슬롯';
}

function refresh(){
  const done = CELLS.filter(id => rec(id).w).length;
  document.getElementById('s-done').textContent = done + '/' + CELLS.length;
  ['ok','minor','bad'].forEach(w =>
    document.getElementById('s-' + w).textContent =
      CELLS.filter(id => rec(id).w === w).length);
  applyFilter();
}

document.getElementById('export').onclick = () => {
  const rows = CELLS.filter(id => rec(id).w).map(id => {
    const el = document.getElementById(id);
    return {cell: id, slot_id: el.dataset.slot, llm: el.dataset.llm,
            renderer: el.dataset.renderer, verdict: rec(id).w, note: rec(id).n};
  });
  const blob = new Blob([JSON.stringify(rows, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'human_verdicts.json'; a.click();
};

document.getElementById('summary').onclick = () => {
  const combos = Array.from(new Set(CELLS.map(id =>
    document.getElementById(id).dataset.combo))).sort();
  const lines = combos.map(c => {
    const ids = CELLS.filter(id => document.getElementById(id).dataset.combo === c);
    const n = w => ids.filter(id => rec(id).w === w).length;
    const judged = ids.filter(id => rec(id).w).length;
    return c + '  쓸만 ' + n('ok') + ' / 수정 ' + n('minor') + ' / 못씀 ' + n('bad')
             + '  (판정 ' + judged + '/' + ids.length + ')';
  });
  const txt = lines.join('\\n');
  navigator.clipboard.writeText(txt).then(() => alert('복사됨:\\n\\n' + txt),
                                          () => prompt('복사하세요:', txt));
};

document.getElementById('next').onclick = () => {
  const target = CELLS.map(id => document.getElementById(id))
    .find(el => el && el.style.display !== 'none' && !rec(el.id).w
                && el.closest('.item').style.display !== 'none');
  if (!target) { alert('보이는 칸은 모두 판정했습니다.'); return; }
  target.scrollIntoView({behavior:'smooth', block:'center'});
};

document.getElementById('reset').onclick = () => {
  if (!confirm('판정을 모두 지울까요?')) return;
  store = {}; save(); CELLS.forEach(paint); refresh();
};

['f-domain','f-verdict'].forEach(id =>
  document.getElementById(id).onchange = refresh);
document.getElementById('f-text').oninput = applyFilter;

document.querySelectorAll('.pgroup .chip').forEach(chip => {
  chip.onclick = () => {
    chip.dataset.on = chip.dataset.on === '1' ? '0' : '1';
    savePick(); refresh();
  };
});
document.querySelectorAll('.pgroup .mini').forEach(btn => {
  btn.onclick = () => {
    const want = btn.dataset.act === 'all' ? '1' : '0';
    document.querySelectorAll('#' + btn.dataset.group + ' .chip')
      .forEach(c => { c.dataset.on = want; });
    savePick(); refresh();
  };
});
loadPick();

const dlg = document.getElementById('zoom');
document.querySelectorAll('.shot img').forEach(img => {
  img.onclick = () => { dlg.querySelector('img').src = img.src; dlg.showModal(); };
});
dlg.onclick = () => dlg.close();

CELLS.forEach(paint);
refresh();
"""


def _embed(path: Path, size: int, quality: int) -> str:
    from PIL import Image
    img = Image.open(path).convert("RGB")
    img.thumbnail((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _auto_badge(verdict: dict | None) -> str:
    if not verdict or verdict.get("usable") is None:
        return '<span class="auto u">자동 판정 없음</span>'
    good = verdict.get("usable") is True
    cls, label = ("y", "자동 쓸만") if good else ("n", "자동 못씀")
    why = html.escape(str(verdict.get("why", ""))[:90])
    return f'<span class="auto {cls}" title="{why}">{label}</span>'


def build(run: Path, slots: list[dict], embed: bool = True,
          only: set | None = None, filename: str = "sheet.html",
          title_suffix: str = "",
          size: int = 320, quality: int = 78) -> Path:
    panels_path = run / "panels.json"
    if not panels_path.exists():
        raise SystemExit(f"{panels_path}가 없다. render 단계를 먼저 돌려라")
    panels = json.loads(panels_path.read_text(encoding="utf-8"))

    judged = {}
    jpath = run / "judge.json"
    if jpath.exists():
        for row in json.loads(jpath.read_text(encoding="utf-8")):
            judged[(row["llm"], row["renderer"], row["name"])] = row.get("verdict")

    grouped = defaultdict(list)
    for rec in panels:
        grouped[(rec["slot_id"], rec["llm"], rec["renderer"])].append(rec)

    by_id = {s["id"]: s for s in slots}
    items = {}
    for (slot_id, llm, renderer) in grouped:
        path = run / "items" / llm / f"{slot_id}.json"
        if path.exists():
            items[(slot_id, llm)] = json.loads(path.read_text(encoding="utf-8"))

    cards, cell_ids, domains, combos = [], [], set(), set()
    arm_set, ren_set = set(), set()
    for slot in slots:
        keys = sorted([k for k in grouped if k[0] == slot["id"]], key=lambda k: (k[1], k[2]))
        if not keys:
            continue
        domains.add(slot["domain"])
        cells = []
        for (slot_id, llm, renderer) in keys:
            if only is not None and f"{slot_id}__{llm}__{renderer}" not in only:
                continue
            combo = f"{llm} x {renderer}"
            combos.add(combo); arm_set.add(llm); ren_set.add(renderer)
            cell_id = f"{slot_id}__{llm}__{renderer}"
            cell_ids.append(cell_id)
            shots = []
            for rec in sorted(grouped[(slot_id, llm, renderer)], key=lambda r: r["panel_index"]):
                img = run / rec["image"]
                # 축소본이 있으면 그쪽을 쓴다. 원본 768px을 96px로 줄여 보여주는 건
                # 낭비고, 저장소에 넣을 때도 용량이 40배 차이난다.
                thumb_rel = rec["image"].replace("images/", "thumbs/", 1).rsplit(".", 1)[0] + ".jpg"
                thumb = run / thumb_rel
                if thumb.exists():
                    src = _embed(thumb, size, quality) if embed else thumb_rel
                elif img.exists():
                    src = _embed(img, size, quality) if embed else rec["image"]
                else:
                    src = ""
                tag = rec["stage"] + (f" {rec['option_index']}" if rec.get("option_index") else "")
                badge = _auto_badge(judged.get((llm, renderer, rec["name"])))
                thumb = (f'<img src="{html.escape(src)}" loading="lazy">' if src
                         else '<div style="width:96px;height:96px;background:#f4f5f7;'
                              'border:1px dashed #ccd2d9;border-radius:6px"></div>')
                shots.append(
                    f'<div class="shot">{thumb}'
                    f'<div class="cap"><b>{html.escape(tag)}</b>{badge}<br>'
                    f'{html.escape(rec["prose"][:180])}</div></div>')
            buttons = "".join(
                f'<button data-w="{k}" title="{html.escape(why)}">{html.escape(label)}</button>'
                for k, label, why in VERDICTS)
            # 질문·정답은 arm마다 다른 산출물이다. 카드 머리말에 하나만 쓰면
            # 다른 arm의 문항을 그 arm 것으로 착각하게 된다. 칸 안에 각자 넣는다.
            it = items.get((slot_id, llm)) or {}
            qa = (f'<div class="qa"><b>Q</b> {html.escape(str(it.get("question", "(없음)")))}'
                  f'<br><b>A</b> {html.escape(str(it.get("answer", "-")))}</div>')
            cells.append(
                f'<div class="cell" id="{html.escape(cell_id)}" '
                f'data-slot="{html.escape(slot_id)}" data-llm="{html.escape(llm)}" '
                f'data-renderer="{html.escape(renderer)}" data-combo="{html.escape(combo)}">'
                f'<div class="cellhead"><span class="who">{html.escape(llm)}</span>'
                f'<span class="who r">{html.escape(renderer)}</span></div>'
                f'{qa}'
                f'<div class="strip">{"".join(shots)}</div>'
                f'<div class="verdict">{buttons}</div>'
                f'<input class="note" placeholder="메모 (선택)"></div>')

        if not cells:
            continue
        item = items.get((slot["id"], keys[0][1])) or {}
        search = f'{slot["id"]} {slot["subtype"]} {item.get("question", "")}'.lower()
        cards.append(
            f'<div class="item" data-domain="{html.escape(slot["domain"])}" '
            f'data-search="{html.escape(search)}">'
            f'<div class="head"><span class="id">{html.escape(slot["id"])}</span>'
            f'<span class="tag">{html.escape(slot["domain"])}</span>'
            f'<span class="tag">{html.escape(slot["subtype"])}</span>'
            f'<span class="tag">{html.escape(slot.get("construct", ""))}</span></div>'
            f'<div class="q">{html.escape(slot.get("construct", ""))} · '
            f'응답 {html.escape(slot.get("response", ""))}</div>'
            f'<div class="cells">{"".join(cells)}</div></div>')

    arm_names, ren_names = sorted(arm_set), sorted(ren_set)
    domain_opts = "".join(f'<option value="{html.escape(d)}">{html.escape(d)}</option>'
                          for d in sorted(domains))

    def chips(group_id: str, names, extra_cls: str = "", default_on=None) -> str:
        """고르기 칩 묶음. default_on이 없으면 전부 켠다."""
        body = "".join(
            f'<button class="chip" data-value="{html.escape(n)}" '
            f'data-on="{"1" if (default_on is None or n in default_on) else "0"}">'
            f'{html.escape(n)}</button>' for n in names)
        return (f'<div class="pgroup {extra_cls}" id="{group_id}">{body}'
                f'<button class="mini" data-group="{group_id}" data-act="all">전체</button>'
                f'<button class="mini" data-group="{group_id}" data-act="none">해제</button>'
                f'</div>')

    # 렌더러를 전부 켜면 한 슬롯에 16칸이 가로로 늘어서 비교가 어렵다. 기본은
    # 가장 성적이 좋은 하나(klein-4B)만 켜고 LLM은 전부 켠다. 칩으로 바꿀 수 있다.
    default_ren = [n for n in ren_names if "klein" in n] or ren_names[:1]
    picker = ('<div class="picker">'
              '<div class="pgroup"><b>LLM</b></div>' + chips("p-llm", arm_names) +
              '<div class="pgroup r"><b>렌더러</b></div>'
              + chips("p-ren", ren_names, "r", default_on=set(default_ren)) +
              '</div>')
    verdict_opts = "".join(f'<option value="{k}">{html.escape(label)}</option>'
                           for k, label, _ in VERDICTS)
    stats = ('<div class="stats"><span>진행 <b id="s-done">0</b></span>'
             + "".join(f'<span>{html.escape(label)} <b id="s-{k}">0</b></span>'
                       for k, label, _ in VERDICTS)
             + f'<span style="margin-left:auto">칸 {len(cell_ids)}개 · '
               f'조합 {len(combos)}개</span></div>')

    doc = (
        f"<!doctype html><meta charset='utf-8'>"
        f"<title>itemgen 검수 — {html.escape(run.name)}"
        f"{(' · ' + html.escape(title_suffix)) if title_suffix else ''}</title>"
        f"<style>{CSS}</style>"
        "<h1>문항 생성 벤치마크 검수</h1>"
        f'<div class="sub">슬롯 {len(cards)}개 · (LLM × 렌더러) 조합 {len(combos)}개 · '
        f'칸 {len(cell_ids)}개 · 이미지 클릭하면 확대 · '
        f'<b>빌드 {time.strftime("%H:%M:%S")}</b></div>'
        '<div id="jserr"></div>' + stats + picker +
        '<div class="bar">'
        f'<select id="f-domain"><option value="">전체 영역</option>{domain_opts}</select>'
        f'<select id="f-verdict"><option value="">전체 판정</option>'
        f'<option value="none">미판정</option>{verdict_opts}</select>'
        '<input id="f-text" placeholder="슬롯·질문 검색" size="16">'
        '<button id="next">다음 미판정</button>'
        '<button id="summary">조합별 요약 복사</button>'
        '<button id="export">JSON 내려받기</button>'
        '<button id="reset">초기화</button>'
        '<span class="count"></span></div>'
        + "".join(cards)
        + '<dialog id="zoom"><img alt=""></dialog>'
        + f"<script>window.__CELLS__={json.dumps(cell_ids, ensure_ascii=False)};{JS}</script>"
    )
    out = run / filename
    out.write_text(doc, encoding="utf-8")
    return out
