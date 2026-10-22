"""Guichets : des services, jamais leurs agents ; un type sans guichet se dit.

Aucun appel réseau. L'archive est FICTIVE — deux communes et quelques guichets
inventés, au format exact de la base de données locales de la DILA (un tar.bz2
qui porte un zip d'un fichier par commune et un JSON des services).

Ce que ces essais protègent :
  1. aucun champ de personne (`affectation_personne`, courriel) ne passe de la
     fiche à l'index, donc à la base ;
  2. seuls les types retenus, et les fiches diffusables, sont gardés ;
  3. un type retenu sans guichet sort quand même, vide, jusqu'à l'export ;
  4. une DILA injoignable rend l'index précédent, ou rien — jamais une erreur ;
  5. l'archive se lit en flux, telle que le serveur la sert.
"""
from __future__ import annotations

import bz2
import io
import json
import sqlite3
import tarfile
import urllib.error
import zipfile

import pytest

from collectors import guichets as g
from scripts.snapshot.socle import RULES
from scripts.snapshot.territoire import export_guichets

AGENT = "Agent-Fictif"   # ne doit apparaître nulle part après lecture

SERVICES = {"service": [
    {"id": "m-1", "nom": "Mairie - Bourg-Fictif", "code_insee_commune": "99001",
     "statut_de_diffusion": True,
     "adresse": [{"type_adresse": "Adresse postale", "numero_voie": "BP 1",
                  "code_postal": "99000", "nom_commune": "Bourg-Fictif"},
                 {"type_adresse": "Adresse", "complement1": "", "numero_voie": "1 place Imaginaire",
                  "code_postal": "99000", "nom_commune": "Bourg-Fictif"}],
     "telephone": [{"valeur": "", "description": ""}, {"valeur": "00 00 00 00 01"}],
     "site_internet": [{"valeur": "https://bourg-fictif.invalid"}],
     "url_service_public": "https://annuaire.invalid/m-1",
     "date_modification_datetime": "2026-01-02T03:04:05+00:00",
     "adresse_courriel": ["agent.fictif@bourg-fictif.invalid"],
     "affectation_personne": [{"personne": {"nom": AGENT, "prenom": "Prénom"},
                               "fonction": "Maire"}]},
    {"id": "fs-1", "nom": "France services - Bourg-Fictif", "code_insee_commune": "99001",
     "statut_de_diffusion": True, "adresse": [], "telephone": []},
    {"id": "fs-2", "nom": "France services - Ailleurs", "code_insee_commune": "99009",
     "statut_de_diffusion": True},
    {"id": "fs-3", "nom": "France services - Non diffusée", "code_insee_commune": "99009",
     "statut_de_diffusion": False},
    {"id": "x-1", "nom": "Type non retenu", "code_insee_commune": "99001",
     "statut_de_diffusion": True},
]}

COMMUNES = {
    "99001.json": {"commune": [{"code_insee_commune": "99001", "nom": "Bourg-Fictif",
                                "type_service_local": [
        {"organisme": ["m-1"], "code_type_service_local": "mairie"},
        {"organisme": ["fs-2", "fs-1", "fs-3"], "code_type_service_local": "france_services"},
        {"organisme": ["x-1"], "code_type_service_local": "ademe"},
    ]}]},
    "99002.json": {"commune": [{"code_insee_commune": "99002", "nom": "Hameau-Témoin",
                                "type_service_local": []}]},
}


def _archive() -> bytes:
    """Le tar.bz2 tel que la DILA le sert : le zip d'abord, puis les services."""
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        for nom, contenu in COMMUNES.items():
            z.writestr(nom, json.dumps(contenu))
    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w") as tar:
        for nom, octets in (("./2026-10-19_032022-data.gouv_commune.zip", zbuf.getvalue()),
                            ("./2026-10-19_033023-data.gouv_local.json",
                             json.dumps(SERVICES).encode())):
            info = tarfile.TarInfo(nom)
            info.size = len(octets)
            tar.addfile(info, io.BytesIO(octets))
    return bz2.compress(tbuf.getvalue())


class _Reponse(io.BytesIO):
    def __enter__(self):
        return self


@pytest.fixture
def magasin(tmp_path, monkeypatch):
    """L'index dans un magasin temporaire, l'archive servie sans réseau."""
    pytest.importorskip("ijson", reason="job « tests-deps »")
    for nom in ("CACHE", "COMPETENCES", "SERVICES", "META"):
        monkeypatch.setattr(g, nom, tmp_path / getattr(g, nom).name)
    monkeypatch.setattr(g, "_url_archive", lambda: "https://archive.invalid/all_latest.tar.bz2")
    monkeypatch.setattr(g.urllib.request, "urlopen",
                        lambda *a, **k: _Reponse(_archive()))
    return tmp_path


