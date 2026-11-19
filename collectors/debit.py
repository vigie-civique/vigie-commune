"""Un débit borné par hôte, partagé par tous les processus qui collectent.

Les quotas des sources comptent par ADRESSE IP, pas par processus : sur le
VPS, les générations du portail, les collectes des ateliers et l'écrivain du
magasin sortent par la même adresse et se partagent les 30 requêtes par minute
de Melodi ou les 50 000 par jour de data.economie.gouv.fr. Le portail les
mettait en file par un verrou PAR STEP, tenu toute la durée du step : `marches`
gardait data.economie.gouv.fr 121 s, surtout pour parcourir le DECP sur le
disque, et plafonnait la machine vers 370 dossiers par jour (audit du 30/10, P6).

Ici, chaque REQUÊTE prend sa place au rythme publié de son hôte, et le travail
local ne retient plus personne. Le point de passage est la résolution de nom
(`reseau._resoudre`), par où passent urllib et requests : aucun collecteur
n'est modifié. Une connexion gardée ouverte par une `requests.Session` ne se
résout qu'une fois : elle compte pour une requête, quel que soit le nombre de
celles qu'elle porte.

Le calcul est celui d'un seau à jetons, tenu en une seule date (GCRA) :
`tat`, l'instant théorique où l'hôte sera de nouveau libre. Chaque requête
l'avance d'un intervalle ; elle attend ce qui dépasse la rafale permise.

L'état vit dans `VIGIE_DEBIT_DIR`, un fichier par hôte sous verrou
(`collectors/verrou.py`), commun aux comptes Unix qui le partagent (groupe
propre, dossier 2770). Ce dossier est un canal entre comptes : un compte
compromis peut y garder un verrou, y poser une date lointaine, un tube ou un
lien. Rien de cela ne doit arrêter les autres :
  * un verrou n'est attendu que `DELAI_ETAT_S` (ou `DELAI_SEUL_S`) par requête ;
  * une date au-delà de ce qu'une file réelle peut produire est remise à zéro ;
  * ce qui n'est pas un fichier ordinaire à lien unique, et toute erreur
    d'entrée-sortie (disque plein), font retomber sur l'état du processus ;
  * au-delà de `BUDGET_COMMUN_S` d'attente cumulée sur un hôte, le processus
    cesse de suivre l'état commun et garde sa borne propre, en le disant.
La gêne qu'un compte peut causer aux autres est donc bornée à
`BUDGET_COMMUN_S` par processus et par hôte — ce qu'il pourrait de toute
façon causer en épuisant le quota depuis la même adresse.
Les noms de fichier ne viennent que de `QUOTAS` (une table du code), jamais
d'une URL ; `seul()` refuse tout nom qui sortirait du dossier. Sans cette
variable, l'état reste dans le processus — ce qui suffit à une collecte seule,
pas au portail, qui la pose toujours. Un dossier déclaré mais inutilisable
retombe sur l'état du processus EN LE DISANT, une fois : mieux vaut une borne
locale qu'une collecte arrêtée.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from collectors import verrou

# hôte → (requêtes par seconde, rafale). Sous le quota publié, jamais au-dessus :
# les ateliers et le magasin passent par la même adresse. Relevé du 30/10/2026.
QUOTAS: dict[str, tuple[float, int]] = {
    # 30/min sans souscription (insee.fr/fr/information/8184173, 16/02/2026). Le
    # mainteneur de Melodi parle de 20/s (InseeFrLab/melodi#27) : la page
    # officielle fait foi tant qu'elle n'a pas changé.
    "api.insee.fr": (0.5, 5),
    # 7/s, « limite que l'administration s'autorise à baisser » (fiche data.gouv).
    "recherche-entreprises.api.gouv.fr": (5.0, 5),
    # 50 000 par jour en anonyme, remis à zéro à minuit UTC (`x-ratelimit-limit`
    # constaté le 30/10). Une rafale large : c'est un quota du JOUR, pas de la seconde.
    "data.economie.gouv.fr": (0.55, 600),
    # 600 par 60 s en anonyme (data.ademe.fr/pages/faq).
    "data.ademe.fr": (8.0, 20),
    # Aucun quota chiffré : « refus d'accès en cas d'usage abusif » (CGU 5.1.2).
    # Choix prudent, pas un chiffre publié.
    "hubeau.eaufrance.fr": (2.0, 4),
    # « Moins de 10 000 requêtes par jour », sans exécution parallèle (wiki OSM,
    # 24/10/2026) : une toutes les 9 s, et `seul()` dans osm.py.
    "overpass-api.de": (1 / 9, 1),
    # Découpage administratif : 10/s par IP (geo.api.gouv.fr/faq).
    "geo.api.gouv.fr": (8.0, 8),
    # Géoplateforme : WFS 30/s, géocodage 50/s par IP (limites d'usage IGN).
    "data.geopf.fr": (25.0, 25),
    # 50/s par IP, 429 et blocage de 5 s au-delà (doc de l'API Adresse).
    "api-adresse.data.gouv.fr": (40.0, 40),
    # 100/s (fiche de l'API tabulaire).
    "tabular-api.data.gouv.fr": (50.0, 50),
}
# Au-delà, l'état est tenu pour faux (fichier abîmé, horloge, écriture d'un
# autre compte) et remis à zéro : une date posée loin dans le futur ne doit pas
# bloquer les autres processus pour des heures. Une file RÉELLE ne s'en
# approche pas : chaque processus ne tient qu'une réservation à la fois, donc
# la file compte au plus autant de places que de processus qui collectent
# (une dizaine sur le VPS). `PROCESSUS_MAX` intervalles en laissent trois fois plus.
ATTENTE_MAX_S = 300
PROCESSUS_MAX = 32
# Au-delà de cette attente cumulée sur un hôte, ce processus ne suit plus
# l'état commun : il garde sa borne propre (cf. docstring).
BUDGET_COMMUN_S = 15 * 60
# L'état d'un hôte se tient quelques microsecondes : dix secondes d'attente
# disent un verrou gardé, pas une file.
DELAI_ETAT_S = 10
# L'exclusivité d'Overpass couvre une requête entière (200 s au plus, osm.py).
DELAI_SEUL_S = 600
# Une attente plus courte ne vaut pas une ligne de journal.
ATTENTE_SIGNALEE_S = 1.0
_NOM_SUR = re.compile(r"[a-z0-9.-]+")

_etat_local: dict[str, float] = {}
_verrou_local = threading.Lock()
_seuls_locaux: dict[str, threading.Lock] = {}
_attente_commune: dict[str, float] = {}
_repli_signale = False


def _dossier() -> Path | None:
    valeur = os.environ.get("VIGIE_DEBIT_DIR")
    return Path(valeur) if valeur else None


def _ouvrir(dossier: Path, nom: str) -> int:
    fd = os.open(dossier / nom, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o660)
    # Un tube posé à la place du fichier fait échouer le verrou ou la lecture ;
    # un lien dur ferait tronquer à un autre compte un fichier qui n'est pas
    # le sien. L'un et l'autre retombent sur l'état du processus.
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(fd)
        raise OSError(f"{nom} n'est pas un fichier ordinaire à lien unique")
    # Le fichier doit rester inscriptible par le groupe, quel que soit le umask
    # de celui qui l'a créé ; seul son propriétaire peut en changer le mode.
    if hasattr(os, "getuid"):
        try:
            if os.fstat(fd).st_uid == os.getuid():
                os.fchmod(fd, 0o660)
        except OSError:
            pass
    return fd


def _prendre_avant(fd: int, delai: float) -> bool:
    """Le verrou du fichier, ou faux au bout de `delai` secondes."""
    limite = time.monotonic() + delai
    while True:
        try:
            verrou.prendre(fd, attendre=False)
            return True
        except BlockingIOError:
            if time.monotonic() >= limite:
                return False
            time.sleep(0.05)


def _signaler_repli(erreur: OSError) -> None:
    global _repli_signale
    if not _repli_signale:
        _repli_signale = True
        print(f"  [debit] {_dossier()} inutilisable ({erreur}) : borne tenue dans ce "
              "seul processus, les autres ne la voient pas", file=sys.stderr, flush=True)


def _signaler_budget(hote: str) -> None:
    print(f"  [debit] {hote} : plus de {BUDGET_COMMUN_S // 60} min d'attente sur l'état "
          "commun, borne tenue dans ce seul processus désormais", file=sys.stderr, flush=True)


def _signaler_verrou_garde(nom: str, delai: float) -> None:
    print(f"  [debit] {nom} gardé ailleurs depuis plus de {delai:.0f} s : on passe "
          "sans lui", file=sys.stderr, flush=True)


def _reserver(hote: str, debit: float, rafale: int) -> float:
    """Avance la date de l'hôte d'un intervalle ; rend l'attente due, en secondes."""
    intervalle = 1 / debit

    plafond = rafale * intervalle + max(ATTENTE_MAX_S, PROCESSUS_MAX * intervalle)

    def avancer(tat: float | None, maintenant: float) -> tuple[float, float]:
        if tat is None or tat < maintenant or tat > maintenant + plafond:
            tat = maintenant
        tat += intervalle
        return tat, tat - maintenant - rafale * intervalle

    dossier = _dossier()
    if dossier is not None and _attente_commune.get(hote, 0.0) <= BUDGET_COMMUN_S:
        debut = time.monotonic()
        try:
            fd = _ouvrir(dossier, f"{hote}.tat")
        except OSError as erreur:
            _signaler_repli(erreur)
        else:
            try:
                if _prendre_avant(fd, DELAI_ETAT_S):
                    os.lseek(fd, 0, os.SEEK_SET)
                    try:
                        tat = float(os.read(fd, 64).decode() or "nan")
                    except ValueError:
                        tat = None
                    if tat != tat:   # NaN : fichier neuf
                        tat = None
                    tat, attente = avancer(tat, time.time())
                    os.ftruncate(fd, 0)
                    os.lseek(fd, 0, os.SEEK_SET)
                    os.write(fd, repr(tat).encode())
                    _compter_attente(hote, time.monotonic() - debut + max(0.0, attente))
                    return attente
                _signaler_verrou_garde(f"{hote}.tat", DELAI_ETAT_S)
                _compter_attente(hote, time.monotonic() - debut)
            except OSError as erreur:   # disque plein, entrée-sortie
                _signaler_repli(erreur)
            finally:
                os.close(fd)   # rend le verrou
    with _verrou_local:
        tat, attente = avancer(_etat_local.get(hote), time.time())
        _etat_local[hote] = tat
        return attente


