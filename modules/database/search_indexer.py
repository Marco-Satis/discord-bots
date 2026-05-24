"""
F55: Dashboard-Volltextsuche — SQLite FTS5-basierter Suchindex.

Indiziert Daten aus mehreren Tabellen (events, players, audit_log,
command_log, backup_history, custom_commands) in den FTS5 search_index.
Unterstuetzt Full-Reindex und inkrementelle Updates.
"""

import json
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from utils.logger import get_logger

logger = get_logger("database.search_indexer")

# Tabellen-Definitionen fuer die Indizierung
# Jeder Eintrag: (table, source_name, id_col, content_cols, timestamp_col)
INDEX_SOURCES = [
    {
        "table": "events",
        "source": "event",
        "id_col": "id",
        "content_template": "{event_type}: {message}",
        "columns": ["event_type", "message", "category", "details"],
        "timestamp_col": "timestamp",
    },
    {
        "table": "players",
        "source": "player",
        "id_col": "id",
        "content_template": "Spieler: {name} (Server: {server_type})",
        "columns": ["name", "server_type"],
        "timestamp_col": "last_seen",
    },
    {
        "table": "audit_log",
        "source": "audit",
        "id_col": "id",
        "content_template": "{action}: {target} — {details}",
        "columns": ["action", "target", "details", "user_name"],
        "timestamp_col": "timestamp",
    },
    {
        "table": "command_log",
        "source": "command",
        "id_col": "id",
        "content_template": "/{command_name} von {user_name}",
        "columns": ["command_name", "user_name", "error_message"],
        "timestamp_col": "timestamp",
    },
    {
        "table": "backup_history",
        "source": "backup",
        "id_col": "id",
        "content_template": "Backup: {filename} ({server_type}, {backup_type})",
        "columns": ["server_type", "backup_type", "filename"],
        "timestamp_col": "created_at",
    },
    {
        "table": "custom_commands",
        "source": "custom_cmd",
        "id_col": "id",
        "content_template": "!{name}: {response}",
        "columns": ["name", "response", "category"],
        "timestamp_col": "created_at",
    },
]