class TestIndex:
    def test_l_archive_se_lit_en_flux_et_date_son_fichier(self, magasin):
        competences, services, meta = g._index()
        assert set(competences) == {"99001", "99002"}
        assert meta["fichier"] == "2026-10-19_033023-data.gouv_local.json"
        assert meta["date"] == "2026-10-19"

    def test_seuls_les_types_retenus_et_les_fiches_diffusables(self, magasin):
        competences, services, _ = g._index()
        assert set(competences["99001"]) == {"mairie", "france_services"}
        assert set(services) == {"m-1", "fs-1", "fs-2"}

    def test_aucune_personne_n_entre_dans_l_index(self, magasin):
        g._index()
        brut = g.SERVICES.read_text()
        assert AGENT not in brut and "agent.fictif" not in brut
        assert set(json.loads(brut)["m-1"]) == {
            "nom", "commune", "adresse", "telephone", "site", "fiche", "maj"}

    def test_l_adresse_est_la_physique_et_le_telephone_le_premier_renseigne(self, magasin):
        _, services, _ = g._index()
        assert services["m-1"]["adresse"] == "1 place Imaginaire, 99000 Bourg-Fictif"
        assert services["m-1"]["telephone"] == "00 00 00 00 01"
        assert services["fs-1"]["adresse"] is None


class TestCoupure:
    def _coupe(self, monkeypatch):
        def refuse(*a, **k):
            raise urllib.error.URLError("connexion fermée par le serveur")
        monkeypatch.setattr(g.urllib.request, "urlopen", refuse)

    def test_sans_index_rien_et_pas_d_erreur(self, magasin, monkeypatch):
        self._coupe(monkeypatch)
        assert g._index() is None

    def test_sans_index_le_step_est_en_echec(self, magasin, monkeypatch):
        # Rendu vide, il serait noté `empty` et compté comme un passage abouti.
        self._coupe(monkeypatch)
        with pytest.raises(RuntimeError, match="aucun index"):
            g.run("99001")

    def test_l_index_precedent_sert(self, magasin, monkeypatch):
        g._index()
        g.META.touch()
        import os
        os.utime(g.META, (0, 0))   # périmé : on retente, la DILA coupe
        self._coupe(monkeypatch)
        assert set(g._index()[0]) == {"99001", "99002"}


class TestExport:
    def _base(self, tmp_path, index) -> sqlite3.Connection:
        competences, services, meta = index
        conn = sqlite3.connect(tmp_path / "guichets.db")
        conn.row_factory = sqlite3.Row
        g.ensure_table(conn)
        for insee in ("99001", "99002"):
            conn.executemany(
                "INSERT INTO guichets (insee, type, service_id, nom, commune, adresse,"
                " telephone, site, fiche_url, maj) VALUES (?,?,?,?,?,?,?,?,?,?)",
                g.lignes_de(insee, competences, services))
            conn.execute("INSERT INTO guichets_releves (insee, fichier, date_fichier, source_url)"
                         " VALUES (?,?,?,?)", (insee, meta["fichier"], meta["date"], meta["url"]))
        return conn

    def test_chaque_type_retenu_sort_meme_vide(self, magasin, tmp_path):
        export = export_guichets(self._base(tmp_path, g._index()), "99002")
        assert [t["type"] for t in export["types"]] == list(g.TYPES)
        assert all(t["guichets"] == [] for t in export["types"])

    def test_le_guichet_sur_place_passe_en_tete(self, magasin, tmp_path):
        export = export_guichets(self._base(tmp_path, g._index()), "99001")
        fs = next(t for t in export["types"] if t["type"] == "france_services")["guichets"]
        assert [x["nom"] for x in fs] == ["France services - Bourg-Fictif",
                                          "France services - Ailleurs"]
        assert fs[0]["sur_place"] and not fs[1]["sur_place"]
        caf = next(t for t in export["types"] if t["type"] == "caf")
        assert caf["guichets"] == []

    def test_l_export_cite_le_fichier_et_sa_date(self, magasin, tmp_path):
        export = export_guichets(self._base(tmp_path, g._index()), "99001")
        assert export["fichier"].endswith("data.gouv_local.json")
        assert export["date_fichier"] == "2026-10-19"
        assert export["source_url"].endswith(".tar.bz2")

    def test_une_commune_non_relevee_n_exporte_rien(self, magasin, tmp_path):
        assert export_guichets(self._base(tmp_path, g._index()), "99003") is None

    def test_le_site_d_un_domaine_generique_ne_sort_pas(self, magasin, tmp_path, monkeypatch):
        # Le site de la mairie EST le domaine générique de l'instance : publié
        # sous `site`, il faisait refuser tout l'aperçu par verify_snapshot.
        conn = self._base(tmp_path, g._index())

        def mairie():
            export = export_guichets(conn, "99001")
            [m] = next(t for t in export["types"] if t["type"] == "mairie")["guichets"]
            return m
        assert mairie()["site"] == "https://bourg-fictif.invalid"
        monkeypatch.setitem(RULES["urls"], "exclude_generic_domains", ["bourg-fictif.invalid"])
        m = mairie()
        assert m["site"] is None
        assert m["fiche"] == "https://annuaire.invalid/m-1"

    def test_la_mairie_sur_place_garde_le_site_de_la_commune(self, magasin, tmp_path,
                                                            monkeypatch):
        # Le site de la commune est générique pour l'instance, mais c'est celui
        # de SA mairie : là, et là seulement, il sort.
        from scripts.snapshot import territoire
        conn = self._base(tmp_path, g._index())
        monkeypatch.setattr(territoire, "URL_COMMUNE", "https://www.bourg-fictif.invalid")
        monkeypatch.setitem(RULES["urls"], "exclude_generic_domains", ["bourg-fictif.invalid"])
        export = export_guichets(conn, "99001")
        [m] = next(t for t in export["types"] if t["type"] == "mairie")["guichets"]
        assert m["site"] == "https://bourg-fictif.invalid"
