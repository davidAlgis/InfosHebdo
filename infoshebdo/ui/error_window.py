"""Fenetre d'erreur de la commande planifiee.

La tache planifiee tourne sans console : une collecte qui echoue ne se verrait
sinon que dans logs/, le jour ou l'on se demande pourquoi le rapport est
vieux. Cette fenetre met l'erreur sous les yeux, avec le message exact de
chaque source, et propose les gestes utiles : ouvrir le rapport, le journal,
l'interface pour relancer une collecte.

Tkinter est charge a la demande : ni la collecte ni la generation du rapport
n'en ont besoin, et un systeme sans affichage doit pouvoir les executer.
"""
from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Sequence

from .. import assets, paths, startup
from .panels import open_in_explorer

log = logging.getLogger(__name__)

WINDOW_TITLE = "InfosHebdo - la collecte a rencontre un probleme"
ERROR_COLOR = "#c0392b"


def heading(count: int) -> str:
    """Titre de la fenetre, accorde selon le nombre de problemes."""
    if count == 1:
        return "1 probleme a ete rencontre"
    return f"{count} problemes ont ete rencontres"


def format_details(problems: Sequence, explanation: str = "") -> str:
    """Texte brut complet, celui qu'on copie pour le coller dans un message.

    `problems` : objets portant `source` et `message`.
    """
    lines: list[str] = []
    if explanation:
        lines += [explanation, ""]
    for problem in problems:
        lines.append(f"- {problem.source}")
        message = (problem.message or "(aucun detail fourni)").strip()
        lines.extend(f"    {line}" for line in message.splitlines() or [""])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class ErrorWindow:
    """Fenetre autonome : elle cree son propre `Tk` et le detruit a la fermeture."""

    def __init__(
        self,
        problems: Sequence,
        explanation: str = "",
        report_path: Path | None = None,
        log_path: Path | None = None,
        root: tk.Tk | None = None,
    ) -> None:
        self.problems = list(problems)
        self.explanation = explanation
        self.report_path = report_path
        self.log_path = log_path
        self.root = root or tk.Tk()
        self._build()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        root = self.root
        root.title(WINDOW_TITLE)
        root.geometry("760x480")
        root.minsize(560, 340)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        try:
            icon = assets.icon_path()
            if icon is not None:
                root.iconbitmap(default=str(icon))
        except Exception as exc:  # noqa: BLE001 - une icone absente n'est pas grave
            log.debug("icone indisponible : %s", exc)

        head = ttk.Label(
            root,
            text=heading(len(self.problems)),
            font=("Segoe UI", 13, "bold"),
            foreground=ERROR_COLOR,
        )
        head.grid(row=0, column=0, sticky="w", padx=14, pady=(14, 4))

        self.explanation_label = ttk.Label(
            root, text=self.explanation, wraplength=720, justify="left"
        )
        self.explanation_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        root.bind(
            "<Configure>",
            lambda e: self.explanation_label.configure(wraplength=max(300, e.width - 40))
            if e.widget is root else None,
        )

        holder = ttk.Frame(root)
        holder.grid(row=2, column=0, sticky="nsew", padx=14)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.text = tk.Text(
            holder, wrap="word", font=("Consolas", 10), relief="solid",
            borderwidth=1, background="#fbfbfc",
        )
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.tag_configure("source", font=("Consolas", 10, "bold"), foreground=ERROR_COLOR)
        self._fill_text()

        bar = ttk.Frame(root, padding=(14, 10))
        bar.grid(row=3, column=0, sticky="ew")
        self.buttons: dict[str, ttk.Button] = {}
        for key, label, command, enabled in (
            ("report", "Ouvrir le dernier rapport", self.open_report,
             bool(self.report_path and self.report_path.exists())),
            ("log", "Ouvrir le journal", self.open_log,
             bool(self.log_path and self.log_path.exists())),
            ("ui", "Ouvrir InfosHebdo", self.open_interface, True),
            ("copy", "Copier le detail", self.copy_details, True),
        ):
            button = ttk.Button(bar, text=label, command=command)
            button.pack(side="left", padx=(0, 6))
            if not enabled:
                button.state(["disabled"])
            self.buttons[key] = button
        close = ttk.Button(bar, text="Fermer", command=root.destroy)
        close.pack(side="right")
        root.bind("<Escape>", lambda _e: root.destroy())
        root.protocol("WM_DELETE_WINDOW", root.destroy)

    def _fill_text(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for problem in self.problems:
            self.text.insert("end", f"{problem.source}\n", "source")
            message = (problem.message or "(aucun detail fourni)").strip()
            self.text.insert("end", message + "\n\n")
        self.text.configure(state="disabled")

    # ------------------------------------------------------------------ #
    def open_report(self) -> None:
        if self.report_path:
            self._safely(open_in_explorer, self.report_path)

    def open_log(self) -> None:
        if self.log_path:
            self._safely(open_in_explorer, self.log_path)

    def open_interface(self) -> None:
        self._safely(startup.launch_detached, ["ui"])

    def copy_details(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(format_details(self.problems, self.explanation))
        self.buttons["copy"].configure(text="Copie !")
        self.root.after(1500, lambda: self.buttons["copy"].configure(text="Copier le detail"))

    @staticmethod
    def _safely(function, *args) -> None:
        """Un bouton qui echoue ne doit pas fermer la fenetre qui porte l'erreur."""
        try:
            function(*args)
        except Exception as exc:  # noqa: BLE001
            log.warning("action impossible depuis la fenetre d'erreur : %s", exc)

    # ------------------------------------------------------------------ #
    def run(self) -> None:
        """Affiche la fenetre au premier plan et attend sa fermeture."""
        root = self.root
        root.update_idletasks()
        root.lift()
        # Au premier plan meme si une autre application a le focus, sans y
        # rester : une tache planifiee demarre souvent derriere la fenetre
        # active, et une erreur qu'on ne voit pas n'en est pas une.
        root.attributes("-topmost", True)
        root.after(400, lambda: root.attributes("-topmost", False))
        root.focus_force()
        root.mainloop()


def show(
    problems: Sequence,
    explanation: str = "",
    report_path: Path | None = None,
    log_path: Path | None = None,
) -> None:
    """Affiche la fenetre et bloque jusqu'a sa fermeture."""
    ErrorWindow(
        problems,
        explanation=explanation,
        report_path=report_path,
        log_path=log_path or paths.LOG_DIR / "infoshebdo.log",
    ).run()
