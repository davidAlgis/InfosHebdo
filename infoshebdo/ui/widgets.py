"""Briques d'interface reutilisees par les onglets.

Rien de decoratif ici : ce sont les quelques assemblages Tk qui reviennent
partout (champ etiquete, groupe de cases a cocher pour une liste de marches,
zone de journal coloree), regroupes pour que les onglets restent lisibles.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Iterable

PADX = 8
PADY = 4

# Couleurs du journal. Volontairement sobres : le texte doit rester lisible
# sur le fond clair du theme Windows.
LOG_COLORS = {
    "debug": "#6b7280",
    "info": "#1c1c1e",
    "warn": "#b45309",
    "error": "#c0392b",
    "title": "#0b5394",
    "ok": "#0b8457",
}


class Section(ttk.LabelFrame):
    """Cadre titre, avec une grille interne a deux colonnes."""

    def __init__(self, master, title: str, **kwargs) -> None:
        super().__init__(master, text=f" {title} ", padding=(10, 8), **kwargs)
        self.columnconfigure(1, weight=1)
        self._row = 0

    def next_row(self) -> int:
        row = self._row
        self._row += 1
        return row

    def add_hint(self, text: str) -> ttk.Label:
        """Ligne d'explication sur toute la largeur."""
        label = ttk.Label(self, text=text, wraplength=620, foreground="#6b7280")
        label.grid(
            row=self.next_row(), column=0, columnspan=2,
            sticky="w", padx=PADX, pady=(0, 6),
        )
        return label


def labeled_entry(
    section: Section,
    label: str,
    variable: tk.Variable,
    hint: str = "",
    secret: bool = False,
    width: int = 38,
) -> ttk.Entry:
    row = section.next_row()
    ttk.Label(section, text=label).grid(row=row, column=0, sticky="w", padx=PADX, pady=PADY)
    entry = ttk.Entry(
        section, textvariable=variable, width=width, show="•" if secret else ""
    )
    entry.grid(row=row, column=1, sticky="ew", padx=PADX, pady=PADY)
    if hint:
        ttk.Label(section, text=hint, foreground="#6b7280").grid(
            row=section.next_row(), column=1, sticky="w", padx=PADX, pady=(0, 6)
        )
    return entry


def labeled_spin(
    section: Section,
    label: str,
    variable: tk.Variable,
    from_: int,
    to: int,
    hint: str = "",
) -> ttk.Spinbox:
    row = section.next_row()
    ttk.Label(section, text=label).grid(row=row, column=0, sticky="w", padx=PADX, pady=PADY)
    spin = ttk.Spinbox(section, from_=from_, to=to, textvariable=variable, width=8)
    spin.grid(row=row, column=1, sticky="w", padx=PADX, pady=PADY)
    if hint:
        ttk.Label(section, text=hint, foreground="#6b7280").grid(
            row=section.next_row(), column=1, sticky="w", padx=PADX, pady=(0, 6)
        )
    return spin


def labeled_combo(
    section: Section,
    label: str,
    variable: tk.Variable,
    values: Iterable[str],
    hint: str = "",
) -> ttk.Combobox:
    row = section.next_row()
    ttk.Label(section, text=label).grid(row=row, column=0, sticky="w", padx=PADX, pady=PADY)
    combo = ttk.Combobox(
        section, textvariable=variable, values=list(values), state="readonly", width=20
    )
    combo.grid(row=row, column=1, sticky="w", padx=PADX, pady=PADY)
    if hint:
        ttk.Label(section, text=hint, foreground="#6b7280").grid(
            row=section.next_row(), column=1, sticky="w", padx=PADX, pady=(0, 6)
        )
    return combo


def checkbox(section: Section, label: str, variable: tk.BooleanVar, hint: str = "") -> ttk.Checkbutton:
    row = section.next_row()
    box = ttk.Checkbutton(section, text=label, variable=variable)
    box.grid(row=row, column=0, columnspan=2, sticky="w", padx=PADX, pady=PADY)
    if hint:
        ttk.Label(section, text=hint, foreground="#6b7280", wraplength=600).grid(
            row=section.next_row(), column=0, columnspan=2,
            sticky="w", padx=PADX + 20, pady=(0, 6),
        )
    return box


class CodeSelector(ttk.Frame):
    """Cases a cocher sur une liste de codes de marche, alignees en ligne.

    L'ordre de la liste produite suit celui des codes proposes, pas l'ordre de
    clic : le rapport affiche les marches dans cet ordre.
    """

    def __init__(self, master, codes: dict[str, str]) -> None:
        super().__init__(master)
        self.variables: dict[str, tk.BooleanVar] = {}
        for index, (code, label) in enumerate(codes.items()):
            variable = tk.BooleanVar(value=False)
            self.variables[code] = variable
            ttk.Checkbutton(self, text=label, variable=variable).grid(
                row=index // 4, column=index % 4, sticky="w", padx=(0, 14), pady=2
            )

    def set_selection(self, codes: Iterable[str]) -> None:
        wanted = set(codes)
        for code, variable in self.variables.items():
            variable.set(code in wanted)

    def selection(self) -> list[str]:
        return [code for code, variable in self.variables.items() if variable.get()]


def code_row(section: Section, label: str, selector_codes: dict[str, str], hint: str = "") -> CodeSelector:
    row = section.next_row()
    ttk.Label(section, text=label).grid(row=row, column=0, sticky="nw", padx=PADX, pady=PADY)
    selector = CodeSelector(section, selector_codes)
    selector.grid(row=row, column=1, sticky="w", padx=PADX, pady=PADY)
    if hint:
        ttk.Label(section, text=hint, foreground="#6b7280", wraplength=560).grid(
            row=section.next_row(), column=1, sticky="w", padx=PADX, pady=(0, 6)
        )
    return selector


class LogPane(ttk.Frame):
    """Zone de journal en lecture seule, avec coloration par niveau."""

    MAX_LINES = 4000

    def __init__(self, master, height: int = 12) -> None:
        super().__init__(master)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.text = tk.Text(
            self,
            height=height,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            background="#fbfbfc",
            relief="solid",
            borderwidth=1,
        )
        self.text.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)

        for level, color in LOG_COLORS.items():
            self.text.tag_configure(level, foreground=color)
        self.text.tag_configure("title", font=("Consolas", 9, "bold"))
        self.text.tag_configure("ok", font=("Consolas", 9, "bold"))

    def append(self, line: str, level: str = "info") -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line.rstrip() + "\n", level)

        # Un journal qui grossit sans fin finit par ralentir le widget.
        excess = int(self.text.index("end-1c").split(".")[0]) - self.MAX_LINES
        if excess > 0:
            self.text.delete("1.0", f"{excess + 1}.0")

        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


class ScrollableFrame(ttk.Frame):
    """Cadre defilant : les onglets de reglages depassent la hauteur d'ecran."""

    def __init__(self, master) -> None:
        super().__init__(master)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scroll.set)

        self.body = ttk.Frame(canvas, padding=(4, 6))
        window = canvas.create_window((0, 0), window=self.body, anchor="nw")

        def on_body_resize(_event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def on_canvas_resize(event) -> None:
            canvas.itemconfigure(window, width=event.width)

        self.body.bind("<Configure>", on_body_resize)
        canvas.bind("<Configure>", on_canvas_resize)

        # La molette n'agit que quand le pointeur survole la zone, sinon elle
        # ferait defiler tous les onglets a la fois.
        def on_wheel(event) -> None:
            canvas.yview_scroll(-1 * (event.delta // 120), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
