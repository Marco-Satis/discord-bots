"""
Erkennt Pakete, die Ubuntu per "Phased Update" (gestaffelte Auslieferung) zurueckhaelt.

Hintergrund (2026-09-30): Ubuntu verteilt manche Updates schrittweise an einen
wachsenden Prozentsatz der Rechner. Solange dieser Rechner nicht dran ist, haelt
apt das Paket zurueck — auch bei full-upgrade. 'apt list --upgradable' listet es
trotzdem, und so meldeten recon-bot und Dashboard ein Update, das kein Knopf
installieren konnte (Beispiel: dnsmasq-base 2.91, "phased 0%").

Nur-Lese-Aufrufe (apt-get -s, apt-cache policy), kein sudo noetig. Fehler liefern
ein leeres Ergebnis — die Erkennung darf den Update-Check nie blockieren.
"""

import asyncio
import re
from typing import Dict, List

from utils.logger import get_logger

logger = get_logger("apt_phased")

_ENV = {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}

# apt ignoriert die Staffelung, wenn es sich fuer einen Chroot haelt (fragt
# /usr/bin/ischroot). Die systemd-Sandbox der Bots und des Dashboards (eigener
# Mount-Namespace) sieht fuer ischroot genau so aus — dort zeigte apt-get -s
# dnsmasq-base als installierbar, waehrend der echte Lauf (systemd-run, Root-
# Namespace) es zurueckhielt. Diese Option erzwingt "kein Chroot", damit die
# Simulation rechnet wie der echte Lauf. Geprueft 2026-09-30 mit /bin/true vs.
# /bin/false: nur mit /bin/false erscheint dnsmasq-base unter "kept back".
APT_NO_CHROOT = ("-o", "Dir::Bin::ischroot=/bin/false")
_PHASED_RE = re.compile(r"\(phased (\d+)%\)")
_KEPT_BACK_HEADER = "The following packages have been kept back:"


async def _run(*args: str) -> str:
    """Fuehrt einen Nur-Lese-apt-Befehl aus und liefert stdout ('' bei Fehler)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_ENV,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"{' '.join(args[:3])} Exit-Code {proc.returncode}: {err[:300]}")
        return stdout.decode("utf-8", errors="replace")
    except (asyncio.TimeoutError, OSError) as e:
        logger.debug(f"{args[0]} fehlgeschlagen: {e}")
        return ""


def parse_kept_back(sim_output: str) -> List[str]:
    """Liest die "kept back"-Liste aus `apt-get -s dist-upgrade`."""
    names: List[str] = []
    in_block = False
    for line in sim_output.splitlines():
        if line.startswith(_KEPT_BACK_HEADER):
            in_block = True
            continue
        if in_block:
            if not line.startswith(" "):
                break
            names.extend(line.split())
    return names


def parse_phased_policy(policy_output: str) -> Dict[str, int]:
    """Liest aus `apt-cache policy <pkgs>` den Freigabe-Prozentsatz des Kandidaten.

    Returns: {paketname: prozent} nur fuer Pakete, deren Kandidat gestaffelt ist.
    """
    result: Dict[str, int] = {}
    name = ""
    candidate = ""
    for line in policy_output.splitlines():
        if line and not line[0].isspace() and line.endswith(":"):
            # "libssl3:i386:" -> "libssl3" — apt list fuehrt den Namen ohne Arch
            name = line[:-1].split(":", 1)[0]
            candidate = ""
            continue
        stripped = line.strip()
        if stripped.startswith("Candidate:"):
            candidate = stripped.split(":", 1)[1].strip()
            continue
        if name and candidate and stripped.lstrip("* ").startswith(candidate + " "):
            m = _PHASED_RE.search(stripped)
            if m:
                result[name] = int(m.group(1))
    return result


async def held_by_phasing() -> Dict[str, int]:
    """Pakete, die apt auf diesem Rechner wegen gestaffelter Auslieferung zurueckhaelt.

    Returns: {paketname: freigabe_prozent}. Leer, wenn nichts zurueckgehalten
    wird oder die Abfrage scheitert.
    """
    kept = parse_kept_back(await _run("apt-get", *APT_NO_CHROOT, "-s", "dist-upgrade"))
    if not kept:
        return {}
    return parse_phased_policy(await _run("apt-cache", *APT_NO_CHROOT, "policy", *kept))
