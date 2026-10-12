"""Tests des listes de pages nommees (interface locale)."""

from __future__ import annotations

import pytest

from socialstats.page_lists import MAX_ACCOUNTS, PageListError, PageListStore, clean_list


@pytest.fixture
def store(tmp_path) -> PageListStore:
    return PageListStore(tmp_path / "data" / "listes.json")


class TestCleanList:
    def test_nom_nettoye_et_pages_dedoublonnees(self):
        list_id, name, accounts = clean_list({"nom": "  Pages d'Awa ", "comptes": ["b", "A", "b"]})
        assert list_id is None
        assert name == "Pages d'Awa"
        assert accounts == ["A", "b"]

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {"nom": "   ", "comptes": []},
            {"nom": "x" * 81, "comptes": []},
            {"nom": "Awa", "comptes": "Compte A"},
            {"nom": "Awa", "comptes": [""]},
            {"nom": "Awa", "comptes": [12]},
            {"nom": "Awa", "comptes": [], "id": 12},
            {"nom": "Awa", "comptes": [f"Compte {i}" for i in range(MAX_ACCOUNTS + 1)]},
        ],
    )
    def test_liste_refusee(self, payload):
        with pytest.raises(PageListError):
            clean_list(payload)


class TestPageListStore:
    def test_sans_fichier_aucune_liste(self, store):
        assert store.all() == []

    def test_creer_modifier_supprimer(self, store):
        created = store.save({"nom": "Awa", "comptes": ["Compte A"]})
        assert store.all() == [created]

        renamed = store.save({"id": created["id"], "nom": "Pages d'Awa", "comptes": ["Compte A", "Compte B"]})
        assert renamed["id"] == created["id"]
        assert [(item["nom"], item["comptes"]) for item in store.all()] == [
            ("Pages d'Awa", ["Compte A", "Compte B"])
        ]

        store.remove(created["id"])
        store.remove(created["id"])  # deja absente : sans erreur
        assert store.all() == []

    def test_nom_unique_sans_tenir_compte_de_la_casse(self, store):
        store.save({"nom": "Awa", "comptes": []})
        with pytest.raises(PageListError, match="porte deja ce nom"):
            store.save({"nom": "AWA ", "comptes": []})

    def test_une_liste_supprimee_entre_temps_n_est_pas_recreee(self, store):
        with pytest.raises(PageListError, match="n'existe plus"):
            store.save({"id": "inconnu", "nom": "Awa", "comptes": []})

    def test_un_fichier_illisible_n_est_jamais_ecrase(self, store):
        """Repartir de zero effacerait toutes les listes au prochain enregistrement."""
        store.path.parent.mkdir(parents=True)
        store.path.write_text("{pas du json", encoding="utf-8")
        with pytest.raises(PageListError, match="illisible"):
            store.save({"nom": "Awa", "comptes": []})
        assert store.path.read_text(encoding="utf-8") == "{pas du json"

    def test_triees_par_nom(self, store):
        for name in ("koffi", "Awa", "Brice"):
            store.save({"nom": name, "comptes": []})
        assert [item["nom"] for item in store.all()] == ["Awa", "Brice", "koffi"]
