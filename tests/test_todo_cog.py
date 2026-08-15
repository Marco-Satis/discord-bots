#!/usr/bin/env python3
"""Test: To-Do-Board (/todo) — Schema, Rechte, Toggle, Sticky-Debounce.

Prueft die Zusagen aus dem Plan:
  - Migration v10 legt todos + todo_board an und ist idempotent
  - bearbeiten/loeschen darf nur der Ersteller (Admin/Owner ausgenommen)
  - Toggle dreht done in beide Richtungen und setzt/leert done_by
  - Sticky-Debounce: mehrere Nachrichten -> genau EIN Repost,
    Bot-eigene Nachricht -> gar keiner
"""
import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiosqlite  # noqa: E402

from modules.database.migrations import run_migrations  # noqa: E402

RESULTS = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    mark = "OK  " if ok else "FEHLER"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not ok else ""))


async def _table_names(db) -> set:
    cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in await cur.fetchall()}


async def test_migration() -> None:
    print("\nMigration v10")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            await run_migrations(db)
            tables = await _table_names(db)
            check("todos angelegt", "todos" in tables)
            check("todo_board angelegt", "todo_board" in tables)

            cur = await db.execute("PRAGMA user_version")
            version = (await cur.fetchone())[0]
            # Gegen CURRENT_VERSION pruefen, nicht gegen eine feste Zahl: die
            # fest eingetragene 10 machte jede neue Migration zum Testfehler,
            # obwohl nichts kaputt war. Geprueft gehoert, dass die Kette bis
            # ans Ende laeuft — nicht, wo das Ende gerade liegt.
            from modules.database.migrations import CURRENT_VERSION
            check(f"user_version = {CURRENT_VERSION}", version == CURRENT_VERSION,
                  f"ist {version}")

            # Zweiter Lauf darf nicht knallen und nichts kaputt machen.
            await run_migrations(db)
            tables2 = await _table_names(db)
            check("zweiter Lauf idempotent", tables == tables2)


class _FakeInteraction:
    """Minimal-Ersatz fuer discord.Interaction im Rechte-Check."""

    def __init__(self, user_id: int):
        self.user = SimpleNamespace(id=user_id, display_name=f"user{user_id}")
        self.guild = None


async def test_permissions() -> None:
    print("\nRechte: bearbeiten/loeschen nur durch Ersteller")
    from cogs.todo_cog import ToDoCog

    cog = ToDoCog.__new__(ToDoCog)
    row = {"id": 1, "created_by": "111", "created_by_name": "Marco"}

    with patch("cogs.todo_cog.is_admin", return_value=False):
        check("Ersteller darf", cog._may_modify(_FakeInteraction(111), row))
        check("fremder Spieler darf nicht", not cog._may_modify(_FakeInteraction(222), row))

    with patch("cogs.todo_cog.is_admin", return_value=True):
        check("Admin darf fremden Eintrag", cog._may_modify(_FakeInteraction(222), row))


async def test_toggle() -> None:
    print("\nToggle: done in beide Richtungen")
    from cogs.todo_cog import ToDoCog

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            await run_migrations(db)
            await db.execute(
                """INSERT INTO todos (id, board, text, created_by, created_by_name, created_at)
                   VALUES (1, 'satisfactory', 'Stahlfabrik', '111', 'Marco', ?)""",
                (datetime.now().isoformat(),),
            )
            await db.commit()

            cog = ToDoCog.__new__(ToDoCog)
            user = SimpleNamespace(id=222, display_name="Mitspieler")

            async def _db():
                return db

            with patch("cogs.todo_cog.get_db", _db), patch("cogs.todo_cog.get_read_db", _db):
                ok = await cog.toggle_todo(1, user)
                check("Toggle meldet Erfolg", ok)
                row = dict(await (await db.execute("SELECT * FROM todos WHERE id=1")).fetchone())
                check("done gesetzt", row["done"] == 1, f"done={row['done']}")
                check("done_by gesetzt", row["done_by"] == "222", f"done_by={row['done_by']}")

                await cog.toggle_todo(1, user)
                row = dict(await (await db.execute("SELECT * FROM todos WHERE id=1")).fetchone())
                check("done zurueckgesetzt", row["done"] == 0)
                check("done_by geleert", row["done_by"] is None)

                check("fehlender Eintrag -> False", not await cog.toggle_todo(999, user))


