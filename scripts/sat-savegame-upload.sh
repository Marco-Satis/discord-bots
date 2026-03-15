#!/bin/bash
# ============================================================
# sat-savegame-upload.sh
# Kopiert ein Savegame sicher in das Satisfactory SaveGames-Verzeichnis
# Wird von botuser via sudo aufgerufen
# ============================================================

SAVE_DIR="/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames"
SAT_USER="satisfactory"

SRC="$1"
DEST_NAME="$2"

# --- Validierung ---
if [[ -z "$SRC" || -z "$DEST_NAME" ]]; then
    echo "FEHLER: Usage: $0 <quelldatei> <zieldateiname.sav>"
    exit 1
fi

if [[ ! -f "$SRC" ]]; then
    echo "FEHLER: Quelldatei nicht gefunden: $SRC"
    exit 1
fi

if [[ "$DEST_NAME" != *.sav ]]; then
    echo "FEHLER: Nur .sav Dateien erlaubt"
    exit 1
fi

# Keine Pfad-Traversal erlauben
if [[ "$DEST_NAME" == */* || "$DEST_NAME" == *..* ]]; then
    echo "FEHLER: Ungueltiger Dateiname"
    exit 1
fi

# --- Backup falls Datei existiert ---
if [[ -f "$SAVE_DIR/$DEST_NAME" ]]; then
    cp -p "$SAVE_DIR/$DEST_NAME" "$SAVE_DIR/${DEST_NAME}.bak"
    chown "$SAT_USER:$SAT_USER" "$SAVE_DIR/${DEST_NAME}.bak"
    echo "BACKUP: ${DEST_NAME}.bak erstellt"
fi

# --- Kopieren und Rechte setzen ---
cp "$SRC" "$SAVE_DIR/$DEST_NAME"
chown "$SAT_USER:$SAT_USER" "$SAVE_DIR/$DEST_NAME"
chmod 644 "$SAVE_DIR/$DEST_NAME"

# Temp-Datei aufraeumen
rm -f "$SRC"

echo "OK"
