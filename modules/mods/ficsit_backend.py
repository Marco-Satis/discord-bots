"""
Satisfactory-Mod-Backend auf Basis des OFFIZIELLEN ficsit-cli.

Kein Eigenbau-Mod-Management: alle Downloads, Abhaengigkeits-Aufloesung und
die SML-Installation macht `ficsit` (github.com/satisfactorymodding/ficsit-cli).
Dieses Modul ruft das CLI nur auf und uebersetzt Ein-/Ausgaben.

Architektur
-----------
* ficsit liegt unter /home/satisfactory/bin/ficsit und laeuft IMMER als User
  `satisfactory` (via sudo -u). Grund: die Config liegt unter
  ~/.local/share/ficsit des ausfuehrenden Users, und die Mod-Dateien muessen
  dem Server-User gehoeren. Liefe es als botuser, haetten wir zwei getrennte
  Zustaende und falsche Dateirechte.
* Mods werden ueber `profiles.json` verwaltet, danach `ficsit apply`.
  ficsit-cli v0.7.1 hat KEINEN Befehl zum Hinzufuegen von Mods zu einem Profil
  (`ficsit profile mods <p>` listet nur) — der scriptbare Weg ist das Profil.
  Getestet: handgeschriebene profiles.json wird anstandslos gelesen.

WARNUNG --dry-run
-----------------
`ficsit apply --dry-run` ueberspringt NUR das Speichern der Config-Dateien.
Mods werden trotzdem heruntergeladen UND entpackt. Die Option ist als
Sicherheitsnetz unbrauchbar und wird hier bewusst nirgends verwendet.
(Verifiziert am 2026-08-11 auf dem Live-Server.)
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import get_logger

logger = get_logger("mods.ficsit")

FICSIT_BIN = "/home/satisfactory/bin/ficsit"
FICSIT_USER = "satisfactory"
FICSIT_HOME = Path("/home/satisfactory")
PROFILES_JSON = FICSIT_HOME / ".local/share/ficsit/profiles.json"
INSTALLATIONS_JSON = FICSIT_HOME / ".local/share/ficsit/installations.json"

# Server-Installationspfad (ficsit erkennt ihn an FactoryServer.sh)
SERVER_PATH = "/home/satisfactory/SatisfactoryDedicatedServer"

# ficsit gibt ANSI-Farbcodes aus, auch ohne TTY
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Mod-Referenzen sind alphanumerisch + Unterstrich (ficsit "mod_reference").
# Wird vor jeder Verwendung geprueft — die Werte landen in profiles.json.
_MOD_REF_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def is_valid_mod_ref(mod_ref: str) -> bool:
    """Prueft eine Mod-Referenz gegen ein enges Muster (kein Injection-Vektor)."""
    return bool(_MOD_REF_RE.match(mod_ref or ""))


async def _run_ficsit(*args: str, timeout: float = 300.0) -> Tuple[int, str, str]:
    """Fuehrt `sudo -n -u satisfactory ficsit <args>` aus.

    Returns: (returncode, stdout, stderr) — beide Streams ANSI-bereinigt.
    """
    cmd = ["sudo", "-n", "-u", FICSIT_USER, FICSIT_BIN, *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            _strip_ansi(out.decode("utf-8", errors="replace")),
            _strip_ansi(err.decode("utf-8", errors="replace")),
        )
    except asyncio.TimeoutError:
        logger.warning(f"ficsit {' '.join(args)}: Timeout nach {timeout}s")
        return -1, "", f"Timeout nach {timeout:.0f}s"
    except OSError as e:
        logger.warning(f"ficsit {' '.join(args)} nicht ausfuehrbar: {e}")
        return -1, "", str(e)


async def is_available() -> Tuple[bool, str]:
    """Prueft, ob ficsit-cli aufrufbar ist (inkl. sudo-Rechte)."""
    if not Path(FICSIT_BIN).exists():
        return False, f"ficsit-cli nicht installiert ({FICSIT_BIN})"
    rc, out, err = await _run_ficsit("version", timeout=20.0)
    if rc != 0:
        hint = ""
        if "password is required" in err or "not allowed" in err:
            hint = (
                " — sudoers-Regel fehlt: "
                "'botuser ALL=(satisfactory) NOPASSWD: /home/satisfactory/bin/ficsit *'"
            )
        return False, f"ficsit nicht aufrufbar{hint}: {err.strip()[:200]}"
    # `ficsit version` schreibt die Version auf stderr, nicht auf stdout
    # (verifiziert mit v0.7.1) — deshalb beide Streams auswerten.
    banner = (out.strip() or err.strip()).splitlines()
    return True, f"ficsit-cli {banner[0]}" if banner else "ficsit-cli einsatzbereit"


# ----------------------------------------------------------------------
# Profil-Verwaltung (profiles.json)
# ----------------------------------------------------------------------


async def _read_json_as_satisfactory(path: Path) -> Optional[dict]:
    """Liest eine JSON-Datei im Kontext des satisfactory-Users.

    Umweg ueber `ficsit`-fremdes cat waere ein Shell-Read; stattdessen liest
    der Bot direkt — die Dateien sind fuer Gruppe satisfactory lesbar und
    botuser ist Mitglied.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"{path.name} nicht lesbar: {e}")
        return None


