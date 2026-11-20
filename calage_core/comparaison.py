"""Comparaison de deux DXF déjà dans le même repère (contrôle qualité, tests)."""
from __future__ import annotations

from collections import Counter

from .appariement import IndexCible, apparier, points_familles
from .explosion import exploser_document
from .geometrie import nom_normalise
from .lecture import lire_plan


def taux_homologues(chemin_a, chemin_b, tol: float = 0.03, meme_calque: bool = True,
                    exclure_calques_b=()):
    """Part des entités de A ayant un homologue dans B (< tol, même famille, même calque).

    Renvoie (part, nb_trouvees, nb_total, Counter des non trouvées par (calque, type)).
    """
    ea = exploser_document(lire_plan(chemin_a).doc).elements
    eb = exploser_document(lire_plan(chemin_b).doc).elements
    exclus = {nom_normalise(c) for c in exclure_calques_b}
    eb = [e for e in eb if nom_normalise(e.calque) not in exclus]
    index = IndexCible.construire(eb)
    ok = 0
    manquants = Counter()
    for el in ea:
        pts = points_familles(el.entite)
        a = apparier(pts, index, nom_normalise(el.calque) if meme_calque else None, tol)
        if a.retrouve or not pts:
            ok += 1
        else:
            manquants[(el.calque, el.entite.dxftype())] += 1
    n = len(ea)
    return (ok / n if n else 0.0), ok, n, manquants
