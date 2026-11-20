"""Commande « appliquer » : rejouer une recette sur un nouvel indice."""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .appariement import COMPLEXES, exploser_complexe, points_familles
from .controle import controler
from .explosion import exploser_document
from .export import Exporteur
from .geometrie import nom_normalise, tableau_segments
from .lecture import lire_plan
from .recette import UNITES_INSUNITS, Recette

logging.getLogger("ezdxf").setLevel(logging.ERROR)


DECOUPABLES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}


def _points_trait(e, sim):
    """Points (MN95) d'un trait, courbes aplaties à 1 mm. None si impossible."""
    from ezdxf import path as ezpath
    try:
        if e.dxftype() == "POLYLINE" and not (e.is_2d_polyline or e.is_3d_polyline):
            return None
        ch = ezpath.make_path(e)
        pts = np.array([(v.x, v.y) for v in ch.flattening(0.001 / sim.echelle)])
        if len(pts) < 2:
            return None
        return sim.appliquer(pts)
    except Exception:
        return None


def contours_hachure(hachure) -> list:
    """Polylignes (LWPOLYLINE) des contours d'une hachure, mêmes calque / couleur."""
    from ezdxf import path as ezpath
    try:
        chemins = ezpath.from_hatch(hachure)
        attribs = {"layer": hachure.dxf.get("layer", "0"), "color": hachure.dxf.get("color", 256)}
        return list(ezpath.to_lwpolylines(chemins, distance=0.01, segments=8, dxfattribs=attribs))
    except Exception:
        return []


@dataclass
class Rapport:
    """Tout ce qui s'est passé, pour la console et pour le fichier JSON."""
    plan: str = ""
    recette: str = ""
    sortie: str | None = None
    fichier_produit: bool = False
    mode: str = ""
    controle: dict = field(default_factory=dict)
    alertes: list[str] = field(default_factory=list)        # À LIRE ABSOLUMENT
    infos: list[str] = field(default_factory=list)
    xrefs: list[dict] = field(default_factory=list)
    calques_nouveaux: dict = field(default_factory=dict)    # nom -> nb entités
    calques_ecartes: dict = field(default_factory=dict)     # nom -> nb entités
    types_supprimes: dict = field(default_factory=dict)     # type -> nb
    hors_zone: int = 0
    non_transformables: dict = field(default_factory=dict)
    entites_sortie_par_type: dict = field(default_factory=dict)
    entites_sortie_par_calque: dict = field(default_factory=dict)
    nb_entites_plan: int = 0
    nb_entites_sortie: int = 0
    transformation_utilisee: dict = field(default_factory=dict)
    apercu: str | None = None

    def vers_dict(self) -> dict:
        return dict(self.__dict__)

    def enregistrer(self, chemin) -> None:
        Path(chemin).write_text(json.dumps(self.vers_dict(), ensure_ascii=False, indent=2),
                                encoding="utf-8")

    def texte(self) -> str:
        L = []
        barre = "=" * 78
        L.append(barre)
        L.append(f" CALAGE DE « {self.plan} » avec la recette « {self.recette} »")
        L.append(barre)
        if self.mode == "identique":
            L.append(" Mode : REJEU TEL QUEL (même origine que l'indice appris)")
        elif self.mode == "recalé":
            L.append(" " + "!" * 76)
            L.append(" Mode : RECALAGE AUTOMATIQUE — le plan avait bougé par rapport à l'indice appris")
            L.append(" " + "!" * 76)
        elif self.mode == "forcé":
            L.append(" Mode : FORCÉ (contrôle de concordance ignoré à la demande de l'utilisateur)")
        else:
            L.append(" Mode : REFUS — aucun fichier produit")
        msg = self.controle.get("message")
        if msg and not any(msg in a for a in self.alertes):   # pas de doublon avec les alertes
            L.append(f"   {msg}")
        L.append(f"   Repères (ancres) retrouvés tels quels : {self.controle.get('part_ancres_identiques', 0):.0%}")
        if "part_ancres_apres_correction" in self.controle:
            L.append(f"   Repères concordants après correction : {self.controle['part_ancres_apres_correction']:.0%}")
        if self.alertes:
            L.append("")
            L.append(" >>> ALERTES À LIRE <<<")
            for a in self.alertes:
                L.append(f"  /!\\ {a}")
        if self.infos:
            L.append("")
            for i in self.infos:
                L.append(f"  - {i}")
        if self.fichier_produit:
            L.append("")
            L.append(f" Entités : {self.nb_entites_plan} dans le plan (blocs explosés) -> "
                     f"{self.nb_entites_sortie} dans le fichier calé")
            if self.calques_ecartes:
                L.append(f"   Écartées (calques inutiles) : {sum(self.calques_ecartes.values())} "
                         f"sur {len(self.calques_ecartes)} calques")
            if self.types_supprimes:
                L.append("   Supprimées par type : " + ", ".join(
                    f"{_nom_type(t)} {n}" for t, n in sorted(self.types_supprimes.items())))
            if self.hors_zone:
                L.append(f"   Hors de la zone de travail : {self.hors_zone}")
            if self.non_transformables:
                L.append("   NON TRANSFORMABLES (absentes du résultat) : " + ", ".join(
                    f"{t} {n}" for t, n in self.non_transformables.items()))
            L.append("   Contenu du fichier : " + ", ".join(
                f"{_nom_type(t)} {n}" for t, n in sorted(self.entites_sortie_par_type.items())))
            L.append(f" Fichier calé : {self.sortie}")
            if self.apercu:
                L.append(f" Aperçu : {self.apercu}")
        L.append(barre)
        return "\n".join(L)


