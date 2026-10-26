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

# 최종 50문항에서 확정한 프레젠테이션 디자인. 기존 검수 기능은 그대로 두고,
# 평상시에는 문제·이미지·정답에 집중한 2열 카드로 보여 준다.
PRESENTATION_CSS = """
:root{
  --ink:#14213d;
  --muted:#64748b;
  --line:#dbe4ec;
  --canvas:#eef3f7;
  --navy:#15324b;
  --teal:#0f766e;
}
html{scroll-behavior:smooth}
body{
  margin:0;
  padding:0 0 80px;
  background:
    radial-gradient(circle at 7% -8%,rgba(15,118,110,.11),transparent 28rem),
    linear-gradient(180deg,#f8fafc 0,#eef3f7 36rem,#eef3f7 100%);
  color:var(--ink);
  font-family:"Pretendard","Noto Sans KR","Apple SD Gothic Neo",system-ui,sans-serif;
  font-size:15px;
  line-height:1.6;
}
.hero{
  width:min(1380px,calc(100% - 40px));
  margin:28px auto 18px;
  padding:34px 38px 30px;
  color:#fff;
  background:
    radial-gradient(circle at 87% 18%,rgba(94,234,212,.22),transparent 19rem),
    linear-gradient(128deg,#102a43 0%,#17465c 58%,#0f766e 115%);
  border:1px solid rgba(255,255,255,.16);
  border-radius:24px;
  box-shadow:0 20px 55px rgba(15,42,67,.18);
}
.eyebrow{
  margin-bottom:8px;
  color:#99f6e4;
  font-size:12px;
  font-weight:800;
  letter-spacing:.14em;
}
.hero h1{margin:0;font-size:clamp(27px,3vw,42px);line-height:1.2;letter-spacing:-.035em}
.hero .sub{margin:10px 0 0;color:#d9e8ef;font-size:15px}
.generation-meta{
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:10px;
  margin-top:22px;
}
.generation-row{
  display:grid;
  grid-template-columns:auto minmax(0,1fr);
  column-gap:12px;
  align-items:center;
  padding:12px 14px;
  border:1px solid rgba(255,255,255,.18);
  border-radius:13px;
  background:rgba(255,255,255,.09);
}
.generation-label{
  padding:5px 8px;
  border-radius:8px;
  background:#99f6e4;
  color:#134e4a;
  font-size:11px;
  font-weight:850;
  white-space:nowrap;
}
.generation-value{color:#fff;font-size:15px;font-weight:800}
.hero-metrics{display:flex;flex-wrap:wrap;gap:10px;margin-top:14px}
.hero-metrics span{
  display:flex;
  align-items:baseline;
  gap:7px;
  padding:8px 13px;
  border:1px solid rgba(255,255,255,.18);
  border-radius:999px;
  background:rgba(255,255,255,.09);
  color:#eaf4f6;
  font-size:13px;
  backdrop-filter:blur(4px);
}
.hero-metrics b{color:#fff;font-size:16px;font-variant-numeric:tabular-nums}
body>#jserr,body>.stats,body>.picker,body>.bar,body>.items-grid{
  width:min(1380px,calc(100% - 40px));
  margin-left:auto;
  margin-right:auto;
}
.items-grid{
  display:grid;
  grid-template-columns:repeat(2,minmax(480px,1fr));
  align-items:start;
  gap:18px;
  overflow-x:auto;
  padding:1px 1px 12px;
}
.items-grid .item{width:100%;margin:0;min-width:0}
.stats,.picker{display:none}
body.review-mode .stats{display:flex}
body.review-mode .picker{display:flex}
.stats,.picker{
  border-color:var(--line);
  border-radius:14px;
  box-shadow:0 5px 18px rgba(15,42,67,.05);
}
.bar{
  top:0;
  margin-bottom:18px;
  padding:12px 14px;
  border:1px solid var(--line);
  border-radius:16px;
  background:rgba(255,255,255,.94);
  box-shadow:0 10px 28px rgba(15,42,67,.10);
  backdrop-filter:blur(12px);
}
.domain-nav{display:flex;flex:1 1 100%;gap:8px;overflow-x:auto;padding:1px 0 3px}
.domain-tab{
  display:flex;
  align-items:center;
  gap:7px;
  white-space:nowrap;
  padding:8px 13px;
  border:1px solid #d8e1e8;
  border-radius:999px;
  background:#fff;
  color:#475569;
  font:inherit;
  font-size:13px;
  font-weight:700;
  cursor:pointer;
  transition:transform .14s ease,background .14s ease,color .14s ease,border-color .14s ease;
}
.domain-tab:hover{transform:translateY(-1px);border-color:#9fb3c3}
.domain-tab b{
  display:inline-grid;
  place-items:center;
  min-width:22px;
  height:22px;
  padding:0 6px;
  border-radius:999px;
  background:#edf2f6;
  color:#475569;
  font-size:11px;
}
.domain-tab.active{background:var(--navy);border-color:var(--navy);color:#fff}
.domain-tab.active b{background:rgba(255,255,255,.18);color:#fff}
.bar #f-domain,.bar #f-verdict,.bar #next,.bar #summary,.bar #export,.bar #reset{display:none}
body.review-mode .bar #f-verdict,body.review-mode .bar #next,
body.review-mode .bar #summary,body.review-mode .bar #export,
body.review-mode .bar #reset{display:inline-block}
.bar input{
  min-width:250px;
  padding:9px 12px;
  border-color:#d8e1e8;
  border-radius:10px;
  background:#f8fafc;
}
.bar input:focus,.bar select:focus{outline:3px solid rgba(15,118,110,.16);border-color:#0f766e}
.bar .count{margin-left:auto;color:#64748b;font-size:13px;font-weight:700}
#toggle-review{
  padding:9px 13px;
  border:1px solid #b8c7d1;
  border-radius:10px;
  background:#fff;
  color:#334155;
  font:inherit;
  font-size:13px;
  font-weight:750;
  cursor:pointer;
}
#toggle-review:hover{border-color:#0f766e;color:#0f766e}
.item{
  --domain:#0f766e;
  --domain-soft:#e7f6f4;
  position:relative;
  overflow:hidden;
  padding:24px 26px 26px;
  border:1px solid rgba(203,213,225,.88);
  border-radius:19px;
  background:rgba(255,255,255,.97);
  box-shadow:0 9px 28px rgba(15,42,67,.07);
}
.item::before{content:"";position:absolute;inset:0 0 auto;height:5px;background:var(--domain)}
.item[data-domain="기억력"]{--domain:#7c3aed;--domain-soft:#f1eafe}
.item[data-domain="주의력"]{--domain:#0f766e;--domain-soft:#e5f6f3}
.item[data-domain="언어능력"]{--domain:#b45309;--domain-soft:#fff3df}
.item[data-domain="시공간능력"]{--domain:#2563eb;--domain-soft:#eaf1ff}
.item[data-domain="집행기능"]{--domain:#be123c;--domain-soft:#ffe9ef}
.head{gap:7px;margin:0 0 14px;padding-top:3px}
.ordinal{
  display:inline-grid;
  place-items:center;
  width:31px;
  height:31px;
  margin-right:3px;
  border-radius:10px;
  background:var(--domain);
  color:#fff;
  font-size:12px;
  font-weight:850;
  font-variant-numeric:tabular-nums;
}
.id{font-size:13px;color:#64748b;letter-spacing:.025em}
.tag{padding:4px 10px;background:#f1f5f9;color:#526173;font-size:12px}
.head .tag:first-of-type{background:var(--domain-soft);color:var(--domain);font-weight:800}
.q{display:none;margin:-5px 0 12px;color:#64748b;font-size:12px}
body.review-mode .q{display:block}
.cells{display:block;overflow:visible;padding:0}
.cell{
  display:flex;
  flex-direction:column;
  width:100%;
  padding:0;
  border:0;
  background:transparent;
}
.cell:not(:first-child){margin-top:18px;padding-top:18px;border-top:1px dashed var(--line)}
.cellhead{display:none;margin:0 0 10px}
body.review-mode .cellhead{display:flex}
.who{background:#335c72}.who.r{background:#746454}
.qa{display:contents;color:var(--ink);font-size:15px;line-height:1.68}
.cellhead{order:0}
.question{order:1}
.strip{order:2}
.answer{order:3}
.verdict{order:4}
.note{order:5}
.question{
  display:grid;
  grid-template-columns:54px minmax(0,1fr);
  gap:13px;
  align-items:start;
  padding:16px 18px 13px;
  border:1px solid #dbe5ec;
  border-bottom:0;
  border-radius:14px 14px 0 0;
  background:#f8fafc;
}
.qlabel,.alabel{
  display:inline-grid;
  place-items:center;
  min-height:28px;
  padding:2px 8px;
  border-radius:8px;
  font-size:12px;
  font-weight:850;
  letter-spacing:.02em;
}
.qlabel{background:var(--domain);color:#fff}
.qtext{font-size:16px;font-weight:700;letter-spacing:-.015em;color:#17243a}
.answer{
  display:grid;
  grid-template-columns:54px minmax(0,1fr);
  gap:13px;
  align-items:start;
  margin-top:10px;
  padding:12px 18px;
  border:1px solid #cfe8dd;
  border-radius:12px;
  background:#f0faf6;
  color:#166534;
}
.alabel{background:#d7f2e5;color:#166534}
.atext{font-size:14px;font-weight:700}
.strip{
  display:grid;
  position:relative;
  gap:10px;
  margin-top:0;
  padding:15px 18px 18px;
  align-items:start;
  border:1px solid #dbe5ec;
  border-top:0;
  border-radius:0 0 14px 14px;
  background:#f8fafc;
}
.strip::before{
  content:"";
  position:absolute;
  top:0;
  left:18px;
  right:18px;
  height:1px;
  background:#dfe7ed;
}
.strip.single{grid-template-columns:minmax(220px,320px)}
.strip.count-2{grid-template-columns:repeat(2,minmax(0,1fr))}
.strip.count-3{grid-template-columns:repeat(3,minmax(0,1fr))}
.strip.count-4{grid-template-columns:repeat(4,minmax(0,1fr))}
.strip.count-5{grid-template-columns:repeat(4,minmax(0,1fr))}
.strip.count-5 .shot:first-child{grid-column:1/-1;width:min(220px,100%);justify-self:start}
.shot{
  display:block;
  width:100%;
  min-width:0;
  max-width:none;
  padding:7px;
  border:1px solid #dbe4ec;
  border-radius:12px;
  background:#fff;
  box-shadow:0 4px 14px rgba(15,42,67,.06);
}
.shot img,.image-placeholder{
  display:block;
  width:100%;
  height:132px;
  object-fit:contain;
  border:0;
  border-radius:8px;
  background:#f8fafc;
}
.shot img{cursor:zoom-in}
.image-placeholder{border:1px dashed #ccd2d9}
.strip.single .shot img,.strip.single .image-placeholder{height:220px}
.strip.count-5 .shot:first-child img{height:150px}
.cap{margin-top:8px;color:#64748b;font-size:11px;line-height:1.45}
.capline{display:flex;align-items:center;gap:6px}
.cap b{display:inline-block;color:var(--domain);font-size:12px}
.scene-desc{display:none;margin:7px 0 0;padding-top:7px;border-top:1px dashed #dbe4ec}
body.review-mode .scene-desc{display:block}
.auto{display:none}
body.review-mode .auto{display:inline-block}
.verdict,.note{display:none}
body.review-mode .verdict{display:flex}
body.review-mode .note{display:block}
.verdict{max-width:420px;margin-top:16px}
.verdict button{padding:8px 7px;border-radius:9px}
.note{max-width:650px;padding:8px 10px;border-radius:9px}
dialog{box-shadow:0 26px 80px rgba(0,0,0,.34)}
dialog img{border-radius:14px}

@media(max-width:760px){
  .hero,body>#jserr,body>.stats,body>.picker,body>.bar,body>.items-grid{
    width:min(100% - 22px,1380px);
  }
  .hero{margin-top:11px;padding:25px 21px;border-radius:18px}
  .hero-metrics span{padding:6px 10px}
  .generation-meta{grid-template-columns:1fr}
  .bar{border-radius:13px}
  .bar input{width:100%;min-width:0}
  .bar .count{margin-left:0}
  .item{padding:20px 16px;border-radius:16px}
  .question,.answer{grid-template-columns:1fr;gap:7px;padding:14px}
  .qlabel,.alabel{width:max-content}
  .items-grid{grid-template-columns:repeat(2,minmax(440px,1fr));gap:13px}
  .strip.single .shot img{height:auto;max-height:310px}
}
@media print{
  body{background:#fff;padding:0}
  .hero{margin-top:0;box-shadow:none}
  .bar,.stats,.picker,.verdict,.note{display:none!important}
  .items-grid{grid-template-columns:repeat(2,minmax(0,1fr));width:100%;overflow:visible}
  .item{break-inside:avoid;box-shadow:none;margin:0}
}
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

PRESENTATION_JS = """
(() => {
  const preferred = ["기억력", "주의력", "언어능력", "시공간능력", "집행기능"];
  const cards = Array.from(document.querySelectorAll('.item'));
  const found = Array.from(new Set(cards.map(card => card.dataset.domain).filter(Boolean)));
  const domains = [
    ...preferred.filter(name => found.includes(name)),
    ...found.filter(name => !preferred.includes(name))
  ];
  const counts = Object.fromEntries(domains.map(name => [name, 0]));

  cards.forEach(card => {
    const domain = card.dataset.domain;
    counts[domain] = (counts[domain] || 0) + 1;
    const ordinal = document.createElement('span');
    ordinal.className = 'ordinal';
    ordinal.textContent = String(counts[domain]).padStart(2, '0');
    const head = card.querySelector('.head');
    if (head) head.prepend(ordinal);
  });

  const bar = document.querySelector('.bar');
  const domainSelect = document.getElementById('f-domain');
  if (!bar || !domainSelect) return;

  const nav = document.createElement('nav');
  nav.className = 'domain-nav';
  nav.setAttribute('aria-label', '인지영역 필터');
  const choices = [['', '전체', cards.length],
                   ...domains.map(name => [name, name, counts[name]])];
  choices.forEach(([value, label, count], index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'domain-tab' + (index === 0 ? ' active' : '');
    button.dataset.domain = value;
    button.innerHTML = `<span>${label}</span><b>${count}</b>`;
    button.onclick = () => {
      domainSelect.value = value;
      applyFilter();
      nav.querySelectorAll('.domain-tab').forEach(tab =>
        tab.classList.toggle('active', tab === button));
      window.scrollTo({top: Math.max(0, bar.offsetTop - 8), behavior: 'smooth'});
    };
    nav.appendChild(button);
  });
  bar.prepend(nav);

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.id = 'toggle-review';
  toggle.textContent = '검수 정보 보기';
  toggle.setAttribute('aria-pressed', 'false');
  toggle.onclick = () => {
    const on = document.body.classList.toggle('review-mode');
    toggle.textContent = on ? '간결 보기' : '검수 정보 보기';
    toggle.setAttribute('aria-pressed', String(on));
  };
  bar.appendChild(toggle);
})();
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


