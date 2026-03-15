# Post-Upgrade Review — Discord Bot System v4.0.0

## Rolle & Auftrag

Du bist ein erfahrener Python-Auditor mit Expertise in Discord.py, FastAPI/Starlette, SQLite und Linux-Systemadministration. Dein Auftrag: Fuehre einen vollstaendigen, intensiven Review des Discord Bot Systems durch, das gerade von v3.2.0 auf v4.0.0 upgegraded wurde. Das Upgrade umfasste 39 neue Features (F27-F65) in 5 Phasen.

**Sprache:** Deutsch (Report, Kommentare, Bewertungen).
**Arbeitsmodus:** Systematisch, Datei fuer Datei. Keine Annahmen — alles pruefen. Ergebnisse dokumentieren.

---

## Server-Zugang

- **SSH:** `ssh -p 4422 marco@203.0.113.10` (oder Alias `netcup-marco`)
- **Server-Pfad:** `/home/botuser/Discord_Bots/`
- **Services:** `monitor-bot`, `gameserver-bot`, `admin-bot`, `web-dashboard`
- **Logs:** `sudo journalctl -u <service> -n 200 --no-pager`
- **Python:** 3.11 auf Server

---

## Review-Ablauf (in dieser Reihenfolge)

### SCHRITT 1: Server-Ist-Zustand erfassen

Fuehre auf dem Server aus und dokumentiere:

```bash
# Service-Status aller 4 Dienste
sudo systemctl status monitor-bot gameserver-bot admin-bot web-dashboard --no-pager

# Aktuelle Fehler in ALLEN Logs (letzte 500 Zeilen pro Service)
for svc in monitor-bot gameserver-bot admin-bot web-dashboard; do
  echo "=== $svc ==="
  sudo journalctl -u $svc -n 500 --no-pager 2>&1 | grep -iE 'error|exception|traceback|critical|warning|failed' | tail -30
done

# Python-Version und installierte Packages
python3 --version
pip3 list 2>/dev/null | head -30

# Disk, RAM, CPU
df -h /home
free -h
uptime

# Offene Ports pruefen
ss -tlnp | grep -E '(8443|15777|15000|25565|25566)'

# Dashboard erreichbar?
curl -s -o /dev/null -w "%{http_code}" https://localhost:8443/login --insecure

# SQLite-Datenbanken vorhanden?
find /home/botuser/Discord_Bots/data -name "*.db" -o -name "*.sqlite" 2>/dev/null
ls -la /home/botuser/Discord_Bots/data/

# JSON-Dateien noch vorhanden? (Migration-Status)
find /home/botuser/Discord_Bots/data -name "*.json" -not -name ".gitkeep" 2>/dev/null

# config.json Feature-Flags
python3 -c "import json; d=json.load(open('/home/botuser/Discord_Bots/config/config.json')); print(json.dumps(d.get('features',{}), indent=2))"
```

Erstelle daraus: `docs/SERVER_STATUS_POST_UPGRADE.md`

---

### SCHRITT 2: Import- und Syntax-Check ALLER Python-Dateien

Pruefe JEDE .py-Datei auf Importfehler — das ist der haeufigste Fehlertyp nach Upgrades:

```bash
# Alle Python-Dateien finden und einzeln importieren
cd /home/botuser/Discord_Bots
find . -name "*.py" -not -path "./.git/*" -not -path "./__pycache__/*" -not -path "./*pycache*" | sort | while read f; do
  module=$(echo "$f" | sed 's|^./||;s|/|.|g;s|.py$||')
  result=$(python3 -c "import importlib; importlib.import_module('$module')" 2>&1)
  if [ $? -ne 0 ]; then
    echo "FEHLER: $f"
    echo "$result" | tail -3
    echo "---"
  fi
done
```

Fuer Dateien die nicht als Modul importierbar sind, nutze Syntax-Check:
```bash
python3 -m py_compile <datei>
```

Dokumentiere JEDE Datei mit Fehler und den genauen Error.

---

### SCHRITT 3: Abhaengigkeits-Analyse (kritisch!)

Pruefe ob ALLE neuen Module korrekt eingebunden sind:

**3a) Cog-Registrierung — Sind alle Cogs in ihrem Bot geladen?**

