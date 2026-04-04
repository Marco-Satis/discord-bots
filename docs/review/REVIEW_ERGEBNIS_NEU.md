# Discord Bot System — Review (Stand: 2026-04-04)

> **Reviewer:** Claude Code (Opus 4.6)
> **Basis:** v4.1.0 | Review-Loop Pass 1

---

## Phase 0: Bestandsaufnahme

### Server-Health
| Check | Ergebnis |
|-------|----------|
| SSH | OK |
| Services (6/6) | monitor-bot, gameserver-bot, admin-bot, web-dashboard, satisfactory, minecraft-bmc — alle **active** |
| Disk | 931 GB frei (4% belegt) |
| RAM | 15.4 GB frei von 32 GB |
| DB Version | 4 (korrekt) |
| DB Integrity | ok |
| Logs (2h) | 0 Fehler in allen 4 Bot-Services |

### Konsistenzcheck (Server vs. lokal)
| Typ | Ergebnis |
|-----|----------|
| Code-Dateien (.py) | **Identisch** — gleiche Dateien auf beiden Seiten |
| Nur lokal | 5 Test-Dateien (test_sat_debug.py, test_sat_status.py, test_server_bots.py, test_server_dashboard.py, test_server_update.py) — akzeptabel |
| Nur Server | venv/-Dateien (Python-Packages) + data/-Dateien (Laufzeitdaten) — erwartet |
| docs/ | Lokal: umfangreiches Archiv. Server: flache Struktur. Keine Code-Relevanz |

---

## Phase 1: Security

