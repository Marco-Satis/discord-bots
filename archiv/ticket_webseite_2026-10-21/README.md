# Archiv: Ticket-Transkript als Webseite (Maki-Lücke 8)

Gebaut und live am 21.10.2026 um 09:58 (`bd2bf44`). Auf Marcos Wunsch um 10:28 wieder ausgebaut (`ef2d9ba`).
Marco will die Seite aufheben, weil sie bei einem größeren Server wieder gebraucht werden kann.

## Inhalt

| Datei | Ziel beim Wiederherstellen |
|---|---|
| `tickets_route.py` | `web/routes/tickets_route.py` (`/tickets`, `/tickets/{id}`) |
| `tickets.html`, `ticket_detail.html` | `web/templates/` |
| `test_tickets_web.py` | `tests/` |
| `einbindung.diff` | Diff zum Einbinden: Router in `web/app.py`, Eintrag in `web/navigation.py`, Bereich in `web/middleware/aenderungs_protokoll.py`, Transkript-Link in `cogs/tickets_cog.py` |

## Wiederherstellen

1. Die Dateien an ihre Ziele kopieren.
2. `git apply archiv/ticket_webseite_2026-10-21/einbindung.diff` ausführen. Der Code hat sich seit dem 21.10. verändert, deshalb vorher mit `--check` prüfen und Konflikte von Hand lösen.
3. `tests/test_tickets_web.py` und die 4 Pflichttests laufen lassen.
4. Deployen über den Wrapper mit SERVICES `web-dashboard marshal-bot` (Marshal nur bei leeren Temp-Talks neu starten).

Die Live-Kopie liegt außerhalb von `web/` unter `/home/botuser/Discord_Bots/archiv/ticket_webseite_2026-10-21/` und wird nicht geladen.
