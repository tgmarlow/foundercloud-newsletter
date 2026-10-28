"""Render the Build Sheet HTML."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


def render_issue(template_dir: Path, context: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("build_sheet.html.j2")
    html = template.render(**context)
    if not html.endswith("\n"):
        html += "\n"
    return html


def render_index(latest_name: str, issue_date: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>The Build Sheet from FounderCloud</title>
  <meta http-equiv="refresh" content="0; url={latest_name}">
  <style>
    body {{ margin: 0; background: #f0f0f0; font-family: Arial, Helvetica, sans-serif; color: #000; }}
    main {{ max-width: 640px; margin: 48px auto; background: #fff; padding: 32px 28px; }}
    h1 {{ font-size: 22px; margin: 0 0 8px; }}
    a {{ color: #2828da; }}
    .band {{ background: #004ab5; color: #fff; padding: 16px 18px; margin: -32px -28px 24px; }}
  </style>
</head>
<body>
  <main>
    <div class="band">
      <h1>The Build Sheet from FounderCloud</h1>
      <p style="margin:0;font-style:italic;">The people, ideas, and connections founders are building right now.</p>
    </div>
    <p>The latest issue is <a href="{latest_name}">latest.html</a> ({issue_date}).</p>
    <p>Each Monday's issue is also kept at its own dated URL.</p>
  </main>
</body>
</html>
"""
