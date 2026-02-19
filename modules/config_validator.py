"""
Config Validator — Phase 9b
Validates all configuration on bot startup.
"""

import json
from pathlib import Path
from typing import Optional, Tuple, List

from utils.logger import get_logger
from utils.config import PROJECT_ROOT, get_env

logger = get_logger("config_validator")


class ConfigValidationError:
    def __init__(self, field: str, message: str, severity: str = "error") -> None:
        self.field = field
        self.message = message
        self.severity = severity  # "error" or "warning"

    def __repr__(self) -> str:  # type: ignore
        icon = "❌" if self.severity == "error" else "⚠️"
        return f"{icon} {self.field}: {self.message}"


class ConfigValidator:
    """
    Validates bot configuration on startup.
    Checks ENV vars, paths, tokens, and config.json.
    """

    def validate_all(self) -> Tuple[bool, List[ConfigValidationError]]:  # type: ignore
        """
        Run all validations.
        Returns (all_passed, list_of_errors).
        """
        errors = []

        errors.extend(self._check_required_env())
        errors.extend(self._check_paths())
        errors.extend(self._check_config_json())
        errors.extend(self._check_tokens())
        errors.extend(self._check_minecraft())

        has_critical = any(e.severity == "error" for e in errors)
        return (not has_critical, errors)

    def _check_required_env(self) -> List[ConfigValidationError]:  # type: ignore
        """Check required environment variables"""
        errors = []
        required = {
            "DISCORD_TOKEN_MANAGER": "Manager Bot Token",
            "DISCORD_TOKEN_WATCHDOG": "Monitor Bot Token",
            "GUILD_ID": "Discord Server ID",
            "OWNER_ID": "Bot Owner ID",
            "API_TOKEN": "Satisfactory API Token",
        }

        for key, desc in required.items():
            val = get_env(key, "")
            if not val:
                errors.append(ConfigValidationError(
                    key, f"{desc} nicht gesetzt", "error"
                ))

        optional_env = {
            "ADMIN_LOG_CHANNEL_ID": "Admin-Log Channel",
            "STATUS_EMBED_CHANNEL_ID": "Status-Embed Channel",
            "GAME_CHAT_CHANNEL_ID": "Game-Chat Channel",
        }

        for key, desc in optional_env.items():
            val = get_env(key, "")
            if not val or val == "0":
                errors.append(ConfigValidationError(
                    key, f"{desc} nicht gesetzt — Feature deaktiviert", "warning"
                ))

        return errors

    def _check_paths(self) -> List[ConfigValidationError]:  # type: ignore
        """Check important paths exist"""
        errors = []

        # Satisfactory server path
        server_path = get_env(
            "SATISFACTORY_SERVER_PATH",
            "/home/satisfactory/SatisfactoryDedicatedServer"
        )
        if not Path(server_path).exists():
            errors.append(ConfigValidationError(
                "SATISFACTORY_SERVER_PATH",
                f"Server-Pfad nicht gefunden: {server_path}",
                "error"
            ))

        # Savegame path
        save_path = get_env(
            "SATISFACTORY_SAVE_PATH",
            "/home/satisfactory/.config/Epic/FactoryGame/Saved/SaveGames"
        )
        if not Path(save_path).exists():
            errors.append(ConfigValidationError(
                "SATISFACTORY_SAVE_PATH",
                f"Savegame-Pfad nicht gefunden: {save_path}",
                "warning"
            ))

        # Data directory
        data_dir = PROJECT_ROOT / "data"
        if not data_dir.exists():
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.debug(f"Failed to create data directory: {e}")
                errors.append(ConfigValidationError(
                    "data_dir",
                    f"Kann data/ Verzeichnis nicht erstellen",
                    "error"
                ))

        # Backup directory
        backup_path = get_env("BACKUP_PATH", str(PROJECT_ROOT / "backups"))
        bp = Path(backup_path)
        if not bp.exists():
            try:
                bp.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.debug(f"Failed to create backup directory: {e}")
                errors.append(ConfigValidationError(
                    "BACKUP_PATH",
                    f"Backup-Pfad nicht erstellbar: {backup_path}",
                    "warning"
                ))

        return errors

    def _check_config_json(self) -> List[ConfigValidationError]:  # type: ignore
        """Validate config.json structure"""
        errors = []
        cfg_path = PROJECT_ROOT / "config" / "config.json"

        if not cfg_path.exists():
            errors.append(ConfigValidationError(
                "config.json", "Datei nicht gefunden", "error"
            ))
            return errors

        try:
            with open(cfg_path, "r") as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(ConfigValidationError(
                "config.json", f"JSON ungueltig: {e}", "error"
            ))
            return errors

        # Check required sections
        required_sections = ["features", "thresholds", "scheduler"]
        for section in required_sections:
            if section not in config:
                errors.append(ConfigValidationError(
                    f"config.json[{section}]",
                    f"Abschnitt '{section}' fehlt",
                    "warning"
                ))

        # Validate threshold values
        thresholds = config.get("thresholds", {})
        for key in ["cpu_warning", "ram_warning", "disk_warning"]:
            val = thresholds.get(key)
            if val is not None and (not isinstance(val, (int, float)) or val < 0 or val > 100):
                errors.append(ConfigValidationError(
                    f"thresholds.{key}",
                    f"Ungültiger Wert: {val} (erwartet: 0-100)",
                    "warning"
                ))

        # Validate scheduler
        sched = config.get("scheduler", {})
        hour = sched.get("daily_restart_hour", 4)
        if not isinstance(hour, int) or hour < 0 or hour > 23:
            errors.append(ConfigValidationError(
                "scheduler.daily_restart_hour",
                f"Ungültige Stunde: {hour}",
                "warning"
            ))

        return errors

    def _check_tokens(self) -> List[ConfigValidationError]:  # type: ignore
        """Basic token format validation (not API calls)"""
        errors = []

        # Discord tokens should be reasonably long
        for token_key in ["DISCORD_TOKEN_MANAGER", "DISCORD_TOKEN_WATCHDOG"]:
            token = get_env(token_key, "")
            if token and len(token) < 50:
                errors.append(ConfigValidationError(
                    token_key,
                    "Token scheint zu kurz zu sein",
                    "warning"
                ))

        # Guild ID should be numeric
        guild = get_env("GUILD_ID", "")
        if guild and not guild.isdigit():
            errors.append(ConfigValidationError(
                "GUILD_ID", f"Muss numerisch sein, ist: {guild[:20]}", "error"
            ))

        return errors

    def _check_minecraft(self) -> List[ConfigValidationError]:  # type: ignore
        """Minecraft Multi-Server Konfiguration pruefen (nur wenn aktiviert)"""
        errors = []
        mc_server_ids = ["BMC", "VANILLA"]

        any_enabled = False
        for sid in mc_server_ids:
            service = get_env(f"MC_{sid}_SERVICE", "")
            if not service:
                continue

            any_enabled = True

            # RCON-Passwort pruefen
            rcon_pw = get_env(f"MC_{sid}_RCON_PASSWORD", "")
            if not rcon_pw:
                errors.append(ConfigValidationError(
                    f"MC_{sid}_RCON_PASSWORD",
                    f"RCON-Passwort fuer {sid} nicht gesetzt — RCON deaktiviert",
                    "warning"
                ))

            # RCON-Port pruefen
            rcon_port = get_env(f"MC_{sid}_RCON_PORT", "0")
            try:
                port_int = int(rcon_port)
                if port_int < 1024 or port_int > 65535:
                    errors.append(ConfigValidationError(
                        f"MC_{sid}_RCON_PORT",
                        f"Ungueltiger Port: {rcon_port}",
                        "warning"
                    ))
            except ValueError:
                errors.append(ConfigValidationError(
                    f"MC_{sid}_RCON_PORT",
                    f"Keine gueltige Zahl: {rcon_port}",
                    "error"
                ))

            # Server-Pfad pruefen (nur Warnung — MC evtl. noch nicht installiert)
            server_path = get_env(f"MC_{sid}_PATH", "")
            if server_path:
                try:
                    sp = Path(server_path)
                    if not sp.exists():
                        errors.append(ConfigValidationError(
                            f"MC_{sid}_PATH",
                            f"Server-Pfad nicht gefunden: {server_path} "
                            f"(normal wenn MC noch nicht installiert)",
                            "warning"
                        ))
                    else:
                        # Log-Pfad pruefen
                        log_path = sp / "logs" / "latest.log"
                        if not log_path.exists():
                            errors.append(ConfigValidationError(
                                f"MC_{sid}_LOG",
                                f"Log-Datei nicht gefunden: {log_path} "
                                f"(wird nach erstem Start erstellt)",
                                "warning"
                            ))

                        # server.properties pruefen
                        props_path = sp / "server.properties"
                        if not props_path.exists():
                            errors.append(ConfigValidationError(
                                f"MC_{sid}_PROPERTIES",
                                f"server.properties nicht gefunden: {props_path} "
                                f"(wird nach erstem Start erstellt)",
                                "warning"
                            ))
                except PermissionError:
                    errors.append(ConfigValidationError(
                        f"MC_{sid}_PATH",
                        f"Keine Leseberechtigung fuer: {server_path}",
                        "warning"
                    ))

            # Backup-Pfad pruefen
            backup_path = get_env(f"MC_{sid}_BACKUP_PATH", "")
            if backup_path:
                bp = Path(backup_path)
                if not bp.exists():
                    try:
                        bp.mkdir(parents=True, exist_ok=True)
                    except OSError:
                        errors.append(ConfigValidationError(
                            f"MC_{sid}_BACKUP_PATH",
                            f"Backup-Pfad nicht erstellbar: {backup_path}",
                            "warning"
                        ))

            # World-Pfad pruefen
            world_path = get_env(f"MC_{sid}_WORLD_PATH", "")
            if world_path and not Path(world_path).exists():
                errors.append(ConfigValidationError(
                    f"MC_{sid}_WORLD_PATH",
                    f"World-Pfad nicht gefunden: {world_path} "
                    f"(wird nach erstem Start erstellt)",
                    "warning"
                ))

            # Chat-Channel pruefen
            chat_ch = get_env(f"MC_{sid}_GAME_CHAT_CHANNEL_ID", "0")
            if not chat_ch or chat_ch == "0":
                errors.append(ConfigValidationError(
                    f"MC_{sid}_GAME_CHAT_CHANNEL_ID",
                    f"Chat-Channel fuer {sid} nicht gesetzt — Chat-Bridge deaktiviert",
                    "warning"
                ))

        if not any_enabled:
            logger.debug("Keine Minecraft-Server konfiguriert — MC-Checks uebersprungen")

        return errors

    def format_report(self, errors: List[ConfigValidationError]) -> str:  # type: ignore
        """Format validation errors as readable text"""
        if not errors:
            return "✅ Alle Konfigurationen gültig."

        lines = []
        critical = [e for e in errors if e.severity == "error"]
        warnings = [e for e in errors if e.severity == "warning"]

        if critical:
            lines.append("**Fehler:**")
            for e in critical:
                lines.append(f"  ❌ `{e.field}`: {e.message}")

        if warnings:
            lines.append("**Warnungen:**")
            for e in warnings:
                lines.append(f"  ⚠️ `{e.field}`: {e.message}")

        return "\n".join(lines)
