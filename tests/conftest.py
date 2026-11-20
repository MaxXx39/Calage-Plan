"""Fixtures communes : recette apprise sur les vrais fichiers + plans simulés."""
from __future__ import annotations

import logging
import math
import random
import shutil
from pathlib import Path

import ezdxf
import pytest
from ezdxf.math import Matrix44

from calage_core.apprentissage import apprendre
from calage_core.lecture import ErreurLecture, lire_plan

logging.getLogger("ezdxf").setLevel(logging.ERROR)

DATA = Path(__file__).parent / "data"
PLAN_A = DATA / "plan_A.dwg"
FINAL_A = DATA / "final_A.dxf"
REFERENCE = DATA / "reference.dwg"


@pytest.fixture(scope="session")
def dossier(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("calage")


@pytest.fixture(scope="session")
def plan_a_doc():
    """Plan A converti (nécessite ODA File Converter ou dwg2dxf)."""
    manquants = [p.name for p in (PLAN_A, FINAL_A, REFERENCE) if not p.exists()]
    if manquants:
        pytest.skip(f"fichiers de test absents de tests/data : {', '.join(manquants)}")
    try:
        return lire_plan(PLAN_A).doc
    except ErreurLecture as ex:
        pytest.skip(f"lecture DWG impossible sur ce poste : {ex}")


@pytest.fixture(scope="session")
def apprentissage(plan_a_doc):
    return apprendre(PLAN_A, FINAL_A, journal=None)


@pytest.fixture(scope="session")
def recette_json(apprentissage, dossier) -> Path:
    chemin = dossier / "recette.json"
    apprentissage.recette.enregistrer(chemin)
    return chemin


def _transformer_modele(doc, matrice) -> int:
    """Applique une matrice à toutes les entités de l'espace objet ; renvoie le nb d'échecs."""
    echecs = 0
    msp = doc.modelspace()
    for e in list(msp):
        try:
            e.transform(matrice)
        except Exception:
            msp.delete_entity(e)
            echecs += 1
    return echecs


@pytest.fixture(scope="session")
def plan_b_decale(plan_a_doc, dossier) -> Path:
    """Indice B simulé : tout décalé de (+1000 ; −500) cm, quelques suppressions et ajouts."""
    doc = lire_plan(PLAN_A).doc        # nouvelle copie
    _transformer_modele(doc, Matrix44.translate(1000, -500, 0))
    msp = doc.modelspace()
    # suppressions : 5 polylignes du calque RS_TS
    for e in [e for e in msp.query("LWPOLYLINE") if e.dxf.layer == "RS_TS"][:5]:
        msp.delete_entity(e)
    # ajouts : quelques lignes sur un calque existant et sur un calque nouveau
    doc.layers.add("NOUVEAU_CALQUE", color=3)
    for i in range(4):
        msp.add_line((19000 + 100 * i, -15000), (19000 + 100 * i, -14000), dxfattribs={"layer": "RS_TS"})
        msp.add_line((18000, -15000 + 100 * i), (18800, -15000 + 100 * i), dxfattribs={"layer": "NOUVEAU_CALQUE"})
    chemin = dossier / "plan_B_decale.dxf"
    doc.saveas(chemin)
    return chemin


@pytest.fixture(scope="session")
def plan_mm(plan_a_doc, dossier) -> Path:
    """Même plan, mais dessiné en millimètres."""
    doc = lire_plan(PLAN_A).doc
    _transformer_modele(doc, Matrix44.scale(10, 10, 10))
    doc.header["$INSUNITS"] = 4
    chemin = dossier / "plan_A_mm.dxf"
    doc.saveas(chemin)
    return chemin


@pytest.fixture(scope="session")
def plan_sans_rapport(dossier) -> Path:
    """Plan sans aucun rapport : segments aléatoires, en cm."""
    rnd = random.Random(42)
    doc = ezdxf.new("R2000")
    doc.header["$INSUNITS"] = 5
    msp = doc.modelspace()
    for _ in range(400):
        x, y = rnd.uniform(-20000, 20000), rnd.uniform(-20000, 20000)
        L, a = rnd.uniform(60, 3000), rnd.uniform(0, 2 * math.pi)
        msp.add_line((x, y), (x + L * math.cos(a), y + L * math.sin(a)), dxfattribs={"layer": "RS_TS"})
    chemin = dossier / "plan_sans_rapport.dxf"
    doc.saveas(chemin)
    return chemin
