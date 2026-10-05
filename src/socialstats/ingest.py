"""Ajout d'un export a `data/raw` depuis l'interface.

Un fichier n'est range dans les donnees brutes qu'apres avoir ete lu et mappe
avec succes : un export de format inconnu depose tel quel serait ignore a
chaque analyse, et l'utilisateur croirait ses donnees prises en compte.

Rien n'est jamais ecrase : un export portant le nom d'un fichier existant mais
un contenu different est range sous un nom suffixe. La deduplication par
identifiant de publication se charge ensuite des chevauchements.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import Settings
from .loader import LoaderError, read_csv_file
from .schema_mapper import SchemaError, map_frame

logger = logging.getLogger(__name__)

ADDED = "Ajoute"
DUPLICATE = "Deja charge"
REJECTED = "Rejete"


@dataclass
class ImportOutcome:
    """Sort reserve a un fichier depose, avec de quoi l'expliquer a l'utilisateur."""

    name: str
    status: str
    detail: str
    path: Path | None = None
    # Faits chiffres d'un export accepte (volume, plateforme, comptes, periode).
    stats: dict[str, object] = field(default_factory=dict)


def _free_path(directory: Path, name: str) -> Path:
    """Premier chemin libre pour `name`, suffixe numerique si necessaire."""
    target = directory / name
    index = 2
    while target.exists():
        target = directory / f"{Path(name).stem}_{index}{Path(name).suffix}"
        index += 1
    return target


def import_export(name: str, content: bytes, settings: Settings) -> ImportOutcome:
    """Valide un export puis le range dans `data/raw`."""
    # Le nom vient du navigateur : seul son dernier segment est conserve.
    safe_name = Path(name).name
    if not safe_name.lower().endswith(".csv"):
        return ImportOutcome(safe_name, REJECTED, "Seuls les exports CSV sont acceptes.")

    raw_dir = settings.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256(content).digest()
    for existing in raw_dir.glob("*.csv"):
        if (
            existing.stat().st_size == len(content)
            and hashlib.sha256(existing.read_bytes()).digest() == digest
        ):
            return ImportOutcome(
                safe_name, DUPLICATE, f"Contenu identique a {existing.name}.", existing
            )

    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / safe_name
        candidate.write_bytes(content)
        try:
            raw = read_csv_file(candidate)
            mapped, report = map_frame(
                raw.frame,
                safe_name,
                settings.column_mappings,
                settings.format_aliases,
                settings.detect_reel_from_permalink,
            )
        except (LoaderError, SchemaError, pd.errors.EmptyDataError) as exc:
            return ImportOutcome(safe_name, REJECTED, str(exc))

    dates = mapped["published_at"].dropna()
    if dates.empty:
        return ImportOutcome(
            safe_name, REJECTED, "Aucune publication avec une date lisible dans ce fichier."
        )

    target = _free_path(raw_dir, safe_name)
    target.write_bytes(content)
    logger.info("Export ajoute : %s (%d ligne(s), %s)", target.name, len(mapped), report.platform)
    stats = {
        "publications": len(mapped),
        "platform": report.platform,
        "accounts": int(mapped["account_name"].nunique()),
        "start": f"{dates.min():%Y-%m-%d}",
        "end": f"{dates.max():%Y-%m-%d}",
    }
    detail = (
        f"{stats['publications']} publication(s) {report.platform}, "
        f"{stats['accounts']} compte(s), du {stats['start']} au {stats['end']}."
    )
    return ImportOutcome(target.name, ADDED, detail, target, stats)
