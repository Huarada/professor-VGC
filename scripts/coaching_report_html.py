"""A single-file HTML report of a coaching testbench run, built for a human to read and validate.

Every case shows the question, the model's answer as formatted text, the automatic checks and judge scores, the engine's
turning point, and the replay it was about (engine timeline, raw log, a download button and, for public games, the
Showdown link). Each answer has a verdict and a note that are kept in the browser (localStorage) and can be exported as JSON.
No external resources: the file works offline.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

RUBRIC = ("grounded", "actionable", "specific", "consistent", "honest")
GROUPS = (("bo1", "BO1, real replays"), ("bo1-sim", "BO1, simulated (random moves)"), ("bo3", "BO3 series"))


def group_of(case_id: str) -> str:
    return "bo1-sim" if case_id.startswith("bo1sim-") else case_id[:3]


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)


def markdown_to_html(text: str) -> str:
    """Headings, bold/italic/code, bullet and numbered lists, quotes and paragraphs: all these answers use."""
    out: list[str] = []
    paragraph: list[str] = []
    stack: list[str] = []  # open list tags

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + " ".join(_inline(p) for p in paragraph) + "</p>")
            paragraph.clear()

    def close_lists() -> None:
        while stack:
            out.append("</li></" + stack.pop() + ">")

    def open_item(tag: str, depth: int, body: str) -> None:
        while len(stack) > depth:
            out.append("</li></" + stack.pop() + ">")
        if len(stack) == depth:
            if stack[-1] != tag:  # a bullet list after a numbered one at the same level
                out.append("</li></" + stack.pop() + "><" + tag + ">")
                stack.append(tag)
            else:
                out.append("</li>")
        while len(stack) < depth:
            out.append("<" + tag + ">")
            stack.append(tag)
        out.append("<li>" + body)

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        bullet = re.match(r"([-*•])\s+(.*)", stripped)
        number = re.match(r"(\d+)[.)]\s+(.*)", stripped)
        if not stripped:
            flush_paragraph()
            continue
        if heading:
            flush_paragraph()
            close_lists()
            level = min(len(heading.group(1)) + 2, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif bullet or number:
            flush_paragraph()
            indent = len(line) - len(line.lstrip())
            item = bullet or number
            open_item("ul" if bullet else "ol", 1 + indent // 2, _inline(item.group(2)))  # type: ignore[union-attr]
        elif stripped.startswith(">"):
            flush_paragraph()
            close_lists()
            out.append(f"<blockquote>{_inline(stripped.lstrip('> '))}</blockquote>")
        else:
            if stack and line[:1] in (" ", "\t"):
                out.append(f"<div class=\"cont\">{_inline(stripped)}</div>")
                continue
            close_lists()
            paragraph.append(stripped)
    flush_paragraph()
    close_lists()
    return "\n".join(out)


def _badge(label: str, tone: str) -> str:
    return f'<span class="badge {tone}">{html.escape(label)}</span>'


def _checks(row: dict[str, Any]) -> str:
    claims = row["damage_claims"]
    good = sum(c["verdict"] == "correct" for c in claims)
    bad = sum(c["verdict"] == "incorrect" for c in claims)
    out = []
    contradictions = row["contradictions"]
    out.append(_badge("no contradiction with the log", "ok") if not contradictions
               else _badge(f"{len(contradictions)} contradiction(s) with the log", "bad"))
    if claims:
        out.append(_badge(f"damage claims: {good} correct, {bad} incorrect of {len(claims)}", "bad" if bad else "ok"))
    presented = row.get("presents_turning_point")
    if presented is not None:
        out.append(_badge("presents the engine's turning point" if presented else "names another turning point",
                          "ok" if presented else "bad"))
    rejections = len(row.get("validation_rejections", []))
    if row["mode"] == "bo3":
        out.append(_badge(f"{rejections} narrative-validation rejection(s)", "warn" if rejections else "ok"))
        if row.get("fallback_notice"):
            out.append(_badge("fell back to verified findings only", "bad"))
    if row["figures_outside_evidence"]:
        out.append(_badge("figures not in the evidence: " + ", ".join(row["figures_outside_evidence"]), "bad"))
    return "".join(out)


def _judge(row: dict[str, Any]) -> str:
    judge = row.get("judge")
    if not judge:
        return '<p class="muted">No judge score.</p>'
    bars = "".join(f'<div class="bar"><span>{k}</span><i style="width:{judge[k] * 20}%"></i><b>{judge[k]}</b></div>'
                   for k in RUBRIC)
    issues = "".join(f"<li>{html.escape(i)}</li>" for i in judge.get("issues", []))
    return (f'<div class="judge">{bars}</div>' + (f'<ul class="issues">{issues}</ul>' if issues else "")
            + '<p class="muted">Judge scores are one model call per answer: subjective, read them last.</p>')


def _turning(row: dict[str, Any]) -> str:
    point = row.get("turning_point")
    if not point:
        return ""
    cands = "".join(f"<li>turn {c['turn']}: {c['winner_alive']} vs {c['loser_alive']} alive, lead {c['lead']:+.2f}"
                    f"{' - ' + html.escape(', '.join(c['faints'])) if c['faints'] else ''}</li>" for c in point["candidates"])
    return (f'<div class="engine"><b>Engine turning point: turn {point["turn"]}</b> ({html.escape(point["rule"])}). '
            f'{html.escape(point["summary"])}<ul>{cands}</ul></div>')


def _replays(row: dict[str, Any], index: int) -> str:
    parts = []
    for n, game in enumerate(row.get("replays", [])):
        link = (f' <a href="{html.escape(game["url"])}" target="_blank" rel="noopener">open on Showdown</a>'
                if game.get("url") else "")
        payload = html.escape(json.dumps({"id": game.get("id"), "players": game.get("players"), "log": game["log"]}), quote=True)
        parts.append(
            f'<details class="replay"><summary>{html.escape(game["label"])}{link}</summary>'
            f'<p class="muted">Players: {html.escape(" vs ".join(game.get("players", [])))}. '
            f'<a href="#" class="dl" data-name="{html.escape(row["id"])}-{n + 1}.json" data-json="{payload}">'
            f'download replay (.json)</a></p>'
            f'<details open><summary>Engine timeline (what the log shows, in order)</summary><pre>{html.escape(game["timeline"])}</pre></details>'
            f'<details><summary>Raw battle log</summary><pre>{html.escape(game["log"])}</pre></details></details>')
    return "".join(parts)


def _case(row: dict[str, Any], index: int) -> str:
    group = group_of(row["id"])
    head = f'<h2 id="{html.escape(row["id"])}">{html.escape(row["id"])}</h2>'
    if "error" in row:
        return f'<article data-group="{group}" data-id="{html.escape(row["id"])}">{head}<p class="err">Error: {html.escape(row["error"])}</p></article>'
    games = ""
    if row.get("game_answers"):
        games = "".join(f'<details><summary>Review of game {i + 1}</summary><div class="answer">{markdown_to_html(a)}</div></details>'
                        for i, a in enumerate(row["game_answers"]))
    contradictions = "".join(f"<li>{html.escape(c)}</li>" for c in row["contradictions"])
    return f'''<article data-group="{group}" data-id="{html.escape(row["id"])}">
{head}
<p class="meta">{html.escape(row["source"])} &middot; {row["seconds"]} s &middot; {row["chars"]} characters</p>
<p class="question"><b>Question:</b> {html.escape(row["question"])}</p>
<div class="answer">{markdown_to_html(row["answer"])}</div>
{games}
<div class="checks">{_checks(row)}</div>
{('<ul class="issues">' + contradictions + '</ul>') if contradictions else ''}
{_turning(row)}
{_judge(row)}
<div class="verdict" data-id="{html.escape(row["id"])}">
  <b>Your verdict</b>
  <label><input type="radio" name="v-{index}" value="ok"> Makes sense</label>
  <label><input type="radio" name="v-{index}" value="partial"> Partly</label>
  <label><input type="radio" name="v-{index}" value="wrong"> Wrong</label>
  <textarea placeholder="What is right or wrong? (saved in this browser)"></textarea>
</div>
{_replays(row, index)}
</article>'''


_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2430;--muted:#667085;--line:#e3e7ee;--ok:#1a7f4b;--bad:#b42318;--warn:#a15c07;--accent:#2f5bd8;--chip:#eef1f6}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--card:#1c2027;--ink:#e8ebf0;--muted:#98a2b3;--line:#2d333d;--ok:#4cc38a;--bad:#ff7b6e;--warn:#e9b24a;--accent:#7c9bff;--chip:#262c36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,Segoe UI,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px}
header h1{margin:0 0 6px;font-size:1.1rem}header .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
button,.chip{font:inherit;border:1px solid var(--line);background:var(--chip);color:var(--ink);border-radius:999px;padding:4px 12px;cursor:pointer}
button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
main{max-width:920px;margin:0 auto;padding:16px}
article{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:0 0 20px}
article h2{margin:0;font-size:1rem;font-family:ui-monospace,Consolas,monospace;word-break:break-all}
.meta,.muted{color:var(--muted);font-size:.85rem;margin:.2rem 0}.question{margin:.6rem 0}
.answer{border-left:3px solid var(--accent);padding:2px 14px;margin:10px 0}.answer h3,.answer h4,.answer h5{margin:.9rem 0 .3rem}
.answer p,.answer li{max-width:70ch}.answer ul,.answer ol{padding-left:1.3rem}
.checks{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.badge{font-size:.78rem;border-radius:6px;padding:2px 8px;border:1px solid var(--line)}
.badge.ok{color:var(--ok)}.badge.bad{color:var(--bad);font-weight:600}.badge.warn{color:var(--warn)}
.issues{color:var(--bad);font-size:.9rem}.engine{background:var(--chip);border-radius:8px;padding:8px 12px;font-size:.92rem;margin:8px 0}
.judge{display:grid;gap:3px;margin:8px 0;max-width:420px}.bar{display:grid;grid-template-columns:90px 1fr 24px;gap:8px;align-items:center;font-size:.85rem}
.bar i{display:block;height:8px;background:var(--accent);border-radius:4px}.bar b{text-align:right}
.verdict{border-top:1px dashed var(--line);margin-top:12px;padding-top:10px;display:grid;gap:6px}
.verdict label{margin-right:14px}.verdict textarea{width:100%;min-height:60px;font:inherit;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px}
details{margin:8px 0}summary{cursor:pointer;color:var(--accent)}pre{white-space:pre-wrap;font-size:.8rem;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;max-height:420px;overflow:auto}
.err{color:var(--bad)}.done{opacity:.72}.cont{margin-left:1.3rem}code{background:var(--chip);padding:0 4px;border-radius:4px}
@media (max-width:560px){main{padding:10px}article{padding:12px}}
"""

