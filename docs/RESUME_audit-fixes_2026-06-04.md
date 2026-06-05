# RESUME — Audit-Fix-Execution (Pickup nach Compact)

> 2026-06-04 nachts · Branch `feature/rbac` · alles UNCOMMITTET im Working-Tree

## ERLEDIGT + validiert (py_compile + Tests grün, Backups archiviert)

**Welle 1 (15):** M01 M02 M03 M05 M07 M09 M10 M11 M12 M20 M24 M27 M30 M33 M38
**Welle 2 (3):** M28 (rbac log-cooldown) · M31 (leveling N+1→get_all) · M32 (6 SELECT→get_read_db)
**Welle 3 / P1 Security (4) — diese Session:**
- **B2** (M13): `WEB_FALLBACK_LOGIN`-ENV-Flag (default false) gated den Fallback-Login + prominentes `[AUDIT]`-Log + `dashboard_audit.log_action` — `web/auth.py`
- **B3** (M15): trust-bewusste Client-IP via neuem `utils/client_ip.py` (X-Real-IP / XFF-LAST nur wenn Peer in `WEB_TRUSTED_PROXIES` {127.0.0.1,::1}) — `web/middleware/rate_limiter.py` + `web/auth.py:login_post`. Logik-Test grün (Spoof blockiert).
- **C1** (M06): Session-Timeout reaktiviert — `session_timeout.py` decodet jetzt `dashboard_token`-JWT (statt totem `session['user']`), `last_seen`-Sliding, 10min Idle → Logout + Cookie-Delete; absolutes Timeout via JWT-`exp` (24h).
- **C2** (M17): CSRF-Token = `HMAC(WEB_SECRET_KEY, jwt['sub'])` (stateless identity-bound), Session-Fallback für unauth — `web/middleware/csrf.py`

**Gesamt 22 Findings gefixt.** Geänderte Files (uncommittet): web/app.py, web/auth.py, web/middleware/{rate_limiter,session_timeout,csrf}.py, web/routes/{admin_bot,leveling,lfg,moderation,analytics,dashboard,export}_route.py, web/dashboard_feed.py, modules/{rbac,leveling,temp_voice}.py, modules/database/{migrations,models}.py, utils/{config,permissions,client_ip}.py, cogs/minecraft_cog.py, requirements-lock.txt + NEU utils/client_ip.py.

## OFFEN (Pickup-Reihenfolge nach Compact)

1. **P2 D1** — LOW-Sweep M34–M50 (~14, Marco-Antwort: **A = komplett**). Mechanisch → ≤5 Subagents parallel, disjunkte Files, read-tool-only, Backup `.bak.pre-fix4`, py_compile.
2. **P2 D2** — Dead-Code: `_UNSET` (modules/leveling.py:59) + ungenutzter `get_db`-Import (web/routes/analytics_route.py) + `Request`-Import (web/middleware/rate_limiter.py:25, jetzt ungenutzt).
3. **P3 B4-C** — Secret-Rotation-Doku/Skript für `GITHUB_WEBHOOK_SECRET` (webhook_route.py). Marco macht sudoers/IP-Allowlist selbst.
4. **P4 E1** — n8n-Fixes per SSH `netcup-marco` verifizieren (Marco: „erledigt, nochmal prüfen").
5. **P5** — Backups `.bak.pre-fix4*` archivieren → `~/.claude/backups/discord_audit_bak_2026-06-04/` (mv); **A1 Commit** auf feature/rbac (Marco: A = jetzt committen).
6. **P6 /review** — auf den Gesamt-Diff, verbleibende Bugs finden (Marco-Wunsch).

## Marco-Infra-offen (NICHT Claude)
- **A2/B1:** Server-Test der Lock-Pins + (optional) fastapi/starlette-Major-Bump M04. **B1-Verify-Ergebnis:** kein `request.url.path`-Branching in middleware/auth → M04 Host-Header-Bypass nicht erreichbar → Routine-Bump.
- **B3-systemd:** uvicorn `--forwarded-allow-ips=127.0.0.1` in den web-dashboard-ExecStart.
- **B4:** sudoers NOPASSWD-Minimierung + GitHub-Hook-IP-Allowlist.
- **C3:** get_db-Repository-Refactor (M29/audit-txn) = eigene **Nacht-Session** (Marco: „größter Plan").
- **E2/E3:** n8n :latest-Pin, Webhook-Auth, 2.24-Update. **E4:** Secrets→KeePass (low-prio). **E5:** FootstepPro A/B (morgen).

## Kontext-Anker
nginx → 127.0.0.1:8080 (dashboard). Tests = standalone `python tests/test_x.py` (NICHT pytest). Pyright-Import-Fehler = FP (runtime sys.path). security-reviewer→general-purpose dispatchen (worktree-fail non-git cwd). Subagent-Datei-Reads NUR via Read-Tool.
