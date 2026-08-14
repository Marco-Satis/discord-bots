"""
Join/Leave-Erkennung aus dem Satisfactory-Serverlog — ueber die Verbindungs-ID.

Warum nicht ueber den Spielernamen: die Abmelde-Zeile des Servers enthaelt
**keinen** Namen. Sie sieht so aus:

    LogNet: UNetConnection::Close: [UNetConnection] RemoteAddr: 1.2.3.4:41317,
            Name: IpConnection_2147330305, Driver: ..., IsServer: YES

Die alte Regex verlangte ein Feld ``PlayerName=`` und traf deshalb nie etwas —
Spieler wurden hinzugefuegt und nie wieder entfernt (Karteileichen im Panel).

Der Server nennt die Verbindung dagegen dreimal beim selben Namen:

    AddClientConnection: Added client connection: ... Name: IpConnection_2147083652
    LogNet: Join succeeded: <Spielername>
    UNetConnection::Close: ... Name: IpConnection_2147083652

Also: Verbindung merken, beim Join an den Namen binden, beim Schliessen wieder
aufloesen. Das ist eindeutig auch dann, wenn zwei Spieler hinter derselben
oeffentlichen IP sitzen — die IP taugt dafuer nicht, die Verbindungs-ID schon.

    parser = SatLogPlayerParser()
    joined, left = parser.feed(neuer_log_abschnitt)
    parser.online   # aktuell verbundene Namen
"""
from __future__ import annotations

import re
from collections import deque
from typing import Deque, Dict, Set, Tuple

# Verbindung wird aufgebaut — ab hier ist die ID bekannt, der Name noch nicht.
RE_ADD_CONNECTION = re.compile(
    r"AddClientConnection:.*?Name:\s*(IpConnection_\d+)"
)

# Spieler ist drin. Der Name steht bis zum Zeilenende.
RE_JOIN = re.compile(r"LogNet:\s*Join succeeded:\s*(.+?)\s*$")

# Verbindung endet — regulaer, per Timeout oder Absturz, immer mit derselben ID.
RE_CLOSE = re.compile(
    r"UNetConnection::Close:.*?Name:\s*(IpConnection_\d+)"
)


class SatLogPlayerParser:
    """Haelt die Zuordnung Verbindung -> Spieler und leitet Join/Leave daraus ab."""

    def __init__(self) -> None:
        # IpConnection_<id> -> Spielername
        self._by_connection: Dict[str, str] = {}
        # Verbindungen, die aufgebaut wurden, aber noch keinen Namen haben.
        # Bewusst eine Schlange, kein einzelner Platz: melden sich zwei Spieler
        # kurz hintereinander an, stehen zwei AddClientConnection-Zeilen vor dem
        # ersten "Join succeeded". Mit einem Einzelplatz haette die zweite die
        # erste ueberschrieben — der erste Spieler waere an die falsche
        # Verbindung gebunden worden und beim Abmelden haengengeblieben, also
        # genau die Karteileiche, gegen die dieses Modul gebaut wurde.
        # Der Server bedient Verbindungen in Reihenfolge, deshalb FIFO.
        self._pending: Deque[str] = deque()
        # Spieler, deren Verbindung nie zugeordnet werden konnte (Log-Anfang
        # mitten in einer Sitzung). Sie bleiben online, bis der Abgleich mit
        # der Server-API sie aufraeumt.
        self._unbound: Set[str] = set()

    # ------------------------------------------------------------------
    # Zustand
    # ------------------------------------------------------------------

    @property
    def online(self) -> Set[str]:
        """Aktuell verbundene Spieler."""
        return set(self._by_connection.values()) | self._unbound

    @property
    def unbound(self) -> Set[str]:
        """Spieler ohne bekannte Verbindung — koennen nicht per Log abgemeldet werden."""
        return set(self._unbound)

    def reset(self) -> None:
        self._by_connection.clear()
        self._pending.clear()
        self._unbound.clear()

    def drop(self, namen: Set[str]) -> None:
        """Spieler entfernen, die laut Server nicht mehr online sind.

        Wird vom Abgleich gegen die API benutzt: das Log kann eine Abmeldung
        verpassen (Log-Rotation, Bot-Neustart mitten in der Sitzung), die API
        kennt die Wahrheit.
        """
        for conn, name in list(self._by_connection.items()):
            if name in namen:
                del self._by_connection[conn]
        self._unbound -= namen

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def feed(self, text: str) -> Tuple[Set[str], Set[str]]:
        """Neuen Log-Abschnitt verarbeiten.

        Returns:
            (joined, left) — Namen, die in diesem Abschnitt dazugekommen bzw.
            gegangen sind. Wer im selben Abschnitt kommt und geht, taucht in
            beiden auf; der Aufrufer bildet ``(online | joined) - left``.
        """
        joined: Set[str] = set()
        left: Set[str] = set()

        for line in text.splitlines():
            m = RE_ADD_CONNECTION.search(line)
            if m:
                if m.group(1) not in self._pending:
                    self._pending.append(m.group(1))
                continue

            m = RE_JOIN.search(line)
            if m:
                name = m.group(1).strip()
                if not name:
                    continue
                if self._pending:
                    self._by_connection[self._pending.popleft()] = name
                else:
                    # Join ohne vorangehenden Verbindungsaufbau: der Log-
                    # Abschnitt beginnt mittendrin. Name merken, Abmeldung
                    # kann dann nur der API-Abgleich liefern.
                    self._unbound.add(name)
                joined.add(name)
                continue

            m = RE_CLOSE.search(line)
            if m:
                conn = m.group(1)
                name = self._by_connection.pop(conn, None)
                if name and name not in self._by_connection.values():
                    left.add(name)
                elif name is None and conn in self._pending:
                    # Verbindung endete vor dem Login — kein Spieler betroffen.
                    self._pending.remove(conn)

        return joined, left
