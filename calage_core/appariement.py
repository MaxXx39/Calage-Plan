"""Appariement d'entités entre deux plans déjà superposés (même repère).

Une entité du plan source est « retrouvée » dans le plan cible si au moins 50 % de ses
points d'échantillon tombent à moins de 3 cm d'un point du cible, de la même FAMILLE
(trait, cercle, texte, hachure...), et — si demandé — dans le calque attendu.

Les objets complexes (cotations, multileaders, multilignes) sont comparés via leurs
entités « explosées » : c'est ainsi qu'ils apparaissent dans un DXF nettoyé.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .geometrie import Similitude, nom_normalise, points_echantillon

TOLERANCE = 0.03       # 3 cm (unités cible)
PART_MIN = 0.5         # 50 % des points

FAMILLES = {
    "LINE": "trait", "LWPOLYLINE": "trait", "POLYLINE": "trait", "SPLINE": "trait",
    "MLINE": "trait", "XLINE": "trait", "RAY": "trait",
    "CIRCLE": "cercle", "ARC": "cercle", "ELLIPSE": "cercle",
    "TEXT": "texte", "MTEXT": "texte", "ATTRIB": "texte", "ATTDEF": "texte",
    "HATCH": "hachure", "SOLID": "plein", "TRACE": "plein", "3DFACE": "plein",
    "POINT": "point", "INSERT": "bloc",
}
# Objets que l'on compare (et que l'on exporte) sous forme explosée
COMPLEXES = {"DIMENSION", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION", "MULTILEADER",
             "MLEADER", "LEADER", "MLINE", "TOLERANCE", "ACAD_TABLE"}
# Annotations : candidates naturelles à la suppression
ANNOTATIONS = {"DIMENSION", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION", "MULTILEADER", "MLEADER",
               "LEADER", "TOLERANCE", "ATTDEF", "ATTRIB"}


def famille(dxftype: str) -> str:
    return FAMILLES.get(dxftype, "autre")


def exploser_complexe(e, profondeur: int = 0) -> list:
    """Entités simples composant un objet complexe (récursif, calque 0 -> calque parent)."""
    try:
        enfants = list(e.virtual_entities())
    except Exception:
        return []
    out = []
    calque = e.dxf.get("layer", "0")
    for v in enfants:
        if v.dxf.get("layer", "0") == "0":
            v.dxf.layer = calque
        t = v.dxftype()
        if (t == "INSERT" or t in COMPLEXES) and profondeur < 10:
            out.extend(exploser_complexe(v, profondeur + 1))
        elif t != "POINT":      # points de définition des cotes : invisibles
            out.append(v)
    return out


def points_familles(e) -> list[tuple[float, float, str]]:
    """Points d'échantillon avec leur famille ; objets complexes via leur explosion."""
    t = e.dxftype()
    if t in COMPLEXES:
        out = []
        for v in exploser_complexe(e):
            f = famille(v.dxftype())
            out += [(x, y, f) for x, y in points_echantillon(v)]
        if out:
            return out
    f = famille(t)
    return [(x, y, f) for x, y in points_echantillon(e)]


@dataclass
class IndexCible:
    """Index spatial des points du plan cible, par (calque normalisé, famille)."""
    arbres: dict
    arbres_famille: dict
    proprietaires: dict          # clé -> tableau des indices d'entités cible
    proprietaires_famille: dict
    calques: list                # calque (brut) de chaque entité cible

    @classmethod
    def construire(cls, elements) -> "IndexCible":
        par_cle = defaultdict(list); prop = defaultdict(list)
        par_fam = defaultdict(list); prop_fam = defaultdict(list)
        calques = []
        for i, el in enumerate(elements):
            calques.append(el.calque)
            cn = nom_normalise(el.calque)
            for x, y, f in points_familles(el.entite):
                par_cle[(cn, f)].append((x, y)); prop[(cn, f)].append(i)
                par_fam[f].append((x, y)); prop_fam[f].append(i)
        arbres = {k: cKDTree(np.asarray(v)) for k, v in par_cle.items()}
        arbres_f = {k: cKDTree(np.asarray(v)) for k, v in par_fam.items()}
        return cls(arbres, arbres_f, {k: np.asarray(v) for k, v in prop.items()},
                   {k: np.asarray(v) for k, v in prop_fam.items()}, calques)


@dataclass
class Appariement:
    retrouve: bool
    part: float
    cibles: list            # indices des entités cible touchées


def apparier(points: list[tuple[float, float, str]], index: IndexCible,
             calque_attendu_norm: str | None = None, tol: float = TOLERANCE) -> Appariement:
    """Teste un jeu de points (déjà dans le repère cible)."""
    if not points:
        return Appariement(False, 0.0, [])
    ok = 0
    cibles = []
    par_fam = defaultdict(list)
    for x, y, f in points:
        par_fam[f].append((x, y))
    for f, pts in par_fam.items():
        if calque_attendu_norm is None:
            arbre, prop = index.arbres_famille.get(f), index.proprietaires_famille.get(f)
        else:
            arbre = index.arbres.get((calque_attendu_norm, f))
            prop = index.proprietaires.get((calque_attendu_norm, f))
        if arbre is None:
            continue
        d, j = arbre.query(np.asarray(pts), distance_upper_bound=tol)
        bon = np.isfinite(d)
        ok += int(bon.sum())
        cibles += prop[j[bon]].tolist()
    part = ok / len(points)
    return Appariement(part >= PART_MIN, part, cibles)


def points_transformes(e, sim: Similitude | None) -> list[tuple[float, float, str]]:
    pf = points_familles(e)
    if sim is None or not pf:
        return pf
    xy = sim.appliquer(np.array([(p[0], p[1]) for p in pf]))
    return [(float(a), float(b), p[2]) for (a, b), p in zip(xy, pf)]
