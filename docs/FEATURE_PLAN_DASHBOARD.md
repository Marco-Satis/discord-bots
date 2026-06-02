# Feature-Plan: Dashboard UI/UX Verbesserung

> **Version:** 1.0 | **Datum:** 15. Maerz 2026
> **Status:** PLANUNG
> **Basis:** v4.1.0 (Review bestanden, 0 Blocker, 37/37 Routes OK)

---

## 1. Uebersicht

### Ziele
- **Benutzererfahrung verbessern:** Schnellere Rueckmeldung bei Aktionen, weniger Full-Page-Reloads
- **Mobile-Tauglichkeit:** Dashboard auf Smartphones und Tablets vollstaendig nutzbar machen
- **Visuelles Feedback:** Loading-States, Toast-Benachrichtigungen, Animationen bei Statuswechsel
- **Code-Qualitaet:** Inline-Styles in CSS-Klassen konsolidieren, JS aus Templates extrahieren
- **Performance:** Langsame Endpunkte (Cloud-Status ~10s, Forecast) im Hintergrund laden/cachen
- **Wartbarkeit:** Wiederkehrende UI-Patterns als Jinja2-Makros extrahieren

### Nicht-Ziele
- **Kein CSS-Framework** (Tailwind, Bootstrap): Bestehender Vanilla-CSS-Ansatz bleibt
- **Kein SPA-Rewrite:** HTMX + Jinja2 bleibt die Architektur, kein React/Vue
- **Kein neues Auth-System:** JWT-Cookie + Discord OAuth2 bleiben
- **Keine neuen Backend-Endpunkte** in diesem Plan (ausser Caching-Endpunkte)
- **Kein Drag-and-Drop Dashboard:** Zu komplex fuer den aktuellen Stack, wird zurueckgestellt

### Abhaengigkeiten
| Abhaengigkeit | Grund |
|---------------|-------|
| HTMX v1.9.10+ | Bereits vorhanden; Upgrade auf v2.0 optional |
| Chart.js v4.4.0 | Bereits via CDN geladen |
| Bestehende SSE-Infrastruktur | Fuer Live-Updates, bereits funktional |
| FastAPI + Jinja2 | Backend-Stack bleibt unveraendert |

---

## 2. Ist-Analyse — Schwachstellen

Aus der Template- und CSS-Analyse ergeben sich folgende Problembereiche:

### 2.1 Inline-Styles (hoch)
- **server_detail.html:** ~25 Inline-`style`-Attribute (Status-Indikator, Badges, Layouts)
- **server_mods.html:** Suchfeld-Styling, Status-Badges, Formular-Layout komplett inline
- **admin_tab_embeds.html:** Embed-Felder, Preview-Bereich, Farbwahl-Inputs alles inline
- **dashboard.html:** Event-Feed-Layout, Package-Update-Bereich, Chart-Controls inline
- **Auswirkung:** Schwer wartbar, keine Theme-Konsistenz, Code-Duplikation

### 2.2 Inline-JavaScript (mittel)
- **analytics.html:** 420+ Zeilen Chart.js-Logik als Inline-Script
- **admin_tab_embeds.html:** 50+ Zeilen Preview-Logik, Color-Picker, HTML-Escape
- **config_login.html:** 75+ Zeilen fuer dynamische ID-Listen-Verwaltung
- **server_mods.html:** filterMods()-Funktion inline
- **Auswirkung:** Nicht cachebar, kein Code-Sharing zwischen Seiten, schwer testbar

### 2.3 Kein Feedback bei Aktionen (hoch)
- Server-Aktionen (Start/Stop/Restart) nutzen `hx-swap="none"` — kein visuelles Feedback
- RCON-Befehle zeigen Ergebnis nur im Console-Output, kein Erfolgs-/Fehler-Hinweis
- Config-Speichern: Full-Page-Reload statt Inline-Bestaetigung
- **Auswirkung:** Benutzer unsicher ob Aktion erfolgreich war

