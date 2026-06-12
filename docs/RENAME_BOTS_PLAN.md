# Bot-Rename Migrations-Plan — Recon / Operator / Marshal

> Erstellt 2026-06-13. Status: PLAN (noch nichts ausgeführt). Voller Rename inkl. Infra.
> Slug-Schema: Suffix behalten (stdlib-Clash-Schutz für `operator`/`marshal`).
> Historie wird NICHT umgeschrieben (archiv/sessions/incidents/CHANGELOG/context bleiben).

## Mapping (interner Vermerk — Quelle der Wahrheit)

| Alt-Service | Alt-Datei | **Neu-Service** | **Neu-Datei** | Rolle |
|-------------|-----------|-----------------|---------------|-------|
| `monitor-bot` | `bots/monitor_bot.py` | **`recon-bot`** | **`bots/recon_bot.py`** | Health/Monitoring, Scheduler, Notifications, MC-Server-Watch |
| `gameserver-bot` | `bots/gameserver_bot.py` | **`operator-bot`** | **`bots/operator_bot.py`** | Satisfactory-Control (RCON, Player-Tracking) |
| `admin-bot` | `bots/admin_bot.py` | **`marshal-bot`** | **`bots/marshal_bot.py`** | Moderation/Admin (Warns, Timeouts, Tickets, Giveaways) |
| `web-dashboard` | — | *(bleibt)* | — | Dashboard (intern, niemand sieht Namen) |
| `pipeline-bot` | `bots/pipeline_bot.py` | *(bleibt — nicht im Scope)* | — | — |

Funknetz-Logik: **Recon meldet → Operator handelt → Marshal greift durch.**

Jede umbenannte Bot-Datei bekommt Header-Docstring:
`"""recon_bot.py — vormals monitor_bot (Service monitor-bot). Rolle: Health/Monitoring/Scheduler."""`

## Buckets (was wird angefasst)

- **Umbenennen:** `bots/*.py`, `web/routes/*`, `web/templates/*` (aktuell), `modules/*`, `systemd/*.service`, `scripts/{deploy*,manage_bots,bot_watchdog}.sh`, sudoers-Whitelist, `tests/*`, aktive Docs (CLAUDE.md, README, runbooks, production-guides)
- **Lassen (Historie):** `docs/archiv/*`, `docs/sessions/CHAT_*`, `docs/incidents/*`, `CHANGELOG.md`, `context/*.md`, `PROGRESS.md.bak*`
- **Skip:** `*.bak.*`, `.deploystg/*`

## Phasen — sichere Reihenfolge (Pipeline bricht nie mittendrin)

### Phase 1 — Display/Auftritt (LOKAL, reversibel, normaler Deploy)
Was User sehen. Service-Slugs + Filenames bleiben → nichts bricht.
- [ ] Discord-Presence/Activity-Strings im Code (`bots/*.py` presence-Setup)
- [ ] Dashboard-Labels/Embeds/Templates die "Monitor Bot" etc. anzeigen
- [ ] Verifikation: 4 Tests + Dashboard-GET

### Phase 2 — Interne Display-Konstanten (LOKAL)
- [ ] Logger-Display-Namen, Konstanten, interne Kommentare (NICHT Service-Slugs die mit Server-systemctl matchen müssen)
- [ ] Verifikation: 4 Tests

### Phase 3 — INFRA-Rename (SERVER, PW-GATED, Wartungsfenster, Lockstep!)
Alles zusammen in einem Fenster, sonst Deploy-/Start-Bruch:
- [ ] `bots/*.py` Dateien umbenennen (git mv) + Header-Docstring
- [ ] Imports/Referenzen auf die Module nachziehen (`grep from bots.`)
- [ ] systemd-Units: neue `.service` (recon-bot/operator-bot/marshal-bot), `ExecStart`-Pfad + `SyslogIdentifier` + `After=`-Deps anpassen
- [ ] Server: `daemon-reload`, alte Unit `disable --now`, neue `enable --now`
- [ ] Deploy-Wrapper: `scripts/discordbots-deploy.sh` SERVICES-Whitelist + `scripts/discordbots-deploy.sudoers`
- [ ] Code das `systemctl <service>` aufruft (`web/routes/system_route.py`, ...) → neue Service-Namen
- [ ] Scripts: `manage_bots.sh`, `bot_watchdog.sh`, `deploy*.sh`
- [ ] `status_writer.py` / `bot_status.json`-Keys falls service-keyed (health, cron-monitor-check)
- [ ] `tests/test_imports.py` (Modul-Pfade), `test_routes/test_cogs`
- [ ] Verifikation: py_compile, 4 Tests, `systemctl status`, `journalctl` clean, Discord-Ready-Event, Dashboard-GET

### Phase 4 — Aktive Docs (LOKAL)
- [ ] CLAUDE.md (Service-Zuordnung-Tabelle + Mapping-Verweis), README, runbooks, production-guides
- [ ] NICHT: archiv/sessions/incidents/CHANGELOG/context

### Phase 5 — Cleanup
- [ ] Alte `.bak`-Files nach 24h Stabilität löschen
- [ ] Discord Developer Portal: Bot-Account-Usernames manuell (Marcos Schritt — Code kann das nicht)

