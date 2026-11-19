"""Primitives du magasin partagé pour les gros jeux nationaux.

Le magasin peut être lu simultanément par plusieurs instances. Une écriture se
fait donc dans un fichier voisin temporaire, puis par remplacement atomique :
un lecteur ne voit jamais un téléchargement partiel. Deux téléchargements
concurrents du même fichier restent possibles, mais leur résultat complet est
identique et aucun verrou orphelin ne peut bloquer les collectes suivantes.

Le magasin est un CACHE, jamais une source. Il peut être monté en lecture seule
— partagé par plusieurs instances dont une seule l'alimente, posé sur un disque
externe débranché, appartenant à un autre utilisateur. Ne pas pouvoir y écrire
n'est donc PAS une erreur de collecte : l'appelant vient d'obtenir son contenu,
il le garde, il ne le partage simplement pas.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import sqlite3
import stat
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath


# Un code INSEE et un département, tels qu'ils nomment les fichiers du magasin.
# `fullmatch` partout : `$` laisserait passer un saut de ligne final.
CODE_INSEE = re.compile(r"\d{5}|2[AB]\d{3}")
DEPARTEMENT = re.compile(r"\d{2}|2[AB]|97\d")


def departement(insee: str) -> str:
    """`30140` → `30`, `2A004` → `2A`, `97411` → `974` : le nom des fichiers
    départementaux (BAN, DVF). `insee[:2]` cherchait `adresses-97` outre-mer."""
    return insee[:3] if insee.startswith("97") else insee[:2]


# Un step écrit des dizaines de fichiers : un magasin refusé le serait autant
# de fois. On n'avertit qu'une fois par dossier.
_refus_signales: set[str] = set()

# Les fichiers périmés lus faute de mieux pendant ce processus (un step) :
# `run_all.run_step` les imprime dans le rapport du step, puis vide la liste.
perimes_lus: list[str] = []

# Un fichier qui remplace un fichier de plus d'1 Mo doit en garder au moins la
# moitié : un consolidé tronqué mais bien formé passe tous les autres contrôles.
RETRECISSEMENT_SEUIL = 1 << 20
RETRECISSEMENT_MIN = 0.5


def ecrivain() -> bool:
    """Ce processus est-il l'écrivain du magasin (`scripts/rafraichir_magasin.py`) ?

    Un lecteur se passe du magasin quand il le refuse ; l'écrivain, non. Pour
    lui, un refus d'écriture ou un contenu refusé fait échouer le step, et le
    repli sur un fichier périmé ne s'applique pas : sinon la passe sortait en 0
    sans avoir rien rafraîchi, et le magasin vieillissait sans que rien le dise.
    """
    return os.environ.get("VIGIE_ECRIVAIN_MAGASIN") == "1"


def _retrecissement_accepte(path: Path) -> bool:
    """Une republication plus petite, acceptée à la main pour CE fichier.

    `rafraichir_magasin.py --accepter-retrecissement decp/decp-2024-09.json` :
    un dédoublonnage par la source est légitime, et la règle de la fonte n'a
    sinon aucune sortie. Le chemin est relatif au magasin, sans joker.
    """
    acceptes = os.environ.get("VIGIE_ACCEPTER_RETRECISSEMENT", "")
    return f"{path.parent.name}/{path.name}" in {a.strip() for a in acceptes.split(",") if a.strip()}


def est_frais(path: Path, jours: int | None) -> bool:
    """Un fichier non vide est frais ; ``None`` signifie immuable."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if jours is None:
        return True
    return (time.time() - path.stat().st_mtime) / 86400 < jours


