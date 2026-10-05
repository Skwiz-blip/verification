"""Serveur local de l'interface de controle mensuel.

Usage :
    python -m socialstats.server
    python -m socialstats.server --port 8060 --no-browser
    python -m socialstats.server --lan

Par defaut le serveur n'ecoute que sur la boucle locale. Avec `--lan`, les
postes du reseau local peuvent CONSULTER l'interface ; il n'y a pas de mot de
passe, et l'ajout d'exports reste reserve au poste qui heberge le serveur.

Il sert l'interface (HTML, CSS, JS du dossier `web/`) et expose le pipeline
existant en JSON. Aucun calcul n'est refait cote navigateur : il n'existe
ainsi qu'une seule version des objectifs, celle de `objectives.py`.

Les reponses ne contiennent que des faits et des codes. Tous les libelles
affiches sont rediges par l'interface.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import socket
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

from .cleaner import CleaningResult, run_cleaning
from .config import UNCATEGORIZED, ConfigError, Settings, load_settings
from .ingest import ADDED, DUPLICATE, ImportOutcome, import_export
from .loader import LoaderError
from .monthly import (
    BELOW,
    LEVEL_1,
    LEVEL_2,
    LEVEL_3,
    NO_OBJECTIVE,
    NO_POST,
    REFERENCE_BEFORE,
    REFERENCE_PRESETS,
    TOO_FEW,
    TOO_FEW_ACCOUNTS,
    available_months,
    category_results,
    default_month,
    measurement_breaks,
    month_view,
    monthly_results,
    reference_bounds,
    slice_months,
    views_per_reach,
)
from .objectives import build_objectives, build_sector_objectives
from .schema_mapper import SchemaError

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8050
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_CACHED_CONTROLS = 64
TEXT_EXCERPT = 280

# Un en-tete personnalise ne peut pas etre envoye par un simple formulaire d'un
# autre site : il empeche une page tierce de deposer un fichier a l'insu de
# l'utilisateur.
UPLOAD_HEADER = "X-Requested-With"
UPLOAD_HEADER_VALUE = "socialstats"

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}

VERDICT_CODES = {
    LEVEL_3: "n3",
    LEVEL_2: "n2",
    LEVEL_1: "n1",
    BELOW: "below",
    TOO_FEW: "few",
    TOO_FEW_ACCOUNTS: "fewc",
    NO_OBJECTIVE: "none",
    NO_POST: "nopost",
}
IMPORT_CODES = {ADDED: "added", DUPLICATE: "duplicate"}

RESULT_FIELDS = {
    "Compte": "compte",
    "Client": "client",
    "Secteur": "secteur",
    "Plateforme": "plateforme",
    "Format": "format",
    "Mois": "mois",
    "Publications": "publications",
    "Vues du mois": "vues",
    "Mediane du mois": "mediane",
    "Niveau 1": "n1",
    "Niveau 2": "n2",
    "Niveau 3": "n3",
    "Atteinte N1 (%)": "att1",
    "Atteinte N2 (%)": "att2",
    "Atteinte N3 (%)": "att3",
    "Ecart au Niveau 1 (%)": "ecart",
    "Niveau atteint": "niveau",
}
CATEGORY_FIELDS = {
    "Secteur": "secteur",
    "Plateforme": "plateforme",
    "Format": "format",
    "Mois": "mois",
    "Comptes actifs": "comptes_actifs",
    "Publications": "publications",
    "Comptes evalues": "comptes_evalues",
    "Mediane des comptes": "mediane",
    "Comptes avec objectif": "comptes_objectif",
    "Comptes a leur Niveau 1 ou plus": "comptes_n1",
    "Niveau 1": "n1",
    "Niveau 2": "n2",
    "Niveau 3": "n3",
    "Dispersion inter-comptes N1": "dispersion",
    "derogation": "derogation",
    "heterogene": "heterogene",
    "Niveau atteint": "niveau",
}
RATIO_FIELDS = {
    "Plateforme": "plateforme",
    "Format": "format",
    "Mois": "mois",
    "Publications": "publications",
    "Vues par personne touchee": "ratio",
}
BREAK_FIELDS = {
    "Plateforme": "plateforme",
    "Format": "format",
    "Mois haut": "mois_haut",
    "Ratio haut": "ratio_haut",
    "Mois bas": "mois_bas",
    "Ratio bas": "ratio_bas",
    "Rapport": "rapport",
}


def host_allowed(name: str, lan: bool = False, machine_names: frozenset[str] = frozenset()) -> bool:
    """Nom sous lequel le serveur accepte d'etre appele (en-tetes Host et Origin).

    Un rebinding DNS passe toujours par un nom de domaine etranger. En mode
    reseau, une adresse IP litterale ou le nom de ce poste restent donc surs,
    sans dependre de la liste des interfaces relevee au demarrage.
    """
    if name in LOCAL_HOSTS:
        return True
    if not lan:
        return False
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name in machine_names
    return True


def client_allowed(address: str, lan: bool = False) -> bool:
    """Poste autorise a consulter : cette machine, et en mode reseau une adresse privee.

    Meme si le port se retrouvait expose plus largement (redirection sur la
    box, interface mobile), une adresse publique n'obtient rien.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_loopback or (lan and (ip.is_private or ip.is_link_local))


