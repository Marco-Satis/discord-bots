"""
RBAC-Kern — Discord-Rollen → Dashboard-Berechtigungen.

Spec: docs/RBAC_SPEC_2026-06-04.md (Phasen R1–R3). Modell:

    perms(user) = owner?  ALLES
                : Vereinigung( Grants je Discord-Rolle des Users aus rbac_role_map )
                  ∪ Member-Default (view auf alle nicht-sensiblen Bereiche)

- **Owner** (ENV `OWNER_ID`) darf immer alles — NICHT ueber die Map regelbar
  (Selbst-Aussperr-Schutz). Owner-Erkennung gespiegelt aus `web/auth.py`
  (`is_owner`-Claim im JWT, gesetzt beim Login anhand `OWNER_ID`).
- **Member-Default**: eingeloggt aber keine spezielle Rolle → `view` auf alle
  Bereiche AUSSER den sensiblen (`rbac`, `audit`, `system`). Diese drei sind
  Owner-only bis Marco sie per /rbac explizit einer Rolle freigibt
  (Empfehlung aus Spec §8.4).
- **Bereichs-Rolle**: die in `rbac_role_map` hinterlegten (resource, action)-Grants
  je Discord-Rollen-ID greifen zusaetzlich.

Server-seitig autoritativ — die FastAPI-Dependency `require_perm` (in web/auth.py)
ruft `has_perm` auf. UI-Hide (R5) ist nur Komfort, kein Schutz.
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

from modules.database.db_manager import get_read_db, transaction
from utils.logger import get_logger

logger = get_logger("rbac")

# M28-Fix: Error-Log-Cooldown gegen Flood bei Dauer-DB-Fehler
# Bei dauerhaftem DB-Ausfall wuerde jeder Auth-Check eine ERROR-Zeile loggen.
# Pro Log-Site wird ERROR hoechstens 1x pro Cooldown-Fenster (default 60s)
# emittiert; dazwischen still uebersprungen. Fail-Closed-Verhalten (deny/return)
# bleibt UNVERAENDERT.
_LAST_ERR_LOG: dict[str, float] = {}


def _err_cooldown(key: str, cooldown: float = 60.0) -> bool:
    """True wenn fuer `key` seit dem letzten Log >= `cooldown`s vergangen sind.

    Bei True wird der Timestamp aktualisiert (Caller soll dann loggen). Bei False
    liegt der letzte Log innerhalb des Fensters → Caller ueberspringt das ERROR.
    """
    now = time.monotonic()
    last = _LAST_ERR_LOG.get(key)
    if last is None or (now - last) >= cooldown:
        _LAST_ERR_LOG[key] = now
        return True
    return False

# --- Ressourcen (Bereiche) + Aktionen — zentrale Whitelist --------------------
# Bereichs-Schnitt offen (Spec §8.1): MC vorerst als 1 Bereich `minecraft`.
# TODO(R4/Marco): evtl. BMC5/Vanilla trennen (mc_bmc/mc_vanilla) — dann hier
# ergaenzen + Migration der bestehenden Grants.
RESOURCES: frozenset[str] = frozenset({
    "minecraft",
    "satisfactory",
    "leveling",
    "moderation",
    "lfg",
    "temp_voice",
    "marshal_bot",
    "system",
    "rbac",
    "audit",
})

ACTIONS: frozenset[str] = frozenset({"view", "edit", "control"})

# Sensible Bereiche: KEIN Member-Default-view. Nur Owner oder explizit gegrantet.
SENSITIVE: frozenset[str] = frozenset({"rbac", "audit", "system"})

# Bereiche, die ein eingeloggtes Member by-default ansehen darf (B2).
MEMBER_VIEW_RESOURCES: frozenset[str] = RESOURCES - SENSITIVE

ACTION_VIEW = "view"
ACTION_EDIT = "edit"
ACTION_CONTROL = "control"


# ===========================================================================
# Owner-Erkennung (gespiegelt aus web/auth.py)
# ===========================================================================

def is_owner(user: Optional[dict]) -> bool:
    """True wenn der User Owner-Rechte hat (JWT-Claim `is_owner`)."""
    if not user:
        return False
    return user.get("is_owner") in (True, "True", "true")


def _user_roles(user: Optional[dict]) -> list[str]:
    """Discord-Rollen-IDs des Users als str-Liste (leer wenn keine)."""
    if not user:
        return []
    roles = user.get("roles") or []
    return [str(r) for r in roles if str(r).strip()]


# ===========================================================================
# Permission-Auflösung (autoritativ)
# ===========================================================================

async def has_perm(user: Optional[dict], resource: str, action: str) -> bool:
    """
    Prueft ob `user` `action` auf `resource` ausfuehren darf.

    Reihenfolge: nicht eingeloggt → False; Owner → True; Member-Default
    (`view` auf nicht-sensible Bereiche) → True; sonst Grant-Lookup in
    `rbac_role_map` ueber die Rollen des Users.

    Unbekannte resource/action (nicht in der Whitelist) → False (deny).
    """
    if user is None:
        return False
    if resource not in RESOURCES or action not in ACTIONS:
        logger.warning(f"has_perm: unbekannte resource/action {resource!r}/{action!r} — deny")
        return False
    if is_owner(user):
        return True
    # Member-Default: view auf nicht-sensible Bereiche
    if action == ACTION_VIEW and resource in MEMBER_VIEW_RESOURCES:
        return True
    roles = _user_roles(user)
    if not roles:
        return False
    return await _has_grant(roles, resource, action)


async def _has_grant(roles: list[str], resource: str, action: str) -> bool:
    """Gibt es in rbac_role_map einen Grant (resource, action) fuer eine der Rollen?"""
    if not roles:
        return False
    placeholders = ",".join("?" for _ in roles)
    # Werte parametrisiert (CWE-89 safe); nur die Platzhalter-Anzahl ist dynamisch.
    sql = f"SELECT 1 FROM rbac_role_map WHERE resource = ? AND action = ? AND role_id IN ({placeholders}) LIMIT 1"  # nosec B608 - nur Platzhalter-Anzahl dynamisch, alle Werte parametrisiert
    try:
        conn = await get_read_db()
        cursor = await conn.execute(sql, (resource, action, *roles))
        return await cursor.fetchone() is not None
    except Exception as e:  # noqa: BLE001 — DB-Fehler => deny (fail-closed)
        if _err_cooldown("has_grant"):  # M28-Fix: Error-Log-Cooldown gegen Flood bei Dauer-DB-Fehler
            logger.error(f"_has_grant fehlgeschlagen ({resource}/{action}): {e}")
        return False


async def resolve_perms(user: Optional[dict]) -> set[tuple[str, str]]:
    """
    Liefert die effektive Permission-Menge des Users als {(resource, action)}.

    Fuer Template-Kontext / UI-Hide (R5) + Tests. Owner → alle Kombinationen.
    """
    if user is None:
        return set()
    if is_owner(user):
        return {(r, a) for r in RESOURCES for a in ACTIONS}
    perms: set[tuple[str, str]] = {(r, ACTION_VIEW) for r in MEMBER_VIEW_RESOURCES}
    roles = _user_roles(user)
    if roles:
        perms |= await _grants_for_roles(roles)
    return perms


async def _grants_for_roles(roles: list[str]) -> set[tuple[str, str]]:
    """Alle (resource, action)-Grants der gegebenen Rollen aus rbac_role_map."""
    if not roles:
        return set()
    placeholders = ",".join("?" for _ in roles)
    sql = f"SELECT resource, action FROM rbac_role_map WHERE role_id IN ({placeholders})"  # nosec B608 - nur Platzhalter-Anzahl dynamisch, alle Werte parametrisiert
    try:
        conn = await get_read_db()
        cursor = await conn.execute(sql, tuple(roles))
        rows = await cursor.fetchall()
        # Nur Whitelist-konforme Eintraege zurueckgeben (Defense-in-Depth)
        return {
            (row[0], row[1]) for row in rows
            if row[0] in RESOURCES and row[1] in ACTIONS
        }
    except Exception as e:  # noqa: BLE001
        if _err_cooldown("grants_for_roles"):  # M28-Fix: Error-Log-Cooldown gegen Flood bei Dauer-DB-Fehler
            logger.error(f"_grants_for_roles fehlgeschlagen: {e}")
        return set()


# ===========================================================================
# Rollen-Mapping-Verwaltung (fuer die /rbac-Seite, Owner-only)
# ===========================================================================

async def get_role_map() -> dict[str, set[tuple[str, str]]]:
    """
    Liefert die gesamte Mapping-Tabelle gruppiert nach Rolle:
    `{ role_id: {(resource, action), ...} }`. Fuer die /rbac-Render-Liste.
    """
    out: dict[str, set[tuple[str, str]]] = {}
    try:
        conn = await get_read_db()
        cursor = await conn.execute(
            "SELECT role_id, resource, action FROM rbac_role_map "
            "ORDER BY role_id, resource, action"
        )
        rows = await cursor.fetchall()
        for role_id, resource, action in rows:
            out.setdefault(str(role_id), set()).add((resource, action))
    except Exception as e:  # noqa: BLE001
        if _err_cooldown("get_role_map"):  # M28-Fix: Error-Log-Cooldown gegen Flood bei Dauer-DB-Fehler
            logger.error(f"get_role_map fehlgeschlagen: {e}")
    return out


def validate_grants(grants: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Filtert eine Grant-Liste auf Whitelist-konforme (resource, action)-Paare.
    Unbekannte Paare werden verworfen (geloggt) statt einen Fehler zu werfen.
    """
    clean: list[tuple[str, str]] = []
    for resource, action in grants:
        if resource in RESOURCES and action in ACTIONS:
            clean.append((resource, action))
        else:
            logger.warning(f"validate_grants: verwerfe ungueltiges Paar {resource!r}/{action!r}")
    return clean