def perime_lisible(path: Path, jours: int | None, facteur: int = 2) -> bool:
    """Un fichier périmé de moins de ``facteur`` fois sa validité se lit encore.

    La règle unique du repli (instruction sécurité du 30/10/2026, Q4). Sur le
    serveur, un seul compte écrit le magasin : quand son passage a du retard,
    un lecteur lit l'ancien fichier plutôt que de retélécharger 5 Go par
    dossier. Il le DIT : le fichier entre dans `perimes_lus`, que le rapport du
    step imprime. Au-delà, l'appelant retélécharge ; s'il échoue, le step
    échoue. ``facteur=1`` refuse tout fichier périmé (données « du jour »).
    """
    if ecrivain() or jours is None or not path.is_file() or path.stat().st_size == 0:
        return False
    age = (time.time() - path.stat().st_mtime) / 86400
    if age >= facteur * jours:
        return False
    perimes_lus.append(f"{path.parent.name}/{path.name} ({age:.1f} j, validité {jours} j)")
    print(f"  [magasin] PÉRIMÉ, lu quand même : {path.name} "
          f"({age:.1f} j pour {jours} j de validité)")
    return True


class ContenuInvalide(ValueError):
    """Le fichier reçu ne se lit pas jusqu'au bout, ou a fondu.

    Contrôlé AVANT le remplacement : une fois dans le magasin, un fichier
    corrompu resterait « frais » pour tous les lecteurs, qui ne peuvent plus
    l'en retirer quand le magasin leur est en lecture seule.
    """


def valider_contenu(temporaire: Path, path: Path) -> None:
    """Lève ``ContenuInvalide`` si ``temporaire`` ne peut pas remplacer ``path``.

    Le format se lit au nom du fichier final : ``.json`` se parse jusqu'au
    bout (en flux quand ijson est là, un consolidé DECP pèse 950 Mo), ``.gz``
    se décompresse entièrement, ``.zip`` passe `testzip` et ne nomme aucun
    membre absolu ni remontant. Puis le fichier ne doit pas avoir fondu.
    """
    nom = path.name.lower()
    try:
        if nom.endswith(".json"):
            try:
                import ijson
            except ImportError:
                with temporaire.open("rb") as flux:
                    json.load(flux)
            else:
                with temporaire.open("rb") as flux:
                    for _ in ijson.parse(flux):
                        pass
        elif nom.endswith(".gz"):
            with gzip.open(temporaire, "rb") as flux:
                while flux.read(1 << 20):
                    pass
        elif nom.endswith(".sqlite"):
            base = sqlite3.connect(f"file:{temporaire}?mode=ro", uri=True)
            try:
                verdict = base.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                base.close()
            if verdict != "ok":
                raise ContenuInvalide(f"{path.name} : base abîmée ({verdict[:80]})")
        elif nom.endswith(".zip"):
            with zipfile.ZipFile(temporaire) as archive:
                for membre in archive.namelist():
                    chemin = PurePosixPath(membre.replace("\\", "/"))
                    if (chemin.is_absolute() or ".." in chemin.parts
                            or re.match(r"^[A-Za-z]:", membre)):
                        raise ContenuInvalide(f"{path.name} : membre refusé {membre[:80]}")
                mauvais = archive.testzip()
                if mauvais is not None:
                    raise ContenuInvalide(f"{path.name} : membre corrompu {mauvais[:80]}")
    except ContenuInvalide:
        raise
    except Exception as e:
        raise ContenuInvalide(f"{path.name} illisible : {type(e).__name__}: {e}") from e
    try:
        avant = path.stat().st_size
    except OSError:
        return
    apres = temporaire.stat().st_size
    if avant > RETRECISSEMENT_SEUIL and apres < avant * RETRECISSEMENT_MIN:
        if _retrecissement_accepte(path):
            print(f"  [magasin] rétrécissement ACCEPTÉ à la main : {path.name} "
                  f"({avant} → {apres} octets)")
            return
        raise ContenuInvalide(
            f"{path.name} : {apres} octets pour {avant} au fichier qu'il remplacerait")


def _signaler_refus(path: Path, erreur: OSError) -> None:
    cle = str(path.parent)
    if cle in _refus_signales:
        return
    _refus_signales.add(cle)
    print(f"  [magasin] écriture refusée dans {cle} ({type(erreur).__name__}) — "
          f"la collecte continue, le fichier n'est pas partagé")


