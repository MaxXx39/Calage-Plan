"""Calage par éléments homologues : POINTS et/ou AXES (droites).

Chaque élément relie un objet du plan à caler à son homologue sur la référence :
  * point : x_plan -> x_ref                                   (2 équations)
  * axe   : la droite du plan doit tomber SUR la droite de la référence
            (les deux extrémités cliquées du plan doivent être sur la droite de la référence ;
             2 équations, peu importe où l'on a cliqué le long de l'axe)

Toutes ces équations sont LINÉAIRES en (a, b) avec x_ref = a·x_plan + b (nombres complexes) :
on résout par moindres carrés. Combinaisons valables : 2 points ; 1 point + 1 axe ;
2 axes + 1 point (recommandé avec des axes de construction)… Deux axes seuls ne suffisent pas :
ils fixent l'origine et l'orientation mais pas l'échelle.

Les éléments de rôle « contrôle » ne participent pas au calcul : leur écart mesure la qualité.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .estimation import snap_echelle
from .geometrie import Similitude

TOL_SNAP_ECHELLE = 0.005     # échelle arrondie à la puissance de 10 si elle en est à < 0,5 %
SEUIL_VERT = 0.01            # écart < 1 cm : bon
SEUIL_ORANGE = 0.05          # < 5 cm : à vérifier ; au-delà : mauvais


class ErreurCalage(Exception):
    pass


@dataclass
class ResultatCalage:
    similitude: Similitude
    echelle_brute: float
    echelle_arrondie: bool
    elements: list[dict]                 # éléments avec leur écart (« ecart_m »)
    verdict: str = ""                    # "bon", "à vérifier", "mauvais", "sans contrôle"
    message: str = ""
    methode: str = "points"              # "points", "axes et points", "automatique"
    confiance: dict = field(default_factory=dict)   # calage automatique : indicateurs

    @property
    def ecarts_calage(self) -> list[float]:
        return [e["ecart_m"] for e in self.elements if e["role"] == "calage"]

    @property
    def ecarts_controle(self) -> list[float]:
        return [e["ecart_m"] for e in self.elements if e["role"] == "controle"]

    def vers_dict(self) -> dict:
        return {
            **self.similitude.vers_dict(),
            "echelle_brute": self.echelle_brute,
            "echelle_arrondie": self.echelle_arrondie,
            "ecarts_calage_m": [round(e, 4) for e in self.ecarts_calage],
            "ecarts_controle_m": [round(e, 4) for e in self.ecarts_controle],
            "elements": self.elements,
            "verdict": self.verdict,
            "message": self.message,
            "methode": self.methode,
            "confiance": self.confiance,
        }


# ------------------------------------------------------------------------------------------
def _equations(el: dict):
    """Lignes (ar, ai, br, bi) et seconds membres d'un élément de calage."""
    lignes, sm = [], []
    if el["type"] == "point":
        (x, y), (u, v) = el["plan"], el["ref"]
        lignes += [[x, -y, 1, 0], [y, x, 0, 1]]
        sm += [u, v]
    elif el["type"] == "axe":
        x1, y1, x2, y2 = el["plan"]
        u1, v1, u2, v2 = el["ref"]
        L = math.hypot(u2 - u1, v2 - v1)
        if L == 0 or math.hypot(x2 - x1, y2 - y1) == 0:
            raise ErreurCalage("Axe de longueur nulle : cliquez sur une ligne.")
        dx, dy = (u2 - u1) / L, (v2 - v1) / L
        for x, y in ((x1, y1), (x2, y2)):
            # distance signée du point transformé à la droite de référence = 0
            lignes.append([y * dx - x * dy, x * dx + y * dy, -dy, dx])
            sm.append(v1 * dx - u1 * dy)
    else:
        raise ErreurCalage(f"Type d'élément inconnu : {el['type']}")
    return lignes, sm


def ecart_element(el: dict, sim: Similitude) -> dict:
    """Écart d'un élément après calage (point : distance ; axe : distance max + angle)."""
    a, b = sim.a, sim.b
    if el["type"] == "point":
        w = a * complex(*el["plan"]) + b
        return {"ecart_m": abs(w - complex(*el["ref"]))}
    x1, y1, x2, y2 = el["plan"]
    u1, v1, u2, v2 = el["ref"]
    d = complex(u2 - u1, v2 - v1); d /= abs(d)
    dist = []
    for z in (complex(x1, y1), complex(x2, y2)):
        w = a * z + b - complex(u1, v1)
        dist.append(abs((w * d.conjugate()).imag))
    dp = a * complex(x2 - x1, y2 - y1)
    ang = abs(math.degrees(math.atan2((dp * d.conjugate()).imag, (dp * d.conjugate()).real)))
    ang = min(ang, 180 - ang)
    return {"ecart_m": max(dist), "ecart_angle_deg": round(ang, 4)}


