"""Onglets de reglages : tout ce qui finit dans config.yaml.

Chaque onglet expose deux methodes symetriques :

* `load(raw)`  remplit les widgets depuis la configuration ;
* `apply(raw)` recopie les widgets dans la configuration.

L'interface n'est ainsi qu'une vue sur les memes fichiers : ce qui est reglable
ici est reglable a la main, et inversement. La fenetre appelle `apply` sur tous
les onglets avant d'ecrire, jamais un onglet isolement.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..collectors.base import MARKET_LABELS
from .widgets import (
    PADX,
    PADY,
    ScrollableFrame,
    Section,
    checkbox,
    code_row,
    labeled_combo,
    labeled_entry,
    labeled_spin,
)

# Marches proposes. Box Office Mojo expose une zone pour chacun, et Steam
# accepte les codes pays ISO correspondants.
BOX_OFFICE_CODES = {
    code: MARKET_LABELS[code] for code in ("FR", "US", "JP", "GB", "DE", "WW")
}
STEAM_CODES = {
    code: MARKET_LABELS[code] for code in ("WW", "FR", "US", "JP", "GB", "DE")
}


def _split_codes(text: str) -> list[str]:
    return [part.strip().upper() for part in text.replace(";", ",").split(",") if part.strip()]


class MappingEditor(ttk.Frame):
    """Petit tableau cle -> valeur editable (marche -> zone Box Office Mojo)."""

    def __init__(self, master, key_title: str, value_title: str, height: int = 5) -> None:
        super().__init__(master)
        self.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            self, columns=("cle", "valeur"), show="headings", height=height
        )
        self.tree.heading("cle", text=key_title)
        self.tree.heading("valeur", text=value_title)
        self.tree.column("cle", width=110, anchor="w")
        self.tree.column("valeur", width=170, anchor="w")
        self.tree.grid(row=0, column=0, sticky="ew")
        self.tree.bind("<Double-1>", lambda _e: self.edit())

        buttons = ttk.Frame(self)
        buttons.grid(row=0, column=1, sticky="n", padx=(8, 0))
        ttk.Button(buttons, text="Ajouter", command=self.add, width=11).pack(pady=2)
        ttk.Button(buttons, text="Modifier", command=self.edit, width=11).pack(pady=2)
        ttk.Button(buttons, text="Retirer", command=self.remove, width=11).pack(pady=2)

    # -- donnees -------------------------------------------------------- #
    def set_mapping(self, mapping: dict) -> None:
        self.tree.delete(*self.tree.get_children())
        for key, value in (mapping or {}).items():
            self.tree.insert("", "end", values=(key, value if value else ""))

    def mapping(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in self.tree.get_children():
            key, value = self.tree.item(item, "values")
            if key:
                result[str(key)] = str(value)
        return result

    # -- edition -------------------------------------------------------- #
    def _dialog(self, title: str, key: str = "", value: str = "") -> tuple[str, str] | None:
        window = tk.Toplevel(self)
        window.title(title)
        window.transient(self.winfo_toplevel())
        window.resizable(False, False)
        window.grab_set()

        key_var = tk.StringVar(value=key)
        value_var = tk.StringVar(value=value)
        result: dict[str, str] = {}

        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Marche (FR, US, JP...)").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=key_var, width=16).grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(body, text="Zone Box Office Mojo").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=value_var, width=16).grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(
            body,
            text="Laisser la zone vide pour la zone par defaut du site\n"
                 "(Amerique du Nord).",
            foreground="#6b7280",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 8))

        def confirm() -> None:
            if not key_var.get().strip():
                messagebox.showwarning("Marche manquant", "Indiquez un code marche.", parent=window)
                return
            result["key"] = key_var.get().strip().upper()
            result["value"] = value_var.get().strip()
            window.destroy()

        actions = ttk.Frame(body)
        actions.grid(row=3, column=0, columnspan=2, sticky="e")
        ttk.Button(actions, text="Annuler", command=window.destroy).pack(side="right", padx=4)
        ttk.Button(actions, text="Valider", command=confirm).pack(side="right")

        window.wait_window()
        if "key" not in result:
            return None
        return result["key"], result["value"]

    def add(self) -> None:
        entry = self._dialog("Ajouter un marche")
        if entry:
            self.tree.insert("", "end", values=entry)

    def edit(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        key, value = self.tree.item(selected[0], "values")
        entry = self._dialog("Modifier le marche", str(key), str(value))
        if entry:
            self.tree.item(selected[0], values=entry)

    def remove(self) -> None:
        for item in self.tree.selection():
            self.tree.delete(item)


class TrackingPanel(ScrollableFrame):
    """Ce qui est suivi : marches, sources, profondeur de rattrapage."""

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app

        self.top_n = tk.IntVar(value=10)
        self.backfill = tk.IntVar(value=8)
        self.timezone = tk.StringVar(value="Europe/Paris")
        self.box_enabled = tk.BooleanVar(value=True)
        self.allocine = tk.BooleanVar(value=True)
        self.mojo = tk.BooleanVar(value=True)
        self.games_enabled = tk.BooleanVar(value=True)
        self.steam_topsellers = tk.BooleanVar(value=True)
        self.steam_concurrents = tk.BooleanVar(value=True)
        self.steamspy = tk.BooleanVar(value=True)
        self.editorial = tk.BooleanVar(value=True)
        self.manual = tk.BooleanVar(value=True)
        self.extra_steam = tk.StringVar(value="")

        general = Section(self.body, "General")
        general.pack(fill="x", padx=6, pady=6)
        labeled_spin(
            general, "Lignes par classement", self.top_n, 3, 100,
            hint="Nombre de films ou de jeux affiches dans chaque tableau.",
        )
        labeled_spin(
            general, "Semaines de rattrapage", self.backfill, 1, 52,
            hint="Semaines re-interrogees a chaque collecte. Le box-office France "
                 "et Japon est publie en retard puis revise : une valeur basse "
                 "laisse des trous definitifs.",
        )
        labeled_entry(
            general, "Fuseau horaire", self.timezone,
            hint="Sert a determiner quelle est la semaine ecoulee.",
        )

        cinema = Section(self.body, "Box-office cinema")
        cinema.pack(fill="x", padx=6, pady=6)
        checkbox(cinema, "Suivre le box-office", self.box_enabled)
        self.box_markets = code_row(
            cinema, "Marches", BOX_OFFICE_CODES,
            hint="« Monde » est un agregat calcule par InfosHebdo, pas une source : "
                 "aucun classement mondial hebdomadaire n'existe en acces libre.",
        )
        checkbox(
            cinema, "Allocine - entrees France", self.allocine,
            hint="Entrees salles reelles, la meilleure donnee France disponible.",
        )
        checkbox(
            cinema, "Box Office Mojo - recettes USD", self.mojo,
            hint="Exhaustif en Amerique du Nord seulement ; les autres zones sont "
                 "partielles et publiees avec deux a cinq semaines de retard.",
        )
        row = cinema.next_row()
        ttk.Label(cinema, text="Zones Mojo").grid(
            row=row, column=0, sticky="nw", padx=PADX, pady=PADY
        )
        self.mojo_areas = MappingEditor(cinema, "Marche", "Zone Mojo")
        self.mojo_areas.grid(row=row, column=1, sticky="ew", padx=PADX, pady=PADY)

        games = Section(self.body, "Jeu video")
        games.pack(fill="x", padx=6, pady=6)
        checkbox(games, "Suivre le jeu video", self.games_enabled)
        self.steam_markets = code_row(games, "Marches Steam", STEAM_CODES)
        labeled_entry(
            games, "Autres pays Steam", self.extra_steam,
            hint="Codes ISO supplementaires, separes par des virgules (IT, ES, KR...).",
            width=24,
        )
        checkbox(
            games, "Classement officiel des ventes par pays", self.steam_topsellers,
            hint="Rang et rang de la semaine precedente. Valve ne publie jamais "
                 "d'unites ni de chiffre d'affaires.",
        )
        checkbox(
            games, "Joueurs simultanes (mondial)", self.steam_concurrents,
            hint="Instantane pris a chaque collecte : mesure l'activite, pas les ventes.",
        )
        checkbox(
            games, "SteamSpy - proprietaires estimes", self.steamspy,
            hint="Estimation tierce, marge d'erreur large. Seul indicateur de volume "
                 "disponible sur Steam.",
        )

        platforms = Section(self.body, "Ventes toutes plateformes")
        platforms.pack(fill="x", padx=6, pady=6)
        platforms.add_hint(
            "Aucune source libre ne publie d'unites hebdomadaires toutes "
            "plateformes : les panels de reference sont payants (GSD en Europe, "
            "Circana aux Etats-Unis, Famitsu au Japon) et les classements "
            "hebdomadaires de VGChartz s'arretent en decembre 2018."
        )
        checkbox(
            platforms, "Veille presse des publications de classements", self.editorial,
            hint="Repere les articles qui relaient ces chiffres et en donne les liens.",
        )
        checkbox(
            platforms, "Import manuel de fichiers CSV", self.manual,
            hint="Voir l'onglet « Import manuel ».",
        )

    # ------------------------------------------------------------------ #
    def load(self, raw: dict) -> None:
        box = raw.get("box_office", {})
        games = raw.get("games", {})
        platforms = games.get("all_platforms", {}) or {}

        self.top_n.set(int(raw.get("top_n", 10)))
        self.backfill.set(int(raw.get("backfill_weeks", 8)))
        self.timezone.set(str(raw.get("timezone", "Europe/Paris")))

        self.box_enabled.set(bool(box.get("enabled", True)))
        self.box_markets.set_selection(box.get("markets", []))
        self.allocine.set(bool(box.get("sources", {}).get("allocine_france", True)))
        self.mojo.set(bool(box.get("sources", {}).get("boxofficemojo", True)))
        self.mojo_areas.set_mapping(box.get("mojo_areas", {}))

        self.games_enabled.set(bool(games.get("enabled", True)))
        steam_markets = list(games.get("steam_markets", []))
        self.steam_markets.set_selection(steam_markets)
        self.extra_steam.set(
            ", ".join(code for code in steam_markets if code not in STEAM_CODES)
        )
        sources = games.get("sources", {})
        self.steam_topsellers.set(bool(sources.get("steam_topsellers", True)))
        self.steam_concurrents.set(bool(sources.get("steam_concurrents", True)))
        self.steamspy.set(bool(sources.get("steamspy", True)))
        self.editorial.set(bool(platforms.get("editorial_watch", True)))
        self.manual.set(bool(platforms.get("manual_import", True)))

    def apply(self, raw: dict) -> None:
        raw["top_n"] = int(self.top_n.get())
        raw["backfill_weeks"] = int(self.backfill.get())
        raw["timezone"] = self.timezone.get().strip() or "Europe/Paris"

        box = raw.setdefault("box_office", {})
        box["enabled"] = self.box_enabled.get()
        box["markets"] = self.box_markets.selection()
        box.setdefault("sources", {})
        box["sources"]["allocine_france"] = self.allocine.get()
        box["sources"]["boxofficemojo"] = self.mojo.get()
        box["mojo_areas"] = self.mojo_areas.mapping()

        games = raw.setdefault("games", {})
        games["enabled"] = self.games_enabled.get()

        # Les codes libres viennent apres les marches coches, sans doublon.
        markets = self.steam_markets.selection()
        for code in _split_codes(self.extra_steam.get()):
            if code not in markets:
                markets.append(code)
        games["steam_markets"] = markets

        games.setdefault("sources", {})
        games["sources"]["steam_topsellers"] = self.steam_topsellers.get()
        games["sources"]["steam_concurrents"] = self.steam_concurrents.get()
        games["sources"]["steamspy"] = self.steamspy.get()

        platforms = games.setdefault("all_platforms", {})
        platforms["editorial_watch"] = self.editorial.get()
        platforms["manual_import"] = self.manual.get()


class FeedsPanel(ScrollableFrame):
    """Flux RSS surveilles pour les classements toutes plateformes."""

    COLUMNS = (("name", "Nom", 150), ("url", "Adresse", 330),
               ("markets", "Marches", 110), ("keywords", "Mots-cles", 240))

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app

        section = Section(self.body, "Flux surveilles")
        section.pack(fill="both", expand=True, padx=6, pady=6)
        section.add_hint(
            "Un article est retenu si l'un des mots-cles apparait dans son titre "
            "ou son resume. Des mots-cles trop larges (« sales » seul) ramenent "
            "les communiques financiers des editeurs, sans rapport avec un "
            "classement."
        )

        row = section.next_row()
        holder = ttk.Frame(section)
        holder.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=PADX, pady=PADY)
        holder.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            holder, columns=[c[0] for c in self.COLUMNS], show="headings", height=7
        )
        for key, title, width in self.COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="ew")
        self.tree.bind("<Double-1>", lambda _e: self.edit())

        buttons = ttk.Frame(holder)
        buttons.grid(row=0, column=1, sticky="n", padx=(8, 0))
        ttk.Button(buttons, text="Ajouter", command=self.add, width=11).pack(pady=2)
        ttk.Button(buttons, text="Modifier", command=self.edit, width=11).pack(pady=2)
        ttk.Button(buttons, text="Retirer", command=self.remove, width=11).pack(pady=2)
        ttk.Button(
            buttons, text="Tester", command=self._test, width=11
        ).pack(pady=(12, 2))

    # -- edition -------------------------------------------------------- #
    def _dialog(self, title: str, feed: dict | None = None) -> dict | None:
        feed = feed or {}
        window = tk.Toplevel(self)
        window.title(title)
        window.transient(self.winfo_toplevel())
        window.resizable(False, False)
        window.grab_set()

        name = tk.StringVar(value=feed.get("name", ""))
        url = tk.StringVar(value=feed.get("url", ""))
        markets = tk.StringVar(value=", ".join(feed.get("markets", []) or []))
        keywords = tk.StringVar(value=", ".join(feed.get("keywords", []) or []))
        result: dict = {}

        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        fields = (
            ("Nom", name, 46),
            ("Adresse du flux", url, 46),
            ("Marches concernes", markets, 46),
            ("Mots-cles", keywords, 46),
        )
        for index, (label, variable, width) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=variable, width=width).grid(
                row=index, column=1, sticky="ew", pady=4, padx=(8, 0)
            )
        ttk.Label(
            body,
            text="Marches et mots-cles : listes separees par des virgules.\n"
                 "Sans mot-cle, tous les articles du flux sont retenus.",
            foreground="#6b7280",
        ).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(4, 10))

        def confirm() -> None:
            if not name.get().strip() or not url.get().strip():
                messagebox.showwarning(
                    "Champs manquants", "Le nom et l'adresse sont obligatoires.",
                    parent=window,
                )
                return
            result.update(
                {
                    "name": name.get().strip(),
                    "url": url.get().strip(),
                    "markets": _split_codes(markets.get()),
                    "keywords": [
                        k.strip() for k in keywords.get().split(",") if k.strip()
                    ],
                }
            )
            window.destroy()

        actions = ttk.Frame(body)
        actions.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e")
        ttk.Button(actions, text="Annuler", command=window.destroy).pack(side="right", padx=4)
        ttk.Button(actions, text="Valider", command=confirm).pack(side="right")

        window.wait_window()
        return result or None

    def _insert(self, feed: dict, item: str | None = None) -> None:
        values = (
            feed["name"],
            feed["url"],
            ", ".join(feed.get("markets") or []),
            ", ".join(feed.get("keywords") or []),
        )
        if item:
            self.tree.item(item, values=values)
        else:
            self.tree.insert("", "end", values=values)

    def add(self) -> None:
        feed = self._dialog("Ajouter un flux")
        if feed:
            self._insert(feed)

    def edit(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        current = self._read_item(selected[0])
        feed = self._dialog("Modifier le flux", current)
        if feed:
            self._insert(feed, selected[0])

    def remove(self) -> None:
        for item in self.tree.selection():
            self.tree.delete(item)

    def _read_item(self, item: str) -> dict:
        name, url, markets, keywords = self.tree.item(item, "values")
        return {
            "name": str(name),
            "url": str(url),
            "markets": _split_codes(str(markets)),
            "keywords": [k.strip() for k in str(keywords).split(",") if k.strip()],
        }

    def _test(self) -> None:
        """Interroge les flux et affiche ce qui serait retenu, sans rien stocker."""
        feeds = [self._read_item(item) for item in self.tree.get_children()]
        if not feeds:
            messagebox.showinfo("Aucun flux", "Ajoutez d'abord un flux a tester.")
            return
        self.app.test_feeds(feeds)

    # ------------------------------------------------------------------ #
    def load(self, raw: dict) -> None:
        self.tree.delete(*self.tree.get_children())
        platforms = raw.get("games", {}).get("all_platforms", {}) or {}
        for feed in platforms.get("feeds") or []:
            self._insert(
                {
                    "name": feed.get("name", ""),
                    "url": feed.get("url", ""),
                    "markets": feed.get("markets") or [],
                    "keywords": feed.get("keywords") or [],
                }
            )

    def apply(self, raw: dict) -> None:
        platforms = raw.setdefault("games", {}).setdefault("all_platforms", {})
        platforms["feeds"] = [
            self._read_item(item) for item in self.tree.get_children()
        ]


class ReportPanel(ScrollableFrame):
    """Contenu du rapport et ouverture automatique dans le navigateur."""

    EMPTY_MODES = ("show", "hide")

    def __init__(self, master, app) -> None:
        super().__init__(master)
        self.app = app

        self.title = tk.StringVar()
        self.empty_sections = tk.StringVar(value="show")
        self.auto_open = tk.BooleanVar(value=True)

        report = Section(self.body, "Rapport")
        report.pack(fill="x", padx=6, pady=6)
        labeled_entry(report, "Titre", self.title)
        labeled_combo(
            report, "Sections vides", self.empty_sections, self.EMPTY_MODES,
            hint="« show » affiche les sections vides avec la raison : on voit "
                 "immediatement qu'une source est tombee.",
        )

        browser = Section(self.body, "Ouverture dans le navigateur")
        browser.pack(fill="x", padx=6, pady=6)
        browser.add_hint(
            "Le rapport HTML est ecrit dans reports/ a chaque generation."
        )
        checkbox(
            browser, "Ouvrir automatiquement le rapport chaque semaine", self.auto_open,
            hint="La tache planifiee l'ouvre une seule fois par semaine, le "
                 "premier jour ou le poste est allume : lundi si possible, "
                 "sinon mardi, etc. Decochee, la tache collecte quand meme.",
        )

    # ------------------------------------------------------------------ #
    def load(self, raw: dict) -> None:
        report = raw.get("report", {})
        self.title.set(str(report.get("title", "")))
        self.empty_sections.set(str(report.get("empty_sections", "show")))
        self.auto_open.set(bool(report.get("auto_open", True)))

    def apply(self, raw: dict) -> None:
        report = raw.setdefault("report", {})
        report["title"] = self.title.get().strip() or "Veille hebdo"
        report["empty_sections"] = self.empty_sections.get()
        report["auto_open"] = self.auto_open.get()
