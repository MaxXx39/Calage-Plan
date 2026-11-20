# Calage de plans — « apprendre puis rejouer »

Outil de calage (plan local → MN95 / LV95) et de nettoyage de plans DWG/DXF.
Le calage et le nettoyage de l'indice A sont faits une fois à la main ; l'outil compare le plan
d'origine et le DXF final, en déduit une **recette** (`recette.json`) et la rejoue sur les
indices B, C, D…

## Installation

```bash
pip install -e .[test]
```

Lecture des `.dwg` : **ODA File Converter** (gratuit) s'il est installé, sinon `dwg2dxf`
(LibreDWG) s'il est dans le PATH, sinon l'outil demande d'exporter le plan en DXF.

## Utilisation

```bash
python -m calage apprendre plan_A.dwg final_A.dxf -o recette.json
```

```bash
python -m calage appliquer plan_B.dwg -r recette.json -o B_MN95.dxf --ref reference.dwg --apercu B.png
```

Options d'`appliquer` : `--forcer` (produire malgré un contrôle négatif, déconseillé),
`--ecarter-nouveaux` (écarter les calques jamais vus au lieu de les garder), `--rapport` (JSON).
Code de sortie : 0 = fichier produit, 2 = refus (calage à refaire à la main), 1 = erreur.

## Ce que fait chaque commande

**apprendre** : vote sur segments de même longueur (échelles 1e-3 … 1000) puis ICP (seuils 1 m → 3 cm,
échelle libre puis puissance de 10) ; appariement des entités (≥ 50 % des points à < 3 cm, même famille,
calque attendu) ; déduction du préfixe de calque, des calques gardés / écartés / renommés, des types
supprimés (globalement et par calque), de la zone de travail ; liste des ajouts manuels ; 600 ancres.

**appliquer** : explosion récursive des blocs et xrefs, **contrôle de concordance** avec les ancres
(identique / recalé automatiquement si confiance forte / refus), nettoyage, transformation, export
DXF R2000 en mètres, rapport console + JSON, aperçu PNG (rouge sur gris).

## Code

- `calage_core/lecture.py` — DWG/DXF (odafc, dwg2dxf, lecture tolérante)
- `calage_core/explosion.py` — explosion récursive, héritage calque 0 / DUBLOC, xrefs
- `calage_core/geometrie.py` — similitudes, segments, points d'échantillon, noms de calques
- `calage_core/estimation.py` — vote + ICP
- `calage_core/appariement.py` — appariement d'entités entre deux plans superposés
- `calage_core/apprentissage.py` — commande `apprendre`
- `calage_core/controle.py` — contrôle de concordance d'un nouvel indice
- `calage_core/application.py` — commande `appliquer` + rapport
- `calage_core/export.py`, `calage_core/apercu.py`, `calage_core/recette.py`
- `calage/cli.py` — ligne de commande

## Tests

```bash
python -m pytest -q
```

Les plans réels ne sont pas versionnés (dépôt public) : voir `tests/data/LISEZMOI.md`.
