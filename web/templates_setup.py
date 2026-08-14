"""
Eine Stelle, an der Jinja-Templates konfiguriert werden.

Bis 2026-08-14 legte jedes der 18 Web-Module seine eigene ``Jinja2Templates``
an. Jede Instanz hat eine eigene Jinja-Umgebung — wer etwas an allen Seiten
ändern wollte (einen Filter, eine globale Variable), musste es achtzehnmal tun
oder es wirkte nur auf der halben Oberfläche.

Konkreter Anlass: die Navigationsleiste in ``base.html`` und ``base_v5.html``
zählte die Spielserver auf. Ein stillgelegter Server blieb im Menü stehen, ein
neuer bekam keinen Eintrag. Damit die Leiste über die Serverliste laufen kann,
braucht jede Seite Zugriff darauf — also gehört die Liste in die
Jinja-Umgebung, und die Umgebung an eine Stelle.

Benutzung in einem Routen-Modul::

    from web.templates_setup import erstelle_templates
    templates = erstelle_templates()
"""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from modules.server_registry import alle as alle_server

TEMPLATE_DIR = Path(__file__).parent / "templates"


def erstelle_templates(verzeichnis: Path | str | None = None) -> Jinja2Templates:
    """
    Eine Jinja-Umgebung mit den projektweiten Globals.

    Args:
        verzeichnis: Template-Ordner. Ohne Angabe ``web/templates``.

    Globals:
        ``spielserver`` — die konfigurierten Spielserver in Anzeige-Reihenfolge.
        Jeder Eintrag hat ``kennung``, ``label`` und ``spiel``; die Navigation
        baut daraus ihre Einträge.
    """
    templates = Jinja2Templates(directory=str(verzeichnis or TEMPLATE_DIR))
    templates.env.globals["spielserver"] = alle_server()
    return templates
