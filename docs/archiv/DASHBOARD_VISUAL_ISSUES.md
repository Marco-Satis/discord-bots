# Dashboard — Visuelle Issues (manuell pruefen)

> **Erstellt:** Phase 0 (21.02.2026) | **Status:** OFFEN — erfordert Browser-Test

Diese Probleme koennen nur mit einem Browser visuell verifiziert werden.

## Code-Analyse Findings (gefixt oder unkritisch)

### 1. HTMX Event-Feed Polling (dashboard.html:251)
- `hx-get="/"` laedt gesamte Seite, `hx-select="#event-feed"` filtert dann
- **Funktioniert**, aber uebertaegt unnoetig viel Daten
- **Optimierung:** Eigener Endpoint `/api/events/recent` (kommt mit F29 SSE)

### 2. Config-Template Werte (config.html)
- `config.scheduler.daily_restart_hour if config.scheduler is defined else 4`
- Wenn scheduler existiert aber Key fehlt: Zeigt leeres Feld statt Default
- **Unkritisch:** Config hat standardmaessig alle Keys

## Visuell zu pruefen (Browser noetig)

1. **Sidebar Responsive:** Hamburger-Menu auf Mobile
2. **Chart.js Rendering:** Charts zeigen Daten korrekt?
3. **Tab-Wechsel:** Config-Tabs und Server-Detail-Tabs
4. **Overflow:** Lange Listen (Events, Spieler) korrekt scrollbar?
5. **Farbkontraste:** Text auf dunklem Hintergrund lesbar?
6. **HTMX Loading:** Werden Ladeanimationen angezeigt?
7. **Buttons:** Server-Actions (Start/Stop/Restart) visuell korrekt?
8. **Mobile-Ansicht:** Dashboard auf Smartphone/Tablet
