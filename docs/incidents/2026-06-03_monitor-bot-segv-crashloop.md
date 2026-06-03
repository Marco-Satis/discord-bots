# Incident 2026-06-03 — monitor-bot SEGV-Crash-Loop

> Status: **behoben** (Fix deployed 03:20, commit `0365f83`). Schweregrad: **P0** (Dauerausfall des Monitoring).

## Symptom
- monitor-bot startet alle ~3-6 min neu (systemd `Scheduled restart job, restart counter`).
- Discord-Spam "Monitor Bot gestartet".
- Offline-Benachrichtigungen für Server ziehen sich über Stunden, obwohl Guards existieren.
- Health-Check scheint manuell gestoppte MC-Server nicht zu respektieren.

## Messung
| Tag | SEGV-Crashes |
|---|---|
| 2026-06-01 | 0 |
| 2026-06-02 | 52 (erster: 19:19:50) |
| 2026-06-03 | 36 (bis 03:20) |

`systemd[1]: monitor-bot.service: Main process exited, code=killed, status=11/SEGV` → `Restart=on-failure` → Neustart. ~88 Crashes total.

## Wurzelursache (5-Why)
1. **Warum** startet der Bot neu? → SIGSEGV (Signal 11), systemd restartet bei `on-failure`.
2. **Warum** SIGSEGV? → Letzte App-Zeile vor jedem Crash (1:1, 36/36): `[satisfactory.savegame_analyzer] INFO: Analyzing save: …`. Crash im nativen Parser.
3. **Warum** crasht der Parser? → `satisfactory-save` 0.9.0 kann das **neue Save-Format** nicht lesen. SAT wurde am 02.06. abends auf Build **23300422** geupdatet → neues Format → Parser liest Müll → SIGSEGV. Erster Crash 02.06. 19:19 = exakt nach dem Update.
4. **Warum** killt das den ganzen Bot? → `_analyze_save` lief den nativen Parser via `run_in_executor(None, …)` = ThreadPool **im Bot-Prozess**. Ein SIGSEGV in einem Thread killt den gesamten Prozess. `try/except` kann SIGSEGV nicht fangen (Signal ≠ Exception).
5. **Warum** trat es wiederholt auf? → SAT-Autosaves rotieren alle paar Minuten → mtime ändert → Cache-Invalidierung → Re-Analyse → Crash → Restart → Loop.

**Sekundär-Effekt:** Weil der Bot alle ~3 min crashte (< 300s Monitoring-Intervalle), liefen `mc_health_check_task` / `port_monitor` / Health-Check nie vollständig durch → die (korrekt implementierten) manual_stop-Guards konnten nicht greifen → wirkte wie "Health-Check ignoriert gestoppte Server". Wurzel war der Crash, **nicht** fehlende Guards.

## Restore-Schritte (durchgeführt)
1. Root-Cause via `journalctl -u monitor-bot | grep SEGV` + letzte App-Zeile vor jedem Crash korreliert.
2. `_analyze_save` umgebaut: nativer Parse läuft in **isoliertem fork-Kindprozess** (`_extract_save_objects_worker`), gibt nur `(header, [class_names])` zurück. SIGSEGV killt nur den Subprozess; Parent erkennt `exitcode < 0` → Header-only-Fallback. (`modules/satisfactory/savegame_analyzer.py`)
3. 4/4 Tests grün + py_compile → Deploy via Wrapper → Restart → Verify.

## Lesson
**Native / C-Extension-Parser NIE in-process im langlebigen Daemon laufen lassen.** Ein SIGSEGV (z.B. durch Format-Drift nach einem Game-Update) ist nicht abfangbar und reißt den ganzen Prozess mit. Lösung: in einem `fork`-Kindprozess isolieren, Crash über `exitcode < 0` erkennen, auf degradierten Modus zurückfallen. `fork` statt `spawn`, wenn das Hauptmodul Setup auf Modul-Ebene hat (kein `__main__`-Reimport).

## Präventiv-Maßnahmen
- [x] Parser-Isolation (commit `0365f83`).
- [x] `package_checker`-`sudo apt update`-PAM-Logspam entfernt (commit `a9516a4`) — unabhängiger Nebenbefund.
- [ ] Optional: systemd `StartLimitIntervalSec`/`StartLimitBurst` setzen, damit ein künftiger Crash-Loop nach N Versuchen stoppt + alarmiert statt endlos zu restarten.
- [ ] Optional: Alert wenn `analysis_error` über mehrere Zyklen gesetzt bleibt (Frühwarnung Format-Drift).

## Deep-Stats wiederherstellen — Lib-Recherche (2026-06-03)
`satisfactory-save` (PyPI) ist bei **0.9.0** (Release 2025-04-08) **eingefroren** — KEIN neueres Release, das das Save-Format von Build 23300422 (Satisfactory 1.1.x) liest. Ein simples `pip install -U` bringt also nichts (0.9.0 ist bereits installiert + crasht).

Optionen für Deep-Fabrik/Power-Stats:
1. **Warten** auf ein `satisfactory-save`-Update (upstream, ungewiss — letztes Release Apr 2025).
2. **Parser-Migration** zu [`GreyHak/sat_sav_parse`](https://github.com/GreyHak/sat_sav_parse) — Python-Tools, laut Beschreibung Support bis **Satisfactory v1.2.0.0**. Andere API → `_extract_save_objects_worker` müsste umgeschrieben werden (eigene Aufgabe, ~2-4h).
3. **Degraded-Mode belassen** (aktuell): Header-only, `available:false`, Bot stabil. Deep-Stats fehlen im Dashboard, sonst kein Impact.

Empfehlung: Option 3 vorerst (kein Druck, System stabil). Option 2 als separater Task wenn Deep-Stats wieder gewünscht.

Quellen: [satisfactory-save PyPI](https://pypi.org/project/satisfactory-save/) · [GreyHak/sat_sav_parse](https://github.com/GreyHak/sat_sav_parse)
