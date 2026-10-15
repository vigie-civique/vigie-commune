from scripts.snapshot.actes import bodacc_personne_physique


def test_une_societe_se_publie():
    assert not bodacc_personne_physique("A.D.B. MENUISERIE", False)
    assert not bodacc_personne_physique("SOCIÉTÉ TARANDIN", False)


def test_nom_prenom_s_exclut():
    assert bodacc_personne_physique("GROS, Mathieu, Denis", False)
    assert bodacc_personne_physique("MG AUTO, DARIGNAC Claude", False)


def test_indice_du_contenu_s_exclut():
    assert bodacc_personne_physique("RENARD", True)
