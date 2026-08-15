#!/usr/bin/env python3
"""Test: Live-Status-Panel im HUD-Stil (modules/monitoring/status_panel.py).

Prueft was das Panel zusagt: Kennzahlen im Kopf stimmen mit den Zeilen ueberein,
jeder Server bekommt Statuspunkt und Balken, Details stehen als Subtext,
gestoppte Server sagen das auch.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.monitoring.status_panel import ServerLine, build_status_panel  # noqa: E402
from utils.embeds import COLOR_NEUTRAL, COLOR_SUCCESS  # noqa: E402

RESULTS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    mark = "OK  " if ok else "FEHLER"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not ok else ""))


def test_kopf_stimmt_mit_zeilen() -> None:
    print("\nKennzahlen-Kopf leitet sich aus den Zeilen ab")
    embed = build_status_panel([
        ServerLine("Satisfactory", True, 1, 4, "Uptime 17h"),
        ServerLine("Better MC 5", True, 3, 20),
        ServerLine("Zweitserver", False),
    ])
    beschreibung = embed.description or ""
    check("Spieler summiert", "**4** Spieler" in beschreibung, beschreibung.splitlines()[0])
    check("Server gezaehlt", "**2/3** Server" in beschreibung, beschreibung.splitlines()[0])
    check("Titel mit Statuspunkt", (embed.title or "").startswith("🟢"), embed.title or "")
    check("Titel bleibt SERVERSTATUS", "SERVERSTATUS" in (embed.title or ""))
    check("Farbe gruen wenn etwas laeuft", embed.color.value == COLOR_SUCCESS)


def test_serverzeile() -> None:
    print("\nEine Serverzeile")
    embed = build_status_panel([
        ServerLine("Satisfactory", True, 1, 4, "Uptime 17h 13m", ["👤 SpielerA"]),
    ])
    text = embed.description or ""
    check("Punkt vor dem Namen", "🟢 **Satisfactory**" in text, text)
    check("Auslastung als Chip", "`1/4`" in text, text)
    check("Balken vorhanden", "▰" in text and "▱" in text, text)
    check("Notiz als Subtext", "-# Uptime 17h 13m" in text, text)
    check("Detailzeile als Subtext", "-# 👤 SpielerA" in text, text)


def test_gestoppter_server() -> None:
    print("\nGestoppter Server")
    embed = build_status_panel([ServerLine("Zweitserver", False)])
    text = embed.description or ""
    check("dunkler Punkt", "⚫ **Zweitserver**" in text, text)
    check("sagt gestoppt", "-# gestoppt" in text, text)
    check("kein Balken", "▰" not in text and "▱" not in text, text)
    check("kein Auslastungs-Chip", "`" not in text.split("\n", 1)[-1], text)


def test_alles_offline() -> None:
    print("\nAlle Server offline")
    embed = build_status_panel([
        ServerLine("Satisfactory", False),
        ServerLine("Zweitserver", False),
    ])
    text = embed.description or ""
    check("0 Spieler", "**0** Spieler" in text, text.splitlines()[0])
    check("0 von 2 Servern", "**0/2** Server" in text, text.splitlines()[0])
    check("Farbe neutral", embed.color.value == COLOR_NEUTRAL, hex(embed.color.value))
    check("Titel-Punkt dunkel", (embed.title or "").startswith("⚫"), embed.title or "")


def test_trennung_und_laenge() -> None:
    print("\nLesbarkeit und Discord-Grenzen")
    embed = build_status_panel([
        ServerLine("A", True, 1, 4, "Notiz", ["Detail 1", "Detail 2"]),
        ServerLine("B", True, 2, 8, "Notiz"),
    ])
    text = embed.description or ""
    check("Leerzeile zwischen Servern", "\n\n" in text, repr(text[:80]))

    # Vollbesetztes Panel: drei Server mit allen Details bleiben unter dem
    # Discord-Limit von 4096 Zeichen fuer die Beschreibung.
    voll = build_status_panel([
        ServerLine(f"Server {i}", True, 20, 20, "Uptime 12d 4h · 🟢 30/30 Ticks", [
            "👤 " + ", ".join(f"Spieler{n}" for n in range(20)),
            "🏗️ 999.999 Gebäude · ⚡ 250 Gen (12.000 MW) · 🏭 4.000 Prod",
            "💾 512 MB · ⏱️ 1.234h Spielzeit",
            "🔄 Neustart <t:1786675764:R>",
        ])
        for i in range(3)
    ])
    check("volles Panel unter 4096 Zeichen", len(voll.description or "") < 4096,
          f"{len(voll.description or '')} Zeichen")


def test_teilangaben() -> None:
    print("\nFehlende Werte brechen nichts"),
    embed = build_status_panel([
        ServerLine("Ohne Spielerzahl", True),
        ServerLine("Nur Notiz", True, note="startet gerade"),
    ])
    text = embed.description or ""
    check("Server ohne Spielerzahl gerendert", "🟢 **Ohne Spielerzahl**" in text, text)
    check("kein Balken ohne Zahlen", text.count("▰") == 0, text)
    check("Notiz ohne Zahlen sichtbar", "-# startet gerade" in text, text)
    check("Kopf zaehlt sie als laufend", "**0** Spieler · **2/2** Server" in text,
          text.splitlines()[0])


def main() -> int:
    print("=" * 62)
    print("  Live-Status-Panel (HUD)")
    print("=" * 62)
    test_kopf_stimmt_mit_zeilen()
    test_serverzeile()
    test_gestoppter_server()
    test_alles_offline()
    test_trennung_und_laenge()
    test_teilangaben()

    print()
    if all(RESULTS):
        print(f"  ERGEBNIS: BESTANDEN ({len(RESULTS)} Checks)")
        return 0
    print(f"  ERGEBNIS: FEHLGESCHLAGEN ({RESULTS.count(False)}/{len(RESULTS)})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
