# Nachtlauf-Report 2026-06-03 (autonom)

> Marco schläft. Alle Änderungen getestet (4/4 + neue Tests), deployed via Wrapper (Auto-Rollback), committed + gepusht. `origin/main` = `d09a853`.

## Was erledigt wurde

### 🔴 P0 — monitor-bot SEGV-Crash-Loop (Hauptproblem, behoben)
- **Wurzel:** `satisfactory-save` 0.9.0 segfaultet am neuen Save-Format nach SAT-Update auf Build 23300422. Parser lief in-process (ThreadPool) → SIGSEGV killte ganzen Bot → Restart-Loop alle ~3-6 min (88 Crashes 02.–03.06.).
- **Fix:** Parser in isolierten **fork-Kindprozess** (`_extract_save_objects_worker`). Crash killt nur Subprozess → Header-Fallback, Bot überlebt. Commit `0365f83`.
- **Validiert live:** `analysis_error="Parser-Crash (Signal 11)"` im Cache = Child segfaultete, Bot blieb up. **0 SEGV** seit Deploy, NRestarts=0.
- Erklärte ALLE 3 Beschwerden (Restart-Spam + Offline-Spam + "Health-Check ignoriert MC").

### 3-Wege-Drift (lokal/GitHub/Server) — clean
- Gesamter Baum (186 .py) geprüft, normalisiert: **0 ungeklärter Drift**.
- 1 reconciled: `("technik","Technik")`-Kategorie war live am Server, nie in git → nach git übernommen (`ee6def3`), hätte sonst nächster Deploy gedroppt.
- Server läuft exakt deinen committed Stand.

### Nebenbefund — package_checker Logspam (behoben)
- `sudo apt update` (botuser ohne sudo) → PAM-auth-fail jeden Zyklus. Step ersatzlos entfernt (`apt-daily.timer` hält Listen frisch). Commit `a9516a4`, verifiziert kein PAM-Fail mehr.

### Backlog abgearbeitet (autonom, deployed)
- **L1** (`d09a853`): `_safe_start` cleart manual_stop-Marker nur noch wenn DIESES Update ihn setzte (Flag) — schützt manuellen Stop-Marker.
- **L3** (`d09a853`): Recovery-Notification im Update-Suppress-Fenster unterdrückt (kein irreführendes "Server recovery" nach geplantem Stop).
- **L4** (`d09a853`): `tests/test_update_checker.py` — 8 Regressions-Checks (Notify-Dedup + L1-Guard), plattformunabhängig. Schützt die SAT-Update-Fixes gegen stilles Brechen.

## ⚠️ Für dich (PuTTY) — konnte ich NICHT autonom (systemd-Gate)

### StartLimitBurst — Crash-Loop-Bremse (~5 min, empfohlen)
Verhindert künftige Endlos-Restart-Loops (egal welche Ursache): nach 5 Fehlversuchen in 5 min stoppt systemd → du merkst es, statt 88× stilles Flapping.

```bash
# pro Service (mind. monitor-bot; ideal alle 4):
sudo systemctl edit monitor-bot
# im Editor einfügen:
[Unit]
StartLimitIntervalSec=300
StartLimitBurst=5
# speichern, dann:
sudo systemctl daemon-reload

# Wiederholen für: gameserver-bot, admin-bot, web-dashboard
```
> Optional zusätzlich `OnFailure=`-Alert-Unit für Discord-Notify bei Burst-Stop — sag Bescheid wenn gewünscht.

## Offen (Backlog — nichts dringend)
| Punkt | Wichtigkeit | Warum nicht heute Nacht |
|---|---|---|
| **Deep-Stats zurück** (Parser-Migration zu `GreyHak/sat_sav_parse`, v1.2-Support) | LOW (kosmetisch) | 2-4h, braucht echte Saves + pip-Wechsel am Server (Classifier-Gate), blind zu riskant |
| **M2** doppelter UpdateChecker konsolidieren | LOW (latent) | 1-2h Architektur-OP an frisch stabilisiertem SAT-Pfad — unsupervised zu riskant |
| `satisfactory-save` Lib | — | bei 0.9.0 eingefroren (Apr 2025), kein Upgrade verfügbar → Deep-Stats bleiben leer bis Migration/Upstream |

## Stand der Bots beim Report
monitor-bot + gameserver-bot: **active**, 0 SEGV, 0 ERROR nach Deploy. Dashboard-Fabrik/Power-Stats leer (degraded, erwartet — Bot stabil).

Details P0: `docs/incidents/2026-06-03_monitor-bot-segv-crashloop.md`.
