# Fragenkatalog — Discord-Bots Audit-Fixes (offene Entscheidungen)

> Stand: 2026-06-04 · Branch `feature/rbac` · nach Full-Audit (4/4) + Fix-Welle 1+2 + Graph-Update
> Zweck: alle aktuell offenen Entscheidungen decision-ready. Antwort hinter `→ ANTWORT:` eintragen.

## Status-Kontext (worauf sich der Katalog bezieht)

- **18 Findings code-seitig gefixt + validiert** (py_compile + 131+ Test-Checks grün): M01/M02/M03/M05/M07/M09/M10/M11/M12/M20/M24/M27/M28/M30/M31/M32/M33/M38.
- Änderungen liegen **uncommittet** im Working-Tree auf `feature/rbac` (12+5 Files).
- Graph tagesaktuell (5723→7661 Nodes).
- Master-Report: `~/.claude/reports/full-audit/discord_bots_2026-06-04/MASTER_REPORT.md`.
- Backups: `~/.claude/backups/discord_audit_bak_2026-06-04/`.
- Dieser Katalog = was NOCH offen ist (Infra/Policy/Design/Cleanup/Prozess).

---

## A — Prozess (sofort, blockt den Rest)

### A1 — Jetzt committen?
**Kontext:** 18 Fixes uncommittet auf `feature/rbac`. Inkl. Marcos fertiger D3-WS-Arbeit (war auch uncommittet).
**Optionen:** (A) Jetzt committen (ein Audit-Fix-Commit auf feature/rbac) · (B) Erst alles aus B/C/D abarbeiten, dann ein großer Commit · (C) Marco committet selbst nach Review.
**Empfehlung:** A — jetzt committen (Fixes sind validiert + isoliert), dann B/C/D als Folge-Commits. Hält History granular + rollback-fähig.
→ ANTWORT: A

### A2 — Deploy-Gate für Lock-Pin-Bumps
**Kontext:** `requirements-lock.txt` bumps (PyJWT 2.13.0 / aiohttp 3.14.0 / idna 3.15) ziehen erst bei nächstem Deploy (Webhook `git pull`+`pip install`). NICHT im Server-venv getestet.
**Optionen:** (A) Vor Merge/Deploy kontrolliert `pip install -r requirements-lock.txt` + Smoke-Test am Server · (B) direkt deployen + beobachten.
**Empfehlung:** A — Webhook deployt auto, also vorher manuell testen (besonders wegen A3/M04).
→ ANTWORT: A

---

## B — Security / Infra (Server- oder Policy-Entscheidung, Claude fasst nicht unilateral an)

### B1 — M04: fastapi 0.129→0.136.3 + starlette 0.52→1.0 (Host-Header-CVE)
**Kontext:** starlette-Major-Bump (0.52→1.0) behebt Host-Header-Injection (Auth-Bypass falls Middleware auf `request.url.path` verzweigt). Major-Version = potenzielle Breaking-Changes in Middleware-API.
**Offen:** (1) Bump überhaupt? (2) Wann/wie testen?
**Optionen:** (A) Server-venv: `pip install fastapi==0.136.3` in Test/Staging → App-Smoke (Login, WS, alle Routes) → bei grün Lock-Pin · (B) erst verifizieren OB überhaupt eine Middleware auf `request.url.path` Auth-Entscheidungen trifft (wenn nein, Risiko niedriger, Bump entspannter) · (C) verschieben.
**Empfehlung:** B dann A — erst `[VERIFY]` ob path-branching existiert (ich kann das prüfen), dann kontrollierter Bump.
→ ANTWORT: B dann A

### B2 — M13: Fallback-Passwort-Login (`is_owner=True` hart)
**Kontext:** `web/auth.py:478-499` — wer `WEB_ADMIN_PASS_HASH` kennt = Owner-Total-Compromise, umgeht RBAC + Discord-OAuth.
**Offen:** Soll der Fallback-Login bleiben/abschaltbar sein?
**Optionen:** (A) Per ENV-Flag `WEB_FALLBACK_LOGIN=false` deaktivierbar + im Prod aus · (B) Behalten, aber starke Passwort-Policy + prominentes Audit-Log bei jedem Fallback-Login · (C) ganz entfernen (nur Discord-OAuth).
**Empfehlung:** A+B — ENV-Flag (default aus in Prod) UND Audit-Log. Code-bar von mir sobald entschieden.
→ ANTWORT:A+B

