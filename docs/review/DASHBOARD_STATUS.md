# Dashboard Zustandsbericht — v4.1.0

> **Stand:** 15. Maerz 2026
> **Fuer:** Cowork-Session (UI/UX Review + Verbesserungen)
> **Getestet:** Alle Routes auf Server verifiziert (37/37 OK)

---

## Route-Map (vollstaendig, aus Code verifiziert)

### Oeffentlich (kein Auth)
| Route | Methode | Status | Beschreibung |
|-------|---------|--------|-------------|
| /auth/login | GET+POST | 200 | Login-Seite |
| /auth/discord | GET | 302 | Discord OAuth Redirect |
| /auth/discord/callback | GET | 302 | Discord OAuth Callback |
| /auth/logout | GET | 302 | Logout + Cookie loeschen |
| /api/health | GET | 200 | Health-Check (public) |
| /api/health/selftest | GET | 200 | Self-Test |
| /api/health/auto-restart | GET | 200 | Auto-Restart Status |
| /api/health/disk | GET | 200 | Disk-Status |
| /api/health/services | GET | 200 | Service-Status |
| /api/health/dns | GET | 200 | DNS-Check |
| /api/health/ports | GET | 200 | Port-Check |
| /static/* | GET | 200 | CSS, JS |
| /api/webhook/github | POST | 200 | GitHub Deploy Webhook (HMAC) |

### Geschuetzt (Auth erforderlich → 302 Redirect zu /auth/login)
| Route | Methode | Status | Beschreibung |
|-------|---------|--------|-------------|
| / | GET | 200 | Dashboard-Uebersicht |
| /system | GET | 200 | System-Management |
| /security | GET | 200 | IP-Security Dashboard |
| /config | GET+POST | 200 | Config-Panel (Owner only) |
| /config/notifications | GET+POST | 200 | Notification-Matrix |
| /config/login | GET+POST | 200 | Login-Verwaltung |
| /config/bot-profiles | GET+POST | 200 | Bot-Profile |
| /search | GET | 200 | Volltextsuche |
| /errors | GET | 200 | Fehler-Uebersicht |
| /changelog | GET | 200 | Changelog (Markdown→HTML) |
| /admin-bot | GET | 200 | Admin-Bot Setup (10 Tabs) |
| /admin-bot/tab/{tab} | GET | 200 | HTMX Tab-Partial |
| /admin-bot/save/{module} | POST | 200 | Modul-Config speichern |
| /server/{id} | GET | 200 | Server-Detail (sat, mc_bmc, mc_vanilla, teamspeak) |

### API (Auth erforderlich → 401 ohne Token)
| Route | Methode | Beschreibung |
|-------|---------|-------------|
| /api/analytics/system | GET | System-Metriken (CPU, RAM, Disk) |
| /api/analytics/server/{id} | GET | Server-Metriken |
| /api/analytics/players | GET | Spieler-Statistiken |
| /api/analytics/summary | GET | Zusammenfassung |
| /api/analytics/heatmap | GET | Aktivitaets-Heatmap |
| /api/analytics/peaks | GET | Peak-Zeiten |
| /api/analytics/trends | GET | Trend-Analyse |
| /api/analytics/server-comparison | GET | Server-Vergleich |
| /api/analytics/correlation | GET | Korrelations-Analyse |
| /api/analytics/anomalies | GET | Anomalie-Erkennung |
| /api/forecast | GET | Resource-Forecast (Disk/RAM) |
| /api/backup/cloud-status | GET | OneDrive Backup-Status (~10s) |
| /api/theme | GET | Theme-Status |
| /api/theme/toggle | POST | Theme umschalten |
| /api/security/ip-overview | GET | IP-Uebersicht |
| /api/security/unban | POST | IP entsperren |
| /api/security/ban-stats | GET | Ban-Statistiken |
| /api/server/{id}/players | GET | Spielerliste |
| /api/server/{id}/backups | GET | Backup-Liste |
| /api/server/{id}/action | POST | Server-Aktion (start/stop/restart) |
| /api/server/{id}/mods | GET | Mod-Liste |
| /api/server/{id}/mods/export | GET | Mods exportieren |
| /api/server/{id}/mods/search | GET | Mods suchen |
| /api/server/{id}/mods/check-updates | GET | Mod-Updates pruefen |
| /api/server/{id}/mods/update | POST | Mod aktualisieren |
| /api/server/{id}/mods/uninstall | POST | Mod deinstallieren |
| /api/server/{id}/rcon | POST | RCON-Befehl senden |
| /api/system/service/action | POST | System-Service steuern |
| /api/system/packages/* | GET/POST | Paket-Verwaltung |
| /api/events/clear | POST | Events loeschen |
| /api/errors/clear | POST | Fehler loeschen |
| /api/search | GET | Such-API (JSON) |
| /api/search/reindex | POST | Such-Index neu aufbauen |
| /api/search/stats | GET | Such-Statistiken |
| /api/config/reload | POST | Config neu laden |
| /api/config/reload/status | GET | Reload-Status |
| /api/webhook/deploy-history | GET | Deploy-Historie |
| /api/export/* | GET | CSV-Export (players, events, stats, audit, commands) |

### SSE (Server-Sent Events)
| Route | Intervall | Beschreibung |
|-------|-----------|-------------|
| /api/sse/dashboard | 5s | Dashboard-Live-Daten |
| /api/sse/events | 3s | Event-Stream |

---

## Templates

### Seiten (11 Templates)
| Datei | Beschreibung |
|-------|-------------|
| base.html | Basis-Layout (Navigation, CSRF, CSS/JS) |
| login.html | Login-Seite (standalone) |
| dashboard.html | Server-Uebersicht mit Status-Cards |
| system.html | System-Info + Service-Management |
| security.html | Firewall, Fail2Ban, SSL, IP-Bans |
| config.html | Config-Editor (Owner) |
| server_detail.html | Server-Detail mit Players, RCON, Backups |
| admin_bot.html | Admin-Bot Setup (10 Konfigurations-Tabs) |
| errors.html | Fehler-Uebersicht (ERROR/WARNING aus Logs) |
| changelog.html | CHANGELOG.md als HTML |
| search.html | Volltextsuche |

### Partials (17 HTMX-Fragments)
| Datei | Beschreibung |
|-------|-------------|
| partials/server_backups.html | Backup-Liste |
| partials/server_players.html | Spielerliste mit Aktionen |
| partials/server_mods.html | Mod-Liste mit Updates |
| partials/analytics.html | Analytics-Tab (24h/7d/30d) |
| partials/config_notifications.html | Notification-Matrix |
| partials/config_login.html | Login-Verwaltung |
| partials/config_bot_profiles.html | Bot-Profile |
| partials/admin_tab_temp_voice.html | Temp-Voice Config |
| partials/admin_tab_teamspeak.html | TeamSpeak Config |
| partials/admin_tab_wordfilter.html | Wortfilter Config |
| partials/admin_tab_antispam.html | AntiSpam Config |
| partials/admin_tab_warn.html | Warn-System Config |
| partials/admin_tab_reaction_roles.html | Reaction-Roles Config |
| partials/admin_tab_leveling.html | Leveling/XP Config |
| partials/admin_tab_tickets.html | Ticket-System Config |
| partials/admin_tab_audit.html | Audit-Logging Config |
| partials/admin_tab_giveaways.html | Giveaway Config |
| partials/admin_tab_embeds.html | Embed-Builder |

---

## Middleware-Stack (Ausfuehrungsreihenfolge)

| # | Middleware | Beschreibung | Status |
|---|-----------|-------------|--------|
| 1 | SessionMiddleware | Session-Cookie (dashboard_session, 24h) | OK |
| 2 | SessionTimeoutMiddleware | 60min Inaktivitaet, 24h absolut, "angemeldet bleiben" 7d | OK |
| 3 | CSRFMiddleware | Token-Validierung bei POST/PUT/DELETE | GEFIXT (vorher deaktiviert) |
| 4 | RateLimitMiddleware | Token-Bucket: Login 5/min, Actions 10/min, Reads 60/min | OK |
| 5 | CORSMiddleware | Erlaubte Origins + Credentials | OK |

---

## Static Assets

| Datei | Beschreibung |
|-------|-------------|
| style.css | Dark Theme (#1a1a2e Hintergrund) |
| themes.css | Light Theme Overrides |
| htmx.min.js | HTMX v1.9.10 |

---

## Bekannte Einschraenkungen

1. **Backup Cloud-Status langsam**: /api/backup/cloud-status braucht ~10s wegen rclone-Aufruf. Caching empfohlen.
2. **Kein WebSocket-Fallback**: SSE wird genutzt, WebSocket-Endpoint existiert aber ist nicht aktiv.
3. **update_cog nicht geladen**: Noch keinem Bot zugeordnet (geplant fuer gameserver-bot).

---

## Verbesserungsvorschlaege fuer UI/UX

1. **Loading-States**: Langsame Endpunkte (cloud-status, forecast) sollten Spinner/Skeleton-UI zeigen
2. **Toast-Notifications**: Erfolgs-/Fehlermeldungen bei Aktionen (RCON, Start/Stop) als Toast statt Page-Reload
3. **Mobile-Responsive**: Navigation fuer mobile Geraete optimieren (Hamburger-Menue)
4. **Dashboard-Widgets**: Drag-and-Drop Anordnung der Status-Cards
5. **Chart-Interaktivitaet**: Zoom/Pan in Analytics-Charts, Zeitraum-Slider
6. **RCON-Historie**: Letzte RCON-Befehle persistent anzeigen (aktuell nur in-memory)
7. **Backup-Status-Caching**: Cloud-Status im Hintergrund aktualisieren statt bei jedem Request

---

## Technische Schulden

| Thema | Beschreibung | Prioritaet |
|-------|-------------|-----------|
| HTMX Version | v1.9.10 — aktuelle Version pruefen | Niedrig |
| Chart.js CDN | Wird via CDN geladen statt lokal | Niedrig |
| Config-Validation | /config POST validiert nicht alle Felder serverseitig | Mittel |
| Error-Log Parsing | Regex-basiert, koennte bei Log-Format-Aenderungen brechen | Niedrig |
| Session-Store | In-Memory (verliert Sessions bei Restart) | Mittel |