Lies die drei Bot-Dateien und pruefe ob JEDER Cog in `cogs/` auch in einem Bot registriert ist:

```
bots/monitor_bot.py   → Welche Cogs werden geladen?
bots/admin_bot.py     → Welche Cogs werden geladen?
bots/gameserver_bot.py → Welche Cogs werden geladen?
```

Erstelle eine Matrix:

| Cog-Datei | Registriert in | setup() vorhanden? | Status |
|-----------|---------------|---------------------|--------|
| cogs/audit_cog.py | admin_bot.py | Ja/Nein | OK/FEHLT |
| cogs/monitor_cog.py | monitor_bot.py | Ja/Nein | OK/FEHLT |
| ... | ... | ... | ... |

WARNUNG: Nicht-registrierte Cogs sind STUMM — kein Error, kein Warning, sie existieren einfach nicht!

**3b) Route-Registrierung — Sind alle Routes im Dashboard geladen?**

Lies `web/app.py` und pruefe ob JEDE Route in `web/routes/` auch per `app.include_router()` eingebunden ist:

| Route-Datei | Registriert in app.py? | Status |
|-------------|----------------------|--------|
| web/routes/health_route.py | Ja/Nein | OK/FEHLT |
| web/routes/security_route.py | Ja/Nein | OK/FEHLT |
| web/routes/sse_route.py | Ja/Nein | OK/FEHLT |
| web/routes/theme_route.py | Ja/Nein | OK/FEHLT |
| web/routes/forecast_route.py | Ja/Nein | OK/FEHLT |
| ... | ... | ... |

**3c) Middleware-Registrierung**

Pruefe ob alle Middleware-Dateien in `web/middleware/` aktiv in `web/app.py` eingebunden sind:
- csrf.py → Aktiv?
- rate_limiter.py → Aktiv?
- session_timeout.py → Aktiv?

**3d) Module-Imports in Bot-Dateien**

Pruefe ob alle neuen Module aus `modules/` korrekt in die Bots importiert und initialisiert werden:

| Modul | Importiert in | Initialisiert? | Background-Task? |
|-------|--------------|----------------|-------------------|
| modules/monitoring/health_checker.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| modules/monitoring/service_watchdog.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| modules/system/disk_guard.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| modules/network/duckdns_monitor.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| modules/network/port_monitor.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| modules/security/fail2ban.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| modules/security/ssl_monitor.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| modules/backup/integrity.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| modules/database/db_manager.py | alle 3 Bots? | Ja/Nein | - |
| modules/monitoring/forecasting.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| modules/monitoring/stats_collector.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| modules/analytics/correlation.py | monitor_bot.py | Ja/Nein | Ja/Nein |
| ... | ... | ... | ... |

---

### SCHRITT 4: Feature-Vollstaendigkeits-Check

Pruefe fuer JEDES der 39 Features ob es tatsaechlich implementiert wurde. Die Features sind:

**Phase 1 — Sicherheit + Stabilitaet:**
F62 (Selftest), F61 (Shutdown), F64 (CSRF), F65 (Session Timeout), F27 (Health Auto-Restart), F49 (Disk Guard), F50 (Service Watchdog), F51 (DuckDNS Monitor), F52 (Port Monitor), F31 (Fail2Ban), F32 (SSL Monitor), F33 (Backup Integrity), F34 (Health Route), F48 (Rate Limiter)

**Phase 2 — Datenbankschicht:**
F28 (SQLite Migration — 8 Teilschritte), F63 (Retention/Cleanup), F56 (Backup-Rotation), F53 (Config-Versionierung)

**Phase 3 — Dashboard-Erweiterungen:**
F29 (SSE Live-Updates), F35 (Korrelations-Dashboard), F36 (Export-Funktionen), F37 (Ressourcen-Forecasting), F44 (Error-Dashboard), F55 (Dashboard-Suche), F57 (Stats-Collector), F58 (Analytics-Dashboard), F45 (Changelog-Seite)

**Phase 4 — Bot-Erweiterungen:**
F30 (Crash-Replay), F39 (Moderation), F40 (Leveling), F41 (Giveaways), F43 (Custom Commands), F54 (Alert-Deduplizierung), F59 (Graceful Degradation)

