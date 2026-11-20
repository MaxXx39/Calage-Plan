"""Calage par points homologues : 2 points pour caler, 1 (ou plus) pour contrôler.

Avec 2 paires de points, la similitude (échelle, rotation, translation) est entièrement
déterminée : une erreur de clic ne se voit pas. D'où le 3e point, de CONTRÔLE, qui ne participe
pas au calcul : son écart mesure la qualité réelle du calage.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .estimation import snap_echelle
from .geometrie import Similitude

TOL_SNAP_ECHELLE = 0.005     # on arrondit l'échelle à la puissance de 10 si elle en est à < 0,5 %
SEUIL_VERT = 0.01            # écart de contrôle < 1 cm : bon
SEUIL_ORANGE = 0.05          # < 5 cm : à vérifier ; au-delà : mauvais


class ErreurCalage(Exception):
    pass


@dataclass
class ResultatCalagePoints:
    similitude: Similitude
    echelle_brute: float
    echelle_arrondie: bool
    ecarts_calage: list[float]               # écart (unités cible) de chaque point de calage
    ecarts_controle: list[float] = field(default_factory=list)
    verdict: str = ""                        # "bon", "à vérifier", "mauvais", "sans contrôle"
    message: str = ""

    def vers_dict(self) -> dict:
        return {
            **self.similitude.vers_dict(),
            "echelle_brute": self.echelle_brute,
            "echelle_arrondie": self.echelle_arrondie,
            "ecarts_calage_m": [round(e, 4) for e in self.ecarts_calage],
            "ecarts_controle_m": [round(e, 4) for e in self.ecarts_controle],
            "verdict": self.verdict,
            "message": self.message,
        }


def calage_par_points(paires_calage, paires_controle=(), arrondir_echelle: bool = True
                      ) -> ResultatCalagePoints:
    """paires = [((x_plan, y_plan), (x_ref, y_ref)), ...] ; au moins 2 paires de calage."""
    if len(paires_calage) < 2:
        raise ErreurCalage("Il faut au moins 2 paires de points pour caler.")
    z = [complex(*p) for p, _ in paires_calage]
    w = [complex(*q) for _, q in paires_calage]
    if abs(z[1] - z[0]) < 1e-9 or abs(w[1] - w[0]) < 1e-9:
        raise ErreurCalage("Les 2 points de calage sont confondus : choisissez des points éloignés.")

    if len(z) == 2:
        a = (w[1] - w[0]) / (z[1] - z[0])
    else:  # plus de 2 points : moindres carrés
        zm, wm = sum(z) / len(z), sum(w) / len(w)
        a = sum((wi - wm) * (zi - zm).conjugate() for zi, wi in zip(z, w)) / \
            sum(abs(zi - zm) ** 2 for zi in z)
    s_brute = abs(a)
    arrondie = False
    if arrondir_echelle:
        s10 = snap_echelle(s_brute)
        if abs(s_brute / s10 - 1) < TOL_SNAP_ECHELLE:
            a = s10 * a / abs(a)
            arrondie = True
    # translation : moyenne des points de calage (répartit l'écart résiduel)
    b = sum(wi - a * zi for zi, wi in zip(z, w)) / len(z)
    sim = Similitude.depuis_complexes(a, b)
    if arrondie:
        sim.echelle = float(snap_echelle(s_brute))

    ecarts = [abs(a * zi + b - wi) for zi, wi in zip(z, w)]
    ctrl = [abs(a * complex(*p) + b - complex(*q)) for p, q in paires_controle]
    res = ResultatCalagePoints(sim, s_brute, arrondie, ecarts, ctrl)

    pire = max(ctrl) if ctrl else None
    if pire is None:
        res.verdict = "sans contrôle"
        res.message = "Aucun point de contrôle : la qualité du calage n'est pas vérifiée."
    elif pire < SEUIL_VERT:
        res.verdict = "bon"
        res.message = f"Point de contrôle à {pire * 1000:.0f} mm : calage bon."
    elif pire < SEUIL_ORANGE:
        res.verdict = "à vérifier"
        res.message = f"Point de contrôle à {pire * 100:.1f} cm : à vérifier (clic imprécis ?)."
    else:
        res.verdict = "mauvais"
        res.message = (f"Point de contrôle à {pire:.2f} m : calage FAUX. Vérifiez que les points "
                       "cliqués sont bien les mêmes sur les deux plans.")
    if arrondir_echelle and not arrondie:
        res.message += (f" Échelle {s_brute:.6g} : pas une puissance de 10 (unités inhabituelles ?).")
    elif arrondie and max(ecarts) > SEUIL_VERT:
        res.message += f" Après arrondi de l'échelle, écart sur les points de calage : {max(ecarts) * 1000:.0f} mm."
    return res
