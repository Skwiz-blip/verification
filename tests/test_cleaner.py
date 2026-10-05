"""Tests du pipeline de nettoyage."""

from __future__ import annotations

import pandas as pd

from socialstats.cleaner import drop_low_volume_accounts


class TestDropLowVolumeAccounts:
    def _frame(self, counts: dict[str, int]) -> pd.DataFrame:
        names = [name for name, n in counts.items() for _ in range(n)]
        return pd.DataFrame({"account_name": names, "views_organic_resolved": 1.0})

    def test_ecarte_les_comptes_sous_le_seuil(self, settings):
        settings.min_publications_per_account = 10
        frame = self._frame({"Actif": 50, "Isole": 1, "Petit": 3})
        kept, dropped = drop_low_volume_accounts(frame, settings)
        assert set(kept["account_name"]) == {"Actif"}
        assert dropped == ["Isole", "Petit"]

    def test_seuil_zero_conserve_tout(self, settings):
        settings.min_publications_per_account = 0
        frame = self._frame({"Actif": 50, "Isole": 1})
        kept, dropped = drop_low_volume_accounts(frame, settings)
        assert len(kept) == len(frame)
        assert dropped == []

    def test_aucun_compte_sous_le_seuil(self, settings):
        settings.min_publications_per_account = 10
        frame = self._frame({"A": 20, "B": 30})
        kept, dropped = drop_low_volume_accounts(frame, settings)
        assert dropped == []
        assert len(kept) == len(frame)
