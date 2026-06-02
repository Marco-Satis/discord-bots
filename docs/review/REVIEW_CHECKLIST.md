# Review-Checkliste — Kurzreferenz

> Vollstaendige Befehle: docs/review/REVIEW_PROMPT.md
> Diese Datei ist nur eine Zusammenfassung.

## 7 Phasen

| Phase | Was | Wie |
|-------|-----|-----|
| 1 | Tests + Server-Health | 4 lokale Tests, Services, Logs, DB, Disk |
| 2 | Auto-Update verifizieren | Module, CurseForge API, Staging, Sudoers, A0-Fixes |
| 3 | Bot-Funktionen | RCON (async with!), SAT Query, Scheduler, Chat-Bridge |
| 4 | Web-Dashboard | Alle Routes, Auth (JWT), Middleware, SSE, Templates |
| 5 | Bekannte Bugs fixen | SAT CPU/RAM, Ports, Charts (OFFEN.md Sektion D) |
| 6 | Code-Qualitaet + CSRF-Bug | bare except, hardcoded Paths, CSRF-Middleware fixen |
| 7 | Cleanup + Abschluss | Pycache, VERSION, DASHBOARD_STATUS.md, CHANGELOG |

## Kritische Fakten (aus Code verifiziert)

- Login-URL: `/auth/login` (prefix="/auth"), NICHT `/login`
- Login-Felder: `username` + `password` (Form), geprueft gegen `WEB_ADMIN_PASS_HASH` (bcrypt)
- Auth: JWT-Cookie `dashboard_token`, NICHT Session-basiert
- CSRF EXEMPT: `/auth/login`, `/auth/discord/callback`, `/api/health`, `/ws`, `/api/webhook/*`
- CSRF-BUG: Middleware prueft `session.user` (immer None), muss JWT pruefen
- CurseForge Header: `x-api-key` (kein $2a Prefix)
- RCON: `async with MinecraftRCON(...) as rcon:` — NICHT direkte Instanz + command()
- SSE: `/api/sse/dashboard` (5s), `/api/sse/events` (3s)
- Server-IDs: `satisfactory`, `mc_bmc`, `mc_vanilla`, `teamspeak`
- RCON Ports: BMC5=25575, Vanilla=25576
- Analytics prefix: `/api/analytics/...`
- 20 Router in app.py (auth_router + 19 route files)
- Multi-line SSH: Python-Code als .py Datei per SCP, nicht inline
