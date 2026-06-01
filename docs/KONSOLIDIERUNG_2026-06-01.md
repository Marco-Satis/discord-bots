# Konsolidierung master ↔ main — State + Plan (2026-06-01)

> **Zweck:** Compact-Survival-Doku. Bei Resume: diese Datei zuerst lesen, dann Plan ab "Resume-Schritte" ausführen.

## Situation

Zwei divergierte Linien, gemeinsamer Vorfahr **`9c680a5`** (FastAPI-Depends-Refactor):

| Linie | Branch | Tip | VERSION | Wo lokal | Status |
|---|---|---|---|---|---|
| Session (V5/Updater/manual_stop) | `main` (= `origin/main`) | `8752139` | 4.3.0 | **Worktree** `.claude/worktrees/vibrant-dirac-34259b/` | deployed auf Server, auf GitHub |
| Parallel-Entwicklung | `master` (= `origin/master`) | `fa08dbf` | 4.1.0 | **Haupt-Ordner** `DIscord_Bots/` | auf GitHub gesichert, NICHT deployed |

**Beide auf GitHub** (`Marco-Satis/discord-bots`) — nichts kann verloren gehen. Server läuft `main`-Code.

## Ziel (Marco)

Projekt mit Repo vergleichen, das **Neuere/Bessere** je File übernehmen (Hinweis: Änderungsdatum), **keine Funktion verlieren**, Code am Ende **aktuell + sauber**. Vorab: Projektordner (master) ist „größtenteils aktuell" — stimmt für die ~40 Module die NUR master änderte; Dashboard/Updater/manual_stop sind aber NUR in main (heute, deployed).

## Datei-Kategorien (ermittelt via `git diff 8752139 fa08dbf`)

### A) NUR in main — aus main übernehmen (7 .py + 13 Template/Static)
`.py`: `modules/levelup_card.py`, `modules/monitoring/manual_stop_state.py`, `scripts/mc_mod_lockfile.py`, `scripts/md_to_docx.py`, `tests/test_manual_stop_state.py`, `utils/async_tasks.py`, `utils/channel.py`
Templates/Static: `web/templates/base_v5.html`, `web/static/_preview/v5*.{css,html}`, `web/static/tailwind/{_tokens,output}.css`, `web/static/fonts/geist/*`

### B) NUR in master — aus master übernehmen (8 .py)
`scripts/rcon_op.py`, `web/tools/build_css.py`, `tests/test_db_retry.py`, `tests/test_sat_debug.py`, `tests/test_sat_status.py`, `tests/test_server_bots.py`, `tests/test_server_dashboard.py`, `tests/test_server_update.py`

### C) In BEIDEN modifiziert (59 .py) — 3-Wege-Merge entscheidet
**Vom Session-Worktree (main) geändert (main-autoritativ bei Konflikt):**
`cogs/scheduler_cog.py`, `cogs/leveling_cog.py`, `modules/leveling.py`, `modules/monitoring/health_checker.py`, `modules/monitoring/service_watchdog.py`, `modules/network/port_monitor.py`, `modules/system/package_checker.py`, `web/routes/dashboard.py`, `web/routes/sse_route.py`, `web/routes/system_route.py`, `web/routes/server_detail_route.py`, `tests/test_routes.py`

**Restliche 47 (NUR master änderte → master-Version i.d.R. neuer/besser):**
u.a. `modules/minecraft/{chat_bridge,rcon,neoforge_updater,world_analyzer}.py`, `modules/security/ssl_monitor.py`, `modules/system/disk_guard.py`, `modules/notifications/{discord_notifier,email_notifier}.py`, `modules/{audit_logger,alert_dedup,timeout_manager,temp_voice_views,server_backup}.py`, `modules/backup/*`, `modules/database/{db_manager,search_indexer}.py`, `bots/*.py`, `web/app.py`, viele `web/routes/*`, etc.

**ECHTE Konflikt-Kandidaten** (beide Seiten dieselben Zeilen → manuell mit mtime-Hinweis):
`scheduler_cog.py`, `health_checker.py`, `service_watchdog.py`, `package_checker.py`, `leveling.py`, `port_monitor.py`, `db_manager.py` (master DB-Write-Retry vs main), `CHANGELOG.md`, `CLAUDE.md`, `VERSION`.

