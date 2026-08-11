"""
Mod-Verwaltung fuer die Gameserver.

Zwei Backends, beide auf echten Quellen statt auf einem JSON-Katalog:

* `ficsit_backend`   — Satisfactory ueber das OFFIZIELLE ficsit-cli
                       (Downloads, Abhaengigkeiten und SML macht das CLI)
* `minecraft_backend` — Minecraft ueber das Dateisystem (.jar/.jar.disabled)
                       + Modrinth-API fuer Downloads

Der frueher hier liegende `modules/mod_manager.py` war eine Attrappe: kein
Download, kein Dateizugriff, aber Erfolgsmeldungen. Er bleibt als duenne
Fassade bestehen, damit bestehende Importe weiterlaufen, delegiert aber an
diese Backends.
"""

from modules.mods import ficsit_backend, minecraft_backend  # noqa: F401

__all__ = ["ficsit_backend", "minecraft_backend"]
