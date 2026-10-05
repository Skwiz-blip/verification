"""Tests du controle mensuel : reference, verdicts, categories, comptage des vues."""

from __future__ import annotations

import pandas as pd
import pytest

from socialstats.monthly import (
    BELOW,
    LEVEL_3,
    NO_OBJECTIVE,
    NO_POST,
    REFERENCE_3,
    REFERENCE_ALL,
    REFERENCE_BEFORE,
    REFERENCE_CUSTOM,
    TOO_FEW,
    TOO_FEW_ACCOUNTS,
    category_results,
    default_month,
    measurement_breaks,
    month_view,
    monthly_results,
    reference_bounds,
    slice_months,
    views_per_reach,
)
from socialstats.objectives import build_objectives, build_sector_objectives

from helpers import make_posts

APRIL = pd.Period("2026-04", freq="M")


def months(*keys: str) -> list[pd.Period]:
    return [pd.Period(key, freq="M") for key in keys]


def reference_posts(account: str = "Compte A", base: float = 100.0) -> pd.DataFrame:
    """60 publications de janvier a debut mars : N1=130, N2=145, N3=150 pour base=100."""
    return make_posts(account, "photo", [base + i for i in range(60)], start="2026-01-01")


def april_posts(account: str, views: list[float]) -> pd.DataFrame:
    return make_posts(account, "photo", views, start="2026-04-01")


class TestReferenceBounds:
    LOADED = months("2026-01", "2026-02", "2026-03", "2026-04", "2026-05")

    def test_par_defaut_la_reference_s_arrete_avant_le_mois_controle(self):
        # Les publications jugees ne doivent pas fixer leurs propres seuils.
        bounds = reference_bounds(REFERENCE_BEFORE, self.LOADED, APRIL)
        assert bounds == tuple(months("2026-01", "2026-03"))

    def test_fenetre_glissante(self):
        may = pd.Period("2026-05", freq="M")
        assert reference_bounds(REFERENCE_3, self.LOADED, may) == tuple(months("2026-02", "2026-04"))

    def test_toute_la_periode_inclut_le_mois_controle(self):
        bounds = reference_bounds(REFERENCE_ALL, self.LOADED, APRIL)
        assert bounds == tuple(months("2026-01", "2026-05"))

    def test_aucun_mois_anterieur(self):
        assert reference_bounds(REFERENCE_BEFORE, self.LOADED, self.LOADED[0]) is None

    def test_periode_personnalisee_bornes_inversees(self):
        start, end = months("2026-03", "2026-02")
        bounds = reference_bounds(REFERENCE_CUSTOM, self.LOADED, APRIL, start, end)
        assert bounds == tuple(months("2026-02", "2026-03"))

    def test_les_bornes_sont_des_mois_charges(self):
        # Fevrier et mars manquent : la reference ne peut pas s'y arreter.
        loaded = months("2026-01", "2026-04")
        assert reference_bounds(REFERENCE_3, loaded, APRIL) == tuple(months("2026-01", "2026-01"))


class TestDefaultMonth:
    def test_ignore_un_dernier_mois_a_peine_entame(self):
        data = pd.concat(
            [reference_posts(), make_posts("Compte A", "photo", [100.0], start="2026-04-01")]
        )
        assert default_month(data) == pd.Period("2026-02", freq="M")


class TestMonthlyResults:
    def run(self, settings, april: pd.DataFrame, min_posts: int = 3) -> pd.Series:
        reference = reference_posts()
        objectives = build_objectives(reference, settings)
        results = monthly_results(pd.concat([reference, april]), objectives, settings, min_posts)
        return results[results["Mois"] == APRIL].iloc[0]

    def test_mois_au_dessus_du_niveau_3(self, settings):
        row = self.run(settings, april_posts("Compte A", [200.0] * 5))
        assert (row["Niveau 1"], row["Niveau 2"], row["Niveau 3"]) == (130.0, 145.0, 150.0)
        assert row["Niveau atteint"] == LEVEL_3
        assert row["Atteinte N1 (%)"] == 100.0

    def test_mois_sous_le_niveau_1(self, settings):
        row = self.run(settings, april_posts("Compte A", [100.0] * 5))
        assert row["Niveau atteint"] == BELOW
        assert row["Ecart au Niveau 1 (%)"] == pytest.approx(-23.1)

    def test_trop_peu_de_publications_ne_donne_aucun_niveau(self, settings):
        """Deux publications virales ne font pas un bon mois."""
        row = self.run(settings, april_posts("Compte A", [5000.0, 5000.0]))
        assert row["Niveau atteint"] == TOO_FEW

    def test_compte_sans_historique(self, settings):
        reference = reference_posts()
        objectives = build_objectives(reference, settings)
        data = pd.concat([reference, april_posts("Nouveau", [300.0] * 5)])
        results = monthly_results(data, objectives, settings)
        row = results[results["Compte"] == "Nouveau"].iloc[0]
        assert row["Niveau atteint"] == NO_OBJECTIVE
        assert pd.isna(row["Niveau 1"])

    def test_les_formats_exclus_ne_sont_pas_controles(self, settings):
        data = make_posts("Compte A", "link", [50.0] * 20)
        assert monthly_results(data, build_objectives(data, settings), settings).empty

    def test_vues_en_float64_nullable(self, settings):
        """Le pipeline livre les vues resolues en Float64 nullable."""
        reference = reference_posts()
        data = pd.concat([reference, april_posts("Compte A", [200.0] * 5)])
        data["views_organic_resolved"] = data["views_organic_resolved"].astype("Float64")
        results = monthly_results(data, build_objectives(reference, settings), settings)
        assert results[results["Mois"] == APRIL].iloc[0]["Niveau atteint"] == LEVEL_3