NOMS_TYPES = {"LINE": "lignes", "LWPOLYLINE": "polylignes", "POLYLINE": "polylignes 3D/anciennes",
              "CIRCLE": "cercles", "ARC": "arcs", "TEXT": "textes", "MTEXT": "textes multilignes",
              "HATCH": "hachures", "DIMENSION": "cotations", "MULTILEADER": "lignes de repère",
              "ATTDEF": "définitions d'attribut", "ATTRIB": "attributs", "SOLID": "surfaces pleines",
              "SPLINE": "courbes spline", "ELLIPSE": "ellipses", "MLINE": "multilignes",
              "POINT": "points"}


def _nom_type(t):
    return NOMS_TYPES.get(t, t)


def appliquer(chemin_plan, chemin_recette, chemin_sortie, ref=None, apercu=None,
              forcer: bool = False, ecarter_nouveaux: bool = False,
              chemin_rapport=None) -> Rapport:
    chemin_plan = Path(chemin_plan)
    recette = Recette.charger(chemin_recette)
    rap = Rapport(plan=chemin_plan.name, recette=Path(chemin_recette).name)

    plan = lire_plan(chemin_plan)
    rap.alertes += plan.avertissements
    ex = exploser_document(plan.doc, chemin_plan.parent)
    elements = ex.elements
    rap.nb_entites_plan = len(elements)

    # --- xrefs et erreurs d'explosion : jamais en silence
    for x in ex.xrefs:
        rap.xrefs.append(dict(x.__dict__))
        if x.statut.startswith("VIDE"):
            rap.alertes.append(
                f"XREF « {x.nom} » VIDE OU INTROUVABLE : son contenu est ABSENT du résultat. "
                f"Fichier attendu : {x.chemin}. Placez-le à côté du plan ou demandez un plan avec xrefs liés.")
    for e in ex.erreurs:
        rap.alertes.append(f"Élément ignoré : {e}")

    # --- unité déclarée
    if plan.insunits and recette.source.get("insunits") and plan.insunits != recette.source["insunits"]:
        rap.alertes.append(
            f"Unité déclarée différente de l'indice appris : "
            f"{UNITES_INSUNITS.get(plan.insunits, plan.insunits)} au lieu de "
            f"{UNITES_INSUNITS.get(recette.source['insunits'], recette.source['insunits'])}.")

    # --- contrôle de concordance AVANT toute chose
    segs = tableau_segments(el.entite for el in elements)
    ctrl = controler(segs, recette, plan.insunits)
    rap.controle = ctrl.vers_dict()
    rap.mode = ctrl.mode
    sim = recette.similitude
    if ctrl.mode == "recalé":
        sim = sim.composer(ctrl.correction)
        rap.alertes.insert(0, ctrl.message)
    elif ctrl.mode == "refus":
        if not forcer:
            rap.alertes.insert(0, ctrl.message)
            if chemin_rapport:
                rap.enregistrer(chemin_rapport)
            return rap
        rap.mode = "forcé"
        rap.alertes.insert(0, "CONTRÔLE IGNORÉ (--forcer) : " + ctrl.message +
                           " Le fichier produit utilise la transformation de l'indice appris SANS garantie.")
    rap.transformation_utilisee = sim.vers_dict()

    # --- nettoyage
    net = recette.nettoyage
    gardes = recette.calques_gardes()
    ecartes = recette.calques_ecartes()
    types_supprimes = set(net.get("types_supprimes", []))
    hachures = "supprimer" if "HATCH" in types_supprimes else net.get("hachures", "garder")
    nb_contours = 0
    types_calque = {nom_normalise(c): set(ts) for c, ts in net.get("types_supprimes_par_calque", {}).items()}
    prefixe = net.get("prefixe_calque", "")
    zone = recette.zone.get("emprise_mn95")
    marge = float(recette.zone.get("marge_m", 0))
    polygone = None
    zone_decoupe = None
    if recette.zone.get("polygone_mn95"):
        from matplotlib.path import Path as MPath
        from .decoupe import Zone
        polygone = MPath(np.asarray(recette.zone["polygone_mn95"], dtype=float))
        if recette.zone.get("decouper", False):
            zone_decoupe = Zone(recette.zone["polygone_mn95"])
    nb_decoupes = 0
    nouveaux_geles = Counter()
    from .vue import etat_calque
    m = sim.matrice()

    exporteur = Exporteur(plan.doc)
    nouveaux, ecartes_cpt, types_cpt = Counter(), Counter(), Counter()
    hors_zone = 0
    for el in elements:
        cn = nom_normalise(el.calque)
        t = el.entite.dxftype()
        if cn in gardes:
            sortie = gardes[cn]["sortie"]
        elif cn in ecartes:
            ecartes_cpt[el.calque] += 1
            continue
        else:
            if etat_calque(plan.doc, el.calque):        # calque nouveau mais gelé / éteint dans le plan
                nouveaux_geles[el.calque] += 1
                continue
            nouveaux[el.calque] += 1
            if ecarter_nouveaux:
                continue
            sortie = prefixe + el.calque
        if t in types_supprimes or (cn in types_calque and t in types_calque[cn]):
            types_cpt[t] += 1
            continue
        if zone_decoupe is not None and t in DECOUPABLES:
            # traits : coupés exactement au bord de la zone
            pts_m = _points_trait(el.entite, sim)
            if pts_m is not None:
                dedans = zone_decoupe.dedans(pts_m)
                if dedans.all() and not zone_decoupe.traverse(pts_m):
                    exporteur.ajouter(el.entite, m, sortie, el.calque)
                    continue
                morceaux = zone_decoupe.decouper(pts_m)
                if not morceaux:
                    hors_zone += 1
                    continue
                for mo in morceaux:
                    exporteur.ajouter_polyligne(mo, sortie, el.calque, el.entite)
                nb_decoupes += 1
                continue
        if zone:
            pts = points_familles(el.entite)
            if pts:
                xy = sim.appliquer(np.array([(p[0], p[1]) for p in pts]))
                if polygone is not None:      # zone dessinée : garder ce qui touche le polygone
                    dedans = polygone.contains_points(xy)
                else:
                    dedans = ((xy[:, 0] >= zone[0] - marge) & (xy[:, 0] <= zone[2] + marge) &
                              (xy[:, 1] >= zone[1] - marge) & (xy[:, 1] <= zone[3] + marge))
                if not dedans.any():
                    hors_zone += 1
                    continue
        if t == "HATCH" and hachures == "contour":
            # la hachure est remplacée par la (les) polyligne(s) de son contour
            lws = contours_hachure(el.entite)
            if not lws:
                exporteur.non_transformables["HATCH"] += 1
            for lw in lws:
                exporteur.ajouter(lw, m, sortie, el.calque)
            nb_contours += 1
        elif t in COMPLEXES:
            enfants = exploser_complexe(el.entite)
            if not enfants:
                exporteur.non_transformables[t] += 1
            for v in enfants:
                exporteur.ajouter(v, m, sortie, el.calque)
        else:
            exporteur.ajouter(el.entite, m, sortie, el.calque)

    rap.calques_nouveaux = dict(nouveaux)
    rap.calques_ecartes = dict(ecartes_cpt)
    rap.types_supprimes = dict(types_cpt)
    rap.hors_zone = hors_zone
    rap.non_transformables = dict(exporteur.non_transformables)
    rap.entites_sortie_par_type = dict(exporteur.compte_types)
    rap.entites_sortie_par_calque = dict(exporteur.compte_calques)
    rap.nb_entites_sortie = sum(exporteur.compte_types.values())

    if nouveaux:
        action = "ÉCARTÉS" if ecarter_nouveaux else "GARDÉS"
        rap.alertes.append(
            f"{len(nouveaux)} calque(s) jamais vu(s) lors de l'apprentissage, {action} : " +
            ", ".join(f"« {c} » ({n})" for c, n in sorted(nouveaux.items())))
    if exporteur.non_transformables:
        rap.alertes.append("Entités non transformables, ABSENTES du résultat : " + ", ".join(
            f"{t} ({n})" for t, n in exporteur.non_transformables.items()))
    if nb_contours:
        rap.infos.append(f"{nb_contours} hachure(s) remplacée(s) par leur contour.")
    if nb_decoupes:
        rap.infos.append(f"{nb_decoupes} trait(s) coupé(s) au bord de la zone.")
    if nouveaux_geles:
        rap.infos.append("Calques nouveaux mais gelés ou éteints dans le plan, écartés : " + ", ".join(
            f"« {c} » ({n})" for c, n in sorted(nouveaux_geles.items())))
    manuels = recette.ajouts_manuels.get("par_calque", {})
    if manuels:
        rap.infos.append("Rappel : éléments ajoutés à la main sur l'indice appris, NON rejoués : " +
                         ", ".join(f"« {c} »" for c in manuels))
    if rap.nb_entites_sortie == 0:
        rap.alertes.append("Le fichier calé est VIDE : vérifiez la recette et le plan.")

    chemin_sortie = Path(chemin_sortie)
    exporteur.enregistrer(chemin_sortie)
    rap.sortie = str(chemin_sortie)
    rap.fichier_produit = True

    if apercu:
        from .apercu import dessiner_apercu
        ents_ref = None
        if ref:
            try:
                pr = lire_plan(ref)
                ents_ref = [x.entite for x in exploser_document(pr.doc, Path(ref).parent).elements]
            except Exception as exc:
                rap.alertes.append(f"Référence illisible pour l'aperçu : {exc}")
        titre = f"{chemin_plan.name} calé (rouge)" + (" sur la référence (gris)" if ents_ref else "") \
                + f" — mode : {rap.mode}"
        dessiner_apercu(apercu, list(exporteur.msp), ents_ref, titre)
        rap.apercu = str(apercu)

    if chemin_rapport:
        rap.enregistrer(chemin_rapport)
    return rap
