# Vollstaendiger Review + Test + Bugfix Prompt (v6 — final)

> Alle URLs, Felder, API-Pfade gegen echten Code verifiziert.
> RCON-Interface verifiziert (async with, nicht direkte Instanz).
> CSRF-Verhalten verifiziert (laesst unauthentifizierte User durch).
> Multi-line SSH-Workaround eingebaut (Python-Skripte statt Inline-Code).
> sys.path Fix: os.getcwd() statt __file__-basiert (Skripte laufen aus /tmp/).

---

## Prompt (komplett kopieren und einfuegen):

```
Lies CLAUDE.md, PROGRESS.md und docs/OFFEN.md.

Du fuehrst einen vollstaendigen Review, Test und Bugfix des gesamten Discord Bot Systems durch — auf dem Server, nicht nur lokal. Das Ziel: Null Bugs, alles funktioniert, produktionsreif.

Dies ist eine lange Aufgabe. Plane deine Arbeit klar. Nutze den vollen Context effizient. Speichere nach jeder Phase deinen Fortschritt in docs/review/REVIEW_ERGEBNIS.md damit du nach /compact nahtlos weitermachen kannst. Stoppe keine Aufgabe frueh wegen Context-Bedenken.

Implementiere Fixes direkt statt sie nur vorzuschlagen. Deploye jeden Fix sofort auf den Server.

SSH: ssh -p 4422 marco@203.0.113.10
Bot-Pfad: /home/botuser/Discord_Bots/
Deployment: .claude/skills/deployment/SKILL.md

WICHTIG fuer SSH-Befehle: Claude Code's Bash-Tool fragt bei mehrzeiligen Befehlen nach Bestaetigung. Um das zu vermeiden: Schreibe Python-Testcode in eine lokale .py Datei, SCP sie auf den Server, fuehre sie dort aus. Keine mehrzeiligen python3 -c "..." Inline-Befehle via SSH.

Die komplette Aufgabenliste mit allen Befehlen steht in docs/review/REVIEW_PROMPT.md. Lies diese Datei und arbeite alle 7 Phasen autonom ab:

Phase 1: Tests lokal + Server-Health (Services, Logs, DB, Disk)
Phase 2: Auto-Update-System verifizieren (Module, API, Staging, A0-Fixes)
Phase 3: Bot-Funktionen testen (RCON, SAT, Scheduler)
Phase 4: Web-Dashboard komplett testen (Routes, Auth, Middleware, SSE)
Phase 5: Bekannte Bugs fixen (OFFEN.md Sektion D)
Phase 6: Code-Qualitaet (bare except, hardcoded paths, CSRF-Bug)
Phase 7: Cleanup + DASHBOARD_STATUS.md fuer Cowork + v4.1.0

Starte jetzt mit Phase 1.
```

---

## Befehle pro Phase

### PHASE 1 — TESTS + SERVER-HEALTH

Lokal:
  python tests/test_imports.py
  python tests/test_routes.py
  python tests/test_cogs.py
  python tests/test_env_completeness.py

Auf Server (einzelne SSH-Befehle, keine Mehrzeiler):
  ssh -p 4422 marco@203.0.113.10 "cd /home/botuser/Discord_Bots && python3 tests/test_imports.py"
  ssh -p 4422 marco@203.0.113.10 "sudo systemctl is-active monitor-bot gameserver-bot admin-bot web-dashboard satisfactory minecraft-bmc"
  ssh -p 4422 marco@203.0.113.10 "sudo journalctl -u monitor-bot --since '2 hours ago' --no-pager | grep -iE 'error|exception|traceback|critical' | tail -10"
  ssh -p 4422 marco@203.0.113.10 "sudo journalctl -u gameserver-bot --since '2 hours ago' --no-pager | grep -iE 'error|exception|traceback|critical' | tail -10"
  ssh -p 4422 marco@203.0.113.10 "sudo journalctl -u admin-bot --since '2 hours ago' --no-pager | grep -iE 'error|exception|traceback|critical' | tail -10"
  ssh -p 4422 marco@203.0.113.10 "sudo journalctl -u web-dashboard --since '2 hours ago' --no-pager | grep -iE 'error|exception|traceback|critical' | tail -10"
  ssh -p 4422 marco@203.0.113.10 "sqlite3 /home/botuser/Discord_Bots/data/botdata.db 'PRAGMA user_version; PRAGMA integrity_check;'"
  ssh -p 4422 marco@203.0.113.10 "sqlite3 /home/botuser/Discord_Bots/data/botdata.db '.tables'"
  ssh -p 4422 marco@203.0.113.10 "df -h /home && free -m"

