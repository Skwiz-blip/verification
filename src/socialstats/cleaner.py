"""Orchestration du pipeline de nettoyage.

Enchainement : lecture -> mapping -> detection du sponsorise -> deduplication
-> validation -> filtrage de periode -> engagement.

L'ordre n'est pas arbitraire :
  - la detection du sponsorise precede la deduplication, pour qu'une publication
    identifiee comme boostee dans un seul export le reste apres fusion ;
  - le filtrage de periode intervient apres la deduplication, sinon une meme
    publication pourrait etre conservee via un fichier et perdue via un autre.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .config import Settings
from .dedup import DedupReport, deduplicate
from .engagement import compute_engagement
from .loader import load_all
from .schema_mapper import MappingReport, SchemaError, display_account_name, map_frame
from .sponsored import SponsorshipReport, detect_sponsored, resolve_organic_views
from .validator import ValidationReport, validate

logger = logging.getLogger(__name__)

MONTHS_BY_PERIOD = {"last_3_months": 3, "last_6_months": 6, "last_12_months": 12}


@dataclass
class CleaningResult:
    """Jeu de donnees propre et l'ensemble des rapports de tracabilite."""

    data: pd.DataFrame
    sponsored_data: pd.DataFrame
    rejected_data: pd.DataFrame
    mapping_reports: list[MappingReport] = field(default_factory=list)
    sponsorship: SponsorshipReport | None = None
    dedup: DedupReport | None = None
    validation: ValidationReport | None = None
    period_start: pd.Timestamp | None = None
    period_end: pd.Timestamp | None = None
    n_period_excluded: int = 0
    skipped_files: list[str] = field(default_factory=list)
    dropped_accounts: list[str] = field(default_factory=list)

    @property
    def n_accounts(self) -> int:
        return self.data["account_name"].nunique() if not self.data.empty else 0


