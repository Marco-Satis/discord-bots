# Server-Aufgaben (erfordert SSH-Zugriff)

> Stand: 22. Februar 2026
> Quelle: docs/REVIEW_v4.0.0.md

## Prioritaet 1 (zeitnah)

### Alte Pycache aufraemen
128 veraltete Python 3.10 Cache-Dateien loeschen:
```bash
ssh -p 4422 marco@203.0.113.10
cd /home/botuser/Discord_Bots
find . -name "*.cpython-310.pyc" -delete
find . -name "__pycache__" -empty -delete
```

### Temp-Dateien entfernen
Review-Backup-Dateien nach Verifikation loeschen:
```bash
rm /home/botuser/Discord_Bots/config/.env.old
rm /home/botuser/Discord_Bots/config/config.json.pre_review_backup
rm /home/botuser/Discord_Bots/web/app.py.pre_review_backup
```

### MC Vanilla Server
Server ist offline laut Health-Route. Entweder:
- Server starten: `sudo systemctl start minecraft-vanilla`
- Oder aus Monitoring ausschliessen: Feature-Flag in config.json

## Prioritaet 2 (Nice-to-have)

### WEB_ADMIN_PASS_HASH setzen
Dashboard-Admin-Login funktioniert nur via Discord OAuth. Falls Passwort-Login gewuenscht:
```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'DEIN_PASSWORT', bcrypt.gensalt()).decode())"
# Ergebnis in config/.env bei WEB_ADMIN_PASS_HASH= eintragen
```

### Unbekannte Ports pruefen
Ports 8081, 8888, 9090 sind offen — pruefen was dort laeuft:
```bash
ss -tlnp | grep -E '(8081|8888|9090)'
```

### RCON BMC sporadische Verbindungsfehler
RCON-Verbindung zu MC BMC (Port 25575) schlaegt gelegentlich fehl.
Moegliche Loesung: Timeout in `modules/minecraft/rcon.py` erhoehen.
