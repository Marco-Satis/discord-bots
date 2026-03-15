"""
F55: Dashboard-Volltextsuche — Web-Route fuer die Suche.

Stellt GET /search (Template), GET /api/search (JSON-API) und
POST /api/search/reindex (Admin: Index neu aufbauen) bereit.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from utils.logger import get_logger
from web.auth import get_current_user
from modules.database.search_indexer import SearchIndexer

logger = get_logger("web.routes.search")

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["Search"])

# Globale SearchIndexer-Instanz
_indexer = SearchIndexer()

# Quellen-Labels fuer die Anzeige
SOURCE_LABELS = {
    "event": "Events",
    "player": "Spieler",
    "audit": "Audit-Log",
    "command": "Commands",
    "backup": "Backups",
    "custom_cmd": "Custom Commands",
}


@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = ""):
    """F55: Suchseite mit Ergebnissen."""
    user = get_current_user(request)
    if user is None:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/auth/login", status_code=303)

    results = []
    grouped = {}
    total = 0

    if q.strip():
        results = await _indexer.search(q, limit=50)
        total = len(results)

        # Ergebnisse nach Quelle gruppieren
        for r in results:
            source = r["source"]
            if source not in grouped:
                grouped[source] = {
                    "label": SOURCE_LABELS.get(source, source),
                    "items": [],
                }
            grouped[source]["items"].append(r)

    # Suchhistorie aus Session (letzte 5)
    recent_searches = request.session.get("recent_searches", [])
    if q.strip() and q not in recent_searches:
        recent_searches.insert(0, q)
        recent_searches = recent_searches[:5]
        request.session["recent_searches"] = recent_searches

    return templates.TemplateResponse("search.html", {
        "request": request,
        "user": user,
        "query": q,
        "results": results,
        "grouped": grouped,
        "total": total,
        "recent_searches": recent_searches,
    })


@router.get("/api/search")
async def api_search(request: Request, q: str = "", source: str = "", limit: int = 50):
    """F55: JSON-API fuer die Volltextsuche."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse(content={"error": "Nicht angemeldet"}, status_code=401)

    if not q.strip():
        return JSONResponse(content={"results": [], "total": 0, "query": ""})

    # Limit begrenzen
    limit = min(max(limit, 1), 100)

    source_filter = source if source else None
    results = await _indexer.search(q, limit=limit, source_filter=source_filter)

    return JSONResponse(content={
        "results": results,
        "total": len(results),
        "query": q,
        "source_filter": source_filter,
    })


@router.post("/api/search/reindex")
async def api_reindex(request: Request):
    """F55: Admin — Index komplett neu aufbauen."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse(content={"error": "Nicht angemeldet"}, status_code=401)

    result = await _indexer.full_reindex()

    if "error" in result:
        return JSONResponse(content=result, status_code=500)

    logger.info(
        f"Reindex durch {user.get('username', '?')}: "
        f"{result.get('indexed_total', 0)} Eintraege"
    )
    return JSONResponse(content=result)


@router.get("/api/search/stats")
async def api_search_stats(request: Request):
    """F55: Index-Statistiken."""
    user = get_current_user(request)
    if user is None:
        return JSONResponse(content={"error": "Nicht angemeldet"}, status_code=401)

    stats = _indexer.get_stats()
    return JSONResponse(content=stats)
