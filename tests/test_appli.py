"""Tests de l'appli : calage par points, accrochage, recette manuelle, serveur local."""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from calage_core.application import appliquer
from calage_core.comparaison import taux_homologues
from calage_core.explosion import exploser_document
from calage_core.lecture import lire_plan
from calage_core.points import ErreurCalage, calage_par_points
from calage_core.recette_manuelle import construire_recette
from calage_core.vue import PlanAffiche

from .conftest import FINAL_A, PLAN_A, REFERENCE

# Points homologues réels (coins du bâtiment visibles sur le plan ET sur la référence)
P1 = ((16382.052, -16553.176), (2510267.726, 1141751.289))
P2 = ((19985.355, -14088.02), (2510227.906, 1141769.19))
P3 = ((21672.818, -15966.025), (2510235.137, 1141793.38))   # contrôle


def test_calage_points_retrouve_la_transformation():
    r = calage_par_points([P1, P2], [P3])
    s = r.similitude
    assert s.echelle == 0.01 and r.echelle_arrondie
    assert abs(s.rotation_deg - 121.417) < 0.01
    assert abs(s.tx - 2510211.855) < 0.01 and abs(s.ty - 1141525.202) < 0.01
    assert r.verdict == "bon" and r.ecarts_controle[0] < 0.01


def test_calage_points_detecte_erreur_de_clic():
    faux = (P3[0], (P3[1][0] + 0.8, P3[1][1]))          # point de contrôle décalé de 80 cm
    r = calage_par_points([P1, P2], [faux])
    assert r.verdict == "mauvais" and "FAUX" in r.message


def test_calage_points_refuse_points_confondus():
    with pytest.raises(ErreurCalage):
        calage_par_points([P1, P1])


@pytest.fixture(scope="module")
def affiche_ref(plan_a_doc):
    p = lire_plan(REFERENCE)
    return PlanAffiche(p.doc, exploser_document(p.doc).elements)


def test_accrochage_sommet(affiche_ref):
    x, y = P1[1]
    r = affiche_ref.accrocher(x + 0.03, y - 0.02, rayon=0.1)
    assert r is not None
    assert abs(r[0] - x) < 0.002 and abs(r[1] - y) < 0.002


def test_accrochage_rien_loin_de_tout(affiche_ref):
    assert affiche_ref.accrocher(2400000, 1000000, rayon=0.5) is None


def test_recette_manuelle_rejouee(plan_a_doc, apprentissage, dossier):
    """Calage par points + les calques/types appris : le rejeu reproduit le final de l'utilisateur."""
    plan = lire_plan(PLAN_A)
    elems = exploser_document(plan.doc).elements
    cal = calage_par_points([P1, P2], [P3])
    appris = apprentissage.recette.nettoyage
    gardes = [c["nom"] for c in appris["calques_gardes"]]
    rec = construire_recette(PLAN_A, plan, elems, cal, gardes,
                             appris["types_supprimes"] + ["HATCH"], appris["prefixe_calque"],
                             zone_polygone=[(2510200, 1141720), (2510290, 1141720),
                                            (2510290, 1141820), (2510200, 1141820)],
                             nom_reference=REFERENCE.name)
    chemin_rec = dossier / "recette_manuelle.json"
    rec.enregistrer(chemin_rec)
    sortie = dossier / "A_manuel.dxf"
    rap = appliquer(PLAN_A, chemin_rec, sortie)
    assert rap.fichier_produit and rap.mode == "identique"
    part, ok, n, _ = taux_homologues(sortie, FINAL_A)
    assert part >= 0.85, f"{ok}/{n}"


