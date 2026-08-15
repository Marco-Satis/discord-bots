#!/bin/bash
# Sicherheitstest fuer scripts/deploy-systemd-dropin.sh
#
# Prueft, dass die Direktiven-Allow-Liste Umgehungsversuche wirklich abweist.
# Laeuft komplett in /tmp/dropin-test gegen eine Kopie des Wrappers, deren
# Zielpfade umgebogen sind — die echte Validierungslogik bleibt unveraendert.
set -u

SRC="$(dirname "$0")/../scripts/deploy-systemd-dropin.sh"
T=/tmp/dropin-test

rm -rf "$T"
mkdir -p "$T/staging" "$T/etc"

sed -e "s|^STAGING=.*|STAGING=$T/staging|" \
    -e "s|^LOG=.*|LOG=$T/log|" \
    -e "s|^\(\s*\)mkdir -p \"\$dir\"|\1dir=$T/etc; target=\"\$dir/\$name\"; mkdir -p \"\$dir\"|" \
    -e "s|install -o root -g root -m 0644|install -m 0644|" \
    -e "s|^systemctl daemon-reload|true|" \
    "$SRC" > "$T/wrapper.sh"
chmod +x "$T/wrapper.sh"

bash -n "$T/wrapper.sh" || { echo "SYNTAXFEHLER im Wrapper"; exit 1; }
echo "Wrapper-Syntax OK"
echo "=== Angriffs- und Gutfall-Tests ==="

FAILED=0

run_case() {
  local name="$1" svc="$2" content="$3" expect="$4" res
  rm -rf "$T/staging"
  mkdir -p "$T/staging/$svc"
  printf '%s\n' "$content" > "$T/staging/$svc/test.conf"
  if "$T/wrapper.sh" >/dev/null 2>&1; then res=ERLAUBT; else res=ABGELEHNT; fi
  if [ "$res" = "$expect" ]; then
    printf '  ok      %-26s -> %s\n' "$name" "$res"
  else
    printf '  FEHLER  %-26s -> %s (erwartet: %s)\n' "$name" "$res" "$expect"
    FAILED=$((FAILED + 1))
  fi
}

# --- Gutfaelle: genau die Direktiven, die wir real brauchen ---
run_case "ReadWritePaths" operator-bot \
"[Service]
ReadWritePaths=/home/satisfactory" ERLAUBT

run_case "SuccessExitStatus" minecraft-vanilla \
"[Service]
SuccessExitStatus=143" ERLAUBT

run_case "Kommentare+Leerzeilen" operator-bot \
"# Kommentar

[Service]
ReadWritePaths=/home/satisfactory" ERLAUBT

# Regression: Keys mit 'n' wurden von der alten tr-basierten Pruefung zerstoert
run_case "StartLimitIntervalSec" operator-bot \
"[Service]
StartLimitIntervalSec=300" ERLAUBT

run_case "InaccessiblePaths" operator-bot \
"[Service]
InaccessiblePaths=/srv" ERLAUBT

# Teilstring darf NICHT als Treffer durchgehen
run_case "Teilstring-Key" operator-bot \
"[Service]
Read=/tmp" ABGELEHNT

# --- Angriffe: muessen ALLE abgelehnt werden ---
run_case "ExecStart (Code als root)" operator-bot \
"[Service]
ExecStart=/bin/sh -c id" ABGELEHNT

run_case "ExecStartPre" operator-bot \
"[Service]
ExecStartPre=/bin/sh -c id" ABGELEHNT

run_case "User-Wechsel" operator-bot \
"[Service]
User=root" ABGELEHNT

run_case "EnvironmentFile" operator-bot \
"[Service]
EnvironmentFile=/etc/beliebige-datei" ABGELEHNT

run_case "Zeilenfortsetzung" operator-bot \
"[Service]
ReadWritePaths=/tmp \\
ExecStart=/bin/sh" ABGELEHNT

run_case ".include" operator-bot \
"[Service]
.include /etc/beliebige-datei" ABGELEHNT

run_case "fremde Section [Unit]" operator-bot \
"[Unit]
Description=x" ABGELEHNT

run_case "Service nicht in Whitelist" fremddienst \
"[Service]
ReadWritePaths=/tmp" ABGELEHNT

run_case "Direktive ohne Section" operator-bot \
"ReadWritePaths=/tmp" ABGELEHNT

run_case "gueltig + geschmuggelt" operator-bot \
"[Service]
ReadWritePaths=/home/satisfactory
ExecStart=/bin/sh -c id" ABGELEHNT

echo "----------------------------------------"
if [ "$FAILED" -eq 0 ]; then
  echo "ALLE TESTS BESTANDEN"
else
  echo "$FAILED TEST(S) FEHLGESCHLAGEN"
fi
rm -rf "$T"
exit "$FAILED"