def is_this_machine(address: str, own_addresses: frozenset[str] = frozenset()) -> bool:
    """Vrai si la requete vient du poste qui heberge le serveur."""
    try:
        return ipaddress.ip_address(address).is_loopback or address in own_addresses
    except ValueError:
        return False


def machine_addresses() -> list[str]:
    """Adresses IPv4 de ce poste, celle de la route par defaut en premier."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # Aucun paquet n'est emis : seule la table de routage est interrogee.
            probe.connect(("192.0.2.1", 9))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    return list(dict.fromkeys(a for a in found if not a.startswith("127.")))


def _period(value: object) -> pd.Period | None:
    """Mois 'AAAA-MM' recu du navigateur ; None s'il est absent ou illisible."""
    if not value:
        return None
    try:
        return pd.Period(str(value), freq="M")
    except (ValueError, TypeError):
        return None


def _int(value: object, default: int) -> int:
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return default


def _records(frame: pd.DataFrame, fields: dict[str, str]) -> list[dict[str, object]]:
    """Table -> objets JSON : mois en texte, verdicts en codes, NaN en null."""
    if frame.empty:
        return []
    out = frame[list(fields)].rename(columns=fields)
    for column in out.columns:
        if column.startswith("mois"):
            out[column] = out[column].astype(str)
    if "niveau" in out.columns:
        out["niveau"] = out["niveau"].map(VERDICT_CODES)
    return json.loads(out.to_json(orient="records", force_ascii=False))


def _data_summary(cleaning: CleaningResult, settings: Settings) -> dict[str, object]:
    """Ce qui a ete lu, ecarte et retenu, pour l'onglet de controle des donnees."""
    data = cleaning.data
    by_file = data.groupby("source_file")["published_at"].agg(["size", "min", "max"])
    files = []
    for report in cleaning.mapping_reports:
        kept = by_file.loc[report.file_name] if report.file_name in by_file.index else None
        files.append(
            {
                "nom": report.file_name,
                "plateforme": report.platform,
                "publications": 0 if kept is None else int(kept["size"]),
                "debut": None if kept is None else f"{kept['min']:%Y-%m-%d}",
                "fin": None if kept is None else f"{kept['max']:%Y-%m-%d}",
                "colonnes_utilisees": report.n_used_columns,
                "colonnes": report.n_source_columns,
            }
        )
    uncategorized = sorted(data.loc[data["sector"] == UNCATEGORIZED, "account_name"].unique())
    return {
        "dossier": str(settings.raw_dir),
        "fichiers": files,
        "ignores": list(cleaning.skipped_files),
        "sans_categorie": uncategorized,
        "nettoyage": {
            "lignes_lues": cleaning.sponsorship.n_total if cleaning.sponsorship else None,
            "sponsorisees": cleaning.sponsorship.n_sponsored if cleaning.sponsorship else None,
            "doublons": cleaning.dedup.n_rows_merged if cleaning.dedup else None,
            "quarantaine": cleaning.validation.n_rejected if cleaning.validation else None,
            "hors_periode": cleaning.n_period_excluded,
            "comptes_ecartes": list(cleaning.dropped_accounts),
            "seuil_compte": settings.min_publications_per_account,
            "retenues": int(len(data)),
        },
    }