# ------------------------------------------------------------------------- serveur local
@pytest.fixture(scope="module")
def serveur(tmp_path_factory, plan_a_doc):
    from calage_app.serveur import fabriquer_gestionnaire
    from calage_app.session import Session
    session = Session(tmp_path_factory.mktemp("appli"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fabriquer_gestionnaire(session))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(url, corps=None, octets=None):
    data = octets if octets is not None else json.dumps(corps or {}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def _get(url):
    with urllib.request.urlopen(url, timeout=300) as r:
        return json.loads(r.read())


def test_calage_mixte_axes_et_point():
    """2 axes (cliqués n'importe où le long des façades) + 1 point, et un point de contrôle."""
    from calage_core.points import calage_mixte
    els = [
        {"type": "axe", "plan": [17000, -16552.66, 20000, -16550.142],
         "ref": [2510260.373, 1141763.304, 2510250.0, 1141780.25]},
        {"type": "axe", "plan": [16382.052, -16553.176, 15905.161, -14091.758],
         "ref": [2510267.726, 1141751.289, 2510249.206, 1141734.389]},
        {"type": "point", "plan": list(P3[0]), "ref": list(P3[1])},
        {"type": "point", "role": "controle", "plan": list(P2[0]), "ref": list(P2[1])},
    ]
    r = calage_mixte(els)
    assert r.similitude.echelle == 0.01
    assert abs(r.similitude.rotation_deg - 121.417) < 0.01
    assert r.verdict == "bon"
    with pytest.raises(ErreurCalage):
        calage_mixte(els[:2])          # deux axes seuls : échelle indéterminée


def test_serveur_parcours_complet(serveur, plan_b_decale):
    assert "Calage de plans" in urllib.request.urlopen(serveur + "/").read().decode()
    assert _post(f"{serveur}/api/fichier?role=ref&nom=reference.dwg", octets=REFERENCE.read_bytes())["ok"]
    assert _post(f"{serveur}/api/fichier?role=plan&nom=plan_A.dwg", octets=PLAN_A.read_bytes())["ok"]
    r = _post(serveur + "/api/ouvrir", {"role": "ref"}); assert r["nb_entites"] > 100
    r = _post(serveur + "/api/ouvrir", {"role": "plan"}); assert r["unite"] == "cm"
    calques = [c["nom"] for c in r["geometrie"]["calques"]]
    a = _post(serveur + "/api/accroche", {"role": "ref", "x": P1[1][0] + 0.03, "y": P1[1][1], "rayon": 0.1})
    assert abs(a["x"] - P1[1][0]) < 0.002
    s = _post(serveur + "/api/accroche", {"role": "ref", "x": 2510260.373, "y": 1141763.304, "rayon": 0.3, "mode": "axe"})
    assert len(s["segment"]) == 4
    els = [{"type": "point", "plan": P1[0], "ref": P1[1]}, {"type": "point", "plan": P2[0], "ref": P2[1]},
           {"type": "point", "role": "controle", "plan": P3[0], "ref": P3[1]}]
    c = _post(serveur + "/api/calage", {"elements": els})
    assert c["verdict"] == "bon"
    fam = _get(serveur + "/api/familles")["familles"]
    assert any(f["libelle"] == "Cotations" and f["coche"] for f in fam)
    gardes = [n for n in calques if not n.startswith(("ATEC", "RS_Cotation"))]
    e = _post(serveur + "/api/exporter", {"chantier": "Chantier A", "type_plan": "Terrassement", "indice": "A",
                                          "prefixe": "X - ", "calques": gardes,
                                          "familles": ["Cotations", "Lignes de repère"], "zone": None})
    assert e["rapport"]["fichier_produit"] and e["rapport"]["mode"] == "identique"
    assert urllib.request.urlopen(serveur + e["telecharger"]).read()[:20].strip().startswith(b"0")
    ch = _get(serveur + "/api/chantiers")["chantiers"]
    assert ch[0]["nom"] == "Chantier A" and ch[0]["reference"] == "reference.dwg"
    assert ch[0]["types"][0]["type"] == "Terrassement"
    assert _get(serveur + "/api/chantiers")["types_existants"] == ["Terrassement"]
    # nouvel indice décalé -> recalé automatiquement, rangé avec le type de plan
    _post(f"{serveur}/api/fichier?role=indice&nom=plan_B.dxf", octets=plan_b_decale.read_bytes())
    i = _post(serveur + "/api/indice", {"chantier": "Chantier A", "type_plan": "Terrassement", "indice": "B"})
    assert i["rapport"]["mode"] == "recalé" and "reference" in i
    # rien n'est enregistré avant validation
    assert len(_get(serveur + "/api/chantiers")["chantiers"][0]["types"][0]["indices"]) == 1
    assert "attente" in i and "telecharger" not in i
    v = _post(serveur + "/api/indice/valider", {"chantier": "Chantier A", "type_plan": "Terrassement",
                                                "attente": i["attente"]})
    assert v["indice"] == "B" and v["precedent"] and urllib.request.urlopen(serveur + v["telecharger"]).read()
    # un indice calé puis abandonné ne laisse aucune trace
    _post(f"{serveur}/api/fichier?role=indice&nom=plan_C.dxf", octets=plan_b_decale.read_bytes())
    i2 = _post(serveur + "/api/indice", {"chantier": "Chantier A", "type_plan": "Terrassement", "indice": "C"})
    _post(serveur + "/api/indice/abandonner", {"chantier": "Chantier A", "type_plan": "Terrassement",
                                               "attente": i2["attente"]})
    indices = _get(serveur + "/api/chantiers")["chantiers"][0]["types"][0]["indices"]
    assert [x["indice"] for x in indices] == ["B", "A"]
    # différences A -> B : les 8 lignes ajoutées sont trouvées. (Les 5 polylignes supprimées du
    # fichier simulé existaient en double dans le plan : leur disparition ne change pas le dessin.)
    d = _post(serveur + "/api/comparer", {"ids": [indices[1]["id"], indices[0]["id"]], "mode": "differences"})
    assert 8 <= d["compte"]["ajoutes"] <= 12
    assert d["compte"]["inchanges"] > 1000
    sup = _post(serveur + "/api/comparer", {"ids": [indices[0]["id"]], "mode": "superposer"})
    assert len(sup["calques"]) == 1
    # calage automatique d'un autre type de plan du chantier (origine différente)
    _post(f"{serveur}/api/fichier?role=ref&nom=reference.dwg", octets=REFERENCE.read_bytes())
    _post(serveur + "/api/ouvrir", {"role": "ref"})
    _post(f"{serveur}/api/fichier?role=plan&nom=plan_B.dxf", octets=plan_b_decale.read_bytes())
    _post(serveur + "/api/ouvrir", {"role": "plan"})
    au = _post(serveur + "/api/calage_auto", {"chantier": "Chantier A"})
    assert au["confiance"]["niveau"] == "forte"
    # comparaison avec un indice choisi (l'ancien indice A), sans la référence
    au = _post(serveur + "/api/calage_auto", {"chantier": "Chantier A", "ids": [indices[1]["id"]],
                                              "avec_reference": False})
    assert au["confiance"]["niveau"] == "forte" and au["confiance"]["compare_avec"] == ["Terrassement (A)"]
    ctrl = _post(serveur + "/api/calage", {"auto": True, "elements": [
        {"type": "point", "plan": [P3[0][0] + 1000, P3[0][1] - 500], "ref": P3[1]}]})
    assert ctrl["verdict"] == "bon"
    # erreurs lisibles
    err = _post(serveur + "/api/indice", {"chantier": "inexistant", "type_plan": "x"})
    assert "introuvable" in err["erreur"]


def test_hachures_en_contour(plan_a_doc, dossier):
    """Mode « contour seulement » : aucune hachure dans le DXF, des polylignes à la place."""
    import ezdxf
    plan = lire_plan(PLAN_A)
    elems = exploser_document(plan.doc).elements
    cal = calage_par_points([P1, P2], [P3])
    gardes = sorted({e.calque for e in elems})
    sorties = {}
    for mode in ("garder", "contour", "supprimer"):
        rec = construire_recette(PLAN_A, plan, elems, cal, gardes, ["DIMENSION", "MULTILEADER"], "",
                                 hachures=mode)
        chemin = dossier / f"recette_hachures_{mode}.json"
        rec.enregistrer(chemin)
        rap = appliquer(PLAN_A, chemin, dossier / f"hachures_{mode}.dxf")
        msp = ezdxf.readfile(dossier / f"hachures_{mode}.dxf").modelspace()
        sorties[mode] = (len(msp.query("HATCH")), len(msp.query("LWPOLYLINE")), rap)
    assert sorties["garder"][0] > 50
    assert sorties["contour"][0] == 0 and sorties["supprimer"][0] == 0
    assert sorties["contour"][1] > sorties["supprimer"][1] + 50       # les contours remplacent les hachures
    assert any("contour" in i for i in sorties["contour"][2].infos)
