"""Render an in-memory Chainlit chat history as a standalone HTML string."""

from collections import Counter
from datetime import datetime
from html import escape
import json
from typing import Any

from markdown_it import MarkdownIt


CSS = """
:root { color-scheme: light; font: 15px/1.7 system-ui, sans-serif;
  color: #253247; background: #f5f6f8; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1000px; margin: auto; padding: 44px 24px 80px; }
h1 { font-size: 28px; letter-spacing: -.6px; margin: 0; }
.subtitle, .note, .meta, .empty { color: #657286; }
.subtitle { margin: 5px 0 16px; }
.note { font-size: 13px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin: 22px 0; }
button { font: inherit; font-size: 13px; color: #34445a; background: white;
  border: 1px solid #d5dce5; border-radius: 7px; padding: 7px 13px; cursor: pointer; }
button:hover { background: #eef2f6; }
button:focus-visible, summary:focus-visible, a:focus-visible {
  outline: 3px solid #518cc1; outline-offset: 3px; }
.entry { background: white; border: 1px solid #dfe4eb; border-radius: 10px;
  margin: 12px 0; overflow: hidden; }
.message { padding: 20px 24px; border-left: 3px solid #729e90; }
.user { background: #f0f6fc; border-left-color: #739bcc; }
.error { border-left: 3px solid #be4242; }
.heading { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 14px; }
.heading strong { font-size: 14px; }
.meta { font-size: 12px; font-weight: normal; overflow-wrap: anywhere; }
.badge { font-size: 11px; padding: 2px 7px; border-radius: 4px;
  background: #eaf0f5; color: #516177; }
.error-badge { color: #a42f2f; background: #ffeded; }
summary { cursor: pointer; padding: 12px 18px; }
summary .heading { display: inline-flex; width: calc(100% - 24px); }
.step-body { padding: 0 22px 18px; border-top: 1px solid #edf0f4; }
.context { font-size: 12px; color: #657286; margin: 10px 0; }
a { color: #316da3; text-underline-offset: 3px; }
.content { overflow-wrap: anywhere; }
.content > :last-child { margin-bottom: 0; }
.content h1 { font-size: 23px; }
.content h2 { font-size: 20px; }
.content h3 { font-size: 17px; }
.content h1, .content h2, .content h3 { margin: 22px 0 10px; line-height: 1.4; }
.content p { margin: 12px 0; }
.content ul, .content ol { padding-left: 24px; }
code { font: 12.5px/1.65 ui-monospace, SFMono-Regular, Consolas, monospace;
  background: #eef2f5; border-radius: 4px; padding: 2px 4px; }
pre { overflow-x: auto; background: #f4f6f8; padding: 14px 16px;
  border: 1px solid #e5e9ef; border-radius: 7px; }
pre code { background: none; padding: 0; white-space: pre; }
.code-label { font-size: 11px; font-weight: 600; color: #657286;
  margin: 16px 0 -8px; text-transform: uppercase; }
.table-scroll { overflow-x: auto; margin: 16px 0; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { border-bottom: 1px solid #e0e6ec; padding: 8px 12px; text-align: left; }
th { background: #f3f6f8; }
blockquote { border-left: 3px solid #ccd7e2; margin-left: 0;
  padding-left: 16px; color: #657286; }
.raw { margin-top: 16px; font-size: 12px; }
.raw summary { color: #657286; padding: 0; }
.raw pre { max-height: 420px; }
hr { border: 0; border-top: 1px solid #e0e6ec; margin: 20px 0; }
@media (max-width: 600px) { main { padding: 24px 12px; }
  .message { padding: 15px; } .step-body { padding: 0 15px 15px; } }
@media print { :root { background: white; } main { max-width: none; padding: 0; }
  .toolbar, .raw { display: none; } pre { white-space: pre-wrap; }
  pre code { white-space: pre-wrap; } .table-scroll { overflow: visible; } }
"""

JS = """
document.querySelectorAll('[data-action]').forEach(button => {
  button.addEventListener('click', () => {
    const action = button.dataset.action;
    document.querySelectorAll('details.step').forEach(step => {
      step.open = action === 'expand';
    });
    if (action === 'collapse') {
      document.querySelectorAll('details.raw').forEach(raw => { raw.open = false; });
    }
  });
});
document.querySelectorAll('a[href^="#entry-"]').forEach(link => {
  link.addEventListener('click', () => {
    const target = document.getElementById(link.getAttribute('href').slice(1));
    if (target && target.tagName === 'DETAILS') target.open = true;
  });
});
"""


