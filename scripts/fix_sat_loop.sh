#!/usr/bin/env bash
# ============================================================================
# Satisfactory Auto-Update-Loop Fix (Schritt 1+2)
#   Ursache: neuer Build 23855705 (24.06.) wird vom Auto-Update nicht installiert
#   -> update_available bleibt True -> _check_auto_update_install retryt alle 30min.
#
# Dieses Script: Auto-Update aus -> recon-bot stoppen (keine Health-/Update-
#   Interferenz) -> SAT sauber stoppen -> steamcmd app_update -> Build verifizieren
#   -> SAT starten -> Auto-Update NUR wieder an wenn Build == 23855705 -> recon-bot an.
#
# Ausfuehren:  sudo bash /tmp/fix_sat_loop.sh
# ============================================================================
set -uo pipefail

REPO=/home/botuser/Discord_Bots
CFG=$REPO/config/config.json
INSTALL=/home/satisfactory/SatisfactoryDedicatedServer
APPID=1690800
EXPECT_BUILD=23855705
TS=$(date +%s)

log(){ echo "[fix $(date +%H:%M:%S)] $*"; }
[ "$(id -u)" = "0" ] || { echo "Muss als root laufen: sudo bash $0"; exit 2; }

manifest_build(){ grep -oE '"buildid"[[:space:]]+"[0-9]+"' "$INSTALL/steamapps/appmanifest_$APPID.acf" 2>/dev/null | grep -oE '[0-9]+' | head -1; }

set_autoupdate(){ # $1 = true|false  (als botuser; bool-Toggle bleibt valides JSON)
  local to="$1" from
  [ "$to" = "true" ] && from="false" || from="true"
  su - botuser -c "grep -q '\"auto_update_enabled\": $to' '$CFG' && { echo '  config: bereits $to'; exit 0; }; sed -i 's/\"auto_update_enabled\": $from/\"auto_update_enabled\": $to/' '$CFG' && grep -q '\"auto_update_enabled\": $to' '$CFG' && echo '  config: auto_update_enabled -> $to'"
}

log "0) Backup config -> $CFG.bak.preloopfix.$TS"
su - botuser -c "cp -a '$CFG' '$CFG.bak.preloopfix.$TS'" || { echo "Backup fehlgeschlagen"; exit 2; }

log "1) Auto-Update AUS"
set_autoupdate false || { echo "config-Edit fehlgeschlagen"; exit 2; }

log "2) recon-bot stoppen (keine Health-/Update-Interferenz waehrend Update)"
systemctl stop recon-bot
sleep 2

log "3) Satisfactory sauber stoppen (systemctl stop -> kein Restart=on-failure-Trigger)"
BEFORE=$(manifest_build); log "   Build vor Update: ${BEFORE:-unbekannt}"
systemctl stop satisfactory
for i in $(seq 1 15); do systemctl is-active --quiet satisfactory || break; sleep 2; done
if systemctl is-active --quiet satisfactory; then
  log "   WARN: SAT nach 30s noch aktiv — breche ab, starte recon-bot wieder"
  systemctl start recon-bot; exit 1
fi
log "   SAT gestoppt"

log "4) steamcmd app_update (als satisfactory, bis 2x wegen Code-8-Selbstupdate)"
rc=99
for attempt in 1 2; do
  sudo -u satisfactory env HOME=/home/satisfactory /usr/games/steamcmd \
    +force_install_dir "$INSTALL" +login anonymous \
    +app_info_update 1 +app_update "$APPID" validate +quit 2>&1 | tail -6
  rc=${PIPESTATUS[0]}
  log "   steamcmd Versuch $attempt rc=$rc"
  [ "$rc" != "8" ] && break
  log "   rc=8 (steamcmd-Selbstupdate) -> erneuter Versuch"
done

AFTER=$(manifest_build); log "5) Build nach Update: ${AFTER:-unbekannt} (erwartet $EXPECT_BUILD)"

log "6) Satisfactory starten"
systemctl start satisfactory
sleep 8
SAT_ACTIVE=$(systemctl is-active satisfactory)
log "   satisfactory: $SAT_ACTIVE"

log "7) recon-bot wieder starten"
systemctl start recon-bot
sleep 3
log "   recon-bot: $(systemctl is-active recon-bot)"

echo ""
if [ "$AFTER" = "$EXPECT_BUILD" ]; then
  log "8) Build verifiziert ($AFTER) -> Auto-Update wieder AN"
  set_autoupdate true
  systemctl restart recon-bot; sleep 2
  echo "================ FIX OK ================"
  echo "Build $BEFORE -> $AFTER. SAT=$SAT_ACTIVE. Auto-Update wieder aktiv (Build aktuell -> kein Loop)."
  echo "Hinweis: NavMesh-Spam/CPU ist ein Save-Thema, unabhaengig vom Build — separat pruefen."
else
  echo "================ UPDATE NICHT GELANDET ================"
  echo "Build blieb $AFTER (erwartet $EXPECT_BUILD). Auto-Update bleibt AUS (kein Loop)."
  echo "steamcmd-Output oben pruefen — Ursache warum app_update nicht installiert."
  echo "SAT laeuft auf altem Build weiter (Build $AFTER)."
fi
echo "Backup config: $CFG.bak.preloopfix.$TS"