Jeden Fehler sofort fixen, committen, deployen.
Fortschritt in docs/review/REVIEW_ERGEBNIS.md speichern.

---

### PHASE 2 — AUTO-UPDATE-SYSTEM

Multi-line Tests als Datei schreiben, SCP, ausfuehren:

Erstelle lokal tests/test_server_update.py:
```python
"""Server-seitiger Test fuer Auto-Update-System. Per SCP auf Server kopieren und ausfuehren."""
import sys, os, asyncio
sys.path.insert(0, os.getcwd())

def test_imports():
    from modules.minecraft.update_manager import UpdateManager
    from modules.minecraft.modpack_updater import ModpackUpdater
    from modules.minecraft.file_manager import FileManager
    from modules.minecraft.mc_countdown import MCCountdownTimer
    from modules.minecraft.neoforge_updater import NeoForgeUpdater
    from modules.monitoring.update_checker import SatisfactoryUpdateChecker
    print("Update-Module Import: OK")

async def test_curseforge():
    import aiohttp
    from dotenv import load_dotenv
    load_dotenv("config/.env")
    key = os.getenv("CURSEFORGE_API_KEY", "")
    if not key:
        print("CurseForge API: FAIL (Kein API Key)")
        return
    async with aiohttp.ClientSession() as s:
        async with s.get("https://api.curseforge.com/v1/games", headers={"x-api-key": key}) as r:
            print(f"CurseForge API: {r.status} ({'OK' if r.status == 200 else 'FAIL'})")

def test_db():
    import sqlite3
    db = sqlite3.connect("data/botdata.db")
    ver = db.execute("PRAGMA user_version").fetchone()[0]
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('modpack_updates','server_versions')").fetchall()]
    print(f"DB Version: {ver} (erwartet 4)")
    print(f"Update-Tabellen: {tables}")
    db.close()

if __name__ == "__main__":
    test_imports()
    asyncio.run(test_curseforge())
    test_db()
```

Dann:
  scp -P 4422 tests/test_server_update.py marco@203.0.113.10:/tmp/
  ssh -p 4422 marco@203.0.113.10 "cd /home/botuser/Discord_Bots && python3 /tmp/test_server_update.py"

Staging + Sudoers (Einzeiler):
  ssh -p 4422 marco@203.0.113.10 "sudo -u botuser touch /home/minecraft/.update_staging/.test && sudo -u botuser rm /home/minecraft/.update_staging/.test && echo 'Staging: OK'"
  ssh -p 4422 marco@203.0.113.10 "sudo -u botuser sudo -n -l | grep minecraft | head -5"

A0-Bugfixes lokal verifizieren (grep nach altem vs neuem Code):
  grep -n "get_event_loop\|call_later" modules/minecraft/mc_countdown.py — sollte 0 Treffer sein
  grep -n "create_task\|_30s" modules/minecraft/mc_countdown.py — sollte Treffer zeigen
  grep -n "resp.read()" modules/minecraft/neoforge_updater.py — sollte 0 Treffer sein
  (Fuer alle 8 Bugs aus docs/CODE_REVIEW_AUTO_UPDATE.md wiederholen)

---

### PHASE 3 — BOT-FUNKTIONEN

