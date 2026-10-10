"""Publication des resultats vers Supabase, pour l'interface hebergee en ligne.

Usage :
    python -m socialstats.publish                    # archive data/raw, calcule, publie
    python -m socialstats.publish --dry-run          # calcule a partir de data/raw, n'envoie rien
    python -m socialstats.publish --no-exports       # sans envoyer data/raw
    python -m socialstats.publish --only-if-pending  # seulement si un export attend (GitHub Actions)

Le depot prive `exports` de Supabase est la source des calculs : il reunit
les exports archives depuis ce poste (a sa racine) et ceux deposes sur le
site (dans `imports/`, chacun trace par une ligne de la table `imports`).
La publication les telecharge tous, statue sur chaque export depose (retenu,
deja charge ou refuse), recalcule avec le pipeline, puis publie. Qu'elle
tourne sur ce poste ou dans GitHub Actions, elle part donc des memes fichiers.

Ce qui part :
  - `controles`    une reponse precalculee par mois x periode de reference,
                   au format exact de l'API locale `/api/controle` ;
  - `publications` les publications organiques retenues (l'historique) ;
  - `etat`         les mois et references publies, et la date de publication ;
  - `imports`      le sort de chaque export depose sur le site.

Deux cles, deux roles. La cle SECRETE ecrit : elle se lit dans `.env`, qui
n'est pas versionne, et ne quitte jamais ce poste. La cle PUBLIQUE ne permet
que de lire : c'est la seule qui soit ecrite dans `web/config.js`.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import logging
import os
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from .cleaner import CleaningResult, run_cleaning
from .config import ConfigError, Settings, load_settings
from .ingest import ADDED, validate_export
from .loader import LoaderError
from .monthly import (
    REFERENCE_3,
    REFERENCE_6,
    REFERENCE_ALL,
    REFERENCE_BEFORE,
    available_months,
    default_month,
)
from .schema_mapper import SchemaError
from .server import TEXT_EXCERPT, build_control

logger = logging.getLogger(__name__)

# La periode personnalisee et le reglage du minimum de publications demandent
# un calcul a la demande : ils restent propres a l'interface locale.
PUBLISHED_PRESETS = (REFERENCE_BEFORE, REFERENCE_6, REFERENCE_3, REFERENCE_ALL)
EXPORT_BUCKET = "exports"
IMPORT_FOLDER = "imports"
PUBLICATION_BATCH = 500
CONTROL_BATCH = 4
LIST_PAGE = 1000

# Sort d'un export depose sur le site (colonne `statut` de la table `imports`).
WAITING = "en_attente"
RUNNING = "en_cours"
ACCEPTED = "ajoute"
DUPLICATE = "deja_charge"
REFUSED = "refuse"

ROLE_SECRET = "secrete"
ROLE_PUBLIC = "publique"
ROLE_UNKNOWN = "inconnue"


class PublishError(Exception):
    """Publication impossible : configuration incomplete ou refus de Supabase."""


def load_env(path: Path) -> dict[str, str]:
    """Variables du fichier `.env`, surchargees par celles de l'environnement."""
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("\"'")
    values.update({k: v for k, v in os.environ.items() if k.startswith("SUPABASE_")})
    return values


def key_role(key: str) -> str:
    """Role d'une cle Supabase, lu dans la cle elle-meme.

    Les cles recentes l'annoncent par leur prefixe ; les anciennes sont des
    jetons signes dont le contenu, lisible, porte le role.
    """
    if key.startswith("sb_secret_"):
        return ROLE_SECRET
    if key.startswith("sb_publishable_"):
        return ROLE_PUBLIC
    parts = key.split(".")
    if len(parts) != 3:
        return ROLE_UNKNOWN
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error):
        return ROLE_UNKNOWN
    if not isinstance(claims, dict):
        return ROLE_UNKNOWN
    return {"service_role": ROLE_SECRET, "anon": ROLE_PUBLIC}.get(claims.get("role"), ROLE_UNKNOWN)