## Marco-Schritte (kann Claude nicht)
1. Phase-3-Wartungsfenster freigeben (PW für systemd/sudoers) — kurze Downtime der 3 Bots
2. Discord Developer Portal: Account-Usernames der 3 Bots umbenennen

## Risiko-Hinweise
- Phase 1+2 sind risikoarm + einzeln deploybar. Phase 3 ist der kritische Lockstep.
- Bei Phase-3-Fehler: Auto-Rollback des Deploy-Wrappers greift NUR solange Service-Slugs in Whitelist stehen → Whitelist-Update zuletzt, nach erfolgreichem Unit-Switch.

---

## STATUS 2026-06-13 — Lokaler Rename FERTIG (im Repo, NICHT committet)

Erledigt (Repo + verifiziert):
- git mv: bots/{monitor_bot,gameserver_bot,admin_bot}.py → {recon_bot,operator_bot,marshal_bot}.py; web/routes/admin_bot_route.py → marshal_bot_route.py; web/templates/admin_bot.html → marshal_bot.html; systemd/{monitor,gameserver,admin}-bot.service → {recon,operator,marshal}-bot.service
- Header-Vermerk (alter Name + Rolle) in den 3 Bot-Files
- Alle Token/Display-Phrasen in aktivem Code/Infra/Templates/Tests/Scripts/.claude-Tooling/aktiven Docs ersetzt
- systemd-Units: ExecStart, SyslogIdentifier, After=-Deps korrekt
- sudoers (botuser-sudoers + discordbots-deploy.sudoers) + deploy-discordbots.sh ALLOWED_SERVICES auf neue Namen
- CLAUDE.md Service-Zuordnung + Mapping-Vermerk
- Historie-Docs/CHANGELOG/context/*.bak NICHT angefasst (gewollt)

Tests: test_imports ✓, test_routes ✓, test_cogs ✓ (toter `pipeline_control_cog`-Kommentar in marshal_bot entfernt — war pre-existing WIP, kein Rename-Bug). test_env: 3 pre-existing MC_*-Abweichungen, rename-unabhängig.

ACHTUNG Working-Tree: enthält FREMDE pipeline-bot-WIP (cogs/pipeline_approval_cog.py, config/.env.example, bots/pipeline_bot.py u.a.) — NICHT vom Rename, NICHT mitcommitten ohne Marco-Klärung.

## SERVER-CUTOVER (Marco, PW, Wartungsfenster — kurze Bot-Downtime)

> Voraussetzung: Rename-Commit auf dem Server verfügbar (git pull ODER rsync des Repos nach /home/botuser/Discord_Bots).

```bash
ssh -p 4422 marco@203.0.113.10

# Backup der alten Units (für Rollback)
sudo cp /etc/systemd/system/monitor-bot.service /etc/systemd/system/gameserver-bot.service /etc/systemd/system/admin-bot.service /root/

# 1. Alte Bots stoppen + deaktivieren
sudo systemctl disable --now monitor-bot gameserver-bot admin-bot

# 2. Neue Code-Files auf Server (Git-Checkout):
sudo -u botuser git -C /home/botuser/Discord_Bots pull
#    (falls kein Git-Checkout: rsync/scp des Repos)

# 3. Neue systemd-Units installieren
sudo cp /home/botuser/Discord_Bots/systemd/recon-bot.service    /etc/systemd/system/
sudo cp /home/botuser/Discord_Bots/systemd/operator-bot.service /etc/systemd/system/
sudo cp /home/botuser/Discord_Bots/systemd/marshal-bot.service  /etc/systemd/system/
sudo cp /home/botuser/Discord_Bots/systemd/bot-watchdog.service /etc/systemd/system/

# 4. Alte Units entfernen + reload
sudo rm /etc/systemd/system/monitor-bot.service /etc/systemd/system/gameserver-bot.service /etc/systemd/system/admin-bot.service
sudo systemctl daemon-reload

# 5. sudoers aktualisieren (Dateinamen unter /etc/sudoers.d/ an Bestand anpassen!)
sudo cp /home/botuser/Discord_Bots/systemd/botuser-sudoers            /etc/sudoers.d/botuser
sudo cp /home/botuser/Discord_Bots/scripts/discordbots-deploy.sudoers /etc/sudoers.d/discordbots-deploy
sudo visudo -c   # MUSS "parsed OK" melden — sonst NICHT weiter

# 6. Deploy-Wrapper aktualisieren (root-owned, neue ALLOWED_SERVICES)
sudo cp /home/botuser/Discord_Bots/scripts/deploy-discordbots.sh /usr/local/sbin/deploy-discordbots
sudo chmod 755 /usr/local/sbin/deploy-discordbots

# 7. Neue Bots starten + enablen
sudo systemctl enable --now recon-bot operator-bot marshal-bot

# 8. Verify
systemctl status recon-bot operator-bot marshal-bot --no-pager
sudo journalctl -u recon-bot -u operator-bot -u marshal-bot --since '1 min ago' --no-pager | grep -iE "ready|error|critical"

# 9. Discord Developer Portal (Web-UI): Bot-Account-Usernames manuell umbenennen
```

Rollback bei Fehler: `sudo systemctl disable --now recon-bot operator-bot marshal-bot` → alte Units aus /root/ zurückkopieren → `daemon-reload` → `sudo systemctl enable --now monitor-bot gameserver-bot admin-bot`.
