"""Forêt, feux et débroussaillement : lectures et publication, sans appel réseau.

La page de la BDIFF reproduite a la forme rendue le 01/10/2026.

Ce que ces essais protègent :
  1. la BDIFF garde ses critères dans la session : une page d'une AUTRE commune
     veut dire que le filtre est perdu, et le relevé s'arrête au lieu d'écrire
     les feux de la France entière ;
  2. une adresse dans le trou d'un polygone n'est pas dans le zonage ;
  3. « aucun feu », « aucune forêt publique » ne se publient que si le relevé a
     abouti : sans ligne de suivi, rien n'est dit.
"""
from __future__ import annotations

import json
import ssl
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

# Le collecteur importe certifi au chargement : sans lui (job `tests`, pytest seul),
# le module est sauté ici, et joué par `tests-deps`.
pytest.importorskip("certifi", reason="job « tests-deps » : pip install -r requirements.txt")

from collectors import incendie  # noqa: E402
from collectors.incendie import (adresses_dans_le_zonage, ensure_tables,  # noqa: E402
                                 forets_de_la_commune, lire_page_bdiff,
                                 points_de_la_commune)


def _page(*lignes):
    """Le tableau des incendies, tel que la BDIFF le rend : le numéro de fiche
    dans un libellé masqué, le code INSEE et le détail des surfaces aussi."""
    corps = "".join(f"""
        <tr><td><input type="checkbox" aria-label="Sélectionner l'incendie {fiche}"></td>
          <td><a href="#" class="eye" title="Aperçu de la fiche {fiche}">
                <span class="hide">Aperçu de la fiche {fiche}"</span></a></td>
          <td> {alerte[6:10]} </td><td><span> {alerte} </span></td>
          <td><span title="GARD"> 30 </span></td>
          <td><span> {commune} </span><div class="hidden"><div><strong>INSEE :</strong> {insee} </div></div></td>
          <td class="right"><span> {surface} </span>{foret}</td>
          <td> {cause} </td><td> - </td></tr>"""
                    for fiche, alerte, commune, insee, surface, foret, cause in lignes)
    return f"<table><tr><td></td><th>Année</th></tr>{corps}</table>"


FORET = '<div class="hidden"><div><strong>Forêt :</strong> 0.1410 ha </div></div>'


def test_une_ligne_de_la_bdiff_est_lue():
    [feu] = lire_page_bdiff(_page(("2016-8831", "10/08/2016 15:35", "Lasalle", "99001",
                                   "0.3000", FORET, "Involontaire (particulier)")), "99001")
    assert feu == {"cle": "2016-8831", "alerte": "2016-08-10 15:35", "surface_ha": 0.3,
                   "foret_ha": 0.141, "cause": "Involontaire (particulier)"}


def test_une_cause_vide_n_est_pas_une_cause():
    [feu] = lire_page_bdiff(_page(("2015-6340", "15/07/2015 10:10", "Lasalle", "99001",
                                   "0.0057", "", "-")), "99001")
    assert feu["cause"] is None and feu["foret_ha"] is None


def test_une_page_d_une_autre_commune_arrete_le_releve():
    with pytest.raises(RuntimeError, match="filtre perdu"):
        lire_page_bdiff(_page(("2020-1", "01/08/2020 12:00", "Ailleurs", "01001", "5.0000",
                               "", "-")), "99001")
    with pytest.raises(RuntimeError, match="tableau"):
        lire_page_bdiff("<html>maintenance</html>", "99001")


CARRE = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
TROU = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]


def test_une_adresse_dans_un_trou_n_est_pas_dans_le_zonage():
    zones = [{"geometry": {"type": "Polygon", "coordinates": [CARRE, TROU]}, "properties": {}},
             {"geometry": {"type": "Polygon", "coordinates": [
                 [[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]]]}, "properties": {}}]
    dans, touchees = adresses_dans_le_zonage([(1, 1), (5, 5), (50, 50)], zones)
    assert (dans, touchees) == (1, {0}), "la zone voisine ne contient aucune adresse"


