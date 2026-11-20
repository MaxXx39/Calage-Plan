"""Ligne de commande :

    python -m calage apprendre PLAN_A FINAL_A -o recette.json
    python -m calage appliquer PLAN_B -r recette.json -o B_MN95.dxf [--ref REFERENCE] [--apercu B.png]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from calage_core.apprentissage import ErreurApprentissage, apprendre
from calage_core.lecture import ErreurLecture
from calage_core.recette import ErreurRecette

# codes de sortie
OK, REFUS, ERREUR = 0, 2, 1


def _cmd_apprendre(args) -> int:
    print(f"Apprentissage : « {args.plan} » -> « {args.final} »")
    res = apprendre(args.plan, args.final, journal=lambda m: print("  " + m))
    res.recette.enregistrer(args.sortie)
    q = res.recette.transformation["qualite"]
    print(f"\nRecette enregistrée : {args.sortie}")
    print(f"  Concordance : {q['points_icp']} points, écart moyen {q['ecart_moyen_m'] * 1000:.1f} mm ; "
          f"rejeu retrouvé à {res.recette.apprentissage['part_rejeu_retrouvee']:.0%}")
    if res.recette.apprentissage["part_rejeu_retrouvee"] < 0.9:
        print("  /!\\ Le rejeu ne reproduit pas fidèlement le final : vérifiez la recette avant usage.")
    return OK


def _cmd_appliquer(args) -> int:
    from calage_core.application import appliquer
    rapport_json = args.rapport or str(Path(args.sortie).with_suffix(".rapport.json"))
    rap = appliquer(args.plan, args.recette, args.sortie, ref=args.ref, apercu=args.apercu,
                    forcer=args.forcer, ecarter_nouveaux=args.ecarter_nouveaux,
                    chemin_rapport=rapport_json)
    print(rap.texte())
    print(f"Rapport détaillé : {rapport_json}")
    return OK if rap.fichier_produit else REFUS


def construire_parseur() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="calage", description="Calage et nettoyage de plans DWG/DXF en MN95.")
    sp = p.add_subparsers(dest="commande", required=True)

    a = sp.add_parser("apprendre", help="déduire une recette à partir d'un plan et de son DXF final calé à la main")
    a.add_argument("plan", help="plan d'origine (DWG ou DXF) de l'indice A")
    a.add_argument("final", help="DXF final calé et nettoyé à la main")
    a.add_argument("-o", "--sortie", default="recette.json", help="fichier recette à écrire")
    a.set_defaults(func=_cmd_apprendre)

    b = sp.add_parser("appliquer", help="rejouer une recette sur un nouvel indice")
    b.add_argument("plan", help="nouvel indice (DWG ou DXF)")
    b.add_argument("-r", "--recette", required=True, help="recette (JSON)")
    b.add_argument("-o", "--sortie", required=True, help="DXF calé à écrire")
    b.add_argument("--ref", help="plan de référence MN95 (pour l'aperçu)")
    b.add_argument("--apercu", help="image PNG d'aperçu à écrire")
    b.add_argument("--rapport", help="rapport JSON (par défaut : à côté du DXF)")
    b.add_argument("--forcer", action="store_true",
                   help="produire le fichier même si le contrôle de concordance échoue (déconseillé)")
    b.add_argument("--ecarter-nouveaux", action="store_true",
                   help="écarter les calques jamais vus à l'apprentissage (par défaut : gardés et signalés)")
    b.set_defaults(func=_cmd_appliquer)
    return p


def main(argv=None) -> int:
    args = construire_parseur().parse_args(argv)
    try:
        return args.func(args)
    except (ErreurLecture, ErreurRecette, ErreurApprentissage) as ex:
        print(f"ERREUR : {ex}", file=sys.stderr)
        return ERREUR
