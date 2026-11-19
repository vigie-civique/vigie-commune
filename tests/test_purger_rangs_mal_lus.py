"""Le retrait ponctuel des deux fiches nées d'un rang mal lu (PR #196), sur une base réelle."""
from __future__ import annotations

import json

import pytest

# Le script importe pdfplumber au chargement : sauté dans le job `tests` (pytest seul).
pytest.importorskip("pdfplumber", reason="job « tests-deps » : pip install -r requirements.txt")

from scripts.purger_rangs_mal_lus import reperer  # noqa: E402

PIECE = "https://caussesaigoualcevennes.fr/wp-content/uploads/2025/11/Deliberations-du-29.10.2025.pdf"


def _acte(conn, numero: str, url: str = PIECE, date: str = "2025-10-29", **cols) -> int:
    champs = {"type": "deliberation_cc", "title": f"Acte {numero}", "date": date,
              "source_url": url, "metadata": json.dumps({"numero_acte": numero}), **cols}
    cur = conn.execute(f"INSERT INTO events({', '.join(champs)}) VALUES "
                       f"({', '.join('?' * len(champs))})", tuple(champs.values()))
    return cur.lastrowid


def test_le_doublon_est_retire_quand_l_acte_veritable_est_la(base):
    faux = _acte(base, "41")
    _acte(base, "141", url=PIECE.replace(".pdf", "-1.pdf"))
    _acte(base, "41", date="2025-03-04")            # le vrai n° 41 d'une autre séance
    a_retirer, retenues = reperer(base)
    assert [f[0] for f in a_retirer] == [faux] and retenues == []


def test_sans_l_acte_veritable_la_fiche_est_conservee(base):
    _acte(base, "41")
    a_retirer, retenues = reperer(base)
    assert a_retirer == [] and "141" in retenues[0][-1]


def test_une_fiche_annotee_est_conservee(base):
    faux = _acte(base, "41")
    _acte(base, "141")
    base.execute("INSERT INTO annotations(object_type, object_id, review_status) "
                 "VALUES('deliberation', ?, 'retenu')", (faux,))
    a_retirer, retenues = reperer(base)
    assert a_retirer == [] and retenues[0][0] == faux
