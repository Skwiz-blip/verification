"""Tests de la deduplication inter-fichiers."""

from __future__ import annotations

import pandas as pd

from socialstats.dedup import deduplicate


def two_exports_of_same_post() -> pd.DataFrame:
    """Une meme publication exportee a deux dates : les compteurs ont augmente."""
    return pd.DataFrame(
        {
            "post_id": ["A1", "A1", "B2"],
            "account_name": ["Compte A", "Compte A", "Compte B"],
            "views_total": [100.0, 180.0, 50.0],
            "reactions": [5.0, 9.0, 2.0],
            "is_sponsored": [False, False, False],
            "source_file": ["export_juillet.csv", "export_aout.csv", "export_aout.csv"],
        }
    )


class TestDeduplicate:
    def test_fusionne_les_doublons(self):
        result, report = deduplicate(two_exports_of_same_post())
        assert len(result) == 2
        assert report.n_duplicate_ids == 1
        assert report.n_rows_merged == 1

    def test_conserve_la_mesure_la_plus_recente(self):
        """Les compteurs Meta sont cumulatifs : le maximum est la valeur a jour."""
        result, _ = deduplicate(two_exports_of_same_post())
        row = result[result["post_id"] == "A1"].iloc[0]
        assert row["views_total"] == 180.0
        assert row["reactions"] == 9.0

    def test_signale_les_chevauchements_entre_fichiers(self):
        _, report = deduplicate(two_exports_of_same_post())
        assert report.overlapping_files == [("export_aout.csv", "export_juillet.csv", 1)]

    def test_le_statut_sponsorise_est_conserve(self):
        """Sponsorisee dans un seul export = sponsorisee definitivement."""
        data = pd.DataFrame(
            {
                "post_id": ["A1", "A1"],
                "account_name": ["Compte A", "Compte A"],
                "views_total": [100.0, 180.0],
                "is_sponsored": [False, True],
                "source_file": ["f1.csv", "f2.csv"],
            }
        )
        result, _ = deduplicate(data)
        assert bool(result["is_sponsored"].iloc[0]) is True

    def test_sans_doublon_le_jeu_est_inchange(self):
        data = pd.DataFrame(
            {
                "post_id": ["A", "B"],
                "account_name": ["X", "Y"],
                "views_total": [1.0, 2.0],
                "is_sponsored": [False, False],
                "source_file": ["f.csv", "f.csv"],
            }
        )
        result, report = deduplicate(data)
        assert len(result) == 2
        assert report.n_rows_merged == 0

    def test_publications_sans_identifiant_sont_conservees(self):
        data = pd.DataFrame(
            {
                "post_id": [None, "B"],
                "account_name": ["X", "Y"],
                "views_total": [1.0, 2.0],
                "is_sponsored": [False, False],
                "source_file": ["f.csv", "f.csv"],
            }
        )
        result, _ = deduplicate(data)
        assert len(result) == 2
