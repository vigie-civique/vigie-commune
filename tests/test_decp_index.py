"""L'index des consolidés DECP par acheteur (P2 du chantier débit, 30/10/2026).

L'écrivain du magasin l'écrit (`marches_publics.remplir_magasin`) ; le step
`marches` le lit, et relit le consolidé en flux dès qu'il ne lui correspond plus.
La propriété qui compte : l'index rend EXACTEMENT ce que rendait la lecture en
flux, ou rien.
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

pytest.importorskip("ijson", reason="job « tests-deps »")

from collectors import marches_publics as marches  # noqa: E402
from collectors import national_store  # noqa: E402

CONSOLIDE_2019 = {"marches": [
    {"id": "A", "acheteur": {"id": "21300140700011", "nom": "Commune de Lasalle"},
     "objet": "voirie", "montant": 1000.5, "titulaires": [{"id": "12345678900011"}]},
    {"id": "B", "acheteur": {"id": "99999999900011", "nom": "Ailleurs"}, "montant": 2000},
    {"id": "S", "acheteur": {"id": "25300000000011", "nom": "Syndicat des Cévennes"},
     "montant": 3000},
    "pas un marché",
]}
CONSOLIDE_2024 = {"marches": {"marche": [
    {"id": "C", "acheteur": {"id": "20007031600011", "nom": "CC Causses Aigoual"}, "montant": 4},
    {"id": "A", "acheteur": {"id": "21300140700011", "nom": "Commune"}, "montant": 5},
]}}


@pytest.fixture
def magasin(tmp_path, monkeypatch):
    cache = tmp_path / "decp"
    cache.mkdir()
    (cache / "decp-2019.json").write_text(json.dumps(CONSOLIDE_2019), encoding="utf-8")
    (cache / "decp-2024.json").write_text(json.dumps(CONSOLIDE_2024), encoding="utf-8")
    monkeypatch.setattr(marches, "DECP_CACHE", cache)
    monkeypatch.setattr(marches, "COMMUNE_SIREN", "213001407")
    monkeypatch.setattr(marches, "COMMUNE_SIRENS", ["213001407"])
    monkeypatch.setattr(marches, "CAC_SIREN", "200070316")
    monkeypatch.setattr(marches, "ACHETEUR_JETONS", ("cévennes",))
    monkeypatch.setattr(marches, "_index_ouvert", {})
    monkeypatch.delenv("VIGIE_ECRIVAIN_MAGASIN", raising=False)
    return cache


def _en_flux(nom):
    return [marches.normalize_decp_marche(m)
            for m in marches.extract_marches_from_file("https://example.test/x", nom)]


def test_l_index_rend_ce_que_rend_la_lecture_en_flux(magasin):
    assert marches.remplir_magasin() == []
    for nom in ("decp-2019.json", "decp-2024.json"):
        assert marches.marches_indexes(nom) == _en_flux(nom)
    assert [m["raw_id"] for m in marches.marches_indexes("decp-2019.json")] == ["A", "S"]


def test_un_consolide_republie_n_est_plus_lu_dans_l_index(magasin):
    marches.remplir_magasin()
    fichier = magasin / "decp-2024.json"
    fichier.write_text(json.dumps({"marches": []}), encoding="utf-8")
    os.utime(fichier, ns=(fichier.stat().st_atime_ns, fichier.stat().st_mtime_ns + 10**9))
    assert marches.marches_indexes("decp-2024.json") is None
    assert marches.marches_indexes("decp-2019.json") is not None


def test_un_index_d_un_autre_schema_est_ignore(magasin, monkeypatch):
    marches.remplir_magasin()
    monkeypatch.setattr(marches, "DECP_INDEX_SCHEMA", marches.DECP_INDEX_SCHEMA + 1)
    monkeypatch.setattr(marches, "_index_ouvert", {})
    assert marches.marches_indexes("decp-2019.json") is None


def test_sans_index_le_lecteur_relit_le_consolide(magasin):
    assert marches.marches_indexes("decp-2019.json") is None


def test_l_ecrivain_ne_lit_jamais_l_index(magasin, monkeypatch):
    marches.remplir_magasin()
    monkeypatch.setenv("VIGIE_ECRIVAIN_MAGASIN", "1")
    assert marches.marches_indexes("decp-2019.json") is None


def test_un_marche_non_normalise_renvoie_a_la_lecture_en_flux(magasin, monkeypatch):
    vraie = marches.normalize_decp_marche

    def capricieuse(m):
        if m.get("id") == "S":
            raise ValueError("forme inattendue")
        return vraie(m)
    monkeypatch.setattr(marches, "normalize_decp_marche", capricieuse)
    marches.remplir_magasin()
    assert marches.marches_indexes("decp-2019.json") is None, "un marché du périmètre manque"
    assert marches.marches_indexes("decp-2024.json") is not None


def test_un_consolide_illisible_est_rendu_en_echec_sans_bloquer_les_autres(magasin):
    (magasin / "decp-2020.json").write_text('{"marches": [{"id": "X", "acheteur": {"id"',
                                           encoding="utf-8")
    assert marches.remplir_magasin() == ["decp-2020.json"]
    assert marches.marches_indexes("decp-2024.json") is not None
    assert marches.marches_indexes("decp-2020.json") is None


def test_l_index_est_publie_par_renommage_sans_rien_laisser(magasin):
    marches.remplir_magasin()
    assert sorted(p.name for p in magasin.iterdir()) == [
        "decp-2019.json", "decp-2024.json", marches.DECP_INDEX_NOM]
    base = sqlite3.connect(magasin / marches.DECP_INDEX_NOM)
    assert base.execute("SELECT valeur FROM meta WHERE cle='schema'").fetchone() == (
        str(marches.DECP_INDEX_SCHEMA),)
    assert base.execute("SELECT nom, marches FROM fichiers ORDER BY nom").fetchall() == [
        ("decp-2019.json", 3), ("decp-2024.json", 2)]


def test_une_base_abimee_n_entre_pas_au_magasin(tmp_path):
    temporaire = tmp_path / ".index.sqlite.1.part"
    temporaire.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    with pytest.raises(national_store.ContenuInvalide):
        national_store.valider_contenu(temporaire, tmp_path / "index.sqlite")


def test_fetch_decp_marches_prend_l_index_et_garde_le_dedoublonnage(magasin, monkeypatch):
    marches.remplir_magasin()
    monkeypatch.setattr(marches, "discover_decp_resources", lambda: [
        {"title": "decp-2019.json", "url": "https://example.test/2019"},
        {"title": "decp-2024.json", "url": "https://example.test/2024"}])
    monkeypatch.setattr(marches, "extract_marches_from_file",
                        lambda *a: pytest.fail("le consolidé a été relu"))
    trouves = marches.fetch_decp_marches()
    assert [m["raw_id"] for m in trouves] == ["A", "S", "C"]
    assert trouves[0]["montant"] == 1000.5


def test_un_blob_hors_perimetre_n_est_jamais_rendu(magasin):
    """Le filtre porte sur les colonnes, la publication sur les blobs : une base
    qui les désapparie ne fait pas publier le marché d'un autre acheteur."""
    import zlib
    marches.remplir_magasin()
    index = magasin / marches.DECP_INDEX_NOM
    base = sqlite3.connect(index)
    etranger = zlib.compress(json.dumps(marches.normalize_decp_marche(
        CONSOLIDE_2019["marches"][1])).encode())
    base.execute("UPDATE donnees SET marche = ? WHERE id = (SELECT id FROM marches"
                 " WHERE fichier = 'decp-2019.json' AND rang = 1)", (etranger,))
    base.commit()
    base.close()
    assert marches.marches_indexes("decp-2019.json") is None