### 2.4 Mobile-Darstellung (mittel)
- Sidebar versteckt sich auf <768px, aber Hamburger-Button hat kein Overlay/Backdrop
- Tab-Navigationen (8+ Tabs bei server_detail, 11 Tabs bei admin_bot) scrollen horizontal ohne Indikator
- Tabellen brechen auf schmalen Screens — kein responsives Table-Fallback
- Chart-Grid bricht erst bei 400px Minimalbreite — auf Phones nur 1 Spalte

### 2.5 Loading-States fehlen (hoch)
- `/api/backup/cloud-status` braucht ~10s — Benutzer sieht nichts waehrend des Ladens
- `/api/forecast` braucht mehrere Sekunden — kein Ladeindikator
- HTMX-Requests zeigen nur `opacity: 0.7` — kein Spinner oder Skeleton-UI
- Initiales Laden von Partials (Spielerliste, Mods, Backups) zeigt nur "wird geladen..."

### 2.6 Technische Schulden
- **HTMX via unpkg CDN:** Sollte lokal liegen (htmx.min.js existiert bereits in /static, wird aber nicht genutzt)
- **Chart.js via CDN:** Keine Offline-Faehigkeit
- **Session-Store In-Memory:** Sessions gehen bei Dashboard-Restart verloren
- **Config-Validation:** POST auf /config validiert nicht alle Felder serverseitig
- **Dashboard-Version:** base.html zeigt "v4.0.0" hardcoded statt dynamisch

---

## 3. Feature-Liste (priorisiert)

### Gruppe A — Quick Wins (Aufwand S, hoher Impact)

| ID | Feature | Aufwand | Abhaengigkeit |
|----|---------|---------|---------------|
| A1 | Toast-Notification-System | S | Keine |
| A2 | Loading-Spinner/Skeleton fuer langsame Endpunkte | S | Keine |
| A3 | HTMX von CDN auf lokale Datei umstellen | S | Keine |
| A4 | Dashboard-Version dynamisch statt hardcoded | S | Keine |
| A5 | Sidebar-Overlay bei Mobile (Backdrop + Close on Click) | S | Keine |

### Gruppe B — UI-Verbesserungen (Aufwand M)

| ID | Feature | Aufwand | Abhaengigkeit |
|----|---------|---------|---------------|
| B1 | Inline-Styles in CSS-Klassen konsolidieren | M | Keine |
| B2 | Server-Detail Status-Indikator als CSS-Klasse | M | B1 |
| B3 | Responsive Tabellen (Card-Fallback auf Mobile) | M | Keine |
| B4 | Tab-Navigation Scroll-Indikator (Pfeile/Fade) | M | Keine |
| B5 | RCON-Console Verbesserungen (Historie, Auto-Scroll) | M | A1 |
| B6 | Chart-Interaktivitaet (Zoom, Tooltips, Legende-Toggle) | M | Keine |

### Gruppe C — Architektur / Technik (Aufwand M-L)

| ID | Feature | Aufwand | Abhaengigkeit |
|----|---------|---------|---------------|
| C1 | Inline-JS in separate .js-Dateien extrahieren | M | Keine |
| C2 | Backup-Cloud-Status Hintergrund-Caching | M | Keine (Backend) |
| C3 | Jinja2-Makros fuer wiederkehrende Komponenten | L | B1 |
| C4 | Config-Validation serverseitig vervollstaendigen | M | Keine (Backend) |
| C5 | Session-Store persistent (SQLite oder Redis) | M | Keine (Backend) |

### Gruppe D — Spaetere Erweiterungen (Aufwand L, niedrigere Prio)

| ID | Feature | Aufwand | Abhaengigkeit |
|----|---------|---------|---------------|
| D1 | Keyboard-Shortcuts (Ctrl+K Suche, Esc Sidebar schliessen) | M | Keine |
| D2 | Accessibility-Verbesserungen (ARIA, Focus-Management) | L | B1, C1 |
| D3 | RCON-Historie persistent (SQLite) | M | B5, C5 |
| D4 | Chart.js lokal statt CDN | S | Keine |
| D5 | PWA-Unterstuetzung (Service Worker, Manifest) | L | D4 |

