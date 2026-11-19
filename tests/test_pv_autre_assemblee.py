"""Un procès-verbal de la CC ne devient pas la pièce d'une séance communale.

Le 29/08/2026, un redécoupage a relu les procès-verbaux de l'intercommunalité en
portée communale (`redecouper_pv.deja_en_cache`, corrigé par `674f7d8`). Les
833 actes en double ont été retirés en septembre ; les séances du conseil
municipal nées du même passage, non. Relevé sur les sites servis le 07/11/2026 :
43 séances communales de Lasalle et 44 de Brassac avaient pour pièce le PV du
conseil communautaire tenu le même jour, dont 32 et 44 séances qui n'ont jamais
eu lieu.

L'identité d'une séance (assemblée, date) était juste : c'est la portée donnée
au document qui était fausse, et rien ne la confrontait à la base. Deux séances
le même jour, une par assemblée, restent deux séances ; mais un document que
l'une tient déjà n'est pas une pièce de l'autre.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pdfplumber",
                    reason="job « tests-deps » : pip install -r requirements.txt")

from collectors import conseils  # noqa: E402
from collectors.connecteurs.base import DocumentPublie  # noqa: E402

DATE = "2021-12-08"
PV_CC = "https://www.exemple-epci.fr/wp-content/uploads/2022/09/PV-du-08.12.2021.pdf"
CR_CM = "https://www.exemple-commune.fr/CR/conseil-municipal-du-8-decembre-2021"
TEXTE = ("Séance du 8 décembre 2021. Le conseil, après en avoir délibéré, "
         "approuve la convention. " * 20)


def _doc(url, libelle):
    return DocumentPublie(date=DATE, url=url, libelle=libelle, source="exemple")


@pytest.fixture
def lecture(monkeypatch):
    monkeypatch.setattr(conseils, "lire_document", lambda doc, *a, **k: (TEXTE, "pdf", False))


def _seances(base):
    return dict(base.execute(
        "SELECT type, source_url FROM events"
        " WHERE type IN ('conseil_municipal', 'conseil_communautaire')").fetchall())


@pytest.mark.parametrize("tenu_par", ["source", "piece"])
def test_le_pv_de_la_cc_relu_en_portee_communale_est_ecarte(base, lecture, tenu_par):
    if tenu_par == "source":
        conseils.traiter(base, _doc(PV_CC, "PV du 08.12.2021.pdf"), "epci", verbose=False)
    else:
        # La séance de la CC tient le PV comme SECONDE pièce, sa source est le recueil.
        conseils.enregistrer_seance(base, _doc(PV_CC.replace("PV-du", "Deliberations"),
                                               "Deliberations 08 decembre 2021.pdf"), "epci")
        conseils.enregistrer_seance(base, _doc(PV_CC, "PV du 08.12.2021.pdf"), "epci",
                                    annonce=True)
    n_actes = base.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    r = conseils.traiter(base, _doc(PV_CC, "PV-du-08.12.2021.pdf"), "commune", verbose=False)

    assert r["statut"] == "autre_assemblee"
    assert "conseil_municipal" not in _seances(base), (
        "le PV de la CC a fait naître une séance du conseil municipal")
    assert base.execute("SELECT COUNT(*) FROM events").fetchone()[0] == n_actes


def test_deux_seances_le_meme_jour_restent_deux_seances(base, lecture):
    conseils.traiter(base, _doc(PV_CC, "PV du 08.12.2021.pdf"), "epci", verbose=False)
    r = conseils.traiter(base, _doc(CR_CM, "conseil municipal du 8 decembre 2021"),
                         "commune", verbose=False)
    assert r["statut"] == "ok"
    assert _seances(base) == {"conseil_communautaire": PV_CC, "conseil_municipal": CR_CM}
