#!/usr/bin/env python
"""Point d'entree independant du repertoire courant.

Equivalent de « python -m infoshebdo », mais utilisable depuis n'importe ou :
c'est ce lanceur qu'inscrit le Planificateur de taches Windows, qui demarre
ses taches depuis system32.

    python run.py collect
    python run.py ui

Deux precautions propres a l'execution planifiee, ou il n'y a aucune console :

* `pythonw.exe` laisse `sys.stdout` et `sys.stderr` a None. Tout `print` est
  alors silencieusement perdu, et un `logging.StreamHandler` sur ce flux leve
  une exception a chaque message. On branche donc des flux de remplacement.
* Une erreur survenant avant la mise en place du journal ne s'afficherait
  nulle part : la tache se contenterait d'echouer en silence. On l'ecrit dans
  logs/crash.log.
"""
from __future__ import annotations

import io
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Empaquete (PyInstaller), `__file__` pointe dans le dossier interne de
# l'application : les journaux et les donnees vivent a cote de l'executable.
if getattr(sys, "frozen", False):
    PROJECT_DIR = Path(sys.executable).resolve().parent
else:
    PROJECT_DIR = Path(__file__).resolve().parent
    sys.path.insert(0, str(PROJECT_DIR))


def _ensure_streams() -> None:
    """Garantit des flux utilisables, meme lances par pythonw.exe."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))


def _report_crash(error: BaseException) -> None:
    """Derniere chance de laisser une trace lisible."""
    try:
        log_dir = PROJECT_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (log_dir / "crash.log").open("a", encoding="utf-8") as fh:
            fh.write(f"\n===== {stamp} - {' '.join(sys.argv)} =====\n")
            fh.write("".join(traceback.format_exception(type(error), error, error.__traceback__)))
    except Exception:
        pass  # on ne peut vraiment plus rien faire


def main() -> int:
    _ensure_streams()
    try:
        from infoshebdo.cli import main as cli_main

        return cli_main()
    except SystemExit:
        raise
    except BaseException as error:  # noqa: BLE001 - trace ou rien
        _report_crash(error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
