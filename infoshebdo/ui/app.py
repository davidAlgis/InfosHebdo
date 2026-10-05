"""Fenetre principale.

Assemble les onglets, la zone de journal, l'execution en tache de fond et
l'icone de notification.

Trois choix d'ergonomie assumes :

* **Tout traitement enregistre d'abord les reglages.** Sans cela, modifier un
  marche puis cliquer sur « Collecter » lancerait la collecte avec l'ancienne
  configuration : c'est l'incomprehension la plus previsible d'une interface de
  ce genre.
* **La fenetre se replie, elle ne se ferme pas.** Lancee au demarrage de la
  session, l'application est residente : la croix replie dans la zone de
  notification et « Quitter » reste dans le menu de l'icone. Si l'icone n'a pas
  pu etre creee, la croix quitte normalement — jamais de fenetre qu'on ne peut
  plus retrouver.
* **Rien ne s'execute sur un autre fil que celui de Tk.** Les traitements et
  les clics du menu de l'icone deposent des messages dans des files, vidées par
  la fenetre.
"""
from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import messagebox, ttk

from .. import assets, config as config_module
from .. import db, paths, pipeline, report as report_module, viewer
from ..http import Client
from .panels import DashboardPanel, ImportPanel, SchedulePanel
from .runner import JobDone, JobRunner, LogLine
from .settings import FeedsPanel, ReportPanel, TrackingPanel
from .tray import TrayIcon
from .widgets import LogPane

log = logging.getLogger(__name__)

POLL_MS = 120
WINDOW_TITLE = "InfosHebdo - veille cinema et jeu video"


