"""Outils géométriques : similitudes 2D, segments, points d'échantillon, noms de calques."""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

import numpy as np
from ezdxf.math import Matrix44, Vec3


# ------------------------------------------------------------------------------------------
# Similitude 2D :  x' = échelle · R(rotation) · x + translation
# (représentée en nombres complexes : z' = a·z + b, avec a = échelle·e^{iθ})
# ------------------------------------------------------------------------------------------
@dataclass
class Similitude:
    echelle: float = 1.0
    rotation_deg: float = 0.0
    tx: float = 0.0
    ty: float = 0.0

    @property
    def a(self) -> complex:
        return self.echelle * complex(math.cos(math.radians(self.rotation_deg)),
                                      math.sin(math.radians(self.rotation_deg)))

    @property
    def b(self) -> complex:
        return complex(self.tx, self.ty)

    @classmethod
    def depuis_complexes(cls, a: complex, b: complex) -> "Similitude":
        return cls(abs(a), math.degrees(math.atan2(a.imag, a.real)) % 360.0, b.real, b.imag)

    def appliquer(self, pts: np.ndarray) -> np.ndarray:
        """Applique la similitude à un tableau (N,2)."""
        pts = np.asarray(pts, dtype=float).reshape(-1, 2)
        z = pts[:, 0] + 1j * pts[:, 1]
        w = self.a * z + self.b
        return np.column_stack([w.real, w.imag])

    def composer(self, avant: "Similitude") -> "Similitude":
        """self ∘ avant  (on applique d'abord `avant`, puis self)."""
        return Similitude.depuis_complexes(self.a * avant.a, self.a * avant.b + self.b)

    def inverse(self) -> "Similitude":
        ai = 1 / self.a
        return Similitude.depuis_complexes(ai, -ai * self.b)

    def matrice(self) -> Matrix44:
        """Matrice ezdxf (l'échelle s'applique aussi à Z pour rester une similitude 3D)."""
        s = self.echelle
        return Matrix44.chain(Matrix44.scale(s, s, s),
                              Matrix44.z_rotate(math.radians(self.rotation_deg)),
                              Matrix44.translate(self.tx, self.ty, 0))

    def vers_dict(self) -> dict:
        return {"echelle": self.echelle, "rotation_deg": self.rotation_deg,
                "translation": [self.tx, self.ty]}

    @classmethod
    def depuis_dict(cls, d: dict) -> "Similitude":
        return cls(float(d["echelle"]), float(d["rotation_deg"]),
                   float(d["translation"][0]), float(d["translation"][1]))


# ------------------------------------------------------------------------------------------
# Noms de calques
# ------------------------------------------------------------------------------------------
def nom_normalise(nom: str) -> str:
    """Ne garde que les caractères ASCII alphanumériques (en majuscules).

    Les accents sont abîmés à l'export de certains logiciels (« bâtiment » -> « b� timent ») :
    on les retire purement et simplement pour comparer les noms.
    """
    return re.sub(r"[^A-Za-z0-9]", "", nom or "").upper()


def nom_normalise_doux(nom: str) -> str:
    """Variante qui translittère d'abord les accents (é -> e) : utile pour les messages."""
    nfkd = unicodedata.normalize("NFKD", nom or "")
    return nom_normalise("".join(c for c in nfkd if not unicodedata.combining(c)))


# ------------------------------------------------------------------------------------------
# Extraction de points et de segments
# ------------------------------------------------------------------------------------------
def _xy(v) -> tuple[float, float]:
    return (float(v[0]), float(v[1]))


def _ocs_vers_wcs(e, pts):
    try:
        ocs = e.ocs()
        if ocs.transform:
            return [ocs.to_wcs(Vec3(p)) for p in pts]
    except Exception:
        pass
    return [Vec3(p) for p in pts]


def sommets_polyligne(e) -> tuple[list[tuple[float, float]], list[float], bool]:
    """(sommets WCS, bulges, fermée) pour LWPOLYLINE / POLYLINE 2D."""
    t = e.dxftype()
    if t == "LWPOLYLINE":
        brut = list(e.get_points("xyb"))
        pts = [(p[0], p[1], e.dxf.get("elevation", 0.0)) for p in brut]
        bulges = [float(p[2]) for p in brut]
        wcs = _ocs_vers_wcs(e, pts)
        return [_xy(p) for p in wcs], bulges, bool(e.closed)
    if t == "POLYLINE":
        if e.is_2d_polyline:
            pts = [v.dxf.location for v in e.vertices]
            bulges = [float(v.dxf.get("bulge", 0.0)) for v in e.vertices]
            wcs = _ocs_vers_wcs(e, pts)
            return [_xy(p) for p in wcs], bulges, bool(e.is_closed)
        if e.is_3d_polyline:
            pts = [_xy(v.dxf.location) for v in e.vertices]
            return pts, [0.0] * len(pts), bool(e.is_closed)
    return [], [], False