def build_control(
    cleaning: CleaningResult,
    settings: Settings,
    month: object = None,
    preset: str = REFERENCE_BEFORE,
    start: object = None,
    end: object = None,
    min_posts: int = 3,
) -> dict[str, object]:
    """Assemble tout ce que l'interface affiche pour un mois et une reference.

    Les parametres viennent du navigateur : chacun est ramene a une valeur
    valide plutot que rejete, et les valeurs effectivement appliquees sont
    renvoyees dans `meta` pour que l'interface s'y aligne.
    """
    data = cleaning.data
    months = available_months(data)
    if not months:
        return {"vide": True, "detail": ""}

    month = _period(month)
    if month not in months:
        month = default_month(data)
    if preset not in REFERENCE_PRESETS:
        preset = REFERENCE_BEFORE
    min_posts = max(1, min(int(min_posts), 30))

    bounds = reference_bounds(preset, months, month, _period(start), _period(end))
    no_history = bounds is None
    ref_start, ref_end = bounds or (months[0], months[-1])
    reference = slice_months(data, ref_start, ref_end)

    objectives = build_objectives(reference, settings)
    sector_objectives = build_sector_objectives(reference, settings)
    results = monthly_results(data, objectives, settings, min_posts)
    categories = category_results(results, sector_objectives, min_posts, settings)
    if not categories.empty:
        has_levels = categories["Niveau 1"].notna()
        categories["derogation"] = has_levels & categories["Secteur"].map(settings.sector.is_forced)
        categories["heterogene"] = (
            categories["Dispersion inter-comptes N1"] > settings.objectives.max_level_spread
        )
    ratios = views_per_reach(reference, settings)

    return {
        "vide": False,
        "meta": {
            "mois": [str(m) for m in months],
            "mois_controle": str(month),
            "ref": {
                "preset": preset,
                "debut": str(ref_start),
                "fin": str(ref_end),
                "mois": [str(m) for m in months if ref_start <= m <= ref_end],
                "publications": int(len(reference)),
                "inclut_mois": bool(ref_start <= month <= ref_end),
                "sans_historique": no_history,
            },
            "min": min_posts,
            "min_observations": settings.min_observations,
            "publications": int(len(data)),
            "fichiers": len(cleaning.mapping_reports),
        },
        "ruptures": _records(measurement_breaks(ratios), BREAK_FIELDS),
        "resultats": _records(month_view(results, objectives, month), RESULT_FIELDS),
        "historique": _records(results, RESULT_FIELDS),
        "categories": _records(categories, CATEGORY_FIELDS),
        "ratios": _records(ratios, RATIO_FIELDS),
        "donnees": _data_summary(cleaning, settings),
    }


def build_posts(
    cleaning: CleaningResult, account: str, platform: str, fmt: str, month: object
) -> list[dict[str, object]]:
    """Publications d'un compte x plateforme x format sur un mois."""
    data = cleaning.data
    period = _period(month)
    if period is None or data.empty:
        return []
    posts = data[
        (data["account_name"] == account)
        & (data["platform"] == platform)
        & (data["format"] == fmt)
        & (data["published_at"].dt.to_period("M") == period)
    ].sort_values("published_at", ascending=False)
    if posts.empty:
        return []

    def column(name: str) -> pd.Series:
        return posts[name] if name in posts.columns else pd.Series(pd.NA, index=posts.index)

    links = column("permalink").astype("string")
    table = pd.DataFrame(
        {
            "date": posts["published_at"].dt.strftime("%Y-%m-%dT%H:%M"),
            "vues": pd.to_numeric(posts["views_organic_resolved"], errors="coerce").astype("float64"),
            "couverture": pd.to_numeric(column("engagement_base"), errors="coerce").astype("float64"),
            "engagement": pd.to_numeric(column("engagement_rate"), errors="coerce")
            .astype("float64")
            .round(2),
            # Seuls les liens web sont transmis : le navigateur en fera des liens cliquables.
            "lien": links.where(links.str.startswith(("http://", "https://"), na=False)),
            "texte": column("description").astype("string").str.slice(0, TEXT_EXCERPT),
        }
    )
    return json.loads(table.to_json(orient="records", force_ascii=False))


def outcome_payload(outcome: ImportOutcome) -> dict[str, object]:
    stats = outcome.stats
    return {
        "nom": outcome.name,
        "statut": IMPORT_CODES.get(outcome.status, "rejected"),
        "detail": outcome.detail,
        "publications": stats.get("publications"),
        "plateforme": stats.get("platform"),
        "comptes": stats.get("accounts"),
        "debut": stats.get("start"),
        "fin": stats.get("end"),
    }


