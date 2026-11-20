"""Préparation d'un plan pour l'affichage dans la page web, et accrochage aux points.

Les coordonnées sont envoyées RELATIVES à une origine (centre de l'emprise) : les navigateurs
dessinent en simple précision et les coordonnées MN95 (2 500 000 m) y perdraient leurs centimètres.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
from ezdxf import path as ezpath
from ezdxf.colors import aci2rgb
from scipy.spatial import cKDTree

from .appariement import COMPLEXES, exploser_complexe
from .geometrie import points_echantillon, segments_entite

TYPES_TEXTE = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}


def _couleur_calque(doc, nom: str) -> str:
    try:
        lay = doc.layers.get(nom)
        if lay.dxf.hasattr("true_color"):
            r, g, b = lay.rgb
        else:
            aci = abs(lay.dxf.get("color", 7))
            if aci in (7, 0, 255):
                return "#202020"
            r, g, b = aci2rgb(aci)
        # éclaircir trop clair -> lisible sur fond blanc
        if r + g + b > 600:
            r, g, b = int(r * 0.6), int(g * 0.6), int(b * 0.6)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return "#202020"


def _texte(e):
    t = e.dxftype()
    try:
        if t == "MTEXT":
            s = e.plain_text().split("\n")[0]
            h = e.dxf.get("char_height", 1.0)
            r = e.get_rotation()
            x, y = e.dxf.insert.x, e.dxf.insert.y
        else:
            s = e.dxf.get("text", "")
            h = e.dxf.get("height", 1.0)
            r = e.dxf.get("rotation", 0.0)
            (x, y), = points_echantillon(e)[:1]
        return x, y, float(h), float(r), s[:80]
    except Exception:
        return None


class PlanAffiche:
    """Géométrie d'un plan, prête à être envoyée à la page et interrogée pour l'accrochage."""

    def __init__(self, doc, elements, precision_rel: float = 1 / 20000):
        self.doc = doc
        self.elements = elements
        pts_tous = [p for el in elements for p in points_echantillon(el.entite)]
        arr = np.asarray(pts_tous) if pts_tous else np.zeros((1, 2))
        # emprise robuste (ignore les points aberrants très éloignés)
        lo = np.percentile(arr, 2, axis=0); hi = np.percentile(arr, 98, axis=0)
        self.emprise = [float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])]
        self.origine = ((lo + hi) / 2).round(0)
        taille = float(max(hi - lo)) or 1.0
        self.precision = taille * precision_rel
        self._construire()

    # ---------------------------------------------------------------------------------------
    def _construire(self):
        ox, oy = self.origine
        calques, types = {}, {}
        groupes = defaultdict(list)
        textes = []
        segs, sommets, centres = [], [], []

        def idx(d, k):
            if k not in d:
                d[k] = len(d)
            return d[k]

        for el in self.elements:
            t_parent = el.entite.dxftype()
            li = idx(calques, el.calque)
            ti = idx(types, t_parent)
            pieces = exploser_complexe(el.entite) if t_parent in COMPLEXES else [el.entite]
            for e in pieces:
                t = e.dxftype()
                if t in TYPES_TEXTE:
                    tx = _texte(e)
                    if tx:
                        x, y, h, r, s = tx
                        textes.append([li, ti, round(x - ox, 3), round(y - oy, 3), round(h, 3), round(r, 2), s])
                    continue
                # accrochage
                segs += segments_entite(e)
                if t in ("CIRCLE", "ARC", "ELLIPSE"):
                    centres += points_echantillon(e)[:1]
                    sommets += points_echantillon(e)[1:]
                elif t not in ("HATCH",):
                    sommets += points_echantillon(e)
                # dessin
                try:
                    chemins = ezpath.from_hatch(e) if t == "HATCH" else [ezpath.make_path(e)]
                except Exception:
                    continue
                for ch in chemins:
                    try:
                        pts = [(v.x, v.y) for v in ch.flattening(self.precision)]
                    except Exception:
                        continue
                    if len(pts) < 2:
                        continue
                    plat = []
                    for x, y in pts:
                        plat += [round(x - ox, 3), round(y - oy, 3)]
                    groupes[(li, ti)].append(plat)

        self.calques = list(calques)
        self.types = list(types)
        self.groupes = [{"l": l, "t": t, "lignes": v} for (l, t), v in groupes.items()]
        self.textes = textes
        compte = defaultdict(lambda: defaultdict(int))
        for el in self.elements:
            compte[el.calque][el.entite.dxftype()] += 1
        self.compte = compte
        # index d'accrochage (coordonnées absolues)
        self.segs = np.asarray(segs, dtype=float).reshape(-1, 4)
        pts = sommets + centres
        self.pts_accroche = np.asarray(pts, dtype=float).reshape(-1, 2)
        self.genre_pts = np.array(["sommet"] * len(sommets) + ["centre"] * len(centres))
        self.arbre = cKDTree(self.pts_accroche) if len(self.pts_accroche) else None

    def vers_dict(self) -> dict:
        return {
            "origine": [float(self.origine[0]), float(self.origine[1])],
            "emprise": self.emprise,
            "calques": [{"nom": c, "couleur": _couleur_calque(self.doc, c),
                         "n": sum(self.compte[c].values()), "types": dict(self.compte[c])}
                        for c in self.calques],
            "types": self.types,
            "groupes": self.groupes,
            "textes": self.textes,
        }

    # ---------------------------------------------------------------------------------------
    def segment_proche(self, x: float, y: float, rayon: float):
        """Segment droit le plus proche (pour désigner un axe) : [x1, y1, x2, y2] ou None."""
        return _segment_proche_impl(self.segs, x, y, rayon)

    def accrocher(self, x: float, y: float, rayon: float):
        """Point remarquable le plus proche de (x, y) dans le rayon : sommet, centre ou intersection.

        Renvoie (x, y, genre) ou None.
        """
        cands = []
        if self.arbre is not None:
            for i in self.arbre.query_ball_point((x, y), rayon):
                px, py = self.pts_accroche[i]
                cands.append((float(np.hypot(px - x, py - y)), float(px), float(py), str(self.genre_pts[i])))
        # intersections des segments proches du clic
        if len(self.segs):
            s = self.segs
            p0, p1 = s[:, :2], s[:, 2:]
            d = p1 - p0
            L2 = (d ** 2).sum(1)
            L2[L2 == 0] = 1e-12
            tt = np.clip(((x - p0[:, 0]) * d[:, 0] + (y - p0[:, 1]) * d[:, 1]) / L2, 0, 1)
            proj = p0 + d * tt[:, None]
            dist = np.hypot(proj[:, 0] - x, proj[:, 1] - y)
            proches = s[dist < rayon][:150]
            for i in range(len(proches)):
                for j in range(i + 1, len(proches)):
                    ip = _intersection(proches[i], proches[j], marge=rayon)
                    if ip is not None:
                        dd = float(np.hypot(ip[0] - x, ip[1] - y))
                        if dd < rayon:
                            cands.append((dd, ip[0], ip[1], "intersection"))
        if not cands:
            return None
        cands.sort(key=lambda c: c[0])
        _, px, py, genre = cands[0]
        return px, py, genre


def _segment_proche_impl(segs, x, y, rayon):
    if not len(segs):
        return None
    p0, p1 = segs[:, :2], segs[:, 2:]
    d = p1 - p0
    L2 = (d ** 2).sum(1)
    L2[L2 == 0] = 1e-12
    tt = np.clip(((x - p0[:, 0]) * d[:, 0] + (y - p0[:, 1]) * d[:, 1]) / L2, 0, 1)
    proj = p0 + d * tt[:, None]
    dist = np.hypot(proj[:, 0] - x, proj[:, 1] - y)
    # à distance égale (segments superposés), préférer le plus long : plus précis pour un axe
    score = dist - 1e-6 * np.sqrt(L2)
    i = int(np.argmin(score))
    if dist[i] > rayon:
        return None
    return [float(v) for v in segs[i]]


def _intersection(s1, s2, marge: float = 0.0):
    """Intersection de deux segments (tolère un léger dépassement `marge`)."""
    x1, y1, x2, y2 = s1
    x3, y3, x4, y4 = s2
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    l1 = np.hypot(x2 - x1, y2 - y1); l2 = np.hypot(x4 - x3, y4 - y3)
    if l1 == 0 or l2 == 0 or abs(den) < 1e-9 * l1 * l2 * 1e-3 or abs(den) / (l1 * l2) < 1e-4:
        return None  # parallèles
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / den
    et, eu = marge / l1, marge / l2
    if -et <= t <= 1 + et and -eu <= u <= 1 + eu:
        return float(x1 + t * (x2 - x1)), float(y1 + t * (y2 - y1))
    return None
