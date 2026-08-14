"""
F28: Database Manager — Zentrale SQLite-Verwaltung

Stellt eine Shared Write-Connection plus einen Read-Pool fuer alle Module bereit.
WAL-Modus ermoeglicht gleichzeitiges Lesen und Schreiben von mehreren Prozessen
(3 Bots + Dashboard); der Read-Pool sorgt dafuer dass lange Analytics-Queries
nicht alle anderen DB-Reader im selben Prozess serialisieren.

Architektur (audit-fix 2026-05-17, perf.md F2):
  - Eine Write-Connection (`_connection`) fuer INSERT/UPDATE/DELETE.
  - Read-Pool von READ_POOL_SIZE Connections fuer SELECT-only (Round-Robin).
  - `get_db()` gibt weiter die Write-Connection (backwards-compatible).
  - `get_read_db()` neu fuer reine SELECT-Pfade in Analytics / Dashboard.
"""

import asyncio
import itertools
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import aiosqlite

from utils.config import DATA_DIR
from utils.logger import get_logger

logger = get_logger("database.manager")

# Pfad zur Haupt-Datenbank
DB_PATH = DATA_DIR / "botdata.db"

# Globale Write-Connection (eine pro Prozess)
_connection: Optional[aiosqlite.Connection] = None
_lock = asyncio.Lock()

# Read-Pool: separate Connections fuer SELECT-only, vermeidet Serialisierung
# der Write-Connection bei langen Analytics-Queries.
READ_POOL_SIZE = 2
_read_pool: list[aiosqlite.Connection] = []
_read_pool_cycle: Optional["itertools.cycle"] = None
_read_pool_lock = asyncio.Lock()

# Write-Retry bei Cross-Prozess-Lock-Kollision (4 Prozesse teilen ein WAL-File).
# busy_timeout=5000 deckt kurze Kollisionen ab; der Retry fängt den Fall ab,
# dass ein anderer Prozess den Writer länger als busy_timeout hält und SQLite
# "database is locked" wirft, statt den Fehler hart zum Caller durchzureichen.
_WRITE_RETRIES = 2
_WRITE_RETRY_BASE_DELAY = 0.05  # Sekunden, verdoppelt sich pro Versuch (50/100 ms)

# C3: Dedizierte Write-Connection NUR fuer Multi-Statement-Transaktionen.
# Begruendung: get_db() liefert eine prozessweit GETEILTE Connection, auf der
# ~98 Caller (Cogs/Routes) roh schreiben (execute()+commit()) OHNE Lock. Ein
# Lock allein auf der shared Connection wuerde Phantom-Commits durch diese rohen
# Caller NICHT verhindern (sie umgehen jeden Lock). Eine separate Connection, die
# AUSSCHLIESSLICH der lock-serialisierte transaction()-Context-Manager benutzt,
# garantiert dagegen, dass keine fremden uncommitteten Writes auf ihr liegen ->
# ROLLBACK ist sicher und atomar, ohne einen der 98 Caller anzufassen.
_txn_connection: Optional[aiosqlite.Connection] = None
_txn_lock = asyncio.Lock()


def get_db_path() -> Path:
    """Gibt den Pfad zur Datenbank zurueck."""
    return DB_PATH


