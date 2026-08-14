"""
F45: Changelog-Seite — Liest CHANGELOG.md und rendert es als HTML.

Konvertiert Markdown zu HTML mit einfachem Regex-Konverter
(keine externe Markdown-Bibliothek erforderlich).
"""

import html
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from utils.config import PROJECT_ROOT
from utils.logger import get_logger
from web.auth import require_auth

logger = get_logger("web.routes.changelog")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Changelog"])

# Pfad zur Changelog-Datei im Projekt-Root
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"


def _markdown_to_html(md_text: str) -> str:
    """
    Einfacher Regex-basierter Markdown-zu-HTML-Konverter.

    Unterstuetzt:
      - Ueberschriften (# bis ######)
      - Listen (- item)
      - Fettschrift (**text**)
      - Inline-Code (`code`)
      - Leerzeilen als <br>
    """
    lines = md_text.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        # Leerzeile — Liste beenden falls offen, dann <br>
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<br>")
            continue

        # Ueberschriften: # bis ######
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            text = _inline_formatting(text)
            html_lines.append(f"<h{level}>{text}</h{level}>")
            continue

        # Listen-Eintraege: - item oder * item
        list_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if list_match:
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            text = list_match.group(1)
            text = _inline_formatting(text)
            html_lines.append(f"<li>{text}</li>")
            continue

        # Normaler Absatz — Liste beenden falls offen
        if in_list:
            html_lines.append("</ul>")
            in_list = False

        text = _inline_formatting(stripped)
        html_lines.append(f"<p>{text}</p>")

    # Falls die Datei mit einer Liste endet
    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def _inline_formatting(text: str) -> str:
    """Wendet Inline-Formatierung an: Fettschrift und Code.

    Defense-in-Depth: escape() zuerst — verhindert XSS falls CHANGELOG.md
    jemals fremden Input enthaelt (z.B. via Merged-PR aus User-Forks).
    """
    # html.escape entfernt < > & " ' — Markdown-Marker (* `) bleiben unveraendert
    text = html.escape(text)
    # Fettschrift: **text** -> <strong>text</strong>
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Inline-Code: `code` -> <code>code</code>
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def _extract_version(md_text: str) -> str:
    """
    Extrahiert die erste Versionsnummer aus dem Changelog.

    Sucht nach Mustern wie '## v4.1.0' oder '## [4.1.0]' oder '# Version 4.1.0'.
    """
    match = re.search(r"#+\s+.*?v?(\d+\.\d+(?:\.\d+)?)", md_text)
    if match:
        return match.group(1)
    return "unbekannt"


@router.get("/changelog", response_class=HTMLResponse)
async def changelog_page(request: Request, current_user: dict = Depends(require_auth)):
    """
    Zeigt die Changelog-Seite mit dem gerenderten Inhalt von CHANGELOG.md.

    Liest die Datei, konvertiert Markdown zu HTML und zeigt
    die aktuelle Version als Badge an.
    """
    user = current_user
    # Changelog-Datei lesen
    if CHANGELOG_PATH.exists():
        try:
            md_content = CHANGELOG_PATH.read_text(encoding="utf-8")
            changelog_html = _markdown_to_html(md_content)
            version = _extract_version(md_content)
        except Exception as e:
            logger.error(f"Fehler beim Lesen der Changelog-Datei: {e}")
            changelog_html = "<p>Fehler beim Lesen des Changelogs.</p>"
            version = "unbekannt"
    else:
        logger.info("CHANGELOG.md nicht gefunden")
        changelog_html = "<p>Kein Changelog vorhanden</p>"
        version = "unbekannt"

    return templates.TemplateResponse(request, "changelog.html", {
        "request": request,
        "user": user,
        "changelog_html": changelog_html,
        "version": version,
    })
