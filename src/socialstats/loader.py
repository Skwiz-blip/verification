"""Decouverte et lecture robuste des exports CSV Meta Business Suite.

Les exports observes varient fortement (32, 35 ou 227 colonnes) : le loader ne
fait donc aucune hypothese sur le nombre ou l'ordre des colonnes. Il se contente
de lire chaque fichier integralement, en texte, et de tracer sa provenance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# utf-8-sig gere le BOM present dans les exports Meta ; les repli suivants
# couvrent les fichiers re-enregistres depuis Excel sous Windows.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
SEPARATORS = (",", ";", "\t")


class LoaderError(Exception):
    """Aucun fichier lisible, ou fichier illisible."""


@dataclass
class RawFile:
    """Un CSV brut charge, avec sa provenance."""

    path: Path
    frame: pd.DataFrame
    encoding: str
    separator: str

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def n_columns(self) -> int:
        return self.frame.shape[1]

    @property
    def n_rows(self) -> int:
        return self.frame.shape[0]


def discover_files(raw_dir: Path | str, pattern: str = "*.csv") -> list[Path]:
    """Liste les CSV a analyser. Le nombre de fichiers n'est jamais code en dur."""
    raw_dir = Path(raw_dir)
    if not raw_dir.exists():
        raise LoaderError(f"Dossier de donnees introuvable : {raw_dir}")
    files = sorted(p for p in raw_dir.glob(pattern) if p.is_file())
    if not files:
        raise LoaderError(f"Aucun fichier {pattern} dans {raw_dir}")
    logger.info("%d fichier(s) detecte(s) dans %s", len(files), raw_dir)
    return files


def _sniff_separator(path: Path, encoding: str) -> str:
    """Choisit le separateur qui decoupe le plus de colonnes sur l'en-tete."""
    with path.open(encoding=encoding, errors="replace") as fh:
        header = fh.readline()
    counts = {sep: header.count(sep) for sep in SEPARATORS}
    best = max(counts, key=lambda s: counts[s])
    return best if counts[best] > 0 else ","


def read_csv_file(path: Path) -> RawFile:
    """Lit un CSV en essayant plusieurs encodages. Tout est lu en texte.

    La conversion numerique est deleguee au schema mapper : lire en texte evite
    que pandas devine des types differents d'un fichier a l'autre pour une meme
    colonne, ce qui casserait la concatenation.
    """
    last_error: Exception | None = None
    for encoding in ENCODINGS:
        try:
            separator = _sniff_separator(path, encoding)
            frame = pd.read_csv(
                path,
                encoding=encoding,
                sep=separator,
                dtype=str,
                keep_default_na=True,
                low_memory=False,
            )
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last_error = exc
            continue

        if frame.empty:
            logger.warning("Fichier vide (aucune ligne de donnees) : %s", path.name)
        frame.columns = [str(c).strip() for c in frame.columns]
        logger.info(
            "Lu %s : %d lignes x %d colonnes (encodage=%s, separateur=%r)",
            path.name,
            frame.shape[0],
            frame.shape[1],
            encoding,
            separator,
        )
        return RawFile(path=path, frame=frame, encoding=encoding, separator=separator)

    raise LoaderError(f"Impossible de lire {path.name} : {last_error}")


def load_all(raw_dir: Path | str, pattern: str = "*.csv") -> list[RawFile]:
    """Charge tous les CSV du dossier. Un fichier illisible n'arrete pas le lot."""
    raw_files: list[RawFile] = []
    failures: list[str] = []

    for path in discover_files(raw_dir, pattern):
        try:
            raw_files.append(read_csv_file(path))
        except LoaderError as exc:
            logger.error("%s", exc)
            failures.append(path.name)

    if not raw_files:
        raise LoaderError(f"Aucun fichier exploitable dans {raw_dir} (echecs : {failures})")
    if failures:
        logger.warning("%d fichier(s) ignore(s) : %s", len(failures), ", ".join(failures))
    return raw_files
