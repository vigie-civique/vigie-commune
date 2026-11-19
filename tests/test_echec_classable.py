"""Un sous-relevé en échec se lit comme le service local du portail le classe.

Jusqu'au 01/11/2026, elections, fiscalite, sitadel, georisques et insee
écrivaient `: erreur —` et poursuivaient : le step finissait « OK », un relevé
perdu passait pour un relevé vide (même piège que patrimoine, #190 ; et que
`guichets`, #110). Les motifs DETAIL et RESUME sont recopiés de
`portail-local/local_service/server.py` : le portail rejoue un échec passager
seulement si chaque élément du résumé a sa ligne de détail.
"""
from __future__ import annotations

import re
import socket
import sqlite3

import pytest

from collectors import elections, fiscalite, georisques, insee_social, urbanisme_sitadel

DETAIL = re.compile(r"\[(\w+)\] (?:\d[\dAB]\d{3} )?(.+?) : échec → (.*)")
RESUME = re.compile(r"relevés en échec : (.*)")


def _muet(*_, **__):
    raise socket.timeout("The read operation timed out")


def _georisques(mp):
    mp.setattr(georisques, "fetch_paginated", _muet)
    mp.setattr(georisques, "REQUEST_DELAY", 0)
    return lambda: georisques.run(["99001"])


def _insee(mp):
    mp.setattr(insee_social, "fetch_dataset", _muet)
    mp.setattr(insee_social, "REQUEST_DELAY", 0)
    return lambda: insee_social.run(["99001", "99002"])


def _fiscalite(mp):
    mp.setattr(fiscalite, "fetch_dataset", _muet)
    mp.setattr(fiscalite, "releve_reperes", _muet)
    return lambda: fiscalite.run(["99001"])


def _sitadel(mp):
    mp.setattr(urbanisme_sitadel, "resolve_datafile", _muet)
    return lambda: urbanisme_sitadel.run(["99001"])


def _elections(mp):
    mp.setattr(elections, "fetch_rows", _muet)
    return lambda: elections.run(insee_list=["99001"])


@pytest.mark.parametrize("step,module,preparer", [
    ("georisques", georisques, _georisques),
    ("insee", insee_social, _insee),
    ("fiscalite", fiscalite, _fiscalite),
    ("sitadel", urbanisme_sitadel, _sitadel),
    ("elections", elections, _elections),
])
def test_un_sous_releve_perdu_remonte_au_format_du_portail(step, module, preparer, base,
                                                            tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(module, "get_conn", lambda **_: sqlite3.connect(tmp_path / "test.db"))
    lancer = preparer(monkeypatch)
    with pytest.raises(RuntimeError) as e:
        lancer()
    resume = RESUME.match(str(e.value))
    assert resume, str(e.value)
    details = DETAIL.findall(capsys.readouterr().out)
    assert details and {d[0] for d in details} == {step}
    assert all("timed out" in d[2] for d in details)
    # Chaque élément du résumé, débarrassé de son INSEE comme le fait le
    # portail, a son détail : sinon « motif non journalisé », donc défaut.
    cles = {d[1] for d in details}
    for sous in resume.group(1).split(", "):
        assert re.sub(r"^\d[\dAB]\d{3} ", "", sous.strip()) in cles