### B3 — M15: X-Forwarded-For-Trust-Boundary
**Kontext:** `rate_limiter.py:164` + `auth.py:423` nehmen ungeprüft erstes XFF-Element → Rate-Limit-Bypass (Login-Brute-Force) + IP-Spoofing in Audit/Ban-Logs.
**Offen:** Läuft die App hinter trusted Reverse-Proxy (nginx)? Welche Proxy-IP(s)?
**Optionen:** (A) uvicorn `--forwarded-allow-ips=<proxy-ip>` (systemd ExecStart) + Middleware nutzt XFF nur dann · (B) Middleware liest `scope["client"]` statt XFF wenn kein trusted-Proxy gesetzt.
**Empfehlung:** A+B kombiniert. Brauche von dir: Proxy-Setup-Info (nginx? welche IP?) — dann mache ich den Middleware-Teil, du den systemd-Teil.
→ ANTWORT (Proxy-Setup?): A+B kombiniert, die Infos musst du dir vom Server holen

### B4 — M16: GitHub-Webhook Deploy-RCE-Härtung
**Kontext:** `webhook_route.py:149` triggert nach HMAC-Verify `git pull`+`pip install`+`sudo systemctl restart` = RCE-by-design, Sicherheit 100% am `GITHUB_WEBHOOK_SECRET`.
**Offen:** sudoers-Minimierung + IP-Allowlist?
**Optionen:** (A) sudoers NOPASSWD nur für die 4 konkreten `systemctl restart <unit>`-Targets (statt breit) · (B) GitHub-Webhook-Source-IP-Allowlist (GitHub publiziert Hook-IP-Ranges) · (C) Secret-Rotation-Routine.
**Empfehlung:** A+B+C. A+B sind Server-Config (deine Sache), C kann ich als Doku/Skript vorbereiten.
→ ANTWORT: A+B+C

---

## C — Design / Schema (code-bar von mir, sobald du das Schema vorgibst)

### C1 — M06: Session-Timeout reaktivieren
**Kontext:** `session_timeout.py` ist toter Code (prüft `session["user"]`, Auth läuft über JWT-Cookie `dashboard_token`). Idle-/Absolut-Timeout greift nie, 24h-JWT ohne Revoke.
**Optionen:** (A) JWT-gekoppelt: `iat`+`last-activity`-Claim, Middleware decodiert `dashboard_token` (leichter, kein State) · (B) serverseitige Session-Tabelle mit Revoke (mehr Aufwand, echtes Logout-überall) · (C) so lassen (JWT-Expiry reicht).
**Empfehlung:** A — JWT-Claim-basiert, deckt Idle-Timeout + ist mit dem bestehenden JWT-Flow konsistent. Revoke (B) nur falls „remote logout" gebraucht.
→ ANTWORT (A/B/C + Idle-Timeout-Minuten?): A 10min

### C2 — M17: CSRF-Token an Identität binden
**Kontext:** `csrf.py:124` — CSRF-Token-Session und Auth-JWT entkoppelt; Check übersprungen ohne `dashboard_token`. Keine direkte Exploit-Kette, aber fragil.
**Optionen:** (A) Token = HMAC über JWT-`sub` (Double-Submit gegen Auth-Cookie) · (B) so lassen (kein akuter Exploit).
**Empfehlung:** A wenn wir eh an der Auth-Schicht sind; sonst Backlog. Code-bar von mir.
→ ANTWORT: A

