const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat,
  HeadingLevel, BorderStyle, WidthType, ShadingType,
  PageNumber, PageBreak, TabStopType, TabStopPosition,
  ExternalHyperlink
} = require("docx");

// ── Color Constants ──
const BLUE_DARK = "1F3864";
const BLUE_MED = "2E75B6";
const BLUE_LIGHT = "D5E8F0";
const GRAY_LIGHT = "F2F2F2";
const GRAY_MED = "666666";
const WHITE = "FFFFFF";

// ── Helper Functions ──
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 60, bottom: 60, left: 100, right: 100 };

function headerCell(text, width) {
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA },
    shading: { fill: BLUE_DARK, type: ShadingType.CLEAR },
    margins: cellMargins,
    verticalAlign: "center",
    children: [new Paragraph({ children: [new TextRun({ text, bold: true, color: WHITE, font: "Arial", size: 20 })] })],
  });
}

function cell(text, width, opts = {}) {
  const runs = [];
  // Support bold prefix via "**text** rest"
  const parts = text.split(/\*\*(.*?)\*\*/);
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      runs.push(new TextRun({ text: parts[i], bold: true, font: "Arial", size: 20 }));
    } else if (parts[i]) {
      runs.push(new TextRun({ text: parts[i], font: "Arial", size: 20, ...(opts.color ? { color: opts.color } : {}) }));
    }
  }
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA },
    shading: opts.shading ? { fill: opts.shading, type: ShadingType.CLEAR } : undefined,
    margins: cellMargins,
    children: [new Paragraph({ children: runs })],
  });
}

function codeCell(text, width) {
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA },
    shading: { fill: GRAY_LIGHT, type: ShadingType.CLEAR },
    margins: cellMargins,
    children: [new Paragraph({ children: [new TextRun({ text, font: "Consolas", size: 18 })] })],
  });
}

function makeTable(headers, rows, colWidths) {
  const tableWidth = colWidths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: tableWidth, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [
      new TableRow({ children: headers.map((h, i) => headerCell(h, colWidths[i])) }),
      ...rows.map((row, ri) => new TableRow({
        children: row.map((c, ci) => {
          if (typeof c === "object" && c._cell) return c._cell;
          return cell(String(c), colWidths[ci], ri % 2 === 1 ? { shading: GRAY_LIGHT } : {});
        })
      }))
    ]
  });
}

function heading(text, level) {
  return new Paragraph({ heading: level, children: [new TextRun(text)] });
}

function para(text, opts = {}) {
  const runs = [];
  const parts = text.split(/\*\*(.*?)\*\*/);
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      runs.push(new TextRun({ text: parts[i], bold: true, font: "Arial", size: 22 }));
    } else if (parts[i]) {
      runs.push(new TextRun({ text: parts[i], font: "Arial", size: 22, ...(opts.italic ? { italics: true } : {}), ...(opts.color ? { color: opts.color } : {}) }));
    }
  }
  return new Paragraph({ spacing: { after: 120 }, children: runs, ...opts.paraOpts });
}

function codePara(text) {
  return new Paragraph({
    spacing: { after: 60 },
    children: [new TextRun({ text, font: "Consolas", size: 20, color: GRAY_MED })],
    indent: { left: 360 },
  });
}

function spacer() {
  return new Paragraph({ spacing: { after: 200 }, children: [] });
}

// ══════════════════════════════════════════════════════════════
// DOCUMENT CONTENT
// ══════════════════════════════════════════════════════════════

const children = [];

// ── Title Page ──
children.push(new Paragraph({ spacing: { before: 3000 }, children: [] }));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 200 },
  children: [new TextRun({ text: "PROJEKTDOKUMENTATION", font: "Arial", size: 52, bold: true, color: BLUE_DARK })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 100 },
  children: [new TextRun({ text: "Discord Bot System", font: "Arial", size: 40, color: BLUE_MED })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 100 },
  children: [new TextRun({ text: "Satisfactory Game Server Management", font: "Arial", size: 28, color: GRAY_MED })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 600 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: BLUE_MED, space: 1 } },
  children: [],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 100 },
  children: [new TextRun({ text: "Version 2.2.0", font: "Arial", size: 24, color: GRAY_MED })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 100 },
  children: [new TextRun({ text: "Stand: 19. Februar 2026", font: "Arial", size: 24, color: GRAY_MED })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 100 },
  children: [new TextRun({ text: "Betreiber: Marco", font: "Arial", size: 24, color: GRAY_MED })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  children: [new TextRun({ text: "Dedizierter Server: Netcup RS 4000 G12 \u2022 203.0.113.10", font: "Arial", size: 24, color: GRAY_MED })],
}));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 1. PROJEKTÜBERSICHT
// ══════════════════════════════════════════════════════════════
children.push(heading("1. Projekt\u00FCbersicht", HeadingLevel.HEADING_1));
children.push(para("Das Discord Bot System besteht aus zwei eigenst\u00E4ndigen Discord-Bots, die gemeinsam einen Satisfactory Dedicated Server verwalten. Die Architektur ist modular aufgebaut: ein **GameServer Bot** f\u00FCr interaktive Slash-Commands und ein **Monitor Bot** f\u00FCr automatisierte Hintergrundaufgaben wie Health-Checks, Backups und Dashboard-Updates."));
children.push(para("Das System l\u00E4uft auf einem **Netcup RS 4000 G12** \u2014 einem **dedizierten Server** (keine geteilte Virtualisierung, alle Ressourcen exklusiv) \u2014 mit Ubuntu 22.04 LTS. Durch die dedizierte Hardware stehen CPU, RAM und Disk dauerhaft ohne Einschr\u00E4nkungen zur Verf\u00FCgung, was konstante Gameserver-Performance gew\u00E4hrleistet. Die Benutzer-Trennung ist strikt: **marco** (Admin-SSH, Port 4422), **botuser** (Bot-Prozesse), **satisfactory** (Gameserver-Prozess)."));