def _compter_attente(hote: str, secondes: float) -> None:
    avant = _attente_commune.get(hote, 0.0)
    _attente_commune[hote] = avant + secondes
    if avant <= BUDGET_COMMUN_S < _attente_commune[hote]:
        _signaler_budget(hote)


def attendre(hote: str) -> float:
    """Attend la place de la prochaine requête vers `hote` ; rend l'attente."""
    hote = hote.lower().rstrip(".")
    quota = QUOTAS.get(hote)
    if quota is None:
        return 0.0
    attente = _reserver(hote, *quota)
    if attente <= 0:
        return 0.0
    if attente >= ATTENTE_SIGNALEE_S:
        # Le même événement que le portail écrivait pour son verrou : la ligne
        # tombe dans le journal de la demande, que `vigie_bilan_generation.py` lit.
        print(json.dumps({
            "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "event": "hote_en_attente", "hote": hote, "attenteS": round(attente, 1),
        }, ensure_ascii=False), flush=True)
    time.sleep(attente)
    return attente


class seul:
    """Une seule requête à la fois vers `hote`, entre tous les processus.

    Pour une source qui interdit le parallèle (Overpass) : le débit seul ne
    l'empêche pas quand une requête dure plus que l'intervalle.
    """

    def __init__(self, hote: str):
        if not _NOM_SUR.fullmatch(hote):
            raise ValueError(f"nom d'hôte inattendu : {hote!r}")
        self.hote = hote
        self.fd: int | None = None
        self.local: threading.Lock | None = None

    def __enter__(self):
        dossier = _dossier()
        if dossier is not None:
            try:
                self.fd = _ouvrir(dossier, f"{self.hote}.seul")
            except OSError as erreur:
                _signaler_repli(erreur)
            else:
                try:
                    if _prendre_avant(self.fd, DELAI_SEUL_S):
                        return self
                    # Mieux vaut une requête en parallèle qu'une collecte
                    # arrêtée par un verrou que personne ne rend.
                    _signaler_verrou_garde(f"{self.hote}.seul", DELAI_SEUL_S)
                except OSError as erreur:
                    _signaler_repli(erreur)
                os.close(self.fd)
                self.fd = None
        with _verrou_local:
            self.local = _seuls_locaux.setdefault(self.hote, threading.Lock())
        self.local.acquire()
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.local is not None:
            self.local.release()
            self.local = None
        return False
