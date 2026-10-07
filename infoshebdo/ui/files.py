"""Ouverture d'un fichier ou d'un dossier avec l'application par defaut."""
from __future__ import annotations

import os
import subprocess
import sys


def open_in_explorer(path) -> None:
    """Ouvre un fichier ou un dossier avec l'application par defaut."""
    target = str(path)
    if os.name == "nt":
        os.startfile(target)  # noqa: S606 - chemin construit par le programme
    elif sys.platform == "darwin":
        subprocess.run(["open", target], check=False)
    else:
        subprocess.run(["xdg-open", target], check=False)
