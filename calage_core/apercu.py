"""Aperçu PNG : le plan calé en rouge par-dessus la référence en gris."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
import numpy as np  # noqa: E402
from ezdxf import path as ezpath  # noqa: E402


def polylignes(entites, precision: float = 0.05):
    """Liste de tableaux (N,2) représentant les entités (textes ignorés)."""
    out = []
    for e in entites:
        t = e.dxftype()
        if t in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF", "POINT", "INSERT"):
            continue
        try:
            if t == "HATCH":
                chemins = ezpath.from_hatch(e)
            else:
                chemins = [ezpath.make_path(e)]
            for ch in chemins:
                pts = np.array([(v.x, v.y) for v in ch.flattening(precision)])
                if len(pts) >= 2:
                    out.append(pts)
        except Exception:
            continue
    return out


def dessiner_apercu(chemin_png, entites_resultat, entites_reference=None, titre: str = "",
                    marge_rel: float = 0.08):
    fig, ax = plt.subplots(figsize=(12, 12), dpi=110)
    res = polylignes(entites_resultat)
    if res:
        allp = np.vstack(res)
        xmin, ymin = allp.min(0); xmax, ymax = allp.max(0)
    else:
        xmin = ymin = 0; xmax = ymax = 1
    dx, dy = xmax - xmin, ymax - ymin
    m = max(dx, dy) * marge_rel + 1
    box = (xmin - m, ymin - m, xmax + m, ymax + m)
    if entites_reference:
        ref = polylignes(entites_reference, precision=0.1)
        ref = [p for p in ref if (p[:, 0].max() >= box[0] and p[:, 0].min() <= box[2]
                                  and p[:, 1].max() >= box[1] and p[:, 1].min() <= box[3])]
        ax.add_collection(LineCollection(ref, colors="#9a9a9a", linewidths=0.6, label="Référence"))
    ax.add_collection(LineCollection(res, colors="#d62020", linewidths=0.5, label="Plan calé"))
    ax.set_xlim(box[0], box[2]); ax.set_ylim(box[1], box[3])
    ax.set_aspect("equal")
    ax.ticklabel_format(useOffset=False, style="plain")
    ax.tick_params(labelsize=7)
    ax.grid(True, lw=0.3, alpha=0.4)
    ax.set_title(titre or "Plan calé (rouge) sur la référence (gris)", fontsize=11)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(chemin_png)
    plt.close(fig)
