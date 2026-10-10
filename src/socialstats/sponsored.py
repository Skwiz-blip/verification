"""Detection des publications sponsorisees et resolution des vues organiques.

Justification metier : la portee d'une publication boostee depend du budget
publicitaire. L'inclure dans l'evaluation d'un Community Manager reviendrait a
mesurer le budget marketing plutot que la qualite du contenu.

Justification technique : sur les trois exports reels analyses, la colonne
"Statut du contenu finance" est vide a 100 %, alors que des publications ont bien
des vues boostees non nulles. Une detection reposant sur cette seule colonne
manquerait donc toutes les publications sponsorisees. La detection combine
plusieurs signaux et trace celui qui a declenche l'exclusion, pour rester
auditable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

# Signal numerique -> libelle lisible dans le rapport d'exclusion.
NUMERIC_SIGNALS = {
    "views_boosted": "vues boostees > 0",
    "reach_boosted": "couverture boostee > 0",
    "ad_impressions": "impressions publicitaires > 0",
    "ad_cpm": "CPM publicitaire > 0",
    "estimated_earnings": "revenus estimes > 0",
}

# Valeurs de "Statut du contenu finance" qui signifient explicitement "non sponsorise".
NON_SPONSORED_TOKENS = {"", "no", "non", "0", "none", "false", "nan", "non finance"}


@dataclass
class SponsorshipReport:
    """Bilan de la detection, destine au rapport d'exclusion."""

    n_total: int
    n_sponsored: int
    by_signal: dict[str, int]
    n_sponsored_without_split: int

    @property
    def n_organic(self) -> int:
        return self.n_total - self.n_sponsored

    @property
    def pct_sponsored(self) -> float:
        return 100.0 * self.n_sponsored / self.n_total if self.n_total else 0.0

    def summary_rows(self) -> list[dict[str, object]]:
        rows = [
            {"indicateur": "Publications analysees", "valeur": self.n_total},
            {"indicateur": "Publications organiques retenues", "valeur": self.n_organic},
            {"indicateur": "Publications sponsorisees exclues", "valeur": self.n_sponsored},
            {"indicateur": "Part sponsorisee (%)", "valeur": round(self.pct_sponsored, 2)},
            {
                "indicateur": "Sponsorisees sans detail organique/paye",
                "valeur": self.n_sponsored_without_split,
            },
        ]
        rows.extend(
            {"indicateur": f"Detectees par : {label}", "valeur": count}
            for label, count in sorted(self.by_signal.items(), key=lambda kv: -kv[1])
        )
        return rows


def _positive(frame: pd.DataFrame, column: str) -> pd.Series:
    """Masque des valeurs strictement positives, tolerant aux colonnes absentes."""
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0) > 0


def _status_flag(frame: pd.DataFrame) -> pd.Series:
    if "sponsored_status" not in frame.columns:
        return pd.Series(False, index=frame.index)
    # Une case vide doit devenir "" avant la conversion : pandas 2 la changeait
    # en "nan", pandas 3 la laisse vide, et toutes les publications passeraient
    # alors pour sponsorisees.
    status = frame["sponsored_status"].fillna("").astype(str).str.strip().str.lower()
    return ~status.isin(NON_SPONSORED_TOKENS)


def detect_sponsored(frame: pd.DataFrame) -> tuple[pd.DataFrame, SponsorshipReport]:
    """Ajoute les colonnes `is_sponsored` et `sponsored_reason`.

    Une publication est sponsorisee des qu'un seul signal est positif.
    """
    signals: dict[str, pd.Series] = {
        label: _positive(frame, column) for column, label in NUMERIC_SIGNALS.items()
    }
    signals["statut de contenu finance renseigne"] = _status_flag(frame)

    is_sponsored = pd.Series(False, index=frame.index)
    reasons = pd.Series("", index=frame.index, dtype=object)
    for label, mask in signals.items():
        is_sponsored |= mask
        reasons = reasons.mask(mask & (reasons == ""), label)
        reasons = reasons.mask(mask & (reasons != "") & ~reasons.str.contains(label), reasons + " + " + label)

    out = frame.copy()
    out["is_sponsored"] = is_sponsored
    out["sponsored_reason"] = reasons

    has_split = "views_organic" in out.columns and out["views_organic"].notna()
    if isinstance(has_split, bool):
        has_split = pd.Series(has_split, index=out.index)
    n_without_split = int((is_sponsored & ~has_split).sum())

    report = SponsorshipReport(
        n_total=len(out),
        n_sponsored=int(is_sponsored.sum()),
        by_signal={label: int(mask.sum()) for label, mask in signals.items() if mask.any()},
        n_sponsored_without_split=n_without_split,
    )
    logger.info(
        "Sponsorise : %d/%d publication(s) detectee(s) (%.1f %%), dont %d sans detail organique.",
        report.n_sponsored,
        report.n_total,
        report.pct_sponsored,
        n_without_split,
    )
    return out, report


def resolve_organic_views(frame: pd.DataFrame) -> pd.DataFrame:
    """Calcule `views_organic_resolved`, la metrique de vues retenue pour l'analyse.

    Cascade volontairement prudente :
      1. colonne de vues organiques dediee si le fichier la fournit ;
      2. sinon, si la publication n'est pas sponsorisee, les vues totales
         (elles sont alors integralement organiques par definition) ;
      3. sinon NaN : une publication sponsorisee sans detail ne peut pas etre
         requalifiee en organique sans inventer de la donnee.

    Le cas 2 est indispensable : l'export a 32 colonnes analyse ne contient
    aucune ventilation organique/paye, et l'ignorer eliminerait 224 publications.
    """
    out = frame.copy()
    dedicated = (
        pd.to_numeric(out["views_organic"], errors="coerce")
        if "views_organic" in out.columns
        else pd.Series(pd.NA, index=out.index, dtype="Float64")
    )
    total = (
        pd.to_numeric(out["views_total"], errors="coerce")
        if "views_total" in out.columns
        else pd.Series(pd.NA, index=out.index, dtype="Float64")
    )

    resolved = dedicated.astype("Float64")
    fallback = resolved.isna() & ~out["is_sponsored"]
    resolved = resolved.mask(fallback, total.astype("Float64"))

    source = pd.Series("colonne organique dediee", index=out.index, dtype=object)
    source = source.mask(dedicated.isna() & fallback, "vues totales (publication non sponsorisee)")
    source = source.mask(resolved.isna(), "indisponible (sponsorisee sans detail)")

    out["views_organic_resolved"] = resolved
    out["views_source"] = source

    logger.info(
        "Vues organiques resolues : %d dediees, %d via vues totales, %d indisponibles.",
        int((source == "colonne organique dediee").sum()),
        int((source == "vues totales (publication non sponsorisee)").sum()),
        int(resolved.isna().sum()),
    )
    return out
