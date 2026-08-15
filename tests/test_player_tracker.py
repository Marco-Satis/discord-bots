#!/usr/bin/env python3
"""Test: Spieler-Erkennung aus dem Satisfactory-Log (Verbindungs-ID statt Name).

Hintergrund: das Panel zeigte "1/4 Players" und listete zwei Namen. Ursache war
die alte Leave-Regex — sie verlangte ein Feld ``PlayerName=``, das der Server in
seinen Abmelde-Zeilen gar nicht schreibt. Abmeldungen wurden nie erkannt, Namen
blieben fuer immer stehen.

Die Log-Zeilen unten sind nachgebaut (generische Namen, Beispiel-IPs), folgen
aber exakt dem Format des echten FactoryGame.log.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.monitoring.sat_log_players import SatLogPlayerParser  # noqa: E402
from modules.satisfactory.api_client import ServerState  # noqa: E402

RESULTS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    mark = "OK  " if ok else "FEHLER"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not ok else ""))


def add_conn(conn: str, ip: str) -> str:
    return (
        f"[2026.08.13-08.20.58:020][172]LogNet: AddClientConnection: Added client "
        f"connection: [UNetConnection] RemoteAddr: {ip}, Name: {conn}, "
        f"Driver: Name:GameNetDriver Def:GameNetDriver FGDSIpNetDriver_2147482164, IsServer: YES"
    )


def join(name: str) -> str:
    return f"[2026.08.13-08.21.01:534][368]LogNet: Join succeeded: {name}"


def close(conn: str, ip: str) -> str:
    return (
        f"[2026.08.13-09.36.59:157][976]LogNet: UNetConnection::Close: [UNetConnection] "
        f"RemoteAddr: {ip}, Name: {conn}, Driver: Name:GameNetDriver "
        f"Def:GameNetDriver FGDSIpNetDriver_2147482164, IsServer: YES, PC: NULL"
    )


def timeout_close(conn: str, ip: str) -> str:
    """Abbruch per Timeout — dieselbe Close-Zeile, davor eine Fehlermeldung."""
    return (
        "[2026.08.13-09.36.59:156][975]LogNet: Error: UEngine::BroadcastNetworkFailure: "
        "FailureType = ConnectionTimeout, ErrorString = UNetConnection::Tick: "
        "Connection TIMED OUT. Closing connection..\n" + close(conn, ip)
    )


def test_join_und_leave() -> None:
    print("\nJoin und Leave ueber die Verbindungs-ID")
    p = SatLogPlayerParser()

    joined, left = p.feed("\n".join([add_conn("IpConnection_1", "10.0.0.1:5000"), join("SpielerA")]))
    check("Join erkannt", joined == {"SpielerA"}, f"{joined}")
    check("noch niemand weg", left == set())
    check("online nach Join", p.online == {"SpielerA"})

    joined, left = p.feed(close("IpConnection_1", "10.0.0.1:5000"))
    check("Leave erkannt", left == {"SpielerA"}, f"{left}")
    check("online nach Leave leer", p.online == set(), f"{p.online}")


def test_abmeldung_ohne_namen() -> None:
    """Der eigentliche Bug: die Abmelde-Zeile enthaelt keinen Spielernamen."""
    print("\nAbmelde-Zeile ohne PlayerName= (Ursache des Zaehler-Bugs)")
    zeile = close("IpConnection_2147083652", "10.0.0.2:63284")
    check("Log-Zeile enthaelt keinen Namen", "PlayerName=" not in zeile)

    p = SatLogPlayerParser()
    p.feed("\n".join([add_conn("IpConnection_2147083652", "10.0.0.2:63284"), join("SpielerB")]))
    _, left = p.feed(zeile)
    check("Abmeldung trotzdem erkannt", left == {"SpielerB"}, f"{left}")


def test_timeout() -> None:
    print("\nVerbindungsabbruch per Timeout")
    p = SatLogPlayerParser()
    p.feed("\n".join([add_conn("IpConnection_9", "10.0.0.9:1234"), join("SpielerC")]))
    _, left = p.feed(timeout_close("IpConnection_9", "10.0.0.9:1234"))
    check("Timeout zaehlt als Leave", left == {"SpielerC"}, f"{left}")
    check("danach niemand online", p.online == set())


def test_gleiche_ip() -> None:
    """Zwei Spieler hinter derselben IP — deshalb Verbindungs-ID statt IP."""
    print("\nZwei Spieler hinter derselben oeffentlichen IP")
    p = SatLogPlayerParser()
    p.feed("\n".join([
        add_conn("IpConnection_10", "10.0.0.5:1111"), join("SpielerD"),
        add_conn("IpConnection_11", "10.0.0.5:2222"), join("SpielerE"),
    ]))
    check("beide online", p.online == {"SpielerD", "SpielerE"}, f"{p.online}")

    _, left = p.feed(close("IpConnection_10", "10.0.0.5:1111"))
    check("nur der Getrennte geht", left == {"SpielerD"}, f"{left}")
    check("der andere bleibt", p.online == {"SpielerE"}, f"{p.online}")


def test_log_beginnt_mittendrin() -> None:
    print("\nLog beginnt mitten in einer Sitzung")
    p = SatLogPlayerParser()
    joined, _ = p.feed(join("SpielerF"))
    check("Join ohne Verbindungsaufbau zaehlt", joined == {"SpielerF"})
    check("gilt als online", p.online == {"SpielerF"})
    check("als unbound markiert", p.unbound == {"SpielerF"}, f"{p.unbound}")

    p.drop({"SpielerF"})
    check("API-Abgleich raeumt ihn weg", p.online == set(), f"{p.online}")


def test_verbindung_ohne_join() -> None:
    print("\nVerbindung bricht vor dem Login ab")
    p = SatLogPlayerParser()
    joined, left = p.feed("\n".join([
        add_conn("IpConnection_20", "10.0.0.7:9999"),
        close("IpConnection_20", "10.0.0.7:9999"),
    ]))
    check("kein Join", joined == set())
    check("kein Leave", left == set())
    check("niemand online", p.online == set())


def test_wiedereinstieg() -> None:
    print("\nAbmelden und mit neuer Verbindung zurueckkommen")
    p = SatLogPlayerParser()
    p.feed("\n".join([add_conn("IpConnection_30", "10.0.0.8:1"), join("SpielerG")]))
    p.feed(close("IpConnection_30", "10.0.0.8:1"))
    joined, _ = p.feed("\n".join([add_conn("IpConnection_31", "10.0.0.8:2"), join("SpielerG")]))
    check("Wiedereinstieg erkannt", joined == {"SpielerG"})
    check("genau einmal online", p.online == {"SpielerG"}, f"{p.online}")

    _, left = p.feed(close("IpConnection_31", "10.0.0.8:2"))
    check("zweite Abmeldung greift", left == {"SpielerG"} and p.online == set())


def test_gleichzeitige_anmeldung() -> None:
    """Zwei Verbindungen bauen sich auf, bevor der erste Join kommt.

    Regression zu Review-Befund Q-01: der Parser merkte sich nur EINE offene
    Verbindung. Die zweite AddClientConnection-Zeile ueberschrieb die erste,
    der erste Spieler wurde an die falsche ID gebunden und blieb beim Abmelden
    als Karteileiche stehen — genau der Fehler, gegen den das Modul gebaut ist.
    """
    print("\nZwei Anmeldungen ueberlappen sich")
    p = SatLogPlayerParser()
    p.feed("\n".join([
        add_conn("IpConnection_50", "10.0.0.20:1"),
        add_conn("IpConnection_51", "10.0.0.21:1"),
        join("SpielerI"),
        join("SpielerJ"),
    ]))
    check("beide online", p.online == {"SpielerI", "SpielerJ"}, f"{p.online}")
    check("keiner haengt ohne Verbindung", p.unbound == set(), f"{p.unbound}")

    # Der Server bedient Verbindungen der Reihe nach: die erste ID gehoert zum
    # ersten Join.
    _, left = p.feed(close("IpConnection_50", "10.0.0.20:1"))
    check("erste Verbindung meldet den ersten Spieler ab", left == {"SpielerI"}, f"{left}")
    check("der zweite bleibt", p.online == {"SpielerJ"}, f"{p.online}")

    _, left = p.feed(close("IpConnection_51", "10.0.0.21:1"))
    check("zweite Abmeldung greift auch", left == {"SpielerJ"} and p.online == set(),
          f"{left} / {p.online}")


def test_reset() -> None:
    print("\nreset() leert den Zustand")
    p = SatLogPlayerParser()
    p.feed("\n".join([add_conn("IpConnection_40", "10.0.0.10:1"), join("SpielerH")]))
    p.reset()
    check("nach reset niemand online", p.online == set())


def test_api_degradiert() -> None:
    """Gescheiterte API-Abfrage darf nicht wie '0 Spieler' aussehen."""
    print("\nGescheiterte API-Abfrage ist unterscheidbar")
    check("Default ist ok=True", ServerState().ok is True)
    degradiert = ServerState(ok=False)
    check("degradiert ist ok=False", degradiert.ok is False)
    check("degradiert meldet 0 Spieler", degradiert.num_players == 0)
    check(
        "0 Spieler allein ist kein Fehlersignal",
        ServerState(num_players=0).ok is True,
    )


def main() -> int:
    print("=" * 62)
    print("  Spieler-Erkennung (Satisfactory-Log)")
    print("=" * 62)
    test_join_und_leave()
    test_abmeldung_ohne_namen()
    test_timeout()
    test_gleiche_ip()
    test_log_beginnt_mittendrin()
    test_verbindung_ohne_join()
    test_wiedereinstieg()
    test_gleichzeitige_anmeldung()
    test_reset()
    test_api_degradiert()

    print()
    if all(RESULTS):
        print(f"  ERGEBNIS: BESTANDEN ({len(RESULTS)} Checks)")
        return 0
    print(f"  ERGEBNIS: FEHLGESCHLAGEN ({RESULTS.count(False)}/{len(RESULTS)})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