def test_les_adresses_sont_celles_de_la_commune():
    import gzip
    csv = b"code_insee;lon;lat\n99001;3.85;44.04\n99002;3.9;44.1\n99001;;\n"
    assert points_de_la_commune(gzip.compress(csv), "99001") == [(3.85, 44.04)]


def test_la_ban_se_lit_en_flux(monkeypatch):
    """Jamais le département entier en mémoire : 1,5 Gio pour le Nord (27/10/2026)."""
    import gzip

    def interdit(*_, **__):
        raise AssertionError("gzip.decompress charge tout le département d'un bloc")
    brut = gzip.compress(b"code_insee;lon;lat\n99001;3.85;44.04\n")
    monkeypatch.setattr(incendie.gzip, "decompress", interdit)
    assert points_de_la_commune(brut, "99001") == [(3.85, 44.04)]


def _ban_en_panne(monkeypatch, tmp_path):
    """Une BAN qui rend 503, comme du 21 au 27/10/2026, et un magasin vide."""
    import urllib.error

    def faux_lire(url, **_):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
    monkeypatch.setattr(incendie, "_lire", faux_lire)
    monkeypatch.setattr(incendie, "CACHE", tmp_path)
    return tmp_path / "adresses-99.csv.gz"


def test_ban_injoignable_reprend_la_copie_perimee(monkeypatch, tmp_path, capsys):
    import gzip
    import os
    copie = _ban_en_panne(monkeypatch, tmp_path)
    copie.write_bytes(gzip.compress(b"code_insee;lon;lat\n99001;3.85;44.04\n"))
    vieux = (datetime.now() - timedelta(days=incendie.BAN_JOURS + 30)).timestamp()
    os.utime(copie, (vieux, vieux))

    assert incendie.adresses_ban("99001") == [(3.85, 44.04)]
    sortie = capsys.readouterr().out
    assert "PÉRIMÉ" in sortie and "503" in sortie, "la reprise se dit dans le journal"


def test_une_copie_de_plus_de_deux_trimestres_ne_se_publie_pas(monkeypatch, tmp_path):
    import gzip
    import os
    copie = _ban_en_panne(monkeypatch, tmp_path)
    copie.write_bytes(gzip.compress(b"code_insee;lon;lat\n99001;3.85;44.04\n"))
    vieux = (datetime.now() - timedelta(days=2 * incendie.BAN_JOURS + 1)).timestamp()
    os.utime(copie, (vieux, vieux))
    with pytest.raises(OSError, match="503"):
        incendie.adresses_ban("99001")


def test_un_lecteur_qui_ne_peut_ecrire_lit_la_copie_bornee_sans_appeler_la_ban(monkeypatch, tmp_path):
    import gzip
    import os
    copie = _ban_en_panne(monkeypatch, tmp_path)
    copie.write_bytes(gzip.compress(b"code_insee;lon;lat\n99001;3.85;44.04\n"))
    vieux = (datetime.now() - timedelta(days=incendie.BAN_JOURS + 1)).timestamp()
    os.utime(copie, (vieux, vieux))
    monkeypatch.setattr(incendie, "magasin_ecrivable", lambda *a: False)
    monkeypatch.setattr(incendie, "_lire", lambda *a, **k: (_ for _ in ()).throw(AssertionError("BAN appelée")))
    assert incendie.adresses_ban("99001") == [(3.85, 44.04)]


def test_ban_injoignable_sans_copie_echoue(monkeypatch, tmp_path):
    """Propriété, pas régression : passait déjà avant la reprise. Sans copie,
    l'échec reste bruyant, jamais « 0 adresse »."""
    _ban_en_panne(monkeypatch, tmp_path)
    with pytest.raises(OSError, match="503"):
        incendie.adresses_ban("99001")