async def get_profile(profile: str = "Default") -> Dict[str, Any]:
    """Liefert das Profil als Dict mit seinen Mods.

    Returns: {"name": str, "mods": {mod_ref: {"version": str, "enabled": bool}}}
    """
    data = await _read_json_as_satisfactory(PROFILES_JSON)
    if not data:
        return {"name": profile, "mods": {}}
    prof = (data.get("profiles") or {}).get(profile) or {}
    mods = prof.get("mods") or {}
    # ficsit erlaubt sowohl {"version": ..} als auch reine Version-Strings
    normalised: Dict[str, Any] = {}
    for ref, val in mods.items():
        if isinstance(val, dict):
            normalised[ref] = {
                "version": val.get("version", ">=0.0.0"),
                "enabled": bool(val.get("enabled", True)),
            }
        else:
            normalised[ref] = {"version": str(val), "enabled": True}
    return {"name": profile, "mods": normalised}


async def _write_profile(profile: str, mods: Dict[str, Any]) -> Tuple[bool, str]:
    """Schreibt das Profil zurueck — via ficsit-User, mit Backup.

    Die Datei gehoert satisfactory; botuser darf sie nicht direkt schreiben.
    Deshalb Umweg ueber `sudo -u satisfactory tee`.
    """
    data = await _read_json_as_satisfactory(PROFILES_JSON) or {
        "profiles": {}, "selected_profile": profile, "version": 0,
    }
    data.setdefault("profiles", {})
    existing = data["profiles"].get(profile) or {}
    data["profiles"][profile] = {
        "mods": mods,
        "name": profile,
        # required_targets steuert, fuer welche Plattform aufgeloest wird.
        # LinuxServer ist fuer den Dedicated Server zwingend.
        "required_targets": existing.get("required_targets") or ["LinuxServer"],
    }
    data.setdefault("selected_profile", profile)

    payload = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "-u", FICSIT_USER,
            "tee", str(PROFILES_JSON),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _o, err = await asyncio.wait_for(
            proc.communicate(payload.encode("utf-8")), timeout=30.0
        )
        if proc.returncode != 0:
            return False, f"Profil schreiben fehlgeschlagen: {err.decode()[:200]}"
        return True, "Profil gespeichert"
    except (asyncio.TimeoutError, OSError) as e:
        return False, f"Profil schreiben fehlgeschlagen: {e}"


# ----------------------------------------------------------------------
# Oeffentliche API
# ----------------------------------------------------------------------