Erstelle lokal tests/test_server_bots.py:
```python
"""Server-seitiger Test fuer Bot-Funktionen. Per SCP auf Server kopieren und ausfuehren."""
import sys, os, asyncio
sys.path.insert(0, os.getcwd())

async def test_rcon():
    from modules.minecraft.rcon import MinecraftRCON
    from dotenv import load_dotenv
    load_dotenv("config/.env")
    pw = os.getenv("MC_BMC_RCON_PASSWORD", "")
    if not pw:
        print("RCON: SKIP (kein Passwort in .env)")
        return
    try:
        async with MinecraftRCON("localhost", 25575, pw, timeout=5.0) as rcon:
            r = await rcon.command("list")
            print(f"RCON BMC5: OK — {r[:80]}")
    except Exception as e:
        print(f"RCON BMC5: FAIL — {e}")

def test_sat_port():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(b"\xff\xff\xff\xff", ("localhost", 15777))
        print("SAT Query Port: OK")
    except Exception:
        print("SAT Query Port: nicht erreichbar (Server evtl. offline)")
    finally:
        s.close()

if __name__ == "__main__":
    asyncio.run(test_rcon())
    test_sat_port()
```

Dann:
  scp -P 4422 tests/test_server_bots.py marco@203.0.113.10:/tmp/
  ssh -p 4422 marco@203.0.113.10 "cd /home/botuser/Discord_Bots && python3 /tmp/test_server_bots.py"

Bot-Logs (einzelne SSH-Befehle):
  ssh -p 4422 marco@203.0.113.10 "sudo journalctl -u monitor-bot --since '30 min ago' --no-pager | grep -iE 'logged in as|ready|connected|scheduler|chat.bridge' | tail -10"
  ssh -p 4422 marco@203.0.113.10 "sudo journalctl -u gameserver-bot --since '30 min ago' --no-pager | grep -iE 'logged in as|ready|error|infinity' | tail -5"

---

### PHASE 4 — WEB-DASHBOARD

Wichtige Fakten (aus Code verifiziert):
- Auth-Prefix: /auth. Login: /auth/login (GET+POST). NICHT /login.
- Login nutzt WEB_ADMIN_PASS_HASH (bcrypt) + WEB_ADMIN_USER.
- CSRF laesst unauthentifizierte User durch (Bug — session.user ist immer None weil Auth per JWT-Cookie laeuft). Diesen Bug in REVIEW_ERGEBNIS.md dokumentieren.
- Server-IDs: satisfactory, mc_bmc, mc_vanilla, teamspeak.
- SSE: /api/sse/dashboard (5s), /api/sse/events (3s).

