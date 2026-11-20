"""Tests unitaires du moteur géométrique (sans fichiers réels)."""
import math

import numpy as np

from calage_core.estimation import estimer, snap_echelle
from calage_core.geometrie import Similitude, nom_normalise


def test_similitude_inverse_et_composition():
    s = Similitude(0.01, 121.4, 2510000.0, 1140000.0)
    p = np.array([[100.0, -200.0], [5000.0, 3000.0]])
    assert np.allclose(s.inverse().appliquer(s.appliquer(p)), p)
    d = Similitude(1, 10, 3, -4)
    assert np.allclose(s.composer(d).appliquer(p), s.appliquer(d.appliquer(p)))


def test_nom_normalise_accents_abimes():
    assert nom_normalise("RS_Emprise bâtiment") == nom_normalise("RS_Emprise b� timent")
    assert nom_normalise("Divers - Axes") == nom_normalise("Divers-Axes")


def test_snap():
    assert snap_echelle(0.00997) == 0.01
    assert snap_echelle(1012) == 1000.0


def test_estimation_synthetique():
    rnd = np.random.default_rng(1)
    n = 300
    p = rnd.uniform(-5000, 5000, (n, 2))
    ang = rnd.uniform(0, 2 * math.pi, n)
    L = rnd.uniform(100, 2000, n)
    segs = np.hstack([p, p + np.column_stack([L * np.cos(ang), L * np.sin(ang)])])
    vraie = Similitude(0.01, 37.25, 2500000.0, 1100000.0)
    cible = np.hstack([vraie.appliquer(segs[:, :2]), vraie.appliquer(segs[:, 2:])])
    cal = estimer(segs, cible)
    s = cal.similitude
    assert s.echelle == 0.01
    assert abs(s.rotation_deg - 37.25) < 1e-6
    assert abs(s.tx - vraie.tx) < 1e-4 and abs(s.ty - vraie.ty) < 1e-4
