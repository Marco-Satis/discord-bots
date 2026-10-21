"""
Sicherheits-Übersicht für den Discord-Server (Vorbild Wick, 21.10.2026).

Eine Ampel je Schutz: Verifizierung, Raid-Schutz, Discord-Einstellungen
(Verifizierungsstufe, 2FA-Pflicht, Inhaltsfilter), Marshals Rechte, Admin-Rollen,
Audit-Log und Rollen über Marshal. Bisher gab es das nur als `/sicherheit status`
in Discord. Reine Funktion über den gesammelten Quellen (testbar ohne Bot).
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from web.module_uebersicht import baue_karten, rollen_probleme

STUFE_TEXT = {
    0: "Keine",
    1: "Niedrig (E-Mail bestätigt)",
    2: "Mittel (Konto älter als 5 Minuten)",
    3: "Hoch (seit 10 Minuten auf dem Server)",
    4: "Sehr hoch (Telefonnummer bestätigt)",
}
FILTER_TEXT = {0: "aus", 1: "nur für Mitglieder ohne Rolle", 2: "für alle Mitglieder"}

# Rechte, die Marshals Funktionen brauchen -> wofür
MARSHAL_BRAUCHT = {
    "manage_roles": "Rollen verwalten (Verifizierung, Rangrollen, Sticky-Rollen)",
    "kick_members": "Kicken",
    "ban_members": "Bannen",
    "moderate_members": "Timeout",
    "manage_messages": "Nachrichten verwalten (Automod)",
    "view_audit_log": "Audit-Log ansehen (Täter im Log)",
    "manage_guild": "Server verwalten (Raid-Sperre)",
}


def _punkt(titel: str, status: str, text: str, link: str = "") -> dict:
    return {"titel": titel, "status": status, "text": text, "link": link}


def pruefungen(q: dict[str, Any], sicherheit: dict[str, Any], marshal_rechte: list[str] | None) -> list[dict]:
    """
    Ampel-Liste. status: „ok“ (grün/cyan), „pruefen“ (magenta), „info“ (neutral).
    `sicherheit` ist snapshot["sicherheit"], `marshal_rechte` die Rechte-Liste der
    Marshal-Rolle aus dem Snapshot (None = unbekannt).
    """
    cfg = q.get("cfg", {})
    punkte: list[dict] = []

    # Verifizierung
    if not cfg.get("security.verification_enabled"):
        punkte.append(_punkt("Verifizierung", "pruefen", "Aus: neue Mitglieder sehen den Server sofort.",
                             "/marshal-bot#mod-verifizierung"))
    elif not cfg.get("security.verification_role_id"):
        punkte.append(_punkt("Verifizierung", "pruefen", "An, aber keine Rolle hinterlegt.",
                             "/marshal-bot#mod-verifizierung"))
    else:
        probleme = rollen_probleme(q, [cfg.get("security.verification_role_id")])
        punkte.append(_punkt("Verifizierung", "pruefen" if probleme else "ok",
                             f"Marshal kann {', '.join(probleme)} nicht vergeben." if probleme
                             else "Neue Mitglieder bestätigen per Knopf.", "/marshal-bot#mod-verifizierung"))

    # Raid-Schutz
    schwelle = int(cfg.get("security.raid_threshold", 0) or 0)
    fenster = cfg.get("security.raid_window_seconds", 10) or 10
    sperre_min = cfg.get("security.raid_lock_minutes", 10)
    bis = cfg.get("security.raid_lock_until")
    jetzt = q.get("jetzt", time.time())
    if bis and float(bis) > jetzt:
        punkte.append(_punkt("Raid-Schutz", "pruefen",
                             f"Sperre aktiv bis {datetime.fromtimestamp(float(bis)).strftime('%H:%M')} Uhr. "
                             "Aufheben mit /sicherheit entsperren.", "/marshal-bot#mod-raidschutz"))
    elif schwelle <= 0:
        punkte.append(_punkt("Raid-Schutz", "pruefen", "Aus: Beitrittswellen werden nicht erkannt.",
                             "/marshal-bot#mod-raidschutz"))
    else:
        reaktion = f"Sperre {sperre_min} min" if int(sperre_min or 0) > 0 else "nur Alarm"
        punkte.append(_punkt("Raid-Schutz", "ok", f"Ab {schwelle} Beitritten in {fenster} s, {reaktion}.",
                             "/marshal-bot#mod-raidschutz"))

    # Discord-Einstellungen (aus dem Snapshot von Recon)
    if sicherheit:
        stufe = int(sicherheit.get("verification_level", 0))
        punkte.append(_punkt("Discord-Verifizierungsstufe", "ok" if stufe >= 1 else "pruefen",
                             STUFE_TEXT.get(stufe, str(stufe)) + ". Einstellbar in Discord unter Server-Einstellungen → Sicherheit."))
        mfa = int(sicherheit.get("mfa_level", 0))
        punkte.append(_punkt("2FA für Moderation", "ok" if mfa else "pruefen",
                             "Pflicht: Moderationsrechte gehen nur mit Zwei-Faktor-Anmeldung." if mfa
                             else "Keine Pflicht: ein gekapertes Team-Konto ohne 2FA kann moderieren."))
        filt = int(sicherheit.get("explicit_content_filter", 0))
        punkte.append(_punkt("Filter für anstößige Bilder", "ok" if filt else "pruefen",
                             f"Discord prüft Medien {FILTER_TEXT.get(filt, str(filt))}."))
        admins = sicherheit.get("admin_rollen", []) or []
        punkte.append(_punkt("Rollen mit Administrator", "ok" if len(admins) <= 2 else "pruefen",
                             (", ".join(admins) if admins else "keine") +
                             ("" if len(admins) <= 2 else ". Je weniger, desto kleiner der Schaden bei einem gekaperten Konto.")))
    else:
        punkte.append(_punkt("Discord-Einstellungen", "info", "Noch keine Daten von Recon. Kommen mit dem nächsten Abgleich."))

    # Marshals Rechte
    if marshal_rechte is None:
        punkte.append(_punkt("Rechte von Marshal", "info", "Noch keine Daten von Recon."))
    elif "administrator" in marshal_rechte:
        punkte.append(_punkt("Rechte von Marshal", "ok", "Administrator: alle Funktionen haben ihre Rechte."))
    else:
        fehlt = [text for recht, text in MARSHAL_BRAUCHT.items() if recht not in marshal_rechte]
        punkte.append(_punkt("Rechte von Marshal", "pruefen" if fehlt else "ok",
                             ("Es fehlt: " + "; ".join(fehlt) + ".") if fehlt else "Alle benötigten Rechte vorhanden."))

    # Rollen über Marshal (alle Modulkarten zusammen)
    karten = baue_karten(q)
    hierarchie = [k["name"] for k in karten if k["status"] == "pruefen" and "nicht vergeben" in k["info"]]
    punkte.append(_punkt("Rollen-Reihenfolge", "pruefen" if hierarchie else "ok",
                         ("Marshal steht zu tief für: " + ", ".join(hierarchie) + ".") if hierarchie
                         else "Alle Rollen, die Marshal vergibt, liegen unter ihm.", "/marshal-bot"))

    # Audit-Log
    audit = q.get("audit") or {}
    audit_an = bool(q.get("module", {}).get("audit", True))
    mod_log = bool(audit.get("mod_log_channel"))
    punkte.append(_punkt("Audit-Log", "ok" if audit_an and mod_log else "pruefen",
                         "Moderation und Server-Änderungen werden protokolliert." if audit_an and mod_log
                         else "Aus oder kein Moderations-Kanal gesetzt.", "/logs"))
    return punkte


def zusammenfassung(punkte: list[dict]) -> dict[str, int]:
    return {s: sum(1 for p in punkte if p["status"] == s) for s in ("ok", "pruefen", "info")}
