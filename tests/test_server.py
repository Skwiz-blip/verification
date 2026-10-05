"""Tests du serveur local : reponses JSON, parametres douteux, requetes refusees."""

from __future__ import annotations

import http.client
import json
import shutil
import threading
from pathlib import Path

import pandas as pd
import pytest

from socialstats.cleaner import CleaningResult
from socialstats.server import (
    VERDICT_CODES,
    build_control,
    build_posts,
    client_allowed,
    host_allowed,
    is_this_machine,
    make_server,
)

from helpers import make_export_csv, make_posts

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cleaning() -> CleaningResult:
    data = pd.concat(
        [
            make_posts("Compte A", "photo", [100.0 + i for i in range(60)], start="2026-01-01"),
            make_posts("Compte A", "photo", [200.0] * 5, start="2026-04-01"),
        ],
        ignore_index=True,
    )
    return CleaningResult(data=data, sponsored_data=data.iloc[0:0], rejected_data=data.iloc[0:0])


class TestBuildControl:
    def test_la_reponse_est_du_json_strict(self, cleaning, settings):
        payload = build_control(cleaning, settings)
        json.dumps(payload, allow_nan=False)
        assert payload["meta"]["mois_controle"] == "2026-04"
        assert payload["meta"]["ref"]["fin"] == "2026-03"
        assert not payload["meta"]["ref"]["inclut_mois"]
        assert {row["niveau"] for row in payload["historique"]} <= set(VERDICT_CODES.values())

    def test_les_parametres_invalides_sont_ramenes_a_des_valeurs_sures(self, cleaning, settings):
        payload = build_control(cleaning, settings, month="pas-un-mois", preset="???", min_posts=999)
        assert payload["meta"]["mois_controle"] == "2026-04"
        assert payload["meta"]["ref"]["preset"] == "before"
        assert payload["meta"]["min"] == 30

    def test_premier_mois_sans_historique(self, cleaning, settings):
        payload = build_control(cleaning, settings, month="2026-01")
        assert payload["meta"]["ref"]["sans_historique"]
        assert payload["meta"]["ref"]["inclut_mois"]

    def test_jeu_vide(self, cleaning, settings):
        nothing = cleaning.data.iloc[0:0]
        empty = CleaningResult(data=nothing, sponsored_data=nothing, rejected_data=nothing)
        assert build_control(empty, settings)["vide"]

    def test_publications_d_un_mois(self, cleaning):
        posts = build_posts(cleaning, "Compte A", "facebook", "photo", "2026-04")
        assert len(posts) == 5
        assert posts[0]["date"] == "2026-04-05T00:00"
        assert build_posts(cleaning, "Compte A", "facebook", "photo", "n'importe quoi") == []


def serve(tmp_path, **options):
    """Serveur reel sur un projet temporaire : vraie configuration, export minimal."""
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "juin.csv").write_text(make_export_csv(n=45), encoding="utf-8")

    server = make_server(tmp_path / "config", PROJECT_ROOT / "web", port=0, **options)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], raw_dir
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def live(tmp_path):
    yield from serve(tmp_path)


@pytest.fixture
def live_lan(tmp_path):
    """Mode reseau, mais lie a la boucle locale : aucun port n'est ouvert sur le reseau."""
    yield from serve(tmp_path, lan=True, bind="127.0.0.1")


def request(
    port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None
):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