Erstelle lokal tests/test_server_dashboard.py:
```python
"""Server-seitiger Dashboard-Test. Per SCP auf Server kopieren und ausfuehren."""
import sys, os, asyncio
sys.path.insert(0, os.getcwd())

async def test_routes():
    import httpx
    async with httpx.AsyncClient(base_url="http://localhost:8080", follow_redirects=False, timeout=5) as c:
        # Oeffentlich (kein Auth noetig)
        tests = [
            ("/auth/login", [200]),
            ("/api/health", [200, 503]),
            ("/static/style.css", [200]),
            ("/static/htmx.min.js", [200]),
            ("/static/themes.css", [200]),
        ]
        # Geschuetzt (sollte redirect 302/303 oder 401)
        protected_pages = ["/", "/system", "/security", "/config", "/search",
                          "/errors", "/changelog", "/admin-bot", "/server/satisfactory"]
        for route in protected_pages:
            tests.append((route, [302, 303, 307]))

        # API ohne Auth (sollte 401)
        api_routes = ["/api/analytics/system", "/api/analytics/summary",
                     "/api/forecast", "/api/backup/cloud-status",
                     "/api/theme", "/api/security/ip-overview",
                     "/api/sse/dashboard"]
        for route in api_routes:
            tests.append((route, [401]))

        print("=== Dashboard Route Tests ===")
        for route, expected in tests:
            try:
                resp = await c.get(route)
                status = "OK" if resp.status_code in expected else "FAIL"
                print(f"  {status} {resp.status_code} {route} (erwartet {expected})")
            except Exception as e:
                print(f"  ERR {route}: {e}")

async def test_auth():
    """Testet authentifizierte Routes mit selbst generiertem JWT."""
    import jwt as pyjwt
    from dotenv import load_dotenv
    from datetime import datetime, timedelta, timezone
    import httpx

    load_dotenv("config/.env")
    secret = os.getenv("WEB_SECRET_KEY", "")
    if not secret or secret == "CHANGE_ME_INSECURE_DEFAULT_KEY":
        print("\nAuth-Test: SKIP (WEB_SECRET_KEY nicht gesetzt oder unsicher)")
        return

    # JWT direkt generieren (umgeht Login-Flow)
    payload = {
        "sub": "local:admin",
        "username": "admin",
        "avatar": "",
        "auth_method": "test",
        "is_owner": True,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = pyjwt.encode(payload, secret, algorithm="HS256")

    print("\n=== Authentifizierte Route Tests ===")
    async with httpx.AsyncClient(
        base_url="http://localhost:8080",
        cookies={"dashboard_token": token},
        follow_redirects=False,
        timeout=5
    ) as c:
        auth_tests = [
            ("/", [200]),
            ("/system", [200]),
            ("/security", [200]),
            ("/config", [200]),
            ("/api/analytics/summary", [200]),
            ("/api/health", [200, 503]),
            ("/api/theme", [200]),
        ]
        for route, expected in auth_tests:
            try:
                resp = await c.get(route)
                status = "OK" if resp.status_code in expected else "FAIL"
                print(f"  {status} {resp.status_code} {route}")
            except Exception as e:
                print(f"  ERR {route}: {e}")

if __name__ == "__main__":
    asyncio.run(test_routes())
    asyncio.run(test_auth())
```

Dann:
  scp -P 4422 tests/test_server_dashboard.py marco@203.0.113.10:/tmp/
  ssh -p 4422 marco@203.0.113.10 "cd /home/botuser/Discord_Bots && python3 /tmp/test_server_dashboard.py"

Dashboard-Logs:
  ssh -p 4422 marco@203.0.113.10 "sudo journalctl -u web-dashboard --since '10 min ago' --no-pager | grep -iE 'error|exception|template|jinja|500' | head -10"

---

### PHASE 5 — BEKANNTE BUGS (docs/OFFEN.md Sektion D)

5a) SAT CPU/RAM zeigt 0:
  grep -n "cpu\|ram\|psutil\|proc\|/proc" modules/monitoring/status_writer.py | head -15
  Auf Server testen, fixen, deployen.

5b) Unbekannte Ports:
  ssh -p 4422 marco@203.0.113.10 "ss -tlnp | grep -E '8081|8888|9090'"
  Dokumentieren (9090 ist vermutlich Webmin, siehe system_route.py WEBMIN_URL).

5c) Spieler-Online-Chart:
  ssh -p 4422 marco@203.0.113.10 "sudo journalctl -u monitor-bot --since '1 hour ago' | grep -iE 'stats|collector|player.*count' | tail -10"

5d) RCON sporadisch:
  ssh -p 4422 marco@203.0.113.10 "sudo journalctl -u monitor-bot --since '2 hours ago' | grep -iE 'rcon.*error|rcon.*fail|rcon.*timeout' | tail -10"

Pro Fix: lokal → testen → committen → deployen → Logs → OFFEN.md aktualisieren.

---

### PHASE 6 — CODE-QUALITAET + CSRF-BUG

6a) CSRF-Bug dokumentieren und fixen:
  Das CSRF-Middleware prueft request.session.get("user") aber Auth nutzt JWT-Cookies.
  session.user ist IMMER None → CSRF-Schutz ist effektiv deaktiviert.
  Fix: In CSRFMiddleware statt session.user den JWT-Cookie pruefen:
    from web.auth import get_current_user
    user = get_current_user(request)
  Oder: dashboard_token Cookie auf Existenz pruefen.

