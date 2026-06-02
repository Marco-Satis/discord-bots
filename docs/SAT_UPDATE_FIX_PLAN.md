# SAT-Update-Subsystem-Fix — Plan & Stand (2026-06-02)

> Fokussierte Korrektur nach v4.4.0-Konsolidierung. SAT läuft gesund auf Build **21237536**.
> Sofort-Stop (Marco/PuTTY, stoppt SAT-Thrashing + False-Crashes):
> `sudo -u botuser sed -i 's/"auto_update_enabled": true/"auto_update_enabled": false/' /home/botuser/Discord_Bots/config/config.json && sudo systemctl restart monitor-bot`

## Bereits gefixt (heute, deployed)
- suppress()-Signatur-Crash (`ea2e1ee`)
- sudo für steamcmd-as-satisfactory (`/etc/sudoers.d/botuser-satisfactory-update`)
- Watchdog-Restart abs. Pfad + `.service` (`5c33e5d`)
- systemd `ReadWritePaths=/home/satisfactory` an monitor-bot (Marco) — steamcmd kann jetzt schreiben
- `+app_info_update 1` im Update-Pfad (Symmetrie zum Check)

## Verifizierte Root-Causes (verbleibend)
1. **3× Notify-Spam:** `update_checker.check()` feuerte `on_update_available` bei JEDEM Check ohne State-Guard. Mehrere Caller: gameserver-bot 30s-Loop (`gameserver_bot.py:252`), scheduler_cog (`:495`), monitor-bot initial (`:2456`). Zudem **2 UpdateChecker-Instanzen** (monitor-bot `:169` + gameserver-bot `:113`).
2. **steamcmd Code 8:** Selbstupdate-Relaunch-Quirk — `steamcmd.sh` exitet 8 ohne app_update wenn es sich selbst aktualisiert (Exit nach ~10s). Inkonsistent (19:02 lief voll durch, 19:13 nur Bootstrap).
3. **False-Crash:** `health_check.py` HealthMonitor (`:154`) wertet Prozess-Tod-während-online als Crash → `_handle_crash` (`:174`) → `on_crash` → "SERVER CRASH ERKANNT" + crash_replay. Geplanter Update-Stop löst das aus. HAR-suppress deckt nur Auto-Restart, NICHT Crash-Detection. (Crash-Log crash_001_190107 war ein gesunder Server — kein echter Crash, keine Datenverluste.)
4. **23300422 echt?** Check (mit app_info_update) sieht public=23300422; appmanifest installed=21237536, BytesToDownload=0. Ungeklärt ob 23300422 wirklich public/anonym installierbar ist. **Ground-Truth-Test ausstehend** (Marco/PuTTY, SAT stoppen, 2× steamcmd manuell):
   `sudo -u satisfactory /usr/games/steamcmd +force_install_dir /home/satisfactory/SatisfactoryDedicatedServer +login anonymous +app_info_update 1 +app_update 1690800 validate +quit` (2×, 2. Lauf = Test).

## Fixes
### ✅ Fix 1 — Notify-Dedup (update_checker.check()) — IMPLEMENTIERT
State-Guard `self._notified_buildid`: `on_update_available` + Log nur bei available != _notified_buildid. Reset wenn kein Update. → 1 Notify pro Build je Instanz.
### ☐ Fix 2 — steamcmd Retry (perform_update)
Bei returncode==8 UND Output zeigt Relaunch (`Starting`/`linux32/steamcmd` / kein echter Fehler): steamcmd EINMAL erneut laufen (nach Selbstupdate ist es current). Hinweis: Edit der subprocess-Zeile triggert security_reminder_hook → ggf. via Helper-Script (atomic) editieren.
### ☐ Fix 3 — Crash-Suppress während Update (health_check.py)
Vor `_handle_crash` prüfen: HAR.is_suppressed("sat","main") ODER ein `update_in_progress`-Flag gesetzt → dann KEIN Crash (geplanter Stop). Erfordert Zugriff auf har/Flag in HealthMonitor.
### ☐ Fix 4 — 23300422 Reconcile
Falls Ground-Truth = Phantom (app_update bleibt 21237536): nach erfolglosem Update `installed_buildid` neu lesen; wenn weiterhin == installed und app_update "up to date" → `update_available=False` setzen / nicht weiter als verfügbar melden (verhindert Dauer-„verfügbar").

## Architektur-Schuld (separat, später)
Doppelter UpdateChecker (monitor-bot + gameserver-bot). Ziel: SAT-Update + Notify auf EINEM Bot (gameserver-bot per Service-Map). Fix 1 entschärft Symptom; echte Konsolidierung als eigene Aufgabe.

## Test/Deploy
4 Pflicht-Tests + py_compile. Deploy via Wrapper (`sudo -n /usr/local/sbin/deploy-discordbots`, SERVICES: monitor-bot gameserver-bot). Verify: 1× Notify, kein False-Crash bei Update-Stop, Update zieht 23300422 ODER sauberes „aktuell".
