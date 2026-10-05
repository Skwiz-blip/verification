"""Tests de la detection des valeurs atypiques."""

from __future__ import annotations

import numpy as np
import pandas as pd
from helpers import make_posts

from socialstats.outliers import (
    compare_methods,
    describe,
    flag_outliers,
    iqr_bounds,
    mad_bounds,
    trimmed_mean,
)


class TestBounds:
    def test_borne_basse_jamais_negative(self):
        """Un nombre de vues negatif n'a aucun sens."""
        low, _ = iqr_bounds(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
        assert low >= 0

    def test_mad_nul_ne_declare_pas_tout_aberrant(self):
        """Si plus de la moitie des valeurs sont identiques, le MAD degenere."""
        low, high = mad_bounds(np.array([100.0] * 10 + [500.0]))
        assert low == float("-inf") and high == float("inf")


class TestTrimmedMean:
    def test_ignore_les_extremes(self):
        values = np.array([1.0] * 9 + [1000.0])
        assert trimmed_mean(values, 0.10) < float(np.mean(values))

    def test_petit_echantillon_retombe_sur_la_moyenne(self):
        values = np.array([1.0, 2.0, 3.0])
        assert trimmed_mean(values, 0.10) == float(np.mean(values))


class TestDescribe:
    def test_rapport_complet(self):
        stats = describe(pd.Series([10.0, 20.0, 30.0, 40.0, 50.0]))
        assert stats.n == 5
        assert stats.median == 30.0
        assert stats.q1 == 20.0 and stats.q3 == 40.0
        assert stats.iqr == 20.0
        assert stats.method == "iqr"

    def test_detecte_une_publication_virale(self):
        stats = describe(pd.Series([100.0] * 20 + [50000.0]))
        assert stats.n_outliers_high == 1
        # La mediane resiste la ou la moyenne derape : c'est l'argument du choix.
        assert stats.median == 100.0
        assert stats.mean > 2000.0

    def test_serie_vide(self):
        stats = describe(pd.Series([], dtype=float))
        assert stats.n == 0
        assert np.isnan(stats.mean)

    def test_valeurs_manquantes_ignorees(self):
        stats = describe(pd.Series([10.0, None, 30.0]))
        assert stats.n == 2


class TestFlagOutliers:
    def test_ne_supprime_aucune_ligne(self):
        """Regle non negociable : detecter n'est pas supprimer."""
        data = make_posts("A", "photo", [100.0] * 20 + [50000.0])
        result = flag_outliers(data, "views_organic_resolved", ["account_name", "format"])
        assert len(result) == len(data)
        assert result["is_outlier"].sum() == 1

    def test_bornes_calculees_par_groupe(self):
        """Une photo n'est pas atypique parce qu'elle fait moins qu'un Reel."""
        data = pd.concat(
            [
                make_posts("A", "photo", [100.0] * 10),
                make_posts("A", "reel", [1000.0] * 10),
            ],
            ignore_index=True,
        )
        result = flag_outliers(data, "views_organic_resolved", ["account_name", "format"])
        assert not result["is_outlier"].any()

    def test_groupe_trop_petit_est_ignore(self):
        """En dessous de 4 observations, les quartiles ne veulent rien dire."""
        data = make_posts("A", "photo", [1.0, 2.0, 9999.0])
        result = flag_outliers(data, "views_organic_resolved", ["account_name", "format"])
        assert not result["is_outlier"].any()

    def test_cote_de_l_outlier_est_indique(self):
        data = make_posts("A", "photo", [100.0] * 20 + [50000.0])
        result = flag_outliers(data, "views_organic_resolved", ["account_name", "format"])
        assert "haute" in result.loc[result["is_outlier"], "outlier_side"].iloc[0]


class TestCompareMethods:
    def test_compare_toutes_les_methodes(self):
        result = compare_methods(pd.Series([100.0] * 20 + [50000.0]))
        assert len(result) == 5
        assert "Mediane" in result["methode"].tolist()

    def test_serie_vide(self):
        assert compare_methods(pd.Series([], dtype=float)).empty
