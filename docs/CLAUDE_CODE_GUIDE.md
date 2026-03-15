# Claude Code — Vollständiger Guide (Stand März 2026)

> Zusammengestellt aus: AlexPEClub AI Coding Starter Kit, Anthropic Docs, Community Best Practices, Web-Recherche

---

## 1. Wie Claude Code am besten nutzen?

### Terminal (empfohlen für Entwickler)

```bash
npm install -g @anthropic-ai/claude-code
cd /pfad/zum/projekt
claude
```

- Voller Zugriff auf Dateisystem, SSH, Git, alle CLI-Tools
- Subagents, Skills, Hooks, Agent Teams — alles verfügbar
- Plan Mode, /compact, /clear, /status — alles im Terminal
- **Immer wenn du Code schreibst, deployest, debuggst**

### VS Code Extension

- Side-by-Side: Code im Editor, Claude daneben
- Weniger Features als Terminal (keine Agent Teams, eingeschränkte Hooks)
- **Gut als Ergänzung für Code-Review und kleinere Änderungen**

### Web (claude.ai / Cowork)

- **Für Planung, Dokumentation, Prompt-Optimierung**
- Nicht für echte Code-Arbeit

### Empfehlung

| Aufgabe | Tool |
|---------|------|
| Bug-Fixes, Integration, Deployment | **Terminal** |
| Code-Review, schnelle Fragen | **VS Code** oder Terminal |
| Planung, Doku, Prompts | **Cowork** (Web) |
| Große Refactorings (>50 KB Dateien) | **Terminal** mit Subagent |

---

## 2. Kern-Konzepte

### Context Engineering > Prompt Engineering

Es geht nicht um den einzelnen Prompt, sondern um den gesamten Kontext:

```
CLAUDE.md (immer geladen, ~80 Zeilen max)
  + .claude/rules/ (auto-geladen bei passenden Dateien)
  + .claude/skills/ (on-demand, bei Slash-Command)
  + .claude/agents/ (isolierter Context, bei Delegation)
  + Konversationshistorie (wächst, muss gemanagt werden)
```

### Geschichtetes Context-Management

| Schicht | Wann geladen |
|---------|-------------|
| CLAUDE.md | Jede Session (auto) |
| .claude/rules/ | Beim Bearbeiten passender Dateien (auto) |
| .claude/skills/ | Bei Slash-Command oder Auto-Discovery |
| .claude/agents/ | Bei Delegation |
| Feature-Specs/Docs | On-demand |

### State lebt in Dateien, nicht im Gedächtnis

Claude vergisst alles nach /compact oder /clear. Deswegen:
- Fortschritt in PROGRESS.md, Aufgaben in OFFEN.md
- Nach /compact: `git diff --name-only` → Dateien lesen → weiterarbeiten

### "Always Read, Never Guess"

- IMMER Datei lesen bevor ändern
- IMMER Import-Pfade per grep verifizieren
- IMMER git diff prüfen nach Compaction
- NIE aus dem Gedächtnis arbeiten

---

## 3. .claude/ Ordner-Struktur

```
.claude/
├── settings.json          # Team-Permissions (git committed)
├── settings.local.json    # Persönliche Overrides (gitignored)
├── rules/                 # Auto-applied Coding Standards
│   ├── general.md         # Immer (globs: *)
│   ├── python.md          # Bei .py (globs: **/*.py)
│   ├── frontend.md        # Bei .tsx/.jsx
│   ├── backend.md         # Bei API/DB-Dateien
│   └── security.md        # Bei .env, Auth
├── skills/                # On-Demand Workflows
│   └── deployment/SKILL.md
└── agents/                # Sub-Agent Configs
    └── integration-dev.md
```

### Rules Format
```markdown
---
description: Wann diese Rule gilt
globs: **/*.py
---
# Inhalt
```

### Skills Format
```markdown
---
name: skill-name
description: Wann nutzen
---
# Workflow-Anleitung
```

### Agents Format
```markdown
---
name: agent-name
description: Was der Agent tut
model: opus
maxTurns: 50
tools: [Read, Write, Edit, Bash, Glob, Grep]
---
System-Prompt
```

---

## 4. CLAUDE.md Best Practices

1. **Unter 100 Zeilen** (Community-Konsens: 50-100)
2. **Für jede Zeile fragen:** "Fehler ohne diese Zeile?" → Nein? Streichen.
3. **Details auslagern** in Rules, Skills, @imports
4. **Nur was IMMER relevant ist**
5. **@import Syntax:** `@docs/OFFEN.md`

---

## 5. Context-Management

| Befehl | Wann |
|--------|------|
| `/compact` | >50% Context |
| `/clear` | Aufgabenwechsel |
| `/status` | Context-Nutzung prüfen |
| `/model` | Modell wechseln |
| `/config` | Thinking Mode |
| `/cost` | Session-Kosten |
| `/resume` | Letzte Session fortsetzen |
| `/hooks` | Hooks verwalten |

---

## 6. Hooks

### Post-Edit Tests
```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "command",
        "command": "python tests/test_imports.py"
      }]
    }]
  }
}
```

---

## 7. Workflow-Patterns

### Plan → Annotate → Implement
1. "Create plan. Don't implement yet."
2. Plan annotieren mit > NOTE: ...
3. "Address all notes, don't implement yet."
4. Wiederholen bis sauber
5. "Implement the plan."

### Bug-Fix
"Fix BUG-6. Read docs/CODE_REVIEW.md for details."

### Große Integration (Subagent)
"Use a subagent for I1. Read OFFEN.md, grep callers, make minimal changes."

---

## 8. Modell-Empfehlungen

| Aufgabe | Modell |
|---------|--------|
| Bug-Fixes, Integrationen | **Opus** |
| Einfache Änderungen | **Sonnet** |
| Codebase-Scanning | **Haiku** (Subagent) |
| Planung + Impl. | **opusplan** |

---

## 9. Checkliste: Neues Projekt aufsetzen

1. `cd /projekt && claude` → `/init`
2. CLAUDE.md verfeinern (<100 Zeilen)
3. `.claude/rules/general.md` — Always-read-never-guess, Tests, Git
4. `.claude/rules/<sprache>.md` — Coding-Standards
5. `.claude/settings.local.json` — Permissions + Hooks
6. Skills für wiederkehrende Workflows
7. Test-Befehl in CLAUDE.md
8. `/config` → Thinking enabled
