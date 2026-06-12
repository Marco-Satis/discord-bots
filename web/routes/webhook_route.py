"""
F47: GitHub-Webhook für automatisches Deployment.

Empfängt Push-Events von GitHub, verifiziert die HMAC-Signatur
und führt bei Pushes auf den main/master-Branch automatisch
ein Deployment aus (git pull, pip install, Service-Restart).

Das Webhook-Secret wird aus der Umgebungsvariable GITHUB_WEBHOOK_SECRET
gelesen. Ist diese leer, ist der Webhook deaktiviert.
"""

import asyncio
import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from modules.database.db_manager import get_db
from utils.config import get_env, PROJECT_ROOT
from utils.logger import get_logger
from web.auth import require_auth_api

logger = get_logger("web.routes.webhook")

router = APIRouter(tags=["Webhook"])

# Webhook-Secret aus Umgebungsvariable — leer = deaktiviert
WEBHOOK_SECRET = get_env("GITHUB_WEBHOOK_SECRET", "")

# Deploy-Befehle: werden nacheinander via asyncio subprocess ausgeführt
DEPLOY_COMMANDS = [
    ["git", "pull", "origin", "main"],
    ["pip", "install", "-r", "requirements.txt", "-q"],
]

# Services, die nach dem Deploy neugestartet werden
RESTART_SERVICES = ["marshal-bot", "recon-bot", "operator-bot", "web-dashboard"]


# ================================================================
#  Hilfsfunktionen
# ================================================================


def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verifiziert die GitHub-Webhook-Signatur (HMAC-SHA256).

    Vergleicht den X-Hub-Signature-256 Header mit dem erwarteten
    HMAC-Hash des Payloads. Nutzt compare_digest gegen Timing-Angriffe.
    """
    if not signature or not secret:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _run_command(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    """
    Führt einen Shell-Befehl asynchron aus.

    Returns:
        Tuple aus (returncode, stdout, stderr)
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )
    except asyncio.TimeoutError:
        return (1, "", "Timeout nach 120 Sekunden")
    except Exception as exc:
        return (1, "", str(exc))


async def _log_deployment(
    commit_sha: str,
    branch: str,
    pusher: str,
    status: str,
    details: str,
) -> None:
    """Schreibt einen Deployment-Eintrag ins Audit-Log (SQLite)."""
    try:
        db = await get_db()
        await db.execute(
            "INSERT INTO audit_log (timestamp, user_id, user_name, action, target, details, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                pusher,
                pusher,
                f"deploy_{status}",
                branch,
                f"Commit: {commit_sha[:8]} — {details}"[:500],
                "github_webhook",
            ),
        )
        await db.commit()
    except Exception as exc:
        logger.error(f"Deployment Audit-Log fehlgeschlagen: {exc}")


async def _execute_deploy(commit_sha: str, branch: str, pusher: str) -> dict:
    """
    Führt die gesamte Deploy-Sequenz aus:
    1. git pull
    2. pip install
    3. systemctl restart für alle Services

    Returns:
        Dict mit Status und Details
    """
    cwd = str(PROJECT_ROOT)
    results: list[str] = []
    success = True

    # --- Deploy-Befehle ausführen ---
    for cmd in DEPLOY_COMMANDS:
        cmd_str = " ".join(cmd)
        logger.info(f"Deploy-Befehl: {cmd_str}")
        returncode, stdout, stderr = await _run_command(cmd, cwd)

        if returncode != 0:
            error_msg = f"FEHLER bei '{cmd_str}': {stderr or stdout}"
            results.append(error_msg)
            logger.error(error_msg)
            success = False
            break

        result_msg = f"OK: {cmd_str}"
        if stdout:
            result_msg += f" ({stdout[:100]})"
        results.append(result_msg)
        logger.info(result_msg)

    # --- Services neustarten (nur wenn bisherige Schritte erfolgreich) ---
    if success:
        for service in RESTART_SERVICES:
            service_name = f"{service}.service"
            logger.info(f"Starte Service neu: {service_name}")
            returncode, stdout, stderr = await _run_command(
                ["sudo", "systemctl", "restart", service_name], cwd
            )

            if returncode != 0:
                warn_msg = f"Service-Restart Warnung: {service_name} — {stderr}"
                results.append(warn_msg)
                logger.warning(warn_msg)
                # Service-Restart-Fehler sind Warnungen, kein Abbruch
            else:
                results.append(f"Service neugestartet: {service_name}")

    status = "success" if success else "failed"
    details = "; ".join(results)

    # Deployment ins Audit-Log schreiben
    await _log_deployment(commit_sha, branch, pusher, status, details)

    return {
        "status": status,
        "commit": commit_sha[:8],
        "branch": branch,
        "pusher": pusher,
        "details": results,
    }


# ================================================================
#  API-Endpoints
# ================================================================


