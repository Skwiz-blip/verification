"""Tests du calcul du taux d'engagement."""

from __future__ import annotations

import pandas as pd

from socialstats.engagement import (
    BASE_REACH_ORGANIC,
    BASE_REACH_TOTAL,
    BASE_VIEWS,
    compute_engagement,
    compute_interactions,
)


def base_frame(**overrides) -> pd.DataFrame:
    data = {
        "reactions": [10.0],
        "comments": [3.0],
        "shares": [2.0],
        "interactions_total": [None],
        "reach_organic": [None],
        "reach_total": [None],
        "views_organic_resolved": [None],
        "is_sponsored": [False],
    }
    data.update(overrides)
    return pd.DataFrame(data)


class TestComputeInteractions:
    def test_somme_les_composantes(self):
        assert compute_interactions(base_frame()).iloc[0] == 15.0

    def test_prefere_la_colonne_agregee_de_meta(self):
        result = compute_interactions(base_frame(interactions_total=[99.0]))
        assert result.iloc[0] == 99.0

    def test_ligne_entierement_vide_reste_nan(self):
        """Une ligne sans donnee ne doit pas devenir "0 interaction"."""
        result = compute_interactions(
            base_frame(reactions=[None], comments=[None], shares=[None])
        )
        assert pd.isna(result.iloc[0])


class TestComputeEngagement:
    def test_utilise_la_couverture_organique(self):
        result = compute_engagement(base_frame(reach_organic=[300.0]))
        assert result["engagement_rate"].iloc[0] == 5.0
        assert result["engagement_base_kind"].iloc[0] == BASE_REACH_ORGANIC

    def test_repli_sur_couverture_totale_si_non_sponsorise(self):
        result = compute_engagement(base_frame(reach_total=[300.0]))
        assert result["engagement_rate"].iloc[0] == 5.0
        assert result["engagement_base_kind"].iloc[0] == BASE_REACH_TOTAL

    def test_repli_ultime_sur_les_vues_est_signale(self):
        """La base change de semantique : elle doit rester tracee."""
        result = compute_engagement(base_frame(views_organic_resolved=[300.0]))
        assert result["engagement_rate"].iloc[0] == 5.0
        assert result["engagement_base_kind"].iloc[0] == BASE_VIEWS

    def test_division_par_zero_donne_nan_pas_l_infini(self):
        result = compute_engagement(base_frame(reach_organic=[0.0]))
        assert pd.isna(result["engagement_rate"].iloc[0])

    def test_base_absente_donne_nan(self):
        result = compute_engagement(base_frame())
        assert pd.isna(result["engagement_rate"].iloc[0])

    def test_sponsorise_n_utilise_pas_la_couverture_totale(self):
        """La couverture totale d'une publication boostee inclut la portee payante."""
        result = compute_engagement(base_frame(reach_total=[300.0], is_sponsored=[True]))
        assert result["engagement_base_kind"].iloc[0] != BASE_REACH_TOTAL