def test_la_foret_du_voisin_n_est_pas_nommee():
    limite = {"type": "Polygon", "coordinates": [CARRE]}
    foret = lambda nom, x: {"properties": {"toponyme": nom, "nature": "Forêt domaniale"},
                            "geometry": {"type": "Polygon", "coordinates": [
                                [[x, 1], [x + 2, 1], [x + 2, 3], [x, 3], [x, 1]]]}}
    assert forets_de_la_commune([foret("Ici", 1), foret("À côté", 40)], limite) == [
        ("Ici", "Forêt domaniale")]


# ── La publication (`export_incendie`) ───────────────────────────────────────

def test_sans_releve_rien_n_est_dit(base):
    from scripts.build_public_snapshot import export_incendie
    assert export_incendie(base, "99001") is None
    ensure_tables(base)
    assert export_incendie(base, "99001") is None, "tables vides : aucun relevé n'a abouti"


def test_un_zero_ne_sort_que_d_un_releve_abouti(base):
    from scripts.build_public_snapshot import export_incendie
    ensure_tables(base)
    base.execute("INSERT INTO incendie_suivi (insee, releve, debut, fin)"
                 " VALUES ('99001', 'feux', 2006, 2025)")
    base.commit()

    i = export_incendie(base, "99001")

    assert i["feux"] == {"debut": 2006, "fin": 2025, "nombre": 0, "surface_ha": 0,
                         "plus_grand": None, "liste": []}
    # Les forêts publiques n'ont pas été relevées : None, pas une liste vide.
    assert i["forets_publiques"] is None and i["boisement"] is None
    assert i["debroussaillement"] is None


def test_les_feux_publies_sont_ceux_de_la_commune(base):
    from scripts.build_public_snapshot import export_incendie
    ensure_tables(base)
    base.executemany("INSERT INTO feux VALUES (?,?,?,?,?,?)", [
        ("99001", "2016-1", "2016-08-10 15:35", 0.3, 0.141, None),
        ("99001", "2015-1", "2015-04-14 13:31", 0.2, 0.094, "Involontaire (travaux)"),
        ("99002", "2019-1", "2019-07-01 12:00", 50.0, 50.0, None)])
    base.executemany("INSERT INTO incendie_suivi (insee, releve, debut, fin) VALUES (?,?,?,?)", [
        ("99001", "feux", 2006, 2025), ("99001", "forets_publiques", None, None)])
    base.commit()

    i = export_incendie(base, "99001")

    assert (i["feux"]["nombre"], i["feux"]["surface_ha"]) == (2, 0.5)
    assert i["feux"]["plus_grand"]["alerte"] == "2016-08-10 15:35"
    assert i["forets_publiques"] == [], "relevé abouti, aucune forêt publique : un zéro mesuré"


def test_curl_recoit_l_intermediaire_que_la_bdiff_ne_presente_pas(tmp_path):
    """Sans lui, le curl d'un Debian sort en code 60 : le relevé « feux » échouait
    sur le VPS et passait sur le Mac (26/10/2026)."""
    # certifi est une dépendance du collecteur, pas de la suite : le job
    # `tests` de la CI n'installe que pytest, `tests-deps` joue ces essais.
    pytest.importorskip("certifi")
    options = incendie._racines_bdiff(str(tmp_path))
    assert options[0] == "--cacert"
    paquet = (tmp_path / "racines-bdiff.pem").read_bytes()
    assert incendie.INTERMEDIAIRE_BDIFF.read_bytes().strip() in paquet
    # Le paquet se charge, et garde les racines de certifi à côté de l'intermédiaire.
    contexte = ssl.create_default_context(cafile=options[1])
    assert len(contexte.get_ca_certs()) > 100


def test_l_intermediaire_de_la_bdiff_ne_perime_pas_sous_peu():
    x509 = pytest.importorskip("cryptography.x509")
    cert = x509.load_pem_x509_certificate(incendie.INTERMEDIAIRE_BDIFF.read_bytes())
    assert "GEANT TLS RSA 1" in cert.subject.rfc4514_string()
    fin = cert.not_valid_after_utc
    assert fin > datetime.now(timezone.utc) + timedelta(days=365)


