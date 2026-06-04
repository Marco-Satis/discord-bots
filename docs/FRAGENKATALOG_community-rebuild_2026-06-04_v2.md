# Fragenkatalog Community-Rebuild — v2 (2026-06-04)

> **Kontext:** Wave-2-Code-Batch (B1+B2, B3, E3, D4, F1, C1) ist gebaut, gereviewt
> (0 CRIT/HIGH) und **live auf Prod** (Schema v8). Alle **autonom machbaren Code-Items
> sind durch.** Was übrig ist, braucht **deine Entscheidung** — dieser Katalog macht die
> offenen Punkte beantwortbar (Optionen + meine Empfehlung). Antworte knapp pro Nummer
> (z.B. „A1: b · A2: eigene…"); danach baue ich autonom weiter.
>
> Vorgänger: `docs/FRAGENKATALOG_community-rebuild_2026-06-04.md` (v1, abgearbeitet).

---

## A — C2: LFG-System („Looking for Group", #spielersuche)

Strukturierte Gruppensuche: User wählt Spiel/Slots/Zeit → Button → erstellt/verlinkt einen
Temp-Voice (koppelt an A/Multi-Hub). Braucht inhaltliche Vorgaben von dir.

- **A1 — Spiel-Liste:** Welche Spiele in der Auswahl? (Dropdown-Choices)
  - a) Fester Katalog den du mir gibst (z.B. CoD, Satisfactory, Minecraft, …)
  - b) Generisch: Freitext-Spielname (keine feste Liste)
  - c) (Empfehlung) **Hybrid** — feste Top-Liste + „Sonstiges (Freitext)"
- **A2 — Felder beim LFG-Post:** Welche Angaben?
  - Vorschlag (Empfehlung): **Spiel · benötigte Slots (2-10) · Zeitpunkt (jetzt/HH:MM) · optional Kommentar**. Mehr/weniger?
- **A3 — Voice-Verknüpfung:**
  - a) (Empfehlung) Button „Voice beitreten" erstellt/verlinkt automatisch einen Temp-Voice (nutzt einen Multi-Hub mit `{game}`-Naming)
  - b) Nur Text-Post mit Ping an eine LFG-Rolle, kein Auto-Voice
- **A4 — Auto-Cleanup:** LFG-Post nach X Stunden / wenn Slots voll automatisch schließen? (Empfehlung: schließen wenn voll ODER nach 6h)
- **A5 — Wo:** Fester Channel `#spielersuche` (per Dashboard/ENV konfigurierbar)? (Empfehlung: ja, Dashboard-Key `lfg.channel`)

---

## B — RBAC (Dashboard-Rollen, Plan D12/E1) — security-kritisch

Refactort `web/auth.py` von Binär-Whitelist → Rollen→Permission-Mapping. **Ich baue erst
wenn das Rollen-Modell von dir steht** (sonst rate ich die Sicherheits-Grenzen).

- **B1 — Welche Rollen brauchst du konkret?** (Empfehlung als Startpunkt:)
  - `owner` (= du, alles)
  - `admin` (alles außer RBAC-Verwaltung + System-Restart)
  - `mod` (Moderation + Leveling-Config, KEINE Server-Control/Config)
  - `viewer` (read-only alles)
  - Brauchst du **per-Server-Admins** (z.B. „SAT-Admin sieht nur Satisfactory, read-only")? → eigene Achse
- **B2 — Permission-Modell:** `(Resource, Action ∈ {view, edit, control})` × Rolle, server-seitig erzwungen (Route-Deps) UND UI (Tabs/Buttons ausblenden). OK so? Oder simpler (nur view/edit)?
- **B3 — Verwaltung:** Rollen-Zuweisung pro User **im Dashboard** editierbar (DB-getrieben) — wer darf zuweisen? (Empfehlung: nur `owner`)
- **B4 — Audit-Log:** „Wer hat was im Dashboard geändert" (Pflicht sobald mehrere Rollen). Jetzt mitbauen? (Empfehlung: ja, gehört zu RBAC)
- **B5 — Login-Identität:** Wie werden Rollen an Personen gebunden? Discord-OAuth-User-ID (bestehend) → Rolle? (Empfehlung: ja, Map Discord-UID→Rolle)

---

## C — E: Music-Bot (Lavalink) — Server-Setup-Gate

Code-Guide steht (`docs/production/lavalink-setup.md`). Bevor ich `cogs/music_cog.py` baue,
braucht es den Lavalink-Service + Secrets (dein PW-Gate).

- **C1 — Setup jetzt angehen?** a) Ja, ich (Marco) richte Lavalink + Spotify-App ein → dann baust du den Cog · b) (Empfehlung wenn unsicher) Später, erst LFG/RBAC fertig
- **C2 — Spotify-App:** Client-ID/Secret aus developer.spotify.com — hast du die / legst du sie an? (gehören in KeePass + `/etc/lavalink/lavalink.env`)
- **C3 — YouTube-ToS-Restrisiko** (Self-hosted Playback, IP-Block möglich) — akzeptiert? (im Plan schon ja, nur Re-Bestätigung)
- **C4 — Welcher Bot hostet den Music-Cog?** a) (Empfehlung) gameserver-bot · b) eigener neuer Service `music-bot`
- **C5 — Scope-Bestätigung:** `/play`·queue·skip·stop·loop·shuffle·volume·Now-Playing-Buttons·Playlist-Import·Auto-Disconnect. Streichen/ergänzen?