| # | Fund | Datei | Zeile | Schwere | Status |
|---|------|-------|-------|---------|--------|
| S1 | WEB_SECRET_KEY hat unsicheren Hardcoded-Fallback "CHANGE_ME_INSECURE_DEFAULT_KEY". Bei fehlendem ENV-Wert koennen JWT-Tokens gefaelscht werden | web/app.py, web/auth.py | 37 | **Hoch** | **gefixt** |
| S2 | CSRF-Middleware: JWT-Cookie-Pruefung korrekt (dashboard_token) | web/middleware/csrf.py | 127 | — | verifiziert (Vorheriger Fix OK) |
| S3 | Alle Route-Handler: Auth-Checks vorhanden, Health-Routes bewusst public | web/routes/*.py | — | — | OK |
| S4 | Subprocess: Alle Aufrufe nutzen create_subprocess_exec, kein shell=True, systemctl hat Action-Whitelist | diverse | — | — | OK |
| S5 | Pfad-Traversal: server_id wird gegen VALID_SERVER_IDS validiert, keine User-Input-Pfade | web/routes/server_detail.py | — | — | OK |
| S6 | Webhook: HMAC-SHA256 Signaturverifizierung mit compare_digest | web/routes/webhook_route.py | 47-59 | — | OK |
| S7 | RCON-Route: Auth-Check, server_id-Validierung, HTML-Escaping, Laengenlimit 500 | web/routes/server_detail.py | 824-855 | — | OK |

### Fix S1: WEB_SECRET_KEY
**Vorher:** `get_env("WEB_SECRET_KEY", "CHANGE_ME_INSECURE_DEFAULT_KEY")` — bei fehlendem ENV-Wert wurde ein bekannter String als JWT-Secret genutzt.
**Nachher:** Default ist leer. Bei fehlendem Wert wird ein temporaerer 32-Byte-Hex-Key generiert und eine Warnung geloggt. Sessions ueberleben keinen Neustart (Feature, kein Bug — motiviert zur korrekten Konfiguration).

---

## Phase 2: Code-Qualitaet

| # | Fund | Datei | Zeile | Schwere | Status |
|---|------|-------|-------|---------|--------|
| C1 | time.sleep(0.1) blockiert Event-Loop. _find_process() ist sync, wird aber aus async get_status() aufgerufen | modules/satisfactory/server.py | 142 | **Mittel** | **gefixt** |
| C2 | .tmp-Datei: status_writer.py.tmp.5.1773603060648 — Ueberbleibsel | modules/monitoring/ | — | **Niedrig** | **gefixt** (geloescht) |
| C3 | Bare except: 0 Treffer | modules/, bots/, cogs/, web/ | — | — | OK |
| C4 | Offene File-Handles: Alle nutzen with-Statements | — | — | — | OK |
| C5 | Hardcoded Pfade: 12 Treffer, alle mit ENV-Fallback | — | — | — | OK (unveraendert) |
| C6 | Blocking requests: 0 Treffer (nur aiohttp) | — | — | — | OK |
| C7 | MinecraftRCON: Immer "async with", keine direkte Instanz | — | — | — | OK |
| C8 | TODO/FIXME: 0 Treffer | — | — | — | OK |

### Fix C1: time.sleep in async-Kontext
**Vorher:** `proc_info = self._find_process()` — blockierender sync-Aufruf mit time.sleep(0.1) in async get_status().
**Nachher:** `proc_info = await asyncio.get_running_loop().run_in_executor(None, self._find_process)` — laeuft in Thread-Pool, blockiert Event-Loop nicht mehr.

---

## Phase 3: Konsistenz

| # | Pruefung | Ergebnis |
|---|----------|----------|
| K1 | Logging-Level | Korrekt: error fuer echte Fehler, debug fuer erwartete Zustaende (z.B. RCON disconnect). Vorheriger Fix (RCON debug-level) weiterhin korrekt |
| K2 | Error-Handling | Konsistent: RCON-Aufrufe nutzen alle async with + spezifische Exception-Handler. HTTP-Calls nutzen aiohttp mit Timeouts |
| K3 | Config-Zugriff | Konsistent: Alle Module nutzen utils/config.get_env(). Kein direktes os.getenv() in Code-Modulen |
| K4 | Service-Zuordnung | Korrekt laut CLAUDE.md-Tabelle. Keine Fehlzuordnungen gefunden |
| K5 | DB-Transaktionen | stats_tracker.py: commit() nach execute() vorhanden. db_manager.py: autocommit-Pattern. Keine fehlenden commits |

---

## Phase 4: Performance + Dauerbetrieb

| # | Fund | Datei | Zeile | Schwere | Status |
|---|------|-------|-------|---------|--------|
| P1 | crash_history (List) waechst unbegrenzt bei jedem Crash-Event | modules/monitoring/health_check.py | 75 | **Niedrig** | **gefixt** |
| P2 | _recent_alerts (List) waechst unbegrenzt bei jedem unbekannten SSH-Login | modules/monitoring/login_audit.py | 36 | **Niedrig** | **gefixt** |
| P3 | Log-Rotation: RotatingFileHandler mit 10 MB maxBytes | utils/logger.py | 52 | — | OK |
| P4 | Rate-Limiter: Bucket-Cleanup fuer inaktive Buckets vorhanden | web/middleware/rate_limiter.py | 138 | — | OK |
| P5 | Stats-Tracker: 90-Tage Cleanup in-memory + DB | modules/monitoring/stats_tracker.py | 170 | — | OK |
| P6 | Crash-Replay: Ring-Buffer (deque maxlen=50) | modules/monitoring/crash_replay.py | 40 | — | OK |
| P7 | Performance-Monitor: deque(maxlen=288) = 24h History | modules/monitoring/performance.py | 61 | — | OK |

### Fix P1: crash_history begrenzen
**Nachher:** `_max_crash_history = 100`. Nach jedem append() wird die Liste auf die letzten 100 Eintraege getrimmt.

### Fix P2: _recent_alerts begrenzen
**Nachher:** `_max_recent_alerts = 200`. Nach jedem append() wird die Liste auf die letzten 200 Eintraege getrimmt.

---

## Fix-Loop Protokoll

### Durchlauf 1 — 2026-04-04
- **Neue Funde:** 5 (S1, C1, C2, P1, P2)
- **Gefixte Funde:** 5
- **Geaenderte Dateien:**
  - web/app.py — WEB_SECRET_KEY Fallback entfernt
  - web/auth.py — WEB_SECRET_KEY Fallback entfernt
  - modules/satisfactory/server.py — _find_process via run_in_executor
  - modules/monitoring/health_check.py — crash_history begrenzt
  - modules/monitoring/login_audit.py — _recent_alerts begrenzt
  - modules/monitoring/status_writer.py.tmp.* — geloescht
- **Lokale Test-Ergebnisse:**
  - test_imports.py: BESTANDEN (165/165 compile OK)
  - test_routes.py: BESTANDEN (0 Fehler)
  - test_cogs.py: BESTANDEN (0 Fehler)
  - test_env_completeness.py: 5 Abweichungen (bekannt, nicht neu)
- **Deployment:** Dateien auf Server hochgeladen (/tmp/), warten auf sudo-Ausfuehrung

### Durchlauf 2 — ausstehend (nach Deploy + Logs pruefen)

---

## Finale Bewertung

**5 neue Funde in Durchlauf 1** — Deploy ausstehend, dann Durchlauf 2 zur Verifizierung.

- **Gefixte Bugs gesamt:** 5
  - 1x Hoch (WEB_SECRET_KEY Hardcoded-Fallback)
  - 1x Mittel (time.sleep blockiert Event-Loop)
  - 3x Niedrig (.tmp-Datei, 2x unbegrenzte Listen)
- **Offene Punkte (bekannt):** Verweis auf docs/OFFEN.md (I2, I3, I7, I8 Auto-Update-Integration)
- **Empfehlung:** WEB_SECRET_KEY sollte in config/.env als persistenter Wert gesetzt werden (z.B. `python3 -c "import secrets; print(secrets.token_hex(32))"`). NOPASSWD-sudo fuer marco einrichten um autonomes Deployment zu ermoeglichen.
