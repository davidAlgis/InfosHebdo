"""Fenetre principale : deux boutons, et l'application residente derriere.

L'application vit dans la zone de notification. Elle demarre avec la session,
verifie seule s'il y a quelque chose a faire (collecte du jour, rapport de la
semaine) et ne se montre qu'a la demande ou pour signaler une erreur. La
fenetre ne propose que deux gestes :

* Rechercher les donnees : une collecte complete ;
* Ouvrir le rapport : le reconstruit depuis la base et l'ouvre.

Toute la logique (quoi faire, quand, avec quels messages) est dans
`infoshebdo.resident` et `infoshebdo.auto`, testees sans fenetre. Ce module ne
fait que l'afficher, avec trois regles :

* **Rien ne s'execute sur un autre fil que celui de Tk, sauf le traitement
  lui-meme.** Il depose son resultat, puis un message dans une file que la
  fenetre vide sur son propre fil.
* **Un seul traitement a la fois** : les collecteurs ecrivent tous dans la
  meme base.
* **La croix replie la fenetre, elle ne quitte pas.** « Quitter » est dans le
  menu de l'icone. Sans icone possible, la croix quitte normalement : jamais de
  fenetre qu'on ne peut plus retrouver.
"""
from __future__ import annotations

import logging
import queue
import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk

from .. import assets, auto, config as config_module, db, paths, resident, startup, viewer
from ..instance import SingleInstance
from .error_window import ErrorWindow
from .runner import JobDone, JobRunner
from .tray import TrayIcon

log = logging.getLogger(__name__)

POLL_MS = 120
TICK_MS = resident.TICK_SECONDS * 1000
STARTUP_DELAY_MS = 800
WINDOW_TITLE = "InfosHebdo"


