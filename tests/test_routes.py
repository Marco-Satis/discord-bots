"""
Statisches Test-Skript fuer das Web-Dashboard.

Prueft ohne FastAPI (rein per Datei-Analyse und Regex):
  1. Registrierte Router in web/app.py
  2. Definierte Routen in web/routes/*.py und web/auth.py
  3. HTMX-URLs in allen HTML-Templates
  4. Ob alle HTMX-URLs auf existierende Routen zeigen
  5. Template-Referenzen ({% include %}, {% extends %})
  6. Ob alle referenzierten Templates als Dateien existieren

Aufruf:
    python tests/test_routes.py
"""

import os
import re
import sys
from pathlib import Path

# =========================================================================
#  Pfade
# =========================================================================

# Projekt-Root ermitteln (tests/ liegt direkt im Projekt-Root)
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

WEB_DIR = PROJECT_ROOT / "web"
APP_FILE = WEB_DIR / "app.py"
AUTH_FILE = WEB_DIR / "auth.py"
ROUTES_DIR = WEB_DIR / "routes"
TEMPLATES_DIR = WEB_DIR / "templates"

# =========================================================================
#  Hilfsfunktionen
# =========================================================================

def read_file(filepath: Path) -> str:
    """Liest eine Datei und gibt den Inhalt als String zurueck."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (IOError, OSError) as e:
        print(f"  WARNUNG: Konnte {filepath} nicht lesen: {e}")
        return ""


def collect_files(directory: Path, pattern: str) -> list[Path]:
    """Sammelt alle Dateien in einem Verzeichnis nach Glob-Pattern."""
    if not directory.exists():
        return []
    return sorted(directory.rglob(pattern))

# =========================================================================
#  1. Registrierte Router in app.py
# =========================================================================

def find_registered_routers(app_source: str) -> list[str]:
    """
    Findet alle app.include_router(...) Aufrufe in app.py.
    Gibt die Variable-Namen der Router zurueck.
    """
    pattern = re.compile(r"app\.include_router\((\w+)\)")
    return pattern.findall(app_source)


def find_router_imports(app_source: str) -> dict[str, str]:
    """
    Findet alle Router-Imports in app.py.
    Gibt ein Dict zurueck: { variable_name: "modul.pfad" }
    """
    # Muster: from web.routes.dashboard import router as dashboard_router
    # oder:   from web.auth import auth_router
    results = {}

    # Muster 1: from X import router as Y
    pattern_as = re.compile(
        r"from\s+([\w.]+)\s+import\s+router\s+as\s+(\w+)"
    )
    for match in pattern_as.finditer(app_source):
        module_path, var_name = match.groups()
        results[var_name] = module_path

    # Muster 2: from X import auth_router (oder aehnlich, direkt ohne 'as')
    pattern_direct = re.compile(
        r"from\s+([\w.]+)\s+import\s+(\w+_router)"
    )
    for match in pattern_direct.finditer(app_source):
        module_path, var_name = match.groups()
        if var_name not in results:
            results[var_name] = module_path

    return results

# =========================================================================
#  2. Definierte Routen in Route-Dateien
# =========================================================================

def find_defined_routes(source: str, filepath: Path) -> list[dict]:
    """
    Findet alle @router.get/post/put/delete/patch(...) Dekoratore.
    Beruecksichtigt auch @auth_router.get/post(...) in auth.py.

    Gibt eine Liste von Dicts zurueck:
        { "method": "GET", "path": "/config", "file": filepath }
    """
    routes = []

    # Muster: @router.get("/path" ...) oder @auth_router.post("/path" ...)
    # Erkennt: @router.get, @auth_router.get, @app.websocket usw.
    pattern = re.compile(
        r"@(\w+)\.(get|post|put|delete|patch)"
        r'\(\s*"([^"]*)"',
        re.IGNORECASE,
    )

    for match in pattern.finditer(source):
        obj_name = match.group(1)      # z.B. "router", "auth_router", "app"
        method = match.group(2).upper()
        path = match.group(3)

        # Nur Router-Objekte beruecksichtigen (nicht z.B. @app.on_event)
        if "router" in obj_name or obj_name == "app":
            routes.append({
                "method": method,
                "path": path,
                "file": filepath,
            })

    return routes


def find_router_prefix(source: str) -> str:
    """
    Findet den Prefix eines APIRouter, z.B. APIRouter(prefix="/api/analytics").
    Gibt den Prefix-String zurueck oder "" wenn keiner gesetzt.
    """
    pattern = re.compile(r'APIRouter\([^)]*prefix\s*=\s*"([^"]*)"')
    match = pattern.search(source)
    return match.group(1) if match else ""

# =========================================================================
#  3. HTMX-URLs in Templates
# =========================================================================

def find_htmx_urls(source: str, filepath: Path) -> list[dict]:
    """
    Findet alle hx-get="..." und hx-post="..." URLs in einem Template.

    Gibt eine Liste von Dicts zurueck:
        { "method": "GET", "url": "/errors", "file": filepath }
    """
    urls = []

    pattern = re.compile(r'hx-(get|post)\s*=\s*"([^"]*)"', re.IGNORECASE)

    for match in pattern.finditer(source):
        method = match.group(1).upper()
        url = match.group(2)
        urls.append({
            "method": method,
            "url": url,
            "file": filepath,
        })

    return urls

# =========================================================================
#  4. Template-Referenzen
# =========================================================================

def find_template_references(source: str, filepath: Path) -> list[dict]:
    """
    Findet alle {% include "..." %} und {% extends "..." %} Referenzen.

    Gibt eine Liste von Dicts zurueck:
        { "type": "include"|"extends", "template": "partials/xxx.html", "file": filepath }
    """
    refs = []

    pattern = re.compile(
        r'\{%\s*(include|extends)\s+"([^"]+)"\s*%\}'
    )

    for match in pattern.finditer(source):
        ref_type = match.group(1)
        template_name = match.group(2)
        refs.append({
            "type": ref_type,
            "template": template_name,
            "file": filepath,
        })

    return refs

# =========================================================================
#  5. Routen-Normalisierung fuer Vergleich
# =========================================================================

def normalize_route_path(path: str) -> str:
    """
    Normalisiert einen Routen-Pfad fuer den Vergleich.
    Entfernt Query-Parameter und ersetzt Pfad-Parameter mit Platzhaltern.

    Beispiele:
        /server/{server_id}       -> /server/{param}
        /errors?level=error       -> /errors
        /api/server/{server_id}/action -> /api/server/{param}/action
    """
    # Jinja2-Block-Tags entfernen: {% if ... %}...{% endif %} etc.
    path = re.sub(r"\{%.*?%\}", "", path)

    # Query-Parameter entfernen
    path = path.split("?")[0]

    # Jinja2-Template-Ausdruecke entfernen: {{ server.id }} -> {param}
    path = re.sub(r"\{\{[^}]+\}\}", "{param}", path)

    # FastAPI Pfad-Parameter normalisieren: {server_id} -> {param}
    path = re.sub(r"\{[^}]+\}", "{param}", path)

    # Trailing Slash normalisieren
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return path


def route_matches_url(route_path: str, htmx_url: str) -> bool:
    """
    Prueft ob eine definierte Route zu einer HTMX-URL passt.

    Beruecksichtigt:
        - Pfad-Parameter ({server_id} passt auf jeden Wert)
        - Query-Parameter werden ignoriert
        - Jinja2-Template-Ausdruecke ({{ server.id }})
        - Konkrete Werte in HTMX-URLs gegen Route-Parameter

    Beispiel:
        Route:    /admin-bot/tab/{tab_name}
        HTMX-URL: /admin-bot/tab/temp_voice
        -> True
    """
    norm_url = normalize_route_path(htmx_url)

    # Jinja2-Block-Tags auch im Route-Pfad entfernen (normalerweise nicht noetig)
    route_clean = re.sub(r"\{%.*?%\}", "", route_path)
    route_clean = route_clean.split("?")[0]

    # Route-Pfad in Regex umwandeln: {param_name} -> [^/]+
    # Zuerst Jinja2 {{ ... }} ersetzen, dann FastAPI { ... }
    route_regex = re.sub(r"\{\{[^}]+\}\}", "[^/]+", route_clean)
    route_regex = re.sub(r"\{[^}]+\}", "[^/]+", route_regex)

    # Regex-Sonderzeichen im Rest escapen (z.B. Punkte)
    # Da wir schon [^/]+ eingefuegt haben, muessen wir diese schuetzen
    parts = re.split(r"(\[\^/\]\+)", route_regex)
    escaped_parts = []
    for part in parts:
        if part == "[^/]+":
            escaped_parts.append(part)
        else:
            escaped_parts.append(re.escape(part))
    route_regex = "".join(escaped_parts)

    # Exakter Match (Anfang und Ende)
    route_regex = "^" + route_regex + "$"

    try:
        return bool(re.match(route_regex, norm_url))
    except re.error:
        # Fallback: direkter String-Vergleich
        return normalize_route_path(route_path) == norm_url

# =========================================================================
#  Hauptprogramm
# =========================================================================

def main():
    print("=" * 70)
    print("  STATISCHER ROUTEN-TEST — Discord Bot Web Dashboard")
    print("=" * 70)
    print()

    errors = []
    warnings = []

    # ------------------------------------------------------------------
    #  Existenzpruefung der Verzeichnisse
    # ------------------------------------------------------------------

    if not WEB_DIR.exists():
        print(f"FEHLER: Web-Verzeichnis nicht gefunden: {WEB_DIR}")
        sys.exit(1)
    if not APP_FILE.exists():
        print(f"FEHLER: app.py nicht gefunden: {APP_FILE}")
        sys.exit(1)

    # ------------------------------------------------------------------
    #  1. Registrierte Router in app.py
    # ------------------------------------------------------------------

    print("1. REGISTRIERTE ROUTER in app.py")
    print("-" * 50)

    app_source = read_file(APP_FILE)
    registered_routers = find_registered_routers(app_source)
    router_imports = find_router_imports(app_source)

    print(f"   Gefundene include_router()-Aufrufe: {len(registered_routers)}")
    for name in registered_routers:
        module = router_imports.get(name, "???")
        print(f"   - {name}  (aus {module})")

    # Pruefen ob alle importierten Router auch registriert sind
    for var_name, module_path in router_imports.items():
        if var_name not in registered_routers:
            msg = f"Router '{var_name}' importiert aber nicht registriert (aus {module_path})"
            warnings.append(msg)
            print(f"   WARNUNG: {msg}")

    print()

    # ------------------------------------------------------------------
    #  2. Definierte Routen in Route-Dateien
    # ------------------------------------------------------------------

    print("2. DEFINIERTE ROUTEN")
    print("-" * 50)

    all_routes = []

    # Route-Dateien durchsuchen
    route_files = collect_files(ROUTES_DIR, "*.py")
    # Auth-Datei hinzufuegen
    if AUTH_FILE.exists():
        route_files.append(AUTH_FILE)

    for route_file in route_files:
        if route_file.name == "__init__.py":
            continue

        source = read_file(route_file)
        prefix = find_router_prefix(source)
        routes = find_defined_routes(source, route_file)

        for route in routes:
            # Prefix anwenden
            route["full_path"] = prefix + route["path"]
            all_routes.append(route)

    print(f"   Gefundene Routen gesamt: {len(all_routes)}")
    print()

    # Nach Datei gruppiert ausgeben
    routes_by_file: dict[str, list[dict]] = {}
    for route in all_routes:
        fname = route["file"].name
        if fname not in routes_by_file:
            routes_by_file[fname] = []
        routes_by_file[fname].append(route)

    for fname, file_routes in sorted(routes_by_file.items()):
        print(f"   {fname}:")
        for r in file_routes:
            print(f"      {r['method']:6s} {r['full_path']}")
    print()

    # ------------------------------------------------------------------
    #  3. HTMX-URLs in Templates
    # ------------------------------------------------------------------

    print("3. HTMX-URLs in Templates")
    print("-" * 50)

    all_htmx_urls = []

    template_files = collect_files(TEMPLATES_DIR, "*.html")

    for tpl_file in template_files:
        source = read_file(tpl_file)
        htmx_urls = find_htmx_urls(source, tpl_file)
        all_htmx_urls.extend(htmx_urls)

    # Auch in Python Route-Dateien nach inline-HTML mit hx-get/hx-post suchen
    for route_file in route_files:
        if route_file.name == "__init__.py":
            continue
        source = read_file(route_file)
        htmx_urls = find_htmx_urls(source, route_file)
        all_htmx_urls.extend(htmx_urls)

    print(f"   Gefundene HTMX-URLs gesamt: {len(all_htmx_urls)}")
    print()

    # Deduplizierte Liste der eindeutigen URLs anzeigen
    seen_urls = set()
    unique_htmx = []
    for h in all_htmx_urls:
        key = (h["method"], normalize_route_path(h["url"]))
        if key not in seen_urls:
            seen_urls.add(key)
            unique_htmx.append(h)

    for h in sorted(unique_htmx, key=lambda x: x["url"]):
        rel_file = h["file"].relative_to(PROJECT_ROOT)
        print(f"   {h['method']:6s} {h['url']:45s}  ({rel_file})")
    print()

    # ------------------------------------------------------------------
    #  4. HTMX-URLs gegen definierte Routen pruefen
    # ------------------------------------------------------------------

    print("4. HTMX-URL VALIDIERUNG")
    print("-" * 50)

    # Normalisierte Routen-Map aufbauen: (method, normalized_path)
    defined_route_keys: set[tuple[str, str]] = set()
    for route in all_routes:
        norm = normalize_route_path(route["full_path"])
        defined_route_keys.add((route["method"], norm))

    matched = 0
    unmatched = 0

    for htmx in all_htmx_urls:
        url = htmx["url"]
        method = htmx["method"]

        # Dynamische Jinja2-URLs mit Pfad-Parametern pruefen
        norm_url = normalize_route_path(url)

        found = False
        for route in all_routes:
            if route["method"] == method and route_matches_url(route["full_path"], url):
                found = True
                break

        if found:
            matched += 1
        else:
            unmatched += 1
            rel_file = htmx["file"].relative_to(PROJECT_ROOT)
            msg = f"Keine Route fuer {method} {url}  (in {rel_file})"
            errors.append(msg)
            print(f"   FEHLER: {msg}")

    if unmatched == 0:
        print(f"   OK: Alle {matched} HTMX-URLs haben passende Routen.")
    else:
        print(f"\n   Ergebnis: {matched} OK, {unmatched} OHNE passende Route")
    print()

    # ------------------------------------------------------------------
    #  5. Template-Referenzen pruefen
    # ------------------------------------------------------------------

    print("5. TEMPLATE-REFERENZEN")
    print("-" * 50)

    all_template_refs = []

    for tpl_file in template_files:
        source = read_file(tpl_file)
        refs = find_template_references(source, tpl_file)
        all_template_refs.extend(refs)

    print(f"   Gefundene Referenzen: {len(all_template_refs)}")

    for ref in all_template_refs:
        rel_file = ref["file"].relative_to(PROJECT_ROOT)
        print(f"   {ref['type']:8s} \"{ref['template']}\"  (in {rel_file})")
    print()

    # ------------------------------------------------------------------
    #  6. Referenzierte Templates auf Existenz pruefen
    # ------------------------------------------------------------------

    print("6. TEMPLATE-EXISTENZPRUEFUNG")
    print("-" * 50)

    # Auch TemplateResponse-Referenzen aus Python-Code pruefen
    template_response_refs = []
    py_files = list(collect_files(ROUTES_DIR, "*.py"))
    if AUTH_FILE.exists():
        py_files.append(AUTH_FILE)

    for py_file in py_files:
        if py_file.name == "__init__.py":
            continue
        source = read_file(py_file)
        # Muster: templates.TemplateResponse("partials/xxx.html", ...)
        pattern = re.compile(r'templates\.TemplateResponse\(\s*"([^"]+)"')
        for match in pattern.finditer(source):
            template_name = match.group(1)
            template_response_refs.append({
                "template": template_name,
                "file": py_file,
            })

    # Alle referenzierten Templates pruefen (Jinja2 + TemplateResponse)
    all_refs_to_check = []

    for ref in all_template_refs:
        all_refs_to_check.append((ref["template"], ref["file"]))

    for ref in template_response_refs:
        all_refs_to_check.append((ref["template"], ref["file"]))

    # Deduplizieren
    seen_refs = set()
    unique_refs = []
    for tpl_name, source_file in all_refs_to_check:
        if tpl_name not in seen_refs:
            seen_refs.add(tpl_name)
            unique_refs.append((tpl_name, source_file))

    missing_templates = 0
    found_templates = 0

    for tpl_name, source_file in sorted(unique_refs):
        tpl_path = TEMPLATES_DIR / tpl_name
        if tpl_path.exists():
            found_templates += 1
            print(f"   OK:     {tpl_name}")
        else:
            missing_templates += 1
            rel_source = source_file.relative_to(PROJECT_ROOT)
            msg = f"Template nicht gefunden: \"{tpl_name}\"  (referenziert in {rel_source})"
            errors.append(msg)
            print(f"   FEHLER: {msg}")

    if missing_templates == 0:
        print(f"\n   Alle {found_templates} referenzierten Templates existieren.")
    else:
        print(f"\n   Ergebnis: {found_templates} OK, {missing_templates} FEHLEND")
    print()

    # ------------------------------------------------------------------
    #  7. Zusammenfassung
    # ------------------------------------------------------------------

    print("=" * 70)
    print("  ZUSAMMENFASSUNG")
    print("=" * 70)
    print()
    print(f"  Registrierte Router:          {len(registered_routers)}")
    print(f"  Definierte Routen:            {len(all_routes)}")
    print(f"  HTMX-URLs in Templates:       {len(all_htmx_urls)} ({len(unique_htmx)} eindeutig)")
    print(f"  Template-Referenzen:           {len(all_template_refs)} (Jinja2) + {len(template_response_refs)} (TemplateResponse)")
    print(f"  Referenzierte Templates:       {len(unique_refs)} eindeutig")
    print()

    if warnings:
        print(f"  WARNUNGEN: {len(warnings)}")
        for w in warnings:
            print(f"    - {w}")
        print()

    if errors:
        print(f"  FEHLER: {len(errors)}")
        for e in errors:
            print(f"    - {e}")
        print()
        print("  ERGEBNIS: FEHLGESCHLAGEN")
        print()
        sys.exit(1)
    else:
        print("  FEHLER: 0")
        print()
        print("  ERGEBNIS: ALLE PRUEFUNGEN BESTANDEN")
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