async def init_db(db_path: Optional[Path] = None) -> aiosqlite.Connection:
    """
    Initialisiert die Datenbank-Verbindung und fuehrt Schema-Migrationen aus.

    Args:
        db_path: Optionaler Pfad zur DB-Datei (Standard: data/botdata.db)

    Returns:
        Die aktive aiosqlite-Connection
    """
    global _connection, DB_PATH

    if db_path:
        DB_PATH = db_path

    async with _lock:
        if _connection is not None:
            # Bereits initialisiert — Connection wiederverwenden
            return _connection

        # Datenverzeichnis sicherstellen
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Oeffne Datenbank: {DB_PATH}")
        _connection = await aiosqlite.connect(str(DB_PATH))

        # WAL-Modus fuer gleichzeitigen Zugriff (3 Bots + Dashboard)
        await _connection.execute("PRAGMA journal_mode=WAL")
        # Synchronous=NORMAL: Guter Kompromiss zwischen Sicherheit und Performance
        await _connection.execute("PRAGMA synchronous=NORMAL")
        # Foreign Keys aktivieren
        await _connection.execute("PRAGMA foreign_keys=ON")
        # Busy Timeout: 5 Sekunden warten bei Lock
        await _connection.execute("PRAGMA busy_timeout=5000")
        # Die drei folgenden schreibt .claude/rules/database.md vor, sie fehlten
        # aber. Der Default-Seitencache liegt bei 2 MB — fuer eine 82-MB-Datei
        # mit 32 MB Indizes heisst das: fast jede Abfrage geht auf die Platte.
        await _connection.execute("PRAGMA cache_size=-64000")   # 64 MB
        await _connection.execute("PRAGMA temp_store=MEMORY")   # Temp-Tabellen im RAM
        await _connection.execute("PRAGMA mmap_size=268435456")  # 256 MB

        # Row-Factory fuer dict-aehnlichen Zugriff
        _connection.row_factory = aiosqlite.Row

        # Schema-Migration ausfuehren
        from modules.database.migrations import run_migrations
        await run_migrations(_connection)

        logger.info("Datenbank initialisiert (WAL-Modus, FK aktiv)")

    # Read-Pool initialisieren (ausserhalb _lock, hat eigenen Lock)
    await _init_read_pool()
    return _connection


async def _init_read_pool() -> None:
    """Initialisiert den Read-Connection-Pool fuer SELECT-only Anfragen.

    Jede Read-Connection oeffnet die DB im read-only-Modus via URI-Form,
    Row-Factory analog zur Write-Connection. WAL-Modus muss bereits aktiv
    sein (passiert in `init_db` vor diesem Call).
    """
    global _read_pool, _read_pool_cycle

    async with _read_pool_lock:
        if _read_pool:
            return  # bereits initialisiert

        # SQLite-URI fuer read-only Mode (verhindert versehentliche Writes)
        ro_uri = f"file:{DB_PATH}?mode=ro"
        for i in range(READ_POOL_SIZE):
            try:
                conn = await aiosqlite.connect(ro_uri, uri=True)
                # busy_timeout auch fuer Reader (sollte selten triggern in WAL)
                await conn.execute("PRAGMA busy_timeout=5000")
                # Der Lesepool profitiert am meisten davon: hier laufen die
                # Analytics- und Statistik-Abfragen.
                await conn.execute("PRAGMA cache_size=-64000")
                await conn.execute("PRAGMA temp_store=MEMORY")
                await conn.execute("PRAGMA mmap_size=268435456")
                conn.row_factory = aiosqlite.Row
                _read_pool.append(conn)
            except Exception as e:
                # M43-Fix: WARNING statt ERROR — bei Fresh-Install fehlt die
                # DB-Datei noch, der ro-Connect schlaegt dann erwartbar fehl.
                logger.warning(
                    f"Read-Pool-Connection #{i} nicht initialisierbar "
                    f"(z.B. DB-Datei fehlt bei Fresh-Install): {e}"
                )

        if _read_pool:
            _read_pool_cycle = itertools.cycle(_read_pool)
            logger.info(f"Read-Pool initialisiert ({len(_read_pool)} Connections)")
        else:
            logger.warning("Read-Pool konnte nicht initialisiert werden — "
                           "Fallback auf Write-Connection fuer Reads")


async def get_db() -> aiosqlite.Connection:
    """
    Gibt die aktive Write-Datenbank-Connection zurueck.
    Initialisiert automatisch falls noetig.

    Fuer SELECT-only Queries siehe `get_read_db()` (Read-Pool, parallel).

    Returns:
        Die aktive aiosqlite-Connection (Write-faehig)

    Raises:
        RuntimeError: Wenn die DB nicht initialisiert werden konnte
    """
    global _connection

    if _connection is not None:
        return _connection

    # Auto-Init falls noch nicht geschehen
    return await init_db()