async def list_mods(profile: str = "Default") -> List[Dict[str, Any]]:
    """Listet die Mods des Profils inkl. Aktiv-Status."""
    prof = await get_profile(profile)
    result: List[Dict[str, Any]] = []
    for ref, meta in sorted(prof["mods"].items()):
        result.append({
            "mod_id": ref,
            "name": ref,
            "version": meta.get("version", ">=0.0.0"),
            "enabled": meta.get("enabled", True),
            "game": "satisfactory",
        })
    return result


async def search_mods(query: str, limit: int = 15) -> List[Dict[str, Any]]:
    """Sucht Mods ueber `ficsit search` (offizielle ficsit.app-API)."""
    if not query or not query.strip():
        return []
    rc, out, err = await _run_ficsit("search", query.strip(), timeout=45.0)
    if rc != 0:
        logger.warning(f"ficsit search fehlgeschlagen: {err.strip()[:200]}")
        return []

    results: List[Dict[str, Any]] = []
    # Format: "Anzeigename (ModReference)"
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(?P<name>.+?)\s+\((?P<ref>[^()]+)\)$", line)
        if not m:
            continue
        results.append({
            "mod_id": m.group("ref").strip(),
            "name": m.group("name").strip(),
            "game": "satisfactory",
        })
        if len(results) >= limit:
            break
    return results


async def add_mod(
    mod_ref: str, version: str = ">=0.0.0", profile: str = "Default"
) -> Tuple[bool, str]:
    """Nimmt eine Mod ins Profil auf. Wirksam erst nach apply()."""
    if not is_valid_mod_ref(mod_ref):
        return False, f"Ungueltige Mod-Referenz: {mod_ref[:60]}"

    prof = await get_profile(profile)
    if mod_ref in prof["mods"]:
        return False, f"`{mod_ref}` ist bereits im Profil"

    prof["mods"][mod_ref] = {"version": version, "enabled": True}
    ok, msg = await _write_profile(profile, prof["mods"])
    if not ok:
        return False, msg
    return True, f"`{mod_ref}` zum Profil hinzugefuegt — apply noetig"


async def remove_mod(mod_ref: str, profile: str = "Default") -> Tuple[bool, str]:
    """Entfernt eine Mod aus dem Profil. Wirksam erst nach apply()."""
    if not is_valid_mod_ref(mod_ref):
        return False, f"Ungueltige Mod-Referenz: {mod_ref[:60]}"

    prof = await get_profile(profile)
    if mod_ref not in prof["mods"]:
        return False, f"`{mod_ref}` ist nicht im Profil"

    del prof["mods"][mod_ref]
    ok, msg = await _write_profile(profile, prof["mods"])
    if not ok:
        return False, msg
    return True, f"`{mod_ref}` aus Profil entfernt — apply noetig"


async def set_enabled(
    mod_ref: str, enabled: bool, profile: str = "Default"
) -> Tuple[bool, str]:
    """Schaltet eine Mod im Profil ein/aus. Wirksam erst nach apply()."""
    if not is_valid_mod_ref(mod_ref):
        return False, f"Ungueltige Mod-Referenz: {mod_ref[:60]}"

    prof = await get_profile(profile)
    if mod_ref not in prof["mods"]:
        return False, f"`{mod_ref}` ist nicht im Profil"

    prof["mods"][mod_ref]["enabled"] = bool(enabled)
    ok, msg = await _write_profile(profile, prof["mods"])
    if not ok:
        return False, msg
    return True, f"`{mod_ref}` {'aktiviert' if enabled else 'deaktiviert'} — apply noetig"