DISPLAY_NAMES = {
    "qwen38_27b": "Qwen3.8-27B",
    "mistral_small_24b": "Mistral Small 24B",
    "flux2_klein_4b": "FLUX.2-klein-4B",
    "compose": "코드 기반 Compose",
    "stub": "Stub (데모)",
}


def _display_name(name: str) -> str:
    return DISPLAY_NAMES.get(name, name.replace("_", " "))


def build(run: Path, slots: list[dict], embed: bool = True,
          only: set | None = None, filename: str = "sheet.html",
          title_suffix: str = "",
          size: int = 320, quality: int = 78,
          default_all_renderers: bool = False) -> Path:
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
    domain_counts = defaultdict(int)
    arm_set, ren_set = set(), set()
    for slot in slots:
        keys = sorted([k for k in grouped if k[0] == slot["id"]], key=lambda k: (k[1], k[2]))
        if not keys:
            continue
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
                         else '<div class="image-placeholder"></div>')
                shots.append(
                    f'<div class="shot">{thumb}'
                    f'<div class="cap"><div class="capline"><b>{html.escape(tag)}</b>'
                    f'{badge}</div><p class="scene-desc">'
                    f'{html.escape(rec["prose"][:180])}</p></div></div>')
            buttons = "".join(
                f'<button data-w="{k}" title="{html.escape(why)}">{html.escape(label)}</button>'
                for k, label, why in VERDICTS)
            # 질문·정답은 arm마다 다른 산출물이다. 카드 머리말에 하나만 쓰면
            # 다른 arm의 문항을 그 arm 것으로 착각하게 된다. 칸 안에 각자 넣는다.
            it = items.get((slot_id, llm)) or {}
            qa = (
                '<div class="qa">'
                '<div class="question"><span class="qlabel">문제</span>'
                f'<div class="qtext">{html.escape(str(it.get("question", "(없음)")))}</div>'
                '</div>'
                '<div class="answer"><span class="alabel">정답</span>'
                f'<div class="atext">{html.escape(str(it.get("answer", "-")))}</div>'
                '</div></div>')
            strip_class = "single" if len(shots) == 1 else f"count-{len(shots)}"
            cells.append(
                f'<div class="cell" id="{html.escape(cell_id)}" '
                f'data-slot="{html.escape(slot_id)}" data-llm="{html.escape(llm)}" '
                f'data-renderer="{html.escape(renderer)}" data-combo="{html.escape(combo)}">'
                f'<div class="cellhead"><span class="who">{html.escape(llm)}</span>'
                f'<span class="who r">{html.escape(renderer)}</span></div>'
                f'{qa}'
                f'<div class="strip {strip_class}">{"".join(shots)}</div>'
                f'<div class="verdict">{buttons}</div>'
                f'<input class="note" placeholder="메모 (선택)"></div>')

        if not cells:
            continue
        domains.add(slot["domain"])
        domain_counts[slot["domain"]] += 1
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
    default_ren = (ren_names if default_all_renderers
                   else ([n for n in ren_names if "klein" in n] or ren_names[:1]))
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

    text_models = " · ".join(_display_name(name) for name in arm_names) or "-"
    diffusion_models = [name for name in ren_names if name != "compose"]
    image_models = (" · ".join(_display_name(name) for name in diffusion_models)
                    if diffusion_models else _display_name("compose"))
    complete_final = (len(cards) == 50 and len(domain_counts) == 5
                      and set(domain_counts.values()) == {10})
    if complete_final:
        subtitle = ("기억력 · 주의력 · 언어능력 · 시공간능력 · 집행기능을 "
                    "영역별 10문제로 구성한 최종 검수본입니다.")
    elif run.name == "demo":
        subtitle = "GPU나 외부 API 없이 실행 구조를 확인하는 데모 결과입니다."
    else:
        subtitle = "인지검사 문항 생성 결과를 확인하는 HTML 시트입니다."
    counts = set(domain_counts.values())
    third_metric = (f'<span>영역별 <b>{next(iter(counts))}</b>문제</span>'
                    if len(counts) == 1 else
                    f'<span>결과 셀 <b>{len(cell_ids)}</b>개</span>')
    hero = (
        '<header class="hero">'
        '<div class="eyebrow">COGNITIVE ITEM GENERATION PIPELINE</div>'
        '<h1>인지검사 문항 생성</h1>'
        f'<div class="sub">{html.escape(subtitle)}</div>'
        '<div class="generation-meta">'
        '<div class="generation-row"><span class="generation-label">테스트 생성 모델</span>'
        f'<strong class="generation-value">{html.escape(text_models)}</strong></div>'
        '<div class="generation-row"><span class="generation-label">이미지 생성 모델</span>'
        f'<strong class="generation-value">{html.escape(image_models)}</strong></div>'
        '</div><div class="hero-metrics">'
        f'<span>전체 <b>{len(cards)}</b>문제</span>'
        f'<span>인지영역 <b>{len(domain_counts)}</b>개</span>'
        + third_metric +
        f'<span>빌드 <b>{time.strftime("%H:%M:%S")}</b></span>'
        '</div></header>')

    doc = (
        f"<!doctype html><meta charset='utf-8'>"
        f"<title>인지검사 문항 생성 — {html.escape(run.name)}"
        f"{(' · ' + html.escape(title_suffix)) if title_suffix else ''}</title>"
        f"<style>{CSS}\n{PRESENTATION_CSS}</style>"
        + hero + '<div id="jserr"></div>' + stats + picker +
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
        + '<main class="items-grid">' + "".join(cards) + '</main>'
        + '<dialog id="zoom"><img alt=""></dialog>'
        + f"<script>window.__CELLS__={json.dumps(cell_ids, ensure_ascii=False)};"
        + JS + PRESENTATION_JS + "</script>"
    )
    out = run / filename
    out.write_text(doc, encoding="utf-8")
    return out