async def get_read_db() -> aiosqlite.Connection:
    """
    Gibt eine Read-Only Connection aus dem Pool zurueck (Round-Robin).

    Fuer reine SELECT-Pfade (Analytics, Dashboard, Stats). Vermeidet
    Serialisierung der Write-Connection bei langen Queries.

    Fallback: Wenn der Read-Pool leer/un-initialisiert ist, wird die
    Write-Connection zurueckgegeben (gleicher Effekt wie `get_db`).

    Returns:
        Eine aiosqlite-Connection (read-only oder Write-Fallback)
    """
    global _read_pool_cycle, _connection

    # Auto-init falls noch nicht geschehen
    if _connection is None:
        await init_db()

    if _read_pool_cycle is None or not _read_pool:
        # Fallback: Write-Connection benutzen
        return _connection if _connection is not None else await init_db()

    return next(_read_pool_cycle)


async def _ensure_txn_connection() -> aiosqlite.Connection:
    """
    Lazy-Init der dedizierten Transaktions-Connection (C3).

    Muss INNERHALB von `_txn_lock` aufgerufen werden — dadurch ist der Init
    race-frei ohne eigenen Guard. `isolation_level=None` = Autocommit, was
    sauberes manuelles `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` ermoeglicht
    (kein Konflikt mit der impliziten Transaktionsverwaltung von sqlite3).
    """
    global _txn_connection
    if _txn_connection is not None:
        return _txn_connection

    # Garantiert, dass DB-Datei + Schema + Migrationen existieren.
    await get_db()

    conn = await aiosqlite.connect(str(DB_PATH), isolation_level=None)
    # WAL ist DB-weit bereits aktiv (von der Write-Connection gesetzt, persistent).
    # busy_timeout + foreign_keys sind per-Connection und muessen hier erneut.
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = aiosqlite.Row
    _txn_connection = conn
    logger.info("Transaktions-Connection initialisiert (dediziert, Autocommit + manuelles BEGIN)")
    return _txn_connection


async def _begin_immediate(conn: aiosqlite.Connection) -> None:
    """`BEGIN IMMEDIATE` mit Retry bei Cross-Prozess-Write-Lock (analog DBHelper.execute)."""
    delay = _WRITE_RETRY_BASE_DELAY
    for attempt in range(1, _WRITE_RETRIES + 1):
        try:
            await conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as e:
            locked = "locked" in str(e).lower() or "busy" in str(e).lower()
            if not locked or attempt == _WRITE_RETRIES:
                raise
            logger.warning(
                f"transaction(): BEGIN gesperrt (Versuch {attempt}/{_WRITE_RETRIES}), "
                f"Retry in {delay * 1000:.0f}ms: {e}"
            )
            await asyncio.sleep(delay)
            delay *= 2


@asynccontextmanager
async def transaction():
    """
    Atomarer Multi-Statement-Write-Context (C3 — Write-Lock + sicherer Rollback).

    Nutzung::

        async with transaction() as conn:
            await conn.execute("DELETE FROM x WHERE ...")
            await conn.execute("INSERT INTO x ...")
        # -> COMMIT bei Erfolg, ROLLBACK bei Exception (re-raised).

    WICHTIG: Im Block NUR die geyieldete `conn` direkt benutzen
    (`await conn.execute(...)`). KEIN `DBHelper.execute()` / `get_db()`-Write —
    die laufen auf der SHARED Connection und waeren nicht Teil dieser Transaktion
    (und ein verschachtelter Lock-Erwerb gibt es hier ohnehin nicht).

    Garantien:
      - Per-Prozess serialisiert via `_txn_lock` (kein Interleave mit anderer
        `transaction()`), auf einer DEDIZIERTEN Connection -> keine fremden
        uncommitteten Writes -> `ROLLBACK` verwirft NUR diese Transaktion.
      - Cross-Prozess atomar via `BEGIN IMMEDIATE` + `busy_timeout`
        (SQLite-Write-Lock ueber das gemeinsame WAL-File).
    """
    async with _txn_lock:
        conn = await _ensure_txn_connection()
        await _begin_immediate(conn)
        try:
            yield conn
        except BaseException:
            try:
                await conn.execute("ROLLBACK")
            except Exception as rb_err:  # noqa: BLE001 — Rollback-Fehler nicht den Original maskieren lassen
                logger.error(f"transaction(): ROLLBACK fehlgeschlagen: {rb_err}")
            raise
        else:
            await conn.execute("COMMIT")