def ecrire_atomiquement(path: Path, contenu: bytes) -> bool:
    """Publie ``contenu`` sans exposer de fichier incomplet aux lecteurs.

    Rend ``True`` si le fichier est désormais dans le magasin, ``False`` si
    celui-ci a refusé l'écriture ou si le contenu ne passe pas
    `valider_contenu`. Ne lève jamais : c'est ce qui garantit qu'un magasin en
    lecture seule ne fait pas échouer une collecte. L'appelant garde son
    contenu ; il ne le partage simplement pas. Sauf chez l'écrivain du
    magasin (`ecrivain()`), pour qui tout refus lève.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        if ecrivain():
            raise
        _signaler_refus(path, e)
        return False

    temporaire = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.part")
    try:
        temporaire.write_bytes(contenu)
        valider_contenu(temporaire, path)
        os.replace(temporaire, path)
        return True
    except ContenuInvalide as e:
        if ecrivain():
            raise
        print(f"  [magasin] contenu refusé, non partagé : {e}")
        return False
    except OSError as e:
        if ecrivain():
            raise
        _signaler_refus(path, e)
        return False
    finally:
        try:
            temporaire.unlink(missing_ok=True)
        except OSError:
            pass


def publier_atomiquement(path: Path, temporaire: Path) -> bool:
    """Publie un fichier déjà écrit À CÔTÉ de ``path`` (même dossier, nom en
    ``.*.part``) : contrôlé par `valider_contenu`, puis remplacement.

    Pour un fichier que l'écrivain construit sur place (l'index DECP, 1 Go) :
    le recopier par `copier_atomiquement` doublerait l'écriture. Même contrat :
    ``False`` sur un refus, sauf chez l'écrivain, pour qui tout refus lève. Le
    temporaire est toujours retiré.
    """
    try:
        valider_contenu(temporaire, path)
        os.replace(temporaire, path)
        return True
    except ContenuInvalide as e:
        if ecrivain():
            raise
        print(f"  [magasin] contenu refusé, non partagé : {e}")
        return False
    except OSError as e:
        if ecrivain():
            raise
        _signaler_refus(path, e)
        return False
    finally:
        try:
            temporaire.unlink(missing_ok=True)
        except OSError:
            pass


class TransfertTronque(EOFError):
    """La source a rendu moins d'octets qu'elle n'en annonçait.

    Pas un ``OSError`` : ce n'est pas le magasin qui refuse, c'est la source qui
    n'a pas tout donné — l'appelant doit réessayer, pas se passer du partage.
    """


def _taille_annoncee(source) -> int | None:
    """Le ``Content-Length`` d'une réponse HTTP, ``None`` pour tout autre flux."""
    entetes = getattr(source, "headers", None)
    valeur = entetes.get("Content-Length") if entetes is not None else None
    try:
        return int(valeur) if valeur is not None else None
    except (TypeError, ValueError):
        return None


