"""Découpe des traits au bord d'une zone polygonale (coordonnées MN95).

Un trait qui sort de la zone est coupé exactement sur le contour : seule la partie intérieure
est gardée. Les courbes (arcs, cercles, splines) sont d'abord converties en polylignes fines
(1 mm), uniquement lorsqu'elles traversent le contour.
"""
from __future__ import annotations

import numpy as np
from matplotlib.path import Path as MPath


class Zone:
    def __init__(self, polygone):
        self.poly = np.asarray(polygone, dtype=float)
        if len(self.poly) >= 2 and np.allclose(self.poly[0], self.poly[-1]):
            self.poly = self.poly[:-1]
        self.chemin = MPath(self.poly)
        self.a = self.poly
        self.b = np.roll(self.poly, -1, axis=0)

    def dedans(self, pts: np.ndarray) -> np.ndarray:
        return self.chemin.contains_points(np.asarray(pts, dtype=float).reshape(-1, 2))

    def _coupures(self, p, q) -> list[float]:
        """Paramètres t ∈ ]0,1[ où le segment p→q croise le contour."""
        d = q - p
        e = self.b - self.a
        den = d[0] * e[:, 1] - d[1] * e[:, 0]
        ok = np.abs(den) > 1e-15
        w = self.a - p
        t = np.full(len(e), np.nan)
        u = np.full(len(e), np.nan)
        t[ok] = (w[ok, 0] * e[ok, 1] - w[ok, 1] * e[ok, 0]) / den[ok]
        u[ok] = (w[ok, 0] * d[1] - w[ok, 1] * d[0]) / den[ok]
        sel = ok & (t > 1e-12) & (t < 1 - 1e-12) & (u >= -1e-12) & (u <= 1 + 1e-12)
        return sorted(t[sel].tolist())

    def decouper(self, pts: np.ndarray) -> list[np.ndarray]:
        """Morceaux (polylignes) d'une polyligne situés DANS la zone."""
        pts = np.asarray(pts, dtype=float)
        morceaux, courant = [], []
        for i in range(len(pts) - 1):
            p, q = pts[i], pts[i + 1]
            ts = [0.0] + self._coupures(p, q) + [1.0]
            for t0, t1 in zip(ts[:-1], ts[1:]):
                if t1 - t0 < 1e-12:
                    continue
                a, b = p + (q - p) * t0, p + (q - p) * t1
                milieu = (a + b) / 2
                if self.chemin.contains_point(milieu):
                    if courant and np.allclose(courant[-1], a, atol=1e-9):
                        courant.append(b)
                    else:
                        if len(courant) >= 2:
                            morceaux.append(np.asarray(courant))
                        courant = [a, b]
                else:
                    if len(courant) >= 2:
                        morceaux.append(np.asarray(courant))
                    courant = []
        if len(courant) >= 2:
            morceaux.append(np.asarray(courant))
        return morceaux

    def traverse(self, pts: np.ndarray) -> bool:
        """Vrai si la polyligne coupe le contour (même si aucun sommet n'est dedans)."""
        pts = np.asarray(pts, dtype=float)
        return any(self._coupures(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