def test_un_index_inscriptible_par_le_groupe_est_ignore(magasin):
    marches.remplir_magasin()
    (magasin / marches.DECP_INDEX_NOM).chmod(0o664)
    assert marches.marches_indexes("decp-2019.json") is None


def test_une_table_en_trop_fait_ignorer_l_index(magasin):
    marches.remplir_magasin()
    base = sqlite3.connect(magasin / marches.DECP_INDEX_NOM)
    base.execute("CREATE VIEW piege AS SELECT 1")
    base.commit()
    base.close()
    assert marches.marches_indexes("decp-2019.json") is None


def test_une_autre_normalisation_fait_ignorer_l_index(magasin, monkeypatch):
    marches.remplir_magasin()
    vraie = marches.normalize_decp_marche
    monkeypatch.setattr(marches, "normalize_decp_marche", lambda m: {**vraie(m), "neuf": 1})
    monkeypatch.setattr(marches, "_index_ouvert", {})
    assert marches.marches_indexes("decp-2019.json") is None


def test_un_index_qui_ne_se_lit_pas_ne_fait_pas_echouer_le_step(magasin, monkeypatch):
    marches.remplir_magasin()
    base = sqlite3.connect(magasin / marches.DECP_INDEX_NOM)
    base.execute("UPDATE donnees SET marche = X'00FF'")
    base.commit()
    base.close()
    assert marches.marches_indexes("decp-2019.json") is None


def test_sans_epci_le_perimetre_ne_s_ouvre_pas_a_la_france(monkeypatch):
    monkeypatch.setattr(marches, "COMMUNE_SIREN", "213001407")
    monkeypatch.setattr(marches, "COMMUNE_SIRENS", ["213001407"])
    monkeypatch.setattr(marches, "CAC_SIREN", "")
    monkeypatch.setattr(marches, "ACHETEUR_JETONS", ("",))
    assert not marches.dans_le_perimetre("99999999900011", "ailleurs")
    assert marches.dans_le_perimetre("21300140700011", "commune")


def test_un_consolide_casse_apres_un_lot_n_abime_pas_les_autres(magasin):
    """En journal OFF, le ROLLBACK après un lot écrit laissait une base
    « malformed » : l'index de toute la nuit était perdu (revue du 30/10)."""
    lignes = ",".join(json.dumps({"id": f"X{i}", "acheteur": {"id": "99999999900011", "nom": "x"}})
                      for i in range(20_050))
    (magasin / "decp-2020.json").write_text('{"marches": [' + lignes + ', {"id": "Y", "ach',
                                           encoding="utf-8")
    assert marches.remplir_magasin() == ["decp-2020.json"]
    assert marches.marches_indexes("decp-2019.json") == _en_flux("decp-2019.json")
    assert marches.marches_indexes("decp-2024.json") is not None