---

## D — D-Rest: Dashboard-Backend (Server-/RCON-gebunden)

Diese brauchen Server/RCON (lokal nicht testbar) + teils Entscheidungen.

- **D1 — Player-Ban anbinden** (`server_detail.html` disabled-Button → RCON/UFW): bauen? (Empfehlung: ja, mit Bestätigungs-Dialog + Audit-Log)
- **D2 — Mod-Integration MC + SAT** (Liste/Suche/Install/Update/Remove im Dashboard): MC-Endpoints existieren teils → ausbauen + SAT ergänzen. Priorität? (Empfehlung: nach RBAC, da Edit-Rechte)
- **D3 — SSE entfernen, WebSocket behalten** (Plan-Entscheid steht): jetzt aufräumen? Risiko an laufendem Dashboard → vorsichtig. (Empfehlung: ja, in ruhiger Phase)
- **D4 — Save-Info-Tab** (Satisfactory/MC Welt-Stats, nutzt `savegame_analyzer`, degraded bis Lib-Fix): als eigener Tab? (Empfehlung: ja, zeigt was verfügbar ist)
- **D5 — Anomaly-Detection:** Plan-Notiz „Stub" ist veraltet — `correlation.py get_anomalies` ist bereits volle z-Score-Impl. → **nichts zu tun** (nur Bestätigung).

---

## E — Tier-2: Design (dein PDF / deine Richtung)

Code-first ist durch; Design kommt ans Ende. Ich implementiere aus deiner Vorgabe.

- **E1 — Dashboard-V5-Visual-Rollout:** Lieferst du ein **PDF/Mockup**, oder soll ich dir
  zuerst **N Design-Varianten** vorschlagen (Farb/Typo/Layout) zum Auswählen? (Empfehlung: 2-3 Varianten von mir → du wählst → ich baue)
- **E2 — Branding (Phase F):** neue **Bot-Namen + Avatare** (Discord-Portal = du) + Server-Icon/Banner + einheitlicher Embed-Stil (Code = ich). Hast du Namen/Farb-Richtung? (kein systemd-Rename — nur Außendarstellung)
- **E3 — Embed-Stil:** zentraler Helper existiert (`utils/embeds.py`) — gib mir Farb-Palette + Footer-Konvention, dann ziehe ich alle Bot-Embeds darauf.
- **E4 — Topics (Phase B):** `/setup_topics` ist gebaut (19 Channels, dry-run-Default). Soll ich es auf deinem Server **anwenden** (du bestätigst die Topic-Texte aus `docs/DISCORD_KANAL_BESCHREIBUNGEN.md`)? Oder trägst du selbst ein?

---

## F — Offene Review-Findings (Wave-2 /review)

0 CRIT/HIGH. Übrig: 1 MEDIUM + 5 LOW. Fixen oder akzeptieren?

- **F1 — MEDIUM `p-e536ffea34`** Temp-Voice Cross-Prozess-Config-Race (json.load sync im Loop beim mtime-Reload + read-merge-write ohne Cross-Prozess-Lock; admin-only/selten). a) (Empfehlung) als Best-Effort dokumentieren — geringe Frequenz · b) stat-Throttle + File-Lock bauen (1-2h)
- **F2 — LOW-Cleanups** (je <30min, ein Commit): `set_xp` dead-loop entfernen · `moderation_cog` `os.getenv`→Cache · `_startup_cleanup` track_task · Bridge-Normalisierung Single-Source · member_cache→DBHelper-Retry. → **jetzt erledigen?** (Empfehlung: ja, sammle ich in einem Cleanup-Commit)

---

## G — Sonstiges / Cleanup (sudo/Marco-gated)

- **G1 — Alte Prod-`.bak`** löschen (>24h): heutige `.bak.1780589221`/`.1780589478` behalten bis morgen; ältere (Mai/MVP-Deploy) per `sudo rm` wegräumen? (dein PW)
- **G2 — Obsolete Worktree-Dirs** (`.claude/worktrees/vibrant-dirac…`): `git worktree remove --force` (Safety-Net blockt mich) — du manuell, oder soll ich's vorbereiten?
- **G3 — Isolations-Test 2. Guild:** Wegwerf-Test-Server früh anlegen (Multi-Tenant-Leak vor Freund-Nutzung prüfen)? (Empfehlung: ja, bevor RBAC/Freunde live gehen)

---

## Reihenfolge-Vorschlag (meine Empfehlung)

Wenn du keine andere Prio nennst, arbeite ich nach Antworten so:
**F2 (Cleanups, sofort) → A (LFG) → B (RBAC + Audit-Log) → D1/D4 → E (Design, nach deinem Input) → C (Music, wenn Lavalink steht).**

> Antworte einfach pro Nummer. Nicht-beantwortete Punkte lasse ich offen / nehme meine
> Empfehlung, wenn sie unkritisch sind (Code-first-Items) — bei security-kritischem (RBAC)
> warte ich auf dich.
