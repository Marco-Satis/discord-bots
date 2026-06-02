# Fix-Prompt — Nach Review gefundene Probleme beheben

> Nur verwenden falls REVIEW_ERGEBNIS.md FAILs oder kritische Warnungen enthaelt.

---

## Prompt:

```
Lies docs/review/REVIEW_ERGEBNIS.md.

Fixe autonom alle Punkte die als FAIL markiert sind. Fuer WARNUNG-Punkte: fixe wenn risikolos, sonst als "akzeptiert" markieren.

Pro Fix:
1. Betroffene Code-Stelle lesen
2. Fix implementieren
3. python tests/test_imports.py && python tests/test_routes.py
4. git add -A && git commit -m "Review-Fix: [Beschreibung]"
5. Deployen (.claude/skills/deployment/SKILL.md) — kein mehrzeiliges python3 -c via SSH
6. Logs pruefen (10s warten)
7. Falls Fix neue Fehler verursacht: Rollback, anderen Ansatz

Am Ende:
- REVIEW_ERGEBNIS.md aktualisieren
- PROGRESS.md aktualisieren

Starte jetzt.
```
