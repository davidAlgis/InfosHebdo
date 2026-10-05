"""Onglets d'action : etat de la base, planification, import manuel.

Ces onglets ne modifient pas config.yaml. Ils declenchent des traitements
(collecte, rapport, planification) et affichent l'etat du systeme.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import db, paths, scheduler, startup
from .widgets import PADX, PADY, ScrollableFrame, Section, checkbox

STATUS_TEXT = {
    "ok": "ok",
    "vide": "vide",
    "desactive": "desactive",
    "erreur": "ERREUR",
}


def open_in_explorer(path) -> None:
    """Ouvre un fichier ou un dossier avec l'application par defaut."""
    target = str(path)
    if os.name == "nt":
        os.startfile(target)  # noqa: S606 - chemin construit par le programme
    elif sys.platform == "darwin":
        subprocess.run(["open", target], check=False)
    else:
        subprocess.run(["xdg-open", target], check=False)


class DashboardPanel(ttk.Frame):
    """Etat de la base, journal de la derniere collecte, actions immediates."""

    def __init__(self, master, app) -> None:
        super().__init__(master, padding=(8, 8))
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # -- actions ---------------------------------------------------- #
        actions = ttk.LabelFrame(self, text=" Actions ", padding=(10, 8))
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        buttons = (
            ("Collecter maintenant", app.run_collect),
            ("Generer et ouvrir le rapport", app.run_report),
            ("Ouvrir le dernier rapport", app.open_last_report),
            ("Actualiser", self.refresh),
        )
        for index, (label, command) in enumerate(buttons):
            ttk.Button(actions, text=label, command=command, width=24).grid(
                row=index // 3, column=index % 3, padx=4, pady=3, sticky="w"
            )

        # -- chiffres --------------------------------------------------- #
        stats = ttk.LabelFrame(self, text=" Base de donnees ", padding=(10, 8))
        stats.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        stats.columnconfigure(1, weight=1)

        self.stat_labels: dict[str, ttk.Label] = {}
        rows = (
            ("observations", "Observations"),
            ("news", "Articles reperes"),
            ("periode", "Periode couverte"),
            ("detail", "Par domaine"),
            ("fichier", "Fichier"),
        )
        for index, (key, label) in enumerate(rows):
            ttk.Label(stats, text=label).grid(
                row=index, column=0, sticky="w", padx=PADX, pady=2
            )
            value = ttk.Label(stats, text="-", foreground="#1c1c1e")
            value.grid(row=index, column=1, sticky="w", padx=PADX, pady=2)
            self.stat_labels[key] = value

        # -- derniere collecte ------------------------------------------ #
        last = ttk.LabelFrame(self, text=" Derniere collecte ", padding=(10, 8))
        last.grid(row=2, column=0, sticky="nsew")
        last.columnconfigure(0, weight=1)
        last.rowconfigure(0, weight=1)

        columns = (("statut", "Statut", 90), ("source", "Source", 380),
                   ("lignes", "Lignes", 70), ("message", "Detail", 320))
        self.tree = ttk.Treeview(
            last, columns=[c[0] for c in columns], show="headings", height=8
        )
        for key, title, width in columns:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.tag_configure("erreur", foreground="#c0392b")
        self.tree.tag_configure("vide", foreground="#6b7280")
        self.tree.tag_configure("desactive", foreground="#9ca3af")

        scroll = ttk.Scrollbar(last, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Relit la base. Sans effet pendant un traitement, qui la verrouille."""
        try:
            with db.session() as conn:
                info = db.stats(conn)
                log_rows = db.last_run_log(conn, "collect")
        except Exception as exc:
            self.app.log(f"Lecture de la base impossible : {exc}", "error")
            return

        self.stat_labels["observations"].configure(text=f"{info['observations']:,}".replace(",", " "))
        self.stat_labels["news"].configure(text=str(info["news"]))
        self.stat_labels["periode"].configure(
            text=(
                f"{info['first_period']} -> {info['last_period']}"
                if info["first_period"]
                else "aucune donnee"
            )
        )
        self.stat_labels["detail"].configure(
            text=", ".join(f"{k} : {v}" for k, v in info["per_domain"].items()) or "-"
        )
        self.stat_labels["fichier"].configure(text=str(paths.db_path()))

        self.tree.delete(*self.tree.get_children())
        for row in log_rows:
            status = row["status"]
            self.tree.insert(
                "",
                "end",
                values=(
                    STATUS_TEXT.get(status, status),
                    row["label"] or row["collector"],
                    row["rows"],
                    (row["message"] or "")[:220],
                ),
                tags=(status,),
            )
        if not log_rows:
            self.tree.insert(
                "", "end", values=("-", "Aucune collecte enregistree", 0, ""), tags=("vide",)
            )


class SchedulePanel(ScrollableFrame):
    """Taches planifiees, lancement au demarrage, comportement de la fenetre."""

    DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app

        self.run_time = tk.StringVar(value="09:00")
        self.launch_at_startup = tk.BooleanVar(value=False)
        self.tray = tk.BooleanVar(value=True)
        self.minimize_after_first_use = tk.BooleanVar(value=True)
        self.close_to_tray = tk.BooleanVar(value=True)

        section = Section(self.body, "Planification automatique")
        section.pack(fill="x", padx=6, pady=6)
        section.add_hint(
            "Une tache est inscrite dans le Planificateur de taches Windows. "
            "Elle se declenche chaque jour a l'heure choisie, et a chaque "
            "ouverture de session. Elle collecte les donnees du jour (une "
            "fois par jour : les joueurs simultanes Steam sont un instantane "
            "et le box-office international est revise apres coup), puis "
            "ouvre le rapport dans le navigateur s'il n'a pas encore ete "
            "ouvert cette semaine. Poste allume le lundi : lundi. Sinon : "
            "mardi, et ainsi de suite."
        )

        row = section.next_row()
        ttk.Label(section, text="Declenchement quotidien a").grid(
            row=row, column=0, sticky="w", padx=PADX, pady=PADY
        )
        ttk.Entry(section, textvariable=self.run_time, width=8).grid(
            row=row, column=1, sticky="w", padx=PADX, pady=PADY
        )

        actions = ttk.Frame(section)
        actions.grid(
            row=section.next_row(), column=0, columnspan=2,
            sticky="w", padx=PADX, pady=(12, 4),
        )
        ttk.Button(actions, text="Installer / mettre a jour", command=self.install).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(actions, text="Supprimer", command=self.uninstall).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(actions, text="Actualiser l'etat", command=self.refresh).pack(side="left")

        # -- demarrage de session ---------------------------------------- #
        boot = Section(self.body, "Lancement au demarrage de Windows")
        boot.pack(fill="x", padx=6, pady=6)
        boot.add_hint(
            "L'interface est inscrite dans la cle « Run » de votre compte "
            "utilisateur : aucune elevation de privileges, et l'entree reste "
            "visible et desactivable dans le Gestionnaire des taches, onglet "
            "« Demarrage »."
        )
        checkbox(
            boot, "Lancer InfosHebdo a l'ouverture de session",
            self.launch_at_startup,
            hint="Elle demarre directement dans la zone de notification, sans "
                 "s'ouvrir devant vous.",
        )
        boot_actions = ttk.Frame(boot)
        boot_actions.grid(
            row=boot.next_row(), column=0, columnspan=2, sticky="w",
            padx=PADX, pady=(6, 2),
        )
        ttk.Button(
            boot_actions, text="Tester le lancement", command=self.test_startup
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            boot_actions, text="Ouvrir le dossier de l'outil",
            command=lambda: open_in_explorer(paths.PROJECT_DIR),
        ).pack(side="left")

        # -- comportement de la fenetre ---------------------------------- #
        window = Section(self.body, "Comportement de la fenetre")
        window.pack(fill="x", padx=6, pady=6)
        checkbox(
            window, "Icone dans la zone de notification", self.tray,
            hint="Pres de l'horloge. Son menu donne acces a la collecte, au "
                 "rapport et a la sortie.",
        )
        checkbox(
            window, "Se replier apres le premier traitement", self.minimize_after_first_use,
            hint="Une fois la premiere collecte ou le premier rapport termine, "
                 "la fenetre disparait dans la zone de notification.",
        )
        checkbox(
            window, "La croix de fermeture replie au lieu de quitter",
            self.close_to_tray,
            hint="« Quitter » reste disponible dans le menu de l'icone. Sans "
                 "icone de notification, la croix quitte normalement.",
        )

        state = Section(self.body, "Etat")
        state.pack(fill="both", expand=True, padx=6, pady=6)
        self.state_text = tk.Text(
            state, height=15, wrap="word", state="disabled",
            font=("Consolas", 9), background="#fbfbfc", relief="solid", borderwidth=1,
        )
        self.state_text.grid(
            row=state.next_row(), column=0, columnspan=2, sticky="nsew", padx=PADX, pady=PADY
        )
        state.rowconfigure(0, weight=1)

    # ------------------------------------------------------------------ #
    def _write(self, lines: list[str]) -> None:
        self.state_text.configure(state="normal")
        self.state_text.delete("1.0", "end")
        self.state_text.insert("end", "\n".join(lines))
        self.state_text.configure(state="disabled")

    def refresh(self) -> None:
        lines: list[str] = []
        if not scheduler.is_supported():
            lines.append(
                "Planification integree indisponible hors Windows.\n"
                "Sous Linux ou macOS, appeler depuis cron :\n"
                f"  0 9 * * *  {sys.executable} {paths.LAUNCHER} auto"
            )
            self._write(lines)
            return

        lines.append("Ce qui est inscrit dans la tache :")
        lines.extend(f"  {line}" for line in scheduler.describe())
        lines.append("")
        lines.append("Etat actuel :")
        for state in scheduler.status():
            lines.append(f"  {state.name} : {state.summary}")
            if state.exists and state.last_result:
                lines.append(f"      dernier resultat : {state.last_result}")

        lines.append("")
        lines.append("Lancement au demarrage :")
        lines.extend(f"  {line}" for line in startup.describe())
        self._write(lines)

    def test_startup(self) -> None:
        """Relance l'interface exactement comme le fera Windows."""
        try:
            startup.launch_detached(["ui", "--minimized"])
        except OSError as exc:
            messagebox.showerror("Lancement impossible", str(exc))
            return
        self.app.log(
            "Seconde instance lancee comme au demarrage : elle doit apparaitre "
            "dans la zone de notification, sans ouvrir de fenetre.",
            "ok",
        )
        messagebox.showinfo(
            "Test lance",
            "Une seconde instance a ete lancee comme le fera Windows au "
            "demarrage.\n\nElle doit apparaitre pres de l'horloge sans ouvrir "
            "de fenetre. Pensez a la quitter par le menu de son icone.",
        )

    def install(self) -> None:
        try:
            messages = scheduler.install(time=self.run_time.get())
        except scheduler.SchedulerError as exc:
            messagebox.showerror("Planification impossible", str(exc))
            self.app.log(f"Planification refusee : {exc}", "error")
            return
        for message in messages:
            self.app.log(message, "ok")
        self.refresh()

    def uninstall(self) -> None:
        if not messagebox.askyesno(
            "Supprimer la planification",
            "Retirer la tache du Planificateur ? La collecte et l'ouverture "
            "automatique du rapport s'arreteront ; la base et les rapports "
            "deja produits sont conserves.",
        ):
            return
        try:
            messages = scheduler.uninstall()
        except scheduler.SchedulerError as exc:
            messagebox.showerror("Suppression impossible", str(exc))
            return
        for message in messages:
            self.app.log(message)
        self.refresh()

    # ------------------------------------------------------------------ #
    def load(self, raw: dict) -> None:
        interface = raw.get("interface", {})
        self.tray.set(bool(interface.get("tray", True)))
        self.minimize_after_first_use.set(
            bool(interface.get("minimize_after_first_use", True))
        )
        self.close_to_tray.set(bool(interface.get("close_to_tray", True)))

        # L'etat du demarrage vit dans le registre, pas dans config.yaml : le
        # fichier de configuration ne doit pas pretendre decrire ce qu'il ne
        # controle pas.
        try:
            self.launch_at_startup.set(startup.status().enabled)
        except startup.StartupError:
            self.launch_at_startup.set(False)

    def apply(self, raw: dict) -> None:
        interface = raw.setdefault("interface", {})
        interface["tray"] = self.tray.get()
        interface["minimize_after_first_use"] = self.minimize_after_first_use.get()
        interface["close_to_tray"] = self.close_to_tray.get()

        if not startup.is_supported():
            return
        wanted = self.launch_at_startup.get()
        try:
            state = startup.status()
            if wanted and (not state.enabled or not state.matches_current):
                startup.enable()
                self.app.log("Lancement au demarrage active.", "ok")
            elif not wanted and state.enabled:
                startup.disable()
                self.app.log("Lancement au demarrage desactive.")
        except startup.StartupError as exc:
            # Un registre inaccessible ne doit pas empecher d'enregistrer le
            # reste des reglages.
            self.app.log(f"Demarrage automatique : {exc}", "error")


class ImportPanel(ScrollableFrame):
    """Depot des chiffres qu'aucune source libre ne publie."""

    MODEL = (
        "market,period_start,period_end,title,platform,metric,value,rank,reliability,source,note\n"
        "FR,2026-08-10,2026-08-16,EA SPORTS FC 26,PS5,units,18500,1,officiel,GSD,Classement GSD France\n"
        "JP,2026-08-10,2026-08-16,Pokemon Nouveau,Switch 2,units,95000,1,officiel,Famitsu,\n"
    )

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app

        section = Section(self.body, "Ventes toutes plateformes")
        section.pack(fill="x", padx=6, pady=6)
        section.add_hint(
            "Les panels de reference sont payants : GSD en Europe, Circana aux "
            "Etats-Unis, Famitsu au Japon. Si vous avez acces a ces chiffres, "
            "deposez-les ici en CSV : ils seront intégrés a la prochaine collecte "
            "puis deplaces dans data/import/traites/."
        )
        section.add_hint(
            "Colonnes obligatoires : market, period_start, title, metric, value. "
            "Optionnelles : period_end, platform, rank, prev_rank, distributor, "
            "currency, reliability, period_type, source, note."
        )

        actions = ttk.Frame(section)
        actions.grid(
            row=section.next_row(), column=0, columnspan=2, sticky="w",
            padx=PADX, pady=(4, 8),
        )
        ttk.Button(actions, text="Ajouter un fichier CSV", command=self.add_file).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(actions, text="Ouvrir le dossier", command=self.open_folder).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(actions, text="Ecrire un modele", command=self.write_model).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(actions, text="Importer maintenant", command=self.app.run_import).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(actions, text="Actualiser", command=self.refresh).pack(side="left")

        pending = Section(self.body, "Fichiers en attente")
        pending.pack(fill="both", expand=True, padx=6, pady=6)
        self.listbox = tk.Listbox(pending, height=8, font=("Consolas", 9))
        self.listbox.grid(
            row=pending.next_row(), column=0, columnspan=2, sticky="nsew",
            padx=PADX, pady=PADY,
        )
        pending.rowconfigure(0, weight=1)

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        paths.ensure_dirs()
        self.listbox.delete(0, "end")
        files = sorted(paths.IMPORT_DIR.glob("*.csv"))
        if not files:
            self.listbox.insert("end", f"(aucun fichier dans {paths.IMPORT_DIR})")
            return
        for path in files:
            self.listbox.insert("end", f"{path.name}  ({path.stat().st_size} octets)")

    def add_file(self) -> None:
        selected = filedialog.askopenfilenames(
            title="Choisir un ou plusieurs fichiers CSV",
            filetypes=[("Fichiers CSV", "*.csv"), ("Tous les fichiers", "*.*")],
        )
        if not selected:
            return
        paths.ensure_dirs()
        import shutil

        for source in selected:
            target = paths.IMPORT_DIR / os.path.basename(source)
            try:
                shutil.copy2(source, target)
                self.app.log(f"Copie dans data/import : {target.name}", "ok")
            except OSError as exc:
                self.app.log(f"Copie impossible ({source}) : {exc}", "error")
        self.refresh()

    def open_folder(self) -> None:
        paths.ensure_dirs()
        open_in_explorer(paths.IMPORT_DIR)

    def write_model(self) -> None:
        paths.ensure_dirs()
        target = paths.IMPORT_DIR / "modele.csv.exemple"
        target.write_text(self.MODEL, encoding="utf-8")
        self.app.log(f"Modele ecrit : {target}", "ok")
        open_in_explorer(target)

    def load(self, raw: dict) -> None:
        self.refresh()
