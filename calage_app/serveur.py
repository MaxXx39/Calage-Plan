"""Petit serveur HTTP local (bibliothèque standard uniquement : facile à empaqueter).

Écoute UNIQUEMENT sur 127.0.0.1 : aucun fichier ne quitte le poste.
"""
from __future__ import annotations

import json
import mimetypes
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from calage_core.lecture import ErreurLecture
from calage_core.recette import ErreurRecette

from .session import ErreurAppli, Session

STATIQUE = Path(__file__).parent / "static"
TAILLE_MAX = 300 * 1024 * 1024


def fabriquer_gestionnaire(session: Session):
    class Gestionnaire(BaseHTTPRequestHandler):
        server_version = "CalagePlans/1"

        def log_message(self, fmt, *args):   # console silencieuse
            pass

        # -------------------------------------------------------------- utilitaires
        def _json(self, obj, code=200):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _corps(self) -> bytes:
            n = int(self.headers.get("Content-Length", 0))
            if n > TAILLE_MAX:
                raise ErreurAppli("Fichier trop volumineux.")
            return self.rfile.read(n) if n else b""

        def _fichier(self, chemin: Path, nom_telechargement: str | None = None):
            data = chemin.read_bytes()
            self.send_response(200)
            type_ = mimetypes.guess_type(chemin.name)[0] or "application/octet-stream"
            if chemin.suffix == ".js":
                type_ = "text/javascript"
            self.send_header("Content-Type", type_ + ("; charset=utf-8" if type_.startswith("text") else ""))
            self.send_header("Content-Length", str(len(data)))
            if nom_telechargement:
                q = urllib.parse.quote(nom_telechargement)
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{q}")
            self.end_headers()
            self.wfile.write(data)

        # -------------------------------------------------------------- GET
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            try:
                if url.path in ("/", "/index.html"):
                    return self._fichier(STATIQUE / "index.html")
                if url.path.startswith("/static/"):
                    f = (STATIQUE / url.path[len("/static/"):]).resolve()
                    if STATIQUE.resolve() in f.parents and f.is_file():
                        return self._fichier(f)
                    return self._json({"erreur": "introuvable"}, 404)
                if url.path == "/api/projets":
                    return self._json(session.projets())
                if url.path == "/api/familles":
                    return self._json(session.familles())
                if url.path == "/telecharger":
                    f = session.chemin_telechargement(q.get("id", [""])[0])
                    if f is None or not f.exists():
                        return self._json({"erreur": "fichier introuvable"}, 404)
                    return self._fichier(f, f.name)
                return self._json({"erreur": "introuvable"}, 404)
            except Exception as ex:
                return self._erreur(ex)

        # -------------------------------------------------------------- POST
        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            try:
                if url.path == "/api/fichier":
                    return self._json(session.deposer(q.get("role", [""])[0], q.get("nom", [""])[0],
                                                      self._corps()))
                d = json.loads(self._corps() or b"{}")
                if url.path == "/api/ouvrir":
                    return self._json(session.ouvrir(d["role"]))
                if url.path == "/api/accroche":
                    return self._json(session.accrocher(d["role"], float(d["x"]), float(d["y"]),
                                                        float(d["rayon"])))
                if url.path == "/api/calage":
                    return self._json(session.caler(d["calage"], d.get("controle", [])))
                if url.path == "/api/exporter":
                    return self._json(session.exporter(d["projet"], d.get("prefixe", ""),
                                                       d["calques"], d.get("familles", []),
                                                       d.get("zone"), d.get("renommages")))
                if url.path == "/api/indice":
                    return self._json(session.caler_indice(d["projet"], bool(d.get("forcer")),
                                                           bool(d.get("ecarter_nouveaux"))))
                return self._json({"erreur": "introuvable"}, 404)
            except Exception as ex:
                return self._erreur(ex)

        def _erreur(self, ex):
            if isinstance(ex, (ErreurAppli, ErreurLecture, ErreurRecette)):
                return self._json({"erreur": str(ex)}, 400)
            traceback.print_exc()
            return self._json({"erreur": f"Erreur inattendue : {ex}"}, 500)

    return Gestionnaire


def lancer(port: int = 8765, ouvrir_navigateur: bool = True, racine=None):
    session = Session(racine)
    serveur = ThreadingHTTPServer(("127.0.0.1", port), fabriquer_gestionnaire(session))
    url = f"http://127.0.0.1:{serveur.server_address[1]}/"
    print(f"Calage de plans : ouvrez {url}  (Ctrl+C pour arrêter)")
    print(f"Dossier de travail : {session.racine}")
    if ouvrir_navigateur:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.server_close()
