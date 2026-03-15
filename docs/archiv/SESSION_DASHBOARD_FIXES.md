# Dashboard-Fixes — Session 21. Februar 2026

> Zusammenfassung fuer neuen Chat. Alle Fixes wurden deployed und Services neugestartet.

---

## 1. Online-Spieler nicht angezeigt (Spielererkennung)

- **Problem:** SAT Detail-Seite zeigte "Keine Spieler" obwohl Spieler online waren (z.B. 1/4)
- **Ursache:** StatusWriter Bug — Spielerdaten wurden nicht korrekt in die `*_status.json` Bridge-Dateien geschrieben bzw. nicht korrekt vom Dashboard-Template gelesen
- **Fix:** `modules/monitoring/status_writer.py` — Spielerdaten werden jetzt korrekt in die JSON-Bridge-Dateien geschrieben
- **Datei:** `modules/monitoring/status_writer.py`

## 2. Kick/Ban uebers Dashboard funktioniert nicht

- **Problem:** Dashboard hatte Kick/Ban-Buttons, aber die loesten keine Aktion aus
- **Ursache:** Action-Handler in `server_detail.py` fehlte bzw. war nicht korrekt implementiert
- **Fix MC:** Kick/Ban ueber RCON implementiert (server_detail Route → RCON-Befehl)
- **Fix SAT:** Eigene Implementierung ueber Satisfactory HTTPS-API (SAT hat kein RCON)
- **Datei:** `web/routes/server_detail.py`

## 3. Versionserkennung bei allen Servern

- **Problem:** Verfuegbare Version wurde nicht angezeigt, nur die installierte
- **Ursache:** Templates + StatusWriter gaben die verfuegbare Version nicht weiter
- **Fix:** StatusWriter und Templates korrigiert — installierte UND verfuegbare Version werden jetzt korrekt angezeigt (SAT, MC BMC, MC Vanilla)
- **Dateien:** `modules/monitoring/status_writer.py`, Dashboard-Templates

## 4. Bot-Ping zeigt "N/Ams"

- **Problem:** Dashboard zeigte "N/Ams" statt z.B. "45ms" bei der Bot-Latenz
- **Ursache:** Formatierungs-Bug im Template — Unit "ms" wurde falsch konkateniert
- **Fix:** Dashboard-Template korrigiert
- **Datei:** Dashboard-Template (Uebersichtsseite)

## 5. SAT Detail: Tick-Rate + RAM-Anzeige

- **Problem:** Tick-Rate mit zu vielen Dezimalstellen (z.B. 29.583333), RAM zeigte 0 MB
- **Fix:** Tick-Rate gerundet, RAM-Anzeige aus `satisfactory_status.json` korrekt gelesen
- **Datei:** SAT Detail-Template

## 6. Fehler-Uebersicht: Loesch-Button

- **Problem:** Error-Dashboard hatte keinen Button zum Loeschen alter Eintraege
- **Fix:** Delete-Button im Error-Dashboard-Template + API-Route dafuer hinzugefuegt
- **Dateien:** Error-Dashboard-Template, API-Route

## 7. Tabs-Design kaputt

- **Problem:** Admin Bot Panel, Konfiguration und Server-Detail hatten kaputtes Tab-Layout
- **Fix:** CSS/HTML der Tab-Komponenten in den betroffenen Templates korrigiert
- **Dateien:** Mehrere Dashboard-Templates (Admin, Config, Server-Detail)

---

## Noch offen (aus dieser Session)

- **SAT CPU/RAM zeigt 0:** psutil AccessDenied weil botuser den satisfactory-User-Prozess nicht lesen kann. `/proc`-Fallback wurde in `modules/satisfactory/server.py` eingebaut (liest VmRSS aus `/proc/<pid>/status` und CPU aus `/proc/<pid>/stat`), funktionierte aber noch nicht. Muss weiter debugged werden.
- **Spieler Online Chart:** Nach StatsCollector-Fix + bot_status.json-Filter noch nicht verifiziert ob der Chart korrekt Daten anzeigt.
- **StatsCollector bot_status.json:** Filter hinzugefuegt (`if json_file.stem == "bot_status": continue`) damit bot_status.json nicht als Gameserver gelesen wird. Datei: `modules/monitoring/stats_collector.py`