---

## 4. Feature-Details

### A1: Toast-Notification-System

**Was:** Globales Toast-System fuer Erfolgs-, Fehler- und Warnmeldungen bei HTMX-Aktionen.
**Warum:** Server-Aktionen (Start/Stop/Restart), Config-Speichern und RCON zeigen aktuell kein oder unzureichendes Feedback.
**Aufwand:** S (1-2h)

**Implementierung:**
- Toast-Container existiert bereits in `style.css` (Zeile 1172-1204) — CSS fertig!
- JS-Funktion `showToast(message, type, duration)` in neuer Datei `web/static/toast.js`
- HTMX Response-Header `HX-Trigger: showToast` nutzen (HTMX Event-System)
- Backend: FastAPI-Responses mit Custom-Header `HX-Trigger` fuer automatische Toasts
- Auto-Dismiss nach 5 Sekunden, manuelles Schliessen per Klick

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/static/toast.js` | NEU — Toast-Logik (showToast, Event-Listener) |
| `web/templates/base.html` | Toast-Container div + Script-Include |
| `web/routes/*.py` | HX-Trigger Header bei POST-Responses |

---

### A2: Loading-Spinner/Skeleton fuer langsame Endpunkte

**Was:** Ladeindikator fuer Endpunkte die >1s brauchen (Cloud-Status, Forecast, Mod-Liste, Spielerliste).
**Warum:** Benutzer sieht bei langsamen Requests (Cloud-Status ~10s) nichts ausser "wird geladen...".
**Aufwand:** S (1-2h)

**Implementierung:**
- CSS-Klasse `.skeleton` mit Shimmer-Animation (pulsierendes Grau)
- CSS-Klasse `.spinner` als kreisfoermiger Ladeindikator
- HTMX-Attribut `hx-indicator` auf relevanten Containern setzen
- Bestehende "wird geladen..."-Texte durch Skeleton-Platzhalter ersetzen

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/static/style.css` | Neue Klassen: `.skeleton`, `.skeleton-line`, `.spinner` |
| `web/templates/server_detail.html` | Skeleton in Players/Backups/Mods Tabs |
| `web/templates/dashboard.html` | Skeleton in Package-Updates, Event-Feed |

---

### A3: HTMX von CDN auf lokale Datei umstellen

**Was:** `<script src>` in base.html von unpkg.com auf `/static/htmx.min.js` aendern.
**Warum:** Die lokale Datei existiert bereits (`web/static/htmx.min.js`), wird aber nicht genutzt. CDN-Abhaengigkeit ist ein Single Point of Failure.
**Aufwand:** S (<15min)

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/templates/base.html` | Zeile 10: CDN-URL → `/static/htmx.min.js` |

---

### A4: Dashboard-Version dynamisch

**Was:** Versionsanzeige in `base.html` Sidebar-Footer dynamisch aus `VERSION`-Datei laden.
**Warum:** Aktuell zeigt base.html hardcoded "v4.0.0" obwohl das System auf v4.1.0 ist.
**Aufwand:** S (<30min)

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/templates/base.html` | Zeile 235: `v4.0.0` → `{{ version }}` |
| `web/app.py` oder `web/routes/dashboard.py` | `version` als Template-Global oder Context-Variable |

---

### A5: Sidebar-Overlay bei Mobile

**Was:** Wenn die Sidebar auf Mobile geoeffnet wird, soll ein halbtransparenter Backdrop erscheinen. Klick auf den Backdrop schliesst die Sidebar.
**Warum:** Aktuell bleibt die Sidebar offen und ueberlagert den Inhalt ohne Moeglichkeit, sie einfach zu schliessen.
**Aufwand:** S (30min-1h)

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/static/style.css` | Neue Klasse: `.sidebar-backdrop` |
| `web/templates/base.html` | Backdrop-Div + angepasste Toggle-Logik |

---

### B1: Inline-Styles in CSS-Klassen konsolidieren

**Was:** Alle Inline-`style`-Attribute aus Templates in wiederverwendbare CSS-Klassen migrieren.
**Warum:** ~100+ Inline-Styles ueber alle Templates verstreut. Schwer wartbar, keine Theme-Konsistenz.
**Aufwand:** M (3-5h)

**Strategie:**
1. Bestandsaufnahme aller Inline-Styles (grep `style=` in Templates)
2. Gemeinsame Patterns identifizieren (Flex-Layouts, Margins, Badge-Styles)
3. Neue CSS-Utility-Klassen erstellen (`.flex-row`, `.gap-sm`, `.mt-1`, `.mb-1`, `.text-mono` etc.)
4. Komponentenspezifische Klassen: `.status-dot`, `.action-bar`, `.info-grid`, `.rcon-form`
5. Templates anpassen: `style="..."` → `class="..."`
6. Visueller Vergleich: Vorher/Nachher Screenshots

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/static/style.css` | ~30 neue CSS-Klassen |
| `web/templates/server_detail.html` | ~25 Inline-Styles entfernen |
| `web/templates/dashboard.html` | ~10 Inline-Styles entfernen |
| `web/templates/partials/server_mods.html` | ~15 Inline-Styles entfernen |
| `web/templates/partials/admin_tab_embeds.html` | ~20 Inline-Styles entfernen |
| `web/templates/system.html` | ~5 Inline-Styles entfernen |
| `web/templates/security.html` | ~10 Inline-Styles entfernen |
| `web/templates/partials/analytics.html` | Inline `<style>` Block → style.css |

---

### B2: Server-Detail Status-Indikator als CSS-Klasse

**Was:** Den 14px-Status-Punkt (gruener/roter Kreis) im Server-Detail-Header als CSS-Klasse extrahieren statt 8 Zeilen Inline-CSS.
**Warum:** Derselbe Indikator wird auch im Bot-Status und Dashboard genutzt — sollte einheitlich sein.
**Aufwand:** M (1h) — haengt von B1 ab

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/static/style.css` | Neue Klasse: `.status-dot`, `.status-dot-online`, `.status-dot-offline` |
| `web/templates/server_detail.html` | Inline-Style → CSS-Klasse |

---

### B3: Responsive Tabellen

**Was:** Tabellen auf Mobile (< 768px) als gestapelte Cards darstellen statt horizontal zu scrollen.
**Warum:** security.html hat bis zu 6 Spalten, errors.html 4 Spalten — auf Phones unlesbar.
**Aufwand:** M (2-3h)

**Implementierung:**
- CSS-only-Loesung mit `display: block` auf `<tr>` + `data-label` Attributen auf `<td>`
- Media-Query `@media (max-width: 768px)`: Tabellen-Zeilen werden zu Cards
- Jede Zelle zeigt ihr Label (aus `data-label`) als Pseudo-Element `::before`

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/static/style.css` | Responsive Table CSS (~40 Zeilen) |
| `web/templates/errors.html` | `data-label` Attribute auf `<td>` |
| `web/templates/security.html` | `data-label` Attribute auf `<td>` |
| `web/templates/system.html` | `data-label` Attribute auf `<td>` |
| `web/templates/partials/server_backups.html` | `data-label` Attribute |
| `web/templates/partials/server_players.html` | `data-label` Attribute |

---

### B4: Tab-Navigation Scroll-Indikator

**Was:** Bei vielen Tabs (admin_bot: 11, server_detail: 8) ein visueller Hinweis dass weitere Tabs durch Scrollen erreichbar sind.
**Warum:** Auf Mobile/Tablets sind nicht alle Tabs sichtbar — Benutzer merkt nicht dass mehr da ist.
**Aufwand:** M (1-2h)

**Implementierung:**
- CSS-Gradient-Fade an den Raendern der Tab-Leiste (rechts/links)
- `overflow-x: auto` ist bereits gesetzt (style.css Zeile 809)
- JS prueft ob Scroll moeglich ist und setzt `.has-scroll-left`/`.has-scroll-right` Klassen
- Optional: Pfeil-Buttons zum Scrollen

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/static/style.css` | Gradient-Overlay Pseudo-Elemente auf Tab-Container |
| `web/templates/base.html` oder `web/static/main.js` | Scroll-Detection JS |

---

### B5: RCON-Console Verbesserungen

**Was:** Befehls-Historie (Pfeiltasten hoch/runter), Auto-Scroll nach unten, Clear-Button, Zeitstempel-Toggle.
**Warum:** Aktuell hat die RCON-Console keine Befehls-Historie — jeder Befehl muss neu getippt werden.
**Aufwand:** M (2-3h)

**Implementierung:**
- JS-Array `rconHistory[]` speichert letzte 50 Befehle (sessionStorage)
- Pfeiltaste-Hoch/Runter navigiert durch Historie
- Clear-Button leert das Output-Div
- Auto-Scroll ist teilweise implementiert (`scroll:#rcon-output:bottom`) — robuster machen

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/static/rcon.js` | NEU — RCON-Logik extrahiert + Historie |
| `web/templates/server_detail.html` | Script-Referenz, Clear-Button, Keyboard-Handler |

---

### B6: Chart-Interaktivitaet

**Was:** Zoom/Pan in Charts, verbessertes Tooltip-Format, Legende klickbar zum Ein-/Ausblenden.
**Warum:** Charts sind aktuell rein statisch — kein Zoom, kein Zeitraum-Slider.
**Aufwand:** M (2-3h)

**Implementierung:**
- Chart.js Plugin `chartjs-plugin-zoom` fuer Zoom/Pan (CDN oder lokal)
- Legende bereits klickbar bei Chart.js (Standard-Feature, muss nur aktiviert werden)
- Tooltip-Callback mit deutschem Datumsformat und Einheiten (%, MB, Spieler)
- Optional: Cursor-Crosshair bei Hover

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/templates/base.html` | Zoom-Plugin Script-Include |
| `web/templates/dashboard.html` | Chart-Konfiguration erweitern |
| `web/templates/partials/analytics.html` | Chart-Optionen + Zoom-Config |

---

### C1: Inline-JS in separate Dateien extrahieren

**Was:** JavaScript-Code aus Templates in eigene `.js`-Dateien verschieben.
**Warum:** ~700+ Zeilen JS in Templates verteilt, nicht cachebar, nicht testbar.
**Aufwand:** M (3-4h)

**Extraktions-Plan:**
| Aus Template | Nach Datei | Zeilen |
|-------------|-----------|--------|
| `dashboard.html` (Charts, SSE, Package) | `web/static/dashboard.js` | ~320 |
| `partials/analytics.html` (Charts) | `web/static/analytics.js` | ~420 |
| `partials/admin_tab_embeds.html` (Preview) | `web/static/embed-builder.js` | ~50 |
| `partials/config_login.html` (ID-Lists) | `web/static/id-list.js` | ~75 |
| `partials/server_mods.html` (Filter) | `web/static/mod-filter.js` | ~10 |
| `server_detail.html` (Tab-Switch, RCON) | `web/static/server-detail.js` | ~30 |
| `base.html` (CSRF, Session, Theme) | `web/static/main.js` | ~120 |

**Betroffene Dateien:** Alle Templates + 7 neue JS-Dateien

---

### C2: Backup-Cloud-Status Hintergrund-Caching

**Was:** Cloud-Status-Abfrage (rclone, ~10s) im Hintergrund alle 5 Minuten ausfuehren und Ergebnis cachen.
**Warum:** Jeder Aufruf von `/api/backup/cloud-status` blockiert 10s — bei jedem Seitenaufruf.
**Aufwand:** M (2-3h)

**Implementierung:**
- Hintergrund-Task in FastAPI (`asyncio.create_task`) der alle 5 Minuten rclone aufruft
- Ergebnis in globalem Dict oder SQLite-Cache speichern
- `/api/backup/cloud-status` liest aus Cache statt rclone auszufuehren
- Cache-Alter wird im Response mitgeliefert ("Stand: vor 3 Minuten")
- Manueller Refresh-Button fuer sofortiges Update

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/routes/backup_routes.py` (o.ae.) | Cache-Logik, Background-Task |
| `web/app.py` | Background-Task beim Start registrieren |
| `web/templates/server_detail.html` | Cache-Alter anzeigen, Refresh-Button |

---

### C3: Jinja2-Makros fuer wiederkehrende Komponenten

**Was:** Haeufig genutzte UI-Patterns als Jinja2-Makros extrahieren.
**Warum:** Toggle-Switch, Stat-Card, Progress-Bar, Alert, Button-Group werden in 10+ Templates kopiert.
**Aufwand:** L (4-6h)

**Geplante Makros:**
| Makro | Verwendung | Templates |
|-------|-----------|-----------|
| `toggle_switch(name, label, checked)` | Feature-Toggles, Config | config.html, admin_tab_*.html |
| `stat_card(label, value, sub)` | System-Stats, Server-Overview | dashboard.html, system.html, server_detail.html |
| `progress_bar(percent, thresholds)` | CPU/RAM/Disk Balken | dashboard.html, system.html, server_detail.html |
| `action_button(label, action, confirm, variant)` | Server-Aktionen | dashboard.html, server_detail.html, system.html |
| `data_table(headers, rows)` | Alle Tabellen | security.html, errors.html, partials/*.html |

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/templates/macros.html` | NEU — Alle Makro-Definitionen |
| `web/templates/*.html` | `{% import "macros.html" %}` + Makro-Aufrufe |
| `web/templates/partials/*.html` | Makro-Aufrufe statt Copy-Paste |

---

### C4: Config-Validation serverseitig

**Was:** Alle Formularfelder der Config-Seite serverseitig validieren.
**Warum:** Aktuell werden Werte ohne Pruefung uebernommen — ungueltige Schwellenwerte moeglich.
**Aufwand:** M (2-3h)

**Validierungsregeln:**
- Schwellenwerte: 0-100 fuer Prozent, positive Ganzzahlen fuer Sekunden/Stunden
- Scheduler-Werte: Stunde 0-23, Minute 0-59, Intervalle > 0
- Restart-Timer: Minuten 1-60, Delay 0-300
- Feature-Flags: Boolean (true/false)
- Fehlermeldungen als Toast (nutzt A1)

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/routes/config_routes.py` | Validierungslogik bei POST |
| `web/templates/config.html` | HTML5 min/max/step Attribute ergaenzen |

---

### C5: Session-Store persistent

**Was:** Sessions in SQLite oder Datei statt In-Memory speichern.
**Warum:** Bei Dashboard-Restart gehen alle Sessions verloren — Benutzer muessen sich neu anmelden.
**Aufwand:** M (2-3h)

**Optionen:**
- **SQLite (bevorzugt):** Neue Tabelle `dashboard_sessions`, bestehende DB nutzen
- **Dateisystem:** JSON-Datei pro Session in `/tmp/dashboard_sessions/`
- Session-Bereinigung: Abgelaufene Sessions alle 24h loeschen (via bestehenden Cleanup-Task)

**Betroffene Dateien:**
| Datei | Aenderung |
|-------|-----------|
| `web/middleware/session.py` (oder `web/app.py`) | Session-Backend austauschen |
| `modules/database/migrations.py` | Neue Tabelle `dashboard_sessions` (Migration v5) |

---

## 5. Umsetzungsreihenfolge (Phasen)

### Phase 1 — Quick Wins (Woche 1)
**Ziel:** Sofortige Verbesserungen mit minimalem Risiko.

```
A3 (HTMX lokal)
 → A4 (Version dynamisch)
 → A1 (Toast-System)
 → A2 (Loading-Spinner)
 → A5 (Mobile Sidebar-Overlay)
```

**Abhaengigkeiten:** Keine untereinander. Koennen parallel oder beliebig geordnet umgesetzt werden.
**Risiko:** Gering — aendert keine bestehende Logik.
**Test:** Manuell: Toasts bei Server-Aktionen pruefen, Mobile-Ansicht testen.

### Phase 2 — CSS-Konsolidierung (Woche 2)
**Ziel:** Technische Basis fuer alle weiteren UI-Aenderungen schaffen.

```
B1 (Inline-Styles → CSS)
 → B2 (Status-Indikator CSS)
 → C1 (Inline-JS extrahieren)
```

**Abhaengigkeit:** B2 baut auf B1 auf. C1 ist unabhaengig aber passt thematisch.
**Risiko:** Mittel — visuelle Regression moeglich. Vorher/Nachher Screenshots vergleichen.
**Test:** Alle 11 Seiten visuell pruefen (Dark + Light Theme).

### Phase 3 — Mobile + Responsive (Woche 3)
**Ziel:** Dashboard vollstaendig mobil nutzbar.

```
B3 (Responsive Tabellen)
 → B4 (Tab-Scroll-Indikator)
```

**Abhaengigkeit:** B3 und B4 sind unabhaengig voneinander.
**Risiko:** Gering-Mittel — nur CSS-Aenderungen + data-Attribute.
**Test:** Alle Tabellen und Tab-Navigationen auf 375px, 768px, 1024px testen.

### Phase 4 — UX-Features (Woche 4)
**Ziel:** Interaktionsqualitaet verbessern.

```
B5 (RCON-Console)
 → B6 (Chart-Interaktivitaet)
 → C4 (Config-Validation)
```

**Abhaengigkeit:** B5 nutzt Toast-System (A1). B6 und C4 sind unabhaengig.
**Risiko:** Gering — additive Features.
**Test:** RCON-Historie, Chart-Zoom, ungueltige Config-Werte eingeben.

### Phase 5 — Backend-Optimierung (Woche 5)
**Ziel:** Performance und Stabilitaet.

```
C2 (Backup-Cache)
 → C5 (Session persistent)
```

**Abhaengigkeit:** Keine zu vorherigen Phasen.
**Risiko:** Mittel — Backend-Aenderungen, DB-Migration fuer Sessions.
**Test:** Cloud-Status-Ladezeit messen (<1s aus Cache). Session nach Restart pruefen.

### Phase 6 — Refactoring (optional, Woche 6+)
**Ziel:** Langfristige Wartbarkeit.

```
C3 (Jinja2-Makros)
 → D1 (Keyboard-Shortcuts)
 → D2 (Accessibility)
```

**Abhaengigkeit:** C3 haengt von B1 ab (Inline-Styles muessen vorher aufgeraeumt sein).
**Risiko:** Mittel — Template-Refactoring kann visuelle Regressionen verursachen.
**Test:** Alle Templates visuell pruefen, Accessibility-Audit mit Browser-DevTools.

---

## 6. Aufwand-Uebersicht

| Phase | Features | Geschaetzter Aufwand |
|-------|----------|---------------------|
| Phase 1 — Quick Wins | A1-A5 | 4-6 Stunden |
| Phase 2 — CSS-Konsolidierung | B1, B2, C1 | 7-11 Stunden |
| Phase 3 — Mobile + Responsive | B3, B4 | 3-5 Stunden |
| Phase 4 — UX-Features | B5, B6, C4 | 6-9 Stunden |
| Phase 5 — Backend-Optimierung | C2, C5 | 4-6 Stunden |
| Phase 6 — Refactoring | C3, D1, D2 | 8-12 Stunden |
| **Gesamt** | **20 Features** | **~32-49 Stunden** |

---

## 7. Dateien-Uebersicht (Neue + Geaenderte)

### Neue Dateien
| Datei | Beschreibung | Phase |
|-------|-------------|-------|
| `web/static/toast.js` | Toast-Notification-Logik | 1 |
| `web/static/main.js` | CSRF, Session-Timeout, Theme (aus base.html) | 2 |
| `web/static/dashboard.js` | Charts, SSE, Updates (aus dashboard.html) | 2 |
| `web/static/analytics.js` | Analytics-Charts (aus analytics.html) | 2 |
| `web/static/embed-builder.js` | Embed-Preview (aus admin_tab_embeds.html) | 2 |
| `web/static/id-list.js` | ID-Listen-Management (aus config_login.html) | 2 |
| `web/static/server-detail.js` | Tab-Switch, RCON (aus server_detail.html) | 2 |
| `web/static/rcon.js` | RCON-Befehls-Historie + Console-Logik | 4 |
| `web/templates/macros.html` | Jinja2-Makros (Toggle, Stat-Card, etc.) | 6 |

### Geaenderte Dateien (Hauptaenderungen)
| Datei | Phase(n) | Aenderung |
|-------|----------|-----------|
| `web/static/style.css` | 1,2,3 | Skeleton, Spinner, Utility-Klassen, Responsive Tables |
| `web/templates/base.html` | 1,2 | HTMX lokal, Version dynamisch, Toast-Container, JS-Extraktion |
| `web/templates/dashboard.html` | 1,2 | Toasts, Skeleton, JS extrahiert |
| `web/templates/server_detail.html` | 1,2,4 | Inline-Styles → CSS, RCON-Verbesserungen |
| `web/templates/partials/analytics.html` | 2,4 | JS extrahiert, Chart-Zoom |
| `web/templates/partials/admin_tab_embeds.html` | 2 | JS extrahiert, Inline-Styles → CSS |
| `web/templates/config.html` | 4 | Validation-Attribute |
| `web/routes/config_routes.py` | 4 | Server-Validation |
| `web/routes/backup_routes.py` | 5 | Background-Caching |
| `web/app.py` | 1,5 | Version-Context, Background-Task, Session-Backend |

---

## 8. Bekannte Risiken

| Risiko | Auswirkung | Mitigation |
|--------|------------|------------|
| Visuelle Regression bei CSS-Migration (B1) | Layout-Fehler im Dark/Light Theme | Vorher/Nachher Screenshots, Theme-Toggle pruefen |
| JS-Extraktion bricht bestehende Funktionalitaet (C1) | Charts laden nicht, SSE verbindet nicht | Schrittweise extrahieren, nach jeder Datei testen |
| Chart.js Zoom-Plugin Kompatibilitaet (B6) | Plugin funktioniert nicht mit v4.4.0 | Vorher Kompatibilitaet pruefen, Pin auf bekannte Version |
| Session-Migration (C5) | Bestehende Sessions ungueltig | Migration: Alle Sessions invalidieren, Benutzer muessen sich neu anmelden |
| HTMX HX-Trigger (A1) | Aeltere HTMX-Versionen unterstuetzen Header nicht | HTMX 1.9.10 unterstuetzt HX-Trigger — kein Problem |

---

## 9. Test-Strategie

| Was | Wie | Wann |
|-----|-----|------|
| Visuelle Regression | Screenshots aller 11 Seiten (Dark + Light, Desktop + Mobile) | Nach Phase 2, 3 |
| Toast-System | Server-Aktion ausfuehren, Toast erscheint | Phase 1 |
| Loading-States | Cloud-Status aufrufen, Skeleton sichtbar | Phase 1 |
| Mobile-Responsiveness | Chrome DevTools Responsive Mode (375px, 768px) | Phase 3 |
| RCON-Historie | 5 Befehle senden, mit Pfeiltasten navigieren | Phase 4 |
| Chart-Zoom | Mausrad auf Chart, Zoom funktioniert | Phase 4 |
| Config-Validation | Ungueltige Werte eingeben, Fehlermeldung erscheint | Phase 4 |
| Session-Persistenz | Dashboard neustarten, Session erhalten | Phase 5 |
| Alle Routes | `python tests/test_routes.py` | Nach jeder Phase |

---

## Aenderungshistorie

| Version | Datum | Aenderungen |
|---------|-------|-------------|
| 1.0 | 15.03.2026 | Initialer Plan nach Ist-Analyse aller Templates, CSS, Partials |
