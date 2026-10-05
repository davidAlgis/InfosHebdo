#!/usr/bin/env python
"""Construit l'executable d'InfosHebdo et l'installe pour l'utilisateur courant.

    python build_exe.py                  # tests, build, verification, installation
    python build_exe.py --register       # + tache planifiee et icone au demarrage
    python build_exe.py --no-install     # build seulement (resultat dans build/dist)

Destination par defaut : %LOCALAPPDATA%\\Programs\\InfosHebdo, le dossier que
Windows reserve aux programmes installes sans droits administrateur.

Etapes, dans l'ordre. Chacune s'arrete net a la premiere erreur :

1. les tests (sauf --skip-tests) : on ne fabrique pas un executable d'un code
   qui ne passe pas ses tests ;
2. l'icone, generee par le programme ;
3. PyInstaller, d'apres infoshebdo.spec ;
4. une verification de l'executable fabrique, AVANT toute installation :
   `selftest` verifie que chaque module et ressource a bien ete embarque. Un
   module oublie ne se voit sinon qu'un matin ou la tache planifiee echoue ;
5. l'installation : on remplace ce que le programme livre (les executables et
   leur dossier interne), jamais les donnees de l'utilisateur
   (config.yaml, .env, data/, reports/, logs/) ;
6. avec --register : tache planifiee et lancement de l'icone au demarrage.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = BUILD_DIR / "dist"
WORK_DIR = BUILD_DIR / "work"
ICON_FILE = BUILD_DIR / "assets" / "infoshebdo.ico"
SPEC_FILE = ROOT / "infoshebdo.spec"

APP = "InfosHebdo"
APP_DIR_NAME = "InfosHebdo"          # dossier produit par PyInstaller (COLLECT name)
GUI_EXE = "InfosHebdo.exe"
CLI_EXE = "InfosHebdo-console.exe"

# Ce qui appartient a l'utilisateur : jamais ecrase, jamais supprime.
USER_DATA = ("config.yaml", ".env", "data", "reports", "logs")


class BuildError(RuntimeError):
    """Echec d'une etape, avec un message affichable tel quel."""


# --------------------------------------------------------------------------- #
# Affichage
# --------------------------------------------------------------------------- #
def step(number: int, title: str) -> None:
    print(f"\n[{number}] {title}")


def info(message: str) -> None:
    print(f"    {message}")


def oem_encoding() -> str:
    """Page de codes de la console : celle dans laquelle les .exe ecrivent."""
    if os.name == "nt":
        try:
            import ctypes

            return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:  # noqa: BLE001
            return "cp850"
    return "utf-8"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Lance une commande, sortie capturee et decodee comme la console."""
    completed = subprocess.run(command, capture_output=True, **kwargs)
    completed.out = completed.stdout.decode(oem_encoding(), errors="replace")
    completed.err = completed.stderr.decode(oem_encoding(), errors="replace")
    return completed


def default_destination() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    local = Path(base) if base else Path.home() / "AppData" / "Local"
    return local / "Programs" / APP


# --------------------------------------------------------------------------- #
# Etapes
# --------------------------------------------------------------------------- #
def check_environment() -> None:
    if os.name != "nt":
        raise BuildError(
            "Ce script fabrique un executable Windows. Sous Linux ou macOS, "
            "lancer simplement `python run.py auto` depuis cron."
        )
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise BuildError(
            "PyInstaller n'est pas installe. Installer les dependances de build :\n"
            "    pip install -r requirements-build.txt"
        ) from exc
    for module in ("PIL", "pystray"):
        try:
            __import__(module)
        except ImportError as exc:
            raise BuildError(
                f"Module « {module} » manquant. Installer les dependances :\n"
                "    pip install -r requirements.txt"
            ) from exc


def run_tests() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=ROOT,
    )
    if result.returncode != 0:
        raise BuildError(
            "Les tests echouent : l'executable n'est pas fabrique. "
            "Corriger, ou relancer avec --skip-tests en connaissance de cause."
        )


def make_icon() -> Path:
    sys.path.insert(0, str(ROOT))
    from infoshebdo import assets

    ICON_FILE.parent.mkdir(parents=True, exist_ok=True)
    return assets.build_icon(ICON_FILE)


def build(clean: bool) -> Path:
    if clean and BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    make_icon()
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--distpath", str(DIST_DIR),
        "--workpath", str(WORK_DIR),
        str(SPEC_FILE),
    ]
    info("PyInstaller (une a deux minutes)...")
    result = run(command, cwd=ROOT)
    if result.returncode != 0:
        tail = "\n".join((result.out + result.err).strip().splitlines()[-25:])
        raise BuildError(f"PyInstaller a echoue :\n{tail}")

    output = DIST_DIR / APP_DIR_NAME
    for exe in (GUI_EXE, CLI_EXE):
        if not (output / exe).exists():
            raise BuildError(f"{exe} absent du resultat de PyInstaller ({output}).")
    return output


def shipped_entries(output: Path) -> list[str]:
    """Contenu livre, releve AVANT les verifications : elles creent des dossiers
    de travail (logs/, data/...) dans le dossier de build, qu'on ne livre pas."""
    return sorted(entry.name for entry in output.iterdir())


