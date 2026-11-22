# Passation — « Calage de plans »

Document de reprise pour intégrer la fonction de calage dans une autre application
(par exemple `app-topo`). Il résume ce qui existe dans ce dépôt **et** ce qui a été décidé
dans les conversations avec l'utilisateur (qui ne se voit pas dans le code).

État au 22.11.2026 — branche `claude/kind-volta-fdtd8j`.

---

## 1. À quoi sert l'appli

- **Qui s'en sert** : le géomètre (seul utilisateur) d'une entreprise de gros œuvre en Suisse (canton de Vaud).
  Il était prévu au départ que des conducteurs de travaux l'utilisent aussi ; **l'utilisateur a abandonné
  cette idée** : l'appli est pour lui seul.
- **Pour quoi faire** : il reçoit des plans DWG/DXF d'architectes et d'ingénieurs, chacun dans son propre
  repère (unités mm/cm/m, rotation, origine locale). Il doit les **caler en MN95 / LV95** (coordonnées
  nationales suisses, en mètres) sur le plan officiel du géomètre (la « référence »), puis les **nettoyer**
  (xrefs liés, blocs éclatés, calques inutiles et cotations supprimés, zone utile seulement).
  Le DXF obtenu est utilisé dans **Mensura** (logiciel de DAO) et dans un **logiciel maison d'implantation
  sur tablette**.
- **Idée centrale** : un même plan reçoit souvent 10 à 20 indices (versions) en quelques semaines.
  Le calage et le nettoyage sont faits **une fois** à la main sur le premier indice ; l'appli enregistre
  une **recette** et la **rejoue** automatiquement sur les indices suivants, après avoir vérifié que le
  nouvel indice a bien la même origine.

## 2. Écrans et fonctions (ce que l'utilisateur voit)

Appli web **locale** (page dans le navigateur, servie par un petit serveur Python sur `127.0.0.1:8765`).
Fil d'Ariane en haut : *Mes chantiers › Chantier › Ajouter un plan / Nouvel indice*.