async def apply(server_running: bool = False) -> Tuple[bool, str]:
    """Wendet das Profil auf die Server-Installation an (Download/Entfernen).

    Args:
        server_running: Wenn True, wird abgebrochen. Die offizielle Doku
            verlangt einen gestoppten Server; Mods werden ohnehin erst beim
            Start geladen, und Schreiben ins laufende Verzeichnis ist riskant.
    """
    if server_running:
        return False, (
            "Server laeuft — vor dem Anwenden stoppen. "
            "(Offizielle Vorgabe: 'make sure the server is not currently running')"
        )

    rc, out, err = await _run_ficsit("apply", SERVER_PATH, timeout=900.0)
    combined = f"{out}\n{err}"

    if rc != 0:
        logger.error(f"ficsit apply fehlgeschlagen (rc={rc}): {err.strip()[:400]}")
        return False, f"apply fehlgeschlagen: {err.strip()[:300] or 'unbekannter Fehler'}"

    installed = re.findall(r"mod_reference=(\S+)\s+version=(\S+)", combined)
    deleted = re.findall(r"deleting mod\s+mod_reference=(\S+)", combined)
    uniq_inst = sorted({f"{n} {v}" for n, v in installed})
    uniq_del = sorted(set(deleted))

    parts = []
    if uniq_inst:
        parts.append(f"{len(uniq_inst)} installiert/aktualisiert: {', '.join(uniq_inst[:8])}")
    if uniq_del:
        parts.append(f"{len(uniq_del)} entfernt: {', '.join(uniq_del[:8])}")
    if not parts:
        parts.append("keine Aenderung noetig")

    logger.info(f"ficsit apply OK — {'; '.join(parts)}")
    return True, "; ".join(parts)


async def get_game_version() -> Optional[int]:
    """Liest die Changelist-Nummer (CL) der Server-Installation.

    Quelle ist die Zeile `LogInit: Build: ++FactoryGame+rel-main-1.2.0-CL-495413`
    aus dem Server-Log — das ist dieselbe Zahl, die SMM als `gameVersion` fuehrt.
    Die Datei gehoert satisfactory, ist aber gruppenlesbar (botuser ist Mitglied).
    """
    log_path = Path(SERVER_PATH) / "FactoryGame/Saved/Logs/FactoryGame.log"
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            # Die Build-Zeile steht im Kopf des Logs — nicht die ganze Datei lesen.
            for _ in range(400):
                line = fh.readline()
                if not line:
                    break
                m = re.search(r"-CL-(\d+)", line)
                if m:
                    return int(m.group(1))
    except OSError as e:
        logger.debug(f"Game-Version nicht lesbar: {e}")
    return None


async def export_smm_profile(
    profile: str = "Default", export_name: Optional[str] = None
) -> Dict[str, Any]:
    """Baut ein SMM-importierbares Profil (.smmprofile) aus dem Server-Profil.

    Format laut SMM-Quelltext (backend/ficsitcli/profiles.go, ExportedProfile):
    `{"profile": {...}, "lockfile": {...}, "metadata": {"gameVersion": <CL>}}`.
    Die Lockfile bleibt leer — SMM loest beim Import selbst auf und zieht dabei
    die fuer Windows passenden Dateien. `required_targets` steht auf Windows,
    weil das Ziel der Spiel-Client ist, nicht der Linux-Server.
    """
    prof = await get_profile(profile)
    mods = {
        ref: {"version": meta.get("version", ">=0.0.0"),
              "enabled": bool(meta.get("enabled", True))}
        for ref, meta in prof["mods"].items()
    }
    return {
        "profile": {
            "mods": mods,
            "name": export_name or f"{profile}-Server",
            "required_targets": ["Windows"],
        },
        "lockfile": {},
        "metadata": {"gameVersion": await get_game_version() or 0},
    }


async def installed_on_disk() -> List[str]:
    """Liest die tatsaechlich im Serververzeichnis liegenden Mods.

    Quelle der Wahrheit fuer 'was laeuft wirklich' — unabhaengig vom Profil.
    """
    mods_dir = Path(SERVER_PATH) / "FactoryGame" / "Mods"
    try:
        if not mods_dir.is_dir():
            return []
        return sorted(
            p.name for p in mods_dir.iterdir()
            if p.is_dir() and p.name not in ("GameFeatures",)
        )
    except OSError as e:
        logger.debug(f"Mods-Verzeichnis nicht lesbar: {e}")
        return []