@router.post("/api/webhook/github")
async def github_webhook(request: Request) -> JSONResponse:
    """
    Empfängt GitHub-Push-Events und löst ein automatisches
    Deployment aus, wenn der Push auf main/master erfolgt.

    Workflow:
      1. Prüfen ob Webhook aktiviert ist (Secret gesetzt)
      2. Signatur verifizieren (X-Hub-Signature-256)
      3. Nur Push-Events auf main/master verarbeiten
      4. Deploy-Sequenz asynchron ausführen
      5. Ergebnis zurückgeben und ins Audit-Log schreiben
    """
    # Webhook deaktiviert wenn kein Secret konfiguriert
    if not WEBHOOK_SECRET:
        logger.warning("GitHub-Webhook empfangen, aber GITHUB_WEBHOOK_SECRET ist nicht gesetzt")
        return JSONResponse(
            status_code=403,
            content={"error": "Webhook ist deaktiviert (kein Secret konfiguriert)"},
        )

    # --- Signatur-Verifizierung ---
    signature = request.headers.get("X-Hub-Signature-256", "")
    try:
        payload = await request.body()
    except Exception as exc:
        logger.error(f"Payload lesen fehlgeschlagen: {exc}")
        return JSONResponse(
            status_code=400,
            content={"error": "Payload konnte nicht gelesen werden"},
        )

    if not _verify_signature(payload, signature, WEBHOOK_SECRET):
        logger.warning("GitHub-Webhook: Ungültige Signatur")
        return JSONResponse(
            status_code=401,
            content={"error": "Ungültige Signatur"},
        )

    # --- Event-Typ prüfen ---
    event_type = request.headers.get("X-GitHub-Event", "")

    # Ping-Event bestätigen (GitHub sendet Ping bei Webhook-Erstellung)
    if event_type == "ping":
        logger.info("GitHub-Webhook Ping empfangen — Verbindung bestätigt")
        return JSONResponse(content={"status": "pong"})

    # Nur Push-Events verarbeiten
    if event_type != "push":
        logger.debug(f"GitHub-Event ignoriert: {event_type}")
        return JSONResponse(
            content={"status": "ignored", "reason": f"Event-Typ '{event_type}' wird nicht verarbeitet"},
        )

    # --- Push-Event verarbeiten ---
    try:
        data = await request.json()
    except Exception as exc:
        logger.error(f"JSON-Parsing fehlgeschlagen: {exc}")
        return JSONResponse(
            status_code=400,
            content={"error": "Ungültiger JSON-Payload"},
        )

    # Branch aus ref extrahieren: "refs/heads/main" -> "main"
    ref = data.get("ref", "")
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref

    # Nur main/master-Branch verarbeiten
    if branch not in ("main", "master"):
        logger.info(f"Push auf Branch '{branch}' ignoriert — nur main/master wird deployed")
        return JSONResponse(
            content={"status": "ignored", "reason": f"Branch '{branch}' wird nicht deployed"},
        )

    # Commit- und Pusher-Informationen extrahieren
    commit_sha = data.get("after", "unknown")
    pusher = data.get("pusher", {}).get("name", "unknown")
    commit_msg = ""
    commits = data.get("commits", [])
    if commits:
        commit_msg = commits[-1].get("message", "")[:100]

    logger.info(
        f"GitHub-Push empfangen: {pusher} -> {branch} "
        f"(Commit: {commit_sha[:8]}, Nachricht: {commit_msg})"
    )

    # --- Deploy asynchron ausführen ---
    try:
        result = await _execute_deploy(commit_sha, branch, pusher)
    except Exception as exc:
        error_msg = f"Deploy-Fehler: {exc}"
        logger.error(error_msg)
        await _log_deployment(commit_sha, branch, pusher, "error", error_msg)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(exc)[:200]},
        )

    status_code = 200 if result["status"] == "success" else 500
    return JSONResponse(status_code=status_code, content=result)


@router.get("/api/webhook/deploy-history")
async def deploy_history(current_user: dict = Depends(require_auth_api)) -> JSONResponse:
    """
    Gibt die letzten 10 Deployments aus dem Audit-Log zurueck.
    Erfordert Authentifizierung.

    Hinweis: github_webhook ist bewusst NICHT mit Depends(require_auth_api) versehen
    — GitHub sendet keinen Session-Cookie, sondern HMAC-Signatur via _verify_signature().
    """
    try:
        db = await get_db()
        cursor = await db.execute(
            "SELECT id, timestamp, user_id, user_name, action, target, details, source "
            "FROM audit_log "
            "WHERE source = 'github_webhook' "
            "ORDER BY timestamp DESC "
            "LIMIT 10"
        )
        rows = await cursor.fetchall()

        deployments = []
        for row in rows:
            deployments.append({
                "id": row["id"],
                "timestamp": row["timestamp"],
                "pusher": row["user_name"],
                "action": row["action"],
                "branch": row["target"],
                "details": row["details"],
            })

        return JSONResponse(content={
            "count": len(deployments),
            "deployments": deployments,
        })

    except Exception as exc:
        logger.error(f"Deploy-History Fehler: {exc}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Fehler beim Lesen der Deploy-History: {str(exc)[:200]}"},
        )
