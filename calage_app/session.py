"""État de travail de l'appli et opérations appelées par la page web.

Un seul utilisateur, en local : l'état est simplement gardé en mémoire.
Dossier de travail (modifiable par la variable d'environnement CALAGE_DOSSIER) :
    ~/CalagePlans/projets/<projet>/recette.json + la référence
    ~/CalagePlans/sorties/            DXF calés et rapports
    ~/CalagePlans/travail/            fichiers déposés (temporaires)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import uuid
from pathlib import Path

from calage_core.application import appliquer
from calage_core.explosion import exploser_document
from calage_core.lecture import lire_plan
from calage_core.points import calage_par_points
from calage_core.recette import Recette
from calage_core.recette_manuelle import (FAMILLES_PAR_DEFAUT, FAMILLES_SUPPRIMABLES,
                                          construire_recette)
from calage_core.vue import PlanAffiche

logging.getLogger("ezdxf").setLevel(logging.ERROR)


class ErreurAppli(Exception):
    """Erreur à afficher telle quelle à l'utilisateur."""


def nom_sur(nom: str, defaut: str = "fichier") -> str:
    """Nom de fichier sans chemin ni caractères gênants."""
    nom = os.path.basename(nom.replace("\\", "/")).strip()
    nom = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nom)
    return nom or defaut


