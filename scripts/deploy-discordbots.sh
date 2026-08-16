#!/bin/bash
# ============================================================================
# Autonomer Deploy-Wrapper — Discord-Bot-System
# ----------------------------------------------------------------------------
# Installation (einmalig, als root):
#   install -o root -g root -m 0755 deploy-discordbots.sh /usr/local/sbin/deploy-discordbots
# sudoers (NOPASSWD, fester Pfad, KEINE Argumente):
#   marco ALL=(root) NOPASSWD: /usr/local/sbin/deploy-discordbots
#
# Sicherheits-Eigenschaften:
#   - root-owned + nicht von marco/botuser beschreibbar -> Logik unveraenderbar.
#   - Liest NUR aus marco-eigenem Staging ($STAGING); fremder Plant wird abgelehnt.
#   - Schreibt NUR in den Bot-Pfad ($DIR); Pfad-Traversal/absolut/Symlink abgelehnt.
#   - Restart NUR Whitelist-Services. py_compile-Gate + Auto-Rollback.
#   - Audit-Log nach $LOG.
# ============================================================================
set -euo pipefail

# Staging-Verzeichnis des Entwicklers. Ueber DBOTS_STAGING anpassbar,
# damit im Repo kein fester Benutzername steht.
STAGING="${DBOTS_STAGING:-/home/$(logname 2>/dev/null || echo entwickler)/dbots_staging}"
DIR=/home/botuser/Discord_Bots
LOG=/var/log/discordbots-deploy.log
ALLOWED_TOPDIRS="bots cogs modules utils web"
ALLOWED_SERVICES="recon-bot operator-bot marshal-bot web-dashboard"
TS=$(date +%s)

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# --- Staging-Sicherheit ---------------------------------------------------
[ -d "$STAGING" ] || { log "FEHLER: Staging $STAGING fehlt"; exit 1; }
owner=$(stat -c '%U' "$STAGING")
[ "$owner" = "marco" ] || { log "FEHLER: Staging gehoert '$owner', erwartet 'marco'"; exit 1; }
[ -f "$STAGING/MANIFEST" ] || { log "FEHLER: $STAGING/MANIFEST fehlt"; exit 1; }

# --- MANIFEST einlesen + validieren --------------------------------------
mapfile -t REL < <(grep -vE '^\s*(#|$)' "$STAGING/MANIFEST" || true)
[ "${#REL[@]}" -gt 0 ] || { log "FEHLER: MANIFEST leer"; exit 1; }

declare -a SRCS TARGETS RELS
for raw in "${REL[@]}"; do
  rel="${raw%$'\r'}"
  case "$rel" in
    /*|*..*) log "ABGELEHNT (Pfad ungueltig): $rel"; exit 1 ;;
  esac
  top="${rel%%/*}"
  echo " $ALLOWED_TOPDIRS " | grep -q " $top " || { log "ABGELEHNT (Top-Dir nicht erlaubt): $rel"; exit 1; }
  src="$STAGING/$rel"
  { [ -f "$src" ] && [ ! -L "$src" ]; } || { log "ABGELEHNT (kein regul. File): $rel"; exit 1; }
  tgt="$DIR/$rel"
  [ -L "$tgt" ] && { log "ABGELEHNT (Ziel ist Symlink): $rel"; exit 1; }
  [ -d "$(dirname "$tgt")" ] || { log "ABGELEHNT (Ziel-Dir fehlt): $rel"; exit 1; }
  RELS+=("$rel"); SRCS+=("$src"); TARGETS+=("$tgt")
done

# --- Services (optional, Whitelist) --------------------------------------
declare -a SVC
if [ -f "$STAGING/SERVICES" ]; then
  while read -r line; do
    s="${line%$'\r'}"; [ -z "$s" ] && continue
    echo " $ALLOWED_SERVICES " | grep -q " $s " || { log "ABGELEHNT (Service nicht erlaubt): $s"; exit 1; }
    SVC+=("$s")
  done < "$STAGING/SERVICES"
fi
[ "${#SVC[@]}" -gt 0 ] || SVC=(recon-bot)

log "=== Deploy start: ${#TARGETS[@]} File(s), Services: ${SVC[*]} ==="

# --- Backup + Copy --------------------------------------------------------
for i in "${!TARGETS[@]}"; do
  t="${TARGETS[$i]}"; s="${SRCS[$i]}"
  [ -f "$t" ] && sudo -u botuser cp -a "$t" "$t.bak.$TS"
  install -o botuser -g botuser -m 644 "$s" "$t"
  log "  deployed: ${RELS[$i]}"
done

# --- py_compile-Gate ------------------------------------------------------
PYFILES=()
for t in "${TARGETS[@]}"; do [[ "$t" == *.py ]] && PYFILES+=("$t"); done
if [ "${#PYFILES[@]}" -gt 0 ]; then
  if ! sudo -u botuser "$DIR/venv/bin/python" -m py_compile "${PYFILES[@]}"; then
    log "!! COMPILE FEHLGESCHLAGEN — Rollback"
    for i in "${!TARGETS[@]}"; do
      t="${TARGETS[$i]}"; [ -f "$t.bak.$TS" ] && sudo -u botuser cp -a "$t.bak.$TS" "$t"
    done
    log "Rollback erledigt, KEIN Restart."
    exit 1
  fi
fi

# --- Restart (recon-bot zuerst) ----------------------------------------
ordered=()
for s in "${SVC[@]}"; do [ "$s" = "recon-bot" ] && ordered+=("$s"); done
for s in "${SVC[@]}"; do [ "$s" != "recon-bot" ] && ordered+=("$s"); done
first=1
for s in "${ordered[@]}"; do
  systemctl restart "$s"
  log "  restart: $s"
  [ "$first" = 1 ] && { sleep 8; first=0; }
done

# --- Verify (+ Rollback bei Fehlschlag) ----------------------------------
sleep 3
fail=0
for s in "${ordered[@]}"; do
  st=$(systemctl is-active "$s" || true)
  log "  status $s: $st"
  [ "$st" = "active" ] || fail=1
done
if [ "$fail" -ne 0 ]; then
  log "!! Mind. 1 Service NICHT active — Rollback + Restart"
  for i in "${!TARGETS[@]}"; do
    t="${TARGETS[$i]}"; [ -f "$t.bak.$TS" ] && sudo -u botuser cp -a "$t.bak.$TS" "$t"
  done
  for s in "${ordered[@]}"; do systemctl restart "$s"; done
  log "Rollback + Restart erledigt."
  exit 1
fi
log "=== Deploy OK (Backups: $DIR/<file>.bak.$TS) ==="
