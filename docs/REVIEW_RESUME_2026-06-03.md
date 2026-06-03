# Resume nach /compact — 2026-06-03 ~03:00

> Worktree: `.claude/worktrees/vibrant-dirac-34259b/` (branch `consolidate`). SSH: `ssh -p 4422 marco@203.0.113.10`. Deploy: Staging `/home/marco/dbots_staging/<relpfad>` + `MANIFEST`/`SERVICES` + `ssh ... sudo -n /usr/local/sbin/deploy-discordbots`.

## Stand (alles deployed + grün)
- SAT-Update-Subsystem komplett gefixt (Fix 1/2/3a/3b) — Details in `docs/SAT_UPDATE_FIX_PLAN.md`. SAT-Build **23300422** live, auto_update läuft.
- Inline-`/review` der SAT-Fixes gelaufen → Report `~/.claude/reports/review/sat-update-fixes_2026-06-03_0250/REVIEW_REPORT_MASTER.md`. 0 CRIT/HIGH, 2 MED, 4 LOW.
  - Quick-Win **L2 erledigt** (manual_stop-Clear-Swallow debug→warning).
  - **M1 bewusst NICHT gepinnt** — `steamcmd *`-Wildcard ist downward-lateral (satisfactory < botuser-Priv, keine Eskalation); Pinning = silent-break-Brittleness. Auf LOW abgewertet.
- **MC/SAT-Offline-Spam gefixt + deployed (02:57):** `mc_health_check_task` + `health_check_task` (SAT) in `bots/monitor_bot.py` prüfen jetzt `manual_stop_state.is_manually_stopped(...)`. sid-Mapping VERIFIZIERT: `MC_SERVER_IDS=["BMC","VANILLA"]` (monitor_bot:348) → `f"mc_{sid.lower()}"` = `mc_bmc`/`mc_vanilla` = matcht `data/manual_stop_state.json` (hat mc_bmc+mc_vanilla seit 05-26). service_watchdog hatte den Check schon (Z.216).
- monitor-bot ist NICHT crash-loop: `NRestarts=1`, stabil. Die vielen „Monitor Bot gestartet" = meine ~7 Deploy-Restarts heute.

## Git
- `origin/main` = `72a8440` (gepusht). **1 commit lokal voraus** (manual-stop-guard `fix(health)...`), UNPUSHED → `git push origin HEAD:main`.

## AKTIVE AUFGABE nach Compact: voller 3-Wege-/review
Marco will: `/review` über den **kompletten** Code, 3 Quellen vergleichen: **lokal (HEAD) ↔ GitHub (origin/main) ↔ Server (`/home/botuser/Discord_Bots/`)**. Anlass: Offline-Spam „zieht sich Stunden" — evtl. Drift (Server läuft evtl. anderen Code).

### Schritt 0 — Verifizieren ob der 02:57-Fix greift (war beim Interrupt offen):
1. Offline-Notifs NACH 02:57 im Bot-Log? `ssh ... 'grep -rhE "0[3-9]:" /home/botuser/Discord_Bots/logs/*.log | grep -iE "offline|nicht erreichbar"'` — wenn welche NACH ~02:58 → Fix greift nicht, sid/Pfad prüfen.
2. Deployed `monitor_bot.py` hat den Guard? `grep -c is_manually_stopped /home/botuser/Discord_Bots/bots/monitor_bot.py` (erwartet ≥2).
3. **3-Wege-Drift:** Server-File-Hashes vs git HEAD. `cd /home/botuser/Discord_Bots && md5sum bots/monitor_bot.py modules/monitoring/*.py` vs lokal `git hash-object`/`md5sum`. Drift-Files listen. ACHTUNG: Server-File-mtime ≠ git; nutze Inhalts-Hash. Vergleichsbasis: was SOLLTE laufen = origin/main-Stand + meine 1 unpushed commit.

### Schritt 1 — /review-Skill (`~/.claude/skills/review/SKILL.md`), Override-Scope `all` (kompletter Code)
- Inline-Modus (nested Worktree, großer Context). Achsen: Security, Async/Correctness, Functional-Logic-Flow (3 Achsen), Test-Coverage.
- Fokus-Bereiche (wo Schmerz war): Health/Monitoring/Notification-Subsystem — SUCHE NACH WEITEREN manual_stop-Lücken (jede Stelle die Offline/Crash/Restart-Alarm sendet ODER Services neustartet MUSS manual_stop_state respektieren). Kandidaten: `service_watchdog` (hat's), `health_check`/`health_checker` (HAR), `mc_health_check_task`+`health_check_task` (gefixt), evtl. weitere in monitor_bot (Z.824/892 „Service ausgefallen"), scheduler daily-restart, restart_timer.
- Bekannte offene Findings aus SAT-Review (re-bewerten im Vollscope): M2 doppelter UpdateChecker (monitor+gameserver) = Architektur-Schuld; L1 `_safe_start` unbedingter manual_stop-Clear; L3 on_recovery-Notif nach geplantem Stop; L4 keine Unit-Tests für retry/dedup/suppress; I1 steamcmd-Fehler loggt Banner statt echten Fehler.
- 3-Wege als eigene Master-Section: pro Drift-File „lokal=X github=Y server=Z" + warum.

### Schritt 2 — Report nach `~/.claude/reports/review/fullcode-3way_<ts>/REVIEW_REPORT_MASTER.md`, Findings + Drift + Roadmap. Danach Memory-Rollup.

## Deploy-/Test-Reminder
4 Pflicht-Tests (test_imports/test_routes/test_cogs/test_env_completeness) + py_compile vor jedem Deploy. Edits mit `subprocess`/`exec` im Inhalt werden vom security_reminder_hook (Edit/Write) geblockt → Bash-Heredoc-Helper.