## Strategie: 3-Wege-Merge (kein whole-file-Picking)

Git nutzt Vorfahr `9c680a5` → merged nicht-überlappende Hunks BEIDER Seiten automatisch (kein Funktionsverlust), flaggt nur echte Zeilen-Konflikte. Das ist überlegen gegenüber mtime-basiertem Ganzdatei-Ersetzen.

mtime-Hinweis von Marco = Tiebreaker NUR bei echten Konflikt-Hunks (neuere Datei gewinnt den Hunk, sofern inhaltlich nicht schlechter).

## Resume-Schritte (nach Compact)

1. **Diese Datei lesen** + `git -C <MR> log --oneline -1 master` + `... main` (Tips bestätigen: master=fa08dbf, main=8752139).
2. **Entscheidung Merge-Richtung** (Marco fragen falls unklar): empfohlen — Konsolidierungs-Branch `consolidate` von `main` (neuer, deployed) abzweigen, dann `master` rein-mergen → main-Basis behält V5/Updater, master-Module kommen via Merge dazu.
   ```
   git -C <MR> fetch origin
   git -C <MR> checkout -b consolidate origin/main
   git -C <MR> merge origin/master   # 3-Wege via 9c680a5
   ```
3. **Konflikte auflösen** (erwartete Liste oben, ~10 Files). Pro Konflikt: beide Seiten-Funktionen erhalten (manual_stop AUS main + parallele Änderung AUS master kombinieren, nicht eine wegwerfen). mtime/Inhalt als Tiebreaker.
4. **VERSION** → 4.4.0 (Konsolidierung), **CHANGELOG** Merge-Eintrag.
5. **Verifikation:** alle 4 Tests (`test_imports/routes/cogs/env_completeness`) + `test_manual_stop_state` + `py_compile` aller geänderten + Jinja-Compile aller Templates.
6. **`/review`** auf den Merge-Diff (Functional-Correctness, dass keine Funktion gekappt wurde).
7. **Server-Abgleich:** prüfen welche master-Module live auf Server fehlen (Server hat main-Code) → nach Konsolidierung neu deployen.
8. **Push** `consolidate` → später `main` (nach Marco-OK). Branch-Name-Hinweis: push via `git push origin HEAD:main` (lokaler Branch ≠ main).

## Wichtige Fakten / Stolpersteine

- **Worktree-Push-Eigenheit:** lokaler Branch heißt `claude/vibrant-dirac-34259b`, trackt `origin/main` → push immer `git push origin HEAD:main`.
- **pre-push detect-secrets-Hook** aktiv. Baseline `.secrets.baseline` (9 acknowledged FP: HTMX-SRI-Hashes + Test-Platzhalter). Neue FP → Baseline regenerieren: `detect-secrets scan --exclude-files 'graphify-out/.*' > .secrets.baseline`.
- **GitHub 100MB-Limit:** `web/tools/tailwindcss.exe` (125MB) ist gitignored — NICHT committen.
- **gitignored (master):** `graphify-out/`, `.claude/worktrees/`, `=*` (pip-Junk), `*.exe`, `web/tools/`.
- **Bash cwd resettet** immer auf Worktree → für Haupt-Repo `git -C "C:/Users/Marco/OneDrive/Dokumente/DIscord_Bots" ...` nutzen (kein `cd`).
- **sudo via PuTTY** (Bash-Tool kann kein sudo-TTY). Deploy: scp nach /tmp → Marco `sudo cp`.
- **Server-Update-Wrapper** (root-owned, schon installiert): `/usr/local/sbin/dashboard-apt-upgrade` + `-fullupgrade`.

## Session-Arbeit die in main steckt (NICHT verlieren beim Merge)

V5-Dashboard-Redesign (base_v5 + v5_components.css, 9 Seiten), `manual_stop_state` (4 Consumer + Self-Heal in health_checker/watchdog/scheduler/port_monitor), Updater-Überarbeitung (apt-get -s Parser, Full-Upgrade-Button, Root-Wrapper, Reboot-Banner, Events-0-Update-Filter), Level-Up-Card (Pillow), Doku v4.3.0 + docx.