class Session:
    def __init__(self, racine: str | Path | None = None):
        self.racine = Path(racine or os.environ.get("CALAGE_DOSSIER") or Path.home() / "CalagePlans")
        for d in ("projets", "sorties", "travail"):
            (self.racine / d).mkdir(parents=True, exist_ok=True)
        self.verrou = threading.RLock()
        self.dossiers = {}          # rôle -> dossier de travail ("plan", "ref", "indice")
        self.fichiers = {}          # rôle -> chemin du fichier principal
        self.lus = {}               # rôle -> PlanLu
        self.elements = {}          # rôle -> éléments explosés
        self.affiches = {}          # rôle -> PlanAffiche
        self.calage = None
        self.paires = ([], [])
        self.telechargements = {}   # id -> chemin
        self._cache_ref_projet = {}

    # ------------------------------------------------------------------ fichiers déposés
    def deposer(self, role: str, nom: str, contenu: bytes) -> dict:
        if role not in ("ref", "plan", "xref", "indice", "indice_xref"):
            raise ErreurAppli(f"Rôle de fichier inconnu : {role}")
        nom = nom_sur(nom)
        with self.verrou:
            principal = role in ("ref", "plan", "indice")
            cle = {"xref": "plan", "indice_xref": "indice"}.get(role, role)
            if principal:
                if Path(nom).suffix.lower() not in (".dwg", ".dxf"):
                    raise ErreurAppli("Déposez un fichier .dwg ou .dxf.")
                d = self.racine / "travail" / uuid.uuid4().hex[:12]
                d.mkdir(parents=True)
                self.dossiers[cle] = d
            elif cle not in self.dossiers:
                raise ErreurAppli("Déposez d'abord le plan, puis ses xrefs.")
            chemin = self.dossiers[cle] / nom
            chemin.write_bytes(contenu)
            if principal:
                self.fichiers[cle] = chemin
                for dico in (self.lus, self.elements, self.affiches):
                    dico.pop(cle, None)
                if cle == "plan":
                    self.calage = None
            return {"ok": True, "nom": nom}

    def ouvrir(self, role: str) -> dict:
        with self.verrou:
            if role not in self.fichiers:
                raise ErreurAppli("Aucun fichier déposé pour ce rôle.")
            chemin = self.fichiers[role]
            plan = lire_plan(chemin)
            ex = exploser_document(plan.doc, chemin.parent)
            aff = PlanAffiche(plan.doc, ex.elements)
            self.lus[role], self.elements[role], self.affiches[role] = plan, ex.elements, aff
            alertes = list(plan.avertissements)
            for x in ex.xrefs:
                if x.statut.startswith("VIDE"):
                    alertes.append(f"Xref « {x.nom} » introuvable : déposez le fichier "
                                   f"« {Path(x.chemin.replace(chr(92), '/')).name or x.nom} » puis rechargez.")
            alertes += ex.erreurs
            from calage_core.recette import UNITES_INSUNITS
            return {"nom": chemin.name, "unite": UNITES_INSUNITS.get(plan.insunits, "?"),
                    "nb_entites": len(ex.elements),
                    "xrefs": [x.__dict__ for x in ex.xrefs], "alertes": alertes,
                    "geometrie": aff.vers_dict()}

    def accrocher(self, role: str, x: float, y: float, rayon: float) -> dict:
        aff = self.affiches.get(role)
        if aff is None:
            raise ErreurAppli("Plan non chargé.")
        r = aff.accrocher(x, y, rayon)
        if r is None:
            return {"aucun": True}
        return {"x": r[0], "y": r[1], "genre": r[2]}

    # ------------------------------------------------------------------ calage
    def caler(self, calage: list, controle: list) -> dict:
        paires_c = [(tuple(p), tuple(q)) for p, q in calage]
        paires_k = [(tuple(p), tuple(q)) for p, q in controle]
        try:
            res = calage_par_points(paires_c, paires_k)
        except Exception as ex:
            raise ErreurAppli(str(ex))
        with self.verrou:
            self.calage = res
            self.paires = (paires_c, paires_k)
        return res.vers_dict()

    def familles(self) -> dict:
        elems = self.elements.get("plan", [])
        presents = {}
        for el in elems:
            presents[el.entite.dxftype()] = presents.get(el.entite.dxftype(), 0) + 1
        out = []
        for lib, types in FAMILLES_SUPPRIMABLES.items():
            n = sum(presents.get(t, 0) for t in types)
            if n:
                out.append({"libelle": lib, "types": types, "n": n,
                            "coche": lib in FAMILLES_PAR_DEFAUT})
        return {"familles": out}

    # ------------------------------------------------------------------ export (indice A)
    def exporter(self, nom_projet: str, prefixe: str, calques_gardes: list, familles: list,
                 zone, renommages: dict | None = None) -> dict:
        with self.verrou:
            if self.calage is None:
                raise ErreurAppli("Faites d'abord le calage.")
            if "plan" not in self.lus:
                raise ErreurAppli("Plan non chargé.")
            if not calques_gardes:
                raise ErreurAppli("Aucun calque gardé : le résultat serait vide.")
            nom_projet = nom_sur(nom_projet, "projet")
            types = [t for lib, ts in FAMILLES_SUPPRIMABLES.items() if lib in familles for t in ts]
            ref = self.fichiers.get("ref")
            recette = construire_recette(
                self.fichiers["plan"], self.lus["plan"], self.elements["plan"], self.calage,
                self.paires[0], self.paires[1], calques_gardes, types, prefixe, zone,
                nom_reference=ref.name if ref else None, renommages=renommages)
            dossier = self.racine / "projets" / nom_projet
            dossier.mkdir(parents=True, exist_ok=True)
            chemin_recette = dossier / "recette.json"
            if chemin_recette.exists():   # on garde l'ancienne version
                shutil.copy2(chemin_recette, dossier / "recette.precedente.json")
            recette.enregistrer(chemin_recette)
            if ref:
                for vieux in dossier.glob("reference.*"):
                    vieux.unlink()
                shutil.copy2(ref, dossier / f"reference{ref.suffix.lower()}")
                self._cache_ref_projet.pop(nom_projet, None)
            return self._appliquer("plan", nom_projet, forcer=False, ecarter_nouveaux=False)

    # ------------------------------------------------------------------ indices suivants
    def projets(self) -> dict:
        out = []
        for d in sorted((self.racine / "projets").iterdir(), key=lambda p: p.name.lower()):
            r = d / "recette.json"
            if not r.exists():
                continue
            try:
                data = json.loads(r.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({"nom": d.name, "cree_le": data.get("cree_le", ""),
                        "plan": data.get("source", {}).get("plan", ""),
                        "reference": data.get("source", {}).get("reference")})
        return {"projets": out}

    def caler_indice(self, projet: str, forcer: bool, ecarter_nouveaux: bool) -> dict:
        with self.verrou:
            if "indice" not in self.fichiers:
                raise ErreurAppli("Déposez d'abord le nouvel indice.")
            return self._appliquer("indice", nom_sur(projet), forcer, ecarter_nouveaux)

    def _appliquer(self, role, projet, forcer, ecarter_nouveaux) -> dict:
        dossier = self.racine / "projets" / projet
        chemin_recette = dossier / "recette.json"
        if not chemin_recette.exists():
            raise ErreurAppli(f"Projet « {projet} » introuvable.")
        plan = self.fichiers[role]
        sortie = self.racine / "sorties" / f"{Path(plan).stem}_MN95.dxf"
        chemin_rapport = sortie.with_suffix(".rapport.json")
        if sortie.exists():
            sortie.unlink()
        rap = appliquer(plan, chemin_recette, sortie, forcer=forcer,
                        ecarter_nouveaux=ecarter_nouveaux, chemin_rapport=chemin_rapport)
        reponse = {"rapport": rap.vers_dict(), "texte": rap.texte(), "projet": projet,
                   "recette": json.loads(chemin_recette.read_text(encoding="utf-8")).get("transformation")}
        if rap.fichier_produit:
            ident = uuid.uuid4().hex[:16]
            self.telechargements[ident] = sortie
            reponse["telecharger"] = f"/telecharger?id={ident}"
            reponse["chemin_sortie"] = str(sortie)
            doc = lire_plan(sortie)
            reponse["resultat"] = PlanAffiche(doc.doc, exploser_document(doc.doc).elements).vers_dict()
        ref = self._reference_projet(projet)
        if ref is not None:
            reponse["reference"] = ref
        return reponse

    def _reference_projet(self, projet):
        if projet in self._cache_ref_projet:
            return self._cache_ref_projet[projet]
        refs = list((self.racine / "projets" / projet).glob("reference.*"))
        if not refs:
            return None
        try:
            p = lire_plan(refs[0])
            g = PlanAffiche(p.doc, exploser_document(p.doc, refs[0].parent).elements).vers_dict()
        except Exception:
            return None
        self._cache_ref_projet[projet] = g
        return g

    def chemin_telechargement(self, ident: str) -> Path | None:
        return self.telechargements.get(ident)