class SearchIndexer:
    """
    FTS5-basierter Suchindex fuer das Dashboard.

    Indiziert Daten aus mehreren Tabellen in den search_index.
    Unterstuetzt Full-Reindex und inkrementelle Updates.

    Usage:
        indexer = SearchIndexer()
        await indexer.full_reindex()            # Kompletter Neuaufbau
        await indexer.incremental_update()       # Nur neue Eintraege
        results = await indexer.search("query")  # Suche
    """

    def __init__(self) -> None:
        # Trackt den letzten indizierten ID-Wert pro Tabelle
        self._last_ids: Dict[str, int] = {}

    async def _get_db(self):
        """Holt die DB-Verbindung."""
        from modules.database.db_manager import get_db
        return await get_db()

    async def _fts5_available(self) -> bool:
        """Prueft ob die FTS5-Tabelle existiert."""
        try:
            db = await self._get_db()
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='search_index'"
            )
            row = await cursor.fetchone()
            return row is not None
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Full Reindex
    # ------------------------------------------------------------------

    async def full_reindex(self) -> Dict[str, Any]:
        """
        Loescht den gesamten Index und baut ihn komplett neu auf.

        Returns:
            Dict mit Statistiken (indexed_total, per_source, duration_ms)
        """
        if not await self._fts5_available():
            return {"error": "FTS5 search_index Tabelle nicht verfuegbar"}

        start = datetime.now(timezone.utc)
        db = await self._get_db()
        total = 0
        per_source: Dict[str, int] = {}

        try:
            # Index leeren
            await db.execute("DELETE FROM search_index")

            for src in INDEX_SOURCES:
                count = await self._index_table(db, src, from_id=0)
                per_source[src["source"]] = count
                total += count

            await db.commit()

            # Last-IDs aktualisieren
            await self._update_last_ids(db)

            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
            logger.info(
                f"Full Reindex abgeschlossen: {total} Eintraege in {elapsed:.0f}ms"
            )
            return {
                "indexed_total": total,
                "per_source": per_source,
                "duration_ms": round(elapsed),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error(f"Full Reindex fehlgeschlagen: {e}")
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Incremental Update
    # ------------------------------------------------------------------

    async def incremental_update(self) -> Dict[str, Any]:
        """
        Indiziert nur neue Eintraege seit dem letzten Lauf.

        Returns:
            Dict mit Statistiken (new_entries, per_source)
        """
        if not await self._fts5_available():
            return {"error": "FTS5 search_index Tabelle nicht verfuegbar"}

        db = await self._get_db()

        # Beim ersten Aufruf: Last-IDs aus DB laden
        if not self._last_ids:
            await self._update_last_ids(db)

        total_new = 0
        per_source: Dict[str, int] = {}

        try:
            for src in INDEX_SOURCES:
                table = src["table"]
                from_id = self._last_ids.get(table, 0)
                count = await self._index_table(db, src, from_id=from_id)
                per_source[src["source"]] = count
                total_new += count

            if total_new > 0:
                await db.commit()

            # Last-IDs aktualisieren
            await self._update_last_ids(db)

            logger.debug(f"Incremental Update: {total_new} neue Eintraege")
            return {
                "new_entries": total_new,
                "per_source": per_source,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error(f"Incremental Update fehlgeschlagen: {e}")
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Suche
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        source_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fuehrt eine FTS5-Suche durch.

        Args:
            query: Suchbegriff (FTS5 MATCH Syntax)
            limit: Maximale Anzahl Ergebnisse
            source_filter: Optional — nur Ergebnisse aus dieser Quelle

        Returns:
            Liste von Ergebnis-Dicts mit source, source_id, snippet, timestamp, rank
        """
        if not await self._fts5_available():
            return []

        if not query or not query.strip():
            return []

        # Query bereinigen: Sonderzeichen escapen
        safe_query = self._sanitize_query(query)
        if not safe_query:
            return []

        db = await self._get_db()

        try:
            sql = (
                "SELECT source, source_id, "
                # Marker statt raw HTML-Tags: erlaubt sicheres Server-side-Escape
                # in search_route bevor Template `|safe` rendert (XSS-Schutz CWE-79).
                "snippet(search_index, 2, '§HL§', '§/HL§', '...', 40) AS snippet, "
                "timestamp, rank "
                "FROM search_index "
                "WHERE search_index MATCH ? "
            )
            params: list = [safe_query]

            if source_filter:
                sql += "AND source = ? "
                params.append(source_filter)

            sql += "ORDER BY rank LIMIT ?"
            params.append(limit)

            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()

            results = []
            for row in rows:
                results.append({
                    "source": row[0],
                    "source_id": row[1],
                    "snippet": row[2],
                    "timestamp": row[3],
                    "rank": row[4],
                })

            return results

        except Exception as e:
            logger.error(f"FTS5-Suche fehlgeschlagen: {e}")
            return []

    # ------------------------------------------------------------------
    # Hilfsmethoden
    # ------------------------------------------------------------------

    async def _index_table(
        self, db, src: Dict[str, Any], from_id: int
    ) -> int:
        """Indiziert eine einzelne Tabelle ab from_id."""
        table = src["table"]
        id_col = src["id_col"]
        columns = src["columns"]
        timestamp_col = src["timestamp_col"]

        # Pruefen ob Tabelle existiert
        try:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not await cursor.fetchone():
                return 0
        except Exception:
            return 0

        # Daten lesen — Identifier-Validierung (Whitelist Defense-in-Depth)
        for ident in [table, id_col, timestamp_col, *columns]:
            if not ident.isidentifier():
                raise ValueError(f"Invalid SQL identifier in search-indexer: {ident!r}")
        col_list = ", ".join([id_col] + columns + [timestamp_col])
        sql = f"SELECT {col_list} FROM {table} WHERE {id_col} > ? ORDER BY {id_col}"  # nosec B608 - alle identifiers validiert

        try:
            cursor = await db.execute(sql, (from_id,))
            rows = await cursor.fetchall()
        except Exception as e:
            logger.debug(f"Tabelle {table} nicht lesbar: {e}")
            return 0

        count = 0
        for row in rows:
            row_id = row[0]
            # Spalten als Dict aufbauen
            col_values = {}
            for i, col_name in enumerate(columns):
                val = row[i + 1]
                col_values[col_name] = str(val) if val else ""

            timestamp = row[len(columns) + 1]

            # Content aus Template erzeugen
            try:
                content = src["content_template"].format(**col_values)
            except (KeyError, IndexError):
                content = " ".join(v for v in col_values.values() if v)

            # Details-Feld anfuegen wenn vorhanden (z.B. JSON details in events)
            if "details" in col_values and col_values["details"]:
                details_str = col_values["details"]
                # JSON-Details entpacken fuer bessere Suchbarkeit
                try:
                    details_obj = json.loads(details_str)
                    if isinstance(details_obj, dict):
                        content += " " + " ".join(
                            str(v) for v in details_obj.values() if v
                        )
                except (json.JSONDecodeError, TypeError):
                    content += " " + details_str

            # In FTS5 einfuegen
            await db.execute(
                "INSERT INTO search_index (source, source_id, content, timestamp) "
                "VALUES (?, ?, ?, ?)",
                (src["source"], str(row_id), content, str(timestamp) if timestamp else ""),
            )
            count += 1

        return count

    async def _update_last_ids(self, db) -> None:
        """Aktualisiert die letzten IDs pro Tabelle."""
        for src in INDEX_SOURCES:
            table = src["table"]
            id_col = src["id_col"]
            if not (table.isidentifier() and id_col.isidentifier()):
                logger.warning(f"INDEX_SOURCES Eintrag ungueltig: {src!r}, skip")
                continue
            try:
                cursor = await db.execute(
                    f"SELECT MAX({id_col}) FROM {table}"  # nosec B608 - identifiers validiert
                )
                row = await cursor.fetchone()
                self._last_ids[table] = row[0] if row and row[0] else 0
            except Exception:
                self._last_ids[table] = 0

    @staticmethod
    def _sanitize_query(query: str) -> str:
        """
        Bereinigt die Suchanfrage fuer FTS5 MATCH.
        Entfernt gefaehrliche Zeichen, fuegt implizite Wildcards hinzu.
        """
        # Sonderzeichen entfernen die FTS5 Syntax brechen koennten
        dangerous = set('"(){}[]^~!@#$%&')
        cleaned = "".join(c for c in query if c not in dangerous)
        cleaned = cleaned.strip()

        if not cleaned:
            return ""

        # Woerter einzeln mit * (Prefix-Match) versehen
        words = cleaned.split()
        # Maximale Wortanzahl begrenzen
        words = words[:10]

        # Jedes Wort als Prefix-Suche
        terms = []
        for word in words:
            word = word.strip()
            if word and len(word) >= 2:
                terms.append(f'"{word}"*')
            elif word:
                terms.append(f'"{word}"')

        if not terms:
            return ""

        return " ".join(terms)

    def get_stats(self) -> Dict[str, Any]:
        """Gibt aktuelle Index-Statistiken zurueck."""
        return {
            "last_ids": dict(self._last_ids),
            "sources": [s["source"] for s in INDEX_SOURCES],
        }
