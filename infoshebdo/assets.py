"""Icone de l'application, dessinee par le programme.

L'icone est generee plutot que livree en binaire : elle existe donc toujours,
meme apres un clone du depot, et se regenere si elle est supprimee. Elle sert a
trois endroits qui exigent des formats differents :

* la fenetre Tk et la barre des taches  -> fichier .ico multi-tailles ;
* la zone de notification (pystray)     -> objet PIL en memoire ;
* l'executable PyInstaller              -> le meme fichier .ico.

Le motif est volontairement geometrique. A 16 pixels, un dessin detaille
devient une bouillie : un triangle de lecture sur fond fonce reste lisible a
toutes les tailles.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from . import paths

ICON_NAME = "infoshebdo.ico"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)

# Indigo profond -> violet, plus une pastille ambre pour le jeu video.
BACKGROUND_TOP = (49, 46, 129)
BACKGROUND_BOTTOM = (109, 40, 217)
FOREGROUND = (255, 255, 255)
ACCENT = (245, 158, 11)


def _draw(size: int) -> Image.Image:
    """Dessine l'icone a la taille demandee.

    On dessine quatre fois plus grand puis on reduit : c'est ce qui donne des
    bords lisses sans avoir a gerer l'anti-aliasing a la main.
    """
    scale = 4
    box = size * scale
    image = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Fond : degrade vertical, dessine ligne par ligne.
    for y in range(box):
        ratio = y / max(1, box - 1)
        color = tuple(
            round(BACKGROUND_TOP[i] + (BACKGROUND_BOTTOM[i] - BACKGROUND_TOP[i]) * ratio)
            for i in range(3)
        )
        draw.line([(0, y), (box, y)], fill=color + (255,))

    # Coins arrondis : on efface les angles par un masque.
    radius = round(box * 0.22)
    mask = Image.new("L", (box, box), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, box - 1, box - 1], radius, fill=255)
    image.putalpha(mask)

    # Triangle de lecture, legerement decale a droite pour paraitre centre :
    # l'oeil place le centre optique d'un triangle plus a gauche que son centre
    # geometrique.
    left = box * 0.34
    right = box * 0.76
    top = box * 0.24
    bottom = box * 0.76
    draw.polygon(
        [(left, top), (left, bottom), (right, (top + bottom) / 2)],
        fill=FOREGROUND + (255,),
    )

    # Pastille ambre en bas a gauche : la seconde moitie du sujet suivi.
    dot = box * 0.13
    margin = box * 0.14
    draw.ellipse(
        [margin, box - margin - dot, margin + dot, box - margin],
        fill=ACCENT + (255,),
    )

    return image.resize((size, size), Image.LANCZOS)


def tray_image(size: int = 64) -> Image.Image:
    """Image pour la zone de notification, en memoire."""
    return _draw(size)


def build_icon(target: Path | None = None) -> Path:
    """Ecrit le fichier .ico multi-tailles et renvoie son chemin."""
    destination = target or (paths.ASSETS_DIR / ICON_NAME)
    destination.parent.mkdir(parents=True, exist_ok=True)
    largest = _draw(max(ICON_SIZES))
    largest.save(
        destination,
        format="ICO",
        sizes=[(s, s) for s in ICON_SIZES],
    )
    return destination


def icon_path() -> Path | None:
    """Chemin du .ico, genere a la demande s'il manque.

    Renvoie None si l'ecriture est impossible : une icone absente ne doit pas
    empecher l'application de demarrer.
    """
    bundled = paths.RESOURCE_DIR / "assets" / ICON_NAME
    if bundled.exists():
        return bundled

    writable = paths.ASSETS_DIR / ICON_NAME
    if writable.exists():
        return writable
    try:
        return build_icon(writable)
    except OSError:
        return None
