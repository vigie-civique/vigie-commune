"""Patrimoine MH : une page HTML n'est pas une réponse, une commune non relevée
n'est pas une commune sans monument (22/10/2026)."""
import io

import pytest


class _Reponse(io.BytesIO):
    def __init__(self, corps: bytes, ct: str, url: str):
        super().__init__(corps)
        self.status, self._url = 200, url
        self.headers = type("H", (), {"get_content_type": lambda _s: ct})()

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_json_refuse_le_html_sans_l_archiver(monkeypatch):
    from collectors import archive
    archives = []
    monkeypatch.setattr(archive, "archive_fetch", lambda *a, **k: archives.append(a))
    monkeypatch.setattr(archive.urllib.request, "urlopen", lambda *a, **k: _Reponse(
        b"<!doctype html><title>portail</title>", "text/html", "https://portail.invalid/"))
    with pytest.raises(ValueError, match="non JSON.*redirigée"):
        archive.fetch_json("https://api.invalid/records", source="test")
    assert archives == []


def test_merimee_suit_les_pages(monkeypatch):
    from collectors import pop_culture
    pages = {
        "p1": {"data": [{"Reference": "PA1"}], "links": {"next": "p2"}},
        "p2": {"data": [{"Reference": "PA2"}], "links": {"next": None}},
    }
    monkeypatch.setattr(pop_culture, "REQUEST_DELAY", 0)
    monkeypatch.setattr(pop_culture, "fetch_json",
                        lambda url, **k: pages["p2" if url == "p2" else "p1"])
    assert [r["Reference"] for r in pop_culture._fetch("99001")] == ["PA1", "PA2"]


def test_coordonnees_lues_ou_ecartees():
    from collectors.pop_culture import _coords
    assert _coords("43.83,4.36") == (43.83, 4.36)
    assert _coords(None) == (None, None)
    assert _coords("n/a") == (None, None)


def test_une_commune_non_relevee_met_le_step_en_echec(monkeypatch):
    from collectors import pop_culture

    class _Conn:
        def commit(self): pass
        def close(self): pass

    def fetch(insee):
        if insee == "99002":
            raise ValueError("réponse non JSON")
        return []

    monkeypatch.setattr(pop_culture, "get_conn", lambda: _Conn())
    monkeypatch.setattr(pop_culture, "REQUEST_DELAY", 0)
    monkeypatch.setattr(pop_culture, "_fetch", fetch)
    with pytest.raises(RuntimeError, match="^relevés en échec : 99002 "):
        pop_culture.run(["99001", "99002"])


def test_l_echec_d_une_commune_se_lit_comme_le_portail_le_classe(monkeypatch, capsys):
    """Le service local du portail rejoue un échec passager seulement s'il lit
    la ligne `[step] … : échec → …` (motifs DETAIL et RESUME de server.py,
    recopiés ici). Avant le 01/11/2026, `: erreur —` n'y était pas lu, et un
    délai dépassé sur une commune devenait un défaut jamais rejoué."""
    import re
    import socket

    from collectors import pop_culture

    class _Conn:
        def commit(self): pass
        def close(self): pass

    def muet(insee):
        raise socket.timeout("The read operation timed out")

    monkeypatch.setattr(pop_culture, "get_conn", lambda: _Conn())
    monkeypatch.setattr(pop_culture, "REQUEST_DELAY", 0)
    monkeypatch.setattr(pop_culture, "_fetch", muet)
    with pytest.raises(RuntimeError) as e:
        pop_culture.run(["99001"])
    resume = re.match(r"relevés en échec : (.*)", str(e.value))
    assert resume
    details = re.findall(r"\[(\w+)\] (?:\d[\dAB]\d{3} )?(.+?) : échec → (.*)",
                         capsys.readouterr().out)
    assert len(details) == 1
    step, cle, motif = details[0]
    assert step == "patrimoine"
    # Le résumé, débarrassé de son INSEE comme le fait le portail, désigne la
    # même commune que le détail : sinon « motif non journalisé », donc défaut.
    assert re.sub(r"^\d[\dAB]\d{3} ", "", resume.group(1)) == cle
    assert "timed out" in motif


def _notice(ref, nom, protection="classé MH"):
    return {"Reference": ref, "Titre_editorial_de_la_notice": nom,
            "Typologie_de_la_protection": protection,
            "coordonnees_au_format_WGS84": "44.84,6.49"}


def _place(base, eid):
    import json
    ligne = base.execute("SELECT osm_category, osm_value, tags FROM places WHERE entity_id=?",
                         (eid,)).fetchone()
    return ligne["osm_category"], ligne["osm_value"], json.loads(ligne["tags"])


