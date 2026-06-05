# Runbook — Rotation `GITHUB_WEBHOOK_SECRET` (M16/B4-C)

> 2026-06-05 · Betrifft `web/routes/webhook_route.py` (`/api/webhook/github`)
> Severity-Kontext: Der Webhook triggert nach HMAC-Verify `git pull` + `pip install` +
> `sudo systemctl restart` = **Deploy-RCE-by-design**. Die gesamte Sicherheit haengt an
> der Staerke + Geheimhaltung dieses einen Secrets → regelmaessig rotieren.

## Wann rotieren
- Routine: alle 90 Tage.
- Sofort bei: Verdacht auf Leak (Secret im Chat/Log/Repo gepostet), Personalwechsel,
  ungeklaerten Eintraegen in `/api/webhook/deploy-history`, oder nach jedem Server-Restore.

## Vorbedingung (Geheimhaltung)
- Secret liegt **nur** in `config/.env` (Key `GITHUB_WEBHOOK_SECRET`, Perm 600) **und** in
  KeePass (`Marco_Vault.kdbx`). Nie in git, Chat, Repo, Slack/Discord.
- Wert beim Erzeugen/Verifizieren **nie** im Klartext in den Chat schreiben.

## Rotation (Reihenfolge wichtig — sonst faellt Auto-Deploy aus)

1. **Neues Secret erzeugen** (lokal, Wert nicht anzeigen lassen — direkt in Zwischenablage):
   ```powershell
   # 64 Hex-Zeichen (32 Byte) — Ausgabe in die Zwischenablage, nicht in die Console
   python -c "import secrets; print(secrets.token_hex(32))" | Set-Clipboard
   ```

2. **GitHub-Seite zuerst aktualisieren** (Repo → Settings → Webhooks → Hook editieren):
   - Feld *Secret* mit dem neuen Wert ueberschreiben → *Update webhook*.
   - GitHub sendet danach `X-Hub-Signature-256` mit dem neuen Secret.

3. **Server-`.env` aktualisieren** (auf netcup, NICHT von hier aus den Wert einsehen lassen):
   ```bash
   ssh netcup-marco
   sudo systemctl stop discord-bot-web     # Annahme: Service-Name; ggf. anpassen
   # .env editieren, GITHUB_WEBHOOK_SECRET= <neuer Wert> setzen (Editor, kein echo mit Wert)
   nano /pfad/zu/config/.env
   chmod 600 /pfad/zu/config/.env
   sudo systemctl start discord-bot-web
   ```
   > Kurzes Stop/Start statt Hot-Reload, damit kein Request waehrend des Wechsels mit
   > gemischten Secrets ankommt.

4. **KeePass aktualisieren**: Eintrag `GITHUB_WEBHOOK_SECRET` (Discord_Bots) → neuer Wert,
   alten als History behalten.

5. **Verifizieren**: in GitHub *Recent Deliveries* → *Redeliver* eines Ping/Push-Events.
   Erwartung: HTTP **204/200**. Bei **401/403** stimmt das Secret nicht ueberein → Schritt 2/3
   pruefen. `/api/webhook/deploy-history` sollte den neuen Deploy zeigen.

## Nach einem Leak zusaetzlich
- Sofort rotieren (oben), **dann** Git-History/Logs auf den geleakten Wert pruefen.
- `/api/webhook/deploy-history` auf unautorisierte Deploys zwischen Leak und Rotation pruefen.
- Falls ein fremder Deploy lief: Server-State (`git log`, installierte Pakete) gegen Erwartung
  abgleichen.

## Offen (Marco, separat — B4-A/B, NICHT Teil dieses Runbooks)
- **A — sudo-Minimierung**: NOPASSWD nur fuer die 4 konkreten `systemctl restart <service>`-Targets,
  kein generelles `systemctl`.
- **B — IP-Allowlist**: `/api/webhook/github` per nginx/Firewall auf die
  [GitHub-Hook-IP-Ranges](https://api.github.com/meta) (`hooks`) beschraenken — Defense-in-Depth
  zusaetzlich zur HMAC-Verifikation.