1. **Mes chantiers**
   - Liste des chantiers (nombre de plans, d'indices, dernière activité).
   - **+ Nouveau chantier** : un nom + le dépôt de la référence MN95 (DWG ou DXF).
2. **Un chantier**
   - Nom, référence (avec lien « changer »).
   - Une **fiche par plan** (ex. « Terrassement ingénieur ») avec :
     **Nouvel indice reçu**, **Télécharger le DXF** (dernier indice), **Historique** (cases à cocher pour
     afficher, lien DXF, « supprimer » par indice), **Supprimer ce plan**.
   - **+ Ajouter un plan à caler** (ouvre l'assistant).
   - **Affichage et comparaison** : à droite, la référence en gris + les indices cochés en couleurs ;
     avec 2 indices cochés, **Voir les différences** (vert = nouveau ou déplacé, rouge = supprimé ou
     déplacé, gris = inchangé, décompte par calque).
   - **Supprimer ce chantier**.
3. **Ajouter un plan** (assistant en 4 étapes)
   1. *Le plan* : nom du plan (liste construite par l'utilisateur au fur et à mesure), indice (« A » par
      défaut), fichier du plan, xrefs éventuels. Alertes : xref introuvable, calques gelés/éteints.
   2. *Caler* :
      - **Calage automatique** : compare le plan aux plans déjà calés du chantier (au choix, n'importe
        quel indice, rubrique « Comparer avec… ») et à la référence ; affiche une confiance
        forte / moyenne / faible.
      - **À la main** : vue double (plan à gauche, référence à droite). Liste d'éléments, chacun
        **Point** ou **Axe**, rôle **calage** ou **vérification**. L'accroche (sommet □, centre ○,
        intersection ×, ligne en orange) s'affiche **au survol, avant le clic**.
        Combinaisons valides : 2 points ; 1 point + 1 axe ; 3 axes ou plus non concourants
        (ex. 2 façades parallèles + 1 perpendiculaire) ; etc.
      - Résultat : échelle (arrondie à la puissance de 10), rotation, translation, verdict de la
        vérification. Bouton « Voir le plan calé sur la référence ».
   3. *Nettoyer* (vue superposée) :
      - Calques : un **clic sur un objet sélectionne tout son calque** ; mode « Écarter au clic » ;
        Annuler / Ctrl+Z ; touche Suppr ; « Voir les écartés » ; filtre ; tout garder / tout écarter.
        Les calques gelés ou éteints dans le DWG sont décochés d'office (badge « gelé » / « éteint »).
      - Types à supprimer (cotations, lignes de repère, attributs… cochés par défaut ; textes, splines,
        surfaces pleines, multilignes… au choix).
      - Hachures : garder / contour seulement / supprimer.
      - Zone à garder (polygone cliqué ; « Terminer la zone » ou double-clic) : ce qui est dehors est
        supprimé, les **traits qui dépassent sont coupés au bord**.
      - Préfixe ajouté aux noms de calques (défaut `DWG_<nom du fichier> - `).
   4. *Terminé* : DXF calé à télécharger + rapport ; boutons « Retour au chantier », « Ajouter un autre plan ».
4. **Nouvel indice** (depuis la fiche d'un plan)
   - Historique des indices, **indice suivant proposé** (A→B, 3→4, B2→B3), dépôt du fichier (+ xrefs),
     option « écarter les calques jamais vus ».
   - « Caler ce plan » : contrôle de concordance puis bandeau
     **vert** (même origine) / **orange** (le plan avait bougé : recalé automatiquement) /
     **rouge** (refusé, rien produit ; bouton « Produire quand même (déconseillé) »).
   - **Rien n'est enregistré avant « Enregistrer l'indice »** (ou « Abandonner »).
   - Après enregistrement : téléchargement + « Voir ce qui a changé depuis l'indice précédent ».

Il existe aussi une **ligne de commande** (`python -m calage apprendre|appliquer|interface`), voir § 4.

## 3. Les données

Tout est en **fichiers sur le disque du poste** (pas de base de données, pas de serveur distant,
pas de Drive, pas d'IndexedDB). Dossier racine : `~/CalagePlans` (Windows :
`C:\Users\<utilisateur>\CalagePlans`), modifiable par la variable d'environnement `CALAGE_DOSSIER`
ou l'option `--dossier`.

```
CalagePlans/
  chantiers/<chantier>/
    chantier.json                    nom + date de création
    reference.dwg|dxf                fond MN95 du chantier (copie)
    plans/<nom du plan>/
      recette.json                   la recette (JSON lisible)
      recette.<AAAAMMJJ-HHMMSS>.json anciennes recettes (archivées quand on recale le plan)
      indices/<AAAAMMJJ-HHMMSS>_<indice>/
        info.json                    indice, date, fichier, mode, nb d'objets
        <plan>_MN95.dxf              DXF calé (R2000, mètres)
        <plan d'origine>.dwg|dxf     copie du fichier reçu
        rapport.json                 rapport détaillé
      a_valider/<…>/                 indice calé en attente de « Enregistrer » (attente.json)
      refus/<…>/                     indices refusés (rapport seulement)
  corbeille/                         tout ce qui est « supprimé » (rien n'est effacé définitivement)
  travail/                           fichiers déposés (temporaires, jamais nettoyés automatiquement)
```

Il n'y a **pas** de matériel, de personnes ni de photos : seulement des chantiers, des plans, des indices,
des recettes et des fichiers DWG/DXF. Les dates sont en ISO 8601 local (`2026-11-20T15:13:00`).
États d'un indice (`mode`) : `identique`, `recalé`, `forcé` (`refus` → rangé dans `refus/`).

**Exemple réel** (essai sur les fichiers de test ; nom de chantier remplacé) —
`chantiers/Chantier exemple/plans/Terrassement/indices/20261120-151300_A/info.json` :

```json
{
  "indice": "A",
  "date": "2026-11-20T15:13:00",
  "plan": "plan_A.dwg",
  "mode": "identique",
  "fichier_produit": true,
  "fichier_sortie": "plan_A_MN95.dxf",
  "nb_entites": 644
}
```

Extrait de la `recette.json` correspondante (listes raccourcies par « … ») :

```json
{
  "version": 1,
  "outil": "calage-plans 0.1.0",
  "source": { "methode": "calage axes et points", "type_plan": "Terrassement",
              "plan": "plan_A.dwg", "empreinte_plan": "713f02c9…", "reference": "reference.dwg",
              "insunits": 5, "unite": "cm", "nb_entites_plan": 5968 },
  "transformation": {
    "echelle": 0.01, "rotation_deg": 121.41628988389203,
    "translation": [2510211.8525824794, 1141525.2003281305],
    "convention": "x_final = echelle · R(rotation) · x_plan + translation",
    "qualite": { "echelle_brute": 0.01000000000017006, "echelle_arrondie": true, "verdict": "bon",
                 "elements": [ { "type": "axe", "role": "calage",
                                 "plan": [18726.96, -16551.17, 16583.24, -16551.85],
                                 "ref": [2510255.486, 1141771.29, 2510266.666, 1141752.999],
                                 "ecart_m": 0.0, "ecart_angle_deg": 0.0 }, "…" ] } },
  "nettoyage": {
    "exploser_blocs": true, "prefixe_calque": "DWG_plan_A - ",
    "calques_gardes":  [ { "nom": "RS_TS", "sortie": "DWG_plan_A - RS_TS", "entites": 118 }, "…" ],
    "calques_ecartes": [ { "nom": "RESEAU_pompes alimentation", "entites": 3054,
                           "raison": "décoché dans l'appli" }, "…" ],
    "calques_renommes": {}, "types_supprimes": ["ATTDEF", "ATTRIB", "DIMENSION", "MULTILEADER", "…"],
    "types_supprimes_par_calque": {}, "hachures": "contour", "calques_nouveaux": "garder" },
  "zone": { "polygone_mn95": [[2510200.0, 1141720.0], [2510290.0, 1141720.0], "…"],
            "emprise_mn95": [2510200.0, 1141720.0, 2510290.0, 1141775.0], "marge_m": 0.0, "decouper": true },
  "ancres": { "unites": "unités du plan d'origine", "tolerance_m": 0.01, "nb": 600,
              "segments": [[15840.9298, -15261.8486, 20773.3481, -15261.8486], "…"] },
  "ajouts_manuels": {},
  "apprentissage": { "methode": "manuel" }
}
```

- `ancres` : ~600 segments longs du plan d'origine (dans SES unités) ; servent à vérifier qu'un nouvel
  indice a la même origine.
- Les noms de calques sont comparés **normalisés** (seulement A–Z/0–9, en majuscules) car les accents
  sont souvent abîmés à l'export d'autres logiciels.

## 4. La technique

- **Langage** : Python ≥ 3.11 (moteur + serveur) ; HTML/CSS/JavaScript « vanilla » (page web, sans
  framework ni étape de construction).
- **Bibliothèques Python** (voir `pyproject.toml`) :
  - `ezdxf` : lecture/écriture DXF, explosion des blocs (`virtual_entities`), transformations, lecture
    tolérante (`ezdxf.recover`) ;
  - `numpy`, `scipy` (`cKDTree`) : calculs, plus proches voisins (vote, ICP, appariement, accroche) ;
  - `matplotlib` : aperçu PNG (ligne de commande) et test « point dans polygone » ;
  - `pytest` pour les tests.
  - Serveur HTTP : **bibliothèque standard uniquement** (`http.server`) pour faciliter un futur
    empaquetage Windows (PyInstaller).
- **Lecture des DWG** : convertisseur externe obligatoire. Ordre : **ODA File Converter** (gratuit,
  détecté automatiquement sous `C:\Program Files\ODA\ODAFileConverter*\`, ou variable `CALAGE_ODA`),
  sinon **`dwg2dxf`** (LibreDWG) dans le PATH, sinon message demandant un DXF. Les conversions sont
  mises en cache dans le dossier temporaire (clé = empreinte SHA-1 du fichier).
- **Sortie** : DXF **R2000**, `$INSUNITS = 6` (mètres), blocs et objets complexes éclatés, seuls les
  types de ligne et styles de texte importés (pas les styles de cote), calques recréés avec couleur et
  type de ligne d'origine, accents corrects (encodage cp1252).
- **Interface → moteur** : la page appelle une API JSON locale (`calage_app/serveur.py`) :
  `GET /api/chantiers`, `GET /api/familles`, `POST /api/fichier?role=ref|plan|xref|indice|indice_xref&nom=…`
  (corps = octets du fichier), `POST /api/ouvrir`, `/api/reference_chantier`, `/api/accroche`,
  `/api/calage`, `/api/calage_auto`, `/api/exporter`, `/api/indice`, `/api/indice/valider`,
  `/api/indice/abandonner`, `/api/comparer`, `/api/chantier/creer`, `/api/chantier/reference`,
  `/api/supprimer`, `GET /telecharger?id=…`, `GET /telecharger_indice?id=chantier/plan/indice`.
  L'état de travail (fichiers déposés, calage en cours) est **en mémoire** dans `Session`.

### Structure des dossiers

```
calage_core/            moteur (aucune dépendance à l'interface)
  lecture.py            lecture DWG/DXF (ODA / dwg2dxf / recover), cache de conversion
  explosion.py          explosion récursive des blocs et xrefs, héritage calque 0 / DUBLOC, xrefs vides
  geometrie.py          classe Similitude (échelle, rotation, translation), segments, points d'échantillon,
                        normalisation des noms de calques
  points.py             calage par points et/ou axes (moindres carrés linéaires), vérification, verdict
  estimation.py         calage automatique : vote sur segments de même longueur + ICP (dans les 2 sens)
  controle.py           contrôle de concordance d'un nouvel indice (identique / recalé / refus, unité changée)
  application.py        rejeu d'une recette : nettoyage, zone (découpe), hachures, transformation, rapport
  export.py             écriture du DXF de sortie
  decoupe.py            découpe des polylignes au bord d'un polygone
  recette.py            format de la recette (JSON versionné)
  recette_manuelle.py   construction de la recette à partir des choix faits dans l'appli
  appariement.py        appariement d'objets entre deux plans superposés (familles, explosion des cotes)
  apprentissage.py      (ligne de commande) déduire une recette en comparant le plan et un DXF final fait à la main
  comparaison.py        taux d'objets retrouvés entre deux DXF (tests, contrôle qualité)
  vue.py                géométrie pour l'affichage (coordonnées relatives), accroche, état gelé/éteint
  apercu.py             aperçu PNG (ligne de commande)
calage_app/
  serveur.py            serveur HTTP local + routes de l'API
  session.py            état de travail, rangement chantiers/plans/indices, corbeille, comparaison
  static/index.html     la page (écrans)
  static/app.js         toute la logique de la page ; classe Vue = visualiseur canvas (zoom, survol, clic)
  static/style.css
calage/cli.py           ligne de commande : interface | apprendre | appliquer
tests/                  pytest (+ tests/e2e/parcours.js : parcours navigateur Playwright)
tests/data/             plans réels de test — NON versionnés (dépôt public), voir LISEZMOI.md
Installer.bat, Calage.bat   installation et lancement sous Windows (double-clic)
```

### Lancer

```bash
pip install -e .[test]
python -m calage interface                 # ouvre http://127.0.0.1:8765/
python -m calage interface --sans-navigateur --dossier /chemin/dossier --port 8765
python -m calage apprendre plan_A.dwg final_A.dxf -o recette.json
python -m calage appliquer plan_B.dwg -r recette.json -o B_MN95.dxf --ref reference.dwg --apercu B.png
```

Windows : installer Python (case « Add to PATH ») et ODA File Converter, puis `Installer.bat` puis `Calage.bat`.

### Tests

```bash
python -m pytest -q          # 27 tests ; ceux sur plans réels sont ignorés si tests/data est vide
```

Les fichiers attendus dans `tests/data/` : `plan_A.dwg`, `final_A.dxf`, `reference.dwg` (fournis par
l'utilisateur, non publiés). Parcours navigateur (serveur lancé sur un dossier vide, port 8765) :
`node tests/e2e/parcours.js <dossier_captures> <plan_B_decale.dxf>` (le plan B décalé est fabriqué par
les tests pytest dans leur dossier temporaire).

## 5. Décisions prises (non visibles dans le code)

- **Démarche initiale abandonnée comme flux principal** : au départ, l'appli devait *deviner* la recette
  en comparant le plan d'origine et le DXF final fait à la main dans Mensura (`apprendre`). L'utilisateur
  a préféré **tout faire dans l'appli** (calage par points/axes + nettoyage à l'écran), plus simple et sans
  erreur d'interprétation. `apprendre` reste disponible **en ligne de commande seulement**.
- **Calage** : 2 éléments de calage + 1 **vérification** (le mot « contrôle » a été jugé peu clair).
  Seuils de la vérification : < 1 cm = juste (vert), < 5 cm = à revoir (orange), au-delà = faux (rouge).
  L'échelle est arrondie à la puissance de 10 la plus proche si elle en est à moins de 0,5 %.
- **Axes** : l'utilisateur veut pouvoir caler avec des axes seuls (« 4 axes ») → accepté à partir de
  3 axes non concourants. 2 axes seuls sont refusés (échelle inconnue).
- **Nouvel indice** : contrôle avant tout. Même origine si ≥ 50 % des ancres sont retrouvées à < 1 cm ;
  sinon recalage automatique seulement si la confiance est forte (vote dominant ≥ 2, ≥ 15 votes,
  ≥ 50 % des ancres après correction) ; sinon **refus** sans fichier. Changement d'unité (mm au lieu de cm…)
  → refus avec message. Calques jamais vus → **gardés et signalés** par défaut.
- **Validation explicite** : un nouvel indice n'est enregistré qu'après « Enregistrer l'indice ».
- **Types de plan** : pas de liste prédéfinie ; l'utilisateur crée sa liste au fur et à mesure.
- **Comparaison** : superposition et différences à l'écran. **Refusé** : un export DXF des différences.
- **Calage automatique** : doit pouvoir se comparer à **n'importe quel** indice calé du chantier.
- **Hachures** : trois choix (garder / contour seulement / supprimer), mémorisés dans la recette.
- **Calques gelés / éteints du DWG** : écartés d'office (interprétation d'une demande de l'utilisateur,
  **à confirmer** avec lui).
- **Zone** : les traits sont coupés au bord ; les textes, cercles, hachures qui touchent la zone sont
  gardés entiers (**à confirmer** avec l'utilisateur).
- **Suppression** : toujours vers une corbeille, jamais d'effacement définitif.
- **Les DXF de sortie servent aussi bien dans Mensura que sur la tablette** (un seul format).
- **Confidentialité** : les plans réels ne sont jamais publiés (dépôt public) ; aucune donnée ne quitte
  le poste (serveur sur 127.0.0.1 uniquement).
- Les dessins ajoutés à la main par l'utilisateur dans Mensura (calque personnel) ne sont **jamais rejoués**.
- Empaquetage en exécutable Windows (PyInstaller) : prévu, **pas fait**.

## 6. Ce qui marche / pas essayé / à faire

**Marche (testé automatiquement, sous Linux)** sur un seul jeu réel (un plan d'ingénieur en cm, son DXF
final fait à la main, la référence du géomètre) :
- calage trouvé = échelle 0,01, rotation ≈ 121,416°, translation ≈ (2 510 211,85 ; 1 141 525,20), à 2 mm
  près des valeurs mesurées par l'utilisateur ; même résultat par 3 points, par 3 axes, et en automatique ;
- rejeu : 96 % des objets produits ont leur homologue dans le DXF final de l'utilisateur ;
- indice simulé décalé de 10 m / 5 m : détecté, corrigé, résultat identique à 1 cm ;
- plan sans rapport et plan en mm : refusés avec message ;
- parcours complet dans un navigateur (création de chantier → calage → nettoyage → indice → différences
  → calage automatique → suppression).

**Jamais essayé sur le terrain** :
- l'appli n'a **jamais été lancée sous Windows** par le développeur ; l'utilisateur n'a pas encore
  confirmé un essai complet ;
- la lecture DWG via **ODA File Converter** n'a jamais été testée (seulement LibreDWG `dwg2dxf` 0.13.3) ;
- aucun vrai indice B reçu n'a été traité (seulement des indices simulés) ;
- le calage automatique n'a été testé que sur un indice simulé du même plan ;
- les DXF produits n'ont pas été vérifiés dans le logiciel tablette.

**À faire / idées en attente** :
- empaquetage Windows (exécutable), nettoyage automatique du dossier `travail/` ;
- reprise des recettes de la toute première version (dossier `CalagePlans/projets/`, non lu aujourd'hui) ;
- suppression d'objets isolés (aujourd'hui : par calque, par type et par zone seulement) ;
- confirmer avec l'utilisateur les deux interprétations du § 5 (calques gelés, objets non coupés par la zone).

## 7. Pièges connus

- **Précision du navigateur** : les coordonnées MN95 (~2,5 millions de mètres) perdent les centimètres en
  simple précision ; le serveur envoie donc une géométrie **relative à une origine** et la page compose les
  transformations (voir `Vue.dessiner` dans `app.js` et `PlanAffiche` dans `vue.py`). À conserver.
- **ICP qui dérive** : si un plan contient beaucoup de géométrie absente de l'autre (réseaux, hachures),
  l'ICP dans un seul sens dérive (constaté : 0,00997 au lieu de 0,01, ou 0,6° d'erreur). Corrigé en
  lançant l'ICP **dans les deux sens** et en gardant le meilleur (`estimation.estimer`). Ne pas simplifier.
- **Lignes de repère (MULTILEADER)** : mal décodées par LibreDWG (textes déplacés de plusieurs mètres) ;
  elles sont supprimées par défaut.
- **DXF final de l'utilisateur** : ~360 petites polylignes (calque « trait autre ») n'existent pas dans le
  DWG tel que lu ; origine inconnue (flèches de cotes ? symboles ?). Sans impact sur le flux actuel.
- **Différences entre indices** : comparaison purement géométrique ; un objet supprimé qui existait en
  double au même endroit n'apparaît pas ; un objet déplacé apparaît deux fois (rouge + vert).
- **Accroche** : prend l'élément le plus proche dans un rayon de 14 pixels ; deux lignes parallèles à
  quelques cm (mur intérieur / façade) → il faut zoomer. La vérification détecte l'erreur.
- **État en mémoire** : redémarrer le serveur perd le calage en cours (pas ce qui est enregistré).
  Un seul utilisateur à la fois. Port 8765 fixe par défaut.
- **Zone en ligne de commande** (`apprendre`) : simple rectangle + marge, sans découpe.
- Noms de calques comparés sans accents ni ponctuation (deux calques ne différant que par un accent sont
  confondus).
- Chemins Windows longs (nom de chantier + nom de plan + date) : non testé.

## 8. Fichiers à reprendre en priorité

Pour copier la fonction dans une autre appli, par ordre d'importance :

1. `calage_core/geometrie.py` — `Similitude` (la transformation), utilitaires de base.
2. `calage_core/points.py` — calage par points/axes + vérification (cœur du calage manuel).
3. `calage_core/lecture.py` + `calage_core/explosion.py` — lire un DWG/DXF et l'aplatir.
4. `calage_core/application.py` + `calage_core/export.py` + `calage_core/decoupe.py` — rejouer une
   recette et écrire le DXF.
5. `calage_core/recette.py` + `calage_core/recette_manuelle.py` — format de la recette.
6. `calage_core/controle.py` + `calage_core/estimation.py` (+ `appariement.py`) — contrôle des nouveaux
   indices et calage automatique.
7. `calage_core/vue.py` + la classe `Vue` de `calage_app/static/app.js` — affichage et accroche.
8. `calage_app/session.py` + `calage_app/serveur.py` — rangement sur disque et API, à adapter au
   stockage de l'appli d'accueil.

Remarque pour l'intégration : le moteur est en **Python** et dépend d'`ezdxf` et d'un convertisseur DWG
externe. Si l'appli d'accueil est une appli web/JavaScript, le plus simple est de garder ce moteur comme
**service local** appelé par l'API HTTP ci-dessus ; un portage complet en JavaScript demanderait de
remplacer `ezdxf` (lecture/écriture DXF, explosion des blocs) et la conversion DWG.
