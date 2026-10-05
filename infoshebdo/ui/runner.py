"""Execution des traitements longs sans figer l'interface.

Une collecte complete dure une a deux minutes : la lancer dans la boucle
d'evenements Tk gelerait la fenetre. On l'execute donc dans un fil d'execution,
et on fait remonter le journal par une file d'attente.

Regle a ne pas enfreindre : aucun appel a un widget Tk depuis le fil de
travail. Le fil ne fait que deposer des messages dans la file ; c'est la
fenetre qui la vide, sur son propre fil, via `after()`.

Un seul traitement a la fois : les collecteurs ecrivent tous dans la meme base
SQLite, et deux collectes simultanees se disputeraient le verrou.
"""
from __future__ import annotations

import logging
import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Callable


@dataclass
class LogLine:
    text: str
    level: str = "info"


@dataclass
class JobDone:
    name: str
    ok: bool
    message: str


class QueueHandler(logging.Handler):
    """Redirige la journalisation du fil de travail vers la file de l'interface."""

    LEVELS = {
        logging.DEBUG: "debug",
        logging.INFO: "info",
        logging.WARNING: "warn",
        logging.ERROR: "error",
        logging.CRITICAL: "error",
    }

    def __init__(self, sink: queue.Queue) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:  # pragma: no cover - la journalisation ne doit pas planter
            return
        self.sink.put(LogLine(text=text, level=self.LEVELS.get(record.levelno, "info")))


class JobRunner:
    """Lance un traitement en tache de fond et rend son journal consommable."""

    def __init__(self) -> None:
        self.queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._current: str = ""

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def current(self) -> str:
        return self._current if self.busy else ""

    def log(self, text: str, level: str = "info") -> None:
        """Depose une ligne dans la file, depuis n'importe quel fil."""
        self.queue.put(LogLine(text=text, level=level))

    def submit(self, name: str, work: Callable[[], str]) -> bool:
        """Demarre `work`. Renvoie False si un traitement tourne deja."""
        if self.busy:
            return False

        self._current = name

        def wrapper() -> None:
            root = logging.getLogger()
            handler = QueueHandler(self.queue)
            handler.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
            # Les onglets composent deja leur propre resume : on ne remonte que
            # ce qui ne serait pas visible autrement.
            handler.setLevel(logging.WARNING)
            root.addHandler(handler)
            try:
                message = work() or "Termine."
                self.queue.put(JobDone(name=name, ok=True, message=message))
            except Exception as exc:
                self.queue.put(
                    LogLine(text=traceback.format_exc().strip(), level="error")
                )
                self.queue.put(
                    JobDone(name=name, ok=False, message=f"{type(exc).__name__}: {exc}")
                )
            finally:
                root.removeHandler(handler)

        self._thread = threading.Thread(target=wrapper, name=f"infoshebdo-{name}", daemon=True)
        self._thread.start()
        return True

    def drain(self, limit: int = 200) -> list[LogLine | JobDone]:
        """Retire jusqu'a `limit` messages. Appele par la fenetre uniquement.

        La limite evite qu'une collecte tres bavarde monopolise la boucle
        d'evenements : le reste sera lu au tour suivant.
        """
        items: list[LogLine | JobDone] = []
        for _ in range(limit):
            try:
                items.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return items
