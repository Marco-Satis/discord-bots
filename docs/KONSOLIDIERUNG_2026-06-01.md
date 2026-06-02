# Konsolidierung master ↔ main (+ Server) — State + Plan (2026-06-01 / erweitert 2026-06-02)

> **Zweck:** Compact-Survival-Doku. Bei Resume: diese Datei zuerst lesen, dann Plan ab "Resume-Schritte" ausführen.
> **2026-06-02 Erweiterung:** Server als 3. Quelle in den Vergleich aufgenommen — siehe Abschnitt "## SERVER ALS 3. QUELLE" unten. Merge-Richtung + Strategie unverändert; Phase 7 erweitert (Full-Redeploy statt nur Kandidaten-Liste).

## SERVER ALS 3. QUELLE (2026-06-02)

Live-Server `/home/botuser/Discord_Bots/` ist **kein git-Checkout** (plain deploy), `VERSION=4.1.0`, Hybrid: v4.1.0-Basis + partielle Session-Deploys. Content-Manifest (sha256, CRLF-normalisiert, 237 Files) gegen `origin/main` (221) + `origin/master` (216) verglichen → 252 Pfade union.

**Grundsatz:** Server ist **Deploy-Target, NIE autoritative Quelle** (Marco: lokal + GitHub = Wahrheit). Server-Dateien können daher keine "verlorene Funktion" sein, nur stale/intermediate Deploys.

| Bucket | n | Inhalt | Aktion |
|---|---|---|---|
| `server_unique` (≠ main & ≠ master) | 13 | alle mtime ≤ 2026-05-26 < Session-Git (06-01); 12/13 existieren in BEIDEN Bäumen, nur `scripts/rcon_op.py` ist [-X] (master-only) | **kein Hotfix-Verlust** — Merge supersedet, Redeploy bringt aktuell |
| `server_only_file` | 19 | ausschließlich `web/static/_preview/*` Design-Experimente + 1 tailwind `_demo_fragment.html` | Noise, nicht Teil der Konsolidierung; optional Server-Cleanup |
| `server_eq_master_not_main` | 21 | Server läuft master-Ära-Code (bots/, cogs/, leveling, rcon, routes…) | Redeploy nach Merge |
| `in_main_not_server` | 6 | `levelup_card.py`, `async_tasks.py`, `channel.py`, `mc_mod_lockfile.py`, `md_to_docx.py`, `tailwind/input.css` | Deploy-Kandidaten (Runtime: levelup_card/async_tasks/channel) |
| `in_master_not_server_not_main` | 9 | 6 master-Test-Files + `server_detail.py` + `basecoat.css` + `build_css.py` | via Merge in Konsolidierung, dann Deploy |

**Neuer Konflikt durch Server-Linse:** `web/routes/server_detail.py` (master) vs `server_detail_route.py` (main) = **reines Rename** (einzige `web/app.py`-Diff zw. Branches = Z.167 Import). Resolution: main-`server_detail_route.py` behalten, master-`server_detail.py` vor Drop auf unique-Funktionen prüfen + ggf. reinportieren, app.py-Z.167 = main.
**Coexistenz-Check:** `health_check.py` UND `health_checker.py` existieren in beiden Bäumen — kein Rename, beide real. Beim Merge prüfen ob beide registriert/genutzt (kein toter Dup).



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

---

## AUSFÜHRBARER MERGE-PLAN (via /lyra, 2026-06-01) — Opus 4.7 @ xhigh

**Phase 0 — Branch-Verifikation + consolidate-Branch**
```
MR="C:/Users/Marco/OneDrive/Dokumente/DIscord_Bots"
git -C "$MR" fetch origin
git -C "$MR" log --oneline -1 origin/main     # 8752139
git -C "$MR" log --oneline -1 origin/master   # fa08dbf
git -C "$MR" merge-base origin/main origin/master   # 9c680a5
git -C "$MR" checkout -b consolidate origin/main
```

**Phase 1 — Pre-Merge-Konflikt-Vorschau (Baseline VOR Merge)**
```
git -C "$MR" merge-tree $(git -C "$MR" merge-base origin/main origin/master) origin/main origin/master > /tmp/merge_preview.txt
grep -c '<<<<<<<' /tmp/merge_preview.txt
```
Erwartete Konflikt-Files (~10): scheduler_cog, health_checker, service_watchdog, package_checker, port_monitor, leveling, db_manager, CHANGELOG, CLAUDE.md, VERSION.

**Phase 2 — 3-Wege-Merge (offen halten)**
```
git -C "$MR" merge --no-commit --no-ff origin/master
git -C "$MR" diff --name-only --diff-filter=U   # Konflikt-Liste
```

**Phase 3 — Konflikt-Auflösung pro Datei (Entscheidungsbaum):**
1. Beide Seiten ändern VERSCHIEDENE Funktionen → beide Hunks behalten.
2. Beide ändern DIESELBE Funktion:
   - main = Session-Feature (manual_stop/V5/Updater) → main-Logik + master-Änderung derselben Stelle einarbeiten falls orthogonal.
   - Versions-/Format-Unterschied (CHANGELOG/VERSION/CLAUDE.md) → manuell: VERSION→4.4.0, CHANGELOG beide chronologisch, CLAUDE.md beide Status-Fakten.
   - Echter Logik-Widerspruch (z.B. db_manager Write-Retry) → Tiebreaker: (a) bessere/robustere Funktion, (b) neueres Commit-Date:
     `git -C "$MR" log -1 --format=%ci origin/master -- <f>` vs `... origin/main -- <f>`.
3. Pro Datei: `git -C "$MR" add <f>` + `python -m py_compile "$MR/<f>"`.