### C3 — M29: Audit-Log-Transaktion / get_db-Repository
**Kontext:** `dashboard_audit.log_action` committet auf shared `get_db()`-Connection → Phantom-Commit eines fremden uncommitted Writes möglich (Multi-Prozess). Graph-Trace zeigte: `get_db()` = God-Node mit 240 Consumern (60 Cogs + 38 Routes direkt) → systemisch.
**Optionen:** (A) Punktfix: audit-Insert auf dedizierter Kurzlebig-Connection · (B) Repository-Layer mit erzwungenem Read-Pool + Transaktions-Scope pro Aufruf (löst M29+M32+M28-Klasse strukturell, größerer Refactor, Tech-Debt-Sprint) · (C) Doku-Kommentar + Backlog.
**Empfehlung:** A jetzt (klein, behebt das konkrete Phantom-Commit-Risiko), B als eigener Tech-Debt-Sprint später (nicht Big-Bang nebenbei).
→ ANTWORT: B, wir machen eine nacht session. hab jetzt auch den größten plan

---

## D — Cleanup

### D1 — LOW-Sweep (M34–M50, ~14 Items)
**Kontext:** billige Polish-Fixes: WEB_SECRET_KEY-Fallback-Härtung (M34), api_client session-lock (M35), login-attempts zeitbasierter Cleanup (M36), psutil disk-partition (M37), set_role_grants-Transaktion (M39), DBHelper cursor-init (M40), get_env type-hint (M41), redaction-substring (M42), read-pool log-level (M43), satisfactory-save Lock-Pin (M44), CSV-`-`-prefix-Nuance, JSON-parse-in-Loop→to_thread (M48), clear_events sync-open→to_thread (M49), mob-sort-in-loop (M50).
**Optionen:** (A) Komplett-Sweep in einem Batch · (B) nur die Security/Korrektheit-relevanten (M34/M35/M36/M39) · (C) skip/Backlog.
**Empfehlung:** B jetzt (4 mit Substanz), Rest (reine Micro-Opt) Backlog — oder A wenn du „sauber durch" willst.
→ ANTWORT: A

### D2 — Dead-Code aus den Fixes
**Kontext:** Nach M31/M32 sind `_UNSET` (leveling.py:59) + `get_db`-Import (analytics_route.py) ungenutzt. Harmlos, nicht lint-blockierend.
**Optionen:** (A) mitnehmen (2-Zeilen-Cleanup) · (B) lassen.
**Empfehlung:** A — trivial, hält die Files sauber.
→ ANTWORT: A

---

## E — Cross-Session offen (aus früherer Session-Notiz; bitte abhaken falls schon erledigt)

> Nicht Teil des Discord-Audits, aber laut Session-Log noch offen. Falls erledigt → streichen.

| ID | Was | Status |
|---|---|---|
| E1 | n8n auto-updater **scharfschalten** (`pipeline_autoupdate.py`, Server) | „in Arbeit" laut Log → fertig? | erledigt, alles zu n8n sollte gefixxt sein, überprüf es aber nochmal
| E2 | n8n `:latest`→Minor-Pin (`p-315f7abe1f`, docker-compose.yml:17 + Dockerfile:3) | offen |
| E3 | n8n Webhook-Auth (`p-6a872e3a13`, HIGH) + n8n 2.24-Update (`p-c01cd23cd3`) | offen (deine Infra) |
| E4 | Secrets → **KeePass**: `GEMINI_API_KEY` / `MCP_API_KEY` / `DEEPSEEK_API_KEY` (nur ENV, kein Vault-Backup) | offen | muss noch erledigt werden, hat aber niedrige prio da nirgends außer in der variable abgespeicehrt
| E5 | FootstepPro `cd_atk` (0.0006) REAPER A/B-Test (v0.5 JSFX) | offen (dein Test) | kommt morgen

→ ANTWORT (welche noch offen?):

---

## Zusammenfassung — was Claude SOFORT tun kann sobald du antwortest

- **Code-bar von mir** (sobald Schema/Flag entschieden): B2 (Flag), B3-Middleware-Teil, C1, C2, C3-Variante-A, D1, D2 + A1-Commit.
- **Nur du** (Server/Policy): A2-Deploy-Test, B1-Server-Bump-Test, B3-systemd, B4-sudoers, E1–E5.
