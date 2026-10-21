#!/usr/bin/env python3
"""Einmalige Maki-Uebergabe. Ohne --ausfuehren nur lokale Vorschau.

Ausfuehrung ausschliesslich nach Marcos Freigabe und bestandenem Verify-Test.
Keine automatische Rollenverschiebung, kein Neustart, kein Token-Fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GUILD = "1204540871005966357"
MAKI = "563434444321587202"
MARSHAL = "1474545328286863432"
LAB_HUB = "1209270674766569472"
LAB_CATEGORY = "1209270640050184253"
CONFIG = Path("/home/botuser/Discord_Bots/data/admin/temp_voice_config.json")
ENV_FILE = Path("/home/botuser/Discord_Bots/config/.env")
API = "https://discord.com/api/v10"


class Stop(Exception):
    """Bereinigte Fehlermeldung ohne HTTP-Inhalte oder Credentials."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_export(path: Path, expected_hash: str | None, execute: bool) -> dict:
    raw = path.read_bytes()
    if expected_hash and digest(raw) != expected_hash:
        raise Stop("Export-Hash weicht ab; neue Vorschau erforderlich.")
    if execute and (not expected_hash or not 0 <= time.time() - path.stat().st_mtime <= 900):
        raise Stop("Ausfuehrung braucht einen Export unter 15 Minuten und seinen SHA256.")
    data = json.loads(raw)
    if data.get("server", {}).get("id") != GUILD:
        raise Stop("Falscher Server im Export.")
    for key in ("bots", "rollen", "kanaele"):
        if not isinstance(data.get(key), list) or not data[key]:
            raise Stop("Unvollstaendiger Export.")
    return data


def proposed_hub(data: dict) -> dict:
    channels = {str(c["id"]): c for c in data["kanaele"]}
    hub, category = channels.get(LAB_HUB), channels.get(LAB_CATEGORY)
    if not hub or not category or hub.get("typ") != "voice" or category.get("typ") != "category":
        raise Stop("Laberecke-Ziele fehlen oder haben den falschen Typ.")
    if hub.get("name") != "〣│➕・Laberecke" or "LABERECKE" not in category.get("name", ""):
        raise Stop("Laberecke-Zielnamen passen nicht zur Vorschau.")
    if hub.get("kategorie") != category["name"]:
        raise Stop("Laberecke-Hub liegt in einer anderen Kategorie.")
    return {"hub_id": int(LAB_HUB), "category_id": int(LAB_CATEGORY),
            "naming": "〣│🗣️・{user}", "default_limit": 0,
            "default_private": False, "game": ""}


def merge_config(config: dict, hub: dict) -> tuple[dict, bool]:
    hubs = config.get("hubs")
    if not isinstance(hubs, list):
        raise Stop("hubs ist keine Liste; keine automatische Migration.")
    matches = [v for v in hubs if str(v.get("hub_id")) == LAB_HUB]
    if len(matches) > 1 or (matches and matches[0] != hub):
        raise Stop("Laberecke ist bereits abweichend oder mehrfach konfiguriert.")
    if matches:
        return config, False
    return {**config, "hubs": [*hubs, hub]}, True


def preview(data: dict, config_path: Path) -> dict:
    roles = {r["name"]: r for r in data["rollen"]}
    bots = {str(b["id"]): b for b in data["bots"]}
    positions = {}
    for uid, name in ((MAKI, "Maki"), (MARSHAL, "Marshal")):
        bot = bots.get(uid)
        if bot is None:
            positions[name] = None
        else:
            if any(r not in roles for r in bot["rollen"]):
                raise Stop("Bot-Rolle fehlt im Export.")
            positions[name] = max([0] + [roles[r]["position"] for r in bot["rollen"]])
    hub = proposed_hub(data)
    raw = config_path.read_bytes()
    config = json.loads(raw)
    _, changed = merge_config(config, hub)
    return {"modus": "Vorschau; keine API-Aufrufe", "maki_bot_id": MAKI,
            "hierarchie": positions,
            "kick_blockiert": positions["Maki"] is not None and
            (positions["Marshal"] is None or positions["Maki"] >= positions["Marshal"]),
            "wegfall": ["Maki-✅-Reaktionsrolle", "Maki-Laberecke-Join-to-Create",
                        "Maki-Leveling", "sonstige Maki-Dashboard-Funktionen (nicht im Export enthalten)"],
            "bestehende_member_rollen_bleiben": True,
            "config_sha256": digest(raw), "laberecke_hub": hub,
            "config_aenderung_noetig": changed}


