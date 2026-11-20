"""Construction d'une recette à partir des choix faits dans l'appli (calage par points + nettoyage).

Ici rien n'est deviné : la recette est le journal exact des décisions de l'utilisateur.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np

from . import __version__
from .apprentissage import _choisir_ancres
from .lecture import _empreinte
from .recette import UNITES_INSUNITS, Recette

# Familles de types proposées à la suppression dans l'appli (libellé -> types DXF)
FAMILLES_SUPPRIMABLES = {
    "Cotations": ["DIMENSION", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION", "TOLERANCE"],
    "Lignes de repère": ["MULTILEADER", "MLEADER", "LEADER"],
    "Attributs de blocs": ["ATTDEF", "ATTRIB"],
    "Hachures": ["HATCH"],
    "Textes": ["TEXT", "MTEXT"],
    "Surfaces pleines": ["SOLID", "TRACE", "3DFACE"],
    "Points": ["POINT"],
    "Multilignes": ["MLINE"],
    "Courbes spline": ["SPLINE"],
    "Images, OLE, objets spéciaux": ["IMAGE", "WIPEOUT", "OLE2FRAME", "ACAD_PROXY_ENTITY",
                                      "REGION", "3DSOLID", "BODY", "MESH", "ACAD_TABLE"],
}
FAMILLES_PAR_DEFAUT = ["Cotations", "Lignes de repère", "Attributs de blocs",
                       "Images, OLE, objets spéciaux"]


def construire_recette(chemin_plan, plan_lu, elements, calage,
                       calques_gardes: list[str], types_supprimes: list[str], prefixe: str,
                       zone_polygone=None, nom_reference: str | None = None,
                       renommages: dict | None = None, chantier: str | None = None,
                       type_plan: str | None = None) -> Recette:
    """calage : ResultatCalage ; calques_gardes : noms (accents corrects) ; zone en MN95."""
    chemin_plan = Path(chemin_plan)
    renommages = renommages or {}
    sim = calage.similitude
    compte = {}
    for el in elements:
        compte[el.calque] = compte.get(el.calque, 0) + 1
    gardes = [{"nom": c, "sortie": prefixe + renommages.get(c, c), "entites": compte.get(c, 0)}
              for c in sorted(compte, key=str.lower) if c in set(calques_gardes)]
    ecartes = [{"nom": c, "entites": n, "raison": "décoché dans l'appli"}
               for c, n in sorted(compte.items(), key=lambda kv: kv[0].lower())
               if c not in set(calques_gardes)]

    zone = {}
    if zone_polygone and len(zone_polygone) >= 3:
        poly = [[round(float(x), 3), round(float(y), 3)] for x, y in zone_polygone]
        arr = np.asarray(poly)
        zone = {"polygone_mn95": poly,
                "emprise_mn95": [float(arr[:, 0].min()), float(arr[:, 1].min()),
                                 float(arr[:, 0].max()), float(arr[:, 1].max())],
                "marge_m": 0.0}

    ancres = _choisir_ancres(elements, set(calques_gardes), sim.echelle)

    return Recette(
        outil=f"calage-plans {__version__}",
        cree_le=dt.datetime.now().isoformat(timespec="seconds"),
        source={
            "methode": f"calage {calage.methode}",
            "chantier": chantier, "type_plan": type_plan,
            "plan": chemin_plan.name, "empreinte_plan": _empreinte(chemin_plan),
            "reference": nom_reference,
            "insunits": plan_lu.insunits, "unite": UNITES_INSUNITS.get(plan_lu.insunits, "?"),
            "nb_entites_plan": len(elements),
        },
        transformation={
            **sim.vers_dict(),
            "convention": "x_final = echelle · R(rotation) · x_plan + translation",
            "qualite": {
                "echelle_brute": calage.echelle_brute,
                "echelle_arrondie": calage.echelle_arrondie,
                "elements": calage.elements,
                "verdict": calage.verdict,
                "confiance_auto": calage.confiance,
            },
        },
        nettoyage={
            "exploser_blocs": True,
            "prefixe_calque": prefixe,
            "calques_gardes": gardes,
            "calques_ecartes": ecartes,
            "calques_renommes": dict(renommages),
            "types_supprimes": sorted(set(types_supprimes)),
            "types_supprimes_par_calque": {},
            "calques_nouveaux": "garder",
        },
        zone=zone,
        ancres={"unites": "unités du plan d'origine", "tolerance_m": 0.01,
                "nb": len(ancres), "segments": ancres},
        ajouts_manuels={},
        apprentissage={"methode": "manuel"},
    )