async def close_db() -> None:
    """
    Schliesst die Datenbank-Verbindungen sauber (Write + Read-Pool + Txn).
    Fuehrt vorher einen WAL-Checkpoint durch.
    """
    global _connection, _read_pool, _read_pool_cycle, _txn_connection

    # Erst Read-Pool schliessen (Read-Connections koennen WAL-Checkpoint blockieren)
    async with _read_pool_lock:
        for conn in _read_pool:
            try:
                await conn.close()
            except Exception as e:
                logger.warning(f"Fehler beim Schliessen einer Read-Pool-Connection: {e}")
        if _read_pool:
            logger.info(f"Read-Pool geschlossen ({len(_read_pool)} Connections)")
        _read_pool = []
        _read_pool_cycle = None

    # C3: Dedizierte Transaktions-Connection schliessen. Der _txn_lock-Erwerb
    # wartet auf eine evtl. noch laufende transaction() (die committet/rollbackt
    # vor Lock-Freigabe) -> Conn hat keine offene Transaktion beim Close.
    async with _txn_lock:
        if _txn_connection is not None:
            try:
                await _txn_connection.close()
                logger.info("Transaktions-Connection geschlossen")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Fehler beim Schliessen der Transaktions-Connection: {e}")
            finally:
                _txn_connection = None

    async with _lock:
        if _connection is None:
            return

        try:
            # WAL-Checkpoint: Alle WAL-Daten in die Hauptdatei schreiben (max 10s)
            await asyncio.wait_for(
                _connection.execute("PRAGMA wal_checkpoint(TRUNCATE)"),
                timeout=10.0
            )
            logger.info("WAL-Checkpoint abgeschlossen")
        except asyncio.TimeoutError:
            logger.warning("WAL-Checkpoint Timeout nach 10 Sekunden — übersprungen")
        except Exception as e:
            logger.warning(f"WAL-Checkpoint fehlgeschlagen: {e}")

        try:
            await _connection.close()
            logger.info("Datenbank-Verbindung geschlossen")
        except Exception as e:
            logger.error(f"Fehler beim Schliessen der Datenbank: {e}")
        finally:
            _connection = None


async def check_integrity() -> bool:
    """
    Fuehrt einen Integritaetscheck der Datenbank durch.

    Returns:
        True wenn die Datenbank intakt ist
    """
    try:
        db = await get_db()
        cursor = await db.execute("PRAGMA integrity_check")
        result = await cursor.fetchone()
        ok = result[0] == "ok" if result else False
        if ok:
            logger.info("Datenbank-Integritaetscheck: OK")
        else:
            logger.error(f"Datenbank-Integritaetscheck FEHLGESCHLAGEN: {result}")
        return ok
    except Exception as e:
        logger.error(f"Integritaetscheck-Fehler: {e}")
        return False