_JS = """
const stamp = document.body.dataset.stamp, store = {};
const key = id => 'coaching-validation:' + stamp + ':' + id;
function load(){document.querySelectorAll('.verdict').forEach(box=>{
  let saved={}; try{saved=JSON.parse(localStorage.getItem(key(box.dataset.id))||'{}')}catch(e){}
  if(saved.v){const r=box.querySelector('input[value='+saved.v+']'); if(r) r.checked=true; box.closest('article').classList.add('done')}
  box.querySelector('textarea').value=saved.note||'';});count();}
function save(box){const v=(box.querySelector('input:checked')||{}).value||'', note=box.querySelector('textarea').value;
  try{localStorage.setItem(key(box.dataset.id),JSON.stringify({v,note}))}catch(e){}
  box.closest('article').classList.toggle('done',!!v);count();}
function count(){const all=document.querySelectorAll('.verdict').length,done=document.querySelectorAll('.verdict input:checked').length;
  document.getElementById('progress').textContent=done+' of '+all+' validated';}
document.addEventListener('change',e=>{const b=e.target.closest('.verdict'); if(b) save(b)});
document.addEventListener('input',e=>{const b=e.target.closest('.verdict'); if(b) save(b)});
document.addEventListener('click',e=>{
  const dl=e.target.closest('.dl'); if(dl){e.preventDefault(); const a=document.createElement('a');
    a.href=URL.createObjectURL(new Blob([dl.dataset.json],{type:'application/json'})); a.download=dl.dataset.name; a.click(); return;}
  const f=e.target.closest('[data-filter]'); if(f){document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('on',x===f));
    document.querySelectorAll('article').forEach(a=>a.hidden=f.dataset.filter!=='all'&&a.dataset.group!==f.dataset.filter);}
  if(e.target.id==='export'){const out={}; document.querySelectorAll('.verdict').forEach(b=>{const v=(b.querySelector('input:checked')||{}).value||'';
    const n=b.querySelector('textarea').value; if(v||n) out[b.dataset.id]={verdict:v,note:n};});
    const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:'application/json'}));
    a.download='coaching-validation-'+stamp+'.json'; a.click();}});
load();
"""


def render_html(report: dict[str, Any]) -> str:
    rows = report["rows"]
    stamp = f"{report['date']}-{report['provider']}-{report['model']}"
    counts = {key: sum(group_of(r["id"]) == key for r in rows) for key, _ in GROUPS}
    filters = '<button class="on" data-filter="all">All ' + str(len(rows)) + "</button>" + "".join(
        f'<button data-filter="{key}">{label} {counts[key]}</button>' for key, label in GROUPS if counts[key])
    cases = "\n".join(_case(row, i) for i, row in enumerate(rows))
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coaching answers to validate</title><style>{_CSS}</style></head>
<body data-stamp="{html.escape(stamp)}">
<header><h1>Coaching answers to validate: {html.escape(report["provider"])} / {html.escape(report["model"])} ({html.escape(report["date"])}, commit {html.escape(report["commit"])})</h1>
<div class="row">{filters}<span class="chip" id="progress"></span><button id="export">Export my verdicts (JSON)</button></div></header>
<main>
<p class="muted">Each card holds the answer, the automatic checks and the replay it is about. Judge scores are subjective, read the checks and the replay first.
Your verdicts are saved in this browser only; use Export to keep them.</p>
{cases}
</main><script>{_JS}</script></body></html>'''
