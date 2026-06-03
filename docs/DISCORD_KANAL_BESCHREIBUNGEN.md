# Discord-Kanal-Beschreibungen (Topics) — Vorlage zum Aktualisieren

> Ziel: Jeder Kanal bekommt ein klares Topic, damit Mitglieder sehen **was geht**. Copy-Paste direkt ins Kanal-Topic (Zahnrad → Kanal bearbeiten → Thema). Bot-Features sind aus den echten Cogs abgeleitet.
> Discord-Topic-Limit: **1024 Zeichen** — die Kurz-Topics passen locker.

---

## 📡 STATUS-SERVER

| Kanal | Topic (Copy-Paste) |
|---|---|
| `live-server-status` | 📊 Live-Status aller Gameserver (Satisfactory, Minecraft BMC5 & Vanilla) — Online/Offline, Spielerzahl, Performance. Auto-aktualisiert vom Bot, nur lesen. |
| 🔊 `SAT-1` | Live-Anzeige Satisfactory-Server: Spieler online / Slots. Name aktualisiert sich automatisch. |
| 🔊 `MC-BMC` | Live-Anzeige Minecraft **Better MC 5** (Modpack): Online-Status & Spielerzahl im Kanalnamen. |
| 🔊 `MC-VANILLA` | Live-Anzeige Minecraft **Vanilla/Paper**: Online-Status & Spielerzahl im Kanalnamen. |

---

## ℹ️ INFOS

| Kanal | Topic |
|---|---|
| `willkommen` | 👋 Automatische Begrüßung neuer Mitglieder. Schau ins #regelwerk und hol dir deine Rollen. |
| `regelwerk` | 📜 Serverregeln + **Rollen per Reaktion**: klick die Emojis um Spiel-/Benachrichtigungs-Rollen zu bekommen. |
| `news` | 📰 Server-Ankündigungen, Updates & Events. Benachrichtigungen über die News-Rolle (#regelwerk). |

---

## 💬 CHATS

| Kanal | Topic |
|---|---|
| `chat` | 💬 Haupt-Chat. Hier sammelst du **XP & Level** beim Schreiben — Fortschritt mit `/rank`, Karte mit `/levelcard`, Rangliste `/leaderboard`. |
| `bot-spam` | 🤖 Spam-Zone für Bot-Ausgaben & längere Command-Tests. Hier nervt niemanden was. |
| `bilder-chat` | 🖼️ Nur Bilder & Screenshots. Quatsch dazu bitte in #chat. |
| `memes` | 😂 Memes rein, lachen raus. |
| `bot-commands` | ⌨️ Slash-Commands des Bots: `/help`, `/profil`, `/rank`, `/leaderboard`, `/levelcard`, Server-Infos `/mcstats` & `/world`. |
| `factorysatis` | 🏭 Alles rund um den **Satisfactory**-Server: Bauten, Logistik, Updates, Mitspieler. |
| `bmc-chat-bridge` | 🌉 **2-Wege-Chat-Bridge** zum Minecraft **BMC5**-Server: was du hier schreibst landet in-game — und umgekehrt. |
| `mc-chat-bridge` | 🌉 **2-Wege-Chat-Bridge** zum Minecraft **Vanilla**-Server: Discord ↔ In-Game-Chat. |

---

## 🎮 GAMING

| Kanal | Topic |
|---|---|
| `euer-setup` | 🖥️ Zeig dein Gaming-Setup: PC, Peripherie, Zimmer. |
| `eure-clips` | 🎬 Deine besten Clips & Highlights. |
| `spielersuche` | 🔎 Mitspieler suchen (LFG). Schreib welches Spiel, Uhrzeit & wie viele — dann ab in einen Voice (Join2Create). |
| `lieblings-games` | 🕹️ Empfehlungen & Diskussion über eure Lieblingsspiele. |

---

## 🔊 TALKS (Voice)

| Kanal | Topic / Funktion |
|---|---|
| 🔊 `Chill Lounge` | Offener Voice zum Quatschen & Chillen. |
| 🔊 `Homie's Ecke` | Voice für die Stammcrew. |
| 🔊 **`Join2Create`** | ⭐ **Eigenen Voice erstellen:** rein joinen → du bekommst automatisch deinen eigenen Kanal + Kontroll-Panel (umbenennen, Limit, sperren, verstecken, User erlauben/kicken …). Leer = wird automatisch gelöscht. |
| 🔊 `RB ranked` | Voice für Ranked-Sessions (z. B. Rainbow/COD). |
| 🔊 `jakoduspako's Channel` | Beispiel für einen via **Join2Create** erstellten Temp-Voice. |

---

## 🔧 TEAM (nur Team/Admin)

| Kanal | Topic |
|---|---|
| `logs` | 🛠️ Bot- & Moderations-Logs (Joins/Leaves, Mod-Aktionen, Befehle). Team-intern. |
| `logs-satis-mc` | 🛠️ Server-Logs Satisfactory & Minecraft (Updates, Crashes, Backups, Auto-Restart). |
| `teamchat` | 💼 Interne Team-Absprachen. |
| 🔊 `Team Talk` | Team-interner Voice. |

---

## 🔒 „Mein Bereich" (privat — Marcos Content-Pipeline)

> Diese Kanäle gehören zu deiner **persönlichen n8n/Claude-Content-Pipeline** (Kategorien = `CATEGORY_CHOICES` aus `pipeline_approval_cog`). **Nicht mitglieder-relevant** — hier brauchst du i. d. R. keine öffentlichen Topics. Falls du sie doch labeln willst, interne Kurz-Beschreibungen:

| Kanal | Interne Funktion |
|---|---|
| `pipelineceo` | Pipeline-Steuerung / CEO-Approval-Stufe. |
| `pipeline-ops` | Pipeline-Betrieb / Logs. |
| `claude-notifications` | Claude-Code-Benachrichtigungen (Approval-Gates, Phasen). |
| `claude-code-tips` | Pipeline-Kategorie: Claude-Code-Tipps. |
| `tech-tools` / `technik` / `smart-home` | Pipeline-Kategorien Technik. |
| `cod-loadouts` / `cod-zombies` / `cod-warzone` / `cod-techniques` / `cod-settings` | Pipeline-Kategorien COD. |
| `essen` / `anime` / `fitness` / `finanzen` / `haushalt` | Pipeline-Kategorien Lifestyle. |
| `tiktok-other` | Pipeline-Kategorie „Other". |

---

## 💡 Optional: „Was kann der Bot?"-Übersicht für #willkommen oder #regelwerk

```
🤖 **Was unser Bot kann**

📊 Server-Status — Satisfactory & Minecraft live in #live-server-status, Details mit /mcstats, /world
🌉 Chat-Bridges — schreib aus Discord direkt in den MC-Server (#bmc-chat-bridge, #mc-chat-bridge)
⭐ Level-System — schreib in #chat, sammel XP. Check /rank, /levelcard, /leaderboard
👤 Profil — /profil zeigt deine Stats; /spieler_leaderboard die Server-Bestenliste
🔊 Eigene Voices — geh in „Join2Create" und du bekommst deinen eigenen Kanal zum Verwalten
🎭 Rollen — hol dir Rollen per Reaktion in #regelwerk
❓ Hilfe — /help zeigt alle Befehle
```
