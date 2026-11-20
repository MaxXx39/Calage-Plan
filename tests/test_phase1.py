"""Tests de la phase 1 sur les fichiers réels (tests/data)."""
from __future__ import annotations

import json

import pytest

from calage_core.application import appliquer
from calage_core.comparaison import taux_homologues
from calage_core.recette import Recette

from .conftest import FINAL_A, PLAN_A, REFERENCE

ROTATION_ATTENDUE = 121.417
TRANSLATION_ATTENDUE = (2510211.855, 1141525.202)


# --------------------------------------------------------------------------- a. apprendre
def test_a_transformation_apprise(apprentissage):
    t = apprentissage.recette.transformation
    assert t["echelle"] == 0.01                                   # échelle exacte
    assert abs(t["rotation_deg"] - ROTATION_ATTENDUE) <= 0.01     # ±0,01°
    assert abs(t["translation"][0] - TRANSLATION_ATTENDUE[0]) <= 0.01   # ±1 cm
    assert abs(t["translation"][1] - TRANSLATION_ATTENDUE[1]) <= 0.01
    assert t["qualite"]["ecart_moyen_m"] < 0.005                  # écart moyen < 5 mm
    assert t["qualite"]["points_icp"] >= 300


def test_a_nettoyage_appris(apprentissage):
    r = apprentissage.recette
    n = r.nettoyage
    assert n["prefixe_calque"] == "DWG_1957-100-G T - "
    assert n["exploser_blocs"] is True
    assert {"DIMENSION", "MULTILEADER"} <= set(n["types_supprimes"])
    gardes = {c["nom"] for c in n["calques_gardes"]}
    ecartes = {c["nom"] for c in n["calques_ecartes"]}
    assert "RS_Emprise bâtiment" in gardes            # accents corrects
    assert "RS_Cotation_Principale" in ecartes
    assert not gardes & ecartes
    # ajouts manuels listés (et non rejoués)
    assert "PERRIN - Dessin" in r.ajouts_manuels["par_calque"]
    assert 400 <= len(r.ancres["segments"]) <= 600
    assert r.zone["emprise_mn95"][0] > 2_500_000
    assert r.source["insunits"] == 5


def test_a_recette_relisible(recette_json):
    r = Recette.charger(recette_json)
    assert r.version == 1
    d = json.loads(recette_json.read_text(encoding="utf-8"))
    assert {"source", "transformation", "nettoyage", "zone", "ancres", "ajouts_manuels"} <= d.keys()


# --------------------------------------------------------------------------- b. rejeu sur A
@pytest.fixture(scope="module")
def sortie_a(recette_json, dossier):
    chemin = dossier / "A_MN95.dxf"
    rap = appliquer(PLAN_A, recette_json, chemin, ref=REFERENCE, apercu=dossier / "A.png",
                    chemin_rapport=dossier / "A.json")
    return chemin, rap


def test_b_rejeu_reproduit_final(sortie_a):
    chemin, rap = sortie_a
    assert rap.fichier_produit and rap.mode == "identique"
    part, ok, n, manquants = taux_homologues(chemin, FINAL_A)
    print(f"sortie -> final : {ok}/{n} = {part:.1%}  ; manquants : {manquants.most_common(5)}")
    assert part >= 0.90


def test_b_final_reproduit_par_rejeu(sortie_a, recette_json, dossier):
    """Inversement : les entités du final qui existent dans le plan d'origine sont reproduites.

    On exclut les éléments ajoutés à la main (calque « PERRIN - Dessin ») et les éléments du
    final qui n'ont AUCUN homologue dans le plan d'origine (même transformé et non nettoyé),
    ainsi que ceux qui ne proviennent que de lignes de repère / cotations (objets dont le décodage
    DWG n'est pas fiable et que la recette supprime).
    """
    from calage_core.appariement import IndexCible, apparier, points_familles
    from calage_core.explosion import exploser_document
    from calage_core.geometrie import nom_normalise
    from calage_core.lecture import lire_plan

    chemin, _ = sortie_a
    r = Recette.charger(recette_json)
    # plan d'origine transformé SANS nettoyage (hors annotations)
    r.nettoyage.update(calques_gardes=[], calques_ecartes=[], types_supprimes_par_calque={},
                       types_supprimes=["DIMENSION", "MULTILEADER", "ATTDEF"])
    r.zone = {}
    brut_json = dossier / "brut.json"
    r.enregistrer(brut_json)
    brut = dossier / "brut.dxf"
    appliquer(PLAN_A, brut_json, brut)

    def elements(p):
        return exploser_document(lire_plan(p).doc).elements

    i_brut = IndexCible.construire(elements(brut))
    i_sortie = IndexCible.construire(elements(chemin))
    manuels = {nom_normalise(c) for c in r.ajouts_manuels["par_calque"]}
    reproductibles = retrouves = 0
    for el in elements(FINAL_A):
        if nom_normalise(el.calque) in manuels:
            continue
        pts = points_familles(el.entite)
        if not apparier(pts, i_brut).retrouve:
            continue
        reproductibles += 1
        retrouves += apparier(pts, i_sortie, nom_normalise(el.calque)).retrouve
    part = retrouves / reproductibles
    print(f"final -> sortie : {retrouves}/{reproductibles} = {part:.1%}")
    assert part >= 0.90


