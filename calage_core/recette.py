"""Recette de calage : fichier JSON versionné, lisible et modifiable à la main."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .geometrie import Similitude, nom_normalise

VERSION_RECETTE = 1

UNITES_INSUNITS = {0: "non précisée", 1: "pouce", 2: "pied", 4: "mm", 5: "cm", 6: "m", 7: "km"}


class ErreurRecette(Exception):
    pass


@dataclass
class CalqueGarde:
    nom: str                       # nom dans le plan d'origine (accents corrects)
    sortie: str                    # nom écrit dans le DXF de sortie
    entites: int = 0               # entités du calque lors de l'apprentissage
    retrouvees: int = 0            # dont retrouvées dans le final


@dataclass
class CalqueEcarte:
    nom: str
    entites: int = 0
    raison: str = "absent du final"


@dataclass
class Recette:
    version: int = VERSION_RECETTE
    outil: str = ""
    cree_le: str = ""
    source: dict = field(default_factory=dict)          # fichiers, unités, empreintes
    transformation: dict = field(default_factory=dict)  # échelle, rotation, translation + qualité
    nettoyage: dict = field(default_factory=dict)       # calques, types, préfixe, explosion
    zone: dict = field(default_factory=dict)            # emprise MN95 + marge
    ancres: dict = field(default_factory=dict)          # segments longs du plan (unités plan)
    ajouts_manuels: dict = field(default_factory=dict)  # listés, jamais rejoués
    apprentissage: dict = field(default_factory=dict)   # statistiques de contrôle

    # --- accès pratiques ------------------------------------------------------------------
    @property
    def similitude(self) -> Similitude:
        return Similitude.depuis_dict(self.transformation)

    def calques_gardes(self) -> dict[str, dict]:
        """nom normalisé -> entrée « calque gardé »."""
        return {nom_normalise(c["nom"]): c for c in self.nettoyage.get("calques_gardes", [])}

    def calques_ecartes(self) -> dict[str, dict]:
        return {nom_normalise(c["nom"]): c for c in self.nettoyage.get("calques_ecartes", [])}

    # --- entrées / sorties ----------------------------------------------------------------
    def enregistrer(self, chemin: str | Path) -> None:
        d = asdict(self)
        texte = json.dumps(d, ensure_ascii=False, indent=2)
        # les ancres sont nombreuses : une ligne par segment pour rester lisible
        Path(chemin).write_text(_compacter_listes(texte), encoding="utf-8")

    @classmethod
    def charger(cls, chemin: str | Path) -> "Recette":
        try:
            d = json.loads(Path(chemin).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ErreurRecette(f"Recette introuvable : {chemin}")
        except json.JSONDecodeError as ex:
            raise ErreurRecette(f"La recette « {chemin} » n'est pas un JSON valide ({ex}).")
        v = d.get("version")
        if v != VERSION_RECETTE:
            raise ErreurRecette(
                f"Version de recette {v} non prise en charge (attendue : {VERSION_RECETTE}).")
        champs = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in champs})


def _compacter_listes(texte: str) -> str:
    """Met les petites listes de nombres sur une seule ligne (JSON plus lisible)."""
    import re
    motif = re.compile(r"\[\s*(-?[\d.eE+-]+(?:,\s*-?[\d.eE+-]+)*)\s*\]")
    return motif.sub(lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",")) + "]", texte)
