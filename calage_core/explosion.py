"""Explosion récursive des blocs et des xrefs.

Toutes les entités du modèle sont « aplaties » en entités virtuelles (copies hors document,
en coordonnées du plan) : les tablettes de chantier n'affichent pas les blocs.
Règles :
  * une entité dessinée sur le calque « 0 » d'un bloc prend le calque de l'INSERT ;
  * couleur / type de ligne « DUBLOC » (BYBLOCK) prennent ceux de l'INSERT ;
  * les attributs (ATTRIB) d'un INSERT sont conservés comme entités ATTRIB
    (la recette décidera de les supprimer ou non) ;
  * xref vide ou non résolu : on cherche le fichier à côté du plan ; sinon AVERTISSEMENT FORT.
"""
from __future__ import annotations

import ntpath
from dataclasses import dataclass, field
from pathlib import Path

from ezdxf.entities import DXFGraphic

PROFONDEUR_MAX = 30  # garde-fou contre les blocs récursifs


@dataclass
class Element:
    """Une entité aplatie du plan."""
    entite: DXFGraphic        # entité virtuelle (copie), coordonnées du plan source
    calque: str               # calque effectif après héritage
    origine: str = "modèle"   # "modèle", "bloc:NOM", "xref:NOM"


@dataclass
class InfoXref:
    nom: str
    chemin: str
    statut: str              # "contenu présent", "résolu depuis le disque", "VIDE / NON RÉSOLU"
    nb_entites: int = 0


@dataclass
class ResultatExplosion:
    elements: list[Element] = field(default_factory=list)
    xrefs: list[InfoXref] = field(default_factory=list)
    erreurs: list[str] = field(default_factory=list)   # entités impossibles à exploser
    nb_inserts_explose: int = 0


def _est_xref(bloc_layout) -> bool:
    br = bloc_layout.block_record
    try:
        if br.is_xref or br.is_xref_overlay:
            return True
    except AttributeError:
        pass
    blk = bloc_layout.block
    return bool(blk is not None and blk.dxf.get("xref_path"))


def _chercher_fichier_xref(chemin_xref: str, dossier_plan: Path | None) -> Path | None:
    """Cherche le fichier d'un xref : chemin absolu, puis même nom à côté du plan."""
    if not chemin_xref:
        return None
    candidats = [Path(chemin_xref)]
    if dossier_plan is not None:
        nom = ntpath.basename(chemin_xref)  # gère les chemins Windows
        base = Path(nom).stem
        candidats += [dossier_plan / nom, dossier_plan / f"{base}.dxf", dossier_plan / f"{base}.dwg"]
    for c in candidats:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def exploser_document(doc, dossier_plan: Path | None = None) -> ResultatExplosion:
    """Aplatit tout l'espace objet du document."""
    res = ResultatExplosion()
    xrefs_externes: dict[str, list[Element] | None] = {}

    # --- inventaire des xrefs -------------------------------------------------------------
    for bloc in doc.blocks:
        if not _est_xref(bloc):
            continue
        chemin = bloc.block.dxf.get("xref_path", "") if bloc.block is not None else ""
        n = len(bloc)
        if n > 0:
            res.xrefs.append(InfoXref(bloc.name, chemin, "contenu présent", n))
            continue
        # xref vide : tenter de lire le fichier
        fichier = _chercher_fichier_xref(chemin, dossier_plan)
        elems = None
        if fichier is not None:
            try:
                from .lecture import lire_plan
                sous = lire_plan(fichier)
                sous_res = exploser_document(sous.doc, fichier.parent)
                elems = []
                for el in sous_res.elements:
                    # nommage identique à une liaison AutoCAD : XREF$0$CALQUE
                    calque = el.calque if el.calque == "0" else f"{bloc.name}$0${el.calque}"
                    el.entite.dxf.layer = calque
                    elems.append(Element(el.entite, calque, f"xref:{bloc.name}"))
            except Exception as ex:  # fichier présent mais illisible
                res.erreurs.append(f"xref « {bloc.name} » trouvé ({fichier}) mais illisible : {ex}")
                elems = None
        xrefs_externes[bloc.name] = elems
        if elems:
            res.xrefs.append(InfoXref(bloc.name, chemin, "résolu depuis le disque", len(elems)))
        else:
            res.xrefs.append(InfoXref(bloc.name, chemin, "VIDE / NON RÉSOLU", 0))

    # --- aplatissement --------------------------------------------------------------------
    for e in doc.modelspace():
        _aplatir(e, res, xrefs_externes, "modèle", 0)
    return res


def _heriter(v, insert) -> None:
    """Applique les règles d'héritage calque 0 / DUBLOC."""
    if v.dxf.get("layer", "0") == "0":
        v.dxf.layer = insert.dxf.get("layer", "0")
    if v.dxf.get("color", 256) == 0:          # BYBLOCK
        v.dxf.color = insert.dxf.get("color", 256)
    if str(v.dxf.get("linetype", "BYLAYER")).upper() == "BYBLOCK":
        v.dxf.linetype = insert.dxf.get("linetype", "BYLAYER")


def _aplatir(e, res: ResultatExplosion, xrefs_externes, origine: str, profondeur: int) -> None:
    t = e.dxftype()
    if t != "INSERT":
        # copie pour que les transformations ultérieures ne touchent pas le document source
        try:
            c = e.copy() if e.doc is not None else e
        except Exception:
            c = e
        res.elements.append(Element(c, c.dxf.get("layer", "0"), origine))
        return

    if profondeur > PROFONDEUR_MAX:
        res.erreurs.append(f"bloc « {e.dxf.name} » imbriqué trop profondément : ignoré")
        return
    nom = e.dxf.name
    res.nb_inserts_explose += 1
    sous_origine = origine if origine != "modèle" else (
        f"xref:{nom}" if nom in xrefs_externes or _est_xref_nom(e) else f"bloc:{nom}")

    # attributs attachés
    for att in getattr(e, "attribs", []):
        try:
            c = att.copy()
            res.elements.append(Element(c, c.dxf.get("layer", "0"), sous_origine))
        except Exception:
            pass

    # xref externe chargé depuis le disque : transformer ses entités par la matrice de l'INSERT
    if nom in xrefs_externes:
        elems = xrefs_externes[nom]
        if not elems:
            return  # déjà signalé « VIDE / NON RÉSOLU »
        m = e.matrix44()
        for el in elems:
            try:
                c = el.entite.copy()
                c.transform(m)
                _heriter(c, e)
                res.elements.append(Element(c, c.dxf.layer, sous_origine))
            except Exception as ex:
                res.erreurs.append(f"{el.entite.dxftype()} du xref « {nom} » non transformable : {ex}")
        return

    try:
        virtuelles = list(e.virtual_entities())
    except Exception as ex:
        res.erreurs.append(f"bloc « {nom} » impossible à exploser : {ex}")
        return
    for v in virtuelles:
        _heriter(v, e)
        _aplatir(v, res, xrefs_externes, sous_origine, profondeur + 1)


def _est_xref_nom(insert) -> bool:
    doc = insert.doc
    if doc is None:
        return False
    b = doc.blocks.get(insert.dxf.name)
    return b is not None and _est_xref(b)
