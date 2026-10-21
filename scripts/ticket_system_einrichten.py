#!/usr/bin/env python3
"""
Ticket-System einrichten: Rolle, Kategorie, Kanaele, Konfiguration, Panels.

Ausgangslage (2026-08-16): Das Ticket-System ist seit Februar gebaut und
geladen, aber `data/admin/ticket_config.json` war nie gefuellt —
`support_roles: []`, `ticket_category_id: null`, `log_channel_id: null`. Ein
Ticket waere angelegt worden und haette ausser dem Ersteller niemand gesehen,
denn die Schleife, die Support-Rollen Zugriff gibt, laeuft bei leerer Liste
schlicht nicht. Kein Zugriffsloch, aber funktional tot.

Marcos Vorgaben:
  * eigene oeffentliche Kategorie zum Eroeffnen, zwei Kanaele (Frage / Fehler)
  * die Ticket-Kanaele bleiben in derselben Support-Kategorie
  * Zugriff: Admin-Rolle plus eine neue Support-Rolle
  * Transcript-Log in die Team-Kategorie

Warum Ticket-Kanaele in einer oeffentlichen Kategorie unbedenklich sind:
`handle_ticket_creation` setzt auf jedem Ticket-Kanal ausdrueckliche Rechte —
`@everyone: view_channel=False`, dazu Ersteller, Bot und Support-Rollen.
Kanal-Rechte schlagen Kategorie-Rechte, die Kategorie vererbt hier also nichts
Gefaehrliches.

Aufruf:
    python3 ticket_system_einrichten.py            # zeigt nur, was passieren wuerde
    python3 ticket_system_einrichten.py --machen   # legt an und schreibt

Ohne `--machen` wird nichts veraendert. Das ist Absicht: der Lauf legt Rollen
und Kanaele auf einem Server an, auf dem Menschen unterwegs sind.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENVFILE = "/home/botuser/Discord_Bots/config/.env"
CONFIGFILE = Path("/home/botuser/Discord_Bots/data/admin/ticket_config.json")

# Keine ID im Code (oeffentliches Repo). Die Admin-Rolle steht bereits in
# config/config.json unter `web_allowed_role_ids` — dieselbe Rolle, die das
# Dashboard freigibt. Alternativ per --admin-rolle <ID> uebergeben.
def _admin_rolle() -> str:
    if "--admin-rolle" in sys.argv:
        return sys.argv[sys.argv.index("--admin-rolle") + 1]
    pfad = Path("/home/botuser/Discord_Bots/config/config.json")
    if pfad.is_file():
        rollen = json.loads(pfad.read_text()).get("web_allowed_role_ids") or []
        if rollen:
            return str(rollen[0])
    raise SystemExit("Keine Admin-Rolle gefunden — mit --admin-rolle <ID> angeben.")


ADMIN_ROLLE_ID = _admin_rolle()
SUPPORT_ROLLE_NAME = "〣│🎫・Support"

KATEGORIE_NAME = "─┐🎫│ SUPPORT│🎫└────────"
KANAL_FRAGE = "〣│💬・hilfe-anfragen"
KANAL_FEHLER = "〣│🐞・fehler-melden"
KANAL_LOG = "〣│🎫・ticket-logs"
TEAM_KENNUNG = "TEAM"                        # Teilstring der Team-Kategorie

MACHEN = "--machen" in sys.argv


def env_lesen() -> dict:
    werte = {}
    with open(ENVFILE, encoding="utf-8") as f:
        for zeile in f:
            zeile = zeile.strip()
            if zeile and not zeile.startswith("#") and "=" in zeile:
                k, v = zeile.split("=", 1)
                werte[k.strip()] = v.strip().strip('"').strip("'")
    return werte


ENV = env_lesen()
GUILD = ENV["GUILD_ID"]
TOKEN = ENV.get("MARSHAL_BOT_TOKEN") or ENV["ADMIN_BOT_TOKEN"]  # alter Name bis 21.10.


def api(pfad: str, methode: str = "GET", daten: dict | None = None):
    rumpf = json.dumps(daten).encode() if daten is not None else None
    anfrage = urllib.request.Request(
        f"https://discord.com/api/v10{pfad}",
        data=rumpf,
        method=methode,
        headers={
            "Authorization": f"Bot {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBots-Setup/1.0",
        },
    )
    try:
        with urllib.request.urlopen(anfrage, timeout=25) as antwort:
            text = antwort.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        print(f"    FEHLER {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        raise


def tun(was: str, pfad: str, methode: str, daten: dict):
    """Eine veraendernde Aktion — im Trockenlauf nur angekuendigt."""
    if not MACHEN:
        print(f"    [wuerde] {was}")
        return None
    ergebnis = api(pfad, methode, daten)
    print(f"    [getan]  {was}")
    return ergebnis


def main() -> int:
    print("=" * 66)
    print("Ticket-System einrichten" + ("" if MACHEN else "   (TROCKENLAUF)"))
    print("=" * 66)

    rollen = api(f"/guilds/{GUILD}/roles")
    kanaele = api(f"/guilds/{GUILD}/channels")
    kat = {c["id"]: c for c in kanaele if c["type"] == 4}

    # --- 1. Support-Rolle ---
    print("\n1/5  Support-Rolle")
    support = next((r for r in rollen if r["name"] == SUPPORT_ROLLE_NAME), None)
    if support:
        print(f"    vorhanden: {SUPPORT_ROLLE_NAME} ({support['id']})")
        support_id = support["id"]
    else:
        # Keine Server-Rechte an der Rolle selbst: Zugriff auf Tickets kommt
        # ueber die Kanal-Rechte. Eine Rolle mit Rechten "fuer alle Faelle"
        # ist genau der Weg, auf dem Berechtigungen unbemerkt wachsen.
        neu = tun(f"Rolle '{SUPPORT_ROLLE_NAME}' anlegen",
                  f"/guilds/{GUILD}/roles", "POST",
                  {"name": SUPPORT_ROLLE_NAME, "permissions": "0",
                   "color": 0x1ABC9C, "hoist": True, "mentionable": True,
                   "reason": "Support-Rolle fuer das Ticket-System"})
        support_id = neu["id"] if neu else "<neu>"

    # --- 2. Support-Kategorie ---
    print("\n2/5  Support-Kategorie")
    kategorie = next((c for c in kat.values() if c["name"] == KATEGORIE_NAME), None)
    if kategorie:
        print(f"    vorhanden: {KATEGORIE_NAME} ({kategorie['id']})")
        kategorie_id = kategorie["id"]
    else:
        neu = tun(f"Kategorie '{KATEGORIE_NAME}' anlegen",
                  f"/guilds/{GUILD}/channels", "POST",
                  {"name": KATEGORIE_NAME, "type": 4})
        kategorie_id = neu["id"] if neu else "<neu>"

    # --- 3. Panel-Kanaele ---
    print("\n3/5  Kanaele zum Eroeffnen")
    # Lesen ja, schreiben nein: hier wird geklickt, nicht diskutiert. Sonst
    # entsteht ein zweiter Support-Kanal neben den Tickets.
    nur_lesen = [{"id": GUILD, "type": 0, "allow": "1024", "deny": "2048"}]
    panel_ids = {}
    for art, name in (("allgemein", KANAL_FRAGE), ("bug", KANAL_FEHLER)):
        da = next((c for c in kanaele if c["type"] == 0 and c["name"] == name), None)
        if da:
            print(f"    vorhanden: {name} ({da['id']})")
            panel_ids[art] = da["id"]
        else:
            neu = tun(f"Kanal '{name}' anlegen",
                      f"/guilds/{GUILD}/channels", "POST",
                      {"name": name, "type": 0, "parent_id": kategorie_id,
                       "permission_overwrites": nur_lesen,
                       "topic": ("Hier eroeffnest du ein Ticket. Klick den Knopf — "
                                 "du bekommst einen eigenen Kanal, den nur du und "
                                 "das Team sehen.")})
            panel_ids[art] = neu["id"] if neu else "<neu>"

    # --- 4. Log-Kanal in der Team-Kategorie ---
    print("\n4/5  Transcript-Log (Team-Kategorie)")
    team = next((c for c in kat.values() if TEAM_KENNUNG in c["name"].upper()), None)
    if not team:
        print(f"    Team-Kategorie nicht gefunden (Suche nach '{TEAM_KENNUNG}')")
        log_id = None
    else:
        da = next((c for c in kanaele if c["type"] == 0 and c["name"] == KANAL_LOG), None)
        if da:
            print(f"    vorhanden: {KANAL_LOG} ({da['id']})")
            log_id = da["id"]
        else:
            neu = tun(f"Kanal '{KANAL_LOG}' in '{team['name'][:20]}' anlegen",
                      f"/guilds/{GUILD}/channels", "POST",
                      {"name": KANAL_LOG, "type": 0, "parent_id": team["id"],
                       "topic": "Abschriften geschlossener Tickets. Wird vom Bot geschrieben."})
            log_id = neu["id"] if neu else None

    # --- 5. Konfiguration + Panels ---
    print("\n5/5  Konfiguration und Panels")
    konfig = {
        "support_roles": [int(ADMIN_ROLLE_ID), int(support_id)] if MACHEN else
                         [ADMIN_ROLLE_ID, support_id],
        "ticket_category_id": int(kategorie_id) if MACHEN else kategorie_id,
        "log_channel_id": int(log_id) if (MACHEN and log_id) else log_id,
    }
    print(f"    ticket_config.json -> {json.dumps(konfig, ensure_ascii=False)}")
    if MACHEN:
        CONFIGFILE.write_text(json.dumps(konfig, indent=2), encoding="utf-8")
        print(f"    [getan]  {CONFIGFILE} geschrieben")

    texte = {
        "allgemein": ("💬 Frage oder Anliegen",
                      "Du hast eine Frage zum Spiel-Server oder zum Discord?\n\n"
                      "Klick unten und schreib kurz, worum es geht. Du bekommst einen "
                      "eigenen Kanal, in dem nur du und das Team lesen — dort meldet "
                      "sich jemand, sobald er kann.\n\n"
                      "**Ein Ticket je Anliegen**, dann geht nichts unter.",
                      "Frage / Anliegen", 1, "💬"),
        "bug": ("🐞 Fehler melden",
                "Etwas funktioniert nicht wie erwartet?\n\n"
                "Klick unten und beschreib, **was du gemacht hast, was passiert ist "
                "und was du erwartet haettest**. Je genauer, desto eher laesst sich "
                "der Fehler nachstellen — und was sich nicht nachstellen laesst, "
                "laesst sich meist auch nicht beheben.\n\n"
                "Du bekommst einen eigenen Kanal, in dem nur du und das Team lesen.",
                "Fehler melden", 4, "🐞"),
    }
    for art, kanal_id in panel_ids.items():
        titel, text, knopf, stil, emoji = texte[art]
        tun(f"Panel '{titel}' posten",
            f"/channels/{kanal_id}/messages", "POST",
            {"embeds": [{"title": titel, "description": text, "color": 0x5865F2,
                         "footer": {"text": "Support-Ticket-System"}}],
             "components": [{"type": 1, "components": [
                 {"type": 2, "style": stil, "label": knopf,
                  "emoji": {"name": emoji},
                  "custom_id": f"ticket_system:create:{art}"}]}]})

    print("\n" + "=" * 66)
    if MACHEN:
        print("Fertig. Danach noch: sudo systemctl restart marshal-bot")
        print("(der Bot liest ticket_config.json beim Start)")
    else:
        print("Trockenlauf — nichts veraendert. Mit --machen ausfuehren.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
