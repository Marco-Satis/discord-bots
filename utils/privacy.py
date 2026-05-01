"""Privacy-Hashing fuer DSGVO-konforme Logs.

Persistente Logs (journald, *.log) duerfen Player-Namen nur pseudonymisiert
enthalten. Audit-Log-DB (audit_log Table mit 600-Perms) bleibt Plain-Text
fuer DSGVO-Audit-Trail.

Quelle: ~/.claude/rules/secret-handling.md + Etappe 3.5 PLAN_review-fixes.md
"""
import hashlib


def hash_player(name: str) -> str:
    """Hash einen Player-Namen fuer pseudonyme Logs.

    Args:
        name: Player-Name (Steam, MC, SAT, TS3 etc.)

    Returns:
        8-stelliger SHA256-Praefix (genug fuer Eindeutigkeit, nicht reversible)

    Beispiele:
        >>> hash_player("Marco")
        '3a8f1c2d'  # deterministisch, gleich fuer gleiche Inputs
    """
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]


def short_player(name: str, n: int = 3) -> str:
    """Kuerzt Player-Namen auf erste N Zeichen + ***.

    Hilfreich kombiniert mit hash_player fuer Logs:
        f"{hash_player(name)} ({short_player(name)})"
    """
    return f"{name[:n]}***" if len(name) > n else name


def safe_player_log(name: str) -> str:
    """Kombinierter Hash + Short-Form fuer Logs.

    Beispiel:
        >>> safe_player_log("MarcoTester")
        '3a8f1c2d (Mar***)'
    """
    return f"{hash_player(name)} ({short_player(name)})"
