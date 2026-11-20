"""État de travail de l'appli et opérations appelées par la page web.

Un seul utilisateur, en local : l'état est gardé en mémoire.
Rangement sur disque (dossier modifiable par la variable d'environnement CALAGE_DOSSIER) :

    ~/CalagePlans/chantiers/<chantier>/reference.dwg|dxf          fond MN95 du chantier
    ~/CalagePlans/chantiers/<chantier>/plans/<type>/recette.json  recette du type de plan
    ~/CalagePlans/chantiers/<chantier>/plans/<type>/indices/<date>_<indice>/
            plan d'origine, DXF calé, rapport.json, info.json
    ~/CalagePlans/travail/                                         fichiers déposés (temporaires)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import shutil
import threading
import uuid
from pathlib import Path

import numpy as np

from calage_core.appariement import IndexCible, apparier, points_denses
from calage_core.application import appliquer
from calage_core.estimation import estimer
from calage_core.explosion import exploser_document
from calage_core.geometrie import tableau_segments
from calage_core.lecture import lire_plan
from calage_core.points import calage_mixte, evaluer
from calage_core.recette import UNITES_INSUNITS
from calage_core.recette_manuelle import (FAMILLES_PAR_DEFAUT, FAMILLES_SUPPRIMABLES,
                                          construire_recette)
from calage_core.vue import PlanAffiche

logging.getLogger("ezdxf").setLevel(logging.ERROR)

class ErreurAppli(Exception):
    """Erreur à afficher telle quelle à l'utilisateur."""


def nom_sur(nom: str, defaut: str = "sans-nom") -> str:
    """Nom de fichier / dossier sans chemin ni caractères gênants."""
    nom = os.path.basename(str(nom or "").replace("\\", "/")).strip().strip(".")
    nom = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nom)
    return nom[:80] or defaut