class App(tk.Tk):
    def __init__(self, minimized: bool = False) -> None:
        super().__init__()
        self.title(WINDOW_TITLE)
        self.geometry("1060x780")
        self.minsize(920, 640)

        self._apply_theme()
        self._apply_icon()

        self.config_data = config_module.load()
        self.runner = JobRunner()

        # Actions venant du menu de l'icone de notification.
        self.tray_commands: queue.Queue = queue.Queue()
        self.tray = TrayIcon(self.tray_commands)
        self._tray_ready = False
        self._first_job_done = False
        self._quitting = False

        self._build_layout()
        self._load_panels()

        self.dashboard.refresh()
        self.schedule.refresh()
        self.imports.refresh()

        self.log("InfosHebdo pret.", "title")
        self.log(f"Configuration : {paths.CONFIG_FILE}")
        self.log(f"Base          : {paths.db_path()}")

        self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll)

        if minimized:
            # Lancee par le demarrage de session : on ne s'impose pas devant
            # l'utilisateur qui vient d'ouvrir son poste.
            self.after(200, lambda: self.hide_to_tray(quiet=True))


    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _apply_theme(self) -> None:
        style = ttk.Style(self)
        for candidate in ("vista", "clam", "default"):
            if candidate in style.theme_names():
                style.theme_use(candidate)
                break
        style.configure("Status.TLabel", foreground="#6b7280")
        style.configure("Action.TButton", padding=(10, 4))

    def _apply_icon(self) -> None:
        """Icone de la fenetre et de la barre des taches."""
        try:
            path = assets.icon_path()
            if path is not None:
                self.iconbitmap(default=str(path))
        except Exception as exc:  # noqa: BLE001 - jamais bloquant
            log.debug("icone de fenetre indisponible : %s", exc)

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Le journal est dans un panneau redimensionnable : selon ce qu'on
        # regarde, on veut plus de place pour les reglages ou pour la sortie.
        splitter = ttk.Panedwindow(self, orient="vertical")
        splitter.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))

        self.notebook = ttk.Notebook(splitter)
        splitter.add(self.notebook, weight=3)

        log_holder = ttk.Labelframe(splitter, text=" Journal ", padding=(6, 4))
        log_holder.columnconfigure(0, weight=1)
        log_holder.rowconfigure(0, weight=1)
        self.log_pane = LogPane(log_holder, height=10)
        self.log_pane.grid(row=0, column=0, sticky="nsew")
        ttk.Button(log_holder, text="Effacer", command=self.log_pane.clear).grid(
            row=1, column=0, sticky="e", pady=(4, 0)
        )
        splitter.add(log_holder, weight=1)

        self.dashboard = DashboardPanel(self.notebook, self)
        self.tracking = TrackingPanel(self.notebook, self)
        self.feeds = FeedsPanel(self.notebook, self)
        self.report_panel = ReportPanel(self.notebook, self)
        self.schedule = SchedulePanel(self.notebook, self)
        self.imports = ImportPanel(self.notebook, self)

        self.notebook.add(self.dashboard, text="  Tableau de bord  ")
        self.notebook.add(self.tracking, text="  Suivi  ")
        self.notebook.add(self.feeds, text="  Flux presse  ")
        self.notebook.add(self.report_panel, text="  Rapport  ")
        self.notebook.add(self.schedule, text="  Planification  ")
        self.notebook.add(self.imports, text="  Import manuel  ")

        # Barre du bas
        bar = ttk.Frame(self, padding=(8, 6))
        bar.grid(row=1, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)

        ttk.Button(
            bar, text="Enregistrer les reglages", style="Action.TButton",
            command=self.save_settings,
        ).grid(row=0, column=0, sticky="w")

        self.status = ttk.Label(bar, text="", style="Status.TLabel")
        self.status.grid(row=0, column=1, sticky="w", padx=12)

        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=160)
        self.progress.grid(row=0, column=2, sticky="e")

    @property
    def settings_panels(self) -> tuple:
        """Onglets qui savent lire et ecrire la configuration."""
        return (self.tracking, self.feeds, self.report_panel, self.schedule)

    def _load_panels(self) -> None:
        for panel in self.settings_panels:
            panel.load(self.config_data.raw)

    # ------------------------------------------------------------------ #
    # Zone de notification
    # ------------------------------------------------------------------ #
    def _setup_tray(self) -> None:
        if not self.config_data.interface.get("tray", True):
            self.log("Icone de notification desactivee dans les reglages.")
            return
        self._tray_ready = self.tray.start()
        if self._tray_ready:
            self.log("Icone de notification active (pres de l'horloge).", "ok")
        else:
            self.log(
                "Icone de notification indisponible : la fenetre se comporte "
                "comme une fenetre ordinaire.",
                "warn",
            )

    def hide_to_tray(self, quiet: bool = False) -> None:
        """Replie la fenetre. Sans icone de notification, reduit simplement."""
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

    def _maybe_hide_after_first_job(self) -> None:
        """Repli automatique apres le premier traitement de la session."""
        if self._first_job_done:
            return
        self._first_job_done = True
        if not self.config_data.interface.get("minimize_after_first_use", True):
            return
        if not self._tray_ready:
            return
        self.hide_to_tray()

    # ------------------------------------------------------------------ #
    # Journal et etat
    # ------------------------------------------------------------------ #
    def log(self, text: str, level: str = "info") -> None:
        self.log_pane.append(text, level)

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    # ------------------------------------------------------------------ #
    # Enregistrement
    # ------------------------------------------------------------------ #
    def save_settings(self, quiet: bool = False) -> bool:
        """Recopie les onglets dans les fichiers. Renvoie False en cas d'echec."""
        raw = self.config_data.raw
        try:
            for panel in self.settings_panels:
                panel.apply(raw)
        except Exception as exc:
            messagebox.showerror("Reglages invalides", str(exc))
            self.log(f"Reglages non enregistres : {exc}", "error")
            return False

        try:
            config_path = config_module.save(raw)
        except Exception as exc:
            messagebox.showerror("Enregistrement impossible", str(exc))
            self.log(f"Ecriture refusee : {exc}", "error")
            return False

        # On relit depuis le disque : ce qui tourne ensuite utilise exactement
        # ce qui a ete ecrit, pas l'etat en memoire.
        self.config_data = config_module.load()
        self._load_panels()

        if not quiet:
            self.log(f"Reglages enregistres dans {config_path.name}.", "ok")
        self.set_status("Reglages enregistres.")
        return True

    def _prepare(self) -> bool:
        if self.runner.busy:
            messagebox.showinfo(
                "Traitement en cours",
                f"« {self.runner.current} » est en cours. Les collecteurs "
                "ecrivent dans la meme base : un seul traitement a la fois.",
            )
            return False
        return self.save_settings(quiet=True)

    # ------------------------------------------------------------------ #
    # Traitements
    # ------------------------------------------------------------------ #
    def _start(self, name: str, work) -> None:
        if not self.runner.submit(name, work):
            return
        self.log("")
        self.log(f"--- {name} ---", "title")
        self.set_status(f"{name}...")
        self.progress.start(12)

    def run_collect(self) -> None:
        if not self._prepare():
            return
        config = self.config_data

        def work() -> str:
            run = pipeline.collect(config)
            for outcome in run.outcomes:
                level = {"erreur": "error", "vide": "warn"}.get(outcome.status, "info")
                self.runner.log(
                    f"[{outcome.status:^9}] {outcome.rows:>5} lignes  {outcome.label}",
                    level,
                )
                if outcome.error:
                    self.runner.log(f"            {outcome.error}", "error")
                for note in outcome.notes[:6]:
                    self.runner.log(f"            - {note}", "debug")
            if run.derived_rows:
                self.runner.log(
                    f"[   ok    ] {run.derived_rows:>5} lignes  Box-office monde (agregat)"
                )
            failed = len(run.failed)
            return (
                f"{run.total_rows} observation(s), {run.total_news} article(s)"
                + (f", {failed} source(s) en erreur" if failed else "")
            )

        self._start("Collecte", work)

    def run_report(self) -> None:
        """Genere le rapport depuis la base puis l'ouvre dans le navigateur."""
        if not self._prepare():
            return
        config = self.config_data

        def work() -> str:
            report, html, text = report_module.build(config)
            html_path, text_path = report_module.save(html, text)
            self.runner.log(f"Rapport HTML  : {html_path}")
            self.runner.log(f"Rapport texte : {text_path}")
            if not report.has_data:
                self.runner.log(
                    "Aucune donnee en base : lancez d'abord une collecte.", "warn"
                )
            if not viewer.open_report(html_path):
                return f"Rapport genere ({html_path.name}), mais le navigateur n'a pas pu l'ouvrir."
            return f"Rapport genere et ouvert ({html_path.name})."

        self._start("Rapport", work)

    def run_import(self) -> None:
        if not self._prepare():
            return
        config = self.config_data

        def work() -> str:
            run = pipeline.collect(config, only=["manual_import"])
            for outcome in run.outcomes:
                for note in outcome.notes:
                    self.runner.log(f"  - {note}")
            return f"{run.total_rows} ligne(s) importee(s)."

        self._start("Import manuel", work)

    def test_feeds(self, feeds: list[dict]) -> None:
        """Interroge les flux et montre ce qui serait retenu, sans rien stocker."""
        if self.runner.busy:
            messagebox.showinfo("Traitement en cours", "Attendez la fin du traitement.")
            return
        config = self.config_data
        delay = config.http_delay

        def work() -> str:
            from ..collectors.editorial import matches, parse_feed
            from ..http import FetchError

            client = Client(delay=delay)
            kept_total = 0
            for feed in feeds:
                name = feed.get("name") or feed.get("url")
                try:
                    items = parse_feed(client.get_text(feed["url"]))
                except FetchError as exc:
                    self.runner.log(f"{name} : {exc}", "error")
                    continue
                kept = [i for i in items if matches(i, feed.get("keywords") or [])]
                kept_total += len(kept)
                self.runner.log(
                    f"{name} : {len(kept)} article(s) retenu(s) sur {len(items)}."
                )
                for item in kept[:8]:
                    self.runner.log(f"    - {item['title']}", "debug")
            return f"{kept_total} article(s) retenu(s) au total."

        self._start("Test des flux", work)

    # ------------------------------------------------------------------ #
    # Ouverture du rapport
    # ------------------------------------------------------------------ #
    def open_last_report(self) -> None:
        """Ouvre le dernier rapport, a la demande."""
        report = viewer.latest_report()
        if report is None:
            messagebox.showinfo(
                "Aucun rapport",
                "Aucun rapport genere pour le moment. Utilisez « Generer et ouvrir le rapport ».",
            )
            return
        if viewer.open_report(report):
            self.log(f"Rapport ouvert dans le navigateur : {report.name}")
        else:
            messagebox.showwarning(
                "Ouverture impossible",
                f"Le navigateur n'a pas pu ouvrir le rapport.\n\n{report}",
            )

    # ------------------------------------------------------------------ #
    # Boucles de lecture des files
    # ------------------------------------------------------------------ #
    def _handle_tray_command(self, action: str) -> None:
        if action == "show":
            self.show_window()
        elif action == "collect":
            self.run_collect()
        elif action == "report":
            self.run_report()
        elif action == "open_report":
            self.open_last_report()
        elif action == "quit":
            self._quit_now()

    def _poll(self) -> None:
        for message in self.runner.drain():
            if isinstance(message, LogLine):
                self.log(message.text, message.level)
            elif isinstance(message, JobDone):
                self.progress.stop()
                level = "ok" if message.ok else "error"
                self.log(f"{message.name} : {message.message}", level)
                self.set_status(message.message)
                self.dashboard.refresh()
                self.imports.refresh()
                if message.ok:
                    if self._tray_ready:
                        self.tray.notify(f"{message.name} : {message.message}")
                    self._maybe_hide_after_first_job()
                else:
                    # Une erreur doit etre vue : on remonte la fenetre plutot
                    # que de la replier.
                    self.show_window()
                    messagebox.showerror(message.name, message.message)

        while True:
            try:
                action = self.tray_commands.get_nowait()
            except queue.Empty:
                break
            self._handle_tray_command(action)

        if not self._quitting:
            self.after(POLL_MS, self._poll)

    # ------------------------------------------------------------------ #
    # Fermeture
    # ------------------------------------------------------------------ #
    def _on_close(self) -> None:
        """La croix replie si l'icone existe, quitte sinon."""
        if self._tray_ready and self.config_data.interface.get("close_to_tray", True):
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
        self.destroy()


def launch(minimized: bool = False) -> int:
    """Point d'entree de la commande `infoshebdo ui`."""
    logging.getLogger().setLevel(logging.INFO)
    paths.ensure_dirs()
    db.init()
    App(minimized=minimized).mainloop()
    return 0