def calage_mixte(elements: list[dict], arrondir_echelle: bool = True) -> ResultatCalage:
    """elements = [{"type": "point"|"axe", "role": "calage"|"controle", "plan": [...], "ref": [...]}]."""
    cal = [e for e in elements if e.get("role", "calage") == "calage"]
    ctrl = [e for e in elements if e.get("role") == "controle"]
    if not cal:
        raise ErreurCalage("Aucun élément de calage.")
    nb_pts = sum(e["type"] == "point" for e in cal)
    nb_axes = sum(e["type"] == "axe" for e in cal)
    if nb_pts == 0 and nb_axes >= 2:
        # Deux axes fixent l'origine et l'orientation, mais l'échelle reste libre
        # (numériquement presque indéterminée) : on exige un point.
        raise ErreurCalage("Deux axes ne suffisent pas (l'échelle n'est pas déterminée) : "
                           "ajoutez un point de calage.")
    if nb_pts + nb_axes < 2:
        raise ErreurCalage("Il faut au moins 2 éléments de calage (2 points, ou 1 point et 1 axe…).")
    A, B = [], []
    for e in cal:
        l, s = _equations(e)
        A += l; B += s
    A = np.asarray(A, float); B = np.asarray(B, float)
    # mise à l'échelle des colonnes pour un conditionnement correct (coordonnées très différentes)
    norme = np.linalg.norm(A, axis=0); norme[norme == 0] = 1
    An = A / norme
    sv = np.linalg.svd(An, compute_uv=False) if len(An) >= 4 else np.zeros(4)
    if len(An) < 4 or sv[-1] < 1e-8 * sv[0]:
        nb_axes = sum(e["type"] == "axe" for e in cal)
        if nb_axes >= 2 and not any(e["type"] == "point" for e in cal):
            raise ErreurCalage("Deux axes ne suffisent pas (l'échelle n'est pas déterminée) : "
                               "ajoutez un point de calage.")
        raise ErreurCalage("Calage indéterminé : il faut 2 points, ou 1 point et 1 axe, "
                           "ou 2 axes et 1 point (axes non parallèles, points distincts).")
    sol, *_ = np.linalg.lstsq(An, B, rcond=None)
    ar, ai, br, bi = sol / norme
    a = complex(ar, ai)
    s_brute = abs(a)
    arrondie = False
    if arrondir_echelle:
        s10 = snap_echelle(s_brute)
        if abs(s_brute / s10 - 1) < TOL_SNAP_ECHELLE:
            a = s10 * a / abs(a)
            arrondie = True
            # translation recalculée avec a fixé (moindres carrés sur les colonnes br, bi)
            rhs = B - A[:, 0] * a.real - A[:, 1] * a.imag
            (br, bi), *_ = np.linalg.lstsq(A[:, 2:], rhs, rcond=None)
    sim = Similitude.depuis_complexes(a, complex(br, bi))
    if arrondie:
        sim.echelle = float(snap_echelle(s_brute))
    sim.tx, sim.ty = float(sim.tx), float(sim.ty)

    sortie = []
    for e in elements:
        d = dict(e)
        d.setdefault("role", "calage")
        d.update({k: (round(v, 5) if isinstance(v, float) else v) for k, v in ecart_element(e, sim).items()})
        sortie.append(d)
    res = ResultatCalage(sim, s_brute, arrondie, sortie,
                         methode="axes et points" if any(e["type"] == "axe" for e in cal) else "points")
    _verdict(res, arrondir_echelle)
    return res


def _verdict(res: ResultatCalage, arrondir_echelle=True) -> None:
    ctrl = res.ecarts_controle
    interne = max(res.ecarts_calage) if res.ecarts_calage else 0.0
    if not ctrl:
        res.verdict = "sans contrôle"
        res.message = "Aucun élément de contrôle : la qualité du calage n'est pas vérifiée."
        if interne > SEUIL_VERT:
            res.message += f" Écart entre les éléments de calage : {interne * 100:.1f} cm."
    else:
        pire = max(ctrl)
        if pire < SEUIL_VERT:
            res.verdict, res.message = "bon", f"Contrôle à {pire * 1000:.0f} mm : calage bon."
        elif pire < SEUIL_ORANGE:
            res.verdict = "à vérifier"
            res.message = f"Contrôle à {pire * 100:.1f} cm : à vérifier (clic imprécis ?)."
        else:
            res.verdict = "mauvais"
            res.message = (f"Contrôle à {pire:.2f} m : calage FAUX. Vérifiez que les éléments "
                           "cliqués sont bien les mêmes sur les deux plans.")
    if arrondir_echelle and not res.echelle_arrondie:
        res.message += f" Échelle {res.echelle_brute:.6g} : pas une puissance de 10 (unités inhabituelles ?)."
    elif res.echelle_arrondie and interne > SEUIL_VERT and ctrl:
        res.message += f" Écart sur les éléments de calage : {interne * 1000:.0f} mm."


def evaluer(sim: Similitude, elements: list[dict], methode: str, echelle_brute: float,
            confiance: dict | None = None) -> ResultatCalage:
    """Évalue une similitude imposée (calage automatique) : tous les éléments sont des contrôles."""
    sortie = []
    for e in elements:
        d = dict(e); d["role"] = "controle"
        d.update({k: (round(v, 5) if isinstance(v, float) else v) for k, v in ecart_element(e, sim).items()})
        sortie.append(d)
    res = ResultatCalage(sim, echelle_brute, True, sortie, methode=methode, confiance=confiance or {})
    _verdict(res)
    return res


# ------------------------------------------------------------------------------------------
def calage_par_points(paires_calage, paires_controle=(), arrondir_echelle: bool = True) -> ResultatCalage:
    """Raccourci : paires = [((x_plan, y_plan), (x_ref, y_ref)), ...]."""
    if len(paires_calage) < 2:
        raise ErreurCalage("Il faut au moins 2 paires de points pour caler.")
    if math.dist(paires_calage[0][0], paires_calage[1][0]) < 1e-9 or \
            math.dist(paires_calage[0][1], paires_calage[1][1]) < 1e-9:
        raise ErreurCalage("Les 2 points de calage sont confondus : choisissez des points éloignés.")
    els = [{"type": "point", "role": "calage", "plan": list(p), "ref": list(q)} for p, q in paires_calage]
    els += [{"type": "point", "role": "controle", "plan": list(p), "ref": list(q)} for p, q in paires_controle]
    return calage_mixte(els, arrondir_echelle)
