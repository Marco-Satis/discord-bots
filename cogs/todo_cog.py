"""
To-Do-Board Cog — gemeinsame Bau-Ziele fuer den Satisfactory-Server.

  /todo here            — Board im aktuellen Kanal verankern (Admin)
  /todo unstick         — Sticky abschalten (Admin)
  /todo list            — Board neu posten / auffrischen
  /todo add <text>      — Eintrag anlegen
  /todo edit <nr> <txt> — Eintrag aendern (nur eigener; Admin/Owner ueberall)
  /todo remove <nr>     — Eintrag loeschen (nur eigener; Admin/Owner ueberall)
  /todo clear           — alle erledigten Eintraege loeschen (Admin)

Abhaken per Klick auf die Checkbox neben dem Eintrag darf jeder Spieler, auch
bei fremden Eintraegen. Bearbeiten und Loeschen bleibt dem Ersteller vorbehalten.

Darstellung: HUD-Stil (Marcos Wahl 2026-08-13) als Components V2 —
Kennzahlen-Kopf mit Fortschrittsbalken, Zwischenueberschriften je Gruppe, je
Eintrag eine Zeile mit Text links und Checkbox-Button rechts, Aktionen unten.

Discord begrenzt eine Nachricht auf 40 Komponenten und eine Eintragszeile kostet
drei davon. Wie viele Zeilen passen, rechnet :func:`ToDoCog._section_budget`
abhaengig davon aus, was sonst noch im Panel steht; der Rest laeuft ueber das
Auswahl-Menue.

Das Board ist eine Sticky-Message: schreibt jemand in den Board-Kanal, wird es
nach kurzer Ruhe geloescht und neu gepostet, damit es immer die letzte
Nachricht bleibt.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.database.db_manager import get_db, get_read_db
from utils import get_logger, is_admin, is_spieler, admin_only, spieler_only
from utils.ui_kit import heading, meta_row, progress_bar, subtext

logger = get_logger("cogs.todo")

# Board-Kennung. Das Schema kann mehrere Boards, die Commands bieten aktuell
# bewusst nur dieses eine an.
BOARD = "satisfactory"

# Seit 2026-08-16 traegt die Spalte `board` die Server-Kennung (MAIN, SECOND, …)
# statt eines festen Namens. Eintraege aus der Zeit davor stehen unter dem alten
# Wert und gehoeren zur ersten Instanz — deshalb bleibt BOARD als Rueckfall
# stehen und wird nicht weggeworfen.
BOARD_ALT = BOARD

# Harte Discord-Grenze: 40 Komponenten je Nachricht. Eine Eintragszeile kostet 3
# (Section + Text + Button); ein Sicherheitsabstand bleibt fuer kuenftige Zusaetze.
COMPONENT_LIMIT = 40
COMPONENTS_PER_ROW = 3

# Discord-Limit fuer ein Auswahl-Menue.
MAX_SELECT_OPTIONS = 25

# Emoji der Checkbox — leer/abgehakt.
BOX_OPEN = "⬜"
BOX_DONE = "✅"

# Akzentfarbe des Panels (HUD-Gold).
ACCENT = 0xF2C14E

# Sekunden Ruhe im Kanal, bevor das Board neu gepostet wird. Ohne diesen
# Debounce erzeugt eine lebhafte Unterhaltung einen Repost pro Nachricht und
# laeuft in Discords Kanal-Rate-Limit (429).
STICKY_DEBOUNCE_SECONDS = 4.0

MAX_TEXT_LEN = 200


def _fmt_user(row: Dict[str, Any], key: str) -> str:
    """Anzeigename aus der Zeile holen, Markdown entschaerft."""
    name = row.get(key) or "?"
    return discord.utils.escape_markdown(str(name))


async def _handle_toggle(
    interaction: discord.Interaction, todo_ids: List[int]
) -> None:
    """Gemeinsamer Ablauf fuer Checkbox-Klick und Auswahl-Menue.

    Rechte pruefen, umschalten, Board neu aufbauen und dieselbe Nachricht
    ersetzen. Scheitert das Ersetzen (z.B. weil die Nachricht noch die alte
    Embed-Darstellung ist, in die sich kein Components-V2-Layout editieren
    laesst), wird das Board neu gepostet.
    """
    if not is_spieler(interaction):
        await interaction.response.send_message(
            "Du brauchst die Satisfactory-Rolle um Eintraege abzuhaken.",
            ephemeral=True,
        )
        return

    cog = interaction.client.get_cog("ToDo")
    if cog is None:
        await interaction.response.send_message(
            "To-Do-Cog ist nicht geladen.", ephemeral=True
        )
        return

    missing = [i for i in todo_ids if not await cog.toggle_todo(i, interaction.user)]
    if len(missing) == len(todo_ids):
        await interaction.response.send_message(
            f"Eintrag `#{todo_ids[0]}` existiert nicht mehr.", ephemeral=True
        )
        await cog.refresh_board()
        return

    view = await cog.build_board()
    try:
        await interaction.response.edit_message(view=view)
    except discord.HTTPException as e:
        logger.warning(f"Board-Edit nach Klick fehlgeschlagen ({e}) — reposte.")
        if not interaction.response.is_done():
            await interaction.response.defer()
        await cog.refresh_board(repost=True)


class TodoToggleButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"todo:toggle:(?P<todo_id>\d+)",
):
    """Checkbox neben einem Eintrag — ein Klick hakt ab bzw. wieder auf.

    DynamicItem statt gewoehnlicher View: die Board-Nachricht steht dauerhaft im
    Kanal und muss einen Bot-Neustart ueberleben. Bei einer normalen View waeren
    die Buttons nach jedem Restart tot.
    """

    def __init__(self, todo_id: int, done: bool):
        self.todo_id = todo_id
        super().__init__(
            discord.ui.Button(
                emoji=BOX_DONE if done else BOX_OPEN,
                style=discord.ButtonStyle.success if done else discord.ButtonStyle.secondary,
                custom_id=f"todo:toggle:{todo_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match,
    ) -> "TodoToggleButton":
        """Rekonstruktion nach Bot-Neustart — der Zustand kommt ohnehin aus der DB."""
        return cls(int(match["todo_id"]), done=item.style == discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _handle_toggle(interaction, [self.todo_id])


class TodoOverflowSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"todo:pick",
):
    """Auswahl-Menue fuer die Eintraege, fuer die keine Checkbox-Zeile mehr passt."""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        options = [
            discord.SelectOption(
                label=f"#{r['id']} {r['text']}"[:100],
                value=str(r["id"]),
                emoji=BOX_DONE if r["done"] else BOX_OPEN,
            )
            for r in (rows or [])[:MAX_SELECT_OPTIONS]
        ] or [discord.SelectOption(label="—", value="0")]

        super().__init__(
            discord.ui.Select(
                custom_id="todo:pick",
                placeholder="Weitere Eintraege abhaken …",
                options=options,
                min_values=1,
                max_values=1,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Select,
        match,
    ) -> "TodoOverflowSelect":
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        picked = [int(v) for v in self.item.values if v.isdigit() and v != "0"]
        if not picked:
            await interaction.response.defer()
            return
        await _handle_toggle(interaction, picked)


class TodoAddModal(discord.ui.Modal, title="Neuer Eintrag"):
    """Eingabefeld hinter dem ``+ Eintrag``-Button.

    Die Zielinstanz steckt im Knopf, nicht im Formular: wer auf ``+ SAT-2``
    drueckt, hat die Entscheidung schon getroffen. Ein zusaetzliches Auswahlfeld
    waere eine Frage, deren Antwort bereits feststeht.
    """

    text = discord.ui.TextInput(
        label="Was soll gebaut werden?",
        placeholder="z.B. Kohlekraftwerk Sued erweitern",
        max_length=MAX_TEXT_LEN,
        required=True,
    )

    def __init__(self, sid: Optional[str] = None, zielname: str = "") -> None:
        super().__init__()
        self.sid = sid
        if zielname:
            self.title = f"Neuer Eintrag — {zielname}"[:45]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("ToDo")
        if cog is None:
            await interaction.response.send_message(
                "To-Do-Cog ist nicht geladen.", ephemeral=True
            )
            return

        neu_id = await cog.add_todo(str(self.text), interaction.user, self.sid)
        await interaction.response.send_message(
            f"Eintrag `#{neu_id}` angelegt.", ephemeral=True
        )
        await cog.refresh_board()


class TodoAddButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"todo:new:(?P<sid>[A-Za-z0-9_\-]+)",
):
    """``+ Eintrag`` — oeffnet das Eingabefeld, ohne Slash-Command.

    Je Instanz ein eigener Knopf. Die Kennung steht in der ``custom_id``, damit
    der Knopf auch nach einem Bot-Neustart noch weiss, wohin der Eintrag gehoert
    — persistente Views bekommen keinen Zustand aus dem Arbeitsspeicher.
    """

    def __init__(self, sid: str, beschriftung: str = "+ Eintrag") -> None:
        self.sid = sid
        super().__init__(
            discord.ui.Button(
                label=beschriftung[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"todo:new:{sid}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match) -> "TodoAddButton":
        return cls(match["sid"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_spieler(interaction):
            await interaction.response.send_message(
                "Du brauchst die Satisfactory-Rolle um Eintraege anzulegen.",
                ephemeral=True,
            )
            return
        cog = interaction.client.get_cog("ToDo")
        zielname = ""
        if cog is not None:
            boards = cog._boards()
            if self.sid in boards:
                zielname = cog._board_name(self.sid, boards.index(self.sid) + 1)
        await interaction.response.send_modal(TodoAddModal(self.sid, zielname))


class TodoClearButton(discord.ui.DynamicItem[discord.ui.Button], template=r"todo:clear"):
    """``Aufraeumen`` — loescht alle erledigten Eintraege (Admin)."""

    def __init__(self, erledigt: int = 0) -> None:
        super().__init__(
            discord.ui.Button(
                label="Aufraeumen" if not erledigt else f"Aufraeumen ({erledigt})",
                style=discord.ButtonStyle.secondary,
                custom_id="todo:clear",
                disabled=erledigt == 0,
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match) -> "TodoClearButton":
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Aufraeumen darf nur die Serverleitung.", ephemeral=True
            )
            return

        cog = interaction.client.get_cog("ToDo")
        if cog is None:
            await interaction.response.send_message(
                "To-Do-Cog ist nicht geladen.", ephemeral=True
            )
            return

        geloescht = await cog.clear_done()
        view = await cog.build_board()
        try:
            await interaction.response.edit_message(view=view)
        except discord.HTTPException as e:
            logger.warning(f"Board-Edit nach Aufraeumen fehlgeschlagen ({e}) — reposte.")
            if not interaction.response.is_done():
                await interaction.response.defer()
            await cog.refresh_board(repost=True)
        await interaction.followup.send(
            f"{geloescht} erledigte Eintraege geloescht.", ephemeral=True
        )


class ToDoCog(commands.Cog, name="ToDo"):
    """Gemeinsames To-Do-Board fuer den Satisfactory-Server."""

    todo = app_commands.Group(name="todo", description="To-Do-Board")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Serialisiert Repost + Button-Refresh, damit nicht zwei Boards
        # nebeneinander stehen bleiben.
        self._board_lock = asyncio.Lock()
        self._sticky_task: Optional[asyncio.Task] = None
        # Kanaele in denen dem Bot ein Recht fehlt — nicht bei jeder Nachricht
        # erneut probieren.
        self._sticky_disabled: bool = False

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(
            TodoToggleButton, TodoOverflowSelect, TodoAddButton, TodoClearButton
        )
        logger.info("ToDoCog geladen — /todo registriert")

    async def cog_unload(self) -> None:
        if self._sticky_task and not self._sticky_task.done():
            self._sticky_task.cancel()

    # ==================================================================
    # DB-Zugriff
    # ==================================================================

    # ------------------------------------------------------------------
    # Instanzen
    # ------------------------------------------------------------------

    @property
    def sat_instanzen(self) -> Dict[str, Any]:
        """Alle Satisfactory-Instanzen, Reihenfolge wie in SAT_SERVER_IDS.

        Auch ueber `self.bot` abgesichert: das Board wird gebaut, bevor der Bot
        vollstaendig bereit ist, und in Tests ohne Bot-Objekt. Fehlt die
        Registry, faellt `_boards()` auf das alte Einzel-Board zurueck — das
        Panel sieht dann aus wie vor dem Umbau statt gar nicht.
        """
        bot = getattr(self, "bot", None)
        return getattr(bot, "sat_servers", {}) or {}

    def _boards(self) -> List[str]:
        """Board-Kennungen in Anzeigereihenfolge.

        Gibt es keine Instanz (Registry leer oder Bot noch nicht bereit),
        bleibt es beim alten Einzel-Board — dann sieht das Panel aus wie
        frueher, statt leer zu sein.
        """
        return list(self.sat_instanzen) or [BOARD_ALT]

    def _board_name(self, sid: str, index: int) -> str:
        """Ueberschrift einer Liste: Anzeigename der Instanz, sonst SAT-<n>."""
        srv = self.sat_instanzen.get(sid)
        name = getattr(srv, "display_name", None) if srv else None
        return name or (BOARD_ALT.upper() if sid == BOARD_ALT else f"SAT-{index}")

    def _erstes_board(self) -> str:
        return self._boards()[0]

    # ------------------------------------------------------------------
    # DB-Zugriff
    # ------------------------------------------------------------------

    async def _fetch_todos(self) -> List[Dict[str, Any]]:
        """Alle Eintraege aller Boards — offene zuerst, dann nach ID.

        Eintraege unter der alten Kennung werden der ersten Instanz
        zugeschlagen, damit nichts unsichtbar wird, was vor dem Umbau
        angelegt wurde.
        """
        conn = await get_read_db()
        cursor = await conn.execute(
            "SELECT * FROM todos ORDER BY done ASC, id ASC"
        )
        rows = [dict(r) for r in await cursor.fetchall()]

        erstes = self._erstes_board()
        bekannt = set(self._boards())
        for r in rows:
            if r.get("board") not in bekannt:
                r["board"] = erstes
        return rows

    async def _fetch_todo(self, todo_id: int) -> Optional[Dict[str, Any]]:
        conn = await get_read_db()
        cursor = await conn.execute(
            "SELECT * FROM todos WHERE id = ?", (todo_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def toggle_todo(self, todo_id: int, user: discord.abc.User) -> bool:
        """Haken setzen oder entfernen. False wenn der Eintrag weg ist."""
        row = await self._fetch_todo(todo_id)
        if row is None:
            return False

        now_done = 0 if row["done"] else 1
        conn = await get_db()
        await conn.execute(
            """UPDATE todos
               SET done = ?, done_by = ?, done_by_name = ?, done_at = ?
               WHERE id = ?""",
            (
                now_done,
                str(user.id) if now_done else None,
                user.display_name if now_done else None,
                datetime.now().isoformat() if now_done else None,
                todo_id,
            ),
        )
        await conn.commit()
        return True

    async def add_todo(
        self, text: str, user: discord.abc.User, sid: Optional[str] = None
    ) -> int:
        """Eintrag anlegen, ID zurueckgeben. Aufrufer prueft Rechte und Laenge.

        Ohne Angabe landet der Eintrag bei der ersten Instanz — dieselbe Regel
        wie bei den /sat-Befehlen, damit man nichts angeben MUSS.
        """
        board = sid if sid in self._boards() else self._erstes_board()
        conn = await get_db()
        cursor = await conn.execute(
            """INSERT INTO todos (board, text, created_by, created_by_name, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                board,
                text.strip(),
                str(user.id),
                user.display_name,
                datetime.now().isoformat(),
            ),
        )
        await conn.commit()
        return int(cursor.lastrowid)

    async def clear_done(self) -> int:
        """Alle erledigten Eintraege loeschen, Anzahl zurueckgeben.

        Gilt bewusst ueber alle Listen: der Knopf sitzt unter dem gemeinsamen
        Panel und raeumt sichtbar das ab, was im Panel als erledigt steht.
        """
        conn = await get_db()
        cursor = await conn.execute("DELETE FROM todos WHERE done = 1")
        await conn.commit()
        return cursor.rowcount

    async def _get_board_ref(self) -> Optional[Dict[str, Any]]:
        conn = await get_read_db()
        cursor = await conn.execute(
            "SELECT * FROM todo_board WHERE board = ?", (BOARD,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def _set_board_ref(
        self, guild_id: int, channel_id: int, message_id: Optional[int]
    ) -> None:
        conn = await get_db()
        await conn.execute(
            """INSERT INTO todo_board (board, guild_id, channel_id, message_id, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(board) DO UPDATE SET
                   guild_id = excluded.guild_id,
                   channel_id = excluded.channel_id,
                   message_id = excluded.message_id,
                   updated_at = excluded.updated_at""",
            (
                BOARD,
                str(guild_id),
                str(channel_id),
                str(message_id) if message_id else None,
                datetime.now().isoformat(),
            ),
        )
        await conn.commit()

    async def _clear_board_ref(self) -> None:
        conn = await get_db()
        await conn.execute("DELETE FROM todo_board WHERE board = ?", (BOARD,))
        await conn.commit()

    # ==================================================================
    # Darstellung
    # ==================================================================

    def _section_text(self, row: Dict[str, Any]) -> str:
        """Zeilentext eines Eintrags — abgehaktes durchgestrichen."""
        text = discord.utils.escape_markdown(row["text"])
        if row["done"]:
            return (
                f"~~{text}~~\n"
                f"-# #{row['id']} · abgehakt von {_fmt_user(row, 'done_by_name')}"
            )
        return f"**{text}**\n-# #{row['id']} · von {_fmt_user(row, 'created_by_name')}"

    def _kopf(self, offen: int, erledigt: int) -> str:
        """Kennzahlen-Kopf mit Fortschrittsbalken."""
        gesamt = offen + erledigt
        prozent = int(erledigt / gesamt * 100) if gesamt else 0
        kennzahlen = meta_row(
            [(str(offen), "offen"), (str(erledigt), "erledigt"), (f"{prozent}%", "fertig")]
        )
        return f"{kennzahlen}\n{progress_bar(erledigt, gesamt, 10, 'outline')}"

    @staticmethod
    def _section_budget(
        listen: int,
        mit_offen: int,
        mit_erledigt: int,
        mit_ueberlauf: bool,
    ) -> int:
        """Wie viele Eintragszeilen ins 40-Komponenten-Budget passen.

        Wird gerechnet statt geraten, damit ein zusaetzliches Element im Panel
        nicht stillschweigend Eintraege verschluckt. Mit zwei Listen kostet
        jede zusaetzliche Ueberschrift Platz — genau deshalb steht die Rechnung
        hier und nicht als Erfahrungswert im Code.

        `listen`       — Anzahl angezeigter Server-Listen
        `mit_offen`    — wie viele davon offene Eintraege haben
        `mit_erledigt` — wie viele davon erledigte haben
        """
        fest = 1 + 1 + 1 + 1              # Container, Ueberschrift, Kopf, Trenner
        fest += 1 + max(listen, 1) + 1    # ActionRow + je Liste ein Plus-Knopf + Aufraeumen
        fest += listen * 2                # je Liste: Servername + Trenner davor
        fest += mit_offen                 # Zwischenueberschrift OFFEN
        fest += mit_erledigt * 2          # Trenner + Zwischenueberschrift ERLEDIGT
        if mit_ueberlauf:
            fest += 3                     # Trenner + ActionRow + Auswahl-Menue
        return max((COMPONENT_LIMIT - fest) // COMPONENTS_PER_ROW, 0)

    def _zeile(self, row: Dict[str, Any]) -> discord.ui.Section:
        """Eine Eintragszeile: Text links, Checkbox rechts."""
        return discord.ui.Section(
            discord.ui.TextDisplay(self._section_text(row)),
            accessory=TodoToggleButton(row["id"], done=bool(row["done"])),
        )

    async def build_board(self) -> discord.ui.LayoutView:
        """Board als Components-V2-Layout aus dem aktuellen DB-Stand bauen.

        Eine Nachricht, je Server eine Liste. Untereinander, nicht nebeneinander:
        Components V2 kennt kein Spaltenlayout — `Container` stapelt seine
        Kinder, und `Section` hat genau eine Beistellung rechts, naemlich die
        Checkbox. Echte Spalten gaebe es nur im klassischen Embed ueber
        `inline=True`, und das kann keine Knoepfe je Zeile. Die Checkbox war der
        Grund fuer den Components-V2-Umbau, deshalb bleibt sie und die Listen
        stehen untereinander.

        HUD-Stil: Kennzahlen-Kopf mit Balken ueber alles, dann je Server ein
        Abschnitt mit eigenem Zaehler und den Gruppen ``OFFEN``/``ERLEDIGT``.
        """
        rows = await self._fetch_todos()
        boards = self._boards()

        # Eintraege den Listen zuordnen, Reihenfolge der Registry beibehalten.
        # Eine Zeile ohne bekannte Kennung gehoert der ersten Liste — das
        # betrifft Altbestand ebenso wie Aufrufer, die `board` nicht mitgeben.
        erstes = boards[0]
        je_board: Dict[str, List[Dict[str, Any]]] = {b: [] for b in boards}
        for r in rows:
            schluessel = r.get("board") or erstes
            if schluessel not in je_board:
                schluessel = erstes
            je_board[schluessel].append(r)

        offen_gesamt = [r for r in rows if not r["done"]]
        erledigt_gesamt = [r for r in rows if r["done"]]

        # Budget zweistufig: erst ohne Ueberlauf rechnen, und nur wenn es dann
        # nicht reicht, mit Auswahl-Menue neu rechnen (das kostet selbst Platz).
        def _budget(mit_ueberlauf: bool) -> int:
            mit_offen = sum(1 for b in boards if any(not r["done"] for r in je_board[b]))
            mit_erl = sum(1 for b in boards if any(r["done"] for r in je_board[b]))
            return self._section_budget(len(boards), mit_offen, mit_erl, mit_ueberlauf)

        budget = _budget(False)
        mit_ueberlauf = len(rows) > budget
        if mit_ueberlauf:
            budget = _budget(True)

        # Der Platz wird der Reihe nach vergeben: erst alle offenen Eintraege
        # von oben nach unten, dann die erledigten. So verdraengt eine volle
        # erste Liste nicht die offenen Punkte der zweiten.
        reihenfolge = [r for b in boards for r in je_board[b] if not r["done"]]
        reihenfolge += [r for b in boards for r in je_board[b] if r["done"]]
        sichtbar = {r["id"] for r in reihenfolge[:budget]}
        rest = reihenfolge[budget:]

        kopf = self._kopf(len(offen_gesamt), len(erledigt_gesamt))
        if len(rest) > MAX_SELECT_OPTIONS:
            # Nicht stillschweigend abschneiden: was das Menue nicht fasst, wird
            # benannt statt verschwiegen.
            kopf += "\n" + subtext(
                f"{len(rest) - MAX_SELECT_OPTIONS} weitere nur ueber `/todo` erreichbar"
            )

        container = discord.ui.Container(accent_colour=discord.Colour(ACCENT))
        container.add_item(discord.ui.TextDisplay(heading("TODO", 2)))
        container.add_item(discord.ui.TextDisplay(kopf))

        if not rows:
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(subtext("Noch nichts eingetragen."))
            )

        for i, board in enumerate(boards, start=1):
            eintraege = je_board.get(board, [])
            b_offen = [r for r in eintraege if not r["done"]]
            b_erledigt = [r for r in eintraege if r["done"]]

            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    f"{heading(self._board_name(board, i), 3)}  "
                    f"-# {len(b_offen)} offen · {len(b_erledigt)} erledigt"
                )
            )

            # Eine Liste ohne Eintraege wird trotzdem gezeigt — sonst sieht es
            # aus, als gaebe es den zweiten Server nicht.
            if not eintraege:
                container.add_item(
                    discord.ui.TextDisplay(subtext("nichts eingetragen"))
                )
                continue

            gezeigt_offen = [r for r in b_offen if r["id"] in sichtbar]
            if gezeigt_offen:
                container.add_item(
                    discord.ui.TextDisplay(subtext(f"OFFEN · {len(b_offen)}"))
                )
                for r in gezeigt_offen:
                    container.add_item(self._zeile(r))

            gezeigt_erledigt = [r for r in b_erledigt if r["id"] in sichtbar]
            if gezeigt_erledigt:
                container.add_item(discord.ui.Separator())
                container.add_item(
                    discord.ui.TextDisplay(subtext(f"ERLEDIGT · {len(b_erledigt)}"))
                )
                for r in gezeigt_erledigt:
                    container.add_item(self._zeile(r))

        if rest:
            container.add_item(discord.ui.Separator())
            auswahl = discord.ui.ActionRow()
            auswahl.add_item(TodoOverflowSelect(rest))
            container.add_item(auswahl)

        aktionen = discord.ui.ActionRow()
        # Je Liste ein eigener Plus-Knopf. Bei nur einer Instanz bleibt es beim
        # schlichten "+ Eintrag" — dann waere ein Servername nur Ballast.
        if len(boards) == 1:
            aktionen.add_item(TodoAddButton(boards[0]))
        else:
            for i, board in enumerate(boards, start=1):
                aktionen.add_item(
                    TodoAddButton(board, f"+ {self._board_name(board, i)}")
                )
        aktionen.add_item(TodoClearButton(len(erledigt_gesamt)))
        container.add_item(aktionen)

        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)
        return view

    # ==================================================================
    # Board posten / auffrischen
    # ==================================================================

    async def refresh_board(self, repost: bool = False) -> Optional[discord.Message]:
        """Board aktualisieren.

        repost=False: bestehende Nachricht editieren (nach add/edit/remove).
        repost=True:  alte Nachricht loeschen und neu senden (Sticky).
        """
        async with self._board_lock:
            ref = await self._get_board_ref()
            if not ref:
                return None

            channel = self.bot.get_channel(int(ref["channel_id"]))
            if channel is None:
                return None

            view = await self.build_board()
            old_id = int(ref["message_id"]) if ref.get("message_id") else None

            async def _send_new() -> discord.Message:
                msg = await channel.send(
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await self._set_board_ref(int(ref["guild_id"]), channel.id, msg.id)
                return msg

            try:
                if not repost and old_id:
                    try:
                        msg = await channel.fetch_message(old_id)
                        await msg.edit(view=view)
                        return msg
                    except discord.HTTPException as e:
                        if isinstance(e, discord.NotFound):
                            raise
                        # Alte Board-Nachricht ist noch die Embed-Variante — in die
                        # laesst sich kein Components-V2-Layout hineineditieren.
                        logger.info(f"Board-Edit nicht moeglich ({e}) — reposte.")
                        repost = True

                if old_id:
                    try:
                        old = await channel.fetch_message(old_id)
                        await old.delete()
                    except discord.NotFound:
                        # Nachricht wurde manuell geloescht — Normalfall.
                        pass

                return await _send_new()

            except discord.NotFound:
                # Editieren schlug fehl, weil die Nachricht weg ist -> neu senden.
                return await _send_new()
            except discord.Forbidden as e:
                self._sticky_disabled = True
                logger.warning(
                    f"Board-Update in Kanal {ref['channel_id']} nicht erlaubt "
                    f"({e}) — Sticky ausgesetzt bis /todo here erneut laeuft."
                )
                return None

    # ==================================================================
    # Sticky-Listener
    # ==================================================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Board nach Ruhe im Kanal wieder ans Ende schieben."""
        if message.author.bot or self._sticky_disabled:
            return

        ref = await self._get_board_ref()
        if not ref or str(message.channel.id) != ref["channel_id"]:
            return

        # Debounce: bei jeder weiteren Nachricht Timer neu starten.
        if self._sticky_task and not self._sticky_task.done():
            self._sticky_task.cancel()
        self._sticky_task = asyncio.create_task(self._delayed_repost())

    async def _delayed_repost(self) -> None:
        try:
            await asyncio.sleep(STICKY_DEBOUNCE_SECONDS)
            await self.refresh_board(repost=True)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Sticky-Repost fehlgeschlagen: {e}")

    # ==================================================================
    # Berechtigung
    # ==================================================================

    def _may_modify(self, interaction: discord.Interaction, row: Dict[str, Any]) -> bool:
        """Bearbeiten/Loeschen: nur der Ersteller, Owner und Admin ausgenommen."""
        return row["created_by"] == str(interaction.user.id) or is_admin(interaction)

    # ==================================================================
    # Commands
    # ==================================================================

    @todo.command(name="here", description="Board in diesem Kanal verankern")
    @admin_only()
    async def todo_here(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Nur in einem Server nutzbar.", ephemeral=True
            )
            return

        self._sticky_disabled = False
        await self._set_board_ref(interaction.guild.id, interaction.channel.id, None)
        await interaction.response.send_message(
            "Board wird hier verankert.", ephemeral=True
        )
        await self.refresh_board(repost=True)

    @todo.command(name="unstick", description="Sticky abschalten (Nachricht bleibt)")
    @admin_only()
    async def todo_unstick(self, interaction: discord.Interaction) -> None:
        ref = await self._get_board_ref()
        if not ref:
            await interaction.response.send_message(
                "Es ist kein Board verankert.", ephemeral=True
            )
            return

        await self._clear_board_ref()
        await interaction.response.send_message(
            "Sticky abgeschaltet. Die bestehende Board-Nachricht bleibt stehen "
            "und ihre Buttons funktionieren weiter.",
            ephemeral=True,
        )

    @todo.command(name="list", description="Board neu posten")
    @spieler_only()
    async def todo_list(self, interaction: discord.Interaction) -> None:
        ref = await self._get_board_ref()
        if not ref:
            if interaction.guild is None:
                await interaction.response.send_message(
                    "Nur in einem Server nutzbar.", ephemeral=True
                )
                return
            # Noch nirgends verankert -> dieser Kanal wird der Board-Kanal.
            # Damit ist auch das Sticky aktiv; abschalten via /todo unstick.
            await self._set_board_ref(
                interaction.guild.id, interaction.channel.id, None
            )

        await interaction.response.send_message("Board wird gepostet.", ephemeral=True)
        await self.refresh_board(repost=True)

    async def _server_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice]:
        """Auswahlliste der Satisfactory-Instanzen fuer den server-Parameter."""
        treffer = []
        for i, sid in enumerate(self._boards(), start=1):
            name = self._board_name(sid, i)
            if current.lower() in sid.lower() or current.lower() in name.lower():
                treffer.append(app_commands.Choice(name=name, value=sid))
        return treffer[:25]

    @todo.command(name="add", description="Neuen Eintrag anlegen")
    @app_commands.describe(
        text="Was soll gebaut werden?",
        server="Fuer welchen Server (leer = erster)",
    )
    @app_commands.autocomplete(server=_server_autocomplete)
    @spieler_only()
    async def todo_add(
        self,
        interaction: discord.Interaction,
        text: str,
        server: Optional[str] = None,
    ) -> None:
        text = text.strip()
        if not text:
            await interaction.response.send_message(
                "Der Text darf nicht leer sein.", ephemeral=True
            )
            return
        if len(text) > MAX_TEXT_LEN:
            await interaction.response.send_message(
                f"Maximal {MAX_TEXT_LEN} Zeichen (deiner hat {len(text)}).",
                ephemeral=True,
            )
            return

        boards = self._boards()
        if server and server not in boards:
            await interaction.response.send_message(
                f"Unbekannter Server: `{discord.utils.escape_markdown(server)}`. "
                f"Verfuegbar: {', '.join(boards)}",
                ephemeral=True,
            )
            return

        ziel = server or self._erstes_board()
        new_id = await self.add_todo(text, interaction.user, ziel)
        name = self._board_name(ziel, boards.index(ziel) + 1)
        await interaction.response.send_message(
            f"Eintrag `#{new_id}` angelegt — {name}.", ephemeral=True
        )
        await self.refresh_board()

    @todo.command(name="edit", description="Eigenen Eintrag aendern")
    @app_commands.describe(nummer="Nummer des Eintrags", text="Neuer Text")
    @spieler_only()
    async def todo_edit(
        self, interaction: discord.Interaction, nummer: int, text: str
    ) -> None:
        row = await self._fetch_todo(nummer)
        if row is None:
            await interaction.response.send_message(
                f"Eintrag `#{nummer}` gibt es nicht.", ephemeral=True
            )
            return
        if not self._may_modify(interaction, row):
            await interaction.response.send_message(
                f"`#{nummer}` gehoert {_fmt_user(row, 'created_by_name')} — "
                f"nur der Ersteller kann ihn aendern.",
                ephemeral=True,
            )
            return

        text = text.strip()
        if not text or len(text) > MAX_TEXT_LEN:
            await interaction.response.send_message(
                f"Text muss zwischen 1 und {MAX_TEXT_LEN} Zeichen lang sein.",
                ephemeral=True,
            )
            return

        conn = await get_db()
        await conn.execute(
            "UPDATE todos SET text = ?, edited_at = ? WHERE id = ?",
            (text, datetime.now().isoformat(), nummer),
        )
        await conn.commit()

        await interaction.response.send_message(
            f"Eintrag `#{nummer}` geaendert.", ephemeral=True
        )
        await self.refresh_board()

    @todo.command(name="remove", description="Eigenen Eintrag loeschen")
    @app_commands.describe(nummer="Nummer des Eintrags")
    @spieler_only()
    async def todo_remove(self, interaction: discord.Interaction, nummer: int) -> None:
        row = await self._fetch_todo(nummer)
        if row is None:
            await interaction.response.send_message(
                f"Eintrag `#{nummer}` gibt es nicht.", ephemeral=True
            )
            return
        if not self._may_modify(interaction, row):
            await interaction.response.send_message(
                f"`#{nummer}` gehoert {_fmt_user(row, 'created_by_name')} — "
                f"nur der Ersteller kann ihn loeschen.",
                ephemeral=True,
            )
            return

        conn = await get_db()
        await conn.execute("DELETE FROM todos WHERE id = ?", (nummer,))
        await conn.commit()

        await interaction.response.send_message(
            f"Eintrag `#{nummer}` geloescht.", ephemeral=True
        )
        await self.refresh_board()

    @todo.command(name="clear", description="Alle erledigten Eintraege loeschen")
    @admin_only()
    async def todo_clear(self, interaction: discord.Interaction) -> None:
        geloescht = await self.clear_done()
        await interaction.response.send_message(
            f"{geloescht} erledigte Eintraege geloescht.", ephemeral=True
        )
        await self.refresh_board()

    # ------------------------------------------------------------------
    # Autocomplete fuer edit/remove — zeigt nur was der Nutzer aendern darf
    # ------------------------------------------------------------------

    async def _own_todo_choices(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[int]]:
        rows = await self._fetch_todos()
        admin = is_admin(interaction)
        out: List[app_commands.Choice[int]] = []
        for r in rows:
            if not admin and r["created_by"] != str(interaction.user.id):
                continue
            label = f"#{r['id']} — {r['text']}"
            if current and current.lower() not in label.lower():
                continue
            out.append(app_commands.Choice(name=label[:100], value=r["id"]))
            if len(out) >= 25:
                break
        return out

    @todo_edit.autocomplete("nummer")
    async def edit_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._own_todo_choices(interaction, current)

    @todo_remove.autocomplete("nummer")
    async def remove_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self._own_todo_choices(interaction, current)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ToDoCog(bot))