children.push(spacer());
children.push(heading("Kerndaten", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Eigenschaft", "Wert"],
  [
    ["Projektversion", "2.2.0"],
    ["Python-Version", "3.10+ (venv)"],
    ["Framework", "discord.py 2.3+ mit app_commands"],
    ["Gesamtumfang", "~17.900 Zeilen Python-Code"],
    ["Anzahl Module", "56 Python-Dateien"],
    ["Slash-Commands", "23 (9 GameServer + 14 Monitor)"],
    ["Server-Typ", "Dedizierter Server (keine Virtualisierung)"],
    ["Server-OS", "Ubuntu 22.04 LTS"],
    ["SSH-Port", "4422 (geh\u00E4rtet)"],
  ],
  [4500, 4860]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 2. ARCHITEKTUR
// ══════════════════════════════════════════════════════════════
children.push(heading("2. Architektur", HeadingLevel.HEADING_1));
children.push(heading("2.1 Zwei-Bot-System", HeadingLevel.HEADING_2));
children.push(para("Die Trennung in zwei Bots erm\u00F6glicht unabh\u00E4ngige Restarts und klare Aufgabenteilung:"));
children.push(spacer());
children.push(makeTable(
  ["Bot", "Token", "Aufgabe", "Cogs"],
  [
    ["GameServer Bot", "DISCORD_TOKEN_MANAGER", "Interaktive Commands", "5 (satisfactory, general, timeout, mod, maintenance)"],
    ["Monitor Bot", "DISCORD_TOKEN_WATCHDOG", "Background Monitoring", "2 (monitor, scheduler)"],
  ],
  [2000, 2500, 2200, 2660]
));

children.push(spacer());
children.push(heading("2.2 Verzeichnisstruktur", HeadingLevel.HEADING_2));
children.push(para("Alle Dateien liegen unter **/home/botuser/Discord_Bots/**:"));
children.push(spacer());

const dirStructure = [
  ["bots/", "Bot-Hauptdateien (gameserver_bot.py, monitor_bot.py)"],
  ["cogs/", "Discord Cog-Module (7 Dateien)"],
  ["modules/", "Business-Logik Module (Kernfunktionalit\u00E4t)"],
  ["modules/satisfactory/", "Satisfactory-spezifisch (API, Server, Savegame)"],
  ["modules/minecraft/", "Minecraft-spezifisch (RCON, Server, Backup)"],
  ["modules/monitoring/", "Monitoring-Subsystem (13 Module)"],
  ["modules/backup/", "Backup-System (Manager, OneDrive, Config)"],
  ["modules/notifications/", "Benachrichtigungen (Discord, Email)"],
  ["utils/", "Hilfsmodule (Config, Logger, Formatting, Permissions)"],
  ["config/", ".env + config.json"],
  ["data/", "Persistente Daten (Stats, Tracker, Caches)"],
  ["logs/", "Log-Dateien"],
  ["backups/", "Lokale Savegame-Backups"],
  ["scripts/", "Shell-Skripte (Deploy, Watchdog, Optimize)"],
  ["systemd/", "Service-Definitionen"],
];
children.push(makeTable(
  ["Verzeichnis", "Beschreibung"],
  dirStructure,
  [3000, 6360]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 3. GAMESERVER BOT - COMMANDS
// ══════════════════════════════════════════════════════════════
children.push(heading("3. GameServer Bot \u2013 Commands", HeadingLevel.HEADING_1));
children.push(para("Der GameServer Bot stellt alle interaktiven Slash-Commands bereit. Er l\u00E4uft als systemd-Service **gameserver-bot.service** und l\u00E4dt 5 Cogs."));

children.push(spacer());
children.push(heading("3.1 Satisfactory Cog (satisfactory_cog.py)", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Command", "Berechtigung", "Beschreibung"],
  [
    ["/sat status", "Alle", "Server-Status mit Spielern, Tick-Rate, Uptime"],
    ["/sat start", "Admin", "Satisfactory-Server starten"],
    ["/sat stop", "Admin", "Server stoppen (5min Countdown)"],
    ["/sat restart", "Admin", "Server neustarten (Sofort / 5min / 10min Auswahl)"],
    ["/sat cancel", "Admin", "Laufenden Countdown abbrechen"],
    ["/sat players online", "Spieler", "Online-Spieler mit Spielzeit anzeigen"],
    ["/sat players ban", "Admin", "Spieler bannen (IP + Name)"],
    ["/sat players unban", "Admin", "Ban aufheben"],
    ["/sat players bans", "Alle", "Alle aktiven Bans anzeigen"],
    ["/sat backup create", "Admin", "Manuelles Savegame-Backup"],
    ["/sat backup save", "Admin", "Spiel speichern (API)"],
    ["/sat backup download", "Admin", "Savegame als Discord-Datei senden"],
    ["/sat backup list", "Alle", "Lokale Backups auflisten"],
    ["/sat backup restore", "Owner", "Backup wiederherstellen"],
    ["/sat config settings", "Alle", "Server-Einstellungen anzeigen"],
    ["/sat config playerlimit", "Admin", "Spielerlimit \u00E4ndern"],
    ["/sat config stats", "Alle", "Savegame-Statistiken"],
    ["/sat config update", "Owner", "Server via SteamCMD updaten"],
    ["/sat config console", "Owner", "Konsolen-Befehl ausf\u00FChren"],
    ["/sat blueprints upload", "Spieler", "Blueprint hochladen"],
    ["/sat blueprints list", "Alle", "Blueprints anzeigen"],
    ["/sat blueprints download", "Spieler", "Blueprint herunterladen"],
    ["/sat blueprints delete", "Admin", "Blueprint l\u00F6schen"],
  ],
  [3200, 1500, 4660]
));

children.push(spacer());
children.push(heading("3.2 General Cog (general_cog.py)", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Command", "Berechtigung", "Beschreibung"],
  [
    ["/help", "Alle", "Befehls\u00FCbersicht"],
    ["/server", "Alle", "System-Info (CPU, RAM, Disk, Server-Status)"],
    ["/clear [optionen]", "Admin", "Nachrichten l\u00F6schen (Bulk + Einzel, auch > 14 Tage)"],
    ["/ping", "Owner", "Bot-Latenz"],
    ["/reload [cog]", "Owner", "Cog zur Laufzeit neuladen"],
  ],
  [3200, 1500, 4660]
));
children.push(spacer());
children.push(para("Der **/clear** Befehl unterst\u00FCtzt: **anzahl** (max 500), **stunden** (letzte X Stunden), **von/bis** (Datumsbereich TT.MM.JJJJ oder TT.MM.JJJJ-HH:MM). Nachrichten \u00E4lter als 14 Tage werden automatisch einzeln gel\u00F6scht."));

children.push(spacer());
children.push(heading("3.3 Weitere Cogs", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Cog", "Commands", "Beschreibung"],
  [
    ["timeout_cog.py", "/timeout [spieler] [min] [grund]", "Game-Kick + Discord-Timeout gleichzeitig"],
    ["mod_cog.py", "/mod list|install|uninstall|update|search|info|export|import", "Mod-Verwaltung f\u00FCr Satisfactory und Minecraft"],
    ["maintenance_cog.py", "/maint version|network|ports|tokens|restart-bot", "Bot-Wartung und System-Diagnose"],
  ],
  [2500, 3500, 3360]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 4. MONITOR BOT - BACKGROUND TASKS
// ══════════════════════════════════════════════════════════════
children.push(heading("4. Monitor Bot \u2013 Background Tasks", HeadingLevel.HEADING_1));
children.push(para("Der Monitor Bot l\u00E4uft als **monitor-bot.service** und f\u00FChrt kontinuierlich Hintergrundaufgaben aus:"));
children.push(spacer());
children.push(makeTable(
  ["Task", "Intervall", "Beschreibung"],
  [
    ["Health Check", "120s", "Crash-Erkennung, Auto-Restart, Player-Sync"],
    ["Player Log Poll", "10s", "Join/Leave aus Server-Log erkennen"],
    ["Status Embed", "300s", "Dashboard-Embed in Discord aktualisieren"],
    ["Voice Stats", "300s", "Voice-Channel-Name mit Status aktualisieren"],
    ["Optimizer", "900s", "System-Health pr\u00FCfen, Auto-Optimierung"],
    ["Login Audit", "60s", "SSH-Login-Erkennung (Brute-Force, unbekannte IPs)"],
    ["Weekly Snapshot", "168h", "W\u00F6chentlicher Savegame-Snapshot"],
  ],
  [2800, 1300, 5260]
));

children.push(spacer());
children.push(heading("4.1 Scheduler Cog", HeadingLevel.HEADING_2));
children.push(para("Der Scheduler verwaltet zeitgesteuerte Aufgaben:"));
children.push(spacer());
children.push(makeTable(
  ["Aufgabe", "Zeitplan", "Details"],
  [
    ["Auto-Backup", "Alle 6h", "Savegame-Backup + OneDrive-Upload + Rotation"],
    ["Daily Restart", "04:00 Uhr", "Server-Neustart mit Countdown (nur wenn > 12h Uptime)"],
    ["Update Check", "Alle 6h", "SteamCMD Build-ID vergleichen"],
    ["Config Backup", "03:00 Uhr", "Server-Config zu OneDrive sichern"],
    ["Auto Cleanup", "02:00 Uhr", "Alte Logs/Backups/Crash-Replays aufr\u00E4umen"],
    ["T\u00E4glicher Report", "07:00 Uhr", "Zusammenfassung an Admin-Channel"],
    ["Wochen-Report", "Montag 08:00", "Ausf\u00FChrlicher Bericht mit Trends"],
    ["Backup-Verify", "Samstag 03:00", "Backup-Integrit\u00E4t pr\u00FCfen"],
  ],
  [2800, 2000, 4560]
));

children.push(spacer());
children.push(heading("4.2 Monitor Cog Commands", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Command", "Beschreibung"],
  [
    ["/performance", "Echtzeit System-Performance (CPU, RAM, Disk, Prozess)"],
    ["/dashboard", "Status-Embed manuell aktualisieren"],
    ["/stats [spieler]", "Spieler-Statistiken (Spielzeit, Sessions)"],
    ["/report [tage]", "Wochen-/Monatsbericht mit Trends"],
    ["/mon world", "Detaillierte Welt-Statistiken aus Savegame"],
    ["/selftest", "Alle Bot-Systeme pr\u00FCfen"],
    ["/commandlog [anzahl]", "Letzte Bot-Commands anzeigen"],
    ["/crashlog [nummer]", "Crash-Replays anzeigen/herunterladen"],
    ["/rollback", "Crash-Loop-Status und Rollback-Info"],
    ["/mail test|status", "Email-Benachrichtigungen verwalten"],
    ["/onedrive status|upload|list", "OneDrive Cloud-Backup"],
    ["/configbackup", "Server-Config Backup manuell erstellen"],
    ["/scheduler", "Scheduler-Status und Konfiguration"],
  ],
  [3800, 5560]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 5. MODULE - BUSINESS LOGIC
// ══════════════════════════════════════════════════════════════
children.push(heading("5. Module \u2013 Business Logic", HeadingLevel.HEADING_1));

children.push(heading("5.1 Satisfactory Module", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Modul", "Datei", "Funktion"],
  [
    ["SatisfactoryServer", "server.py", "systemd Start/Stop/Restart, Prozess-Info"],
    ["SatisfactoryAPI", "api_client.py", "HTTPS API (Status, Save, Einstellungen)"],
    ["SavegameAnalyzer", "savegame_analyzer.py", "Welt-Statistiken (Geb\u00E4ude, Strom, Transport)"],
    ["BlueprintManager", "blueprint_manager.py", "Blueprint Upload/Download/Verwaltung"],
    ["BlacklistManager", "blacklist.py", "Spieler-Bans (IP + UFW Firewall)"],
    ["WhitelistManager", "whitelist.py", "Spieler-Whitelist"],
    ["SettingsBackup", "settings_backup.py", "Server-Einstellungen sichern/laden"],
    ["SaveHeader", "save_header.py", "Savegame-Header parsen"],
  ],
  [2500, 2800, 4060]
));

children.push(spacer());
children.push(heading("5.2 Monitoring Module", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Modul", "Datei", "Funktion"],
  [
    ["HealthChecker", "health_check.py", "Server-Health, Crash-Erkennung, Auto-Restart"],
    ["PerformanceMonitor", "performance.py", "CPU/RAM/Disk-Metriken mit Schwellwerten"],
    ["PlayerTracker", "player_tracker.py", "Spielzeit-Tracking, Session-Verwaltung"],
    ["PlayerIPTracker", "player_ip_tracker.py", "Name-zu-IP Mapping, Ban-Evasion-Erkennung"],
    ["UpdateChecker", "update_checker.py", "SteamCMD Build-ID Vergleich"],
    ["StatsTracker", "stats_tracker.py", "Langzeit-Statistiken f\u00FCr Reports"],
    ["CrashReplay", "crash_replay.py", "Log-Kontext bei Crashes speichern"],
    ["LoginAudit", "login_audit.py", "SSH-Login \u00DCberwachung"],
    ["AutoCleanup", "auto_cleanup.py", "Alte Dateien automatisch aufr\u00E4umen"],
    ["ServerOptimizer", "optimizer.py", "Automatische System-Optimierung"],
    ["GracefulDegradation", "graceful_degradation.py", "Service-Ausfall-Handling mit Retry"],
    ["SavegameProtection", "savegame_protection.py", "Crash-Loop-Erkennung, Rollback-Info"],
    ["SteamChangelog", "steam_changelog.py", "Update-Changelog von Steam"],
    ["SelfTest", "selftest.py", "Bot-System-Selbsttest"],
  ],
  [2500, 2800, 4060]
));

children.push(spacer());
children.push(heading("5.3 Backup Module", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Modul", "Datei", "Funktion"],
  [
    ["BackupManager", "backup_manager.py", "Savegame-Backups erstellen, listen, rotieren"],
    ["OneDriveBackup", "onedrive_backup.py", "rclone-basierter Cloud-Upload"],
    ["ConfigBackup", "config_backup.py", "Server-Config (.env, config, systemd) sichern"],
  ],
  [2500, 2800, 4060]
));

children.push(spacer());
children.push(heading("5.4 Notification Module", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Modul", "Datei", "Funktion"],
  [
    ["DiscordNotifier", "discord_notifier.py", "Crash/Recovery/Update/Performance an Discord"],
    ["EmailNotifier", "email_notifier.py", "SMTP-Alerts via Brevo (Crash, Restart-Fail)"],
  ],
  [2500, 2800, 4060]
));

children.push(spacer());
children.push(heading("5.5 Sonstige Module", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Modul", "Datei", "Funktion"],
  [
    ["RestartTimer", "restart_timer.py", "Countdown mit API-Warnings und Abbruch"],
    ["ModManager", "mod_manager.py", "Mod-Installation und -Verwaltung"],
    ["WordFilter", "word_filter.py", "Chat-Filter (29 Patterns)"],
    ["AntiSpam", "anti_spam.py", "Rate-Limiting f\u00FCr Commands"],
    ["CommandLogger", "command_logger.py", "Command-Audit-Log"],
    ["BotMaintenance", "maintenance.py", "System-Diagnose (Ports, Tokens, Netzwerk)"],
    ["ConfigValidator", "config_validator.py", "Konfiguration validieren beim Start"],
  ],
  [2500, 2800, 4060]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 6. KONFIGURATION
// ══════════════════════════════════════════════════════════════
children.push(heading("6. Konfiguration", HeadingLevel.HEADING_1));

children.push(heading("6.1 Environment-Variablen (.env)", HeadingLevel.HEADING_2));
children.push(para("Die Datei **config/.env** (chmod 600) enth\u00E4lt alle sensiblen Zugangsdaten:"));
children.push(spacer());
children.push(makeTable(
  ["Kategorie", "Variablen"],
  [
    ["Discord Tokens", "DISCORD_TOKEN_MANAGER, DISCORD_TOKEN_WATCHDOG, GUILD_ID"],
    ["Berechtigungen", "OWNER_ID, ADMIN_ROLE_ID, SATISFACTORY_ROLE_ID"],
    ["Channels", "ADMIN_LOG_CHANNEL_ID, STATUS_EMBED_CHANNEL_ID, GAME_CHAT_CHANNEL_ID, VOICE_STATS_CATEGORY_ID"],
    ["Satisfactory Server", "SATISFACTORY_SERVICE, SATISFACTORY_USER, SATISFACTORY_SERVER_PATH"],
    ["Satisfactory API", "API_HOST, API_PORT, API_TOKEN, API_VERIFY_SSL"],
    ["Backup", "BACKUP_PATH, SATISFACTORY_SAVE_PATH"],
    ["OneDrive", "ONEDRIVE_ENABLED, ONEDRIVE_REMOTE, ONEDRIVE_PATH"],
    ["Email (Brevo)", "SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO"],
    ["Minecraft (geplant)", "MINECRAFT_SERVICE, MINECRAFT_RCON_*, MINECRAFT_ROLE_ID"],
  ],
  [2800, 6560]
));

children.push(spacer());
children.push(heading("6.2 Feature-Flags (config.json)", HeadingLevel.HEADING_2));
children.push(para("Die Datei **config/config.json** steuert welche Features aktiv sind. Alle Features k\u00F6nnen einzeln aktiviert/deaktiviert werden: chat_bridge, word_filter, anti_spam, player_tracking, auto_backup, onedrive_backup, email_notifications, auto_update, daily_restart, voice_stats, status_embed, login_audit, auto_cleanup, savegame_protection, graceful_degradation, steam_changelog."));

children.push(spacer());
children.push(heading("6.3 Schwellwerte", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Schwellwert", "Standard", "Beschreibung"],
  [
    ["cpu_warning", "80%", "CPU-Warnung"],
    ["ram_warning", "85%", "RAM-Warnung"],
    ["disk_warning", "90%", "Festplatten-Warnung"],
    ["disk_critical", "95%", "Festplatten-Kritisch"],
    ["tick_rate_warning", "20", "Tick-Rate unter 20 = Warnung"],
    ["crash_window", "10 min / 3 Crashes", "Crash-Loop-Erkennung"],
  ],
  [2500, 1500, 5360]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 7. SERVER-INFRASTRUKTUR
// ══════════════════════════════════════════════════════════════
children.push(heading("7. Server-Infrastruktur", HeadingLevel.HEADING_1));

children.push(heading("7.1 Benutzer-Trennung", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Benutzer", "Aufgabe", "Zugang"],
  [
    ["marco", "Admin, SSH-Verwaltung", "SSH Port 4422, sudo"],
    ["botuser", "Bot-Prozesse", "systemd-Services, eingeschr\u00E4nktes sudo"],
    ["satisfactory", "Gameserver-Prozess", "Kein SSH, nur Service"],
  ],
  [2000, 3000, 4360]
));

children.push(spacer());
children.push(heading("7.2 systemd Services", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Service", "Benutzer", "Beschreibung"],
  [
    ["gameserver-bot.service", "botuser", "GameServer Bot mit venv und .env"],
    ["monitor-bot.service", "botuser", "Monitor Bot mit venv und .env"],
    ["satisfactory.service", "satisfactory", "Satisfactory Dedicated Server"],
    ["bot-watchdog.timer", "root", "Watchdog-Timer f\u00FCr Bot-\u00DCberwachung"],
  ],
  [3200, 1500, 4660]
));

children.push(spacer());
children.push(heading("7.3 sudoers-Berechtigungen", HeadingLevel.HEADING_2));
children.push(para("Die Datei **/etc/sudoers.d/botuser** erlaubt dem botuser ohne Passwort:"));
children.push(codePara("systemctl start|stop|restart satisfactory.service"));
children.push(codePara("systemctl start|stop|restart gameserver-bot.service"));
children.push(codePara("systemctl start|stop|restart monitor-bot.service"));
children.push(codePara("/usr/local/bin/drop-caches.sh"));

children.push(spacer());
children.push(heading("7.4 Backup-Strategie", HeadingLevel.HEADING_2));
children.push(para("Mehrstufiges Backup-Konzept:"));
children.push(spacer());
children.push(makeTable(
  ["Ebene", "Intervall", "Aufbewahrung", "Ziel"],
  [
    ["Savegame-Backup", "Alle 6h", "20 lokal", "/home/botuser/Discord_Bots/backups/"],
    ["Cloud-Backup", "Nach jedem lokalen", "10 auf OneDrive", "OneDrive/SatisfactoryBackups/"],
    ["Config-Backup", "T\u00E4glich 03:00", "7 St\u00FCck", "OneDrive/Backups/ServerConfig/"],
    ["W\u00F6chentl. Snapshot", "Montags", "52 Wochen", "data/analyzer_cache/"],
  ],
  [2200, 2000, 2000, 3160]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 8. SICHERHEITSFEATURES
// ══════════════════════════════════════════════════════════════
children.push(heading("8. Sicherheitsfeatures", HeadingLevel.HEADING_1));
children.push(makeTable(
  ["Feature", "Beschreibung"],
  [
    ["SSH-H\u00E4rtung", "Port 4422, Key-Only, Fail2Ban"],
    ["Login Audit", "Unbekannte SSH-Logins werden per Discord + Email gemeldet"],
    ["Brute-Force-Erkennung", "Mehr als 5 fehlgeschlagene Logins = Alert"],
    ["Ban-Evasion", "IP-Tracking erkennt gebannte Spieler mit neuem Namen"],
    ["Firewall-Integration", "Bans werden per UFW in die Firewall \u00FCbernommen"],
    ["Crash-Loop-Schutz", "3 Crashes in 10 Min = Auto-Restart deaktiviert"],
    ["Savegame-Schutz", "Gr\u00F6\u00DFen-\u00DCberwachung, Integrit\u00E4tspr\u00FCfung nach Crash"],
    ["Graceful Degradation", "Service-Ausf\u00E4lle (API, OneDrive, Email) werden isoliert"],
    [".env Schutz", "chmod 600, nie in Git"],
    ["Word Filter", "29 Patterns f\u00FCr Chat-Moderation"],
    ["Anti-Spam", "Rate-Limiting: 5 Nachrichten/10s, 3 Commands/10s"],
    ["Command Audit", "Alle Slash-Commands werden geloggt"],
  ],
  [2800, 6560]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 9. DATEILISTE (für Code-Review)
// ══════════════════════════════════════════════════════════════
children.push(heading("9. Komplette Dateiliste", HeadingLevel.HEADING_1));
children.push(para("Alle aktiven Projektdateien f\u00FCr den Code-Review:"));
children.push(spacer());

const allFiles = [
  ["bots/gameserver_bot.py", "GameServer Bot Hauptdatei, Cog-Loading, Service-Init"],
  ["bots/monitor_bot.py", "Monitor Bot, Background Tasks, Status Embed, Voice Stats"],
  ["cogs/satisfactory_cog.py", "Satisfactory Commands (start/stop/restart/backup/config/players)"],
  ["cogs/general_cog.py", "Allgemeine Commands (help/server/clear/ping/reload)"],
  ["cogs/monitor_cog.py", "Monitoring Commands (performance/dashboard/stats/report)"],
  ["cogs/scheduler_cog.py", "Scheduler Tasks (Backup/Restart/Update/Report/Cleanup)"],
  ["cogs/maintenance_cog.py", "Wartungs-Commands (version/network/ports/tokens)"],
  ["cogs/timeout_cog.py", "Cross-Platform Timeout (Game + Discord)"],
  ["cogs/mod_cog.py", "Mod-Verwaltung (list/install/uninstall/search)"],
  ["cogs/minecraft_cog.py", "Minecraft Commands (Platzhalter f\u00FCr Phase 14)"],
  ["modules/satisfactory/server.py", "Satisfactory systemd-Integration"],
  ["modules/satisfactory/api_client.py", "HTTPS API Client"],
  ["modules/satisfactory/savegame_analyzer.py", "Welt-Statistiken aus Savegame"],
  ["modules/satisfactory/blueprint_manager.py", "Blueprint Upload/Download"],
  ["modules/satisfactory/blacklist.py", "Ban-System mit IP + UFW"],
  ["modules/satisfactory/whitelist.py", "Whitelist-Verwaltung"],
  ["modules/satisfactory/settings_backup.py", "Einstellungs-Backup"],
  ["modules/satisfactory/save_header.py", "Savegame-Header Parser"],
  ["modules/satisfactory/savegame_stats.py", "Savegame-Statistik-Datenklassen"],
  ["modules/monitoring/health_check.py", "Server Health + Crash Detection"],
  ["modules/monitoring/performance.py", "CPU/RAM/Disk Metriken"],
  ["modules/monitoring/player_tracker.py", "Spielzeit-Tracking"],
  ["modules/monitoring/player_ip_tracker.py", "IP-Tracking + Ban-Evasion"],
  ["modules/monitoring/update_checker.py", "SteamCMD Update-Check"],
  ["modules/monitoring/stats_tracker.py", "Langzeit-Statistiken"],
  ["modules/monitoring/crash_replay.py", "Crash Log Capture"],
  ["modules/monitoring/login_audit.py", "SSH Login \u00DCberwachung"],
  ["modules/monitoring/auto_cleanup.py", "Automatische Dateibereinigung"],
  ["modules/monitoring/optimizer.py", "System Auto-Optimierung"],
  ["modules/monitoring/graceful_degradation.py", "Service-Ausfall-Handling"],
  ["modules/monitoring/savegame_protection.py", "Crash-Loop + Savegame-Schutz"],
  ["modules/monitoring/steam_changelog.py", "Steam Update Changelog"],
  ["modules/monitoring/selftest.py", "System-Selbsttest"],
  ["modules/backup/backup_manager.py", "Savegame-Backup Verwaltung"],
  ["modules/backup/onedrive_backup.py", "rclone OneDrive Upload"],
  ["modules/backup/config_backup.py", "Config-Backup zu OneDrive"],
  ["modules/notifications/discord_notifier.py", "Discord-Benachrichtigungen"],
  ["modules/notifications/email_notifier.py", "Email-Benachrichtigungen (Brevo SMTP)"],
  ["modules/restart_timer.py", "Countdown-Timer mit API-Warnings"],
  ["modules/mod_manager.py", "Mod-Verwaltung (SMM/CurseForge)"],
  ["modules/word_filter.py", "Chat-Filter (29 Patterns)"],
  ["modules/anti_spam.py", "Rate-Limiting"],
  ["modules/command_logger.py", "Command Audit Log"],
  ["modules/maintenance.py", "System-Diagnose Funktionen"],
  ["modules/config_validator.py", "Konfigurations-Validierung"],
  ["modules/minecraft/server.py", "Minecraft Server-Integration (Phase 14)"],
  ["modules/minecraft/rcon.py", "Minecraft RCON Client"],
  ["modules/minecraft/backup.py", "Minecraft Backup-Manager"],
  ["utils/config.py", "Config + .env Loader"],
  ["utils/logger.py", "Logging-Setup"],
  ["utils/formatting.py", "Format-Helfer (Uptime, Bytes, Emoji, Progress)"],
  ["utils/permissions.py", "Permission-Decorators (admin_only, owner_only etc.)"],
  ["config/.env", "Umgebungsvariablen (Token, API-Keys)"],
  ["config/config.json", "Feature-Flags und Schwellwerte"],
];
children.push(makeTable(
  ["Datei", "Beschreibung"],
  allFiles,
  [4200, 5160]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 10. DURCHGEFÜHRTE VERBESSERUNGEN & FIXES
// ══════════════════════════════════════════════════════════════
children.push(heading("10. Durchgef\u00FChrte Verbesserungen & Fixes", HeadingLevel.HEADING_1));
children.push(para("Die folgenden \u00C4nderungen wurden w\u00E4hrend der Code-Review und Optimierungsphase (Februar 2026) vorgenommen:"));
children.push(spacer());

children.push(heading("10.1 Kritische Bug-Fixes", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Bug", "Datei", "Problem", "L\u00F6sung"],
  [
    ["maintenance_cog Naming-Kollision", "maintenance_cog.py", "self.maint = BotMaintenance() \u00FCberschrieb die maint Command-Group, sodass alle /maint Commands nicht registriert wurden", "Umbenannt zu self.maintenance, alle Aufrufe angepasst"],
    ["ModManager fehlender Parameter", "gameserver_bot.py", "ModManager wurde ohne server_path initialisiert, was zum Absturz beim Cog-Laden f\u00FChrte", "server_path=bot.sat_server.server_path als Parameter erg\u00E4nzt"],
    ["Voice-Channel zeigt offline", "monitor_bot.py", "update_voice_stats nutzte nur den gecachten health_checker.status.state, der vor dem ersten Health-Check nicht gesetzt war", "Direkter Server-Check via sat_server.is_running() + API-Abfrage f\u00FCr Spielerdaten"],
    ["/clear bricht ab", "general_cog.py", "channel.purge(bulk=True) bricht bei Nachrichten \u00E4lter als 14 Tage sofort ab", "Nachrichten werden jetzt vorgesammelt und in Bulk (< 14 Tage) + Einzel (\u2265 14 Tage) aufgeteilt"],
    ["sudo-Berechtigung fehlt", "Server sudoers", "botuser konnte systemctl nicht f\u00FCr satisfactory.service ausf\u00FChren, /sat start|stop|restart scheiterten", "/etc/sudoers.d/botuser mit NOPASSWD f\u00FCr alle relevanten Services erstellt"],
  ],
  [1800, 1600, 3000, 2960]
));

children.push(spacer());
children.push(heading("10.2 Entfernte Features (Bereinigung)", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Feature", "Dateien", "Grund"],
  [
    ["/sat players kick", "satisfactory_cog.py", "Redundant mit Ban-Befehl (Kick = tempor\u00E4rer Ban)"],
    ["/maint update (git pull)", "maintenance_cog.py", "Kein GitHub-Workflow, Deployment per FileZilla"],
    ["/maint changelog", "maintenance_cog.py", "Ohne Git-History nicht nutzbar"],
    ["ChatBridge (Satisfactory)", "gameserver_bot.py, monitor_bot.py", "Nicht kompatibel mit Vanilla Satisfactory Server"],
    ["/sat players broadcast", "satisfactory_cog.py", "Nicht kompatibel mit Vanilla Satisfactory"],
  ],
  [2500, 3200, 3660]
));

children.push(spacer());
children.push(heading("10.3 Neue Features", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Feature", "Datei", "Beschreibung"],
  [
    ["/sat restart Auswahl-Buttons", "satisfactory_cog.py", "3 Optionen: Sofort, 5 Minuten, 10 Minuten Countdown statt nur 10min. Buttons mit 30s Timeout"],
    ["/clear mit Bulk + Einzel", "general_cog.py", "L\u00F6scht auch Nachrichten \u00E4lter als 14 Tage per Einzell\u00F6schung. Fortschrittsanzeige alle 50 Nachrichten"],
    ["Status-Embed vereinfacht", "monitor_bot.py", "Cloud/Save/Email/Build-Zeilen entfernt. Nur noch Server, Spieler, Savegame, Restart, Tick Rate"],
    ["Voice-Channel direkt-check", "monitor_bot.py", "Nutzt sat_server.is_running() + API statt nur gecachten Status"],
    ["Downtime-Benachrichtigung", "monitor_bot.py", "Nach 6 Min Offline wird automatisch eine Benachrichtigung gesendet, bei Recovery ebenfalls"],
  ],
  [2500, 2200, 4660]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 11. BEKANNTE PROBLEME & WARTUNGSHINWEISE
// ══════════════════════════════════════════════════════════════
children.push(heading("11. Bekannte Probleme & Wartungshinweise", HeadingLevel.HEADING_1));

children.push(heading("11.1 Bekannte Einschr\u00E4nkungen", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Problem", "Auswirkung", "Workaround"],
  [
    ["Satisfactory-Verbindung h\u00E4ngt sporadisch", "Spieler k\u00F6nnen nicht joinen obwohl Server als online angezeigt wird", "/sat restart mit Sofort-Option nutzen. Gameserver-Prozess h\u00E4ngt sich gelegentlich auf"],
    ["Discord Bulk-Delete 14-Tage-Limit", "/clear f\u00FCr alte Nachrichten ist langsam (~4 pro Sekunde)", "Bei gro\u00DFen Mengen \u00E4lterer Nachrichten Geduld mitbringen, Fortschritt wird angezeigt"],
    ["Voice-Channel Rate-Limit", "Discord erlaubt nur 2 Namens\u00E4nderungen pro 10 Minuten pro Channel", "Voice-Update l\u00E4uft alle 5 Min, bei schnellen Status\u00E4nderungen Verz\u00F6gerung m\u00F6glich"],
    ["Savegame-Analyse Genauigkeit", "WorldStats sind gesch\u00E4tzt basierend auf Dateianalyse, nicht exakt", "F\u00FCr exakte Werte die Spielinterne Statistik nutzen"],
    ["ChatBridge nicht verf\u00FCgbar", "Kein Discord-zu-Game Chat f\u00FCr Satisfactory", "Feature ist Vanilla-inkompatibel, nur f\u00FCr Minecraft geplant"],
    ["Minecraft noch nicht aktiv", "minecraft_cog.py + modules/minecraft/ sind Platzhalter", "Wird in Phase 14 implementiert"],
    ["/clear Fortschrittsanzeige fehlt", "Beim L\u00F6schen von Nachrichten wird kein Fortschritt angezeigt, obwohl der Code vorhanden ist", "Muss debuggt werden: edit_original_response() funktioniert m\u00F6glicherweise nicht korrekt mit ephemeral defer"],
    ["Verwaiste SSH-Key-Dateien", "Auf dem Server liegen fehlerhafte SSH-Key-Dateien im Home-Verzeichnis von marco", "Manuell l\u00F6schen: rm ~/'\":USERPROFILE\\.ssh\\botuser_key\"' etc."],
  ],
  [2500, 3000, 3860]
));

children.push(spacer());
children.push(heading("11.2 Kritische Wartungshinweise", HeadingLevel.HEADING_2));
children.push(para("**ACHTUNG: Diese Punkte m\u00FCssen bei jeder \u00C4nderung beachtet werden:**"));
children.push(spacer());
children.push(makeTable(
  ["Bereich", "Hinweis"],
  [
    ["Dateien l\u00F6schen auf Server", "NIEMALS den gesamten Discord_Bots Ordner l\u00F6schen! Das venv, die .env mit echten Tokens, die data/ Dateien und requirements.txt sind nur auf dem Server vorhanden. Bei versehentlichem L\u00F6schen m\u00FCssen Tokens aus OneDrive-Backup wiederhergestellt werden"],
    ["FileZilla Upload", "Immer nur die ge\u00E4nderten .py Dateien hochladen, NICHT den gesamten Ordner. config/, data/, venv/, logs/ d\u00FCrfen nicht \u00FCberschrieben werden"],
    [".env Datei", "Enth\u00E4lt echte Discord-Tokens, API-Keys und SMTP-Passw\u00F6rter. Datei ist chmod 600 und darf NIEMALS in Git oder \u00F6ffentlich geteilt werden"],
    ["maintenance_cog.py", "Die BotMaintenance-Instanz MUSS self.maintenance hei\u00DFen (nicht self.maint!). self.maint ist reserviert f\u00FCr die app_commands.Group"],
    ["ModManager Initialisierung", "MUSS immer server_path als Parameter enthalten: ModManager(game, server_path=..., mods_dir=...)"],
    ["sudoers \u00E4ndern", "Immer \u00FCber visudo -c validieren. Syntaxfehler in sudoers sperren sudo f\u00FCr alle Benutzer aus"],
    ["Bot-Neustart", "Immer beide Services neustarten: sudo systemctl restart gameserver-bot.service monitor-bot.service"],
    ["SSH-Key botuser", "Key liegt in /home/marco/.ssh/botuser_key (auf Server generiert). Private Key als lokale Datei f\u00FCr FileZilla verwenden"],
    ["Backup-Rotation", "Max 20 lokal, 10 Cloud. Bei vollen Backups werden \u00E4lteste automatisch gel\u00F6scht. Config-Backups max 7"],
    ["venv neu erstellen", "Falls venv besch\u00E4digt: sudo -u botuser python3 -m venv /home/botuser/Discord_Bots/venv && source venv/bin/activate && pip install -r requirements.txt"],
  ],
  [2500, 6860]
));

children.push(spacer());
children.push(heading("11.3 Fehlerbehebungs-Checkliste", HeadingLevel.HEADING_2));
children.push(para("Bei Problemen in dieser Reihenfolge pr\u00FCfen:"));
children.push(spacer());
children.push(makeTable(
  ["Symptom", "Pr\u00FCfung", "Befehl"],
  [
    ["Bot startet nicht", "Service-Status pr\u00FCfen", "sudo journalctl -u gameserver-bot.service -n 50 --no-pager"],
    ["Cog l\u00E4dt nicht", "Import-Fehler im Log suchen", "sudo journalctl -u gameserver-bot.service | grep 'Failed to load'"],
    ["Commands fehlen", "Sync-Anzahl im Log pr\u00FCfen", "Sollte 9 (GameServer) bzw. 14 (Monitor) Commands synced zeigen"],
    [".env nicht gefunden", "Pfad + Rechte pr\u00FCfen", "ls -la /home/botuser/Discord_Bots/config/.env (muss 600 sein)"],
    ["venv fehlt", "Verzeichnis pr\u00FCfen", "ls /home/botuser/Discord_Bots/venv/bin/python3"],
    ["Server offline trotz Prozess", "API-Verbindung testen", "/maint network und /maint ports in Discord"],
    ["Spieler kann nicht joinen", "Server neu starten", "/sat restart und Sofort w\u00E4hlen"],
    ["Status-Embed aktualisiert nicht", "Monitor Bot Log pr\u00FCfen", "sudo journalctl -u monitor-bot.service -n 30 --no-pager"],
    ["Backup fehlgeschlagen", "OneDrive/rclone pr\u00FCfen", "/onedrive status in Discord"],
    ["Email geht nicht", "SMTP-Config pr\u00FCfen", "/mail status und /mail test in Discord"],
  ],
  [2800, 2800, 3760]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 12. DETAILLIERTE FUNKTIONSBESCHREIBUNGEN
// ══════════════════════════════════════════════════════════════
children.push(heading("12. Detaillierte Funktionsbeschreibungen", HeadingLevel.HEADING_1));
children.push(para("Nachfolgend eine ausf\u00FChrliche Beschreibung aller aktiven Funktionen des Bot-Systems zur \u00DCbersicht und Kontrolle."));

children.push(spacer());
children.push(heading("12.1 Server-Steuerung (GameServer Bot)", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Funktion", "Ablauf", "Technische Details"],
  [
    ["/sat start", "1. Pr\u00FCft ob Server bereits l\u00E4uft. 2. Startet via sudo systemctl start satisfactory.service. 3. Wartet auf Prozess-Start. 4. Meldet Ergebnis.", "SatisfactoryServer._systemctl('start'), Timeout 60s"],
    ["/sat stop", "1. Pr\u00FCft ob Server l\u00E4uft. 2. Startet 5min Countdown mit Warnungen bei 5/3/1 Min. 3. Sendet API-Warnung an Spieler. 4. Stoppt via systemctl.", "RestartTimer.countdown(), API-Broadcast, systemctl stop"],
    ["/sat restart", "1. Zeigt Auswahl-Buttons: Sofort / 5 Min / 10 Min. 2. Bei Countdown: Warnungen via API an Spieler. 3. Stoppt und startet Server. 4. Meldet Ergebnis.", "RestartModeView mit discord.ui.Button, _do_restart_immediate() oder _do_restart_countdown()"],
    ["/sat status", "1. Liest Server-Status via systemctl is-active. 2. Fragt API nach Spielerdaten, Tick-Rate, Session-Info. 3. Zeigt Savegame-Stats. 4. Baut Embed.", "sat_server.is_running(), sat_api.query_server_state(), savegame_analyzer.get_stats()"],
    ["/sat cancel", "Bricht laufenden Countdown ab. Timer wird gestoppt, Spieler informiert.", "TimerManager.get_active().cancel()"],
  ],
  [1800, 3800, 3760]
));

children.push(spacer());
children.push(heading("12.2 Spieler-Verwaltung", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Funktion", "Ablauf", "Technische Details"],
  [
    ["/sat players online", "Zeigt alle online Spieler mit aktueller Session-Dauer aus dem PlayerTracker.", "player_tracker.get_online_players(), session_start Zeitstempel"],
    ["/sat players ban", "1. Sucht Spieler-IP. 2. Erstellt Blacklist-Eintrag. 3. Blockiert IP per UFW-Firewall. 4. Best\u00E4tigung.", "blacklist.add(), player_ip_tracker.get_ip(), ufw deny from IP"],
    ["/sat players unban", "1. Entfernt Blacklist-Eintrag. 2. Hebt UFW-Block auf. 3. Best\u00E4tigung.", "blacklist.remove(), ufw delete deny from IP"],
    ["/sat players bans", "Listet alle aktiven Bans mit Name, IP, Datum und Grund.", "blacklist.get_all()"],
    ["/timeout", "1. Kickt Spieler vom Game-Server (API). 2. Setzt Discord-Timeout f\u00FCr angegebene Dauer.", "sat_api.kick_player(), member.timeout()"],
  ],
  [1800, 4000, 3560]
));

children.push(spacer());
children.push(heading("12.3 Backup-System", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Funktion", "Ablauf", "Technische Details"],
  [
    ["/sat backup create", "1. Erstellt tar.gz des Savegame-Ordners. 2. Optional: Upload zu OneDrive. 3. Rotiert alte Backups (max 20 lokal).", "backup_manager.create(), onedrive_backup.upload(), backup_manager.rotate()"],
    ["/sat backup save", "Sendet Save-Befehl via Satisfactory API (erzwingt Speichern).", "sat_api.save_game()"],
    ["/sat backup list", "Listet alle lokalen Backups mit Gr\u00F6\u00DFe und Datum.", "backup_manager.list_backups()"],
    ["/sat backup restore", "1. Best\u00E4tigung per Button. 2. Stoppt Server. 3. Entpackt Backup. 4. Startet Server.", "RestoreConfirmView, server.stop(), backup_manager.restore(), server.start()"],
    ["/sat backup download", "Sendet das letzte Savegame als Discord-Datei-Anhang.", "discord.File() mit Savegame-Pfad"],
    ["Auto-Backup (Scheduler)", "Alle 6h: Backup erstellen, OneDrive-Upload, lokale Rotation, Cloud-Rotation.", "scheduler_cog: backup_task @6h"],
    ["Config-Backup", "T\u00E4glich 03:00: .env, config.json, systemd-Files als tar.gz zu OneDrive.", "config_backup.create_and_upload()"],
  ],
  [2200, 4000, 3160]
));

children.push(spacer());
children.push(heading("12.4 Monitoring & Health", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Funktion", "Ablauf", "Technische Details"],
  [
    ["Health Check (120s)", "1. Pr\u00FCft Prozess-Status via systemctl. 2. Fragt API-State ab. 3. Bei Crash: Auto-Restart, Notification, Crash-Replay. 4. Crash-Loop-Erkennung (3 in 10min).", "health_checker.check(), ServerState Enum, crash_replay.capture()"],
    ["Player Log Poll (10s)", "Liest FactoryGame.log, erkennt Join/Leave per Regex, aktualisiert PlayerTracker.", "_poll_player_events(), _PLAYER_JOIN_RE, _PLAYER_LEAVE_RE"],
    ["Status Embed (300s)", "Baut Dashboard-Embed mit Server-Status, Spielern, Savegame-Stats, N\u00E4chstem Restart. Editiert bestehende Nachricht.", "_update_status_embed_impl(), _status_message_id persistiert"],
    ["Voice Stats (300s)", "Setzt Voice-Channel-Name auf 'SAT-1 | \uD83D\uDFE2 X/Y' oder '\uD83D\uDD34 Offline'. Direkter Server-Check via is_running().", "update_voice_stats(), sat_server.is_running(), vc.edit(name=...)"],
    ["Performance (300s)", "Sammelt CPU/RAM/Disk/Prozess-Metriken. Warnung bei \u00DCberschreitung der Schwellwerte.", "perf_monitor.collect(), perf_monitor.check_thresholds()"],
    ["Optimizer (900s)", "Pr\u00FCft System-Health, setzt Prozess-Priorit\u00E4t, cache-drop bei Bedarf.", "optimizer.check_and_optimize(), nice -10, drop-caches.sh"],
    ["Login Audit (60s)", "Liest /var/log/auth.log, erkennt unbekannte SSH-Logins und Brute-Force-Versuche.", "login_audit.check(), on_unknown_login, on_failed_login_burst"],
  ],
  [2200, 4200, 2960]
));

children.push(spacer());
children.push(heading("12.5 Benachrichtigungen", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Event", "Discord", "Email"],
  [
    ["Server Crash", "Admin-Channel + Rolle-Ping + Crash-Replay", "Crash-Alert Email"],
    ["Auto-Restart erfolgreich", "Admin-Channel Embed", "Nein"],
    ["Auto-Restart fehlgeschlagen", "Admin-Channel + Rolle-Ping", "Restart-Failed Email"],
    ["Crash-Loop erkannt", "Admin-Channel + Rolle-Ping, Auto-Restart deaktiviert", "Nein"],
    ["Server Recovery (nach Downtime)", "Game-Chat-Channel + Admin", "Nein"],
    ["Performance-Warnung", "Admin-Channel", "Performance-Alert Email"],
    ["Update verf\u00FCgbar", "Admin-Channel", "Update-Available Email"],
    ["Unbekannter SSH-Login", "Admin-Channel + Rolle-Ping", "Security-Alert Email"],
    ["SSH Brute-Force", "Admin-Channel", "Nein"],
    ["Neuer Spieler beigetreten", "Admin-Channel (blau, ohne Ping)", "Nein"],
    ["Gebannter Spieler versucht Join", "Admin-Channel + Rolle-Ping (rot)", "Nein"],
    ["Ban-Evasion erkannt", "Admin-Channel + Rolle-Ping (rot)", "Nein"],
    ["Server-Downtime (> 6 Min)", "Game-Chat-Channel", "Nein"],
    ["Service ausgefallen (API/OneDrive/Email)", "Admin-Channel", "Nein"],
    ["Service wiederhergestellt", "Admin-Channel", "Nein"],
  ],
  [3200, 3500, 2660]
));

children.push(spacer());
children.push(heading("12.6 Wartungs-Commands", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Funktion", "Beschreibung"],
  [
    ["/maint version", "Zeigt Bot-Version, Python-Version, discord.py-Version, Uptime"],
    ["/maint network", "Testet Netzwerk-Konnektivit\u00E4t (DNS, Internet, Steam, Discord API)"],
    ["/maint ports", "Pr\u00FCft ob Game-Ports offen sind (7777, 15777, 15000)"],
    ["/maint tokens", "Validiert alle konfigurierten API-Tokens und Credentials"],
    ["/maint restart-bot", "Startet den Bot-Service selbst neu via systemctl"],
    ["/selftest", "F\u00FChrt vollst\u00E4ndigen System-Selbsttest durch (API, Backup, OneDrive, Email, Permissions)"],
    ["/commandlog", "Zeigt die letzten X ausgef\u00FChrten Slash-Commands mit User und Zeitstempel"],
    ["/crashlog", "Listet Crash-Replays oder l\u00E4dt spezifisches Replay als Datei herunter"],
    ["/rollback", "Zeigt Crash-Loop-Status, letztes gutes Save, Reset-Button"],
    ["/configbackup", "Erstellt manuelles Config-Backup und l\u00E4dt zu OneDrive hoch"],
  ],
  [3000, 6360]
));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 13. MINECRAFT-INTEGRATION — DETAILPLANUNG (Phase 14)
// ══════════════════════════════════════════════════════════════
children.push(heading("13. Minecraft-Integration \u2013 Detailplanung", HeadingLevel.HEADING_1));
children.push(para("Die Integration von zwei Minecraft-Servern in das bestehende Bot-System ist als **Phase 14** geplant. Die vorhandene Modulstruktur (modules/minecraft/) bietet Platzhalter, die vervollst\u00E4ndigt werden m\u00FCssen."));

children.push(spacer());
children.push(heading("13.1 Server-\u00DCbersicht", HeadingLevel.HEADING_2));
children.push(makeTable(
  ["Eigenschaft", "Server 1: Better Minecraft", "Server 2: Vanilla (sp\u00E4ter Mods)"],
  [
    ["Name", "MC-1 Better Minecraft", "MC-2 Vanilla+"],
    ["Modpack", "Better Minecraft (Forge/Fabric)", "Vanilla, sp\u00E4ter Mods (noch offen)"],
    ["Minecraft-Version", "Abh\u00E4ngig vom Modpack (1.20.x)", "Aktuelle Version (1.21.x)"],
    ["Server-Software", "Forge oder Fabric (je nach Modpack)", "Paper MC (sp\u00E4ter Fabric/Forge)"],
    ["Port (Game)", "25565", "25566"],
    ["Port (RCON)", "25575", "25576"],
    ["Port (Query)", "25565 (UDP)", "25566 (UDP)"],
    ["RAM-Zuweisung", "6-8 GB (Modded braucht mehr)", "4-6 GB"],
    ["systemd Service", "minecraft-1.service", "minecraft-2.service"],
    ["Install-Pfad", "/home/minecraft/server1/", "/home/minecraft/server2/"],
    ["World-Ordner", "/home/minecraft/server1/world/", "/home/minecraft/server2/world/"],
    ["Backup-Pfad", "backups/minecraft/server1/", "backups/minecraft/server2/"],
  ],
  [2500, 3430, 3430]
));

children.push(spacer());
children.push(heading("13.2 Server-Infrastruktur", HeadingLevel.HEADING_2));
children.push(para("Folgende Infrastruktur-\u00C4nderungen sind notwendig:"));
children.push(spacer());
children.push(makeTable(
  ["Schritt", "Beschreibung", "Befehl / Details"],
  [
    ["1. System-User", "Eigener User minecraft f\u00FCr beide Server", "sudo adduser --system --group --home /home/minecraft minecraft"],
    ["2. Java installieren", "OpenJDK 21 LTS f\u00FCr aktuelle MC-Versionen", "sudo apt install openjdk-21-jre-headless"],
    ["3. Server-Verzeichnisse", "Getrennte Ordner f\u00FCr beide Server", "mkdir -p /home/minecraft/server1 /home/minecraft/server2"],
    ["4. Server 1 installieren", "Better Minecraft Modpack herunterladen und entpacken", "Forge/Fabric Installer + Modpack-Dateien"],
    ["5. Server 2 installieren", "Paper MC Server-JAR herunterladen", "wget paper-mc.jar, java -jar paper.jar --nogui"],
    ["6. EULA akzeptieren", "eula.txt auf true setzen (beide Server)", "echo 'eula=true' > eula.txt"],
    ["7. server.properties", "Ports, RCON, Query, Whitelist konfigurieren", "server-port, rcon.port, enable-rcon=true, rcon.password"],
    ["8. systemd Services", "Eigene .service-Dateien f\u00FCr beide Server", "minecraft-1.service, minecraft-2.service (User=minecraft)"],
    ["9. sudoers erweitern", "botuser darf MC-Services steuern", "start|stop|restart minecraft-1.service, minecraft-2.service"],
    ["10. Firewall", "Ports in UFW \u00F6ffnen", "ufw allow 25565:25576/tcp, ufw allow 25565:25566/udp"],
    ["11. Backup-Ordner", "Getrennte Backup-Verzeichnisse", "mkdir -p backups/minecraft/server1 backups/minecraft/server2"],
  ],
  [2000, 3400, 3960]
));

children.push(spacer());
children.push(heading("13.3 Neue Minecraft-Commands (minecraft_cog.py)", HeadingLevel.HEADING_2));
children.push(para("Der minecraft_cog.py wird komplett \u00FCberarbeitet und erh\u00E4lt folgende Commands:"));
children.push(spacer());
children.push(makeTable(
  ["Command", "Berechtigung", "Beschreibung"],
  [
    ["/mc status [server]", "Alle", "Status beider/eines Servers: Online, Spieler, TPS, Version, RAM"],
    ["/mc start <server>", "Admin", "Minecraft-Server starten (1 oder 2)"],
    ["/mc stop <server>", "Admin", "Server stoppen mit Countdown + RCON-Warnung an Spieler"],
    ["/mc restart <server>", "Admin", "Neustart mit Sofort/5min/10min Auswahl (wie /sat restart)"],
    ["/mc cancel", "Admin", "Laufenden Countdown abbrechen"],
    ["/mc players <server>", "Spieler", "Online-Spieler mit Spielzeit anzeigen"],
    ["/mc console <server> <befehl>", "Owner", "RCON-Befehl direkt an Server senden"],
    ["/mc say <server> <nachricht>", "Admin", "Nachricht an alle Spieler senden (RCON say)"],
    ["/mc whitelist add|remove|list <server>", "Admin", "Whitelist verwalten via RCON"],
    ["/mc ban|unban|bans <server> <spieler>", "Admin", "Spieler bannen/entbannen via RCON"],
    ["/mc kick <server> <spieler> [grund]", "Admin", "Spieler kicken via RCON"],
    ["/mc backup create <server>", "Admin", "Manuelles World-Backup mit save-all + save-off"],
    ["/mc backup list <server>", "Alle", "Lokale Backups auflisten"],
    ["/mc backup restore <server>", "Owner", "Backup wiederherstellen (Server wird gestoppt)"],
    ["/mc tp <server> <spieler> <x> <y> <z>", "Admin", "Spieler teleportieren via RCON"],
    ["/mc gamemode <server> <spieler> <mode>", "Admin", "Spielmodus \u00E4ndern via RCON"],
    ["/mc weather <server> <wetter>", "Admin", "Wetter setzen (clear/rain/thunder)"],
    ["/mc time <server> <zeit>", "Admin", "Tageszeit setzen (day/night/noon)"],
    ["/mc difficulty <server> <schwierigkeit>", "Admin", "Schwierigkeit \u00E4ndern"],
    ["/mc mod list <server>", "Alle", "Installierte Mods auflisten"],
    ["/mc mod install <server> <mod>", "Owner", "Mod installieren (CurseForge/Modrinth)"],
  ],
  [3600, 1400, 4360]
));

children.push(spacer());
children.push(heading("13.4 Chat-Bridge (Minecraft \u2194 Discord)", HeadingLevel.HEADING_2));
children.push(para("F\u00FCr Minecraft ist eine **bidirektionale Chat-Bridge** geplant \u2014 im Gegensatz zu Satisfactory unterst\u00FCtzt Minecraft dies nativ \u00FCber RCON und Log-Parsing:"));
children.push(spacer());
children.push(makeTable(
  ["Richtung", "Methode", "Details"],
  [
    ["Discord \u2192 Minecraft", "RCON say/tellraw", "Nachrichten aus dem Discord-Channel werden per RCON als In-Game-Chat gesendet. Format: [DC] Username: Nachricht"],
    ["Minecraft \u2192 Discord", "Server-Log Parsing", "latest.log wird auf Chat-Nachrichten \u00FCberwacht (Regex). Nachrichten werden als Embed in den Discord-Channel gepostet"],
    ["Join/Leave", "Log Parsing", "Spieler-Joins und -Leaves werden automatisch erkannt und in Discord gemeldet"],
    ["Achievements", "Log Parsing", "Erfolge/Advancements werden in Discord gepostet"],
    ["Tod-Nachrichten", "Log Parsing", "Spieler-Tode werden in Discord gemeldet"],
    ["Server-Nachrichten", "RCON broadcast", "Admin kann per /mc say Nachrichten an alle senden"],
  ],
  [2200, 2400, 4760]
));

children.push(spacer());
children.push(heading("13.5 Monitor-Bot Erweiterungen", HeadingLevel.HEADING_2));
children.push(para("Der Monitor Bot wird um folgende Funktionen f\u00FCr Minecraft erweitert:"));
children.push(spacer());
children.push(makeTable(
  ["Feature", "Beschreibung", "Umsetzung"],
  [
    ["Health Check", "Alle 120s: Prozess-Check + RCON-Ping f\u00FCr beide MC-Server. Crash-Erkennung und Auto-Restart.", "health_checker erweitern um MinecraftServer-Instanzen"],
    ["Player Tracking", "Join/Leave per Log-Parsing. Spielzeit-Tracking pro Server getrennt.", "player_tracker pro Server-Instanz"],
    ["TPS-\u00DCberwachung", "Ticks Per Second via RCON (/forge tps oder mspt). Warnung bei TPS < 15.", "RCON-Befehl periodisch, Schwellwert konfigurierbar"],
    ["Status Embed", "Dashboard zeigt beide MC-Server unter dem Satisfactory-Eintrag: Status, Spieler, TPS, Version.", "Status-Embed in monitor_bot.py erweitern"],
    ["Voice Channel", "Zwei zus\u00E4tzliche Voice-Channels: 'MC-1 | \uD83D\uDFE2 X/Y' und 'MC-2 | \uD83D\uDFE2 X/Y'", "update_voice_stats() um MC-Server erweitern"],
    ["Auto-Backup", "World-Backups alle 6h mit save-all + save-off vor Backup, save-on danach. OneDrive-Upload.", "scheduler_cog.py + minecraft/backup.py"],
    ["Daily Restart", "Geplanter Neustart um 05:00 (1h nach Satisfactory) mit Countdown.", "scheduler_cog.py erweitern"],
    ["Crash Replay", "Log-Kontext bei Crashes speichern (letzte 50 Zeilen von latest.log).", "crash_replay erweitern oder eigene Instanz"],
    ["Performance", "RAM-Verbrauch pro MC-Server \u00FCberwachen. Java-Heap-Nutzung.", "perf_monitor.collect() um MC-Prozesse erweitern"],
    ["Update Check", "MC-Version gegen Mojang-API pr\u00FCfen. Paper/Forge Updates erkennen.", "Eigener MC-UpdateChecker"],
  ],
  [2000, 3500, 3860]
));

children.push(spacer());
children.push(heading("13.6 .env Erweiterungen", HeadingLevel.HEADING_2));
children.push(para("Folgende neue Umgebungsvariablen werden f\u00FCr Minecraft ben\u00F6tigt:"));
children.push(spacer());
children.push(makeTable(
  ["Variable", "Beispielwert", "Beschreibung"],
  [
    ["MINECRAFT_ENABLED", "true", "Feature-Flag f\u00FCr Minecraft-Integration"],
    ["MINECRAFT_USER", "minecraft", "System-User f\u00FCr MC-Server"],
    ["MC1_SERVICE", "minecraft-1.service", "systemd Service-Name Server 1"],
    ["MC1_SERVER_PATH", "/home/minecraft/server1", "Installationspfad Server 1"],
    ["MC1_RCON_HOST", "127.0.0.1", "RCON-Host Server 1"],
    ["MC1_RCON_PORT", "25575", "RCON-Port Server 1"],
    ["MC1_RCON_PASSWORD", "GeheimesPasswort1", "RCON-Passwort Server 1"],
    ["MC1_WORLD_PATH", "/home/minecraft/server1/world", "World-Ordner f\u00FCr Backups"],
    ["MC1_LOG_PATH", "/home/minecraft/server1/logs/latest.log", "Server-Log f\u00FCr Chat-Bridge"],
    ["MC2_SERVICE", "minecraft-2.service", "systemd Service-Name Server 2"],
    ["MC2_SERVER_PATH", "/home/minecraft/server2", "Installationspfad Server 2"],
    ["MC2_RCON_HOST", "127.0.0.1", "RCON-Host Server 2"],
    ["MC2_RCON_PORT", "25576", "RCON-Port Server 2"],
    ["MC2_RCON_PASSWORD", "GeheimesPasswort2", "RCON-Passwort Server 2"],
    ["MC_CHAT_CHANNEL_1", "Channel-ID", "Discord-Channel f\u00FCr MC-1 Chat-Bridge"],
    ["MC_CHAT_CHANNEL_2", "Channel-ID", "Discord-Channel f\u00FCr MC-2 Chat-Bridge"],
    ["MC_VOICE_STATS_1", "Channel-ID", "Voice-Channel f\u00FCr MC-1 Status"],
    ["MC_VOICE_STATS_2", "Channel-ID", "Voice-Channel f\u00FCr MC-2 Status"],
    ["MINECRAFT_ROLE_ID", "Rollen-ID", "Discord-Rolle f\u00FCr MC-Spieler (Berechtigungen)"],
  ],
  [2500, 2800, 4060]
));

children.push(spacer());
children.push(heading("13.7 Implementierungsreihenfolge", HeadingLevel.HEADING_2));
children.push(para("Die Umsetzung erfolgt in folgenden Schritten:"));
children.push(spacer());
children.push(makeTable(
  ["Phase", "Aufgabe", "Abh\u00E4ngigkeiten"],
  [
    ["14a", "System-User minecraft + Java + Verzeichnisse anlegen", "Keine (Server-Setup)"],
    ["14b", "Server 1 (Better Minecraft) installieren und konfigurieren", "14a"],
    ["14c", "Server 2 (Vanilla/Paper) installieren und konfigurieren", "14a"],
    ["14d", "systemd Services erstellen, sudoers + Firewall anpassen", "14b, 14c"],
    ["14e", "modules/minecraft/server.py vervollst\u00E4ndigen (Start/Stop/Status)", "14d"],
    ["14f", "modules/minecraft/rcon.py implementieren (RCON-Client)", "14d"],
    ["14g", "minecraft_cog.py: Basis-Commands (status/start/stop/restart/players)", "14e, 14f"],
    ["14h", "minecraft_cog.py: RCON-Commands (console/say/whitelist/ban/kick)", "14f"],
    ["14i", "modules/minecraft/backup.py: World-Backup mit save-all/save-off", "14e, 14f"],
    ["14j", "Chat-Bridge: Discord \u2194 Minecraft (Log-Parsing + RCON)", "14f"],
    ["14k", "Monitor Bot: Health-Check + Player-Tracking f\u00FCr MC", "14e"],
    ["14l", "Monitor Bot: Status-Embed + Voice-Channel f\u00FCr MC", "14k"],
    ["14m", "Monitor Bot: Auto-Backup + Daily Restart f\u00FCr MC", "14i, 14k"],
    ["14n", "Mod-Verwaltung f\u00FCr Server 2 (sp\u00E4tere Mods)", "14c"],
    ["14o", "Test, Dokumentation aktualisieren", "Alle"],
  ],
  [1200, 5000, 3160]
));

children.push(spacer());
children.push(heading("13.8 RAM-Planung", HeadingLevel.HEADING_2));
children.push(para("Der Netcup RS 4000 G12 hat **32 GB RAM**. Geplante Aufteilung:"));
children.push(spacer());
children.push(makeTable(
  ["Dienst", "RAM", "Anmerkung"],
  [
    ["Ubuntu + System", "2 GB", "Basis-Betriebssystem"],
    ["Satisfactory Server", "8-12 GB", "Skaliert mit Fabrikgr\u00F6\u00DFe"],
    ["Minecraft Server 1 (Better MC)", "6-8 GB", "Modded braucht mehr RAM (-Xmx8G)"],
    ["Minecraft Server 2 (Vanilla+)", "4-6 GB", "Vanilla/Paper effizienter (-Xmx6G)"],
    ["Discord Bots (2x)", "0.5 GB", "Python-Prozesse sind schlank"],
    ["Reserve", "2-4 GB", "F\u00FCr Peaks und System-Cache"],
    ["GESAMT", "~28-32 GB", "Alle Server gleichzeitig m\u00F6glich"],
  ],
  [3000, 1800, 4560]
));
children.push(para("**Hinweis:** Wenn alle drei Gameserver gleichzeitig laufen, wird der RAM knapp. Bei Engp\u00E4ssen kann der Optimizer automatisch Cache-Drops durchf\u00FChren. Falls dauerhaft zu wenig RAM, muss einer der MC-Server reduziert oder der vServer aufger\u00FCstet werden."));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ══════════════════════════════════════════════════════════════
// 14. NÄCHSTE SCHRITTE
// ══════════════════════════════════════════════════════════════
children.push(heading("14. N\u00E4chste Schritte \u2013 Alle geplanten Aufgaben", HeadingLevel.HEADING_1));
children.push(para("Nachfolgend alle geplanten und offenen Aufgaben in der vorgesehenen Reihenfolge. Jede Aufgabe wird mit Kontext, betroffenen Dateien und erwarteten \u00C4nderungen beschrieben."));

// ── 14.1 /clear Progress Fix ──
children.push(spacer());
children.push(heading("14.1 Bug-Fix: /clear Fortschrittsanzeige", HeadingLevel.HEADING_2));
children.push(para("**Status:** Offen | **Priorit\u00E4t:** Hoch | **Datei:** cogs/general_cog.py"));
children.push(spacer());
children.push(para("**Problem:** Beim L\u00F6schen von Nachrichten mit /clear wird dem Benutzer kein Fortschritt angezeigt. Der Code enth\u00E4lt zwar einen Fortschritts-Update alle 50 Nachrichten via interaction.edit_original_response(), aber dieser funktioniert nicht korrekt."));
children.push(spacer());
children.push(para("**Vermutete Ursache:** Der initiale followup.send() sendet die Startmeldung, aber danach wird versucht, edit_original_response() zu nutzen. Bei ephemeral defer k\u00F6nnte die Referenz auf die Original-Response verloren gehen, da followup.send() eine neue Nachricht erstellt."));
children.push(spacer());
children.push(para("**Geplante L\u00F6sung:**"));
children.push(codePara("1. followup.send() durch edit_original_response() ersetzen fuer die initiale Meldung"));
children.push(codePara("2. Oder: followup.send() Referenz speichern und per message.edit() aktualisieren"));
children.push(codePara("3. Fortschrittsanzeige auch fuer Bulk-Delete hinzufuegen (nicht nur fuer alte Nachrichten)"));
children.push(codePara("4. Abschlussmeldung mit Zusammenfassung: X Nachrichten geloescht (Y Bulk + Z einzeln)"));
children.push(spacer());
children.push(para("**Betroffene Code-Stelle:** Zeilen 236-350 in general_cog.py, insbesondere die Logik um interaction.followup.send() und interaction.edit_original_response(). Der Fix muss sicherstellen, dass bei grossen Mengen alter Nachrichten der Benutzer den Fortschritt in Echtzeit sieht."));

// ── 14.2 Code-Review ──
children.push(spacer());
children.push(heading("14.2 Komplette Code-\u00DCberpr\u00FCfung aller Dateien", HeadingLevel.HEADING_2));
children.push(para("**Status:** Offen | **Priorit\u00E4t:** Hoch"));
children.push(spacer());
children.push(para("Als n\u00E4chster grosser Arbeitsschritt steht eine **komplette \u00DCberpr\u00FCfung aller 56 Python-Dateien** an. Ziel ist es, verbliebene Bugs, inkonsistente Imports, ungenutzte Variablen und potenzielle Fehlerquellen systematisch zu identifizieren und zu beheben."));
children.push(spacer());
children.push(para("**Pr\u00FCfkategorien pro Datei:**"));
children.push(codePara("- Import-Analyse: Fehlende oder ungenutzte Imports identifizieren"));
children.push(codePara("- Fehlerbehandlung: Alle try/except-Bloecke auf korrekte Exception-Typen pruefen"));
children.push(codePara("- Async/Await: Race Conditions, fehlende awaits, Timeout-Handling"));
children.push(codePara("- Logging: Konsistente Nutzung von get_logger(), korrekte Log-Level"));
children.push(codePara("- Naming-Konventionen: self.maint vs self.maintenance Problem (siehe 10.1)"));
children.push(codePara("- Typ-Annotationen: Optional[], Union[] korrekt genutzt"));
children.push(codePara("- Permission-Checks: admin_only(), owner_only() an allen richtigen Stellen"));
children.push(codePara("- Ressourcen-Management: Dateien, Verbindungen korrekt geschlossen"));
children.push(spacer());
children.push(para("**Reihenfolge der \u00DCberpr\u00FCfung:**"));
children.push(spacer());
children.push(makeTable(
  ["Reihenfolge", "Bereich", "Dateien", "Fokus"],
  [
    ["1", "Bot-Hauptdateien", "bots/gameserver_bot.py, bots/monitor_bot.py", "Cog-Loading, Service-Init, Background-Tasks, Event-Loops"],
    ["2", "Cogs (Commands)", "cogs/*.py (7 Dateien)", "Command-Logik, Permission-Checks, Error-Handling, UI-Views"],
    ["3", "Satisfactory Module", "modules/satisfactory/*.py (9 Dateien)", "API-Calls, Server-Management, Savegame-Analyse"],
    ["4", "Monitoring Module", "modules/monitoring/*.py (14 Dateien)", "Health-Checks, Tracker, Scheduler-Integration"],
    ["5", "Backup Module", "modules/backup/*.py (3 Dateien)", "Datei-Operationen, OneDrive/rclone, Rotation"],
    ["6", "Notification Module", "modules/notifications/*.py (2 Dateien)", "Discord-Embeds, SMTP, Rate-Limiting"],
    ["7", "Sonstige Module", "modules/*.py (7 Dateien)", "Timer, Filter, Spam, Logger, Validator"],
    ["8", "Utils", "utils/*.py (4 Dateien)", "Config-Loader, Formatting, Permissions, Logger-Setup"],
  ],
  [1200, 2200, 3200, 2760]
));
children.push(spacer());
children.push(para("**Erwartetes Ergebnis:** Eine Liste aller gefundenen Probleme mit konkreten Fixes. Jeder Fix wird in der Dokumentation unter Kapitel 10 nachgetragen. Nach der Review sollen alle Module fehlerfrei zusammenarbeiten und der Code konsistent formatiert sein."));

// ── 14.3 Server-Bereinigung ──
children.push(spacer());
children.push(heading("14.3 Server-Bereinigung", HeadingLevel.HEADING_2));
children.push(para("**Status:** Offen | **Priorit\u00E4t:** Niedrig"));
children.push(spacer());
children.push(para("Auf dem Server befinden sich verwaiste SSH-Key-Dateien im Home-Verzeichnis von marco, die beim initialen SSH-Setup durch einen Windows-Pfad-Fehler entstanden sind:"));
children.push(codePara("rm ~/'\":USERPROFILE\\.ssh\\botuser_key\"'"));
children.push(codePara("rm ~/'\":USERPROFILE\\.ssh\\botuser_key.pub\"'"));
children.push(spacer());
children.push(para("Diese Dateien haben keinen Einfluss auf die Funktion, sollten aber zur Sauberkeit entfernt werden. Der korrekte SSH-Key liegt unter **/home/marco/.ssh/botuser_key**."));

// ── 14.4 Deployment-Workflow ──
children.push(spacer());
children.push(heading("14.4 Aktueller Deployment-Workflow", HeadingLevel.HEADING_2));
children.push(para("**Wichtig:** Es gibt kein GitHub/Git-Repository. Deployment erfolgt manuell per FileZilla."));
children.push(spacer());
children.push(para("**Schritt-f\u00FCr-Schritt Deployment:**"));
children.push(spacer());
children.push(makeTable(
  ["Schritt", "Aktion", "Details"],
  [
    ["1", "Dateien lokal bearbeiten", "Python-Dateien im lokalen Discord_Bots Ordner \u00E4ndern"],
    ["2", "FileZilla \u00F6ffnen", "Verbindung: sftp://203.0.113.10:4422, User: botuser, Key: botuser_key"],
    ["3", "Ge\u00E4nderte .py hochladen", "NUR die ge\u00E4nderten Dateien! NICHT config/, data/, venv/, logs/ \u00FCberschreiben"],
    ["4", "SSH-Verbindung \u00F6ffnen", "ssh -p 4422 marco@203.0.113.10"],
    ["5", "Bots neustarten", "sudo systemctl restart gameserver-bot.service monitor-bot.service"],
    ["6", "Logs pr\u00FCfen", "sudo journalctl -u gameserver-bot.service -n 30 --no-pager"],
    ["7", "Cog-Count verifizieren", "GameServer: 5 cogs, 9 commands. Monitor: 2 cogs, 14 commands"],
    ["8", "Funktionstest", "In Discord: /ping, /sat status, /performance testen"],
  ],
  [1200, 3000, 5160]
));
children.push(spacer());
children.push(para("**KRITISCH: Niemals folgende Dateien/Ordner per FileZilla \u00FCberschreiben:**"));
children.push(codePara("config/.env         - Enth\u00E4lt echte Tokens und Passw\u00F6rter"));
children.push(codePara("config/config.json  - Enth\u00E4lt aktive Server-Konfiguration"));
children.push(codePara("data/               - Persistente Daten (Stats, Tracker, Caches)"));
children.push(codePara("venv/               - Python Virtual Environment (nur auf Server)"));
children.push(codePara("logs/               - Laufende Log-Dateien"));
children.push(codePara("backups/            - Savegame-Backups"));
children.push(codePara("requirements.txt    - Nur auf Server relevant (pip freeze)"));

// ── 14.5 Bot-Neustart und Wartung ──
children.push(spacer());
children.push(heading("14.5 Bot-Neustart und Wartungs-Befehle", HeadingLevel.HEADING_2));
children.push(para("H\u00E4ufig ben\u00F6tigte Wartungsbefehle auf dem Server:"));
children.push(spacer());
children.push(makeTable(
  ["Aktion", "Befehl", "Anmerkung"],
  [
    ["Beide Bots neustarten", "sudo systemctl restart gameserver-bot.service monitor-bot.service", "Nach jedem Deployment"],
    ["Nur GameServer Bot", "sudo systemctl restart gameserver-bot.service", "Bei Cog-\u00C4nderungen"],
    ["Nur Monitor Bot", "sudo systemctl restart monitor-bot.service", "Bei Monitor-\u00C4nderungen"],
    ["Satisfactory neustarten", "sudo systemctl restart satisfactory.service", "Bei H\u00E4nger oder Update"],
    ["Bot-Logs live anzeigen", "sudo journalctl -u gameserver-bot.service -f", "Ctrl+C zum Beenden"],
    ["Monitor-Logs live", "sudo journalctl -u monitor-bot.service -f", "Ctrl+C zum Beenden"],
    ["Satisfactory-Logs", "sudo journalctl -u satisfactory.service -n 100 --no-pager", "Letzte 100 Zeilen"],
    ["Bot-Status pr\u00FCfen", "sudo systemctl status gameserver-bot.service monitor-bot.service", "Zeigt ob aktiv/fehlgeschlagen"],
    ["venv aktivieren", "source /home/botuser/Discord_Bots/venv/bin/activate", "F\u00FCr pip install etc."],
    ["Neues Paket installieren", "pip install paketname (in aktivem venv)", "Danach Bots neustarten"],
    ["System-RAM pr\u00FCfen", "free -h", "Wichtig bei allen 3 Servern"],
    ["Disk-Belegung", "df -h /", "Warnung bei > 90%"],
    ["Prozesse anzeigen", "htop oder ps aux | grep -E 'python|satisfactory'", "\u00DCbersicht aller Game/Bot-Prozesse"],
  ],
  [2500, 4500, 2360]
));

// ── 14.6 Geplante Verbesserungen ──
children.push(spacer());
children.push(heading("14.6 Geplante Verbesserungen (nach Code-Review)", HeadingLevel.HEADING_2));
children.push(para("Nach Abschluss des Code-Reviews und vor der Minecraft-Integration sind folgende Verbesserungen geplant:"));
children.push(spacer());
children.push(makeTable(
  ["Verbesserung", "Datei(en)", "Beschreibung", "Priorit\u00E4t"],
  [
    ["/clear Fortschrittsanzeige", "general_cog.py", "Fortschrittsbalken/Prozentanzeige beim L\u00F6schen, auch f\u00FCr Bulk-Delete sichtbar", "Hoch"],
    ["Error-Recovery verbessern", "health_check.py", "Nach fehlgeschlagenem Auto-Restart: Exponentielles Backoff statt sofortigem Retry", "Mittel"],
    ["Backup-Verify erweitern", "backup_manager.py", "tar.gz-Integrit\u00E4tspr\u00FCfung: Nicht nur Dateiexistenz, sondern auch Entpack-Test", "Mittel"],
    ["Stats-Dashboard erweitern", "monitor_cog.py", "Graphische Spielzeit-Trends (letzte 7 Tage) im /report Command", "Niedrig"],
    ["Config Hot-Reload", "utils/config.py", "config.json \u00C4nderungen ohne Bot-Neustart \u00FCbernehmen", "Niedrig"],
    ["Command-Cooldowns", "satisfactory_cog.py", "Cooldowns f\u00FCr /sat start und /sat restart (60s) um versehentliche Doppel-Aufrufe zu verhindern", "Mittel"],
    ["Audit-Log erweitern", "command_logger.py", "Auch fehlgeschlagene Commands und Permission-Denials loggen", "Niedrig"],
    ["Savegame-Gr\u00F6ssen-Trend", "savegame_protection.py", "Warnung wenn Savegame-Gr\u00F6sse pl\u00F6tzlich um >50% schrumpft (m\u00F6gliche Korruption)", "Mittel"],
  ],
  [2200, 2200, 3200, 1740]
));

// ── 14.7 Minecraft-Integration ──
children.push(spacer());
children.push(heading("14.7 Minecraft-Integration (Phase 14a\u201314o)", HeadingLevel.HEADING_2));
children.push(para("Die vollst\u00E4ndige Minecraft-Integration ist in Kapitel 13 detailliert beschrieben. Sie umfasst 15 Phasen (14a bis 14o) und wird nach dem Code-Review gestartet. Zusammenfassung der Meilensteine:"));
children.push(spacer());
children.push(makeTable(
  ["Meilenstein", "Phasen", "Beschreibung", "Gesch\u00E4tzter Aufwand"],
  [
    ["Server-Setup", "14a\u201314d", "User anlegen, Java installieren, beide Server konfigurieren, systemd Services, sudoers, Firewall", "1\u20132 Tage"],
    ["Basis-Steuerung", "14e\u201314g", "modules/minecraft/server.py + rcon.py implementieren, minecraft_cog.py Basis-Commands", "1\u20132 Tage"],
    ["Erweiterte Commands", "14h\u201314i", "RCON-Commands (console, say, whitelist, ban), World-Backup mit save-all/save-off", "1 Tag"],
    ["Chat-Bridge", "14j", "Bidirektionale Chat-Bridge: Discord \u2194 Minecraft per Log-Parsing + RCON", "1 Tag"],
    ["Monitor-Integration", "14k\u201314m", "Health-Check, Player-Tracking, Status-Embed, Voice-Channels, Auto-Backup, Daily Restart", "2\u20133 Tage"],
    ["Mod-Verwaltung", "14n", "Mod-Installation f\u00FCr Server 2 (Vanilla+ mit sp\u00E4teren Mods)", "0.5 Tage"],
    ["Test + Doku", "14o", "Vollst\u00E4ndiger Test aller MC-Features, Dokumentation aktualisieren", "1 Tag"],
  ],
  [2000, 1400, 3800, 2160]
));
children.push(spacer());
children.push(para("**Gesamtaufwand Minecraft-Integration: ca. 7\u201311 Arbeitstage.** Die Integration kann parallel zum laufenden Satisfactory-Betrieb erfolgen, da alle neuen Module unabh\u00E4ngig sind."));

// ── 14.8 Langfrist-Planung ──
children.push(spacer());
children.push(heading("14.8 Langfrist-Planung", HeadingLevel.HEADING_2));
children.push(para("Folgende Features sind f\u00FCr sp\u00E4tere Versionen angedacht, aber noch nicht konkret geplant:"));
children.push(spacer());
children.push(makeTable(
  ["Feature", "Beschreibung", "Voraussetzung"],
  [
    ["Web-Dashboard", "Browser-basiertes Dashboard mit Echtzeit-Status aller Server, Grafana-\u00E4hnlich", "Alle Server laufen stabil"],
    ["Discord-Bot Cluster", "Mehrere Bot-Instanzen f\u00FCr Ausfallsicherheit", "Aktuell nicht notwendig bei einem Server"],
    ["Automatisches Mod-Update", "Mods automatisch auf neue Versionen pr\u00FCfen und aktualisieren", "Minecraft-Integration abgeschlossen"],
    ["Spieler-Economy", "Discord-basiertes Punkt/Rang-System basierend auf Spielzeit", "Player-Tracking stabil"],
    ["Server-Migration", "Tools f\u00FCr einfache Migration auf neuen vServer falls Upgrade n\u00F6tig", "Wenn RAM nicht mehr ausreicht"],
    ["Backup-Encryption", "Verschl\u00FCsselte Backups f\u00FCr zus\u00E4tzliche Sicherheit bei Cloud-Upload", "Wenn sensible Weltdaten vorhanden"],
  ],
  [2500, 4500, 2360]
));

// ── 14.9 Aufgaben-Zusammenfassung ──
children.push(spacer());
children.push(heading("14.9 Aufgaben-Zusammenfassung und Reihenfolge", HeadingLevel.HEADING_2));
children.push(para("Komplette \u00DCbersicht aller offenen Aufgaben in der geplanten Reihenfolge:"));
children.push(spacer());
children.push(makeTable(
  ["Nr.", "Aufgabe", "Status", "Abh\u00E4ngigkeit"],
  [
    ["1", "/clear Fortschrittsanzeige reparieren (Kapitel 14.1)", "Offen", "Keine"],
    ["2", "Code-Review aller 56 Python-Dateien (Kapitel 14.2)", "Offen", "Keine"],
    ["3", "Server-Bereinigung: Verwaiste SSH-Keys l\u00F6schen (Kapitel 14.3)", "Offen", "Keine"],
    ["4", "Gefundene Bugs aus Code-Review beheben", "Offen", "Aufgabe 2"],
    ["5", "Geplante Verbesserungen umsetzen (Kapitel 14.6)", "Offen", "Aufgabe 4"],
    ["6", "Dokumentation mit Review-Ergebnissen aktualisieren", "Offen", "Aufgabe 4"],
    ["7", "Minecraft Server-Setup: User, Java, Verzeichnisse (Phase 14a\u201314d)", "Offen", "Aufgabe 5"],
    ["8", "Minecraft Bot-Integration: server.py, rcon.py, minecraft_cog.py (Phase 14e\u201314h)", "Offen", "Aufgabe 7"],
    ["9", "Minecraft Backup-System (Phase 14i)", "Offen", "Aufgabe 8"],
    ["10", "Minecraft Chat-Bridge (Phase 14j)", "Offen", "Aufgabe 8"],
    ["11", "Monitor Bot Minecraft-Erweiterung (Phase 14k\u201314m)", "Offen", "Aufgabe 8"],
    ["12", "Minecraft Mod-Verwaltung (Phase 14n)", "Offen", "Aufgabe 7"],
    ["13", "Kompletter Test + Dokumentation aktualisieren (Phase 14o)", "Offen", "Aufgabe 11, 12"],
  ],
  [800, 5200, 1200, 2160]
));

// ══════════════════════════════════════════════════════════════
// BUILD DOCUMENT
// ══════════════════════════════════════════════════════════════

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: BLUE_DARK },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: BLUE_MED, space: 4 } } },
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: BLUE_MED },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 },
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 }, // A4
        margin: { top: 1200, right: 1200, bottom: 1200, left: 1200 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: BLUE_MED, space: 4 } },
          children: [
            new TextRun({ text: "Discord Bot System \u2022 Projektdokumentation", font: "Arial", size: 18, color: GRAY_MED }),
            new TextRun({ text: "\tv2.2.0", font: "Arial", size: 18, color: GRAY_MED }),
          ],
          tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ text: "Seite ", font: "Arial", size: 18, color: GRAY_MED }),
            new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 18, color: GRAY_MED }),
          ],
        })],
      }),
    },
    children,
  }],
});

Packer.toBuffer(doc).then(buffer => {
  const outPath = "/sessions/amazing-optimistic-bardeen/mnt/DIscord_Bots/docs/Projektdokumentation_v2.2.0.docx";
  fs.writeFileSync(outPath, buffer);
  console.log(`Document written: ${outPath} (${(buffer.length / 1024).toFixed(0)} KB)`);
});