class TestHttp:
    def test_la_page_est_servie_avec_ses_protections(self, live):
        port, _ = live
        status, headers, body = request(port, "GET", "/")
        assert status == 200
        assert b"<title>Contr" in body
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        assert headers["X-Content-Type-Options"] == "nosniff"

    def test_le_controle_repond_sur_un_export_reel(self, live):
        port, _ = live
        status, _, body = request(port, "GET", "/api/controle")
        payload = json.loads(body)
        assert status == 200
        assert not payload["vide"]
        assert payload["meta"]["mois_controle"] == "2026-07"
        assert payload["meta"]["ref"]["mois"] == ["2026-06"]

    def test_seuls_les_fichiers_de_l_interface_sont_servis(self, live):
        port, _ = live
        for path in ("/config/clients.csv", "/../config/clients.csv", "/app.py"):
            assert request(port, "GET", path)[0] == 404

    def test_un_hote_etranger_est_refuse(self, live):
        port, _ = live
        assert request(port, "GET", "/api/controle", headers={"Host": "exemple.test"})[0] == 403

    def test_un_depot_sans_l_en_tete_attendu_est_refuse(self, live):
        port, raw_dir = live
        body = make_export_csv(n=5, start="2026-08-01").encode("utf-8")
        assert request(port, "POST", "/api/import?nom=aout.csv", body)[0] == 403
        foreign = {"X-Requested-With": "socialstats", "Origin": "https://exemple.test"}
        assert request(port, "POST", "/api/import?nom=aout.csv", body, foreign)[0] == 403
        assert [p.name for p in raw_dir.iterdir()] == ["juin.csv"]

    def test_un_depot_valide_est_pris_en_compte_au_calcul_suivant(self, live):
        port, raw_dir = live
        body = make_export_csv(n=20, account="Compte B", start="2026-08-01").encode("utf-8")
        status, _, answer = request(
            port, "POST", "/api/import?nom=aout.csv", body, {"X-Requested-With": "socialstats"}
        )
        assert status == 200
        assert json.loads(answer)["statut"] == "added"
        assert (raw_dir / "aout.csv").exists()

        payload = json.loads(request(port, "GET", "/api/controle")[2])
        assert payload["meta"]["mois"][-1] == "2026-08"
        assert payload["meta"]["fichiers"] == 2


class TestNetworkMode:
    def test_sans_mode_reseau_seule_la_boucle_locale_est_servie(self):
        assert host_allowed("localhost")
        assert not host_allowed("192.168.1.20")
        assert client_allowed("127.0.0.1")
        assert not client_allowed("192.168.1.20")

    def test_en_mode_reseau_les_postes_du_reseau_local_consultent(self):
        assert client_allowed("192.168.1.20", lan=True)
        assert client_allowed("10.0.0.7", lan=True)
        assert host_allowed("192.168.1.147", lan=True)
        assert host_allowed("poste-cm", lan=True, machine_names=frozenset({"poste-cm"}))

    def test_une_adresse_publique_n_obtient_rien(self):
        # Meme si le port etait expose au-dela du reseau local.
        assert not client_allowed("8.8.8.8", lan=True)
        assert not client_allowed("pas-une-adresse", lan=True)

    def test_un_nom_de_domaine_etranger_reste_refuse(self):
        """Le rebinding DNS passe par un nom de domaine, jamais par une adresse IP."""
        assert not host_allowed("exemple.test", lan=True, machine_names=frozenset({"poste-cm"}))

    def test_l_import_reste_reserve_au_poste_hebergeur(self):
        own = frozenset({"192.168.1.147"})
        assert is_this_machine("127.0.0.1", own)
        assert is_this_machine("192.168.1.147", own)
        assert not is_this_machine("192.168.1.20", own)

    def test_le_serveur_local_refuse_une_adresse_du_reseau(self, live):
        port, _ = live
        headers = {"Host": f"192.168.1.147:{port}"}
        assert request(port, "GET", "/api/controle", headers=headers)[0] == 403

    def test_le_serveur_reseau_repond_a_son_adresse_ip(self, live_lan):
        port, _ = live_lan
        status, _, body = request(port, "GET", "/api/controle", headers={"Host": f"192.168.1.147:{port}"})
        assert status == 200
        # La requete de test vient de ce poste : l'import lui reste ouvert.
        assert json.loads(body)["import_autorise"] is True
        assert request(port, "GET", "/", headers={"Host": "exemple.test"})[0] == 403

    def test_le_serveur_reseau_refuse_un_depot_d_origine_etrangere(self, live_lan):
        port, raw_dir = live_lan
        body = make_export_csv(n=5, start="2026-08-01").encode("utf-8")
        foreign = {"X-Requested-With": "socialstats", "Origin": "https://exemple.test"}
        assert request(port, "POST", "/api/import?nom=aout.csv", body, foreign)[0] == 403
        same = {"X-Requested-With": "socialstats", "Origin": f"http://192.168.1.147:{port}"}
        assert request(port, "POST", "/api/import?nom=aout.csv", body, same)[0] == 200
        assert (raw_dir / "aout.csv").exists()
