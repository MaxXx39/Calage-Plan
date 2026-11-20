# Calage de plans — « apprendre puis rejouer »

Outil de calage (plan local → MN95 / LV95) et de nettoyage de plans DWG/DXF.
Le calage et le nettoyage de l'indice A sont faits une fois à la main ; l'outil compare le plan
d'origine et le DXF final, en déduit une **recette** (`recette.json`) et la rejoue sur les
indices B, C, D…

## Installation sous Windows

1. Installer **Python** (3.11 ou plus) depuis python.org — cocher « Add python.exe to PATH ».
2. Installer **ODA File Converter** (gratuit, opendesign.com) pour lire les DWG.
3. Télécharger ce dépôt (bouton « Code » → « Download ZIP »), le décompresser dans un dossier fixe.
4. Double-cliquer **Installer.bat** (une seule fois).
5. Double-cliquer **Calage.bat** pour ouvrir l'appli.

## Installation (autres systèmes)

```bash
pip install -e .[test]
```

Lecture des `.dwg` : **ODA File Converter** (gratuit) s'il est installé, sinon `dwg2dxf`
(LibreDWG) s'il est dans le PATH, sinon l'outil demande d'exporter le plan en DXF.

## L'appli (recommandé)

```bash
python -m calage interface
```

Une page s'ouvre dans le navigateur (servie sur 127.0.0.1 : aucun fichier ne quitte le poste).
Organisation : **Mes chantiers → un chantier → ses plans → leurs indices.**

- **Mes chantiers** : la liste ; « + Nouveau chantier » = un nom + la référence MN95 du géomètre.
- **Un chantier** : une carte par plan (ex. « Terrassement ingénieur ») avec « Nouvel indice reçu »,
  « Télécharger le DXF », l'historique des indices (afficher, comparer, supprimer) et
  « Supprimer ce plan ». « + Ajouter un plan à caler » ouvre l'assistant. Cocher 2 indices →
  « Voir les différences ». Les suppressions vont dans `~/CalagePlans/corbeille/`.
- **Ajouter un plan** (assistant) :
  1. nom du plan, indice, fichier (+ xrefs) ;
  2. caler : calage automatique (comparaison avec les plans du chantier, au choix) ou à la main avec
     des points et/ou des axes (2 points, 1 point + 1 axe, 3 axes…). Accroche visible avant le clic.
     La **vérification** est un point en plus, qui ne sert pas au calcul : s'il tombe à moins de 1 cm
     de sa place, le calage est juste ;
  3. nettoyer : un clic sur un objet = tout son calque (calques gelés/éteints du DWG écartés d'office),
     types à supprimer, hachures (garder / contour / supprimer), zone à garder (les traits qui
     dépassent sont **coupés** au bord), préfixe de calque ;
  4. enregistrer : DXF calé (R2000, mètres, blocs éclatés) + recette.
- **Nouvel indice reçu** : indice suivant proposé, dépôt du fichier, « Caler ». Contrôle de
  concordance (même origine / recalé automatiquement / refus). **Rien n'est enregistré avant
  « Enregistrer l'indice »** ; ensuite : téléchargement et « Voir ce qui a changé ».

Dossier de travail : `~/CalagePlans/chantiers/<chantier>/plans/<plan>/` (recette + indices).

## Ligne de commande

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
- `calage_core/points.py` — calage par 2 points + contrôle
- `calage_core/vue.py` — géométrie pour l'affichage, accrochage
- `calage_core/recette_manuelle.py` — recette construite depuis les choix faits dans l'appli
- `calage_app/` — serveur local (bibliothèque standard) et page web (`static/`)
- `calage/cli.py` — ligne de commande (`interface`, `apprendre`, `appliquer`)

## Tests

```bash
python -m pytest -q
```

Parcours complet dans le navigateur (Playwright, serveur lancé sur le port 8765) :
`node tests/e2e/parcours.js <dossier_captures> <plan_indice_B>`.

Les plans réels ne sont pas versionnés (dépôt public) : voir `tests/data/LISEZMOI.md`.
