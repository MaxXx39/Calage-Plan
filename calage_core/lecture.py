"""Lecture des plans DWG / DXF.

Les DWG sont convertis en DXF temporaire avec, dans l'ordre :
  1. ODA File Converter (via ezdxf.addons.odafc) s'il est installé ;
  2. `dwg2dxf` (LibreDWG) s'il est dans le PATH ;
sinon un message clair demande d'exporter le plan en DXF.

La lecture DXF est tolérante (ezdxf.recover) : les DWG convertis contiennent souvent
de petites incohérences que ezdxf sait réparer.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf import recover


class ErreurLecture(Exception):
    """Plan illisible : le message est destiné à l'utilisateur final."""


@dataclass
class PlanLu:
    """Document lu + informations utiles au rapport."""
    chemin: Path
    doc: "ezdxf.document.Drawing"
    convertisseur: str | None = None          # "odafc", "dwg2dxf" ou None (DXF natif)
    avertissements: list[str] = field(default_factory=list)

    @property
    def insunits(self) -> int:
        return int(self.doc.header.get("$INSUNITS", 0) or 0)


# Cache des conversions DWG -> DXF (clé = empreinte du fichier) pour éviter de
# reconvertir le même fichier plusieurs fois dans une session (tests, aperçus...).
_DOSSIER_CACHE = Path(tempfile.gettempdir()) / "calage_plans_cache"


def _empreinte(chemin: Path) -> str:
    h = hashlib.sha1()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def _chercher_oda_windows() -> str | None:
    """Sous Windows, ODA s'installe dans un dossier qui porte son numéro de version
    (« C:\\Program Files\\ODA\\ODAFileConverter 26.4.0 ») : on prend le plus récent.
    La variable d'environnement CALAGE_ODA permet d'indiquer le chemin à la main."""
    import glob
    import platform
    if platform.system() != "Windows":
        return None
    manuel = os.environ.get("CALAGE_ODA")
    if manuel and Path(manuel).is_file():
        return manuel
    trouves = []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        trouves += glob.glob(os.path.join(base, "ODA", "ODAFileConverter*", "ODAFileConverter.exe"))
    return sorted(trouves)[-1] if trouves else None


def _odafc_disponible() -> bool:
    try:
        from ezdxf.addons import odafc
        if odafc.is_installed():
            return True
        chemin = _chercher_oda_windows()
        if chemin:
            ezdxf.options.set("odafc-addon", "win_exec_path", f'"{chemin}"')
            return bool(odafc.is_installed())
        return False
    except Exception:
        return False


def _convertir_dwg(chemin: Path) -> tuple[Path, str]:
    """Convertit un DWG en DXF (fichier en cache) et renvoie (chemin_dxf, convertisseur)."""
    _DOSSIER_CACHE.mkdir(parents=True, exist_ok=True)
    cible = _DOSSIER_CACHE / f"{_empreinte(chemin)}.dxf"
    if cible.exists() and cible.stat().st_size > 0:
        return cible, "cache"

    if _odafc_disponible():
        from ezdxf.addons import odafc
        try:
            odafc.convert(str(chemin), str(cible), version="R2018", audit=False, replace=True)
            if cible.exists():
                return cible, "odafc"
        except Exception:
            pass  # on tente LibreDWG

    exe = shutil.which("dwg2dxf")
    if exe:
        tmp = cible.with_suffix(".tmp.dxf")
        try:
            subprocess.run([exe, "-y", "-o", str(tmp), str(chemin)],
                           capture_output=True, timeout=600, check=False)
        except Exception as ex:  # pragma: no cover - dépend de l'installation
            raise ErreurLecture(f"La conversion du DWG a échoué ({ex}).") from ex
        if tmp.exists() and tmp.stat().st_size > 0:
            os.replace(tmp, cible)
            return cible, "dwg2dxf"
        raise ErreurLecture(
            f"Le fichier « {chemin.name} » n'a pas pu être converti. "
            "Ouvrez-le dans votre logiciel de DAO et enregistrez-le en DXF.")

    raise ErreurLecture(
        "Impossible de lire les fichiers DWG sur ce poste : aucun convertisseur trouvé.\n"
        "Solutions : installer « ODA File Converter » (gratuit), ou exporter le plan "
        "en DXF depuis votre logiciel de DAO et redonner le fichier DXF.")


def lire_plan(chemin: str | os.PathLike) -> PlanLu:
    """Lit un DWG ou un DXF et renvoie un PlanLu. Lève ErreurLecture avec un message clair."""
    chemin = Path(chemin)
    if not chemin.exists():
        raise ErreurLecture(f"Fichier introuvable : {chemin}")
    ext = chemin.suffix.lower()
    convertisseur = None
    source = chemin
    if ext == ".dwg":
        source, convertisseur = _convertir_dwg(chemin)
    elif ext != ".dxf":
        raise ErreurLecture(f"Format non reconnu « {ext} » : donnez un fichier .dwg ou .dxf.")

    try:
        doc, auditor = recover.readfile(str(source))
    except Exception as ex:
        raise ErreurLecture(f"Le fichier « {chemin.name} » est illisible ou abîmé ({ex}).") from ex

    plan = PlanLu(chemin=chemin, doc=doc, convertisseur=convertisseur)
    if auditor.has_errors:
        plan.avertissements.append(
            f"{len(auditor.errors)} erreur(s) de structure réparée(s) à la lecture.")
    return plan
