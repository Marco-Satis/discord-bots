# Server-Voraussetzungen (Phase 0 Schritt 4)

> **Datum:** 21.02.2026 | **Server:** gameserver-netcup (203.0.113.10)

## Installierte Software

| Software | Version | Status |
|----------|---------|--------|
| Python | 3.10.12 | OK |
| pip | 22.0.2 | OK |
| Fail2Ban | 0.11.2 | INSTALLIERT |
| Certbot | 1.21.0 | INSTALLIERT |
| Nginx | 1.18.0 | INSTALLIERT |
| SQLite3 CLI | (nachinstalliert) | OK |
| rclone | 1.73.1 | INSTALLIERT |
| GnuPG | 2.2.27 | INSTALLIERT |

## System-Ressourcen

| Ressource | Wert |
|-----------|------|
| Disk gesamt | 1007 GB |
| Disk belegt | 18 GB (2%) |
| Disk frei | 949 GB |
| RAM gesamt | 31 GB |
| RAM belegt | 9.5 GB |
| RAM verfuegbar | 20 GB |
| Swap | 4 GB (nicht genutzt) |

## Services

| Service | Status |
|---------|--------|
| gameserver-bot.service | active |
| monitor-bot.service | active |
| admin-bot.service | active |
| web-dashboard.service | active |

## Firewall (UFW)

- Status: active
- SSH Port 4422 erlaubt

## Auswirkungen auf Feature-Plan

- **F31 (Fail2Ban):** INSTALLIERT — Kann voll implementiert werden
- **F32 (SSL-Monitor):** Certbot INSTALLIERT — Kann voll implementiert werden
- **F36 (Offsite-Backup):** rclone INSTALLIERT — Kann voll implementiert werden
- **F49 (Disk-Guard):** 949 GB frei, aber trotzdem implementieren (Zukunftssicherheit)
- **Alle Features:** Keine Einschraenkungen, Server hat ausreichend Ressourcen
