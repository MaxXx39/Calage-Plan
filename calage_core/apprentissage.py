"""Commande « apprendre » : déduire la recette en comparant le plan d'origine et le DXF final.

Aucune intervention de l'utilisateur : tout est déduit par comparaison géométrique.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import __version__
from .appariement import ANNOTATIONS, COMPLEXES, IndexCible, apparier, points_transformes
from .estimation import estimer
from .explosion import exploser_document
from .geometrie import longueurs, nom_normalise, segments_entite, tableau_segments
from .lecture import _empreinte, lire_plan
from .recette import UNITES_INSUNITS, Recette

logging.getLogger("ezdxf").setLevel(logging.ERROR)

MARGE_ZONE = 10.0          # m autour de l'emprise retrouvée
NB_ANCRES = 600
PART_MIN_CALQUE = 0.10     # un calque est « gardé » si ≥ 10 % de ses entités (dans la zone) sont retrouvées
PART_MIN_RENOMME = 0.50    # ... et ≥ 50 % s'il a été renommé (sinon : simple superposition fortuite)
NB_MIN_TYPE_CALQUE = 3     # type supprimé sur un calque donné : au moins 3 cas
PART_MAX_TYPE_SUPPRIME = 0.05
NB_MIN_TYPE_SUPPRIME = 5   # pour un type « ordinaire » : il faut au moins 5 cas pour en faire une règle


class ErreurApprentissage(Exception):
    pass


@dataclass
class ResultatApprentissage:
    recette: Recette
    messages: list[str] = field(default_factory=list)


def _prefixe_brut(calque_final: str, calque_source: str) -> str | None:
    """Préfixe P tel que calque_final = P + calque_source (comparaison normalisée)."""
    ns = nom_normalise(calque_source)
    if not ns:
        return None
    for i in range(len(calque_final) + 1):
        if nom_normalise(calque_final[i:]) == ns:
            # le plus long préfixe brut qui convient (on avance tant que ça reste égal)
            j = i
            while j + 1 <= len(calque_final) and nom_normalise(calque_final[j + 1:]) == ns:
                j += 1
            return calque_final[:j]
    return None


def apprendre(chemin_plan, chemin_final, journal=print) -> ResultatApprentissage:
    chemin_plan, chemin_final = Path(chemin_plan), Path(chemin_final)
    msgs: list[str] = []

    def dire(m):
        msgs.append(m)
        if journal:
            journal(m)

    plan = lire_plan(chemin_plan)
    final = lire_plan(chemin_final)
    ex_p = exploser_document(plan.doc, chemin_plan.parent)
    ex_f = exploser_document(final.doc, chemin_final.parent)
    elems_p, elems_f = ex_p.elements, ex_f.elements
    dire(f"Plan d'origine : {len(elems_p)} entités après explosion des blocs ; "
         f"final : {len(elems_f)} entités.")

    # ---------------------------------------------------------------- 1. transformation
    segs_p = tableau_segments(el.entite for el in elems_p)
    segs_f = tableau_segments(el.entite for el in elems_f)
    calage = estimer(segs_p, segs_f)
    if calage is None or calage.icp.nb_points < 10:
        raise ErreurApprentissage(
            "Impossible de retrouver la correspondance entre le plan et le final : "
            "trop peu d'éléments communs.")
    sim = calage.similitude
    dire(f"Transformation : échelle {sim.echelle:g}, rotation {sim.rotation_deg:.4f}°, "
         f"translation ({sim.tx:.3f} ; {sim.ty:.3f}) — {calage.icp.nb_points} points, "
         f"écart moyen {calage.icp.ecart_moyen * 1000:.1f} mm.")

    # ---------------------------------------------------------------- 2. appariement
    index = IndexCible.construire(elems_f)
    pts_p = [points_transformes(el.entite, sim) for el in elems_p]

    # passe 1 : sans contrainte de calque -> votes pour la correspondance des calques
    votes = defaultdict(Counter)            # calque source -> Counter(entité finale -> nb)
    revendique = defaultdict(set)           # entité finale -> calques source (normalisés)
    paires = []
    for el, pts in zip(elems_p, pts_p):
        a = apparier(pts, index)
        if not a.retrouve:
            continue
        for j in set(a.cibles):
            votes[el.calque][j] += 1
            revendique[j].add(nom_normalise(el.calque))
            paires.append((index.calques[j], el.calque))

    # préfixe ajouté à l'export
    cands = Counter()
    for cf, cs in paires:
        p = _prefixe_brut(cf, cs)
        if p is not None:
            cands[p] += 1
    prefixe = ""
    if cands:
        p, n = cands.most_common(1)[0]
        if n >= 0.3 * len(paires):
            prefixe = p
    np_ = nom_normalise(prefixe)
    if prefixe:
        dire(f"Préfixe de calque ajouté à l'export : « {prefixe} »")

    calques_finals = Counter(el.calque for el in elems_f)
    norm_finals = {nom_normalise(c): c for c in calques_finals}
    calques_source = Counter(el.calque for el in elems_p)
    identite_vers_source = {np_ + nom_normalise(c): c for c in calques_source}

    # correspondance calque source -> calque final
    cible_de = {}
    for cs in calques_source:
        ident = np_ + nom_normalise(cs)
        v = Counter()
        for j, n in votes[cs].items():
            ncf = nom_normalise(index.calques[j])
            if ncf == ident:
                v[ncf] += n
                continue
            # Vote vers un AUTRE calque : ignoré si cette entité finale est déjà expliquée par
            # le calque source « naturel » de ce calque final (géométries superposées).
            src_naturel = identite_vers_source.get(ncf)
            if src_naturel is not None and nom_normalise(src_naturel) in revendique[j]:
                continue
            v[ncf] += n
        total = sum(v.values())
        if ident in norm_finals and v[ident] > 0 and v[ident] >= total / 3:
            cible_de[cs] = norm_finals[ident]
        elif total > 0:
            best, _ = v.most_common(1)[0]
            if not np_ or best.startswith(np_):
                cible_de[cs] = norm_finals[best]
        # sinon : pas de correspondance -> calque écarté

    # passe 2 : appariement contraint au calque attendu
    retrouve2 = np.zeros(len(elems_p), dtype=bool)
    cibles_par_elem = {}
    for i, (el, pts) in enumerate(zip(elems_p, pts_p)):
        cf = cible_de.get(el.calque)
        if cf is None:
            continue
        a = apparier(pts, index, nom_normalise(cf))
        if a.retrouve:
            retrouve2[i] = True
            cibles_par_elem[i] = a.cibles

    # ---------------------------------------------------------------- 3. zone
    pts_ret = [(p[0], p[1]) for i in np.nonzero(retrouve2)[0] for p in pts_p[i]]
    if not pts_ret:
        raise ErreurApprentissage("Aucune entité du plan n'a été retrouvée dans le final.")
    arr = np.asarray(pts_ret)
    emprise = [float(arr[:, 0].min()), float(arr[:, 1].min()),
               float(arr[:, 0].max()), float(arr[:, 1].max())]

    def dans_zone(pts):
        if not pts:
            return True
        a = np.asarray([(p[0], p[1]) for p in pts])
        return bool(np.any((a[:, 0] >= emprise[0] - MARGE_ZONE) & (a[:, 0] <= emprise[2] + MARGE_ZONE)
                           & (a[:, 1] >= emprise[1] - MARGE_ZONE) & (a[:, 1] <= emprise[3] + MARGE_ZONE)))

    en_zone = np.array([dans_zone(p) for p in pts_p])

    # ---------------------------------------------------------------- 4. calques gardés / écartés
    stats = defaultdict(lambda: {"total": 0, "zone": 0, "retrouvees": 0})
    for i, el in enumerate(elems_p):
        s = stats[el.calque]
        s["total"] += 1
        s["zone"] += int(en_zone[i])
        s["retrouvees"] += int(retrouve2[i])

    gardes, ecartes = [], []
    renommes = {}
    for cs in sorted(calques_source, key=str.lower):
        s = stats[cs]
        cf = cible_de.get(cs)
        part = s["retrouvees"] / max(s["zone"], 1)
        renomme = cf is not None and nom_normalise(cf) != np_ + nom_normalise(cs)
        seuil = PART_MIN_RENOMME if renomme else PART_MIN_CALQUE
        if cf is not None and s["retrouvees"] > 0 and part >= seuil:
            nom_cible = cf[len(prefixe):] if prefixe and cf.startswith(prefixe) else cf
            # nom de sortie : préférer le nom source (accents corrects) quand il correspond
            if nom_normalise(nom_cible) == nom_normalise(cs):
                nom_sortie_base = cs
            else:
                src = identite_vers_source.get(nom_normalise(cf))
                nom_sortie_base = src if src is not None else nom_cible
                renommes[cs] = nom_sortie_base
            gardes.append({"nom": cs, "sortie": prefixe + nom_sortie_base,
                           "entites": s["total"], "retrouvees": s["retrouvees"]})
        else:
            raison = "absent du final" if cf is None else \
                f"presque absent du final ({s['retrouvees']}/{s['zone']} retrouvées)"
            ecartes.append({"nom": cs, "entites": s["total"], "raison": raison})
            retrouve2[[i for i, el in enumerate(elems_p) if el.calque == cs]] = False

    noms_gardes = {g["nom"] for g in gardes}
    if renommes:
        for a, b in renommes.items():
            dire(f"Calque renommé : « {a} » -> « {b} »")

    # ---------------------------------------------------------------- 5. types supprimés
    par_type = defaultdict(lambda: {"total": 0, "retrouvees": 0, "total_global": 0, "ret_global": 0})
    for i, el in enumerate(elems_p):
        t = el.entite.dxftype()
        par_type[t]["total_global"] += 1
        par_type[t]["ret_global"] += int(retrouve2[i])
        if el.calque in noms_gardes and en_zone[i]:
            par_type[t]["total"] += 1
            par_type[t]["retrouvees"] += int(retrouve2[i])
    types_supprimes = []
    for t, s in sorted(par_type.items()):
        if t in ANNOTATIONS:
            # cotations, multileaders, attributs : supprimés s'ils ne sont (presque) jamais retrouvés
            if s["total_global"] > 0 and s["ret_global"] <= PART_MAX_TYPE_SUPPRIME * s["total_global"]:
                types_supprimes.append(t)
        elif s["total"] >= NB_MIN_TYPE_SUPPRIME and s["retrouvees"] == 0:
            types_supprimes.append(t)
    if types_supprimes:
        dire("Types d'entités supprimés : " + ", ".join(types_supprimes))

    # types supprimés calque par calque (ex. hachures retirées d'un seul calque)
    par_calque_type = defaultdict(lambda: [0, 0])
    for i, el in enumerate(elems_p):
        t = el.entite.dxftype()
        if el.calque in noms_gardes and en_zone[i] and t not in types_supprimes:
            par_calque_type[(el.calque, t)][0] += 1
            par_calque_type[(el.calque, t)][1] += int(retrouve2[i])
    types_par_calque = defaultdict(list)
    for (c, t), (n, r) in sorted(par_calque_type.items()):
        if n >= NB_MIN_TYPE_CALQUE and r == 0:
            types_par_calque[c].append(t)
    for c, ts in types_par_calque.items():
        dire(f"Sur le calque « {c} », supprimés : " + ", ".join(ts))

    # ---------------------------------------------------------------- 6. ajouts manuels
    explique = set()
    for i, cibles in cibles_par_elem.items():
        if retrouve2[i]:
            explique.update(cibles)
    manuels = defaultdict(Counter)
    sans_homologue = defaultdict(Counter)
    for j, el in enumerate(elems_f):
        if j in explique:
            continue
        t = el.entite.dxftype()
        if np_ and not nom_normalise(el.calque).startswith(np_):
            manuels[el.calque][t] += 1
        else:
            sans_homologue[el.calque][t] += 1
    nb_manuels = sum(sum(c.values()) for c in manuels.values())
    nb_sans = sum(sum(c.values()) for c in sans_homologue.values())
    if manuels:
        dire(f"Éléments ajoutés à la main dans le final (non rejoués) : {nb_manuels} sur les calques "
             + ", ".join(f"« {c} »" for c in manuels))
    if sans_homologue:
        dire(f"Éléments du final sans homologue dans le plan d'origine : {nb_sans} (non rejoués)")

    # ---------------------------------------------------------------- 7. ancres
    ancres = _choisir_ancres(elems_p, noms_gardes, sim.echelle)

    # ---------------------------------------------------------------- 8. auto-contrôle
    # Ce que donnerait le rejeu : entités gardées (calque gardé, type non supprimé, en zone)
    rejouees = [i for i, el in enumerate(elems_p)
                if el.calque in noms_gardes and el.entite.dxftype() not in types_supprimes
                and el.entite.dxftype() not in types_par_calque.get(el.calque, ())
                and en_zone[i]]
    nb_ok = int(retrouve2[rejouees].sum()) if rejouees else 0
    part_rejeu = nb_ok / max(len(rejouees), 1)
    dire(f"Auto-contrôle : {nb_ok}/{len(rejouees)} entités rejouées ont un homologue dans le final "
         f"({part_rejeu:.0%}).")

    # points du plan à < 1 cm du final (indicateur demandé)
    from scipy.spatial import cKDTree
    pf = np.vstack([segs_f[:, :2], segs_f[:, 2:]]) if len(segs_f) else np.zeros((0, 2))
    pp = sim.appliquer(np.vstack([segs_p[:, :2], segs_p[:, 2:]])) if len(segs_p) else np.zeros((0, 2))
    part_1cm = float((cKDTree(pf).query(pp)[0] < 0.01).mean()) if len(pf) and len(pp) else 0.0

    recette = Recette(
        outil=f"calage-plans {__version__}",
        cree_le=dt.datetime.now().isoformat(timespec="seconds"),
        source={
            "plan": chemin_plan.name, "final": chemin_final.name,
            "empreinte_plan": _empreinte(chemin_plan),
            "insunits": plan.insunits, "unite": UNITES_INSUNITS.get(plan.insunits, "?"),
            "nb_entites_plan": len(elems_p), "nb_entites_final": len(elems_f),
        },
        transformation={
            **sim.vers_dict(),
            "convention": "x_final = echelle · R(rotation) · x_plan + translation",
            "qualite": {
                "points_icp": calage.icp.nb_points,
                "ecart_moyen_m": round(calage.icp.ecart_moyen, 5),
                "ecart_max_m": round(calage.icp.ecart_max, 5),
                "score_vote": calage.vote.score,
                "dominance_vote": round(min(calage.vote.dominance, 1e6), 2),
                "part_points_segments_1cm": round(part_1cm, 3),
            },
        },
        nettoyage={
            "exploser_blocs": True,
            "prefixe_calque": prefixe,
            "calques_gardes": gardes,
            "calques_ecartes": ecartes,
            "calques_renommes": renommes,
            "types_supprimes": types_supprimes,
            "types_supprimes_par_calque": dict(types_par_calque),
            "exploser_objets_complexes": sorted(COMPLEXES),
            "calques_nouveaux": "garder",
        },
        zone={"emprise_mn95": [round(v, 3) for v in emprise], "marge_m": MARGE_ZONE},
        ancres={"unites": "unités du plan d'origine", "tolerance_m": 0.01,
                "nb": len(ancres), "segments": ancres},
        ajouts_manuels={
            "nb": nb_manuels,
            "par_calque": {c: dict(v) for c, v in manuels.items()},
            "sans_homologue_nb": nb_sans,
            "sans_homologue_par_calque": {c: dict(v) for c, v in sans_homologue.items()},
            "remarque": "Ces éléments ne sont jamais rejoués : à redessiner si besoin.",
        },
        apprentissage={
            "entites_rejouees": len(rejouees),
            "entites_rejouees_retrouvees": nb_ok,
            "part_rejeu_retrouvee": round(part_rejeu, 3),
            "entites_hors_zone": int((~en_zone).sum()),
            "avertissements_lecture": plan.avertissements + final.avertissements,
            "xrefs": [x.__dict__ for x in ex_p.xrefs],
        },
    )
    return ResultatApprentissage(recette, msgs)


def _choisir_ancres(elems_p, noms_gardes, echelle, nb=NB_ANCRES) -> list[list[float]]:
    """~600 segments parmi les plus longs, d'abord sur les calques gardés."""
    gardes, autres = [], []
    for el in elems_p:
        for s in segments_entite(el.entite):
            (gardes if el.calque in noms_gardes else autres).append(s)
    out = []
    for groupe in (gardes, autres):
        if not groupe or len(out) >= nb:
            continue
        a = np.asarray(groupe, dtype=float)
        L = longueurs(a) * echelle
        a = a[L >= 0.5]
        L = L[L >= 0.5]
        a = a[np.argsort(-L)]
        # dédoublonnage (segments superposés)
        vus = set()
        for s in a:
            cle = tuple(np.round(s, 2)); cle2 = (cle[2], cle[3], cle[0], cle[1])
            if cle in vus or cle2 in vus:
                continue
            vus.add(cle)
            out.append([round(float(v), 4) for v in s])
            if len(out) >= nb:
                break
    return out