class DiscordAPI:
    def __init__(self, token: str) -> None:
        self.token = token

    def request(self, method: str, path: str, *, missing_ok: bool = False):
        headers = {"Authorization": "Bot " + self.token,
                   "User-Agent": "DiscordBot (maki-uebergabe, 1.0)"}
        if method == "DELETE":
            headers["X-Audit-Log-Reason"] = urllib.parse.quote(
                "Owner-Freigabe: Maki durch Marshal ersetzen; Verifizierung getestet", safe="")
        req = urllib.request.Request(API + path, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return None
            raise Stop(f"Discord {method}: HTTP {exc.code}; kein automatischer Schreib-Retry.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Stop(f"Discord {method}: Verbindung unklar; vor Wiederholung lesend pruefen.") from None


def authenticate() -> DiscordAPI:
    from dotenv import dotenv_values
    # Marshal-Token; ADMIN_BOT_TOKEN ist der Name vor der Umbenennung (21.10.)
    werte = dotenv_values(ENV_FILE)
    token = werte.get("MARSHAL_BOT_TOKEN") or werte.get("ADMIN_BOT_TOKEN")
    if not token:
        raise Stop("MARSHAL_BOT_TOKEN fehlt; kein Token-Fallback.")
    api = DiscordAPI(token)
    own = api.request("GET", "/users/@me")
    if own.get("id") != MARSHAL or not own.get("bot"):
        raise Stop("Token gehoert nicht zu Marshal.")
    return api


def kick(api: DiscordAPI) -> str:
    target = api.request("GET", f"/guilds/{GUILD}/members/{MAKI}", missing_ok=True)
    if target is None:
        return "Maki bereits abwesend; kein DELETE."
    if target.get("user", {}).get("id") != MAKI or not target["user"].get("bot"):
        raise Stop("Ziel ist nicht der erwartete Maki-Bot.")
    own = api.request("GET", f"/guilds/{GUILD}/members/{MARSHAL}")
    roles = api.request("GET", f"/guilds/{GUILD}/roles")
    if not isinstance(roles, list):
        raise Stop("Rollen nicht lesbar.")
    by_id = {r["id"]: r for r in roles}
    def highest(member: dict) -> int:
        if any(r not in by_id for r in member.get("roles", [])):
            raise Stop("Unbekannte Rolle; Hierarchie nicht sicher pruefbar.")
        return max([0] + [by_id[r]["position"] for r in member.get("roles", [])])
    if highest(target) >= highest(own):
        raise Stop("Maki steht nicht unter Marshal; Owner muss zuerst die Hierarchie aendern.")
    permissions = int(by_id[GUILD]["permissions"])
    for role in own.get("roles", []):
        permissions |= int(by_id[role]["permissions"])
    if not permissions & ((1 << 1) | (1 << 3)):
        raise Stop("Marshal fehlt KICK_MEMBERS.")
    api.request("DELETE", f"/guilds/{GUILD}/members/{MAKI}")
    if api.request("GET", f"/guilds/{GUILD}/members/{MAKI}", missing_ok=True) is not None:
        raise Stop("DELETE nicht als Abwesenheit bestaetigt; nicht blind wiederholen.")
    return "Maki entfernt; Abwesenheit per GET bestaetigt."


def add_laberecke(api: DiscordAPI, data: dict, expected_hash: str,
                 config_path: Path = CONFIG) -> str:
    if api.request("GET", f"/guilds/{GUILD}/members/{MAKI}", missing_ok=True) is not None:
        raise Stop("Maki noch anwesend; kein zweiter Join-to-Create-Anbieter.")
    for uid, typ in ((LAB_HUB, 2), (LAB_CATEGORY, 4)):
        channel = api.request("GET", f"/channels/{uid}")
        if channel.get("id") != uid or channel.get("guild_id") != GUILD or channel.get("type") != typ:
            raise Stop("Live-Kanal passt nicht zum Export.")
        if uid == LAB_HUB and channel.get("parent_id") != LAB_CATEGORY:
            raise Stop("Live-Laberecke liegt in anderer Kategorie.")
    raw = config_path.read_bytes()
    if digest(raw) != expected_hash:
        raise Stop("Live-Konfiguration hat sich seit der Vorschau geaendert.")
    updated, changed = merge_config(json.loads(raw), proposed_hub(data))
    if not changed:
        return "Laberecke bereits identisch konfiguriert; nichts geschrieben."
    backup = config_path.with_name(config_path.name + ".bak." +
                                  datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    with backup.open("xb") as stream:
        os.fchmod(stream.fileno(), config_path.stat().st_mode & 0o777)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    shutil.copystat(config_path, backup)
    fd, temporary = tempfile.mkstemp(prefix=config_path.name + ".codex-", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), config_path.stat().st_mode & 0o777)
            json.dump(updated, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if config_path.read_bytes() != raw:
            raise Stop("Konfiguration parallel geaendert; kein Ueberschreiben.")
        os.replace(temporary, config_path)
        if json.loads(config_path.read_text()) != updated:
            raise Stop("Konfigurations-Ruecklesung fehlgeschlagen.")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return "Laberecke atomar ergaenzt und zurueckgelesen; Backup: " + str(backup)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--export-sha256")
    parser.add_argument("--config-sha256")
    parser.add_argument("--aktion", choices=("kick", "laberecke"), default="kick")
    parser.add_argument("--ausfuehren", action="store_true")
    args = parser.parse_args()
    try:
        data = load_export(args.export, args.export_sha256, args.ausfuehren)
        if not args.ausfuehren:
            print(json.dumps(preview(data, CONFIG), ensure_ascii=False, indent=2))
            return 0
        if args.aktion == "laberecke" and not args.config_sha256:
            raise Stop("Laberecke braucht den Konfigurations-Hash aus der Vorschau.")
        api = authenticate()
        result = kick(api) if args.aktion == "kick" else add_laberecke(api, data, args.config_sha256)
        print(json.dumps({"ergebnis": result, "zeit_utc": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False))
        return 0
    except (Stop, ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"abgebrochen": str(exc) if isinstance(exc, Stop) else type(exc).__name__}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
