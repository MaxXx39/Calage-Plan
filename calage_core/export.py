"""Écriture du DXF de sortie (R2000, mètres, blocs explosés, accents corrects)."""
from __future__ import annotations

import logging
from collections import Counter

import ezdxf
from ezdxf.addons import Importer

log = logging.getLogger(__name__)

TYPES_COPIABLES = {"POINT", "LINE", "CIRCLE", "ARC", "ELLIPSE", "LWPOLYLINE", "SPLINE",
                   "POLYLINE", "3DFACE", "SOLID", "TRACE", "TEXT", "MTEXT", "HATCH"}


class Exporteur:
    """Construit le document de sortie au fil de l'eau."""

    def __init__(self, doc_source):
        self.src = doc_source
        self.doc = ezdxf.new("R2000", setup=False)
        self.doc.header["$INSUNITS"] = 6          # mètres
        self.doc.header["$MEASUREMENT"] = 1       # métrique
        self.msp = self.doc.modelspace()
        self.non_transformables = Counter()
        self.compte_types = Counter()
        self.compte_calques = Counter()
        self._importer_tables()

    def _importer_tables(self):
        """Seulement types de ligne et styles de texte (pas de styles de cote)."""
        try:
            imp = Importer(self.src, self.doc)
            imp.import_tables(("linetypes", "styles"))
            imp.finalize()
        except Exception as ex:  # pragma: no cover - tables exotiques
            log.warning("import des tables incomplet : %s", ex)

    def assurer_calque(self, nom_sortie: str, nom_source: str | None):
        if nom_sortie in self.doc.layers:
            return
        attribs = {}
        src = self.src.layers.get(nom_source) if nom_source and nom_source in self.src.layers else None
        if src is not None:
            attribs["color"] = abs(src.dxf.get("color", 7)) or 7
            lt = src.dxf.get("linetype", "Continuous")
            attribs["linetype"] = lt if lt in self.doc.linetypes else "Continuous"
            lw = src.dxf.get("lineweight", -3)
            attribs["lineweight"] = lw
            if src.dxf.get("true_color") is not None:
                attribs["true_color"] = src.dxf.true_color
        lay = self.doc.layers.add(nom_sortie, **{k: v for k, v in attribs.items() if k != "true_color"})
        if "true_color" in attribs:
            lay.dxf.true_color = attribs["true_color"]
        if src is not None and src.is_off():
            lay.off()

    def ajouter(self, e, matrice, calque_sortie: str, calque_source: str | None) -> bool:
        """Copie + transforme une entité simple. Renvoie False si impossible."""
        t = e.dxftype()
        try:
            if t in ("ATTRIB", "ATTDEF"):
                c = self._texte_depuis_attribut(e)
            elif t in TYPES_COPIABLES:
                c = e.copy()
            else:
                self.non_transformables[t] += 1
                return False
            c.transform(matrice)
            c.dxf.layer = calque_sortie
            self.assurer_calque(calque_sortie, calque_source)
            self.msp.add_foreign_entity(c, copy=False)
        except Exception as ex:
            log.debug("entité %s non transformable : %s", t, ex)
            self.non_transformables[t] += 1
            return False
        self.compte_types[t if t not in ("ATTRIB", "ATTDEF") else "TEXT"] += 1
        self.compte_calques[calque_sortie] += 1
        return True

    def ajouter_polyligne(self, points_mn95, calque_sortie: str, calque_source: str | None, modele=None):
        """Ajoute une polyligne déjà en coordonnées MN95 (morceau d'un trait découpé)."""
        attribs = {"layer": calque_sortie}
        if modele is not None:
            attribs["color"] = modele.dxf.get("color", 256)
            lt = modele.dxf.get("linetype", "BYLAYER")
            if lt in self.doc.linetypes or lt.upper() in ("BYLAYER", "BYBLOCK"):
                attribs["linetype"] = lt
            if modele.dxf.hasattr("lineweight"):
                attribs["lineweight"] = modele.dxf.lineweight
        self.assurer_calque(calque_sortie, calque_source)
        self.msp.add_lwpolyline([(float(x), float(y)) for x, y in points_mn95], dxfattribs=attribs)
        self.compte_types["LWPOLYLINE"] += 1
        self.compte_calques[calque_sortie] += 1

    @staticmethod
    def _texte_depuis_attribut(att):
        """Un attribut isolé n'est pas valide hors bloc : on le convertit en TEXT."""
        from ezdxf.entities import Text
        attribs = {k: att.dxf.get(k) for k in ("insert", "height", "rotation", "style", "halign",
                                               "valign", "align_point", "width", "oblique",
                                               "color", "linetype", "extrusion")
                   if att.dxf.hasattr(k)}
        attribs["text"] = att.dxf.get("text", "")
        return Text.new(dxfattribs=attribs)

    def enregistrer(self, chemin):
        self.doc.saveas(chemin, encoding="cp1252")