**Phase 4 — Verifikation vor Commit**
```
git -C "$MR" diff --cached --name-only -- '*.py' | sed "s#^#$MR/#" | xargs -r python -m py_compile
# + Jinja: Loader $MR/web/templates, jedes *.html get_template()
git -C "$MR" commit   # "merge(consolidate): master Module + main V5/Updater/manual_stop -> v4.4.0"
```

**Phase 5 — Tests (alle Pflicht)**
```
cd "$MR" && for t in test_imports test_routes test_cogs test_env_completeness test_manual_stop_state; do python tests/$t.py; echo "$t -> $?"; done
```
(test_env nur pre-existing WEB_HOST-Drift akzeptieren.)

**Phase 6 — /review auf Merge-Diff** (`git diff origin/main...consolidate`): Functional-Correctness + Data-Flow, Coverage-statt-Filtering. Pro Konflikt-Datei prüfen: main-Funktion UND master-Funktion final drin (0 gekappt).

**Phase 7 — Server-Konsolidierung (erweitert 2026-06-02):** Ziel = Live-Server von Hybrid-v4.1.0 auf saubere konsolidierte v4.4.0 bringen. Quelle: 3-Quellen-Analyse oben (Buckets). Vorgehen:
1. Deploy-Set = alle .py/Templates/Static aus `consolidate` die auf Server fehlen ODER abweichen (server_eq_master_not_main 21 + in_main_not_server 6 + in_master_not_server 9 + server_unique 13 = überschreiben mit konsolidierter Version).
2. `_preview/*` (19) NICHT deployen; optional Server-Cleanup separat.
3. Server-Backup vor Deploy: `cp -a <file> <file>.bak.$(date +%s)` (botuser) bzw. Tarball `/home/botuser/Discord_Bots`.
4. VERSION-File auf Server auf 4.4.0 setzen (war 4.1.0 — Drift).
5. Bundle nach `/tmp` scp, **Marco führt `sudo cp` + Service-Restarts via PuTTY aus** (Bash kann kein sudo-TTY). Restart-Reihenfolge: monitor-bot → sleep 10 → gameserver/admin/web-dashboard.
6. Smoke-Test: Dashboard-GET 200, Discord-Ready in journalctl, kein ERROR.
**Approval-Gate:** Server-Deploy = Marco-Freigabe (irreversibel-nah). git-Merge+Push läuft autonom davor.

**Phase 8 — VERSION 4.4.0 + CHANGELOG + Push** `git -C "$MR" push origin HEAD:main`.

**Rollback:** vor Commit `git -C "$MR" merge --abort`; nach Commit/vor Push `git -C "$MR" reset --hard origin/main`. Self-Stop: 3× Fehlschlag selbe Datei → Marco melden.

---

## AUSFÜHRUNGS-STATUS (2026-06-02) — Phasen 0–8 git-seitig ERLEDIGT

- **Merge fertig:** `consolidate` (= `origin/main` jetzt `70303eb`). Alle 15 Konflikte gelöst (beide Funktionsseiten erhalten: db_manager Pool+Retry, reaction_roles track_task+cleanup, pipeline_approval Superset, monitoring manual_stop, Frontend V5+Events-Clear). Junk (.bak/.legacy/agent-memory/docx) untracked. VERSION 4.4.0.
- **Tests grün:** test_imports(173), test_routes(80/0), test_cogs(28/161/0), test_env(exit0, pre-existing MC_VANILLA-Doku-Drift), test_manual_stop(8/8). py_compile + Jinja 0 Fehler.
- **Functional-Correctness verifiziert:** main-Kern-Features post-auto-merge intakt (manual_stop sat+MC, Updater apt-get -s, 0-Update-Filter, Levelcard, RedirectResponse-Fix); server_detail-Rename sauber (kein Orphan).
- **Gepusht:** `origin/main` `70303eb`. Beide Linien weiter auf GitHub (Rollback intakt).

### Phase 7 — Server-Redeploy: GESTAGED, wartet auf Marco-Freigabe (sudo)
- **Deploy-Set:** 55 Runtime-Files (8 neu, 47 geändert) — Server v4.1.0-Hybrid → v4.4.0. Liste: `/tmp/deploy_set_lf.txt`.
- **Bundle auf Server:** `/tmp/consolidate_v440.tar.gz` (55 Files, 240 KB, LF).
- **Deploy-Script auf Server:** `/tmp/deploy_consolidate_v440.sh` (Backup→Extract→compileall-Check→Restart→Smoke→Auto-Rollback bei Compile-Fail). Im Repo: `scripts/deploy_consolidate_v440.sh`.
- **Marco-Befehl (PuTTY):** `sudo bash /tmp/deploy_consolidate_v440.sh`
- **Rollback:** `sudo -u botuser tar -xzf /home/botuser/backup_pre_v440_<TS>.tar.gz -C /home/botuser/Discord_Bots && sudo systemctl restart monitor-bot gameserver-bot admin-bot web-dashboard`

### SICHERHEITS-BEFUND (offen)
- `docs/server_snapshot/vanilla_server.properties` (von master, jetzt auf GitHub `origin/main`+`origin/master`) enthält `rcon.password` im Klartext. RCON ist 127.0.0.1-gebunden (low real-risk), aber Hygiene-Issue. Empfehlung: RCON-PW rotieren + Snapshot scrubben (oder Snapshot ganz aus git nehmen) + ggf. git-history-cleanup. NICHT blockierend für Konsolidierung.

**Self-Check vor Push:** Konflikte alle weg | pro Datei beide Funktionen benannt | 5 Tests grün | py+Jinja-Compile | /review 0 gekappt | VERSION 4.4.0 | Branches auf GitHub (Rollback intakt) | Deploy-Liste erstellt.