6b) Bare except:
  grep -rn "except:" modules/ bots/ cogs/ web/ | grep -v __pycache__ | head -10

6c) Offene File-Handles:
  grep -rn "open(" modules/ web/ | grep -v "with \|\.close()\|__pycache__" | head -10

6d) Hardcoded Pfade:
  grep -rn '"/home/' modules/ | grep -v __pycache__ | head -10

Probleme fixen + deployen.

---

### PHASE 7 — CLEANUP + ABSCHLUSS

7a) Server-Cleanup:
  ssh -p 4422 marco@203.0.113.10 "find /home/botuser/Discord_Bots -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; echo 'Pycache bereinigt'"

7b) VERSION:
  echo "4.1.0" > VERSION && git add VERSION && git commit -m "Bump VERSION to 4.1.0"
  Deployen.

7c) Dashboard-Zustandsbericht fuer Cowork:
  Erstelle docs/review/DASHBOARD_STATUS.md mit:
  - Vollstaendige Route-Map (alle Endpunkte mit HTTP-Status)
  - Template-Liste (12 Templates + 17 Partials)
  - Middleware-Status (CSRF-Bug dokumentieren!)
  - Static Assets
  - Verbesserungsvorschlaege fuer UI/UX/Features
  - Technische Schulden

7d) Dokumentation:
  - docs/review/REVIEW_ERGEBNIS.md fertigstellen (alle 7 Phasen)
  - PROGRESS.md aktualisieren
  - docs/OFFEN.md: Gefixte Bugs als erledigt
  - CHANGELOG.md: v4.1.0 Eintrag

7e) Finaler Commit:
  git add -A && git commit -m "v4.1.0: Review abgeschlossen, Bugs gefixt, Cleanup"

---

## Verifizierte Route-Map (aus Code, nicht geraten)

Auth: /auth/login (GET+POST), /auth/discord (GET), /auth/discord/callback (GET), /auth/logout (GET)
Dashboard: / (GET), /api/events/clear (POST)
Health: /api/health, /api/health/selftest, /api/health/auto-restart, /api/health/disk, /api/health/services, /api/health/dns, /api/health/ports
System: /system (GET), /api/system/service/action (POST), /api/system/packages/list|check|upgrade|status|history
Security: /security (GET), /api/security/ip-overview, /api/security/unban (POST), /api/security/ban-stats
Config: /config (GET+POST), /config/notifications (GET+POST), /config/login (GET+POST), /config/bot-profiles (GET+POST)
Search: /search (GET), /api/search, /api/search/reindex (POST), /api/search/stats
Server: /server/{id} (GET), /api/server/{id}/players|backups|action|mods|mods/export|mods/search|mods/check-updates|mods/update|mods/uninstall|rcon
Analytics (prefix /api/analytics): /system, /server/{id}, /players, /summary, /heatmap, /peaks, /trends, /server-comparison
Correlation (prefix /api/analytics): /correlation, /anomalies
SSE (prefix /api/sse): /dashboard, /events
Errors: /errors (GET), /api/errors/clear (POST)
Changelog: /changelog (GET)
Admin Bot: /admin-bot (GET), /admin-bot/tab/{tab_name} (GET), /admin-bot/save/{module_name} (POST)
Forecast: /api/forecast (GET)
Backup: /api/backup/cloud-status (GET)
Export (prefix /api/export): /player-sessions, /events, /stats-history, /audit-log, /command-log
Config Reload: /api/config/reload (POST), /api/config/reload/status (GET)
Webhook: /api/webhook/github (POST), /api/webhook/deploy-history (GET)
Theme: /api/theme (GET), /api/theme/toggle (POST)

Server-IDs: satisfactory, mc_bmc, mc_vanilla, teamspeak
