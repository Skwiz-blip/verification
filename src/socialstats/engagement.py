"""Calcul du taux d'engagement.

Formule retenue :
    engagement_rate = (reactions + commentaires + partages) / base * 100

Le denominateur suit une cascade documentee, car tous les exports ne fournissent
pas la meme granularite. La base utilisee est tracee ligne par ligne : un taux
calcule sur la couverture (personnes touchees) et un taux calcule sur les vues
n'ont pas exactement la meme semantique, et melanger les deux sans le signaler
donnerait une comparaison trompeuse.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

BASE_REACH_ORGANIC = "couverture organique"
BASE_REACH_TOTAL = "couverture totale (publication non sponsorisee)"
BASE_VIEWS = "vues organiques (couverture indisponible)"
BASE_NONE = "indisponible"


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def compute_interactions(frame: pd.DataFrame) -> pd.Series:
    """Somme les interactions d'une publication.

    Facebook : reactions + commentaires + partages (la colonne agregee de Meta
    est preferee lorsqu'elle existe).

    Instagram : mentions J'aime + commentaires + partages + enregistrements.
    Les enregistrements sont un signal d'engagement fort sur Instagram et sont
    inclus quand la colonne existe ; elle est absente des exports Facebook, ce
    qui n'affecte pas leur calcul.

    Les valeurs manquantes comptent pour 0 uniquement si au moins une composante
    est renseignee, afin de ne pas transformer une ligne vide en "0 interaction".
    """
    components = pd.concat(
        [
            _numeric(frame, "reactions"),
            _numeric(frame, "comments"),
            _numeric(frame, "shares"),
            _numeric(frame, "saves"),
        ],
        axis=1,
    )

    has_any = components.notna().any(axis=1)
    summed = components.sum(axis=1, min_count=1)
    summed = summed.where(has_any, np.nan)

    aggregated = _numeric(frame, "interactions_total")
    return aggregated.where(aggregated.notna(), summed)


def compute_engagement(frame: pd.DataFrame) -> pd.DataFrame:
    """Ajoute `interactions`, `engagement_base`, `engagement_base_kind`, `engagement_rate`."""
    out = frame.copy()
    out["interactions"] = compute_interactions(out)

    reach_organic = _numeric(out, "reach_organic")
    reach_total = _numeric(out, "reach_total")
    views_organic = pd.to_numeric(out.get("views_organic_resolved"), errors="coerce")
    is_sponsored = out.get("is_sponsored", pd.Series(False, index=out.index)).fillna(False)

    base = reach_organic.copy()
    kind = pd.Series(BASE_REACH_ORGANIC, index=out.index, dtype=object)

    use_total = base.isna() & ~is_sponsored & reach_total.notna()
    base = base.mask(use_total, reach_total)
    kind = kind.mask(use_total, BASE_REACH_TOTAL)

    use_views = base.isna() & views_organic.notna()
    base = base.mask(use_views, views_organic)
    kind = kind.mask(use_views, BASE_VIEWS)

    kind = kind.mask(base.isna(), BASE_NONE)

    # Une base nulle ou negative ne permet aucun taux : NaN plutot qu'infini.
    safe_base = base.where(base > 0, np.nan)
    rate = (out["interactions"] / safe_base) * 100.0
    rate = rate.replace([np.inf, -np.inf], np.nan)

    out["engagement_base"] = base
    out["engagement_base_kind"] = kind
    out["engagement_rate"] = rate

    n_missing = int(rate.isna().sum())
    n_zero_base = int((base.notna() & (base <= 0)).sum())
    logger.info(
        "Engagement : %d taux calcules, %d non calculables (dont %d base nulle).",
        int(rate.notna().sum()),
        n_missing,
        n_zero_base,
    )
    if (kind == BASE_VIEWS).any():
        logger.warning(
            "%d publication(s) utilisent les vues comme base d'engagement "
            "(couverture absente) : taux non strictement comparable aux autres.",
            int((kind == BASE_VIEWS).sum()),
        )
    return out