class Session:
    def __init__(self, racine: str | Path | None = None):
        self.racine = Path(racine or os.environ.get("CALAGE_DOSSIER") or Path.home() / "CalagePlans")
        for d in ("chantiers", "travail"):
            (self.racine / d).mkdir(parents=True, exist_ok=True)
        self.verrou = threading.RLock()
        self.dossiers, self.fichiers, self.lus, self.elements, self.affiches = {}, {}, {}, {}, {}
        self.calage = None
        self.telechargements = {}
        self._cache_geo = {}        # chemin -> (mtime, géométrie)

    # ================================================================== chemins
    def _d_chantier(self, chantier: str) -> Path:
        return self.racine / "chantiers" / nom_sur(chantier)

    def _d_type(self, chantier: str, type_plan: str) -> Path:
        return self._d_chantier(chantier) / "plans" / nom_sur(type_plan)

    def _reference_chantier(self, chantier: str) -> Path | None:
        refs = sorted(self._d_chantier(chantier).glob("reference.*"))
        return refs[0] if refs else None

    def _chemin_indice(self, ident: str) -> Path:
        """ident = « chantier/type/dossier_indice » -> dossier, sans sortir de la racine."""
        morceaux = [nom_sur(m) for m in ident.split("/")]
        if len(morceaux) != 3:
            raise ErreurAppli("Identifiant de plan invalide.")
        d = self.racine / "chantiers" / morceaux[0] / "plans" / morceaux[1] / "indices" / morceaux[2]
        if not d.is_dir():
            raise ErreurAppli("Plan calé introuvable.")
        return d

    # ================================================================== fichiers déposés
    def deposer(self, role: str, nom: str, contenu: bytes) -> dict:
        if role not in ("ref", "plan", "xref", "indice", "indice_xref"):
            raise ErreurAppli(f"Rôle de fichier inconnu : {role}")
        nom = nom_sur(nom, "fichier")
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

    def utiliser_reference_chantier(self, chantier: str) -> dict:
        ref = self._reference_chantier(chantier)
        if ref is None:
            raise ErreurAppli("Ce chantier n'a pas encore de référence : déposez-la.")
        with self.verrou:
            self.fichiers["ref"] = ref
            for dico in (self.lus, self.elements, self.affiches):
                dico.pop("ref", None)
        return self.ouvrir("ref")

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
                    nom_x = Path(x.chemin.replace("\\", "/")).name or x.nom
                    alertes.append(f"Xref « {x.nom} » introuvable : déposez le fichier « {nom_x} » "
                                   "dans « Xrefs du plan ».")
            alertes += ex.erreurs
            return {"nom": chemin.name, "unite": UNITES_INSUNITS.get(plan.insunits, "?"),
                    "nb_entites": len(ex.elements), "xrefs": [x.__dict__ for x in ex.xrefs],
                    "alertes": alertes, "geometrie": aff.vers_dict()}

    def accrocher(self, role: str, x: float, y: float, rayon: float, mode: str = "point") -> dict:
        aff = self.affiches.get(role)
        if aff is None:
            raise ErreurAppli("Plan non chargé.")
        if mode == "axe":
            s = aff.segment_proche(x, y, rayon)
            return {"aucun": True} if s is None else {"segment": s, "genre": "axe"}
        r = aff.accrocher(x, y, rayon)
        return {"aucun": True} if r is None else {"x": r[0], "y": r[1], "genre": r[2]}

    # ================================================================== calage
    def caler(self, elements: list, auto: bool = False) -> dict:
        try:
            if auto:
                if self.calage is None or self.calage.methode != "automatique":
                    raise ErreurAppli("Pas de calage automatique en cours.")
                res = evaluer(self.calage.similitude, elements, "automatique",
                              self.calage.echelle_brute, self.calage.confiance)
            else:
                res = calage_mixte(elements)
        except ErreurAppli:
            raise
        except Exception as ex:
            raise ErreurAppli(str(ex))
        with self.verrou:
            self.calage = res
        return res.vers_dict()

    def calage_auto(self, chantier: str | None, ids: list[str] | None = None,
                    avec_reference: bool = True) -> dict:
        """Cherche le calage en comparant le plan à des plans déjà calés du chantier et/ou à la référence.

        ids : plans calés choisis (« chantier/type/indice ») ; None = le dernier indice de chaque type.
        """
        with self.verrou:
            if "plan" not in self.elements:
                raise ErreurAppli("Plan non chargé.")
            if ids is None and chantier:
                ids = []
                for d_type in sorted((self._d_chantier(chantier) / "plans").glob("*")):
                    dernier = self._dernier_indice(d_type)
                    if dernier is not None:
                        ids.append(f"{self._d_chantier(chantier).name}/{d_type.name}/{dernier.name}")
            cibles, sources = [], []
            for ident in ids or []:
                d = self._chemin_indice(ident)
                info = json.loads((d / "info.json").read_text(encoding="utf-8"))
                sortie = d / info["fichier_sortie"]
                if sortie.exists():
                    ex = exploser_document(lire_plan(sortie).doc)
                    cibles.append(tableau_segments(e.entite for e in ex.elements))
                    sources.append(f"{ident.split('/')[1]} ({info.get('indice') or info['plan']})")
            if avec_reference and "ref" in self.elements:
                cibles.append(tableau_segments(e.entite for e in self.elements["ref"]))
                sources.append("référence")
            if not cibles:
                raise ErreurAppli("Rien à comparer : chargez la référence ou calez d'abord un autre plan du chantier.")
            segs_cible = np.vstack([c for c in cibles if len(c)]) if any(len(c) for c in cibles) else np.zeros((0, 4))
            segs_plan = tableau_segments(e.entite for e in self.elements["plan"])
            cal = estimer(segs_plan, segs_cible)
            if cal is None:
                raise ErreurAppli("Aucun calage automatique trouvé : calez à la main (points ou axes).")
            v, i = cal.vote, cal.icp
            dom = min(v.dominance, 99.0)
            if dom >= 2 and v.score >= 15 and i.nb_points >= 30 and i.ecart_moyen < 0.01:
                niveau = "forte"
            elif dom >= 1.5 and v.score >= 8 and i.nb_points >= 15 and i.ecart_moyen < 0.02:
                niveau = "moyenne"
            else:
                niveau = "faible"
            confiance = {"niveau": niveau, "points_concordants": i.nb_points,
                         "ecart_moyen_mm": round(i.ecart_moyen * 1000, 1), "votes": v.score,
                         "dominance": round(dom, 1), "compare_avec": sources}
            res = evaluer(cal.similitude, [], "automatique", abs(cal.icp.similitude.echelle), confiance)
            res.verdict = "sans contrôle"
            res.message = {
                "forte": "Calage automatique trouvé, confiance FORTE. Vérifiez la superposition et "
                         "ajoutez un point de contrôle.",
                "moyenne": "Calage automatique trouvé, confiance MOYENNE : vérifiez soigneusement "
                           "avec un ou deux points de contrôle.",
                "faible": "Calage automatique INCERTAIN : probablement faux. Calez à la main.",
            }[niveau]
            self.calage = res
            return res.vers_dict()

    def familles(self) -> dict:
        presents = {}
        for el in self.elements.get("plan", []):
            presents[el.entite.dxftype()] = presents.get(el.entite.dxftype(), 0) + 1
        out = []
        for lib, types in FAMILLES_SUPPRIMABLES.items():
            n = sum(presents.get(t, 0) for t in types)
            if n:
                out.append({"libelle": lib, "types": types, "n": n, "coche": lib in FAMILLES_PAR_DEFAUT})
        return {"familles": out}

    # ================================================================== chantiers
    def chantiers(self) -> dict:
        out = []
        for d in sorted((self.racine / "chantiers").iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir():
                continue
            ref = self._reference_chantier(d.name)
            types = []
            for t in sorted((d / "plans").glob("*"), key=lambda p: p.name.lower()):
                r = t / "recette.json"
                if not r.exists():
                    continue
                data = json.loads(r.read_text(encoding="utf-8"))
                indices = []
                for di in sorted((t / "indices").glob("*"), reverse=True):
                    try:
                        info = json.loads((di / "info.json").read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    indices.append({"id": f"{d.name}/{t.name}/{di.name}", **{
                        k: info.get(k) for k in ("indice", "date", "plan", "mode", "nb_entites")}})
                types.append({"type": t.name, "cree_le": data.get("cree_le", ""),
                              "plan": data.get("source", {}).get("plan", ""), "indices": indices})
            out.append({"nom": d.name, "reference": ref.name if ref else None, "types": types})
        # liste des types : uniquement ceux que l'utilisateur a déjà créés (tous chantiers confondus)
        tous_types = sorted({t["type"] for c in out for t in c["types"]}, key=str.lower)
        return {"chantiers": out, "types_existants": tous_types}

    def _dernier_indice(self, d_type: Path) -> Path | None:
        ind = sorted((d_type / "indices").glob("*/info.json"))
        return ind[-1].parent if ind else None

    # ================================================================== export (premier plan)
    def exporter(self, chantier: str, type_plan: str, indice: str, prefixe: str,
                 calques_gardes: list, familles: list, zone, renommages: dict | None = None) -> dict:
        with self.verrou:
            if self.calage is None:
                raise ErreurAppli("Faites d'abord le calage.")
            if "plan" not in self.lus:
                raise ErreurAppli("Plan non chargé.")
            if not calques_gardes:
                raise ErreurAppli("Aucun calque gardé : le résultat serait vide.")
            if not (chantier or "").strip() or not (type_plan or "").strip():
                raise ErreurAppli("Indiquez le chantier et le type de plan.")
            types = [t for lib, ts in FAMILLES_SUPPRIMABLES.items() if lib in familles for t in ts]
            d_ch = self._d_chantier(chantier)
            d_ch.mkdir(parents=True, exist_ok=True)
            ref = self.fichiers.get("ref")
            if ref and ref.parent != d_ch:          # nouvelle référence -> devient celle du chantier
                for vieux in d_ch.glob("reference.*"):
                    vieux.unlink()
                shutil.copy2(ref, d_ch / f"reference{ref.suffix.lower()}")
                self.fichiers["ref"] = d_ch / f"reference{ref.suffix.lower()}"
            recette = construire_recette(
                self.fichiers["plan"], self.lus["plan"], self.elements["plan"], self.calage,
                calques_gardes, types, prefixe, zone,
                nom_reference=ref.name if ref else None, renommages=renommages,
                chantier=d_ch.name, type_plan=nom_sur(type_plan))
            d_t = self._d_type(chantier, type_plan)
            d_t.mkdir(parents=True, exist_ok=True)
            chemin_recette = d_t / "recette.json"
            if chemin_recette.exists():   # on archive l'ancienne recette
                horo = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                shutil.copy2(chemin_recette, d_t / f"recette.{horo}.json")
            recette.enregistrer(chemin_recette)
            return self._appliquer("plan", chantier, type_plan, indice, False, False)

    # ================================================================== indices suivants
    def caler_indice(self, chantier, type_plan, indice, forcer, ecarter_nouveaux) -> dict:
        with self.verrou:
            if "indice" not in self.fichiers:
                raise ErreurAppli("Déposez d'abord le nouvel indice.")
            return self._appliquer("indice", chantier, type_plan, indice, forcer, ecarter_nouveaux)

    def _appliquer(self, role, chantier, type_plan, indice, forcer, ecarter_nouveaux) -> dict:
        d_t = self._d_type(chantier, type_plan)
        chemin_recette = d_t / "recette.json"
        if not chemin_recette.exists():
            raise ErreurAppli(f"Type de plan « {type_plan} » introuvable pour le chantier « {chantier} ».")
        plan = self.fichiers[role]
        horo = dt.datetime.now()
        d_i = d_t / "indices" / f"{horo:%Y%m%d-%H%M%S}_{nom_sur(indice or Path(plan).stem)}"
        d_i.mkdir(parents=True)
        sortie = d_i / f"{Path(plan).stem}_MN95.dxf"
        rap = appliquer(plan, chemin_recette, sortie, forcer=forcer,
                        ecarter_nouveaux=ecarter_nouveaux, chemin_rapport=d_i / "rapport.json")
        try:   # copie du plan d'origine (pour pouvoir comparer / refaire plus tard)
            shutil.copy2(plan, d_i / Path(plan).name)
        except Exception:
            pass
        info = {"indice": indice or "", "date": horo.isoformat(timespec="seconds"),
                "plan": Path(plan).name, "mode": rap.mode, "fichier_produit": rap.fichier_produit,
                "fichier_sortie": sortie.name if rap.fichier_produit else None,
                "nb_entites": rap.nb_entites_sortie}
        if not rap.fichier_produit:
            # un refus n'est pas un indice calé : on garde le rapport à part
            refus = d_t / "refus"
            refus.mkdir(exist_ok=True)
            shutil.move(str(d_i), str(refus / d_i.name))
            d_i = refus / d_i.name
        else:
            (d_i / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        reponse = {"rapport": rap.vers_dict(), "texte": rap.texte(), "chantier": nom_sur(chantier),
                   "type_plan": nom_sur(type_plan)}
        if rap.fichier_produit:
            ident = uuid.uuid4().hex[:16]
            self.telechargements[ident] = d_i / sortie.name
            reponse["telecharger"] = f"/telecharger?id={ident}"
            reponse["chemin_sortie"] = str(d_i / sortie.name)
            reponse["resultat"] = self._geometrie(d_i / sortie.name)
            reponse["id"] = f"{nom_sur(chantier)}/{nom_sur(type_plan)}/{d_i.name}"
        ref = self._reference_chantier(chantier)
        if ref is not None:
            reponse["reference"] = self._geometrie(ref)
        return reponse

    def _geometrie(self, chemin: Path, elements=None):
        cle = str(chemin)
        mt = chemin.stat().st_mtime
        if elements is None and cle in self._cache_geo and self._cache_geo[cle][0] == mt:
            return self._cache_geo[cle][1]
        p = lire_plan(chemin)
        g = PlanAffiche(p.doc, elements if elements is not None else
                        exploser_document(p.doc, chemin.parent).elements).vers_dict()
        if elements is None:
            self._cache_geo[cle] = (mt, g)
        return g

    # ================================================================== comparaison
    def comparer(self, ids: list[str], mode: str) -> dict:
        if not ids:
            raise ErreurAppli("Choisissez au moins un plan calé.")
        plans = []
        for ident in ids:
            d = self._chemin_indice(ident)
            info = json.loads((d / "info.json").read_text(encoding="utf-8"))
            plans.append((ident, d / info["fichier_sortie"], info))
        chantier = ids[0].split("/")[0]
        ref = self._reference_chantier(chantier)
        rep = {"reference": self._geometrie(ref) if ref else None}
        if mode == "differences":
            if len(plans) != 2:
                raise ErreurAppli("Pour voir les différences, choisissez exactement 2 plans.")
            rep.update(self._differences(plans[0], plans[1]))
        else:
            rep["calques"] = [{"id": i, "nom": f"{i.split('/')[1]} — {inf.get('indice') or inf['plan']}",
                               "geometrie": self._geometrie(f)} for i, f, inf in plans]
        return rep

    def _differences(self, ancien, nouveau, tol: float = 0.01) -> dict:
        """Objets inchangés / ajoutés (dans le nouveau seulement) / supprimés (dans l'ancien seulement)."""
        (_, f_a, inf_a), (_, f_b, inf_b) = ancien, nouveau
        doc_a, doc_b = lire_plan(f_a).doc, lire_plan(f_b).doc
        ea, eb = exploser_document(doc_a).elements, exploser_document(doc_b).elements
        ia, ib = IndexCible.construire(ea, points_denses), IndexCible.construire(eb, points_denses)
        inchanges, ajoutes, supprimes = [], [], []
        par_calque = {}
        for el in eb:
            pts = points_denses(el.entite)
            (inchanges if (not pts or apparier(pts, ia, None, tol).retrouve) else ajoutes).append(el)
        for el in ea:
            pts = points_denses(el.entite)
            if pts and not apparier(pts, ib, None, tol).retrouve:
                supprimes.append(el)
        for genre, liste in (("ajoutes", ajoutes), ("supprimes", supprimes)):
            for el in liste:
                par_calque.setdefault(el.calque, {"ajoutes": 0, "supprimes": 0})[genre] += 1

        def geo(doc, els):
            return PlanAffiche(doc, els).vers_dict() if els else None

        return {
            "ancien": inf_a.get("indice") or inf_a["plan"], "nouveau": inf_b.get("indice") or inf_b["plan"],
            "inchanges": geo(doc_b, inchanges), "ajoutes": geo(doc_b, ajoutes),
            "supprimes": geo(doc_a, supprimes),
            "compte": {"inchanges": len(inchanges), "ajoutes": len(ajoutes), "supprimes": len(supprimes)},
            "par_calque": dict(sorted(par_calque.items(), key=lambda kv: -sum(kv[1].values()))),
        }

    def chemin_telechargement(self, ident: str) -> Path | None:
        return self.telechargements.get(ident)

    def telecharger_indice(self, ident: str) -> Path:
        d = self._chemin_indice(ident)
        info = json.loads((d / "info.json").read_text(encoding="utf-8"))
        return d / info["fichier_sortie"]