**Phase 5 — Polishing:**
F38 (Maintenance-Mode), F42 (Audit-Logging), F46 (Dark Mode), F60 (Webhook-Integration), F47 (Performance-Optimierung)

Fuer jedes Feature pruefe:
1. Existiert die Hauptdatei?
2. Ist sie importierbar (kein SyntaxError)?
3. Ist sie im entsprechenden Bot/Dashboard registriert?
4. Hat sie die laut FEATURE_PLAN.md erwartete Funktionalitaet? (Stichproben-Check)
5. Gibt es zugehoerige config.json Eintraege?
6. Gibt es zugehoerige Templates (falls Dashboard-Feature)?

Erstelle eine Feature-Status-Tabelle:

| Feature | Datei existiert | Importierbar | Registriert | Config vorhanden | Template vorhanden | Bewertung |
|---------|----------------|-------------|-------------|-----------------|-------------------|-----------|
| F27 | Ja/Nein | Ja/Nein | Ja/Nein | Ja/Nein | n/a | OK/PROBLEM |
| ... | ... | ... | ... | ... | ... | ... |

---

### SCHRITT 5: config.json Vollstaendigkeits-Check

Pruefe ob config.json:
1. Valides JSON ist (kein Syntaxfehler)
2. ALLE erwarteten Feature-Flags enthaelt
3. ALLE erwarteten Threshold-Werte hat
4. ALLE erwarteten Scheduler-Keys hat
5. Keine verwaisten Keys enthaelt (Keys ohne zugehoerigen Code)
6. Keine fehlenden Keys hat (Code referenziert Keys die nicht existieren)

Suche im gesamten Code nach `config.get(`, `config[`, `config.json` Referenzen und gleiche mit der tatsaechlichen config.json ab.

---

### SCHRITT 6: .env Vollstaendigkeits-Check

Vergleiche `config/.env` mit `config/.env.example`:
1. Fehlen Keys in .env die in .env.example stehen?
2. Gibt es leere Werte wo welche sein sollten?
3. Stimmen die Token/Secrets (nicht leer, richtiges Format)?
4. Suche im Code nach `os.getenv(` / `os.environ` und pruefe ob ALLE referenzierten ENV-Variablen auch in .env existieren

---

### SCHRITT 7: Template-Integritaet (Dashboard)

Pruefe alle HTML-Templates in `web/templates/`:
1. Erbt jedes Template von `base.html`? (`{% extends "base.html" %}`)
2. Sind alle `{% block %}` Tags korrekt geschlossen?
3. Sind alle HTMX-Attribute (`hx-get`, `hx-post`, etc.) auf existierende Routes verlinkt?
4. Sind alle `{{ url_for() }}` Aufrufe auf existierende Endpoints?
5. Gibt es kaputte Jinja2-Syntax? (`python3 -c "from jinja2 import Environment; ..."`)
6. Haben CSRF-geschuetzte Formulare auch das CSRF-Token? (`<input type="hidden" name="csrf_token"...>`)

---

### SCHRITT 8: SQLite-Migration-Status

Falls F28 implementiert wurde:
1. Existiert `modules/database/db_manager.py`?
2. Existiert `modules/database/models.py` mit allen erwarteten Tabellen?
3. Existiert `modules/database/migrations.py`?
4. Existiert `modules/database/json_importer.py`?
5. Werden die Datenbanken korrekt erstellt? (Tabellen-Schema pruefen)
6. Ist der Dual-Read-Modus implementiert (JSON-Fallback)?
7. Was ist der Wert von `features.sqlite_migration_complete` in config.json?
8. Existieren .db/.sqlite Dateien auf dem Server in `/home/botuser/Discord_Bots/data/`?

---

### SCHRITT 9: Sicherheits-Review

Pruefe spezifisch:
1. **CSRF:** Ist `web/middleware/csrf.py` aktiv? Hat `base.html` das CSRF Meta-Tag? Nutzen ALLE POST-Formulare CSRF-Tokens?
2. **Session Timeout:** Ist `web/middleware/session_timeout.py` aktiv? Steht der Timeout-Wert in config?
3. **Rate Limiter:** Ist `web/middleware/rate_limiter.py` aktiv? Welche Endpoints sind geschuetzt?
4. **Secrets:** Suche nach hartcodierten Passwoertern, Tokens, API-Keys im Code (`grep -rn "password\|secret\|token\|api_key" --include="*.py" | grep -v ".env" | grep -v "__pycache__"`)
5. **Debug-Mode:** Ist der Dashboard-Server im Production-Modus? (kein `debug=True`)

