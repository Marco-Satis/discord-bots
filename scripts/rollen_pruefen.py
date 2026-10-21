#!/usr/bin/env python3
"""
Rollen-IDs gegen den echten Discord-Server pruefen.

Zweck: bevor der Dashboard-Zugang auf Rollen-IDs verengt wird, muss belegt
sein, dass es diese Rollen gibt. Eine vertippte ID faellt sonst erst auf,
wenn sich jemand nicht mehr anmelden kann.

Gibt ausschliesslich Rollen-ID und Rollenname aus. Das Token wird gelesen,
aber nie ausgegeben, nie geloggt und steht nicht in der Prozessliste.
"""

import json
import os
import sys
import urllib.request

ENV = "/home/botuser/Discord_Bots/config/.env"
# Keine IDs im Code: dieses Repo ist oeffentlich. Die zu pruefenden IDs
# kommen als Argumente.
#     python3 rollen_pruefen.py <ID> [<ID> ...]
ZU_PRUEFEN = {k: "angefragt" for k in sys.argv[1:]}


def env_lesen(pfad):
    werte = {}
    with open(pfad, encoding="utf-8") as f:
        for zeile in f:
            zeile = zeile.strip()
            if not zeile or zeile.startswith("#") or "=" not in zeile:
                continue
            k, v = zeile.split("=", 1)
            werte[k.strip()] = v.strip().strip('"').strip("'")
    return werte


def hole(pfad, token):
    anfrage = urllib.request.Request(
        f"https://discord.com/api/v10{pfad}",
        headers={"Authorization": f"Bot {token}",
                 "User-Agent": "DiscordBots-Audit/1.0"},
    )
    with urllib.request.urlopen(anfrage, timeout=20) as antwort:
        return json.loads(antwort.read().decode())


def main():
    # Frueh und mit klarer Ansage abbrechen: ohne IDs hat der Lauf nichts zu
    # tun, und ein Traceback beim Token-Lesen erklaert das niemandem.
    if not ZU_PRUEFEN:
        print("Aufruf: rollen_pruefen.py <ID> [<ID> ...]\n"
              "        Prueft, ob die angegebenen IDs auf dem Server als Rolle\n"
              "        existieren, und listet alle Rollen mit Administrator-Recht.\n"
              "        Muss als botuser laufen (liest config/.env).", file=sys.stderr)
        return 2

    env = env_lesen(ENV)
    guild = env.get("GUILD_ID")
    if not guild:
        print("GUILD_ID fehlt", file=sys.stderr)
        return 1

    # Marshal ist der Bot mit dem members-Intent — er sieht Rollen am
    # zuverlaessigsten. Faellt sein Token aus, tut es jedes andere.
    token = None
    for name in ("MARSHAL_BOT_TOKEN", "ADMIN_BOT_TOKEN", "RECON_BOT_TOKEN",
                 "DISCORD_TOKEN_WATCHDOG", "OPERATOR_BOT_TOKEN", "DISCORD_TOKEN_MANAGER"):
        if env.get(name):
            token = env[name]
            quelle = name
            break
    if not token:
        print("kein Bot-Token in der .env gefunden", file=sys.stderr)
        return 1
    print(f"Token-Quelle: {quelle} (Wert nicht angezeigt)")

    rollen = hole(f"/guilds/{guild}/roles", token)
    nach_id = {r["id"]: r for r in rollen}

    print(f"\nServer hat {len(rollen)} Rollen.\n")
    print("=== Die drei angegebenen IDs ===")
    for kennung, was in ZU_PRUEFEN.items():
        if kennung in nach_id:
            r = nach_id[kennung]
            rechte = int(r.get("permissions", 0))
            admin = " [Administrator-Recht]" if rechte & 0x8 else ""
            print(f"  ROLLE   {kennung}  '{r['name']}'{admin}   ({was})")
        else:
            print(f"  KEINE ROLLE  {kennung}   ({was})")

    print("\n=== Rollen mit Administrator-Recht (zur Gegenprobe) ===")
    for r in rollen:
        if int(r.get("permissions", 0)) & 0x8:
            print(f"  {r['id']}  {r['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
