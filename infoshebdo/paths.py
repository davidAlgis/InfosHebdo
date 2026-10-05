"""Emplacements de fichiers, resolus une seule fois.

Deux racines a ne pas confondre, et c'est ce qui casse le plus souvent une
application empaquetee :

* `RESOURCE_DIR` : les ressources livrees avec le programme (gabarit du
  rapport, icone). En mode normal c'est le paquet Python ; dans un executable
  PyInstaller en un seul fichier, c'est un dossier temporaire recree a chaque
  lancement et efface a la sortie. On n'y ecrit jamais.
* `PROJECT_DIR` : les donnees de l'utilisateur (config.yaml, .env, base,
  rapports, journaux). En mode normal c'est la racine du depot ; en mode
  empaquete c'est le dossier de l'executable, pour que la configuration et
  l'historique survivent au remplacement du .exe.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

# `sys.frozen` est pose par PyInstaller ; `sys._MEIPASS` designe le dossier
# d'extraction des ressources embarquees.
FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", PACKAGE_DIR))
    PROJECT_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = PACKAGE_DIR
    PROJECT_DIR = PACKAGE_DIR.parent

DATA_DIR = PROJECT_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = PROJECT_DIR / "reports"
IMPORT_DIR = DATA_DIR / "import"
PROCESSED_IMPORT_DIR = IMPORT_DIR / "traites"
LOG_DIR = PROJECT_DIR / "logs"

# Ressources : embarquees en mode empaquete, dans le paquet sinon.
TEMPLATES_DIR = RESOURCE_DIR / "templates" if FROZEN else PACKAGE_DIR / "templates"
# L'icone peut etre generee, donc elle a aussi un emplacement inscriptible.
ASSETS_DIR = PROJECT_DIR / "assets" if FROZEN else PACKAGE_DIR / "assets"

CONFIG_FILE = PROJECT_DIR / "config.yaml"
ENV_FILE = PROJECT_DIR / ".env"

# Lanceur independant du repertoire courant. Le Planificateur de taches
# Windows demarre ses taches depuis system32 : « python -m infoshebdo » n'y
# trouverait pas le paquet, alors qu'un chemin absolu vers ce fichier suffit.
# Sans objet en mode empaquete, ou l'executable se suffit a lui-meme.
LAUNCHER = PROJECT_DIR / "run.py"

# Executable empaquete sans console (voir infoshebdo.spec).
WINDOWLESS_EXE = "InfosHebdo.exe"


def db_path() -> Path:
    """Chemin de la base SQLite (surchargeable par INFOSHEBDO_DB)."""
    override = os.environ.get("INFOSHEBDO_DB")
    return Path(override) if override else DATA_DIR / "infoshebdo.sqlite3"


def ensure_dirs() -> None:
    for d in (DATA_DIR, CACHE_DIR, REPORTS_DIR, IMPORT_DIR, LOG_DIR, ASSETS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def command_prefix() -> list[str]:
    """Debut de la ligne de commande pour relancer l'outil.

    Empaquete : l'executable sans console (InfosHebdo.exe), meme si la
    commande est lancee depuis sa version console : inscrire celle-ci ferait
    apparaitre une fenetre noire a chaque declenchement. Sinon : l'interpreteur
    sans console suivi du lanceur. Utilise par la planification et par le
    demarrage automatique, qui doivent tous deux pointer vers la bonne cible.
    """
    if FROZEN:
        executable = Path(sys.executable).resolve()
        windowless = executable.with_name(WINDOWLESS_EXE)
        return [str(windowless if windowless.exists() else executable)]

    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    if windowless.exists():
        interpreter = windowless
    return [str(interpreter), "-X", "utf8", str(LAUNCHER)]