def copier_atomiquement(path: Path, source, taille_bloc: int = 1 << 20) -> bool:
    """Écrit un flux dans le magasin sans jamais le charger entièrement en mémoire.

    Même contrat que ``ecrire_atomiquement`` — publication par remplacement, et
    un refus d'écriture rend ``False`` au lieu de lever. La différence est le
    plafond de mémoire : un consolidé DECP annuel pèse jusqu'à 950 Mo, et
    ``resp.read()`` en faisait autant de mémoire vive avant même le parsing.

    Une seule chose lève, ``TransfertTronque`` : lue par blocs, une connexion
    coupée en route rend un bloc vide comme une fin de fichier, sans erreur.
    Le 30/09/2026 deux consolidés sont ainsi entrés au magasin à 465 et 268 Mo
    au lieu de 699 et 586, « frais » pour sept jours. La taille reçue est donc
    comparée à la taille annoncée AVANT le remplacement.

    ``ContenuInvalide`` lève aussi : le fichier reçu ne passe pas
    `valider_contenu`, et l'appelant n'a pas d'autre copie à lire.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        if ecrivain():
            raise
        _signaler_refus(path, e)
        return False

    annoncee = _taille_annoncee(source)
    temporaire = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.part")
    try:
        with temporaire.open("wb") as sortie:
            shutil.copyfileobj(source, sortie, taille_bloc)
        recue = temporaire.stat().st_size
        if annoncee is not None and recue != annoncee:
            raise TransfertTronque(
                f"{path.name} : {recue} octets reçus sur {annoncee} annoncés")
        valider_contenu(temporaire, path)
        os.replace(temporaire, path)
        return True
    except OSError as e:
        if ecrivain():
            raise
        _signaler_refus(path, e)
        return False
    finally:
        try:
            temporaire.unlink(missing_ok=True)
        except OSError:
            pass


def deposer(url: str, fichier: Path, source: str, jours: int | None,
            facteur_perime: int = 2) -> Path:
    """Le fichier à lire : celui du magasin s'il est frais, sinon une copie neuve.

    Le patron de `lovac`, repris par les collecteurs de jeux nationaux venus
    après lui : quand le magasin refuse de remplacer le fichier (celui d'un
    autre compte, dossier sticky), la copie est lue dans un dossier temporaire.
    Relire l'ancienne en la disant déposée publiait un millésime périmé.
    L'appelant efface le dossier temporaire (`retirer_hors_magasin`).

    Le fichier n'est pas archivé dans `raw_documents` : `archive_fetch` ne
    garde rien d'un corps vide, et un fichier national n'a rien à y faire.
    """
    import tempfile
    import urllib.parse
    import urllib.request

    from .archive import HEADERS

    # L'URL vient d'un catalogue : `urlopen` lirait aussi bien un `file:` local.
    if urllib.parse.urlsplit(url).scheme != "https":
        raise RuntimeError(f"{source} : URL refusée, https attendu ({url[:80]})")
    if est_frais(fichier, jours):
        return fichier
    ecrivable = magasin_ecrivable(fichier.parent, fichier.name)
    # Seul l'écrivain du magasin le rafraîchit : un lecteur prend le périmé borné.
    if not ecrivable and perime_lisible(fichier, jours, facteur_perime):
        return fichier
    cible = (fichier if ecrivable
             else Path(tempfile.mkdtemp(prefix=f"vigie-{source}-")) / fichier.name)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=600) as r:
            if not copier_atomiquement(cible, r):
                raise OSError(f"écriture impossible dans {cible.parent}")
    except BaseException:
        retirer_hors_magasin(cible, fichier)
        raise
    print(f"  [{source}] fichier national {'déposé' if cible == fichier else 'lu hors du magasin'}"
          f" : {fichier.name}")
    return cible


def retirer_hors_magasin(lu: Path, fichier: Path) -> None:
    """Efface la copie temporaire de `deposer` ; ne touche jamais au magasin."""
    if lu != fichier:
        shutil.rmtree(lu.parent, ignore_errors=True)


def magasin_ecrivable(dossier: Path, nom: str | None = None) -> bool:
    """Dit si le magasin accepte une écriture, AVANT d'ouvrir une connexion.

    Le savoir après coup ne servirait à rien : une réponse HTTP ne se rembobine
    pas, et un consolidé de 950 Mo ne se retélécharge pas pour rien.

    Avec ``nom``, dit aussi si CE fichier pourra être remplacé. Sur le serveur,
    le magasin est partagé entre le portail et les ateliers, et ses dossiers
    portent le sticky bit (`durcir.sh` du portail) : chacun n'y remplace que ce
    qui est à lui. La sonde y passe, puis le renommage échoue (EPERM).
    """
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        sonde = dossier / f".ecriture-{os.getpid()}-{uuid.uuid4().hex}"
        sonde.touch()
        sonde.unlink()
    except OSError as e:
        if ecrivain():
            raise PermissionError(f"l'écrivain du magasin ne peut pas écrire dans {dossier} : {e}") from e
        return False
    if nom is None or _remplacable(dossier / nom):
        return True
    if ecrivain():
        raise PermissionError(f"l'écrivain du magasin ne peut pas remplacer {dossier / nom}")
    return False


def _remplacable(path: Path) -> bool:
    """Un fichier absent, à soi, ou hors d'un dossier sticky se remplace."""
    try:
        proprietaire = path.stat().st_uid
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if os.geteuid() in (0, proprietaire):
        return True
    return not path.parent.stat().st_mode & stat.S_ISVTX
