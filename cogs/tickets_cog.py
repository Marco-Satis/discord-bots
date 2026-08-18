"""
Ticket Cog — Support-Ticket-System für den Admin Bot

Features:
  - Support-Embed mit interaktivem "Ticket erstellen" Button
  - Private Ticket-Channels mit individuellen Berechtigungen
  - Automatisches Transcript-Logging aller Nachrichten
  - Ticket schliessen mit Transcript-Export in Log-Channel
  - Persistente Views (überleben Bot-Neustarts)

Command-Struktur:
  /ticket setup <channel>    — Support-Embed posten (Admin)
  /ticket close [grund]      — Aktuelles Ticket schliessen (im Ticket-Channel)
  /ticket list               — Alle offenen Tickets anzeigen (Admin)

Interaktive Elemente:
  - "Ticket erstellen" Button (persistent, custom_id basiert)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import io

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.tickets import TicketManager
from utils.logger import get_logger
from utils.config import ADMIN_DATA_DIR, get_config
from utils.permissions import admin_only
from utils.embeds import (
    error_embed,
    info_embed,
    neutral_embed,
)

logger = get_logger("cogs.tickets")

# Maximale offene Tickets pro User (Spam-Schutz)
MAX_OPEN_TICKETS_PER_USER = 3


# ======================================================================
# Persistente Views — überleben Bot-Neustarts
# ======================================================================

# Die zwei Ticket-Arten. Getrennt, weil sie unterschiedlich beantwortet werden:
# eine Frage braucht eine Antwort, ein Fehlerbericht braucht Schritte zum
# Nachstellen. Ein gemeinsames Formular fuer beides fragt immer die Haelfte
# zu viel oder zu wenig.
TICKET_ARTEN: dict[str, dict[str, str]] = {
    "allgemein": {
        "label": "Frage / Anliegen",
        "emoji": "\U0001f4ac",
        "kanal": "hilfe",
        "titel": "Frage oder Anliegen",
        "feld": "Worum geht es?",
        "platzhalter": "z.B. Wie komme ich auf den Satisfactory-Server?",
        "detail_label": "Beschreibung",
        "detail_platzhalter": "Erzaehl uns mehr — je genauer, desto schneller die Antwort.",
    },
    "bug": {
        "label": "Fehler melden",
        "emoji": "\U0001f41e",
        "kanal": "bug",
        "titel": "Fehler melden",
        "feld": "Was funktioniert nicht?",
        "platzhalter": "z.B. /sat status antwortet nicht",
        "detail_label": "Was hast du gemacht?",
        # Befund C-23 (Audit 2026-08-18): dieser Text war 108 Zeichen lang.
        # Discord erlaubt 100 und lehnt das ganze Formular ab — `/ticket` mit
        # der Art „Fehler melden" liess sich dadurch NIE oeffnen (HTTP 400,
        # error code 50035). Aufgefallen erst, als Marco es von Hand probierte.
        "detail_platzhalter": (
            "Was hast du getan, was ist passiert, was haettest du erwartet?"
        ),
    },
}
STANDARD_ART = "allgemein"


def _art_daten(art: str) -> dict[str, str]:
    """Metadaten einer Ticket-Art — unbekannte Art faellt auf 'allgemein'."""
    return TICKET_ARTEN.get(art, TICKET_ARTEN[STANDARD_ART])


class TicketCreateView(discord.ui.View):
    """
    Persistente View mit den Ticket-Knoepfen — einer je Art.

    Feste custom_ids (`ticket_system:create:<art>`), damit die Knoepfe einen
    Bot-Neustart ueberleben. Die View wird in cog_load je Art registriert.

    Die Art steckt im Knopf und nicht in einem Auswahlfeld im Formular: wer auf
    "Fehler melden" drueckt, hat die Frage schon beantwortet.
    """

    def __init__(self, art: str = STANDARD_ART) -> None:
        # timeout=None für persistente Views (kein Ablauf)
        super().__init__(timeout=None)
        self.art = art if art in TICKET_ARTEN else STANDARD_ART
        daten = _art_daten(self.art)
        knopf = discord.ui.Button(
            label=daten["label"],
            style=(discord.ButtonStyle.danger if self.art == "bug"
                   else discord.ButtonStyle.primary),
            custom_id=f"ticket_system:create:{self.art}",
            emoji=daten["emoji"],
        )
        knopf.callback = self._knopf_gedrueckt
        self.add_item(knopf)

    async def _knopf_gedrueckt(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TicketCreateModal(self.art))


class TicketCreateModal(discord.ui.Modal):
    """
    Formular fuer die Ticket-Erstellung — Beschriftung je nach Art.

    Bei einem Fehlerbericht wird ausdruecklich nach dem Weg dorthin gefragt.
    Ohne diese Frage kommen Meldungen wie "geht nicht", und dann beginnt die
    Arbeit mit einer Rueckfrage statt mit der Ursache.
    """

    def __init__(self, art: str = STANDARD_ART) -> None:
        self.art = art if art in TICKET_ARTEN else STANDARD_ART
        daten = _art_daten(self.art)
        super().__init__(title=daten["titel"])

        self.subject = discord.ui.TextInput(
            label=daten["feld"],
            placeholder=daten["platzhalter"][:100],
            style=discord.TextStyle.short,
            max_length=100,
            required=True,
        )
        self.description = discord.ui.TextInput(
            label=daten["detail_label"],
            # Guertel zum Hosentraeger: Discord lehnt das GANZE Formular ab,
            # wenn ein Platzhalter zu lang ist. Lieber ein gekuerzter Hinweis
            # als ein Dialog, der sich nicht oeffnet (C-23).
            placeholder=daten["detail_platzhalter"][:100],
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=(self.art == "bug"),
        )
        self.add_item(self.subject)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Modal abgeschickt — Ticket wird erstellt."""
        # Cog-Referenz über den Bot holen
        cog: TicketsCog | None = interaction.client.get_cog("TicketsCog")
        if not cog:
            await interaction.response.send_message(
                "Ticket-System ist nicht verfügbar.", ephemeral=True
            )
            return

        await cog.handle_ticket_creation(
            interaction,
            subject=self.subject.value,
            description=self.description.value or None,
            art=self.art,
        )


