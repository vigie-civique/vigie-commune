"""`/api/rag/ask` : l'erreur réseau du modèle ne part pas telle quelle au client
(tri CodeQL du 19/11/2026, alerte 16). Le détail reste dans le journal serveur."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="job « tests-deps » : pip install -r requirements.txt")

ADRESSE = "192.168.77.5"
PORT = "11999"


def test_l_erreur_du_modele_ne_montre_ni_adresse_ni_port(monkeypatch, capsys):
    import api

    monkeypatch.setattr(api, "RAG_ENABLED", True)
    monkeypatch.setattr(api, "RAG_ASK_ENABLED", True)
    monkeypatch.setattr(api, "rag_search", lambda q, limit, user: [])

    def en_echec(*a, **k):
        raise ConnectionError(
            f"HTTPConnectionPool(host='{ADRESSE}', port={PORT}): http://{ADRESSE}:{PORT}/api/chat refusé")
    monkeypatch.setattr(api._requests, "post", en_echec)

    sortie = api.rag_ask({"question": "budget"}, {"id": 1, "email": "v@fictiville.invalid"})

    assert sortie["answer"] is None
    assert sortie["error"] == "le modèle n'a pas répondu"
    for trace in ("http", PORT, ADRESSE):
        assert trace not in sortie["error"]
    # le détail n'est pas perdu : il part dans le journal serveur
    assert ADRESSE in capsys.readouterr().err
