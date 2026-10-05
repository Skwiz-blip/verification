"""Validation des lignes et mise en quarantaine des donnees inexploitables.

Les lignes rejetees ne sont jamais supprimees silencieusement : elles sont
retournees avec le motif du rejet, pour figurer dans le rapport qualite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    n_input: int
    n_valid: int
    reasons: dict[str, int]

    @property
    def n_rejected(self) -> int:
        return self.n_input - self.n_valid

    def summary_rows(self) -> list[dict[str, object]]:
        rows = [
            {"indicateur": "Lignes soumises a validation", "valeur": self.n_input},
            {"indicateur": "Lignes valides", "valeur": self.n_valid},
            {"indicateur": "Lignes mises en quarantaine", "valeur": self.n_rejected},
        ]
        rows.extend(
            {"indicateur": f"Rejet : {reason}", "valeur": count}
            for reason, count in sorted(self.reasons.items(), key=lambda kv: -kv[1])
        )
        return rows


def validate(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, ValidationReport]:
    """Separe les lignes exploitables des lignes en quarantaine.

    Retourne (lignes valides, lignes rejetees avec motif, rapport).
    """
    checks: dict[str, pd.Series] = {
        "nom de compte manquant": frame["account_name"].isna()
        | (frame["account_name"].astype(str).str.strip().isin(["", "nan"])),
        "date de publication illisible": frame["published_at"].isna(),
        "aucune metrique de vues": frame["views_total"].isna()
        & frame.get(
            "views_organic", pd.Series(pd.NA, index=frame.index)
        ).isna(),
    }
    if "views_total" in frame.columns:
        checks["vues negatives (donnee corrompue)"] = (
            pd.to_numeric(frame["views_total"], errors="coerce") < 0
        )

    rejected_mask = pd.Series(False, index=frame.index)
    reason = pd.Series("", index=frame.index, dtype=object)
    counts: dict[str, int] = {}

    for label, mask in checks.items():
        mask = mask.fillna(False)
        counts[label] = int(mask.sum())
        reason = reason.mask(mask & (reason == ""), label)
        rejected_mask |= mask

    valid = frame[~rejected_mask].copy()
    rejected = frame[rejected_mask].copy()
    rejected["motif_rejet"] = reason[rejected_mask]

    report = ValidationReport(
        n_input=len(frame),
        n_valid=len(valid),
        reasons={k: v for k, v in counts.items() if v > 0},
    )
    if report.n_rejected:
        logger.warning(
            "Validation : %d ligne(s) mise(s) en quarantaine sur %d.",
            report.n_rejected,
            report.n_input,
        )
    else:
        logger.info("Validation : les %d lignes sont exploitables.", report.n_input)
    return valid, rejected, report