class TicketCloseView(discord.ui.View):
    """
    Persistente View mit dem "Ticket schliessen" Button.

    Wird in jedem Ticket-Channel als Willkommensnachricht angezeigt.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket schliessen",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_system:close_ticket",
        emoji="\U0001f512",
    )
    async def close_ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Button-Handler: Ticket schliessen."""
        cog: TicketsCog | None = interaction.client.get_cog("TicketsCog")
        if not cog:
            await interaction.response.send_message(
                "Ticket-System ist nicht verfügbar.", ephemeral=True
            )
            return

        await cog.handle_ticket_close(interaction, reason=None)

    @discord.ui.button(
        label="Merken",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_system:bookmark_ticket",
        emoji="\U0001f516",
    )
    async def bookmark_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Button-Handler: Lesezeichen an oder aus.

        Ein gemerktes Ticket wird weder nach sieben stillen Tagen geschlossen
        noch sein Kanal als verwaist entfernt — fuer Anliegen, die laenger
        dauern oder die man behalten will.
        """
        cog: TicketsCog | None = interaction.client.get_cog("TicketsCog")
        if not cog:
            await interaction.response.send_message(
                "Ticket-System ist nicht verfügbar.", ephemeral=True
            )
            return

        await cog.handle_ticket_bookmark(interaction)


# ======================================================================
# Ticket Cog
# ======================================================================

class TicketsCog(commands.Cog):
    """Support-Ticket-System mit interaktiven Buttons und Transcripts"""

    # Slash-Command-Gruppe
    ticket_grp = app_commands.Group(
        name="ticket",
        description="Support-Ticket-System",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ticket_mgr = TicketManager(
            data_file=ADMIN_DATA_DIR / "tickets.json",
            config_file=ADMIN_DATA_DIR / "ticket_config.json",
        )
        # Aufraeum-Regeln (Marco-Auftrag 18.08.). Beide Werte stehen im
        # `tickets`-Block der config.json; 0 schaltet den jeweiligen Teil ab.
        _cfg = get_config().get("tickets", {})
        self._auto_close_tage: int = int(_cfg.get("auto_close_after_days", 7))
        self._verwaist_stunden: int = int(_cfg.get("orphan_channel_after_hours", 24))

    async def cog_load(self) -> None:
        """Persistente Views beim Laden registrieren und DB-Daten laden."""
        # Views muessen beim Bot registriert werden damit sie
        # nach einem Neustart wieder funktionieren
        # Je Art eine View registrieren, sonst antwortet der Knopf der zweiten
        # Art nach einem Neustart nicht mehr.
        for _art in TICKET_ARTEN:
            self.bot.add_view(TicketCreateView(_art))
        self.bot.add_view(TicketCloseView())
        # Tickets aus SQLite laden
        try:
            await self.ticket_mgr.load_from_db()
            logger.info("Ticket-Daten aus SQLite geladen")
        except Exception as e:
            logger.warning(f"SQLite-Load fehlgeschlagen: {e}")
        self._aufraeumen.start()
        logger.info(
            "Ticket-Cog geladen, persistente Views registriert — Aufraeumen: "
            "offene Tickets nach %d Tagen ohne Aktivitaet, verwaiste Kanaele "
            "nach %d Stunden",
            self._auto_close_tage, self._verwaist_stunden)

    async def cog_unload(self) -> None:
        self._aufraeumen.cancel()

    # ==================================================================
    # Aufraeumen (Marco-Auftrag 18.08.)
    # ==================================================================

    @tasks.loop(hours=1)
    async def _aufraeumen(self) -> None:
        """Alte Tickets schliessen und liegengebliebene Kanaele entfernen.

        Zwei Faelle, beide bisher unbehandelt:

        1. **Offene Tickets, in denen nichts mehr passiert.** Sie blieben
           unbegrenzt stehen — jeder erzeugt einen Kanal, und die Liste wuchs.
        2. **Verwaiste Kanaele.** Beim Schliessen wird der Kanal geloescht;
           scheitert der Schritt davor (bis 18.08. der Fall, siehe C-24),
           bleibt der Kanal fuer immer stehen, obwohl das Ticket in der
           Datenbank geschlossen ist.

        Das Transkript ist in beiden Faellen vorher im Log-Kanal gesichert —
        geloescht wird der Kanal, nicht der Vorgang.
        """
        try:
            await self._alte_offene_schliessen()
            await self._verwaiste_kanaele_entfernen()
        except Exception as e:  # noqa: BLE001 — Aufraeumen darf den Bot nie stoppen
            logger.error(f"Ticket-Aufraeumen fehlgeschlagen: {e}", exc_info=True)

    @_aufraeumen.before_loop
    async def _vor_aufraeumen(self) -> None:
        await self.bot.wait_until_ready()
        # Nicht direkt beim Start: erst sollen die Daten geladen sein.
        await asyncio.sleep(120)

    def _letzte_aktivitaet(self, ticket: dict) -> datetime:
        """Zeitpunkt der letzten Nachricht, sonst der Erstellung."""
        zeiten = [ticket.get("created_at") or ""]
        zeiten += [e.get("timestamp", "") for e in ticket.get("transcript", [])]
        neueste = max((z for z in zeiten if z), default="")
        try:
            return datetime.fromisoformat(neueste)
        except ValueError:
            return datetime.now()

    async def _alte_offene_schliessen(self) -> None:
        if self._auto_close_tage <= 0:
            return
        grenze = datetime.now() - timedelta(days=self._auto_close_tage)

        for ticket in self.ticket_mgr.get_open_tickets():
            if ticket.get("gemerkt"):
                continue  # Lesezeichen: bleibt stehen, bis jemand es freigibt
            if self._letzte_aktivitaet(ticket) > grenze:
                continue

            tid = ticket.get("ticket_id") or ticket.get("id")
            kanal = self.bot.get_channel(int(ticket.get("channel_id") or 0))
            logger.info(
                "Ticket #%s wird automatisch geschlossen: seit %d Tagen keine "
                "Aktivitaet", tid, self._auto_close_tage)

            if kanal is not None:
                try:
                    await kanal.send(embed=info_embed(
                        title="Ticket wird geschlossen",
                        description=(
                            f"Seit {self._auto_close_tage} Tagen ist hier nichts "
                            "mehr passiert. Das Ticket wird jetzt automatisch "
                            "geschlossen und der Kanal entfernt.\n\n"
                            "Wenn das Anliegen offen ist, mach einfach ein neues "
                            "Ticket auf — der Verlauf bleibt im Team-Protokoll."),
                    ))
                except (discord.Forbidden, discord.HTTPException):
                    pass

            geschlossen = await self.ticket_mgr.close_ticket(
                int(tid), closed_by=self.bot.user.id,
                reason=f"automatisch nach {self._auto_close_tage} Tagen ohne Aktivitaet")
            if not geschlossen:
                continue

            gilde = kanal.guild if kanal is not None else None
            if gilde is not None:
                await self._send_transcript_log(
                    gilde, geschlossen,
                    self.ticket_mgr.format_transcript(int(tid)), gilde.me)

            await self._kanal_entfernen(kanal, tid, "automatisch geschlossen")

    async def _verwaiste_kanaele_entfernen(self) -> None:
        """Kanaele geschlossener Tickets, die noch stehen (C-24-Folge)."""
        if self._verwaist_stunden <= 0:
            return
        grenze = datetime.now() - timedelta(hours=self._verwaist_stunden)

        for ticket in self.ticket_mgr.alle_tickets():
            if ticket.get("status") == "open":
                continue
            if ticket.get("gemerkt"):
                continue  # gemerkte Vorgaenge behalten ihren Kanal
            kanal_id = int(ticket.get("channel_id") or 0)
            if not kanal_id:
                continue
            kanal = self.bot.get_channel(kanal_id)
            if kanal is None:
                continue  # laengst weg, alles gut

            zu = ticket.get("closed_at") or ""
            try:
                if zu and datetime.fromisoformat(zu) > grenze:
                    continue
            except ValueError:
                pass

            tid = ticket.get("ticket_id") or ticket.get("id")
            logger.warning(
                "Ticket #%s ist geschlossen, sein Kanal stand aber noch — "
                "wird entfernt", tid)
            await self._kanal_entfernen(kanal, tid, "verwaister Ticket-Kanal")

    async def _kanal_entfernen(self, kanal, tid, grund: str) -> None:
        if kanal is None:
            return
        try:
            await kanal.delete(reason=f"Ticket #{tid}: {grund}")
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Ticket-Kanal #{tid} loeschen fehlgeschlagen: {e}")

    # ==================================================================
    # Event: Nachrichten in Ticket-Channels tracken
    # ==================================================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Nachrichten in Ticket-Channels zum Transcript hinzufuegen.

        Ueberspringt Bot-Nachrichten und DMs.
        """
        if message.author.bot:
            return
        if not message.guild:
            return

        # Pruefen ob die Nachricht in einem Ticket-Channel ist
        ticket = self.ticket_mgr.get_ticket_by_channel(message.channel.id)
        if not ticket:
            return
        if ticket.get("status") != "open":
            return

        # Nachricht zum Transcript hinzufuegen
        content = message.content or ""

        # Anhaenge als Text anhaengen
        if message.attachments:
            attachment_urls = [a.url for a in message.attachments]
            if content:
                content += "\n"
            content += "[Anhaenge: " + ", ".join(attachment_urls) + "]"

        if content:
            await self.ticket_mgr.add_transcript_entry(
                ticket["ticket_id"],
                author=message.author.display_name,
                content=content,
                author_id=message.author.id,
            )

    # ==================================================================
    # Ticket-Erstellung (von Modal aufgerufen)
    # ==================================================================

    async def handle_ticket_creation(
        self,
        interaction: discord.Interaction,
        subject: str,
        description: str | None = None,
        art: str = STANDARD_ART,
    ) -> None:
        """
        Ticket erstellen: Channel anlegen, Berechtigungen setzen, Willkommen senden.

        Args:
            interaction: Discord-Interaction (vom Modal)
            subject: Ticket-Betreff
            description: Optionale ausfuehrliche Beschreibung
        """
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild:
            await interaction.followup.send(
                "Tickets können nur auf einem Server erstellt werden.",
                ephemeral=True,
            )
            return

        user = interaction.user

        # Spam-Schutz: Maximale Anzahl offener Tickets prüfen
        open_count = self.ticket_mgr.get_user_ticket_count(user.id)
        if open_count >= MAX_OPEN_TICKETS_PER_USER:
            await interaction.followup.send(
                f"Du hast bereits {open_count} offene Tickets. "
                f"Bitte schliesse ein Ticket bevor du ein neues erstellst.",
                ephemeral=True,
            )
            return

        # Ticket im Manager erstellen
        ticket = await self.ticket_mgr.create_ticket(
            user_id=user.id,
            subject=subject,
            art=art,
        )
        ticket_id = ticket["ticket_id"]

        # Kategorie für Ticket-Channels ermitteln
        category = None
        cat_id = self.ticket_mgr.ticket_category_id
        if cat_id:
            category = guild.get_channel(cat_id)
            if category and not isinstance(category, discord.CategoryChannel):
                category = None

        # Berechtigungen setzen
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False,
            ),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
            ),
        }

        # Support-Rollen Zugriff geben
        for role_id in self.ticket_mgr.support_roles:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )

        # Channel erstellen
        # Die Art steht im Kanalnamen: 'bug-0007' sagt dem Team schon in der
        # Kanalliste, was es erwartet. Die verbindliche Zuordnung steht aber in
        # der Datenbank, nicht hier — Kanaele werden umbenannt.
        channel_name = f"{_art_daten(art)['kanal']}-{ticket_id:04d}"
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Ticket #{ticket_id} — {subject} — von {user.display_name}",
                reason=f"Support-Ticket #{ticket_id} von {user.display_name}",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Ticket-Channel erstellen fehlgeschlagen: {e}")
            await interaction.followup.send(
                "Ticket-Channel konnte nicht erstellt werden. "
                "Bitte kontaktiere einen Admin.",
                ephemeral=True,
            )
            return

        # Channel-ID im Ticket speichern
        await self.ticket_mgr.update_ticket_channel(ticket_id, channel.id)

        # Willkommens-Embed senden
        embed = info_embed(
            title=f"Ticket #{ticket_id}",
            description=(
                f"**Betreff:** {subject}\n\n"
                f"Willkommen in deinem Support-Ticket, {user.mention}!\n"
                f"Ein Teammitglied wird sich in Kuerze um dein Anliegen kuemmern.\n\n"
                f"Beschreibe dein Problem so detailliert wie möglich."
            ),
        )
        embed.set_footer(text=f"Ticket erstellt von {user.display_name}")

        if description:
            embed.add_field(
                name="Beschreibung",
                value=description,
                inline=False,
            )

        # Willkommen-Nachricht mit Schliessen-Button senden
        close_view = TicketCloseView()
        try:
            await channel.send(embed=embed, view=close_view)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Willkommens-Embed senden fehlgeschlagen: {e}")

        # Beschreibung als ersten Transcript-Eintrag speichern
        if description:
            await self.ticket_mgr.add_transcript_entry(
                ticket_id,
                author=user.display_name,
                content=description,
                author_id=user.id,
            )

        # Support-Rollen erwaehnen (in separater Nachricht, damit Embed sauber bleibt)
        support_mentions: list[str] = []
        for role_id in self.ticket_mgr.support_roles:
            role = guild.get_role(role_id)
            if role:
                support_mentions.append(role.mention)

        if support_mentions:
            try:
                ping_msg = await channel.send(
                    f"Neues Ticket von {user.mention} — "
                    + " ".join(support_mentions),
                    # Gewollt: User + Support-Rollen pingen. everyone=False schuetzt
                    # vor versehentlichem @everyone/@here (z.B. missconfig'd Rolle).
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, users=True, roles=True
                    ),
                )
                # Ping-Nachricht nach kurzer Zeit löschen (optisch sauberer)
                await asyncio.sleep(2)
                try:
                    await ping_msg.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass
            except (discord.Forbidden, discord.HTTPException):
                pass

        # Bestaetigung an den User
        await interaction.followup.send(
            f"Dein Ticket wurde erstellt: {channel.mention}",
            ephemeral=True,
        )

        logger.info(
            f"Ticket #{ticket_id} erstellt: {channel_name} "
            f"von {user.display_name} — {subject}"
        )

    # ==================================================================
    # Ticket schliessen (von Button oder Command aufgerufen)
    # ==================================================================

    async def handle_ticket_bookmark(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """
        Lesezeichen des Tickets umschalten.

        Gemerkte Tickets nimmt `_aufraeumen` von beiden Automatiken aus. Das
        Lesezeichen ist bewusst ein Schalter und keine Frist: wer es setzt,
        entscheidet selbst, wann der Vorgang erledigt ist.
        """
        await interaction.response.defer(ephemeral=True)

        ticket = self.ticket_mgr.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            await interaction.followup.send(
                "Dieser Channel ist kein Ticket-Channel.", ephemeral=True
            )
            return

        neu = not ticket.get("gemerkt")
        aktualisiert = await self.ticket_mgr.merken(
            int(ticket["ticket_id"]), neu, von=interaction.user.id
        )
        if aktualisiert is None:
            await interaction.followup.send(
                "Lesezeichen konnte nicht gespeichert werden — Details im Log.",
                ephemeral=True,
            )
            return

        if neu:
            text = (
                f"Ticket #{ticket['ticket_id']} ist gemerkt. Es wird nicht mehr "
                f"automatisch geschlossen und der Kanal bleibt stehen, bis du "
                f"das Lesezeichen wieder entfernst."
            )
        else:
            text = (
                f"Lesezeichen von Ticket #{ticket['ticket_id']} entfernt. Es "
                f"wird wieder automatisch geschlossen, wenn "
                f"{self._auto_close_tage} Tage lang nichts passiert."
            )
        await interaction.followup.send(text, ephemeral=True)

        # Sichtbar im Kanal, damit es niemanden ueberrascht, wenn das Ticket
        # stehen bleibt (oder eben doch verschwindet).
        try:
            await interaction.channel.send(embed=info_embed(
                title="Ticket gemerkt" if neu else "Lesezeichen entfernt",
                description=(
                    f"{interaction.user.mention} hat dieses Ticket "
                    + ("gemerkt — es bleibt offen, bis jemand es schliesst."
                       if neu else
                       "freigegeben — die Aufraeum-Automatik greift wieder.")
                ),
            ))
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def handle_ticket_close(
        self,
        interaction: discord.Interaction,
        reason: str | None = None,
    ) -> None:
        """
        Ticket schliessen: Transcript sichern, Log senden, Channel löschen.

        Args:
            interaction: Discord-Interaction
            reason: Optionaler Grund für das Schliessen
        """
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        # Pruefen ob wir in einem Ticket-Channel sind
        ticket = self.ticket_mgr.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            await interaction.followup.send(
                "Dieser Channel ist kein Ticket-Channel.",
                ephemeral=True,
            )
            return

        if ticket.get("status") == "closed":
            await interaction.followup.send(
                "Dieses Ticket ist bereits geschlossen.",
                ephemeral=True,
            )
            return

        ticket_id = ticket["ticket_id"]
        closed_by = interaction.user

        # Ticket im Manager schliessen
        closed_ticket = await self.ticket_mgr.close_ticket(
            ticket_id,
            closed_by=closed_by.id,
            reason=reason,
        )

        if not closed_ticket:
            await interaction.followup.send(
                "Ticket konnte nicht geschlossen werden.",
                ephemeral=True,
            )
            return

        # Transcript formatieren
        transcript_text = self.ticket_mgr.format_transcript(ticket_id)

        # Transcript an Log-Channel senden
        await self._send_transcript_log(
            interaction.guild,
            closed_ticket,
            transcript_text,
            closed_by,
        )

        # Bestaetigung im Ticket-Channel
        embed = error_embed(
            title="Ticket geschlossen",
            description=(
                f"Dieses Ticket wurde von {closed_by.mention} geschlossen."
                + (f"\n**Grund:** {reason}" if reason else "")
                + "\n\nDer Channel wird in 5 Sekunden gelöscht."
            ),
        )

        try:
            await interaction.channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

        await interaction.followup.send(
            "Ticket wird geschlossen...", ephemeral=True
        )

        # Channel nach Verzoegerung löschen
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(
                reason=f"Ticket #{ticket_id} geschlossen von {closed_by.display_name}"
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(
                f"Ticket-Channel löschen fehlgeschlagen (#{ticket_id}): {e}"
            )

        logger.info(
            f"Ticket #{ticket_id} geschlossen von {closed_by.display_name}"
            + (f" — Grund: {reason}" if reason else "")
        )

    async def _send_transcript_log(
        self,
        guild: discord.Guild,
        ticket: dict,
        transcript_text: str,
        closed_by: discord.Member | discord.User,
    ) -> None:
        """
        Transcript als Embed + Textdatei an den Log-Channel senden.

        Args:
            guild: Discord-Guild
            ticket: Ticket-Datensatz
            transcript_text: Formatierter Transcript-Text
            closed_by: User der das Ticket geschlossen hat
        """
        log_channel_id = self.ticket_mgr.log_channel_id
        if not log_channel_id:
            return

        log_channel = guild.get_channel(log_channel_id)
        if not log_channel or not isinstance(log_channel, discord.TextChannel):
            logger.warning(
                f"Log-Channel {log_channel_id} nicht gefunden oder kein Text-Channel"
            )
            return

        ticket_id = ticket.get("ticket_id", "?")
        user_id = ticket.get("user_id", 0)
        subject = ticket.get("subject", "Kein Betreff")
        created_at = ticket.get("created_at", "?")
        transcript_entries = ticket.get("transcript", [])

        # Erstelldatum formatieren
        try:
            dt = datetime.fromisoformat(created_at)
            created_at = dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            pass

        # Log-Embed
        embed = neutral_embed(
            title=f"Ticket #{ticket_id} — Transcript",
        )
        embed.add_field(name="Betreff", value=subject, inline=True)
        embed.add_field(
            name="Erstellt von",
            value=f"<@{user_id}>",
            inline=True,
        )
        embed.add_field(name="Erstellt am", value=created_at, inline=True)
        embed.add_field(
            name="Geschlossen von",
            value=closed_by.mention,
            inline=True,
        )
        embed.add_field(
            name="Nachrichten",
            value=str(len(transcript_entries)),
            inline=True,
        )

        # Transcript als Datei anhaengen.
        #
        # Befund C-24 (Audit 2026-08-18): hier stand `fp=transcript_text.
        # encode("utf-8")`. `discord.File` behandelt `bytes` als PFADANGABE,
        # nicht als Inhalt — beim Schliessen eines Tickets ohne Nachrichten
        # versuchte es also die Datei „(Kein Transcript vorhanden)" zu oeffnen
        # und warf `FileNotFoundError`. Der Knopf „Ticket schliessen" endete
        # damit in einer Ausnahme, und der Nutzer sah nur „Interaktion
        # fehlgeschlagen". Aufgefallen bei Marcos Live-Probe.
        transcript_file = discord.File(
            fp=io.BytesIO(transcript_text.encode("utf-8")),
            filename=f"ticket-{ticket_id}-transcript.txt",
        )

        try:
            await log_channel.send(embed=embed, file=transcript_file)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.error(f"Transcript-Log senden fehlgeschlagen: {e}")

    # ==================================================================
    # /ticket setup <channel>
    # ==================================================================

    @ticket_grp.command(
        name="setup",
        description="Support-Embed mit Ticket-Button in einem Channel posten",
    )
    @app_commands.describe(
        channel="Channel in dem das Support-Embed gepostet wird",
        art="Welche Ticket-Art dieses Panel anbietet",
        kategorie="Kategorie für neue Ticket-Channels (optional)",
        log_channel="Channel für Transcript-Logs (optional)",
        support_rolle="Support-Rolle die Tickets sehen kann (optional)",
    )
    @app_commands.choices(art=[
        app_commands.Choice(name="Frage / Anliegen", value="allgemein"),
        app_commands.Choice(name="Fehler melden", value="bug"),
    ])
    @admin_only()
    async def ticket_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        art: str = STANDARD_ART,
        kategorie: Optional[discord.CategoryChannel] = None,
        log_channel: Optional[discord.TextChannel] = None,
        support_rolle: Optional[discord.Role] = None,
    ) -> None:
        """Support-Embed mit interaktivem Button posten und Konfiguration speichern."""
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send(
                "Dieser Befehl funktioniert nur auf einem Server.",
                ephemeral=True,
            )
            return

        # Konfiguration aktualisieren
        config_updates: dict = {}

        if kategorie:
            config_updates["ticket_category_id"] = kategorie.id
        if log_channel:
            config_updates["log_channel_id"] = log_channel.id
        if support_rolle:
            # Bestehende Rollen beibehalten und neue hinzufuegen
            existing_roles = self.ticket_mgr.support_roles
            if support_rolle.id not in existing_roles:
                existing_roles.append(support_rolle.id)
            config_updates["support_roles"] = existing_roles

        if config_updates:
            self.ticket_mgr.set_config(**config_updates)

        # Support-Embed erstellen — Text und Knopf richten sich nach der Art.
        daten = _art_daten(art)
        if art == "bug":
            beschreibung = (
                "Etwas funktioniert nicht wie erwartet?\n\n"
                "Klick unten und beschreib, **was du gemacht hast, was passiert "
                "ist und was du erwartet haettest**. Je genauer, desto eher "
                "laesst sich der Fehler nachstellen — und was sich nicht "
                "nachstellen laesst, laesst sich meist auch nicht beheben.\n\n"
                "Du bekommst einen eigenen Kanal, in dem nur du und das Team lesen."
            )
        else:
            beschreibung = (
                "Du hast eine Frage zum Spiel-Server oder zum Discord?\n\n"
                "Klick unten und schreib kurz, worum es geht. Du bekommst einen "
                "eigenen Kanal, in dem nur du und das Team lesen — dort meldet "
                "sich jemand, sobald er kann.\n\n"
                "**Ein Ticket je Anliegen**, dann geht nichts unter."
            )
        embed = info_embed(
            title=f"{daten['emoji']} {daten['titel']}",
            description=beschreibung,
        )
        embed.set_footer(text="Support-Ticket-System")

        # Persistente View erstellen
        view = TicketCreateView(art)

        try:
            await channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException) as e:
            await interaction.followup.send(
                f"Support-Embed konnte nicht gesendet werden: {e}",
                ephemeral=True,
            )
            return

        # Bestaetigung
        config_info: list[str] = [f"Support-Embed in {channel.mention} gepostet"]
        if kategorie:
            config_info.append(f"Ticket-Kategorie: {kategorie.name}")
        if log_channel:
            config_info.append(f"Log-Channel: {log_channel.mention}")
        if support_rolle:
            config_info.append(f"Support-Rolle: {support_rolle.name}")

        await interaction.followup.send(
            "\n".join(f"• {info}" for info in config_info),
            ephemeral=True,
        )

        logger.info(
            f"Ticket-Setup in #{channel.name} von {interaction.user.display_name}"
        )

    # ==================================================================
    # /ticket close [grund]
    # ==================================================================

    @ticket_grp.command(
        name="close",
        description="Aktuelles Ticket schliessen (nur in Ticket-Channels)",
    )
    @app_commands.describe(
        grund="Optionaler Grund für das Schliessen"
    )
    async def ticket_close(
        self,
        interaction: discord.Interaction,
        grund: Optional[str] = None,
    ) -> None:
        """Aktuelles Ticket schliessen (muss in einem Ticket-Channel ausgefuehrt werden)."""
        await self.handle_ticket_close(interaction, reason=grund)

    # ==================================================================
    # /ticket list
    # ==================================================================

    @ticket_grp.command(
        name="list",
        description="Alle offenen Tickets anzeigen",
    )
    @admin_only()
    async def ticket_list(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Alle offenen Tickets als Embed anzeigen."""
        await interaction.response.defer(ephemeral=True)

        open_tickets = self.ticket_mgr.get_open_tickets()

        if not open_tickets:
            await interaction.followup.send(
                "Keine offenen Tickets vorhanden.",
                ephemeral=True,
            )
            return

        embed = info_embed(
            title=f"Offene Tickets ({len(open_tickets)})",
        )

        for ticket in open_tickets[:20]:  # Maximal 20 im Embed
            ticket_id = ticket.get("ticket_id", "?")
            user_id = ticket.get("user_id", 0)
            subject = ticket.get("subject", "Kein Betreff")
            channel_id = ticket.get("channel_id", 0)
            created_at = ticket.get("created_at", "?")
            transcript_count = len(ticket.get("transcript", []))

            # Datum formatieren
            try:
                dt = datetime.fromisoformat(created_at)
                created_at = dt.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                pass

            # Channel-Mention oder Fallback
            channel_ref = f"<#{channel_id}>" if channel_id else "Kein Channel"

            embed.add_field(
                name=f"Ticket #{ticket_id} — {subject}",
                value=(
                    f"**Erstellt von:** <@{user_id}>\n"
                    f"**Channel:** {channel_ref}\n"
                    f"**Erstellt am:** {created_at}\n"
                    f"**Nachrichten:** {transcript_count}"
                ),
                inline=False,
            )

        if len(open_tickets) > 20:
            embed.set_footer(
                text=f"... und {len(open_tickets) - 20} weitere Tickets"
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================================================================
    # Fehlerbehandlung
    # ==================================================================

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Zentrale Fehlerbehandlung für alle Commands in dieser Cog."""
        if isinstance(error, app_commands.CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Keine Berechtigung für diesen Befehl.", ephemeral=True
                )
            return

        cmd_name = interaction.command.name if interaction.command else "unknown"
        logger.error(f"Command error in {cmd_name}: {error}", exc_info=True)

        try:
            msg = f"Ein Fehler ist aufgetreten: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as e:
            logger.debug(f"Exception swallowed (B110-refactor 3.1): {e}")


async def setup(bot: commands.Bot) -> None:
    """Cog zum Bot hinzufuegen und persistente Views registrieren."""
    await bot.add_cog(TicketsCog(bot))
