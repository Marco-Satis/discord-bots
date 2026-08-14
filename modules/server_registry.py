"""
Die eine Liste der Spielserver.

Bis zum 2026-08-14 stand „welche Server gibt es" an dreizehn Stellen: drei
Python-Listen, drei Slash-Command-Auswahllisten, drei Anzeigetabellen, zwei
Port-Zuordnungen, eine Dienstliste der Systemseite und zwei Navigationsleisten.
Einen Server stillzulegen hiess deshalb, dreizehn Stellen zu finden — und wer
eine uebersah, bekam einen Discord-Befehl, der einen Server anbietet, den es
nicht mehr gibt.

Hier steht es einmal, gelesen aus zwei ENV-Variablen:

    MC_SERVER_IDS=BMC             # Minecraft
    SAT_SERVER_IDS=MAIN           # Satisfactory

Ein Server gilt als vorhanden, wenn seine ID in der Liste steht **und** ein
Service-Name konfiguriert ist. Eine ID ohne Service ist ein halber Ruckweg
(etwa ein Tippfehler) und erzeugt bewusst keine tote Kachel.

Dieses Modul kennt weder discord.py noch den Bot-Zustand — es liest nur die
Umgebung. Damit koennen Cogs, Monitoring und die Web-Routen dieselbe Quelle
benutzen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from utils.config import get_env, server_ids

# Reihenfolge zaehlt: der erste vorhandene Server eines Spiels ist der
# Vorgabe-Server fuer Befehle ohne Server-Angabe.
MC_DEFAULT = "BMC"
SAT_DEFAULT = "MAIN"


@dataclass(frozen=True)
class Spielserver:
    """Ein Spielserver, so wie ihn Befehle, Dashboard und Monitoring sehen."""

    kennung: str        # Marco-seitig, auch in URLs und Dateinamen: "satisfactory", "mc_bmc"
    spiel: str          # "satisfactory" oder "minecraft"
    server_id: str      # ENV-Praefix-ID: "MAIN", "BMC"
    service: str        # systemd-Unit
    label: str          # Anzeigename in Discord und Dashboard

    @property
    def env_praefix(self) -> str:
        """Praefix der ENV-Variablen dieses Servers, z.B. ``MC_BMC_``."""
        return f"{'MC' if self.spiel == 'minecraft' else 'SAT'}_{self.server_id}_"

    @property
    def kurz_id(self) -> str:
        """
        Kurzform, wie das Timeout-System sie in der Datenbank fuehrt:
        ``sat``, ``bmc``. Absichtlich eine eigene Form — gespeicherte
        Timeout-Eintraege benutzen sie seit jeher, und die sollen lesbar
        bleiben.
        """
        if self.spiel == "minecraft":
            return self.server_id.lower()
        return "sat" if self.server_id == SAT_DEFAULT else f"sat_{self.server_id.lower()}"


def _mc_kennung(server_id: str) -> str:
    return f"mc_{server_id.lower()}"


def _sat_kennung(server_id: str) -> str:
    # Der erste Satisfactory-Server behaelt die Kennung "satisfactory": daran
    # haengen die Statusdatei, die Dashboard-URL und die RBAC-Ressource.
    return "satisfactory" if server_id == SAT_DEFAULT else f"sat_{server_id.lower()}"


def minecraft_server() -> List[Spielserver]:
    """Alle konfigurierten Minecraft-Server, in ENV-Reihenfolge."""
    gefunden = []
    for sid in server_ids("MC_SERVER_IDS", MC_DEFAULT):
        service = get_env(f"MC_{sid}_SERVICE", "")
        if not service:
            continue
        gefunden.append(Spielserver(
            kennung=_mc_kennung(sid),
            spiel="minecraft",
            server_id=sid,
            service=service,
            label=get_env(f"MC_{sid}_DISPLAY_NAME", f"Minecraft {sid.title()}"),
        ))
    return gefunden


def satisfactory_server() -> List[Spielserver]:
    """Alle konfigurierten Satisfactory-Server, in ENV-Reihenfolge."""
    gefunden = []
    for sid in server_ids("SAT_SERVER_IDS", SAT_DEFAULT):
        # Der erste Server darf die alten Variablennamen ohne ID benutzen —
        # so aendert sich fuer den laufenden Server nichts.
        service = get_env(f"SAT_{sid}_SERVICE", "")
        if not service and sid == SAT_DEFAULT:
            service = get_env("SATISFACTORY_SERVICE", "satisfactory.service")
        if not service:
            continue
        label = get_env(f"SAT_{sid}_DISPLAY_NAME", "")
        if not label:
            label = "Satisfactory" if sid == SAT_DEFAULT else f"Satisfactory {sid.title()}"
        gefunden.append(Spielserver(
            kennung=_sat_kennung(sid),
            spiel="satisfactory",
            server_id=sid,
            service=service,
            label=label,
        ))
    return gefunden


def alle() -> List[Spielserver]:
    """Alle Spielserver — Satisfactory zuerst, dann Minecraft."""
    return satisfactory_server() + minecraft_server()


def nach_kennung(kennung: str) -> Optional[Spielserver]:
    """Einen Server ueber seine Kennung finden (``mc_bmc``, ``satisfactory``)."""
    for srv in alle():
        if srv.kennung == kennung:
            return srv
    return None


def nach_service(service: str) -> Optional[Spielserver]:
    """Einen Server ueber seinen systemd-Unit-Namen finden."""
    for srv in alle():
        if srv.service == service:
            return srv
    return None


def kennung_zu_service() -> dict:
    """Zuordnung Kennung → systemd-Unit."""
    return {srv.kennung: srv.service for srv in alle()}


def service_zu_kennung() -> dict:
    """Zuordnung systemd-Unit → Kennung."""
    return {srv.service: srv.kennung for srv in alle()}


def kennung_zu_label() -> dict:
    """Zuordnung Kennung → Anzeigename."""
    return {srv.kennung: srv.label for srv in alle()}


def vorgabe(spiel: str) -> Optional[str]:
    """
    Die ENV-Praefix-ID des Vorgabe-Servers eines Spiels.

    Befehle ohne Server-Angabe benutzen sie. Frueher stand dort ein fester Name
    (``server: str = "VANILLA"`` in ``/mc crashlog``) — der lief ins Leere,
    sobald der Server wegfiel.
    """
    liste = minecraft_server() if spiel == "minecraft" else satisfactory_server()
    return liste[0].server_id if liste else None


__all__ = [
    "Spielserver", "minecraft_server", "satisfactory_server", "alle",
    "nach_kennung", "nach_service", "kennung_zu_service", "service_zu_kennung",
    "kennung_zu_label", "vorgabe", "MC_DEFAULT", "SAT_DEFAULT",
]