def as_text(value):
    """Keep structured or null step fields readable without dropping data."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def code_block(value, language=""):
    label = f'<div class="code-label">{escape(as_text(language))}</div>' if language else ""
    return f'{label}<pre><code>{escape(as_text(value))}</code></pre>'


def make_markdown():
    """Render Markdown without executing exported HTML or loading remote images."""
    md = MarkdownIt("commonmark", {"html": False}).enable("table")

    def render_fence(tokens, index, options, env):
        token = tokens[index]
        return code_block(token.content, token.info.strip())

    def render_image(tokens, index, options, env):
        token = tokens[index]
        return '<span class="note">[Image reference: ' + escape(token.content) + "]</span>"

    md.renderer.rules["fence"] = render_fence
    md.renderer.rules["image"] = render_image
    md.renderer.rules["table_open"] = lambda *args: '<div class="table-scroll"><table>\n'
    md.renderer.rules["table_close"] = lambda *args: '</table></div>\n'
    return md


def timestamp(value):
    text = as_text(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.isoformat(sep=" ", timespec="seconds")
    except ValueError:
        return text


def duration(step):
    """Display recorded step duration; this may differ from actual execution time."""
    try:
        start = datetime.fromisoformat(step["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(step["end"].replace("Z", "+00:00"))
        seconds = (end - start).total_seconds()
        return f" · {seconds:.2f}s" if seconds > 0 else ""
    except (KeyError, AttributeError, ValueError, TypeError):
        return ""


def chat_history_to_html(
    history: list[Any],
    *,
    title: str = "DE-Rigor Agent · Chat history",
    source_name: str = "chat_history.json",
) -> str:
    """Return offline HTML for a parsed JSON array of Chainlit step objects.

    Preserve export order and all original fields without mutating the input.
    This function performs no file I/O. Invalid history structure raises
    ValueError; values must be JSON-serializable.
    """
    if not isinstance(history, list):
        raise ValueError("Expected a JSON array of Chainlit step objects.")
    for index, step in enumerate(history, 1):
        if not isinstance(step, dict):
            raise ValueError(f"Record {index} must be a JSON object.")

    md = make_markdown()
    id_index = {
        (as_text(step.get("threadId")), as_text(step["id"])): index
        for index, step in enumerate(history, 1) if step.get("id")
    }
    counts = Counter(as_text(step.get("type")) for step in history)
    messages = sum(value for key, value in counts.items() if key.endswith("_message"))
    entries = []
    for index, step in enumerate(history, 1):
        kind = as_text(step.get("type")) or "undefined"
        message = kind.endswith("_message")
        name = as_text(step.get("name")) or kind
        stamp = timestamp(step.get("createdAt") or step.get("start"))
        status = '<span class="badge error-badge">Error</span>' if step.get("isError") else ""
        command = as_text(step.get("command"))
        if command:
            status += f'<span class="badge">/{escape(command)}</span>'
        heading = (
            f'<span class="heading"><strong>{escape(name)}</strong>{status}'
            f'<span class="meta">#{index} · {escape(stamp or "No timestamp")}'
            f'{duration(step)}</span></span>'
        )
        parent = as_text(step.get("parentId"))
        parent_index = id_index.get((as_text(step.get("threadId")), parent))
        context = ""
        if parent_index is not None:
            parent_name = as_text(history[parent_index - 1].get("name")) or "Step"
            context = (f'<div class="context">Parent: <a href="#entry-{parent_index}">'
                       f'#{parent_index} {escape(parent_name)}</a></div>')
        elif parent:
            context = f'<div class="context">Parent not included: {escape(parent)}</div>'

        content = ""
        input_text = as_text(step.get("input"))
        if input_text:
            language = step.get("showInput")
            label = f"Input · {language}" if isinstance(language, str) else "Input"
            content += code_block(input_text, label)
        output = as_text(step.get("output"))
        if output:
            language = step.get("language")
            content += (code_block(output, language) if language else md.render(output))
        if not content:
            content = '<p class="empty">No text recorded.</p>'
        raw = (
            '<details class="raw"><summary>Original JSON record</summary>'
            + code_block(json.dumps(step, ensure_ascii=False, indent=2)) + '</details>'
        )
        body = context + f'<div class="content">{content}</div>' + raw
        error_class = " error" if step.get("isError") else ""
        if message:
            role_class = " user" if kind == "user_message" else ""
            entries.append(f'<article id="entry-{index}" class="entry message{role_class}{error_class}">'
                           f'{heading}{body}</article>')
        else:
            opened = " open" if step.get("isError") or step.get("defaultOpen") else ""
            entries.append(f'<details id="entry-{index}" class="entry step{error_class}"{opened}>'
                           f'<summary>{heading}</summary><div class="step-body">{body}</div></details>')

    transcript = "\n".join(entries) or '<p class="empty">This chat history is empty.</p>'
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{escape(title)}</title>
<style>{CSS}</style>
</head>
<body><main>
<header><h1>{escape(title)}</h1>
<p class="subtitle">{escape(source_name)} · {messages} messages · {len(history) - messages} execution steps</p>
<p class="note">Records appear in export order. Timestamps retain their recorded timezone offsets.
Images, attachments and action controls are not stored by the app's JSON export.
Only text and recorded step data can be reproduced here.</p></header>
<nav class="toolbar" aria-label="Transcript controls">
<button type="button" data-action="expand">Expand all steps</button>
<button type="button" data-action="collapse">Collapse all steps</button>
</nav>
{transcript}
</main><script>{JS}</script></body>
</html>
'''
