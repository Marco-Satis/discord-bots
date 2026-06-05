#!/bin/bash
# ============================================================================
# Git-Pull Deploy-Wrapper — Discord-Bot-System (langfristiger Auto-Deploy)
# ----------------------------------------------------------------------------
# Loest den scp-Staging-Wrapper (deploy-discordbots) fuer das git-Clone-Setup ab:
# zieht origin/main per Fast-Forward in den Bot-Clone, py_compile-Gate, optionaler
# DB-Snapshot, Restart der Services, Verify, Auto-Rollback (git reset --hard).
#
# Installation (einmalig, als root):
#   install -o root -g root -m 0755 git-deploy-discordbots.sh /usr/local/sbin/git-deploy-discordbots
# sudoers (NOPASSWD, fester Pfad, KEINE Argumente):
#   marco ALL=(root) NOPASSWD: /usr/local/sbin/git-deploy-discordbots
# Aufruf:
#   ssh ... "sudo -n /usr/local/sbin/git-deploy-discordbots"
#
# Sicherheits-Eigenschaften:
#   - root-owned + nicht von marco/botuser beschreibbar -> Logik unveraenderbar.
#   - Branch HART = main; nur Fast-Forward (kein Merge-Commit, keine Divergenz).
#   - git-Ops laufen als botuser (Repo-Owner + Deploy-Key). Kein blanket sudo.
#   - Dirty-Tree -> Abbruch (clobbert keine lokalen Aenderungen).
#   - py_compile-Gate + Auto-Rollback (reset --hard auf alten HEAD).
#   - Restart NUR Whitelist-Services, nur wenn Code-Pfade geaendert wurden.
#   - DB-Snapshot vor Restart (Migrationen laufen beim Start) -> Datensicherheit.
#   - Audit-Log nach $LOG.
# ============================================================================
set -euo pipefail

DIR=/home/botuser/Discord_Bots
BRANCH=main
LOG=/var/log/discordbots-deploy.log
DB="$DIR/data/botdata.db"
SNAPDIR="$DIR/data/predeploy_backups"
ALLOWED_TOPDIRS="bots cogs modules utils web"
SERVICES="monitor-bot gameserver-bot admin-bot web-dashboard"
KEEP_SNAPSHOTS=5
TS=$(date +%s)

log() { echo "[$(date '+%F %T')] git-deploy: $*" | tee -a "$LOG"; }
# git als botuser im Bot-Clone. -H setzt HOME=/home/botuser, damit git+ssh den
# Deploy-Key (~botuser/.ssh) und ~botuser/.gitconfig findet (sonst schlaegt der
# SSH-Fetch von git@github.com fehl).
gitb() { sudo -u botuser -H git -C "$DIR" "$@"; }

# --- Vorbedingungen -------------------------------------------------------
[ -d "$DIR/.git" ] || { log "FEHLER: $DIR ist kein git-Clone"; exit 1; }