def test_un_monument_fusionne_avec_osm_garde_sa_protection(base, monkeypatch):
    """La Chapelle Saint-Sébastien de Vallouise (PA00080648) fusionnait avec sa
    fiche OSM et sortait « lieu de culte », sans protection (02/11/2026)."""
    import json
    from collectors import pop_culture
    monkeypatch.setattr(pop_culture, "_commune_de", lambda insee: "Vallouise-Pelvoux")
    cur = base.execute("INSERT INTO entities (type, name, commune, confidence)"
                       " VALUES ('place', 'Chapelle Saint-Sébastien', 'Vallouise-Pelvoux', 'verified')")
    eid = cur.lastrowid
    base.execute("INSERT INTO places (entity_id, osm_id, osm_category, osm_value, tags)"
                 " VALUES (?, 1, 'amenity', 'place_of_worship', ?)",
                 (eid, json.dumps({"amenity": "place_of_worship", "phone": "+33 1 99 00 13 00"})))
    pop_culture.import_mh(base, "05175", [_notice("PA00080648", "Chapelle Saint-Sébastien")])
    pop_culture.import_mh(base, "05175", [_notice("PA00080648", "Chapelle Saint-Sébastien")])
    cat, val, tags = _place(base, eid)
    assert (cat, val) == ("amenity", "place_of_worship")
    assert tags["phone"] == "+33 1 99 00 13 00"
    assert tags["monuments_historiques"] == [{"reference": "PA00080648", "protection": "classé MH"}]


def test_un_monument_seul_est_range_en_patrimoine(base, monkeypatch):
    from collectors import pop_culture
    monkeypatch.setattr(pop_culture, "_commune_de", lambda insee: "Vallouise-Pelvoux")
    pop_culture.import_mh(base, "05175", [_notice("PA1", "Église", "inscrit MH")])
    eid = base.execute("SELECT id FROM entities WHERE name='Église'").fetchone()[0]
    assert _place(base, eid) == ("patrimoine", "monument_historique",
                                 {"monuments_historiques": [{"reference": "PA1",
                                                             "protection": "inscrit MH"}]})


def test_le_snapshot_ne_sort_des_tags_que_la_protection():
    import json
    from scripts.snapshot.fiches import monuments_historiques
    nouvelle = json.dumps({"phone": "x", "email": "y",
                           "monuments_historiques": [{"reference": "PA2", "protection": None}]})
    assert monuments_historiques("place_of_worship", nouvelle) == [
        {"reference": "PA2", "protection": None,
         "notice": "https://www.pop.culture.gouv.fr/notice/merimee/PA2"}]
    # Forme d'avant le 02/11/2026, en base jusqu'au prochain passage du step.
    ancienne = json.dumps({"reference": "PA3", "protection": "classé MH"})
    assert monuments_historiques("monument_historique", ancienne)[0]["reference"] == "PA3"
    assert monuments_historiques("restaurant", json.dumps({"reference": "R1"})) == []
    assert monuments_historiques("place_of_worship", "pas du json") == []
    assert monuments_historiques(None, None) == []


def test_la_fiche_publiee_porte_la_protection_dans_l_ordre_du_pipeline(base, monkeypatch):
    """Collecte puis étape `fiches` du snapshot, sur la même base : la protection
    posée par le collecteur arrive dans la fiche publique, sans les autres tags."""
    import json
    from collections import Counter, defaultdict
    from collectors import pop_culture
    from scripts.snapshot import fiches
    monkeypatch.setattr(pop_culture, "_commune_de", lambda insee: "Vallouise-Pelvoux")
    monkeypatch.setattr(fiches, "load_confirmed_urls", lambda: {})
    cur = base.execute("INSERT INTO entities (type, name, commune, perimetre, confidence)"
                       " VALUES ('place', 'Chapelle Saint-Sébastien', 'Vallouise-Pelvoux', 'C1', 'verified')")
    base.execute("INSERT INTO places (entity_id, osm_id, osm_category, osm_value, tags)"
                 " VALUES (?, 1, 'amenity', 'place_of_worship', ?)",
                 (cur.lastrowid, json.dumps({"phone": "+33 1 99 00 13 00"})))
    pop_culture.import_mh(base, "05175", [_notice("PA00080648", "Chapelle Saint-Sébastien")])
    exclusions = defaultdict(Counter)
    sortie = fiches.etape_fiches(base, {}, set(), set(), exclusions, [], redige=lambda t: t)
    fiche = next(e for e in sortie["public_entities"] if e["name"] == "Chapelle Saint-Sébastien")
    assert fiche["monuments_historiques"][0]["reference"] == "PA00080648"
    assert "+33 1 99 00 13 00" not in json.dumps(fiche)
