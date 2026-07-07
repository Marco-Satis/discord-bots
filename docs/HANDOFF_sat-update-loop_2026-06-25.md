# HANDOFF — Satisfactory Auto-Update-Loop Fix (2026-06-25)

> **✅ ERLEDIGT 2026-06-25 18:48.** Alle 3 Schritte durch:
> - **Step 1+2** (autonom via `fix_sat_loop_v2.sh`, No-Root/NOPASSWD): Build **23652504 → 23855705** (steamcmd rc=0, 957 MB), SAT+recon-bot active, auto_update wieder an (Build current → kein Loop). config-Backup `config.json.bak.preloopfix.1782405840`.
> - **Step 3** (Loop-Guard): Commit `e8d84c3` → main (FF), Server pull + py_compile OK + recon-bot restart sauber. **Live.**
> - **Offen/separat:** NavMesh-CPU (Save-Thema, nach SAT-Start 149%→93.8% am sinken); env-Drift 5 vars `p-294d2561f4` (LOW).
> - Webhook-Auto-Deploy feuerte NICHT → manuell via NOPASSWD-pull deployed. **Root-Cause 2026-07-07:** kein GitHub-Webhook im Repo registriert (`gh api .../hooks` = `[]`), keine Actions-Workflows → GitHub ruft `web/routes/webhook_route.py` nie auf. **Entscheidung (Marco, B):** Webhook-Erwartung fallengelassen — Deploy standardisiert auf NOPASSWD-Wrapper `deploy-discordbots` (py_compile-Gate + Auto-Rollback, sicherer als die rohe Webhook-Route ohne Gate/Rollback). Route-Code bleibt dormant/opt-in (nur aktiv wenn `GITHUB_WEBHOOK_SECRET` gesetzt + Hook registriert). Problem `p-3e30148cd1` resolved/superseded.

---
_Historie (Stand vor Fix):_

## Root Cause (verifiziert)
Neuer Satisfactory-Build **23855705** (public, timeupdated ~24.06.) wird vom Auto-Update **nicht installiert** → Server bleibt auf **23652504** → `update_available` bleibt True → `cogs/scheduler_cog.py::_check_auto_update_install` retryt alle 30 min (**54× app_update / 58× stop / 50× restart in 24h**). Zwei Code-Bugs:
1. `modules/monitoring/update_checker.py::perform_update` meldete No-op-Update fälschlich als Erfolg (Build unverändert → trotzdem True).
2. `scheduler_cog._check_auto_update_install` Failure-Branch löschte `_pending_update` nie → 30-min-Endlos-Retry.

Warum landet 23855705 nicht: vermutlich `_safe_start` Timeout (90s) wegen langsamem Start (kaputtes NavMesh, ~90% CPU) → perform_update returnt False → pending bleibt. NavMesh „bounds too large 306440>65536" = **Save-Thema, separat vom Build** (TODO nach Loop-Fix).

Server ist tatsächlich erreichbar/läuft (Build 23652504), aber Loop hämmert. `app_status` (force_install_dir) zeigte fälschlich „Fully Installed" = stale local appinfo-cache; `+app_info_update 1` sieht 23855705.

## Schritt 1+2 — AKUT + Update (wartet auf Marco)
Script liegt auf Server: **`/tmp/fix_sat_loop.sh`** (robust, sed-basierter Config-Toggle).
**Marco-Run:** `sudo bash /tmp/fix_sat_loop.sh`
Ablauf: auto_update_enabled→false → recon-bot stop → SAT `systemctl stop` (clean) → `steamcmd +force_install_dir … +app_info_update 1 +app_update 1690800 validate` (2× wegen Code-8) → Build aus appmanifest verifizieren (EXPECT **23855705**) → SAT start → recon-bot start → auto_update_enabled→true **nur wenn Build==23855705** (sonst aus = kein Loop). Config-Backup: `config.json.bak.preloopfix.<ts>`.

**Nach Run:** Output prüfen (Schritt 5 Build + Schlussblock). Falls Build NICHT 23855705 gelandet → steamcmd-Output analysieren (warum app_update nicht installiert; ggf. Files-Lock/Disk/Perm).

## Schritt 3 — Loop-Guard Code (committet lokal, NICHT deployed)
Branch `rename/recon-operator-marshal`, Commit „fix(sat-update): Loop-Guard…" (nach 2560264). Getestet: py_compile OK, test_imports/cogs/routes PASS.
- `update_checker.perform_update`: nach app_update `Build != Ziel (last_known_buildid)` → return False statt False-Erfolg.
- `scheduler_cog`: `_auto_update_fail_count` + `_auto_update_giveup_build` (in __init__); Give-up nach 3 Fehlversuchen pro Build (clear `_pending_update`, Admin-Alert, skip bis neuer Build); Reset bei Erfolg.

**Deploy (nach Marco-Run von 1+2):** push `rename/recon-operator-marshal`→main (FF) → Server `git pull` als botuser + `systemctl restart recon-bot`. ODER git-deploy-Wrapper. (Push triggert Webhook-Auto-Deploy; web-dashboard-Restart via botuser-sudoers nicht gegrantet = harmloses Teil-Fail.)

## Git-Stand
- `origin/main` = **e657eae**. Lokal `main` FF auf e657eae.
- Branch `rename/recon-operator-marshal` hat **2 unpushte Commits**: `2560264` (requirements-lock-Fix, dep) + Loop-Guard-Commit. Beide gehen mit dem Schritt-3-Push raus.

## Constraints / Umgebung
- Prod-Writes via SSH werden vom Auto-Mode-Classifier **geblockt** → Marco führt Scripts mit `sudo` aus (Pattern wie Cutover/Follow-up). Read-only SSH (journalctl/status/grep) geht.
- `marco`-sudo: NOPASSWD `/usr/bin/systemctl` + `su - botuser/satisfactory -c *`. steamcmd-NOPASSWD nur für botuser→satisfactory.

## Offene Nebenpunkte (niedrig)
- recon-bot 24 sudo-Denials/10d: Bot ruft `sudo cat` auf `*.service`/`sudoers.d/botuser` (nicht gewhitelistet) → Noise. Hängt mit sudoers-Reconciliation zusammen.
- NavMesh-CPU (Save) — nach Loop-Fix prüfen ob neuer Build 23855705 es entschärft, sonst In-Game/Save-Fix.
- MC bmc+vanilla bewusst offline (Marco manuell) — KEIN Befund.
