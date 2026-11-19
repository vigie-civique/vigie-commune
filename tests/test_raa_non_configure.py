"""`raa` sans préfecture déclarée : « non relevé », jamais « interrogé, rien ».

Un dossier national n'a ni `prefecture_url` ni `prefecture_raa_path` : le step
rendait `empty`, et la couverture laissait lire qu'on avait cherché sans rien
trouver (relevé de Vallouise-Pelvoux, 02/11/2026).
"""
from test_profondeur import charger_config, instance  # noqa: F401  (fixture)


def test_sans_prefecture_declaree_raa_est_non_configure(instance):  # noqa: F811
    assert "raa" in charger_config(instance(prefecture_raa_path="")).SOURCES_NON_CONFIGUREES
    assert "raa" in charger_config(instance(prefecture_url="")).SOURCES_NON_CONFIGUREES
    assert charger_config(instance()).SOURCES_NON_CONFIGUREES == {}


def test_le_collecteur_n_interroge_rien(monkeypatch, capsys):
    from collectors import raa_prefecture
    monkeypatch.setattr(raa_prefecture, "SOURCES_NON_CONFIGUREES", {"raa": "aucune préfecture"})

    def interdit(*a, **k):
        raise AssertionError("aucune requête ni base attendue")
    monkeypatch.setattr(raa_prefecture, "get_conn", interdit)
    monkeypatch.setattr(raa_prefecture, "list_pdfs", interdit)
    raa_prefecture.run(2026)
    assert "[raa] non relevé : aucune préfecture" in capsys.readouterr().out


def test_la_couverture_dit_non_configure_a_cote_du_statut(base, monkeypatch):
    from scripts.snapshot import couverture
    monkeypatch.setattr(couverture, "SOURCES_NON_CONFIGUREES", {"raa": "aucune préfecture"})
    base.execute("INSERT INTO collector_runs (collector, started_at, finished_at, status,"
                 " items_added) VALUES ('raa', datetime('now'), datetime('now'), 'empty', 0)")
    sortie = couverture.export_couverture(base, [], {})
    assert sortie["collecteurs"]["raa"]["non_configure"] == "aucune préfecture"
    assert sortie["collecteurs"]["raa"]["statut"] == "empty"
    assert "non_configure" not in str({k: v for k, v in sortie["collecteurs"].items() if k != "raa"})
