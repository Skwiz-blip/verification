"""Constructeurs de jeux de donnees synthetiques pour les tests."""

from __future__ import annotations

import pandas as pd


def make_posts(
    account: str,
    fmt: str,
    views: list[float],
    sector: str = "Optique",
    start: str = "2026-01-01",
    reach_ratio: float = 0.8,
    engagement_rate: float = 2.5,
) -> pd.DataFrame:
    """Construit des publications synthetiques au schema canonique."""
    n = len(views)
    return pd.DataFrame(
        {
            "post_id": [f"{account}-{fmt}-{i}" for i in range(n)],
            "account_name": account,
            "sector": sector,
            "cm_name": "",
            "platform": "facebook",
            "format": fmt,
            "published_at": pd.date_range(start, periods=n, freq="D"),
            "views_organic_resolved": views,
            "views_total": views,
            "reach_total": [v * reach_ratio for v in views],
            "reach_organic": [v * reach_ratio for v in views],
            "reactions": [max(1.0, v * 0.02) for v in views],
            "comments": [0.0] * n,
            "shares": [0.0] * n,
            "interactions_total": [max(1.0, v * 0.02) for v in views],
            "engagement_rate": [engagement_rate] * n,
            "is_sponsored": False,
            "source_file": "test.csv",
        }
    )


def make_export_csv(n: int = 40, account: str = "Compte A", start: str = "2026-06-01") -> str:
    """Export Facebook minimal, au format texte de Meta Business Suite."""
    dates = pd.date_range(start, periods=n, freq="D")
    lines = [
        "Identifiant de la publication,ID de la Page,Nom de la page,Heure de publication,"
        "Permalien,Type de publication,Vues,Couverture"
    ]
    lines += [
        f"{account}-{i},10,{account},{date:%m/%d/%Y} 10:00,"
        f"https://www.facebook.com/page/posts/{i},Photos,{100 + i},{60 + i}"
        for i, date in enumerate(dates)
    ]
    return "\n".join(lines) + "\n"