class TestMonthView:
    def test_un_compte_devenu_muet_reste_visible(self, settings):
        reference = pd.concat([reference_posts("Compte A"), reference_posts("Compte B", 200.0)])
        objectives = build_objectives(reference, settings)
        data = pd.concat([reference, april_posts("Compte A", [200.0] * 5)])
        view = month_view(monthly_results(data, objectives, settings), objectives, APRIL)

        silent = view[view["Compte"] == "Compte B"].iloc[0]
        assert silent["Niveau atteint"] == NO_POST
        assert silent["Publications"] == 0
        assert silent["Niveau 1"] == 230.0
        assert len(view) == 2


class TestCategoryResults:
    def setup_data(self, settings, april_accounts: dict[str, list[float]]):
        reference = pd.concat(
            [
                reference_posts("Compte A"),
                reference_posts("Compte B", 200.0),
                reference_posts("Compte C", 300.0),
            ]
        )
        data = pd.concat([reference] + [april_posts(a, v) for a, v in april_accounts.items()])
        results = monthly_results(data, build_objectives(reference, settings), settings)
        return results, build_sector_objectives(reference, settings)

    def test_chaque_compte_pese_pour_un(self, settings):
        """Le compte prolifique ne dicte pas la valeur de la categorie."""
        results, sector_objectives = self.setup_data(
            settings, {"Compte A": [100.0] * 30, "Compte B": [200.0] * 5, "Compte C": [300.0] * 5}
        )
        table = category_results(results, sector_objectives, settings=settings)
        row = table[table["Mois"] == APRIL].iloc[0]
        assert row["Mediane des comptes"] == 200.0
        assert row["Comptes evalues"] == 3
        # Les trois comptes sont sous leur propre Niveau 1 (130, 230, 330).
        assert row["Comptes a leur Niveau 1 ou plus"] == 0

    def test_pas_de_niveau_de_categorie_sur_trop_peu_de_comptes(self, settings):
        results, sector_objectives = self.setup_data(settings, {"Compte C": [900.0] * 5})
        table = category_results(results, sector_objectives, settings=settings)
        row = table[table["Mois"] == APRIL].iloc[0]
        assert row["Comptes evalues"] == 1
        assert row["Niveau atteint"] == TOO_FEW_ACCOUNTS


class TestMeasurementBreaks:
    def posts(self, start: str, reach_ratio: float) -> pd.DataFrame:
        return make_posts("Compte A", "photo", [400.0] * 20, start=start, reach_ratio=reach_ratio)

    def test_un_changement_de_comptage_est_signale(self, settings):
        # 4 vues par personne en janvier, 2 en mars : meme audience, comptage different.
        data = pd.concat([self.posts("2026-01-01", 0.25), self.posts("2026-03-01", 0.5)])
        breaks = measurement_breaks(views_per_reach(data, settings))
        assert len(breaks) == 1
        assert breaks.iloc[0]["Rapport"] == 2.0
        assert breaks.iloc[0]["Mois haut"] == pd.Period("2026-01", freq="M")

    def test_un_ratio_stable_n_est_pas_signale(self, settings):
        data = pd.concat([self.posts("2026-01-01", 0.5), self.posts("2026-03-01", 0.5)])
        assert measurement_breaks(views_per_reach(data, settings)).empty

    def test_un_mois_trop_peu_alimente_est_ignore(self, settings):
        sparse = make_posts("Compte A", "photo", [400.0] * 3, start="2026-03-01", reach_ratio=0.1)
        data = pd.concat([self.posts("2026-01-01", 0.5), sparse])
        assert measurement_breaks(views_per_reach(data, settings)).empty


def test_slice_months_bornes_incluses():
    data = reference_posts()
    start, end = months("2026-02", "2026-02")
    assert set(slice_months(data, start, end)["published_at"].dt.month) == {2}
