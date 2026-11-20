"""Contrôle de concordance d'un nouvel indice avec l'indice appris.

Trois issues possibles :
  * « identique » : la plupart des ancres de A se retrouvent à < 1 cm dans B -> on rejoue tel quel ;
  * « recalé »    : B est décalé/tourné par rapport à A ; le décalage est estimé (vote + ICP)
                    et appliqué automatiquement car la confiance est forte ;
  * « refus »     : origine inconnue, unité changée ou confiance insuffisante -> pas de fichier
                    (sauf --forcer) : il faut refaire le calage à la main.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from .estimation import ECHELLES_CANDIDATES, icp, voter
from .geometrie import Similitude, points_segments

PART_IDENTIQUE = 0.5      # part d'ancres retrouvées pour conclure « même origine »
PART_RECALAGE = 0.5       # part d'ancres retrouvées APRÈS correction pour accepter le recalage
DOMINANCE_MIN = 2.0       # la meilleure solution du vote doit dominer nettement la seconde
SCORE_MIN = 15            # nombre minimal de votes concordants


@dataclass
class ResultatControle:
    mode: str                                  # "identique", "recalé", "refus"
    part_ancres: float                         # part d'ancres retrouvées telles quelles
    message: str
    correction: Similitude | None = None       # B -> A, en unités du plan
    part_ancres_apres: float | None = None
    unite_changee: float | None = None         # facteur détecté (ex. 10 si mm au lieu de cm)
    details: dict = field(default_factory=dict)

    def vers_dict(self) -> dict:
        d = {"mode": self.mode, "part_ancres_identiques": round(self.part_ancres, 3),
             "message": self.message}
        if self.correction is not None:
            c = self.correction
            d["correction_B_vers_A"] = {"echelle": c.echelle, "rotation_deg": round(c.rotation_deg, 5),
                                        "translation_unites_plan": [round(c.tx, 4), round(c.ty, 4)]}
        if self.part_ancres_apres is not None:
            d["part_ancres_apres_correction"] = round(self.part_ancres_apres, 3)
        if self.unite_changee is not None:
            d["facteur_unite_detecte"] = self.unite_changee
        d.update(self.details)
        return d


def part_ancres_retrouvees(ancres: np.ndarray, segs: np.ndarray, tol: float) -> float:
    """Part des ancres dont les deux extrémités ont une extrémité de segment de B à < tol."""
    if len(ancres) == 0 or len(segs) == 0:
        return 0.0
    pts = np.vstack([segs[:, :2], segs[:, 2:]])
    arbre = cKDTree(pts)
    d1, _ = arbre.query(ancres[:, :2])
    d2, _ = arbre.query(ancres[:, 2:])
    return float(np.mean((d1 < tol) & (d2 < tol)))


def controler(segs_b: np.ndarray, recette, insunits_b: int | None = None) -> ResultatControle:
    """Compare le nouvel indice (segments en unités du plan) aux ancres de la recette."""
    ancres = np.asarray(recette.ancres.get("segments", []), dtype=float).reshape(-1, 4)
    s = recette.similitude.echelle
    tol_m = float(recette.ancres.get("tolerance_m", 0.01))
    tol = tol_m / s                                    # 1 cm exprimé en unités du plan
    details = {}
    ins_a = int(recette.source.get("insunits", 0) or 0)
    if insunits_b and ins_a and insunits_b != ins_a:
        details["avertissement_unites"] = (
            f"L'unité déclarée dans le fichier a changé ($INSUNITS {ins_a} -> {insunits_b}).")

    if len(ancres) == 0:
        return ResultatControle("refus", 0.0, "La recette ne contient aucune ancre : contrôle impossible.",
                                details=details)

    part = part_ancres_retrouvees(ancres, segs_b, tol)
    if part >= PART_IDENTIQUE:
        return ResultatControle(
            "identique", part,
            f"Même origine que l'indice appris : {part:.0%} des repères retrouvés à moins de 1 cm.",
            details=details)

    # --- estimation du décalage A -> B (en mètres approximatifs : on applique l'échelle de A).
    # Les ancres (propres, peu nombreuses) servent de SOURCE : chaque point d'ancre cherche son
    # plus proche voisin dans B (riche), ce qui évite que la géométrie propre à B ne fasse dériver l'ICP.
    sc = Similitude(s, 0, 0, 0)
    a_m = ancres * s
    b_m = segs_b * s
    vote = voter(a_m, b_m, ECHELLES_CANDIDATES)
    if vote is None or vote.score <= 0:
        return ResultatControle(
            "refus", part,
            "Ce plan ne ressemble pas à l'indice appris (aucun repère commun trouvé). "
            "Refaites le calage à la main.", details=details)

    details["vote"] = {"echelle": vote.echelle, "score": vote.score,
                       "dominance": round(min(vote.dominance, 1e6), 2)}
    if vote.echelle != 1.0 and vote.score >= SCORE_MIN and vote.dominance >= DOMINANCE_MIN:
        facteur = vote.echelle            # B = facteur · A
        nom = {10.0: "mm au lieu de cm (×10)", 0.1: "÷10 (cm au lieu de mm ?)",
               100.0: "×100 (cm au lieu de m ?)", 0.01: "÷100 (m au lieu de cm ?)",
               1000.0: "×1000 (mm au lieu de m ?)", 0.001: "÷1000 (m au lieu de mm ?)"}.get(
            facteur, f"facteur {facteur:g}")
        return ResultatControle(
            "refus", part,
            f"L'UNITÉ DU PLAN SEMBLE AVOIR CHANGÉ : {nom}. Aucun fichier produit : "
            "refaites le calage à la main (ou demandez un plan dans la même unité).",
            unite_changee=facteur, details=details)

    pts_a = points_segments(a_m)
    pts_b = points_segments(b_m)
    res_icp = icp(pts_a, pts_b, vote.similitude, echelle_fixe=1.0)
    d_m = res_icp.similitude.inverse()          # B -> A, en mètres
    # correction en unités du plan : D_plan = Sc⁻¹ ∘ D_m ∘ Sc
    corr = sc.inverse().composer(d_m.composer(sc))
    corr.echelle = 1.0
    b_corrige = np.hstack([corr.appliquer(segs_b[:, :2]), corr.appliquer(segs_b[:, 2:])])
    part_apres = part_ancres_retrouvees(ancres, b_corrige, tol)
    details["icp"] = {"points": res_icp.nb_points,
                      "ecart_moyen_mm": round(res_icp.ecart_moyen * 1000, 2)}

    fort = (vote.echelle == 1.0 and vote.score >= SCORE_MIN and vote.dominance >= DOMINANCE_MIN
            and part_apres >= PART_RECALAGE)
    if fort:
        dep = np.hypot(d_m.tx, d_m.ty)
        return ResultatControle(
            "recalé", part,
            f"ATTENTION : CE PLAN N'A PAS LA MÊME ORIGINE QUE L'INDICE APPRIS. "
            f"Décalage détecté et corrigé automatiquement : rotation {(d_m.rotation_deg + 180) % 360 - 180 + 0.0:+.4f}°, "
            f"déplacement {d_m.tx:+.3f} m / {d_m.ty:+.3f} m (≈ {dep:.2f} m). "
            f"{part_apres:.0%} des repères concordent après correction. Vérifiez l'aperçu.",
            correction=corr, part_ancres_apres=part_apres, details=details)

    return ResultatControle(
        "refus", part,
        "Impossible de caler ce plan avec certitude sur l'indice appris "
        f"(repères concordants : {part:.0%} avant, {part_apres:.0%} après tentative de correction). "
        "Aucun fichier produit : refaites le calage à la main.",
        correction=corr, part_ancres_apres=part_apres, details=details)