def test_feux_bdiff_passe_les_racines_a_curl(monkeypatch):
    pytest.importorskip("certifi")
    appels = []

    def faux_curl(cmd, **_):
        appels.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=_page().encode())
    monkeypatch.setattr(incendie.subprocess, "run", faux_curl)
    incendie.feux_bdiff("12082", 2006, 2025)
    assert appels and "--cacert" in appels[0]


def test_le_fichier_d_outre_mer_porte_trois_chiffres():
    assert [incendie.departement(c) for c in ("30140", "2A004", "97411")] == ["30", "2A", "974"]


def _ban_france(tmp_path, lignes):
    import gzip
    (tmp_path / "source").mkdir(exist_ok=True)
    france = tmp_path / "source" / "adresses-france.csv.gz"
    france.write_bytes(gzip.compress(("id;nom_voie;code_insee;lon;lat\n" + "".join(lignes)).encode()))
    return france


def test_la_ban_france_se_decoupe_par_departement(tmp_path):
    import gzip
    france = _ban_france(tmp_path, ['a;"rue ;du Pont";30140;3.85;44.04\n', "b;r;97411;55.4;-21.1\n",
                                    "c;r;2A004;8.7;41.9\n", "d;r;30141;3.9;44.0\n", "e;r;;0;0\n"])
    sortie = tmp_path / "sortie"
    sortie.mkdir()
    decoupes = incendie.decouper_france(france, sortie)
    assert {dep: n for dep, (_, n) in decoupes.items()} == {"30": 2, "974": 1, "2A": 1}
    gard = gzip.decompress(decoupes["30"][0].read_bytes())
    assert points_de_la_commune(gzip.compress(gard), "30140") == [(3.85, 44.04)]
    assert b'"rue ;du Pont"' in gard, "un champ qui porte le séparateur reste entier"


def test_une_ban_france_tronquee_ne_remplace_rien(monkeypatch, tmp_path):
    monkeypatch.setattr(incendie, "CACHE", tmp_path)
    monkeypatch.setattr(incendie, "TEMOIN_FRANCE", tmp_path / "france.json")
    ancien = tmp_path / "adresses-30.csv.gz"
    ancien.write_bytes(b"ancien")

    def telecharger(cible):
        cible.write_bytes(_ban_france(tmp_path, ["a;r;30140;3.85;44.04\n"]).read_bytes())
    monkeypatch.setattr(incendie, "_telecharger_france", telecharger)
    monkeypatch.setattr(incendie, "_exiger_disque", lambda *a: None)
    with pytest.raises(RuntimeError, match="1 départements"):
        incendie.remplir_magasin()
    assert ancien.read_bytes() == b"ancien"
    assert not (tmp_path / "france.json").exists()
    assert not (tmp_path / ".adresses-france.part").exists()


def test_une_ban_france_fraiche_ne_se_retelecharge_pas(monkeypatch, tmp_path):
    monkeypatch.setattr(incendie, "TEMOIN_FRANCE", tmp_path / "france.json")
    (tmp_path / "france.json").write_text("{}")
    monkeypatch.setattr(incendie, "_telecharger_france",
                        lambda c: (_ for _ in ()).throw(AssertionError("téléchargée")))
    assert incendie.remplir_magasin() == []