def _demo_rows(n: int, done_ab: int = 10**9):
    return [
        {
            "id": i,
            "text": f"Eintrag {i}",
            "done": 1 if i >= done_ab else 0,
            "created_by": "111",
            "created_by_name": "Marco",
            "done_by_name": "Mitspieler",
        }
        for i in range(1, n + 1)
    ]


def _mit_rows(cog, rows):
    async def _fetch(_r=rows):
        return _r

    cog._fetch_todos = _fetch
    return cog


def _texte(payload) -> str:
    """Alle TextDisplay-Inhalte eines Containers zu einem String."""
    return "\n".join(c["content"] for c in payload if c["type"] == 10)


async def test_board_layout() -> None:
    """HUD-Board: 40-Komponenten-Limit, Kennzahlen-Kopf, Gruppen, Ueberlauf."""
    print("\nBoard-Layout (HUD, Components V2)")
    from cogs.todo_cog import COMPONENT_LIMIT, ToDoCog

    cog = ToDoCog.__new__(ToDoCog)

    for n in (0, 1, 5, 9, 12, 40, 60):
        _mit_rows(cog, _demo_rows(n, done_ab=max(1, n // 2)))
        try:
            view = await cog.build_board()
            payload = view.to_components()
            ok = view._total_children <= COMPONENT_LIMIT and len(payload) == 1
            check(
                f"{n} Eintraege -> gueltiges Layout",
                ok,
                f"{view._total_children} Komponenten",
            )
        except Exception as e:  # Limit ueberschritten o.ae.
            check(f"{n} Eintraege -> gueltiges Layout", False, f"{type(e).__name__}: {e}")

    # Kopf + Gruppen: 3 offen, 2 erledigt
    _mit_rows(cog, _demo_rows(5, done_ab=4))
    payload = (await cog.build_board()).to_components()[0]["components"]
    text = _texte(payload)
    check("Ueberschrift bleibt TODO", "## TODO" in text)
    check("Kennzahlen im Kopf", "**3** offen" in text and "**2** erledigt" in text)
    check("Fortschritt in Prozent", "**40%** fertig" in text)
    check("Balken im Kopf", "▬" in text and "▭" in text)
    check("Gruppe OFFEN mit Zaehler", "-# OFFEN · 3" in text)
    check("Gruppe ERLEDIGT mit Zaehler", "-# ERLEDIGT · 2" in text)

    buttons = [
        b
        for c in payload
        if c["type"] == 1
        for b in c.get("components", [])
        if b["type"] == 2
    ]
    ids = {b.get("custom_id") for b in buttons}
    check("Aktionszeile hat + Eintrag", "todo:new" in ids)
    check("Aktionszeile hat Aufraeumen", "todo:clear" in ids)

    # Ueberlauf: alles was keine Zeile bekommt, muss im Menue auftauchen
    gesamt = 30
    _mit_rows(cog, _demo_rows(gesamt, done_ab=25))
    payload = (await cog.build_board()).to_components()[0]["components"]
    sections = [c for c in payload if c["type"] == 9]
    selects = [
        s
        for c in payload
        if c["type"] == 1
        for s in c.get("components", [])
        if s["type"] == 3
    ]
    check("Ueberlauf bekommt Auswahl-Menue", len(selects) == 1)
    if selects:
        abgedeckt = len(sections) + len(selects[0]["options"])
        check(
            "kein Eintrag faellt still weg",
            abgedeckt == gesamt or f"{gesamt - abgedeckt} weitere" in _texte(payload),
            f"{abgedeckt} von {gesamt} erreichbar",
        )


async def test_section_budget() -> None:
    """Das Komponenten-Budget muss zum tatsaechlichen Aufbau passen."""
    print("\nKomponenten-Budget")
    from cogs.todo_cog import COMPONENTS_PER_ROW, COMPONENT_LIMIT, ToDoCog

    cog = ToDoCog.__new__(ToDoCog)
    for hat_offen, hat_erledigt, ueberlauf in (
        (True, False, False),
        (False, True, False),
        (True, True, False),
        (True, True, True),
    ):
        budget = ToDoCog._section_budget(hat_offen, hat_erledigt, ueberlauf)
        check(
            f"Budget offen={hat_offen} erledigt={hat_erledigt} ueberlauf={ueberlauf} > 0",
            budget > 0,
            f"{budget}",
        )

    # Worst Case wirklich bauen: beide Gruppen + Ueberlauf, Budget voll ausgereizt
    budget = ToDoCog._section_budget(True, True, True)
    _mit_rows(cog, _demo_rows(budget + 5, done_ab=budget))
    view = await cog.build_board()
    check(
        "Worst Case bleibt unter dem Limit",
        view._total_children <= COMPONENT_LIMIT,
        f"{view._total_children} Komponenten",
    )
    sections = [c for c in view.to_components()[0]["components"] if c["type"] == 9]
    check(
        "Budget wird ausgeschoepft, nicht ueberschritten",
        len(sections) <= budget,
        f"{len(sections)} Zeilen bei Budget {budget}",
    )
    check("Zeile kostet 3 Komponenten", COMPONENTS_PER_ROW == 3)


async def test_sticky_debounce() -> None:
    print("\nSticky-Debounce")
    import cogs.todo_cog as todo_mod
    from cogs.todo_cog import ToDoCog

    cog = ToDoCog.__new__(ToDoCog)
    cog._board_lock = asyncio.Lock()
    cog._sticky_task = None
    cog._sticky_disabled = False
    cog.refresh_board = AsyncMock()

    async def _ref():
        return {"board": "satisfactory", "guild_id": "1", "channel_id": "42",
                "message_id": "99"}

    cog._get_board_ref = _ref

    def _msg(author_bot: bool, channel_id: int = 42):
        return SimpleNamespace(
            author=SimpleNamespace(bot=author_bot),
            channel=SimpleNamespace(id=channel_id),
        )

    original_delay = todo_mod.STICKY_DEBOUNCE_SECONDS
    todo_mod.STICKY_DEBOUNCE_SECONDS = 0.05
    try:
        # Fuenf Nachrichten in schneller Folge -> ein Repost.
        for _ in range(5):
            await cog.on_message(_msg(False))
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.2)
        check(
            "5 Nachrichten -> 1 Repost",
            cog.refresh_board.await_count == 1,
            f"{cog.refresh_board.await_count} Reposts",
        )

        # Bot-eigene Nachricht darf nichts ausloesen (sonst Endlosschleife).
        cog.refresh_board.reset_mock()
        await cog.on_message(_msg(True))
        await asyncio.sleep(0.2)
        check("Bot-Nachricht -> kein Repost", cog.refresh_board.await_count == 0)

        # Anderer Kanal ist irrelevant.
        await cog.on_message(_msg(False, channel_id=7))
        await asyncio.sleep(0.2)
        check("fremder Kanal -> kein Repost", cog.refresh_board.await_count == 0)
    finally:
        todo_mod.STICKY_DEBOUNCE_SECONDS = original_delay


async def main() -> int:
    print("=" * 62)
    print("  To-Do-Board (/todo)")
    print("=" * 62)
    await test_migration()
    await test_permissions()
    await test_toggle()
    await test_board_layout()
    await test_section_budget()
    await test_sticky_debounce()

    print()
    if all(RESULTS):
        print(f"  ERGEBNIS: BESTANDEN ({len(RESULTS)} Checks)")
        return 0
    print(f"  ERGEBNIS: FEHLGESCHLAGEN ({RESULTS.count(False)}/{len(RESULTS)})")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
