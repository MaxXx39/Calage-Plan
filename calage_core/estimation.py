"""Estimation de la similitude plan -> cible par vote sur segments puis ICP.

Étapes :
 1. Vote : pour chaque échelle candidate (puissances de 10), on apparie des segments de même
    longueur (tolérance 5 mm + 0,05 %), dans les deux sens. Chaque paire propose une rotation
    et une translation ; on compte les propositions par cellule (0,1° ; 0,25 m) en lissant
    avec les cellules voisines. La meilleure cellule toutes échelles confondues gagne.
 2. ICP (« plus proches points itérés ») en moindres carrés complexes, en ne gardant que les
    points déjà proches, avec des seuils décroissants 1 m -> 3 cm. Échelle libre d'abord,
    puis échelle « snappée » sur la puissance de 10 la plus proche et fixée.

Les distances et tolérances sont exprimées dans les unités de la CIBLE (mètres MN95 en général).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from .geometrie import Similitude, longueurs, points_segments

ECHELLES_CANDIDATES = (1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0, 1000.0)
SEUILS_ICP = (1.0, 0.5, 0.2, 0.1, 0.05, 0.03)


@dataclass
class ParametresVote:
    tol_abs: float = 0.005          # 5 mm
    tol_rel: float = 0.0005         # 0,05 %
    longueur_min: float = 0.5       # segments plus courts ignorés (unités cible)
    nb_max_segments: int = 900      # on garde les plus longs
    max_candidats: int = 60         # longueurs trop banales ignorées
    pas_angle_deg: float = 0.1
    pas_translation: float = 0.25


@dataclass
class ResultatVote:
    echelle: float
    similitude: Similitude
    score: float                      # votes lissés de la meilleure cellule
    score_second: float = 0.0         # meilleure cellule d'une AUTRE solution (contrôle d'ambiguïté)
    scores_par_echelle: dict = field(default_factory=dict)

    @property
    def dominance(self) -> float:
        """Rapport meilleur / second : > 2 = solution nette."""
        return self.score / self.score_second if self.score_second > 0 else float("inf")


@dataclass
class ResultatICP:
    similitude: Similitude
    nb_points: int                    # points appariés au dernier seuil
    ecart_moyen: float                # en unités cible
    ecart_max: float
    part_1cm: float                   # part des points source à < 1 cm (en unités cible)
    historique: list = field(default_factory=list)


def _preparer(segs: np.ndarray, echelle: float, p: ParametresVote):
    """Filtre les segments (longueur min à l'échelle) et garde les plus longs."""
    if len(segs) == 0:
        return segs, np.zeros(0)
    L = longueurs(segs) * echelle
    ok = L >= p.longueur_min
    segs, L = segs[ok], L[ok]
    if len(segs) > p.nb_max_segments:
        idx = np.argsort(-L)[: p.nb_max_segments]
        segs, L = segs[idx], L[idx]
    return segs, L


def voter(segs_src: np.ndarray, segs_cible: np.ndarray,
          echelles=ECHELLES_CANDIDATES, p: ParametresVote | None = None) -> ResultatVote | None:
    """Vote rotation/translation pour chaque échelle candidate ; renvoie le meilleur."""
    p = p or ParametresVote()
    cible, Lc = _preparer(segs_cible, 1.0, p)
    if len(cible) == 0:
        return None
    ordre = np.argsort(Lc)
    Lc_tri = Lc[ordre]
    cible_tri = cible[ordre]
    ang_c = np.arctan2(cible_tri[:, 3] - cible_tri[:, 1], cible_tri[:, 2] - cible_tri[:, 0])
    mil_c = (cible_tri[:, :2] + cible_tri[:, 2:]) / 2

    meilleur = None
    scores = {}
    for s in echelles:
        src, Ls = _preparer(segs_src, s, p)
        if len(src) == 0:
            scores[s] = 0.0
            continue
        tol = p.tol_abs + p.tol_rel * Ls
        lo = np.searchsorted(Lc_tri, Ls - tol, side="left")
        hi = np.searchsorted(Lc_tri, Ls + tol, side="right")
        nb = hi - lo
        garde = (nb > 0) & (nb <= p.max_candidats)
        if not garde.any():
            scores[s] = 0.0
            continue
        # liste des paires (i source, j cible)
        i_idx = np.repeat(np.nonzero(garde)[0], nb[garde])
        j_idx = np.concatenate([np.arange(a, b) for a, b in zip(lo[garde], hi[garde])])
        ang_s = np.arctan2(src[:, 3] - src[:, 1], src[:, 2] - src[:, 0])
        mil_s = (src[:, :2] + src[:, 2:]) / 2 * s
        zs = mil_s[i_idx, 0] + 1j * mil_s[i_idx, 1]
        zc = mil_c[j_idx, 0] + 1j * mil_c[j_idx, 1]
        thetas, txs, tys = [], [], []
        for sens in (0.0, math.pi):  # les deux sens de parcours
            th = (ang_c[j_idx] - ang_s[i_idx] + sens) % (2 * math.pi)
            b = zc - np.exp(1j * th) * zs
            thetas.append(th); txs.append(b.real); tys.append(b.imag)
        th = np.concatenate(thetas); tx = np.concatenate(txs); ty = np.concatenate(tys)
        res = _meilleure_cellule(th, tx, ty, p)
        if res is None:
            scores[s] = 0.0
            continue
        score, th0, tx0, ty0, second = res
        scores[s] = float(score)
        if meilleur is None or score > meilleur.score:
            meilleur = ResultatVote(s, Similitude(s, math.degrees(th0) % 360, tx0, ty0),
                                    float(score), float(second))
    if meilleur is not None:
        meilleur.scores_par_echelle = scores
        # la seconde solution peut venir d'une autre échelle
        autres = [v for k, v in scores.items() if k != meilleur.echelle]
        if autres:
            meilleur.score_second = max(meilleur.score_second, max(autres))
    return meilleur


def _meilleure_cellule(th, tx, ty, p: ParametresVote):
    """Regroupe les propositions en cellules, lisse avec les voisines, renvoie la meilleure."""
    nb_ang = int(round(360 / p.pas_angle_deg))
    ka = np.floor(np.degrees(th) / p.pas_angle_deg).astype(np.int64) % nb_ang
    kx = np.floor(tx / p.pas_translation).astype(np.int64)
    ky = np.floor(ty / p.pas_translation).astype(np.int64)
    cles = np.column_stack([ka, kx, ky])
    uniq, inv, cnt = np.unique(cles, axis=0, return_inverse=True, return_counts=True)
    if len(uniq) == 0:
        return None
    table = {tuple(k): c for k, c in zip(uniq.tolist(), cnt.tolist())}
    # lissage : somme sur les 27 cellules voisines, uniquement pour les cellules prometteuses
    ordre = np.argsort(-cnt)[:2000]
    lisse = []
    for idx in ordre:
        a, x, y = uniq[idx]
        tot = 0
        for da in (-1, 0, 1):
            aa = (a + da) % nb_ang
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    tot += table.get((aa, x + dx, y + dy), 0)
        lisse.append((tot, idx))
    lisse.sort(reverse=True)
    best_tot, best_idx = lisse[0]
    a0, x0, y0 = uniq[best_idx]
    # seconde solution : meilleure cellule éloignée (> 1° ou > 2 m) de la première
    second = 0
    for tot, idx in lisse[1:]:
        a, x, y = uniq[idx]
        da = min(abs(a - a0), nb_ang - abs(a - a0)) * p.pas_angle_deg
        dxy = math.hypot(x - x0, y - y0) * p.pas_translation
        if da > 1.0 or dxy > 2.0:
            second = tot
            break
    # estimation fine : moyenne des propositions du voisinage de la meilleure cellule
    sel = (np.minimum(np.abs(ka - a0), nb_ang - np.abs(ka - a0)) <= 1) & \
          (np.abs(kx - x0) <= 1) & (np.abs(ky - y0) <= 1)
    th_ref = (a0 + 0.5) * math.radians(p.pas_angle_deg)
    dth = np.angle(np.exp(1j * (th[sel] - th_ref)))
    th0 = th_ref + float(np.median(dth))
    return best_tot, th0, float(np.median(tx[sel])), float(np.median(ty[sel])), second


# ------------------------------------------------------------------------------------------
# ICP
# ------------------------------------------------------------------------------------------
def _moindres_carres(z: np.ndarray, w: np.ndarray, echelle_fixe: float | None):
    """Meilleure similitude w ≈ a·z + b (nombres complexes)."""
    zm, wm = z.mean(), w.mean()
    dz, dw = z - zm, w - wm
    num = np.sum(dw * np.conj(dz))
    if echelle_fixe is None:
        a = num / np.sum(np.abs(dz) ** 2)
    else:
        a = echelle_fixe * num / abs(num)
    return a, wm - a * zm


def snap_echelle(s: float) -> float:
    """Puissance de 10 la plus proche (en logarithme)."""
    return float(f"1e{round(math.log10(s))}")


def icp(pts_src: np.ndarray, pts_cible: np.ndarray, depart: Similitude,
        seuils=SEUILS_ICP, snap: bool = True, iterations: int = 15,
        echelle_fixe: float | None = None) -> ResultatICP:
    """Affine `depart` par ICP. Les points source sont dans les unités source."""
    arbre = cKDTree(pts_cible)
    z = pts_src[:, 0] + 1j * pts_src[:, 1]
    a, b = depart.a, depart.b
    hist = []

    def passe(seuil, fixe):
        nonlocal a, b
        n = 0
        for _ in range(iterations):
            w = a * z + b
            d, j = arbre.query(np.column_stack([w.real, w.imag]), distance_upper_bound=seuil)
            ok = np.isfinite(d)
            n = int(ok.sum())
            if n < 3:
                return n
            wc = pts_cible[j[ok], 0] + 1j * pts_cible[j[ok], 1]
            a2, b2 = _moindres_carres(z[ok], wc, fixe)
            fini = abs(a2 - a) < 1e-12 and abs(b2 - b) < 1e-6
            a, b = a2, b2
            if fini:
                break
        return n

    # 1) échelle libre (sauf si imposée)
    if echelle_fixe is None:
        for s in seuils:
            n = passe(s, None)
            hist.append(("libre", s, n, abs(a)))
            if n < 3:
                break
    # 2) échelle snappée / imposée puis fixée
    fixe = echelle_fixe if echelle_fixe is not None else (snap_echelle(abs(a)) if snap else None)
    if fixe is not None:
        a = fixe * a / abs(a)
        # On repart de la meilleure solution libre mais on refait toute la descente :
        # le snap peut avoir déplacé légèrement les points.
        for s in seuils:
            n = passe(s, fixe)
            hist.append(("fixe", s, n, abs(a)))

    sim = Similitude.depuis_complexes(a, b)
    if fixe is not None:
        sim.echelle = float(fixe)        # valeur exacte (évite 0.010000000000000002)
    sim.tx, sim.ty = float(sim.tx), float(sim.ty)
    w = a * z + b
    d, _ = arbre.query(np.column_stack([w.real, w.imag]))
    proches = d < seuils[-1]
    return ResultatICP(
        similitude=sim,
        nb_points=int(proches.sum()),
        ecart_moyen=float(d[proches].mean()) if proches.any() else float("nan"),
        ecart_max=float(d[proches].max()) if proches.any() else float("nan"),
        part_1cm=float((d < 0.01).mean()) if len(d) else 0.0,
        historique=hist,
    )


@dataclass
class Calage:
    vote: ResultatVote
    icp: ResultatICP

    @property
    def similitude(self) -> Similitude:
        return self.icp.similitude


def estimer(segs_src: np.ndarray, segs_cible: np.ndarray, pts_src: np.ndarray | None = None,
            pts_cible: np.ndarray | None = None, echelles=ECHELLES_CANDIDATES,
            echelle_fixe: float | None = None, p: ParametresVote | None = None) -> Calage | None:
    """Vote puis ICP. Renvoie None si aucune solution n'émerge."""
    vote = voter(segs_src, segs_cible, echelles, p)
    if vote is None or vote.score <= 0:
        return None
    if pts_src is None:
        pts_src = points_segments(segs_src)
    if pts_cible is None:
        pts_cible = points_segments(segs_cible)
    # ICP dans les deux sens, et on garde le meilleur.
    # Pourquoi : l'ICP part de chaque point SOURCE vers son plus proche voisin. Si la source contient
    # beaucoup de géométrie absente de la cible (réseaux, hachures…), ces points « parasites »
    # trouvent de faux voisins et font dériver la solution. Dans l'autre sens, c'est la géométrie
    # propre à la cible qui gêne. L'un des deux sens est presque toujours sain.
    directe = icp(pts_src, pts_cible, vote.similitude, echelle_fixe=echelle_fixe)
    candidats = [directe]
    try:
        e_inv = None if echelle_fixe is None else 1.0 / echelle_fixe
        inv = icp(pts_cible, pts_src, vote.similitude.inverse(), echelle_fixe=e_inv)
        sim_inv = inv.similitude.inverse()
        if echelle_fixe is not None or abs(snap_echelle(sim_inv.echelle) / sim_inv.echelle - 1) < 0.01:
            sim_inv.echelle = snap_echelle(sim_inv.echelle) if echelle_fixe is None else echelle_fixe
        candidats.append(statistiques(sim_inv, pts_src, pts_cible, inv.historique))
    except Exception:
        pass
    meilleur = max(candidats, key=lambda r: concordance(r.similitude, pts_src, pts_cible))
    return Calage(vote, meilleur)


def concordance(sim: Similitude, pts_src: np.ndarray, pts_cible: np.ndarray, tol: float = 0.01) -> int:
    """Nombre de points (du plus petit des deux jeux) ayant un homologue à < tol."""
    ts = sim.appliquer(pts_src)
    if len(pts_cible) <= len(ts):
        d, _ = cKDTree(ts).query(pts_cible)
    else:
        d, _ = cKDTree(pts_cible).query(ts)
    return int((d < tol).sum())


def statistiques(sim: Similitude, pts_src, pts_cible, historique=None, seuil=SEUILS_ICP[-1]) -> ResultatICP:
    """Indicateurs d'un calage (points source à < seuil, écart moyen…) dans le sens source -> cible."""
    d, _ = cKDTree(pts_cible).query(sim.appliquer(pts_src))
    proches = d < seuil
    return ResultatICP(sim, int(proches.sum()),
                       float(d[proches].mean()) if proches.any() else float("nan"),
                       float(d[proches].max()) if proches.any() else float("nan"),
                       float((d < 0.01).mean()) if len(d) else 0.0, historique or [])