---

### SCHRITT 10: Systemd-Services vs. Code-Konsistenz

Pruefe ob die systemd Service-Dateien in `systemd/` mit dem tatsaechlichen Code uebereinstimmen:
1. Stimmen die ExecStart-Pfade?
2. Stimmen die WorkingDirectory-Pfade?
3. Stimmt der User (botuser)?
4. Sind alle Environment-Variablen gesetzt?
5. Stimmen die Service-Dateien auf dem Server mit denen im Repo ueberein?

```bash
for svc in admin-bot gameserver-bot monitor-bot web-dashboard; do
  echo "=== $svc ==="
  diff <(cat /home/botuser/Discord_Bots/systemd/$svc.service) <(cat /etc/systemd/system/$svc.service) 2>/dev/null || echo "UNTERSCHIED oder nicht gefunden"
done
```

---

### SCHRITT 11: Toter Code & Verwaiste Dateien

Identifiziere:
1. **Python-Dateien die nirgends importiert werden** (tote Module)
2. **Templates die von keiner Route gerendert werden** (tote Templates)
3. **Routes die von keinem Template verlinkt sind** (tote Endpoints)
4. **__pycache__ mit .cpython-310.pyc** (alte Python-Version Artefakte — sollten bereinigt werden)
5. **Duplizierte Funktionalitaet** (z.B. `modules/anti_spam.py` vs `modules/moderation/anti_spam.py`)
6. **Dateien mit .new, .bak, .old, .tmp Endungen**

---

### SCHRITT 12: requirements.txt Abgleich

Pruefe:
1. Sind alle im Code importierten Packages auch in `requirements.txt`?
2. Sind die Versionen kompatibel?
3. Gibt es Packages in requirements.txt die nicht mehr benoetigt werden?

```bash
# Auf dem Server: Tatsaechlich installierte vs. requirements
cd /home/botuser/Discord_Bots
pip3 freeze > /tmp/installed.txt
diff <(sort requirements.txt) <(sort /tmp/installed.txt)
```

---

## Output-Format

Erstelle am Ende einen vollstaendigen Report als `docs/REVIEW_v4.0.0.md` mit folgender Struktur:

```markdown
# Review-Report — Discord Bot System v4.0.0
> Datum: [HEUTE]
> Reviewer: Claude Code

## Zusammenfassung
- Gesamtbewertung: [KRITISCH / WARNUNG / GUT / SEHR GUT]
- Kritische Fehler: X
- Warnungen: Y
- Hinweise: Z
- Features implementiert: XX/39

## Kritische Fehler (sofort beheben)
[Fehler die den Betrieb verhindern oder Sicherheitsluecken darstellen]

## Warnungen (zeitnah beheben)
[Probleme die funktionieren aber nicht optimal sind]

## Hinweise (Nice-to-fix)
[Verbesserungsvorschlaege, Code-Qualitaet, Aufraeum-Arbeiten]

## Feature-Status-Matrix
[Tabelle aus Schritt 4]

## Abhaengigkeits-Matrix
[Tabellen aus Schritt 3]

## Server-Status
[Ergebnisse aus Schritt 1]

## Detaillierte Befunde
[Alle Einzelergebnisse aus Schritt 2-12]
```

---

## Wichtige Regeln

1. **Pruefe ALLES auf dem Server** — nicht nur lokal. Der Server ist die Wahrheit.
2. **Keine Annahmen** — wenn du nicht sicher bist ob etwas funktioniert, pruefe es.
3. **Dokumentiere auch was funktioniert** — nicht nur Fehler.
4. **Fasse dich kurz bei OK-Befunden**, ausfuehrlich bei Problemen.
5. **Priorisiere Befunde**: Kritisch > Warnung > Hinweis.
6. **Aendere NICHTS** — das ist ein reiner Review, keine Fixes. Nur lesen und dokumentieren.
7. **Bei Context-Limit**: Schreibe Zwischen-Ergebnisse sofort in docs/REVIEW_v4.0.0.md und arbeite inkrementell weiter.