def test_b_sortie_dxf_conforme(sortie_a):
    import ezdxf
    chemin, rap = sortie_a
    doc = ezdxf.readfile(chemin)
    assert doc.dxfversion == "AC1015"                       # R2000
    assert doc.header["$INSUNITS"] == 6                     # mètres
    msp = doc.modelspace()
    assert len(msp.query("INSERT DIMENSION MULTILEADER")) == 0
    assert "DWG_1957-100-G T - RS_Emprise bâtiment" in doc.layers   # accents corrects
    assert not [s for s in doc.dimstyles if s.dxf.name not in ("Standard",)]
    assert rap.apercu is not None


# --------------------------------------------------------------------------- c. indice B décalé
def test_c_indice_b_decale(plan_b_decale, recette_json, sortie_a, dossier):
    chemin_b = dossier / "B_MN95.dxf"
    rap = appliquer(plan_b_decale, recette_json, chemin_b, chemin_rapport=dossier / "B.json")
    assert rap.fichier_produit
    assert rap.mode == "recalé"
    c = rap.controle["correction_B_vers_A"]
    assert abs(c["translation_unites_plan"][0] + 1000) < 1.0     # < 1 cm (unités : cm)
    assert abs(c["translation_unites_plan"][1] - 500) < 1.0
    assert abs((c["rotation_deg"] + 180) % 360 - 180) < 0.001
    assert any("PAS LA MÊME ORIGINE" in a for a in rap.alertes)   # signalé en gros
    assert "NOUVEAU_CALQUE" in rap.calques_nouveaux                # calque nouveau gardé + signalé
    # même résultat que A à 1 cm près (hors entités supprimées / ajoutées)
    chemin_a, _ = sortie_a
    part, ok, n, _ = taux_homologues(chemin_b, chemin_a, tol=0.01)
    assert part >= 0.97, f"B -> A : {ok}/{n}"
    part, ok, n, _ = taux_homologues(chemin_a, chemin_b, tol=0.01)
    assert part >= 0.97, f"A -> B : {ok}/{n}"


# --------------------------------------------------------------------------- d. plan sans rapport
def test_d_plan_sans_rapport_refuse(plan_sans_rapport, recette_json, dossier):
    sortie = dossier / "rien.dxf"
    rap = appliquer(plan_sans_rapport, recette_json, sortie)
    assert not rap.fichier_produit
    assert rap.mode == "refus"
    assert not sortie.exists()
    assert "calage à la main" in rap.controle["message"]


def test_d_reference_refusee(recette_json, dossier):
    sortie = dossier / "ref.dxf"
    rap = appliquer(REFERENCE, recette_json, sortie)
    assert not rap.fichier_produit and rap.mode == "refus"


def test_d_forcer_produit_un_fichier_signale(plan_sans_rapport, recette_json, dossier):
    sortie = dossier / "force.dxf"
    rap = appliquer(plan_sans_rapport, recette_json, sortie, forcer=True)
    assert rap.fichier_produit and rap.mode == "forcé"
    assert any("CONTRÔLE IGNORÉ" in a for a in rap.alertes)


# --------------------------------------------------------------------------- e. plan en mm
def test_e_plan_en_mm_detecte(plan_mm, recette_json, dossier):
    sortie = dossier / "mm.dxf"
    rap = appliquer(plan_mm, recette_json, sortie)
    assert not rap.fichier_produit and rap.mode == "refus"
    assert rap.controle.get("facteur_unite_detecte") == 10.0
    assert "UNITÉ" in rap.controle["message"]
    assert any("Unité déclarée différente" in a for a in rap.alertes)