def check_keys(url: str, secret_key: str, public_key: str) -> None:
    """Refuse de publier avec des cles absentes ou interverties."""
    if not url.startswith("https://"):
        raise PublishError("SUPABASE_URL est absente de .env (attendu : https://....supabase.co).")
    if not secret_key:
        raise PublishError(
            "SUPABASE_SERVICE_ROLE_KEY est absente de .env : sans la cle secrete, "
            "rien ne peut etre ecrit dans Supabase."
        )
    if key_role(secret_key) == ROLE_PUBLIC:
        raise PublishError(
            "SUPABASE_SERVICE_ROLE_KEY contient la cle publique : elle ne permet pas d'ecrire. "
            "Renseignez la cle secrete (\"secret\" ou \"service_role\")."
        )
    if public_key and key_role(public_key) == ROLE_SECRET:
        raise PublishError(
            "SUPABASE_ANON_KEY contient la cle SECRETE. Elle serait recopiee dans "
            "web/config.js et rendue publique : publication annulee."
        )


class SupabaseClient:
    """Les trois appels REST dont la publication a besoin, sans dependance."""

    def __init__(self, url: str, key: str, send=None) -> None:
        self.url = url.rstrip("/")
        self._key = key
        self._send = send or self._urlopen

    @staticmethod
    def _urlopen(request: urllib.request.Request) -> bytes:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()

    def _call(
        self, method: str, path: str, body: bytes | None = None, headers: dict | None = None
    ) -> bytes:
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=body,
            method=method,
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "User-Agent": "socialstats-publish",
                **(headers or {}),
            },
        )
        target = path.split("?")[0]
        try:
            return self._send(request)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise PublishError(f"Supabase a refuse {method} {target} (HTTP {exc.code}) : {detail}") from exc
        except urllib.error.URLError as exc:
            raise PublishError(f"Supabase est injoignable ({exc.reason}).") from exc

    def upsert(self, table: str, rows: list[dict], on_conflict: str, batch: int) -> None:
        """Insere ou met a jour `rows`, par lots."""
        for start in range(0, len(rows), batch):
            body = json.dumps(rows[start : start + batch], ensure_ascii=False, allow_nan=False)
            self._call(
                "POST",
                f"/rest/v1/{table}?on_conflict={on_conflict}",
                body.encode("utf-8"),
                {
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
            )

    def delete_older(self, table: str, stamp: str) -> None:
        """Retire les lignes qu'une publication precedente avait laissees."""
        self._call(
            "DELETE",
            f"/rest/v1/{table}?maj_le=lt.{quote(stamp, safe='')}",
            headers={"Prefer": "return=minimal"},
        )

    def select(self, table: str, query: str) -> list[dict]:
        body = self._call("GET", f"/rest/v1/{table}?{query}", headers={"Accept": "application/json"})
        return json.loads(body or b"[]")

    def update(self, table: str, query: str, values: dict) -> None:
        self._call(
            "PATCH",
            f"/rest/v1/{table}?{query}",
            json.dumps(values, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            {"Content-Type": "application/json", "Prefer": "return=minimal"},
        )

    def upload(self, bucket: str, name: str, content: bytes) -> None:
        self._call(
            "POST",
            f"/storage/v1/object/{bucket}/{quote(name)}",
            content,
            {"Content-Type": "text/csv", "x-upsert": "true"},
        )

    def list_objects(self, bucket: str, prefix: str = "") -> list[dict]:
        """Entrees d'un dossier du depot (fichiers et sous-dossiers), toutes pages lues."""
        entries: list[dict] = []
        while True:
            body = json.dumps(
                {"prefix": prefix, "limit": LIST_PAGE, "offset": len(entries),
                 "sortBy": {"column": "name", "order": "asc"}}
            )
            page = json.loads(
                self._call(
                    "POST",
                    f"/storage/v1/object/list/{bucket}",
                    body.encode("utf-8"),
                    {"Content-Type": "application/json"},
                )
                or b"[]"
            )
            entries += page
            if len(page) < LIST_PAGE:
                return entries

    def download(self, bucket: str, name: str) -> bytes:
        return self._call("GET", f"/storage/v1/object/{bucket}/{quote(name)}")

    def remove(self, bucket: str, names: list[str]) -> None:
        if names:
            self._call(
                "DELETE",
                f"/storage/v1/object/{bucket}",
                json.dumps({"prefixes": names}).encode("utf-8"),
                {"Content-Type": "application/json"},
            )


def stored_exports(client: SupabaseClient) -> list[str]:
    """Chemins de tous les exports du depot : sa racine et le dossier des imports."""
    paths: list[str] = []
    for prefix in ("", IMPORT_FOLDER):
        for entry in client.list_objects(EXPORT_BUCKET, prefix):
            # Un sous-dossier n'a pas d'identifiant ; seuls les CSV sont des exports.
            if entry.get("id") is None or not str(entry.get("name", "")).lower().endswith(".csv"):
                continue
            paths.append(f"{prefix}/{entry['name']}" if prefix else entry["name"])
    return sorted(paths)


def fetch_exports(client: SupabaseClient, paths: list[str], raw_dir: Path) -> dict[str, Path]:
    """Telecharge les exports dans `raw_dir` ; retourne chemin du depot -> fichier local."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    local: dict[str, Path] = {}
    for path in paths:
        target = raw_dir / Path(path).name
        if target.exists():
            target = raw_dir / f"{IMPORT_FOLDER}_{Path(path).name}"
        target.write_bytes(client.download(EXPORT_BUCKET, path))
        local[path] = target
    return local


@dataclass
class ImportDecision:
    """Sort d'un export depose sur le site."""

    id: str
    path: str
    status: str
    detail: str
    stats: dict[str, object] = field(default_factory=dict)

    def values(self, stamp: str) -> dict[str, object]:
        """Colonnes de la table `imports` a mettre a jour."""
        values: dict[str, object] = {"statut": self.status, "detail": self.detail[:500], "traite_le": stamp}
        if self.stats:
            values.update(
                publications=self.stats["publications"],
                plateforme=self.stats["platform"],
                comptes=self.stats["accounts"],
                debut=self.stats["start"],
                fin=self.stats["end"],
            )
        return values


def review_imports(pending: list[dict], local: dict[str, Path], settings: Settings) -> list[ImportDecision]:
    """Statue sur chaque export depose, dans l'ordre de depot.

    Un export retenu reste dans le dossier de calcul. Un export refuse ou deja
    charge en est retire (et `local` est mis a jour) : il ne pese pas dans les
    resultats, et sera supprime du depot.
    """
    waiting = {row["chemin"] for row in pending}
    known = {
        hashlib.sha256(path.read_bytes()).digest(): Path(stored).name
        for stored, path in local.items()
        if stored not in waiting
    }
    decisions: list[ImportDecision] = []
    for row in pending:
        stored = row["chemin"]
        path = local.get(stored)
        if path is None:
            decisions.append(ImportDecision(
                row["id"], stored, REFUSED, "Fichier introuvable dans le stockage : le depot n'a pas abouti."
            ))
            continue

        content = path.read_bytes()
        digest = hashlib.sha256(content).digest()
        if digest in known:
            outcome = ImportDecision(row["id"], stored, DUPLICATE, f"Contenu identique a {known[digest]}.")
        else:
            checked = validate_export(path.name, content, settings)
            if checked.status == ADDED:
                known[digest] = Path(stored).name
                decisions.append(ImportDecision(row["id"], stored, ACCEPTED, checked.detail, checked.stats))
                continue
            outcome = ImportDecision(row["id"], stored, REFUSED, checked.detail)

        path.unlink()
        del local[stored]
        decisions.append(outcome)
    return decisions


def report_decisions(client: SupabaseClient, decisions: list[ImportDecision], stamp: str) -> None:
    """Inscrit le sort de chaque export depose, et retire du depot ceux qui sont ecartes."""
    for decision in decisions:
        client.update("imports", f"id=eq.{quote(str(decision.id), safe='')}", decision.values(stamp))
    client.remove(EXPORT_BUCKET, [d.path for d in decisions if d.status in (DUPLICATE, REFUSED)])


def publication_rows(cleaning: CleaningResult, stamp: str) -> list[dict[str, object]]:
    """Publications retenues, au schema de la table `publications`."""
    data = cleaning.data
    if data.empty:
        return []

    def column(name: str) -> pd.Series:
        return data[name] if name in data.columns else pd.Series(pd.NA, index=data.index)

    def number(name: str) -> pd.Series:
        return pd.to_numeric(column(name), errors="coerce").astype("float64")

    links = column("permalink").astype("string")
    table = pd.DataFrame(
        {
            "post_id": data["post_id"].astype(str),
            "compte": data["account_name"],
            "client": column("client"),
            "secteur": column("sector"),
            "plateforme": data["platform"],
            "format": data["format"],
            "publie_le": data["published_at"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "mois": data["published_at"].dt.strftime("%Y-%m"),
            "vues": number("views_organic_resolved"),
            "couverture": number("engagement_base"),
            "interactions": number("interactions"),
            "engagement": number("engagement_rate").round(2),
            "lien": links.where(links.str.startswith(("http://", "https://"), na=False)),
            "texte": column("description").astype("string").str.slice(0, TEXT_EXCERPT),
            "fichier": column("source_file"),
        }
    )
    table["maj_le"] = stamp
    # Deux lignes de meme identifiant dans un lot feraient echouer tout le lot.
    table = table.dropna(subset=["publie_le"]).drop_duplicates("post_id")
    return json.loads(table.to_json(orient="records", force_ascii=False))


def control_rows(
    cleaning: CleaningResult, settings: Settings, min_posts: int, stamp: str, on_month=None
) -> list[dict[str, object]]:
    """Une reponse `/api/controle` par mois et par periode de reference publiee.

    `on_month` est appele avant chaque mois : le calcul dure plus d'une minute
    sur une annee d'exports, et une console muette ressemble a une panne.
    """
    rows: list[dict[str, object]] = []
    for month in available_months(cleaning.data):
        if on_month is not None:
            on_month(month)
        for preset in PUBLISHED_PRESETS:
            payload = build_control(
                cleaning, settings, month=str(month), preset=preset, min_posts=min_posts
            )
            # Le chemin du dossier local n'a rien a faire sur un site public.
            payload["donnees"] = {**payload["donnees"], "dossier": ""}
            rows.append({"mois": str(month), "ref": preset, "payload": payload, "maj_le": stamp})
    return rows


def state_row(cleaning: CleaningResult, min_posts: int, stamp: str) -> dict[str, object]:
    """Ce que l'interface doit savoir avant de demander un controle."""
    return {
        "cle": "publication",
        "valeur": {
            "mois": [str(month) for month in available_months(cleaning.data)],
            "mois_defaut": str(default_month(cleaning.data)),
            "refs": list(PUBLISHED_PRESETS),
            "min": min_posts,
            "publications": int(len(cleaning.data)),
            "publie_le": stamp,
        },
        "maj_le": stamp,
    }


def write_web_config(web_dir: Path, url: str, public_key: str) -> Path:
    """Ecrit `web/config.js`, qui branche l'interface en ligne sur Supabase."""
    if key_role(public_key) == ROLE_SECRET:
        raise PublishError("Refus d'ecrire une cle secrete dans web/config.js.")
    settings = json.dumps({"supabaseUrl": url, "supabaseKey": public_key}, indent=2)
    path = web_dir / "config.js"
    path.write_text(
        "// Branche l'interface en ligne sur Supabase. Cette cle est la cle PUBLIQUE :\n"
        "// elle ne permet que de lire les tables publiees.\n"
        f"window.CONTROLE_CONFIG = {settings};\n",
        encoding="utf-8",
    )
    return path


def publish(
    client: SupabaseClient,
    publications: list[dict],
    controls: list[dict],
    state: dict,
    stamp: str,
) -> None:
    """Envoie tout, puis retire ce qu'une publication precedente avait laisse.

    Les nouvelles lignes arrivent avant que les anciennes ne partent : pendant
    la publication, l'interface en ligne n'est jamais vide.
    """
    print(f"  publications : {len(publications)} ligne(s)")
    client.upsert("publications", publications, "post_id", PUBLICATION_BATCH)
    print(f"  controles    : {len(controls)} reponse(s) precalculee(s)")
    client.upsert("controles", controls, "mois,ref", CONTROL_BATCH)
    client.upsert("etat", [state], "cle", 1)
    client.delete_older("publications", stamp)
    client.delete_older("controles", stamp)


def compute(cleaning: CleaningResult, settings: Settings, min_posts: int, stamp: str):
    """Controles et publications a envoyer, a partir d'un nettoyage."""
    if cleaning.data.empty:
        raise PublishError("Aucune publication exploitable : rien a publier.")
    print("Calcul des controles (un par mois et par periode de reference)...")
    controls = control_rows(
        cleaning, settings, min_posts, stamp, lambda month: print(f"  {month}", flush=True)
    )
    return controls, publication_rows(cleaning, stamp)


def pending_imports(client: SupabaseClient) -> list[dict]:
    """Exports deposes sur le site et pas encore traites, du plus ancien au plus recent.

    Un import reste "en cours" seulement si une publication s'est interrompue :
    les executions ne se chevauchant pas, il est repris comme les autres.
    """
    try:
        return client.select(
            "imports", f"select=id,nom,chemin&statut=in.({WAITING},{RUNNING})&order=depose_le.asc"
        )
    except PublishError as exc:
        if "PGRST205" in str(exc):
            raise PublishError(
                "La table imports n'existe pas encore : executez supabase/schema.sql "
                "dans Supabase > SQL Editor."
            ) from exc
        raise


def run(client: SupabaseClient, settings: Settings, min_posts: int, only_if_pending: bool) -> bool:
    """Traite les exports deposes, recalcule et publie. Retourne False si rien n'a change."""
    pending = pending_imports(client)
    if only_if_pending and not pending:
        print("Aucun export en attente : resultats inchanges.")
        return False
    if pending:
        print(f"{len(pending)} export(s) depose(s) sur le site a traiter.")
        ids = ",".join(quote(str(row["id"]), safe="") for row in pending)
        client.update("imports", f"id=in.({ids})", {"statut": RUNNING, "detail": None})

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory() as tmp:
        # Le calcul part du depot, jamais d'un dossier local qui pourrait differer.
        settings.project_root = Path(tmp)
        print("Recuperation des exports archives dans Supabase...")
        local = fetch_exports(client, stored_exports(client), settings.raw_dir)
        decisions = review_imports(pending, local, settings)
        accepted = [d for d in decisions if d.status == ACCEPTED]
        report_decisions(client, [d for d in decisions if d.status != ACCEPTED], stamp)
        for decision in decisions:
            print(f"  {Path(decision.path).name} : {decision.status}")

        if only_if_pending and not accepted:
            print("Aucun nouvel export retenu : resultats inchanges.")
            return False

        try:
            print("Lecture et nettoyage des exports...")
            cleaning = run_cleaning(settings)
            controls, publications = compute(cleaning, settings, min_posts, stamp)
            print("Publication...")
            publish(client, publications, controls, state_row(cleaning, min_posts, stamp), stamp)
        except BaseException as exc:
            # L'export n'est pas en cause : il sera repris a la prochaine execution.
            for decision in accepted:
                try:
                    client.update(
                        "imports",
                        f"id=eq.{quote(str(decision.id), safe='')}",
                        {"statut": WAITING, "detail": f"Calcul interrompu, nouvel essai prevu : {exc}"[:500]},
                    )
                except PublishError:
                    pass
            raise
        report_decisions(client, accepted, stamp)
    return True


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        prog="socialstats.publish",
        description="Calcule les resultats et les publie dans Supabase pour l'interface en ligne.",
    )
    parser.add_argument("--config", type=Path, default=root / "config",
                        help="Dossier de configuration (defaut : ./config)")
    parser.add_argument("--min-posts", type=int, default=3,
                        help="Publications minimum par mois pour un niveau (defaut : 3)")
    parser.add_argument("--no-exports", action="store_true",
                        help="N'envoie pas les exports de data/raw dans Supabase")
    parser.add_argument("--only-if-pending", action="store_true",
                        help="Ne recalcule que si un export depose sur le site est retenu")
    parser.add_argument("--dry-run", action="store_true",
                        help="Calcule a partir de data/raw et resume, sans rien envoyer")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s", stream=sys.stderr)

    env = load_env(root / ".env")
    url = env.get("SUPABASE_URL", "").rstrip("/")
    secret_key = env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    public_key = env.get("SUPABASE_ANON_KEY", "")
    min_posts = max(1, min(args.min_posts, 30))

    try:
        # Les cles d'abord : une configuration incomplete s'annonce avant tout calcul.
        if not args.dry_run:
            check_keys(url, secret_key, public_key)
        settings = load_settings(args.config)
        if args.dry_run:
            print("Lecture et nettoyage des exports de data/raw...")
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            controls, publications = compute(run_cleaning(settings), settings, min_posts, stamp)
            size = sum(len(json.dumps(row, ensure_ascii=False)) for row in controls) / 1e6
            print(
                f"Essai a blanc : {len(publications)} publication(s), {len(controls)} controle(s) "
                f"({size:.1f} Mo). Rien n'a ete envoye."
            )
            return 0

        client = SupabaseClient(url, secret_key)
        if not args.no_exports:
            for path in sorted(settings.raw_dir.glob("*.csv")):
                print(f"Archive : {path.name}")
                client.upload(EXPORT_BUCKET, path.name, path.read_bytes())

        changed = run(client, settings, min_posts, args.only_if_pending)
        if public_key:
            path = write_web_config(root / "web", url, public_key)
            print(f"Interface en ligne configuree : {path}")
    except (PublishError, ConfigError, LoaderError, SchemaError) as exc:
        print(f"Publication impossible : {exc}", file=sys.stderr)
        return 1

    print("Publication terminee." if changed else "Rien a publier.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
