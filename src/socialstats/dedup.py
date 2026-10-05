"""Deduplication des publications presentes dans plusieurs exports.

Necessite verifiee sur les donnees reelles : 44 publications apparaissent a la
fois dans l'export "Jul-24 -> Aug-20" et dans l'export "May-23 -> Aug-20".
Sans fusion, ces publications pesent deux fois dans les objectifs.

Regle de fusion : les compteurs Meta (vues, couverture, reactions...) sont
cumulatifs et ne peuvent que croitre entre deux exports d'une meme publication.
On conserve donc le MAXIMUM de chaque metrique numerique, c'est-a-dire la
mesure la plus recente. Pour les champs descriptifs, on garde la ligne la plus
complete (celle qui a le moins de valeurs manquantes).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

NUMERIC_MERGE_EXCLUDED = {"duration_s", "ad_cpm"}


@dataclass
class DedupReport:
    n_before: int
    n_after: int
    n_duplicate_ids: int
    n_rows_merged: int
    overlapping_files: list[tuple[str, str, int]]

    def summary_rows(self) -> list[dict[str, object]]:
        rows = [
            {"indicateur": "Lignes avant deduplication", "valeur": self.n_before},
            {"indicateur": "Publications uniques apres fusion", "valeur": self.n_after},
            {"indicateur": "Publications vues dans plusieurs fichiers", "valeur": self.n_duplicate_ids},
            {"indicateur": "Lignes redondantes fusionnees", "valeur": self.n_rows_merged},
        ]
        rows.extend(
            {"indicateur": f"Chevauchement {a} / {b}", "valeur": n}
            for a, b, n in self.overlapping_files
        )
        return rows


def _overlaps(frame: pd.DataFrame) -> list[tuple[str, str, int]]:
    """Compte les publications partagees entre chaque paire de fichiers."""
    if "source_file" not in frame.columns:
        return []
    by_file = {name: set(group["post_id"]) for name, group in frame.groupby("source_file")}
    names = sorted(by_file)
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            shared = len(by_file[a] & by_file[b])
            if shared:
                pairs.append((a, b, shared))
    return pairs


def deduplicate(frame: pd.DataFrame) -> tuple[pd.DataFrame, DedupReport]:
    """Fusionne les publications dupliquees par identifiant."""
    n_before = len(frame)
    overlapping = _overlaps(frame)

    valid_id = frame["post_id"].notna() & (frame["post_id"].astype(str).str.strip() != "") & (
        frame["post_id"].astype(str).str.lower() != "nan"
    )
    if not valid_id.all():
        logger.warning(
            "%d publication(s) sans identifiant : conservees telles quelles, non dedupliquees.",
            int((~valid_id).sum()),
        )

    identified = frame[valid_id]
    unidentified = frame[~valid_id]

    duplicated_ids = identified["post_id"][identified["post_id"].duplicated()].unique()
    n_duplicate_ids = len(duplicated_ids)

    if n_duplicate_ids == 0:
        merged = identified
    else:
        numeric_cols = [
            c
            for c in identified.columns
            if c not in NUMERIC_MERGE_EXCLUDED
            and pd.api.types.is_numeric_dtype(identified[c])
            and not pd.api.types.is_bool_dtype(identified[c])
        ]
        # La ligne la plus complete sert de base pour les champs descriptifs.
        completeness = identified.notna().sum(axis=1)
        ordered = identified.assign(_completeness=completeness).sort_values(
            ["post_id", "_completeness"], ascending=[True, False]
        )
        merged = ordered.groupby("post_id", as_index=False, sort=False).first()
        merged = merged.drop(columns="_completeness")

        if numeric_cols:
            maxima = identified.groupby("post_id", as_index=False)[numeric_cols].max()
            merged = merged.drop(columns=numeric_cols).merge(maxima, on="post_id", how="left")

        # Une publication sponsorisee dans un export l'est definitivement.
        if "is_sponsored" in identified.columns:
            flags = identified.groupby("post_id", as_index=False)["is_sponsored"].max()
            merged = merged.drop(columns="is_sponsored").merge(flags, on="post_id", how="left")

    result = pd.concat([merged, unidentified], ignore_index=True)
    report = DedupReport(
        n_before=n_before,
        n_after=len(result),
        n_duplicate_ids=n_duplicate_ids,
        n_rows_merged=n_before - len(result),
        overlapping_files=overlapping,
    )
    logger.info(
        "Deduplication : %d -> %d lignes (%d publication(s) presente(s) dans plusieurs fichiers).",
        n_before,
        len(result),
        n_duplicate_ids,
    )
    return result, report
