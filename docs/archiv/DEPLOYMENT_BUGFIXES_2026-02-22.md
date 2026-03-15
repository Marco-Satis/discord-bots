# Deployment: Dashboard-Bugfixes (22.02.2026)

> 4 Bugs gefixt, 4 Dateien geaendert

## Geaenderte Dateien

| Datei | Bug | Fix |
|-------|-----|-----|
| `modules/satisfactory/api_client.py` | "Session is closed" bei jedem Retry | `session` wird jetzt innerhalb der Retry-Schleife geholt |
| `modules/monitoring/health_checker.py` | MC-Ports vertauscht (BMC↔Vanilla) | BMC=25566, VANILLA=25565 |
| `modules/network/port_monitor.py` | False Positives (UDP-Port per TCP, Port 15000 existiert nicht) | Port 15000+15777 entfernt, Port 8080 (Dashboard) hinzugefuegt |
| `modules/minecraft/server.py` | RCON BMC sporadische Verbindungsfehler | Timeout 5s→10s, Retry-Logik (2 Versuche) |

---

## Schritt 1: Dateien hochladen (PowerShell)

```powershell
# Lokaler Pfad anpassen falls noetig
$LOCAL = "C:\Pfad\zu\DIscord_Bots"
$SERVER = "marco@203.0.113.10"
$PORT = 4422
$REMOTE_TMP = "/tmp"

scp -P $PORT "$LOCAL\modules\satisfactory\api_client.py" "${SERVER}:${REMOTE_TMP}/api_client.py"
scp -P $PORT "$LOCAL\modules\monitoring\health_checker.py" "${SERVER}:${REMOTE_TMP}/health_checker.py"
scp -P $PORT "$LOCAL\modules\network\port_monitor.py" "${SERVER}:${REMOTE_TMP}/port_monitor.py"
scp -P $PORT "$LOCAL\modules\minecraft\server.py" "${SERVER}:${REMOTE_TMP}/server.py"
```

---

## Schritt 2: Dateien deployen (PuTTY / SSH)

```bash
# Als marco einloggen, dann:
DEST=/home/botuser/Discord_Bots

# Backup der alten Dateien
sudo cp $DEST/modules/satisfactory/api_client.py $DEST/modules/satisfactory/api_client.py.bak
sudo cp $DEST/modules/monitoring/health_checker.py $DEST/modules/monitoring/health_checker.py.bak
sudo cp $DEST/modules/network/port_monitor.py $DEST/modules/network/port_monitor.py.bak
sudo cp $DEST/modules/minecraft/server.py $DEST/modules/minecraft/server.py.bak

# Neue Dateien kopieren
sudo cp /tmp/api_client.py $DEST/modules/satisfactory/api_client.py
sudo cp /tmp/health_checker.py $DEST/modules/monitoring/health_checker.py
sudo cp /tmp/port_monitor.py $DEST/modules/network/port_monitor.py
sudo cp /tmp/server.py $DEST/modules/minecraft/server.py

# Ownership setzen
sudo chown botuser:botuser $DEST/modules/satisfactory/api_client.py
sudo chown botuser:botuser $DEST/modules/monitoring/health_checker.py
sudo chown botuser:botuser $DEST/modules/network/port_monitor.py
sudo chown botuser:botuser $DEST/modules/minecraft/server.py
```

---

## Schritt 3: Services neustarten (PuTTY / SSH)

```bash
# Monitor-Bot neustart (laedt health_checker, port_monitor, api_client)
sudo systemctl restart monitor-bot

# GameServer-Bot neustart (laedt server.py fuer RCON-Fix)
sudo systemctl restart gameserver-bot

# 10 Sekunden warten, dann Status pruefen
sleep 10
sudo systemctl status monitor-bot --no-pager -l
sudo systemctl status gameserver-bot --no-pager -l
```

---

## Schritt 4: Verifizieren (PuTTY / SSH)

```bash
# Logs pruefen — keine "Session is closed" Fehler mehr?
sudo journalctl -u monitor-bot --since '2 min ago' --no-pager | grep -i "session is closed"

# Health-Checker laeuft mit korrekten Ports?
sudo journalctl -u monitor-bot --since '2 min ago' --no-pager | grep -i "health"

# Port Monitor zeigt nur noch TCP-Ports?
sudo journalctl -u monitor-bot --since '2 min ago' --no-pager | grep -i "port"

# RCON-Fehler reduziert?
sudo journalctl -u monitor-bot --since '5 min ago' --no-pager | grep -i "rcon"
sudo journalctl -u gameserver-bot --since '5 min ago' --no-pager | grep -i "rcon"

# Dashboard erreichbar?
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/health
```

---

## Rollback (falls noetig)

```bash
DEST=/home/botuser/Discord_Bots
sudo cp $DEST/modules/satisfactory/api_client.py.bak $DEST/modules/satisfactory/api_client.py
sudo cp $DEST/modules/monitoring/health_checker.py.bak $DEST/modules/monitoring/health_checker.py
sudo cp $DEST/modules/network/port_monitor.py.bak $DEST/modules/network/port_monitor.py
sudo cp $DEST/modules/minecraft/server.py.bak $DEST/modules/minecraft/server.py
sudo chown botuser:botuser $DEST/modules/satisfactory/api_client.py $DEST/modules/monitoring/health_checker.py $DEST/modules/network/port_monitor.py $DEST/modules/minecraft/server.py
sudo systemctl restart monitor-bot gameserver-bot
```

---

## Temp-Dateien aufraeumen (nach erfolgreicher Verifizierung)

```bash
# Auf dem Server
sudo rm /tmp/api_client.py /tmp/health_checker.py /tmp/port_monitor.py /tmp/server.py

# Backups entfernen (erst wenn alles laeuft!)
# sudo rm $DEST/modules/satisfactory/api_client.py.bak
# sudo rm $DEST/modules/monitoring/health_checker.py.bak
# sudo rm $DEST/modules/network/port_monitor.py.bak
# sudo rm $DEST/modules/minecraft/server.py.bak
```