async def set_role_grants(
    role_id: str,
    grants: Iterable[tuple[str, str]],
    updated_by: str = "dashboard",
) -> None:
    """
    Setzt die Grants einer Rolle komplett neu (replace): erst alle bestehenden
    Eintraege der Rolle loeschen, dann die uebergebenen (validierten) Paare
    einfuegen. `role_id` muss eine numerische Snowflake sein (Caller validiert).
    """
    role_id = str(role_id).strip()
    if not role_id.isdigit():
        raise ValueError("Rollen-ID muss numerisch sein (Discord-Snowflake)")
    clean = validate_grants(grants)
    # C3 (M39 final): DELETE + INSERT atomar im transaction()-Context-Manager.
    # Dieser haelt den per-Prozess Write-Lock auf einer DEDIZIERTEN Connection
    # und macht den ROLLBACK sicher — ein Partial-Fail (Abbruch nach DELETE)
    # verwirft jetzt die ganze Transaktion und laesst die alten Grants stehen,
    # statt die Rolle ohne Grants zu hinterlassen. Kein Verwerfen fremder Writes
    # mehr (frueheres shared-Connection-Risiko, Review A01/Q1 2026-06-05).
    async with transaction() as conn:
        await conn.execute("DELETE FROM rbac_role_map WHERE role_id = ?", (role_id,))
        for resource, action in clean:
            await conn.execute(
                "INSERT OR IGNORE INTO rbac_role_map "
                "(role_id, resource, action, updated_at, updated_by) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)",
                (role_id, resource, action, updated_by),
            )
    logger.info(
        f"RBAC: Rolle {role_id} -> {len(clean)} Grants gesetzt (von {updated_by})"
    )


async def delete_role(role_id: str) -> None:
    """Entfernt alle Grants einer Rolle (Rolle aus dem Mapping nehmen)."""
    role_id = str(role_id).strip()
    if not role_id.isdigit():
        raise ValueError("Rollen-ID muss numerisch sein (Discord-Snowflake)")
    # C3: ueber transaction() (dedizierte Connection) statt shared get_db().commit()
    # -> kein Phantom-Commit fremder uncommitteter Writes mehr.
    async with transaction() as conn:
        await conn.execute("DELETE FROM rbac_role_map WHERE role_id = ?", (role_id,))
    logger.info(f"RBAC: Rolle {role_id} aus Mapping entfernt")