def smoke_test(output: Path) -> None:
    """Fait tourner l'executable fabrique, sans rien ecrire hors du dossier de build."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "INFOSHEBDO_DB": str(Path(tmp) / "essai.sqlite3")}

        console = run([str(output / CLI_EXE), "selftest"], env=env, cwd=tmp)
        for line in console.out.strip().splitlines():
            info(line)
        if console.returncode != 0:
            raise BuildError(
                f"`{CLI_EXE} selftest` echoue (code {console.returncode}). "
                "Un module ou une ressource manque dans l'executable.\n" + console.err
            )

        # L'executable sans console est celui de la tache planifiee : on
        # verifie qu'il demarre et rend un code retour, meme s'il n'ecrit nulle part.
        windowed = run([str(output / GUI_EXE), "selftest"], env=env, cwd=tmp, timeout=120)
        if windowed.returncode != 0:
            raise BuildError(
                f"`{GUI_EXE} selftest` echoue (code {windowed.returncode})."
            )
        info(f"{GUI_EXE} : demarre et verifie (code 0).")


def is_running(exe: str = GUI_EXE) -> bool:
    result = run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"])
    return exe.lower() in result.out.lower()


def stop_running() -> None:
    for exe in (GUI_EXE, CLI_EXE):
        run(["taskkill", "/F", "/IM", exe])


def check_destination(dest: Path) -> None:
    """Refuse d'ecrire dans un dossier qui n'est visiblement pas le notre."""
    if not dest.exists():
        return
    entries = {entry.name for entry in dest.iterdir()}
    if entries and not (entries & {GUI_EXE, CLI_EXE, "config.yaml", "data"}):
        raise BuildError(
            f"{dest} existe, n'est pas vide et ne ressemble pas a une installation "
            "d'InfosHebdo. Choisir un autre dossier avec --dest."
        )


def install(output: Path, entries: list[str], dest: Path, with_data: bool, kill: bool) -> None:
    check_destination(dest)

    if is_running() or is_running(CLI_EXE):
        if not kill:
            raise BuildError(
                "InfosHebdo est en cours d'execution : ses fichiers sont verrouilles.\n"
                "    Le quitter (icone pres de l'horloge -> Quitter), ou relancer avec --kill."
            )
        info("Arret des processus InfosHebdo en cours...")
        stop_running()

    dest.mkdir(parents=True, exist_ok=True)

    # On remplace ce que le programme livre, et seulement cela.
    for name in entries:
        if name in USER_DATA:
            continue
        target = dest / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        source = output / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    info(f"Programme installe dans {dest}")

    # Reprise des reglages et de l'historique du depot, sans jamais ecraser.
    config_source = ROOT / "config.yaml"
    if config_source.exists() and not (dest / "config.yaml").exists():
        shutil.copy2(config_source, dest / "config.yaml")
        info("config.yaml repris du depot.")
    if with_data:
        copy_data(dest)


def copy_data(dest: Path) -> None:
    source_data = ROOT / "data"
    target_data = dest / "data"
    target_data.mkdir(parents=True, exist_ok=True)
    database = source_data / "infoshebdo.sqlite3"
    if database.exists() and not (target_data / database.name).exists():
        shutil.copy2(database, target_data / database.name)
        info("Base de donnees reprise du depot.")
    elif database.exists():
        info("Base de donnees deja presente : conservee.")
    imports = source_data / "import"
    if imports.exists() and not (target_data / "import").exists():
        shutil.copytree(imports, target_data / "import")
        info("Dossier d'import manuel repris du depot.")


def register(dest: Path) -> None:
    """Tache planifiee et lancement de l'icone a l'ouverture de session."""
    console = dest / CLI_EXE
    for arguments in (["schedule", "install"], ["startup", "enable"]):
        result = run([str(console), *arguments], cwd=dest)
        for line in result.out.strip().splitlines():
            info(line)
        if result.returncode != 0:
            raise BuildError(
                f"`{CLI_EXE} {' '.join(arguments)}` echoue :\n{result.err.strip()}"
            )


# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fabrique l'executable d'InfosHebdo et l'installe pour l'utilisateur courant."
    )
    parser.add_argument(
        "--dest", type=Path, default=None,
        help=f"dossier d'installation (defaut : {default_destination()})",
    )
    parser.add_argument("--no-install", action="store_true",
                        help="fabriquer sans installer (resultat dans build/dist)")
    parser.add_argument("--skip-tests", action="store_true",
                        help="ne pas lancer les tests avant la fabrication")
    parser.add_argument("--clean", action="store_true",
                        help="repartir de zero (supprime build/)")
    parser.add_argument("--kill", action="store_true",
                        help="arreter InfosHebdo s'il tourne, pour pouvoir le remplacer")
    parser.add_argument("--with-data", action="store_true",
                        help="reprendre la base et les imports du depot (sans ecraser)")
    parser.add_argument("--register", action="store_true",
                        help="inscrire la tache planifiee et le lancement de l'icone a la session")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dest = (args.dest or default_destination()).resolve()

    try:
        step(1, "Verification de l'environnement")
        check_environment()
        info("ok")

        step(2, "Tests")
        if args.skip_tests:
            info("ignores (--skip-tests)")
        else:
            run_tests()

        step(3, "Fabrication de l'executable")
        output = build(args.clean)
        entries = shipped_entries(output)
        info(f"{', '.join(entries)}")

        step(4, "Verification de l'executable fabrique")
        smoke_test(output)

        if args.no_install:
            print(f"\nTermine. Resultat : {output}")
            return 0

        step(5, f"Installation dans {dest}")
        install(output, entries, dest, args.with_data, args.kill)

        if args.register:
            step(6, "Tache planifiee et lancement a l'ouverture de session")
            register(dest)

    except BuildError as exc:
        print(f"\nERREUR : {exc}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as exc:
        print(f"\nERREUR : delai depasse : {exc}", file=sys.stderr)
        return 1

    print("\nTermine.")
    print(f"  Executable   : {dest / GUI_EXE}")
    print(f"  Console      : {dest / CLI_EXE}")
    if not args.register:
        print("\nPour activer l'ouverture automatique du rapport et l'icone pres de l'horloge :")
        print(f'  "{dest / CLI_EXE}" schedule install')
        print(f'  "{dest / CLI_EXE}" startup enable')
        print("  (ou relancer ce script avec --register)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