def segments_entite(e) -> list[tuple[float, float, float, float]]:
    """Segments droits d'une entité : LINE et arêtes sans arc des polylignes."""
    t = e.dxftype()
    if t == "LINE":
        s, f = e.dxf.start, e.dxf.end
        return [(s.x, s.y, f.x, f.y)]
    if t in ("LWPOLYLINE", "POLYLINE"):
        pts, bulges, ferme = sommets_polyligne(e)
        n = len(pts)
        out = []
        nb_aretes = n if ferme else n - 1
        for i in range(max(nb_aretes, 0)):
            if abs(bulges[i]) > 1e-9:
                continue
            p, q = pts[i], pts[(i + 1) % n]
            out.append((p[0], p[1], q[0], q[1]))
        return out
    return []


def points_echantillon(e) -> list[tuple[float, float]]:
    """Points caractéristiques d'une entité : sommets, centres, points d'insertion, contours."""
    t = e.dxftype()
    try:
        if t == "LINE":
            return [_xy(e.dxf.start), _xy(e.dxf.end)]
        if t in ("LWPOLYLINE", "POLYLINE"):
            pts, _, _ = sommets_polyligne(e)
            if pts:
                return pts
            if t == "POLYLINE":  # maillages, polyfaces : sommets bruts
                return [_xy(v.dxf.location) for v in e.vertices]
        if t == "CIRCLE":
            return [_xy(_ocs_vers_wcs(e, [e.dxf.center])[0])]
        if t == "ARC":
            c = _ocs_vers_wcs(e, [e.dxf.center])[0]
            return [_xy(c), _xy(e.start_point), _xy(e.end_point)]
        if t == "ELLIPSE":
            return [_xy(e.dxf.center), _xy(e.start_point), _xy(e.end_point)]
        if t == "SPLINE":
            pts = list(e.fit_points) or list(e.control_points)
            return [_xy(p) for p in pts]
        if t in ("TEXT", "ATTRIB", "ATTDEF"):
            return [_xy(_ocs_vers_wcs(e, [e.dxf.insert])[0])]
        if t == "MTEXT":
            return [_xy(e.dxf.insert)]
        if t in ("SOLID", "TRACE", "3DFACE"):
            pts = [e.dxf.get(f"vtx{i}") for i in range(4)]
            pts = [p for p in pts if p is not None]
            if t != "3DFACE":
                pts = _ocs_vers_wcs(e, pts)
            return list(dict.fromkeys(_xy(p) for p in pts))
        if t == "POINT":
            return [_xy(e.dxf.location)]
        if t == "HATCH":
            return _points_hachure(e)
        if t == "INSERT":
            return [_xy(e.dxf.insert)]
        if t == "DIMENSION":
            return [_xy(e.dxf.get("defpoint", (0, 0))), _xy(e.dxf.get("text_midpoint", (0, 0)))]
    except Exception:
        pass
    # repli : centre de l'emprise
    try:
        from ezdxf import bbox
        b = bbox.extents([e], fast=True)
        if b.has_data:
            return [_xy(b.center)]
    except Exception:
        pass
    return []


def _points_hachure(e) -> list[tuple[float, float]]:
    pts = []
    elev = e.dxf.get("elevation", Vec3()).z if e.dxf.hasattr("elevation") else 0.0
    for chemin in e.paths:
        if hasattr(chemin, "vertices"):           # PolylinePath
            pts += [(v[0], v[1], elev) for v in chemin.vertices]
        else:                                      # EdgePath
            for arete in chemin.edges:
                if hasattr(arete, "start") and not hasattr(arete, "center"):
                    pts.append((arete.start[0], arete.start[1], elev))
                    pts.append((arete.end[0], arete.end[1], elev))
                elif hasattr(arete, "center"):
                    pts.append((arete.center[0], arete.center[1], elev))
                elif hasattr(arete, "control_points"):
                    pts += [(p[0], p[1], elev) for p in arete.control_points]
    wcs = _ocs_vers_wcs(e, pts)
    return list(dict.fromkeys(_xy(p) for p in wcs))


def tableau_segments(entites) -> np.ndarray:
    """Tableau (N,4) des segments droits d'une liste d'entités."""
    segs = []
    for e in entites:
        segs.extend(segments_entite(e))
    if not segs:
        return np.zeros((0, 4))
    return np.asarray(segs, dtype=float)


def longueurs(segs: np.ndarray) -> np.ndarray:
    return np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])


def points_segments(segs: np.ndarray) -> np.ndarray:
    """Extrémités des segments, dédoublonnées (au µ près)."""
    if len(segs) == 0:
        return np.zeros((0, 2))
    pts = np.vstack([segs[:, :2], segs[:, 2:]])
    return dedoublonner(pts)


def dedoublonner(pts: np.ndarray, pas: float = 1e-6) -> np.ndarray:
    if len(pts) == 0:
        return pts.reshape(0, 2)
    cle = np.round(pts / pas).astype(np.int64)
    _, idx = np.unique(cle, axis=0, return_index=True)
    return pts[np.sort(idx)]