cur_branch=$(gitb rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
[ "$cur_branch" = "$BRANCH" ] || { log "FEHLER: Clone auf '$cur_branch', erwartet '$BRANCH'"; exit 1; }

# Nur TRACKED-Aenderungen blocken (lokale Edits an versionierten Files). Untracked
# Cruft (.bak-Files, Alt-Artefakte vom Clone-ueber-bestehendem-Dir) stoert einen
# Fast-Forward NICHT; ein echter Pfad-Konflikt (untracked File wird vom Pull
# ueberschrieben) wird vom ff-merge unten mit klarer Meldung abgefangen.
if ! gitb diff --quiet 2>/dev/null || ! gitb diff --cached --quiet 2>/dev/null; then
  log "FEHLER: Working-Tree hat TRACKED-Aenderungen (lokale Edits an versionierten Files) — manuell reconcilen. Abbruch."
  gitb status --porcelain --untracked-files=no | sed 's/^/    /' | tee -a "$LOG"
  exit 1
fi

# --- Fetch + FF-Pruefung --------------------------------------------------
OLD_HEAD=$(gitb rev-parse HEAD)
log "=== Git-Deploy start (HEAD $OLD_HEAD, Branch $BRANCH) ==="
gitb fetch --quiet origin "$BRANCH" || { log "FEHLER: git fetch fehlgeschlagen"; exit 1; }
NEW_HEAD=$(gitb rev-parse FETCH_HEAD)

if [ "$OLD_HEAD" = "$NEW_HEAD" ]; then
  log "Already up to date ($OLD_HEAD) — nichts zu tun."
  exit 0
fi
gitb merge-base --is-ancestor "$OLD_HEAD" "$NEW_HEAD" \
  || { log "FEHLER: kein Fast-Forward ($OLD_HEAD -> $NEW_HEAD divergiert) — Abbruch"; exit 1; }

# --- Geaenderte Files ermitteln (vor dem Merge) ---------------------------
mapfile -t CHANGED < <(gitb diff --name-only "$OLD_HEAD" "$NEW_HEAD")
log "  $((${#CHANGED[@]})) Datei(en) geaendert: $OLD_HEAD..$NEW_HEAD"

CODE_CHANGED=0
for f in "${CHANGED[@]}"; do
  top="${f%%/*}"
  if echo " $ALLOWED_TOPDIRS " | grep -q " $top "; then CODE_CHANGED=1; fi
done

# --- Fast-Forward anwenden ------------------------------------------------
gitb merge --ff-only "$NEW_HEAD" >/dev/null 2>&1 \
  || { log "FEHLER: ff-merge fehlgeschlagen"; gitb reset --hard "$OLD_HEAD" >/dev/null 2>&1 || true; exit 1; }
log "  Fast-Forward: $OLD_HEAD -> $NEW_HEAD"

rollback() {
  log "!! Rollback -> $OLD_HEAD"
  gitb reset --hard "$OLD_HEAD" >/dev/null 2>&1 || log "  WARN: git reset fehlgeschlagen"
}

# --- py_compile-Gate auf geaenderte .py-Files -----------------------------
PYFILES=()
for f in "${CHANGED[@]}"; do
  [[ "$f" == *.py ]] && [ -f "$DIR/$f" ] && PYFILES+=("$DIR/$f")
done
if [ "${#PYFILES[@]}" -gt 0 ]; then
  if ! sudo -u botuser "$DIR/venv/bin/python" -m py_compile "${PYFILES[@]}" 2>>"$LOG"; then
    log "!! COMPILE FEHLGESCHLAGEN — Rollback, KEIN Restart."
    rollback
    exit 1
  fi
  log "  py_compile OK (${#PYFILES[@]} Files)"
fi

# --- Nur Doku/Tests geaendert? Kein Restart noetig ------------------------
if [ "$CODE_CHANGED" -eq 0 ]; then
  log "=== Git-Deploy OK (nur Nicht-Code-Pfade geaendert, kein Restart) — HEAD $NEW_HEAD ==="
  exit 0
fi

# --- DB-Snapshot vor Restart (Migrationen laufen beim Start) --------------
if [ -f "$DB" ]; then
  sudo -u botuser mkdir -p "$SNAPDIR"
  if sudo -u botuser sqlite3 "$DB" ".backup '$SNAPDIR/botdata.$TS.db'" 2>>"$LOG"; then
    log "  DB-Snapshot: $SNAPDIR/botdata.$TS.db (sqlite .backup, WAL-konsistent)"
  elif sudo -u botuser cp -a "$DB" "$SNAPDIR/botdata.$TS.db" 2>>"$LOG"; then
    log "  DB-Snapshot: $SNAPDIR/botdata.$TS.db (cp-Fallback)"
  else
    log "  WARN: DB-Snapshot fehlgeschlagen — Deploy faehrt fort (Migrationen i.d.R. additiv)"
  fi
  # Prune: nur die letzten $KEEP_SNAPSHOTS behalten
  ls -1t "$SNAPDIR"/botdata.*.db 2>/dev/null | tail -n +$((KEEP_SNAPSHOTS + 1)) | xargs -r rm -f
fi

# --- Restart (monitor-bot zuerst) -----------------------------------------
ordered=()
for s in $SERVICES; do [ "$s" = "monitor-bot" ] && ordered+=("$s"); done
for s in $SERVICES; do [ "$s" != "monitor-bot" ] && ordered+=("$s"); done
first=1
for s in "${ordered[@]}"; do
  systemctl restart "$s"
  log "  restart: $s"
  [ "$first" = 1 ] && { sleep 8; first=0; }
done

# --- Verify (+ Rollback bei Fehlschlag) -----------------------------------
sleep 3
fail=0
for s in "${ordered[@]}"; do
  st=$(systemctl is-active "$s" || true)
  log "  status $s: $st"
  [ "$st" = "active" ] || fail=1
done
if [ "$fail" -ne 0 ]; then
  log "!! Mind. 1 Service NICHT active — Code-Rollback + Restart"
  rollback
  for s in "${ordered[@]}"; do systemctl restart "$s" || true; done
  log "Rollback + Restart erledigt."
  exit 1
fi

log "=== Git-Deploy OK — HEAD $NEW_HEAD, Services neu gestartet, DB-Snapshot $TS ==="
