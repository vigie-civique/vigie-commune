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
    with pytest.raises(RuntimeError, match="1 commune"):
        pop_culture.run(["99001", "99002"])