def resolve_period(
    frame: pd.DataFrame, settings: Settings
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Traduit la configuration de periode en bornes concretes."""
    if settings.analysis_period == "all":
        return None, None

    if settings.analysis_period == "custom":
        return settings.custom_start, settings.custom_end

    months = MONTHS_BY_PERIOD[settings.analysis_period]
    if settings.period_reference == "today":
        reference = pd.Timestamp.now().normalize()
    else:
        reference = frame["published_at"].max()
        if pd.isna(reference):
            logger.warning("Aucune date exploitable : filtrage de periode ignore.")
            return None, None
    return reference - pd.DateOffset(months=months), reference


def filter_period(
    frame: pd.DataFrame, settings: Settings
) -> tuple[pd.DataFrame, pd.Timestamp | None, pd.Timestamp | None, int]:
    """Restreint le jeu de donnees a la fenetre d'analyse configuree."""
    start, end = resolve_period(frame, settings)
    if start is None and end is None:
        return frame, frame["published_at"].min(), frame["published_at"].max(), 0

    mask = pd.Series(True, index=frame.index)
    if start is not None:
        mask &= frame["published_at"] >= start
    if end is not None:
        mask &= frame["published_at"] <= end

    filtered = frame[mask]
    excluded = len(frame) - len(filtered)
    logger.info(
        "Periode %s : %s -> %s, %d publication(s) hors periode ecartee(s).",
        settings.analysis_period,
        start.date() if start is not None else "debut",
        end.date() if end is not None else "fin",
        excluded,
    )
    return filtered, start, end, excluded


def enrich_with_clients(frame: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Ajoute le secteur et, si declare, le Community Manager de chaque compte."""
    out = frame.copy()
    out["sector"] = out["account_name"].map(settings.sector_of)
    out["client"] = out["account_name"].map(settings.client_of)
    out["cm_name"] = out["account_name"].map(lambda a: settings.cm_of(a) or "")
    # Le nom brut reste la cle technique ; ce libelle sert aux rapports et
    # graphiques, ou les noms Instagram stylises seraient illisibles.
    out["account_name"] = out["account_name"].map(display_account_name)

    unknown = sorted(
        out.loc[out["sector"].eq("Non categorise"), "account_name"].unique().tolist()
    )
    if unknown:
        logger.warning(
            "%d compte(s) sans secteur declare dans config/clients.csv : %s",
            len(unknown),
            ", ".join(unknown),
        )
    return out


def drop_low_volume_accounts(
    frame: pd.DataFrame, settings: Settings
) -> tuple[pd.DataFrame, list[str]]:
    """Ecarte les comptes trop peu alimentes pour etre analyses.

    Les exports Meta contiennent des comptes qui n'ont qu'une ou deux
    publications sur toute la periode : publications isolees, tests, ou comptes
    tiers apparus dans l'export. Les garder n'apporte aucun objectif — ils
    passent de toute facon sous `min_observations` — mais fausse les comptages
    presentes a la direction et gonfle les graphiques.

    Le seuil est configurable plutot que la liste des comptes : un compte
    aujourd'hui inactif peut devenir actif au prochain export, et l'inverse.
    """
    threshold = settings.min_publications_per_account
    if threshold <= 0 or frame.empty:
        return frame, []

    volumes = frame.groupby("account_name").size()
    dropped = sorted(
        display_account_name(name) for name in volumes[volumes < threshold].index
    )
    if not dropped:
        return frame, []

    kept = frame[frame["account_name"].isin(volumes[volumes >= threshold].index)].copy()
    logger.info(
        "%d compte(s) ecarte(s) : moins de %d publication(s) sur la periode (%s).",
        len(dropped),
        threshold,
        ", ".join(dropped[:8]) + (" ..." if len(dropped) > 8 else ""),
    )
    return kept, dropped


def run_cleaning(settings: Settings) -> CleaningResult:
    """Execute le pipeline complet de nettoyage."""
    frames: list[pd.DataFrame] = []
    reports: list[MappingReport] = []
    skipped: list[str] = []

    for raw in load_all(settings.raw_dir):
        try:
            mapped, report = map_frame(
                raw.frame,
                raw.name,
                settings.column_mappings,
                settings.format_aliases,
                settings.detect_reel_from_permalink,
            )
        except SchemaError as exc:
            logger.error("Fichier ignore (%s) : %s", raw.name, exc)
            skipped.append(raw.name)
            continue
        frames.append(mapped)
        reports.append(report)

    if not frames:
        raise SchemaError(
            "Aucun fichier exploitable : tous les exports ont un format non reconnu."
        )

    combined = pd.concat(frames, ignore_index=True, sort=False)
    logger.info("Concatenation : %d ligne(s) issues de %d fichier(s).", len(combined), len(frames))

    combined, sponsorship = detect_sponsored(combined)
    combined, dedup_report = deduplicate(combined)
    combined = resolve_organic_views(combined)
    valid, rejected, validation = validate(combined)
    valid, start, end, n_period_excluded = filter_period(valid, settings)
    valid, dropped_accounts = drop_low_volume_accounts(valid, settings)
    valid = enrich_with_clients(valid, settings)
    valid = compute_engagement(valid)

    sponsored_data = valid[valid["is_sponsored"]].copy()
    organic = valid[~valid["is_sponsored"]].copy() if settings.exclude_sponsored else valid.copy()

    logger.info(
        "Jeu de donnees final : %d publication(s) organique(s), %d compte(s), %d format(s).",
        len(organic),
        organic["account_name"].nunique(),
        organic["format"].nunique(),
    )

    return CleaningResult(
        data=organic,
        dropped_accounts=dropped_accounts,
        sponsored_data=sponsored_data,
        rejected_data=rejected,
        mapping_reports=reports,
        sponsorship=sponsorship,
        dedup=dedup_report,
        validation=validation,
        period_start=start,
        period_end=end,
        n_period_excluded=n_period_excluded,
        skipped_files=skipped,
    )


def save_cleaned(result: CleaningResult, settings: Settings) -> Path:
    """Ecrit le dataset nettoye sur disque."""
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    path = settings.processed_dir / "cleaned_social_data.csv"
    result.data.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("Dataset nettoye ecrit : %s (%d lignes)", path, len(result.data))
    return path