def test_la_ban_france_remplace_chaque_departement(monkeypatch, tmp_path):
    monkeypatch.setattr(incendie, "CACHE", tmp_path)
    monkeypatch.setattr(incendie, "TEMOIN_FRANCE", tmp_path / "france.json")
    monkeypatch.setattr(incendie, "DEPARTEMENTS_MIN", 2)

    def telecharger(cible):
        cible.write_bytes(_ban_france(tmp_path, ["a;r;30140;3.85;44.04\n", "b;r;26289;5.0;44.7\n"]).read_bytes())
    monkeypatch.setattr(incendie, "_telecharger_france", telecharger)
    monkeypatch.setattr(incendie, "_exiger_disque", lambda *a: None)
    assert incendie.remplir_magasin() == []
    assert incendie.adresses_ban("30140") == [(3.85, 44.04)]
    assert incendie.adresses_ban("26289") == [(5.0, 44.7)]
    assert json.loads((tmp_path / "france.json").read_text())["adresses"] == {"26": 1, "30": 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "adresses-26.csv.gz", "adresses-30.csv.gz", "france.json", "source"]


def test_un_code_insee_hostile_ne_nomme_aucun_fichier(tmp_path):
    france = _ban_france(tmp_path, ["a;r;30140;3.85;44.04\n", "b;r;../x1;0;0\n", "c;r;9/001;0;0\n",
                                    "d;r;zz001;0;0\n", "e;r;30140\n;0;0\n", "f;r;2C004;0;0\n"])
    sortie = tmp_path / "sortie"
    sortie.mkdir()
    decoupes = incendie.decouper_france(france, sortie)
    assert list(decoupes) == ["30"]
    assert sorted(p.name for p in sortie.iterdir()) == ["adresses-30.csv.gz"]


def test_trop_de_codes_invalides_arretent_la_decoupe(monkeypatch, tmp_path):
    monkeypatch.setattr(incendie, "REJETS_MAX", 2)
    france = _ban_france(tmp_path, ["a;r;xx;0;0\n"] * 3)
    sortie = tmp_path / "sortie"
    sortie.mkdir()
    with pytest.raises(RuntimeError, match="sans code INSEE valide"):
        incendie.decouper_france(france, sortie)


def test_sans_place_sur_le_disque_rien_ne_se_telecharge(monkeypatch, tmp_path):
    import collections
    monkeypatch.setattr(incendie, "CACHE", tmp_path)
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(incendie.shutil, "disk_usage", lambda _: Usage(0, 0, 4 << 30))
    with pytest.raises(OSError, match="nécessaires"):
        incendie._exiger_disque(2 << 30, "avant le téléchargement")
    incendie._exiger_disque(100 << 20, "avant la découpe")


def test_un_departement_refuse_garde_l_ancien_et_reste_signale(monkeypatch, tmp_path):
    monkeypatch.setattr(incendie, "CACHE", tmp_path)
    monkeypatch.setattr(incendie, "TEMOIN_FRANCE", tmp_path / "france.json")
    monkeypatch.setattr(incendie, "DEPARTEMENTS_MIN", 2)
    monkeypatch.setattr(incendie, "_exiger_disque", lambda *a: None)
    ancien = tmp_path / "adresses-30.csv.gz"
    ancien.write_bytes(b"ancien illisible")

    def telecharger(cible):
        cible.write_bytes(_ban_france(tmp_path, ["a;r;30140;3.85;44.04\n", "b;r;26289;5.0;44.7\n"]).read_bytes())
    monkeypatch.setattr(incendie, "_telecharger_france", telecharger)
    vrai = incendie.copier_atomiquement

    def copier(chemin, flux):
        if chemin.name == "adresses-30.csv.gz":
            from collectors.national_store import ContenuInvalide
            raise ContenuInvalide("fondu")
        return vrai(chemin, flux)
    monkeypatch.setattr(incendie, "copier_atomiquement", copier)
    assert incendie.remplir_magasin() == ["30"]
    assert ancien.read_bytes() == b"ancien illisible"
    assert incendie.adresses_ban("26289") == [(5.0, 44.7)]
    monkeypatch.setattr(incendie, "_telecharger_france",
                        lambda c: (_ for _ in ()).throw(AssertionError("retéléchargée")))
    assert incendie.remplir_magasin() == ["30"], "signalé à chaque passe, sans 2 Go de plus"
