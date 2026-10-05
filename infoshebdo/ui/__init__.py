"""Interface graphique Tkinter.

Importee a la demande par `infoshebdo ui` : Tkinter n'est pas charge lors
d'une collecte planifiee, qui tourne sans affichage.
"""
from __future__ import annotations


def launch(minimized: bool = False) -> int:
    from .app import launch as _launch

    return _launch(minimized=minimized)


__all__ = ["launch"]