async def get_table_counts() -> dict:
    """
    Gibt die Anzahl der Eintraege pro Tabelle zurueck.
    Nuetzlich fuer Dashboard und Monitoring.
    """
    db = await get_db()
    counts = {}
    try:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'search_%'"
        )
        tables = await cursor.fetchall()
        for table in tables:
            name = table[0]
            if not name.isidentifier():
                logger.warning(f"DB-Tabellen-Name nicht-Whitelist-konform: {name!r}, skip")
                continue
            try:
                c = await db.execute(f"SELECT COUNT(*) FROM [{name}]")  # nosec B608 - name validiert via .isidentifier()
                row = await c.fetchone()
                counts[name] = row[0] if row else 0
            except Exception as e:
                counts[name] = -1
                logger.debug(f"COUNT(*) auf Tabelle {name!r} fehlgeschlagen: {e}")
    except Exception as e:
        logger.error(f"Fehler beim Zaehlen der Tabellen: {e}")
    return counts


async def get_db_size() -> int:
    """Gibt die Datenbankgroesse in Bytes zurueck."""
    try:
        return DB_PATH.stat().st_size if DB_PATH.exists() else 0
    except OSError:
        return 0


class DBHelper:
    """
    Duenner Wrapper um aiosqlite.Connection fuer UpdateManager & Co.

    Bietet convenience-Methoden die aiosqlite nicht direkt hat:
      - fetch_one(sql, params) -> dict | None
      - execute(sql, params) -> lastrowid (fuer INSERT) oder None
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def fetch_one(
        self, sql: str, params: Tuple = ()
    ) -> Optional[Dict[str, Any]]:
        """
        Fuehrt eine Query aus und gibt die erste Zeile als dict zurueck.

        Kein Write-Retry wie in execute(): unter WAL blocken Reader nicht auf
        Writer (konsistenter Snapshot aus dem WAL). Das einzige Lock-Fenster
        fuer einen Read ist ein checkpoint(TRUNCATE), und das deckt
        busy_timeout=5000 bereits ab — ein Retry waere hier toter Code.
        """
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        # aiosqlite.Row unterstuetzt dict-aehnlichen Zugriff,
        # aber .get() braucht ein echtes dict
        return dict(row)

    async def execute(
        self, sql: str, params: Tuple = ()
    ) -> Union[int, None]:
        """
        Fuehrt ein INSERT/UPDATE/DELETE aus und committet.

        Bei "database is locked" (Cross-Prozess-Write-Kollision, die ueber
        busy_timeout hinaus dauert) wird das Statement mit exponentiellem
        Backoff bis zu _WRITE_RETRIES-mal neu versucht.

        WICHTIG: Nur der execute()-Aufruf wird retryt, NICHT der commit().
        Grund: Schlaegt execute() fehl, wurde nichts geschrieben — ein erneuter
        Versuch ist sicher. Schlaegt dagegen commit() fehl, steckt der Write
        bereits in der Transaktion; ein erneutes execute() wuerde ihn doppelt
        anwenden (doppeltes INSERT, doppeltes UPDATE x=x+1). Kein rollback als
        Ausweg: die Connection ist prozessweit geteilt, ein rollback wuerde die
        parallele Transaktion eines anderen Callers verwerfen. commit()-Fehler
        werden daher unveraendert zum Caller durchgereicht.

        Returns:
            lastrowid bei INSERT, sonst None
        """
        delay = _WRITE_RETRY_BASE_DELAY
        cursor = None  # M40-Fix: explizit init (pyright: moeglicherweise ungebunden)
        for attempt in range(1, _WRITE_RETRIES + 1):
            try:
                cursor = await self._conn.execute(sql, params)
                break
            except sqlite3.OperationalError as e:
                locked = "locked" in str(e).lower() or "busy" in str(e).lower()
                if not locked or attempt == _WRITE_RETRIES:
                    raise
                logger.warning(
                    f"DB gesperrt (Versuch {attempt}/{_WRITE_RETRIES}), "
                    f"Retry in {delay * 1000:.0f}ms: {e}"
                )
                await asyncio.sleep(delay)
                delay *= 2

        # Schleife verlaesst man nur via break (cursor gesetzt) oder raise.
        assert cursor is not None  # noqa: S101 — Typ-Narrowing fuer pyright
        await self._conn.commit()
        return cursor.lastrowid
