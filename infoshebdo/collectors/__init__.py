"""Collecteurs de donnees.

Importer ce module suffit a enregistrer toutes les sources : chaque module
appelle `@register` au chargement.
"""
from __future__ import annotations

from .base import Collector, CollectorResult, Observation, register, registry

# L'import est ce qui remplit le registre ; l'ordre fixe l'ordre d'execution.
from . import boxofficemojo  # noqa: F401,E402
from . import allocine  # noqa: F401,E402
from . import steam  # noqa: F401,E402
from . import steamspy  # noqa: F401,E402
from . import editorial  # noqa: F401,E402
from . import manual  # noqa: F401,E402

__all__ = [
    "Collector",
    "CollectorResult",
    "Observation",
    "register",
    "registry",
    "registry_instances",
]


def registry_instances(client, config) -> list[Collector]:
    """Instancie tous les collecteurs enregistres."""
    return [cls(client, config) for cls in registry()]
