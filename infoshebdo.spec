# -*- mode: python ; coding: utf-8 -*-
"""Specification PyInstaller d'InfosHebdo.

A lancer par `python build_exe.py`, pas a la main : le script genere l'icone,
verifie le resultat et l'installe.

Deux executables partagent les memes bibliotheques (un seul dossier) :

* InfosHebdo.exe          sans console : l'interface, et la tache planifiee.
                          Une console noire ne doit jamais apparaitre chaque
                          matin ;
* InfosHebdo-console.exe  avec console : pour les commandes (`status`,
                          `schedule install`, `selftest`...) dont on veut lire
                          la sortie. Un executable sans console ne peut pas
                          ecrire dans le terminal qui l'a lance.

Format « dossier » plutot que « fichier unique » : un fichier unique
s'extrait dans un dossier temporaire a chaque lancement, ce qui ralentit
chaque declenchement de la tache planifiee et alerte davantage les antivirus.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH)  # noqa: F821 - fourni par PyInstaller
ICON = ROOT / "build" / "assets" / "infoshebdo.ico"
if not ICON.exists():
    raise SystemExit("Icone absente : lancer `python build_exe.py`.")

# L'interface et la fenetre d'erreur sont importees a la demande (dans des
# fonctions) ; pystray choisit son moteur Windows par un import dynamique que
# l'analyse statique ne voit pas. Sans ces lignes, l'executable demarre mais
# l'icone ou la fenetre manquent au moment de s'en servir.
HIDDEN = collect_submodules("infoshebdo") + [
    "pystray._win32",
    "lxml.etree",
    "lxml._elementpath",
]

# Le programme n'utilise que Tk (interface), Pillow (icone) et pystray. Le
# Python qui fabrique l'executable en contient souvent bien d'autres, et les
# « hooks » de PyInstaller les embarqueraient des qu'un module les mentionne :
# PyQt5 et PyQt6 ensemble font meme echouer la fabrication. On les exclut
# explicitement pour que le resultat ne depende pas de la machine de build.
EXCLUDES = [
    "PyQt5", "PyQt6", "PySide2", "PySide6", "PIL.ImageQt",
    "numpy", "scipy", "pandas", "matplotlib",
    "IPython", "jedi", "parso", "zmq", "tornado", "notebook", "nbformat",
    "jsonschema", "jsonschema_specifications", "sphinx", "docutils",
    "pytest", "setuptools", "pkg_resources", "wheel", "cryptography",
]

DATAS = [
    # `paths.TEMPLATES_DIR` : RESOURCE_DIR / "templates" en mode empaquete.
    (str(ROOT / "infoshebdo" / "templates"), "templates"),
    # `assets.icon_path` cherche RESOURCE_DIR / "assets" / "infoshebdo.ico".
    (str(ICON), "assets"),
]

analysis = Analysis(  # noqa: F821
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(analysis.pure)  # noqa: F821


def make_exe(name: str, console: bool):
    return EXE(  # noqa: F821
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        icon=str(ICON),
        debug=False,
        strip=False,
        upx=False,
    )


gui = make_exe("InfosHebdo", console=False)
cli = make_exe("InfosHebdo-console", console=True)

COLLECT(  # noqa: F821
    gui,
    cli,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="InfosHebdo",
)