class DataStore:
    """Jeu nettoye et reponses calculees, refaits seulement quand un fichier change."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = Path(config_dir)
        self._lock = threading.Lock()
        self._signature: tuple | None = None
        self._settings: Settings | None = None
        self._cleaning: CleaningResult | None = None
        self._controls: dict[tuple, dict[str, object]] = {}

    def _current(self) -> tuple[Settings, CleaningResult]:
        """Etat a jour des donnees. A appeler sous verrou."""
        raw_dir = self.config_dir.parent / "data" / "raw"
        paths = sorted(self.config_dir.glob("*"))
        if raw_dir.exists():
            paths += sorted(raw_dir.glob("*.csv"))
        signature = tuple(
            (str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths if p.is_file()
        )
        if signature != self._signature or self._cleaning is None or self._settings is None:
            settings = load_settings(self.config_dir)
            self._cleaning = run_cleaning(settings)
            self._settings = settings
            self._signature = signature
            self._controls.clear()
        return self._settings, self._cleaning

    def control(self, **params: object) -> dict[str, object]:
        with self._lock:
            settings, cleaning = self._current()
            key = tuple(sorted((name, str(value)) for name, value in params.items()))
            if key not in self._controls:
                if len(self._controls) >= MAX_CACHED_CONTROLS:
                    self._controls.clear()
                self._controls[key] = build_control(cleaning, settings, **params)
            return self._controls[key]

    def posts(self, account: str, platform: str, fmt: str, month: object) -> list[dict[str, object]]:
        with self._lock:
            _, cleaning = self._current()
            return build_posts(cleaning, account, platform, fmt, month)

    def upload(self, name: str, content: bytes) -> ImportOutcome:
        with self._lock:
            return import_export(name, content, load_settings(self.config_dir))


class ControlHandler(BaseHTTPRequestHandler):
    """Routes de l'interface. Les attributs sont fixes par `make_server`."""

    store: DataStore
    web_dir: Path
    lan: bool = False
    machine_names: frozenset[str] = frozenset()
    own_addresses: frozenset[str] = frozenset()
    server_version = "socialstats"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.debug("%s %s", self.address_string(), format % args)

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in RESPONSE_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"erreur": message}, status)

    def _accepted(self) -> bool:
        """Filtre commun a toutes les routes : qui appelle, et sous quel nom."""
        host = (urlparse(f"//{self.headers.get('Host') or ''}").hostname or "").lower()
        return client_allowed(self.client_address[0], self.lan) and host_allowed(
            host, self.lan, self.machine_names
        )

    def _can_import(self) -> bool:
        """L'ajout d'exports modifie les donnees de tous : il reste reserve a ce poste."""
        return is_this_machine(self.client_address[0], self.own_addresses)

    def _respond(self, compute, extra: dict[str, object] | None = None) -> None:
        """Execute un calcul et traduit ses echecs en reponses propres.

        `extra` complete la reponse, y compris quand aucun export n'est charge.
        """
        try:
            payload = compute()
        except (LoaderError, SchemaError) as exc:
            # Aucun export exploitable : etat normal d'une installation neuve.
            payload = {"vide": True, "detail": str(exc)}
        except ConfigError as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Configuration invalide : {exc}")
            return
        except Exception:
            logger.exception("Echec du traitement de %s", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Erreur interne, voir le journal du serveur.")
            return
        self._json({**payload, **extra} if extra else payload)

    def do_GET(self) -> None:  # noqa: N802
        if not self._accepted():
            self._error(HTTPStatus.FORBIDDEN, "Hote non autorise.")
            return
        url = urlparse(self.path)
        query = {name: values[-1] for name, values in parse_qs(url.query).items()}

        if url.path in STATIC_FILES:
            file_name, content_type = STATIC_FILES[url.path]
            try:
                body = (self.web_dir / file_name).read_bytes()
            except OSError:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Fichier d'interface manquant : {file_name}")
                return
            self._send(HTTPStatus.OK, body, content_type)
        elif url.path == "/api/controle":
            # La reponse calculee est partagee entre les postes ; le droit
            # d'importer depend de celui qui demande et s'ajoute a la volee.
            self._respond(
                lambda: self.store.control(
                    month=query.get("mois"),
                    preset=query.get("ref", REFERENCE_BEFORE),
                    start=query.get("debut"),
                    end=query.get("fin"),
                    min_posts=_int(query.get("min"), 3),
                ),
                extra={"import_autorise": self._can_import()},
            )
        elif url.path == "/api/publications":
            self._respond(
                lambda: {
                    "publications": self.store.posts(
                        query.get("compte", ""),
                        query.get("plateforme", ""),
                        query.get("format", ""),
                        query.get("mois"),
                    )
                }
            )
        else:
            self._error(HTTPStatus.NOT_FOUND, "Ressource introuvable.")

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        origin = urlparse(self.headers.get("Origin") or "").hostname
        if (
            not self._accepted()
            or (origin is not None and not host_allowed(origin, self.lan, self.machine_names))
            or self.headers.get(UPLOAD_HEADER) != UPLOAD_HEADER_VALUE
        ):
            self._error(HTTPStatus.FORBIDDEN, "Requete refusee.")
            return
        if not self._can_import():
            self._error(
                HTTPStatus.FORBIDDEN,
                "L'ajout d'exports se fait depuis le poste qui heberge l'interface.",
            )
            return
        if url.path != "/api/import":
            self._error(HTTPStatus.NOT_FOUND, "Ressource introuvable.")
            return

        length = _int(self.headers.get("Content-Length"), -1)
        if length <= 0:
            self._error(HTTPStatus.LENGTH_REQUIRED, "Fichier vide ou taille non declaree.")
            return
        if length > MAX_UPLOAD_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Fichier trop volumineux.")
            return

        name = parse_qs(url.query).get("nom", [""])[-1]
        content = self.rfile.read(length)
        self._respond(lambda: outcome_payload(self.store.upload(name, content)))


def make_server(
    config_dir: Path,
    web_dir: Path,
    port: int = DEFAULT_PORT,
    lan: bool = False,
    bind: str | None = None,
) -> ThreadingHTTPServer:
    """Construit le serveur.

    Sans `lan`, il n'est lie qu'a la boucle locale. Avec `lan`, il ecoute sur
    toutes les interfaces et filtre lui-meme les appelants. `bind` force
    l'adresse d'ecoute, pour eprouver le mode reseau sans ouvrir de port.
    """
    hostname = socket.gethostname().lower()
    handler = type(
        "BoundControlHandler",
        (ControlHandler,),
        {
            "store": DataStore(Path(config_dir)),
            "web_dir": Path(web_dir),
            "lan": lan,
            "machine_names": frozenset({hostname, socket.getfqdn().lower()}),
            "own_addresses": frozenset(machine_addresses()),
        },
    )
    return ThreadingHTTPServer((bind or ("0.0.0.0" if lan else "127.0.0.1"), port), handler)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        prog="socialstats.server",
        description="Interface de controle mensuel des performances (serveur local).",
    )
    parser.add_argument("--config", type=Path, default=root / "config",
                        help="Dossier de configuration (defaut : ./config)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port d'ecoute local (defaut : {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true",
                        help="N'ouvre pas le navigateur au demarrage")
    parser.add_argument("--lan", action="store_true",
                        help="Ouvre la consultation aux postes du reseau local (sans mot de passe)")
    parser.add_argument("--verbose", action="store_true", help="Journalise chaque requete")
    args = parser.parse_args(argv)

    # Par defaut la console ne montre que les anomalies : le detail du nettoyage
    # est deja lisible dans l'onglet "Donnees" de l'interface.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    try:
        server = make_server(args.config, root / "web", args.port, lan=args.lan)
    except OSError as exc:
        logger.error(
            "Port %d indisponible (%s). Relancez avec --port %d.", args.port, exc, args.port + 1
        )
        return 1

    url = f"http://127.0.0.1:{args.port}/"
    print(f"Interface disponible sur {url}  (Ctrl+C pour arreter)")
    if args.lan:
        shared = [a for a in machine_addresses() if client_allowed(a, lan=True)]
        print("Mode reseau : consultation seule pour les autres postes, sans mot de passe.")
        for address in shared:
            print(f"  Adresse a leur donner : http://{address}:{args.port}/")
        if not shared:
            print("  Aucune adresse de reseau local detectee sur ce poste.")
        print(f"  Si la page ne s'ouvre pas chez eux, le pare-feu bloque le port {args.port}.")
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArret du serveur.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