class App(tk.Tk):
    def __init__(self, minimized: bool = False, single: SingleInstance | None = None) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry("460x300")
        self.resizable(False, False)
        self._apply_theme()
        self._apply_icon()

        self.config_data = config_module.load()
        self.runner = JobRunner()
        self.notice = resident.DailyNotice()

        # Actions venant du menu de l'icone ou d'une seconde instance.
        self.tray_commands: queue.Queue = queue.Queue()
        self.tray = TrayIcon(
            self.tray_commands,
            autostart_checked=resident.autostart_enabled if startup.is_supported() else None,
        )
        self._tray_ready = False
        self._first_auto_done = False
        self._quitting = False
        self._kind = ""
        self._auto_result: auto.AutoResult | None = None
        self._search_result: resident.SearchResult | None = None
        self._open_result: resident.OpenResult | None = None
        self._error_windows: list[ErrorWindow] = []

        self.single = single
        if single is not None:
            single.on_show = lambda: self.tray_commands.put("show")

        self._build_layout()
        self.refresh_status()
        self._setup_tray()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll)
        if minimized:
            # Lancee par la session : on ne s'impose pas devant l'utilisateur.
            self.after(200, lambda: self.hide_to_tray(quiet=True))
        self.after(STARTUP_DELAY_MS, self._startup)

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _apply_theme(self) -> None:
        style = ttk.Style(self)
        for candidate in ("vista", "clam", "default"):
            if candidate in style.theme_names():
                style.theme_use(candidate)
                break
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Sub.TLabel", foreground="#6b7280")
        style.configure("Status.TLabel", foreground="#374151")
        style.configure("Big.TButton", font=("Segoe UI", 11), padding=(14, 10))

    def _apply_icon(self) -> None:
        try:
            path = assets.icon_path()
            if path is not None:
                self.iconbitmap(default=str(path))
        except Exception as exc:  # noqa: BLE001 - jamais bloquant
            log.debug("icone de fenetre indisponible : %s", exc)

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=(22, 18, 22, 14))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)

        ttk.Label(root, text="InfosHebdo", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(root, text="Veille cinema et jeu video", style="Sub.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )

        self.status_lines = [ttk.Label(root, text="", style="Status.TLabel") for _ in range(2)]
        for index, label in enumerate(self.status_lines):
            label.grid(row=2 + index, column=0, columnspan=2, sticky="w", pady=1)

        self.search_button = ttk.Button(
            root, text="Rechercher les donnees", style="Big.TButton", command=self.search_data
        )
        self.report_button = ttk.Button(
            root, text="Ouvrir le rapport", style="Big.TButton", command=self.open_report
        )
        self.search_button.grid(row=4, column=0, sticky="ew", padx=(0, 6), pady=(18, 10))
        self.report_button.grid(row=4, column=1, sticky="ew", padx=(6, 0), pady=(18, 10))

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.grid(row=5, column=0, columnspan=2, sticky="ew")
        self.message = ttk.Label(root, text="", style="Sub.TLabel", wraplength=410)
        self.message.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

    # ------------------------------------------------------------------ #
    # Zone de notification
    # ------------------------------------------------------------------ #
    def _setup_tray(self) -> None:
        self._tray_ready = self.tray.start()
        if not self._tray_ready:
            log.info("icone de notification indisponible")

    def hide_to_tray(self, quiet: bool = False) -> None:
        """Replie la fenetre. Sans icone de notification, la reduit simplement."""
        if not self._tray_ready:
            self.iconify()
            return
        self.withdraw()
        if not quiet:
            self.tray.notify(
                "InfosHebdo continue en arriere-plan. Double-cliquez sur "
                "l'icone pour revenir."
            )

    def show_window(self) -> None:
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    # ------------------------------------------------------------------ #
    # Etat affiche
    # ------------------------------------------------------------------ #
    def refresh_status(self) -> None:
        try:
            lines = resident.status_lines()
        except Exception as exc:  # noqa: BLE001 - une base verrouillee ne doit pas figer la fenetre
            log.debug("etat non actualise : %s", exc)
            return
        for label, text in zip(self.status_lines, lines):
            label.configure(text=text)

    def set_message(self, text: str) -> None:
        self.message.configure(text=text)

    def _set_busy(self, busy: bool) -> None:
        state = ["disabled"] if busy else ["!disabled"]
        self.search_button.state(state)
        self.report_button.state(state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    # ------------------------------------------------------------------ #
    # Traitements
    # ------------------------------------------------------------------ #
    def _reload_config(self):
        """Relit config.yaml : une modification a la main est prise en compte."""
        self.config_data = config_module.load()
        return self.config_data

    def _start(self, name: str, kind: str, work) -> bool:
        if not self.runner.submit(name, work):
            return False
        self._kind = kind
        self._set_busy(True)
        self.set_message(f"{name}...")
        return True

    def _refuse_if_busy(self) -> bool:
        if self.runner.busy:
            messagebox.showinfo(
                "Traitement en cours",
                f"« {self.runner.current} » est en cours. Un seul traitement a la fois.",
            )
            return True
        return False

    def run_auto(self) -> None:
        """Verification automatique : collecte du jour, rapport de la semaine."""
        if self.runner.busy:
            return
        config = self._reload_config()

        def work() -> str:
            self._auto_result = auto.run(config)
            result = self._auto_result
            if result.opened:
                return "Rapport de la semaine ouvert."
            return result.reason.capitalize() + "." if result.reason else "A jour."

        self._start("Verification", "auto", work)

    def search_data(self) -> None:
        if self._refuse_if_busy():
            return
        config = self._reload_config()

        def work() -> str:
            self._search_result = resident.search_data(config)
            return self._search_result.message

        self._start("Recherche des donnees", "search", work)

    def open_report(self) -> None:
        if self._refuse_if_busy():
            return
        config = self._reload_config()

        def work() -> str:
            self._open_result = resident.open_report_now(config)
            return self._open_result.message

        self._start("Rapport", "report", work)

    # ------------------------------------------------------------------ #
    # Fin d'un traitement
    # ------------------------------------------------------------------ #
    def _on_done(self, done: JobDone) -> None:
        self._set_busy(False)
        self.set_message(done.message)
        self.refresh_status()

        if not done.ok:
            # Une erreur doit etre vue : on remonte la fenetre plutot que de la replier.
            self.show_window()
            messagebox.showerror(done.name, done.message)
            return

        if self._kind == "auto":
            self._after_auto()
        elif self._kind == "search":
            self._after_search()
        elif self._kind == "report":
            result = self._open_result
            if result is not None and not result.opened:
                messagebox.showwarning("Ouverture impossible", f"{result.message}\n\n{result.path}")

    def _after_auto(self) -> None:
        result = self._auto_result
        if result is not None and result.problems and self.notice.allow(date.today()):
            self.show_errors(result.problems, result.explanation, retry=self.search_data)
        if not self._first_auto_done:
            self._first_auto_done = True
            # Premiere verification de la session terminee : on se replie.
            if self._tray_ready:
                self.hide_to_tray()

    def _after_search(self) -> None:
        result = self._search_result
        if result is None:
            return
        if result.problems:
            self.show_errors(result.problems, result.explanation, retry=self.search_data)
        elif self._tray_ready:
            self.tray.notify(f"Recherche terminee : {result.message}.")

    def show_errors(self, problems, explanation: str, retry=None) -> None:
        """Fenetre d'erreur, au premier plan meme si l'application est repliee."""
        window = ErrorWindow(
            problems,
            explanation=explanation,
            report_path=viewer.latest_report(),
            log_path=paths.LOG_DIR / "infoshebdo.log",
            parent=self,
            on_retry=retry,
        )
        self._error_windows = [w for w in self._error_windows if w.root.winfo_exists()]
        self._error_windows.append(window)
        window.present()

    # ------------------------------------------------------------------ #
    # Demarrage et minuterie
    # ------------------------------------------------------------------ #
    def _startup(self) -> None:
        outcome = resident.ensure_autostart()
        log.info("lancement au demarrage : %s", outcome)
        self.run_auto()
        self.after(TICK_MS, self._tick)

    def _tick(self) -> None:
        """Toutes les 30 minutes : y a-t-il quelque chose a faire ?

        Une session laissee ouverte d'un dimanche au lundi ouvre ainsi le
        rapport le lundi, et un poste sorti de veille rattrape la collecte du
        jour. `auto.run` ne fait que ce qui n'est pas deja fait : la plupart
        des verifications ne font rien d'autre que lire la base.
        """
        if self._quitting:
            return
        self.run_auto()
        self.after(TICK_MS, self._tick)

    # ------------------------------------------------------------------ #
    # Menu de l'icone et files
    # ------------------------------------------------------------------ #
    def _toggle_autostart(self) -> None:
        wanted = not resident.autostart_enabled()
        try:
            resident.set_autostart(wanted)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Lancement au demarrage", str(exc))
            return
        self.tray.refresh_menu()
        self.set_message(
            "InfosHebdo se lancera a l'ouverture de session."
            if wanted else "InfosHebdo ne se lancera plus a l'ouverture de session."
        )

    def _handle_command(self, action: str) -> None:
        if action == "show":
            self.show_window()
        elif action == "search":
            self.search_data()
        elif action == "report":
            self.open_report()
        elif action == "toggle_autostart":
            self._toggle_autostart()
        elif action == "quit":
            self._quit_now()

    def _poll(self) -> None:
        for message in self.runner.drain():
            if isinstance(message, JobDone):
                self._on_done(message)

        while True:
            try:
                action = self.tray_commands.get_nowait()
            except queue.Empty:
                break
            self._handle_command(action)

        if not self._quitting:
            self.after(POLL_MS, self._poll)

    # ------------------------------------------------------------------ #
    # Fermeture
    # ------------------------------------------------------------------ #
    def _on_close(self) -> None:
        """La croix replie si l'icone existe, quitte sinon."""
        if self._tray_ready:
            self.hide_to_tray()
            return
        self._quit_now()

    def _quit_now(self) -> None:
        if self.runner.busy:
            self.show_window()
            if not messagebox.askyesno(
                "Traitement en cours",
                f"« {self.runner.current} » est en cours. Quitter quand meme ?\n"
                "Le traitement sera interrompu et la collecte pourra etre incomplete.",
            ):
                return
        self._quitting = True
        self.tray.stop()
        if self.single is not None:
            self.single.close()
        self.destroy()


def launch(minimized: bool = False) -> int:
    """Point d'entree : une seule instance, puis la boucle de la fenetre."""
    logging.getLogger().setLevel(logging.INFO)
    paths.ensure_dirs()
    db.init()

    single: SingleInstance | None = SingleInstance(on_show=lambda: None)
    if not single.acquire():
        if single.signal_existing():
            return 0            # une instance tourne deja : elle se montre
        single = None           # port occupe par autre chose : sans exclusivite

    try:
        App(minimized=minimized, single=single).mainloop()
    finally:
        if single is not None:
            single.close()
    return 0
