#!/usr/bin/env python3
"""L'écrivain unique du magasin national : il le remplit, les autres le lisent.

Sur le serveur, le portail et les ateliers lisaient ET écrivaient le magasin
partagé (`$VIGIE_NATIONAL_STORE`). Sous le sticky bit, chaque fichier restait à
son premier écrivain : les DECP de Lasalle ne se rafraîchissaient que par
Lasalle, et un atelier compromis pouvait réécrire ce que le portail publie
(instruction sécurité du 30/10/2026). Désormais un seul compte écrit
(`vigie-magasinier`, unité `vigie-magasin.service` du portail), la nuit.

Pas de script jumeau : les URL et les noms de fichiers restent ceux des
collecteurs, que les lecteurs cherchent. Deux façons de les jouer :
  - les jeux NATIONAUX (`STEPS_NATIONAUX`) : le vrai step, UNE fois par passe,
    sur une instance jetable posée sur la première commune demandée ;
  - les jeux par département (`REMPLISSEURS`) : la fonction
    `remplir_magasin(departements)` du collecteur, sans commune ni base. La BAN
    se lit pour toute la France, une fois par trimestre.
Avant (jusqu'au 30/10/2026), les quinze steps étaient rejoués par département :
40 minutes pour 10 départements, et la borne de 40 départements en vue.

    rafraichir_magasin.py --communes /srv/vigie/magasin-demandes/communes.txt
    rafraichir_magasin.py --communes … --a-blanc     # dit ce qu'il ferait
    rafraichir_magasin.py --communes … --accepter-retrecissement decp/decp-2024-09.json

Les steps tournent en mode écrivain (`VIGIE_ECRIVAIN_MAGASIN=1`) : un refus
d'écriture ou un contenu refusé fait échouer le step, au lieu d'être contourné
comme chez un lecteur. Un fichier republié plus petit que la moitié du
précédent est refusé (`valider_contenu`) ; `--accepter-retrecissement` lève la
règle pour CE fichier, à la main, après l'avoir regardé.

Le fichier des communes est écrit par le portail, un service exposé : c'est une
entrée NON FIABLE. Une ligne qui n'est pas un code INSEE est ignorée.

À la fin, `MANIFESTE.json` à la racine du magasin : taille, empreinte et date de
chaque fichier, pour constater ce qui a changé d'une nuit à l'autre. Sortie en 1
si un step a échoué : l'unité le montre, `vigie_etat.sh --ecarts` aussi.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collectors.national_store import (CODE_INSEE, DEPARTEMENT, departement,  # noqa: E402
                                       ecrire_atomiquement)

# Les steps des jeux NATIONAUX, et leur module : le même fichier sert à toutes
# les communes, le step se joue une fois par passe.
# `tests/test_rafraichir_magasin.py` vérifie que tout module qui importe
# `national_store` est dans l'une des trois tables : un collecteur nouveau ne
# peut pas être oublié.
STEPS_NATIONAUX = {
    "marches": "marches_publics",
    "sispea": "sispea",
    "lovac": "lovac",
    "sante": "sante",
    "cantines": "cantines",
    "enaf": "enaf",
    "loyers": "loyers",
    "vigieau": "vigieau",
    "equipements": "equipements",
    "guichets": "guichets",
    "mobilite": "mobilite",
    "telecoms": "telecoms",
    "jaune": "jaune_associations",
}
# Les jeux par département : `collectors.<module>.remplir_magasin(departements)`.
REMPLISSEURS = {
    "dvf": "dvf",
    "incendie": "incendie",
    # L'index des consolidés DECP par acheteur : après `marches`, qui vient de
    # les rafraîchir (les nationaux passent avant les remplisseurs).
    "decp_index": "marches_publics",
}
# Déposent au magasin, mais par COMMUNE : rien à préparer pour une commune que
# personne n'a encore demandée. Le lecteur télécharge la sienne.
LECTEURS_SEULS = {
    "cadastre": "parcellaire d'une commune",
}
# Le formulaire du portail est le levier : la borne tient la passe sous son
# budget. Depuis que seul DVF se remplit par département (~5 Mo chacun,
# mesuré le 30/10/2026), elle couvre la France entière (101 départements).
DEPARTEMENTS_MAX = 110
COMMUNES_OCTETS_MAX = 64 << 10
STEP_TIMEOUT = 3600
# La BAN de toute la France : ~2 Go à télécharger, valider, découper et
# recompresser sous Nice=10, avec trois essais. Estimé, pas encore mesuré.
DELAI_DU_STEP = {"incendie": 2 * 3600}
# Sous le TimeoutStartSec=4h de l'unité : la passe s'arrête d'elle-même, écrit
# son manifeste et sort en 1, plutôt que d'être tuée sans rien dire.
BUDGET_SECONDES = 3 * 3600 + 1800
# Le plus gros fichier (un consolidé DECP, ~950 Mo) en `.part`, plus l'ancien
# encore ouvert par un lecteur : en deçà, la passe s'arrête et garde l'ancien.
DISQUE_MARGE = 3 << 30
PART_AGE_MAX = 6 * 3600
# Ni les bases ni les données d'une instance : la copie vient d'une révision
# du dépôt, mais rien n'interdit d'en avoir déposé à côté.
IGNORES = shutil.ignore_patterns(".git", "node_modules", "venv", ".venv", "__pycache__",
                                 "*.db", "*.db-*", "*.sqlite*", "raw", "audits")


def communes_par_departement(lignes: list[str]) -> list[str]:
    """La première commune valide de chaque département, dans l'ordre du fichier."""
    vues: dict[str, str] = {}
    for ligne in lignes:
        code = ligne.strip().upper()
        if not CODE_INSEE.fullmatch(code):
            continue
        vues.setdefault(departement(code), code)
        if len(vues) >= DEPARTEMENTS_MAX:
            break
    return list(vues.values())


def lire_communes(chemin: Path) -> list[str]:
    """Le fichier des communes, lu comme une entrée hostile.

    Il est écrit par le compte du service exposé : ce pourrait être un lien
    vers /dev/zero, un FIFO qui bloque la lecture jusqu'au délai de l'unité, ou
    un fichier énorme. Seul un fichier ordinaire est lu, et 64 Kio au plus.
    """
    try:
        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    except OSError as e:
        print(f"  ⚠ {chemin} refusé : {e}")
        return []
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            print(f"  ⚠ {chemin} n'est pas un fichier ordinaire : ignoré")
            return []
        brut = os.read(fd, COMMUNES_OCTETS_MAX)
    finally:
        os.close(fd)
    return brut.decode("utf-8", errors="replace").splitlines()


def purger_parts(magasin: Path, maintenant: float | None = None) -> list[Path]:
    """Les `.*.part` de plus de 6 h : l'écrivain est seul, ce sont les siens
    (un SIGKILL ou un manque de mémoire en pleine copie)."""
    maintenant = time.time() if maintenant is None else maintenant
    retires = []
    for chemin in magasin.rglob(".*.part"):
        try:
            if maintenant - chemin.lstat().st_mtime < PART_AGE_MAX:
                continue
            if chemin.is_dir() and not chemin.is_symlink():
                shutil.rmtree(chemin)
            else:
                chemin.unlink()
            retires.append(chemin)
        except OSError as e:
            print(f"  ⚠ {chemin} : {e}")
    return retires


def manifeste(magasin: Path) -> dict:
    fichiers = {}
    for chemin in sorted(magasin.rglob("*")):
        relatif = chemin.relative_to(magasin)
        if (not chemin.is_file() or chemin.is_symlink()
                or any(p.startswith(".") for p in relatif.parts)
                or relatif.as_posix() == "MANIFESTE.json"):
            continue
        empreinte = hashlib.sha256()
        with chemin.open("rb") as flux:
            for bloc in iter(lambda: flux.read(1 << 20), b""):
                empreinte.update(bloc)
        info = chemin.stat()
        fichiers[relatif.as_posix()] = {
            "taille": info.st_size,
            "sha256": empreinte.hexdigest(),
            "modifie": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(timespec="seconds"),
        }
    return fichiers


def preparer_espace(destination: Path) -> Path:
    moteur = destination / "moteur"
    shutil.copytree(ROOT, moteur, ignore=IGNORES, symlinks=True)
    return moteur


def lancer(commande: list[str], cwd: Path, env: dict[str, str],
           delai: int = STEP_TIMEOUT) -> bool:
    print(f"  $ {' '.join(commande)}", flush=True)
    try:
        return subprocess.run(commande, cwd=cwd, env=env, timeout=delai).returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  ✗ dépassement de {delai} s", flush=True)
        return False


def rafraichir(communes: list[str], magasin: Path, steps: list[str],
               acceptes: list[str] | None = None) -> list[str]:
    """Joue chaque step du magasin une fois ; rend les échecs."""
    echecs: list[str] = []
    fin = time.monotonic() + BUDGET_SECONDES
    departements = [departement(c) for c in communes]
    env = os.environ.copy()
    env["VIGIE_NATIONAL_STORE"] = str(magasin)
    env["VIGIE_ECRIVAIN_MAGASIN"] = "1"
    env["VIGIE_ACCEPTER_RETRECISSEMENT"] = ",".join(acceptes or [])
    with tempfile.TemporaryDirectory(prefix="vigie-magasinier-") as espace:
        moteur = preparer_espace(Path(espace))
        env["PYTHONPATH"] = str(moteur)
        # Une instance jetable, pour que les collecteurs aient une configuration
        # et une base où archiver : la première commune qui s'initialise.
        for code in communes:
            print(f"\n── instance jetable : {code}", flush=True)
            shutil.copy2(moteur / "tests" / "instance_test.json", moteur / "config" / "instance.json")
            for exemple, cible in (("config/profils_locaux.exemple.json", "config/profils_locaux.json"),
                                   ("config/seed_local.exemple.json", "config/seed_local.json")):
                if (moteur / exemple).is_file():
                    shutil.copy2(moteur / exemple, moteur / cible)
            if lancer([sys.executable, "scripts/init_instance.py", code, "--force"], moteur, env):
                break
            echecs.append(f"{code}:init")
        else:
            return echecs
        for step in steps:
            if time.monotonic() >= fin:
                print(f"  ✗ budget de {BUDGET_SECONDES} s épuisé : passe arrêtée", flush=True)
                echecs.append(f"{step}:budget")
                return echecs
            libre = shutil.disk_usage(magasin).free
            if libre < DISQUE_MARGE:
                print(f"  ✗ {libre >> 20} Mio libres sous {magasin} : passe arrêtée, "
                      "les fichiers en place sont gardés", flush=True)
                echecs.append(f"{step}:disque")
                return echecs
            print(f"\n── {step}", flush=True)
            if step in REMPLISSEURS:
                commande = [sys.executable, "scripts/rafraichir_magasin.py",
                            "--remplir", REMPLISSEURS[step], *departements]
            else:
                commande = [sys.executable, "-m", "collectors.run_all", "--step", step]
            if not lancer(commande, moteur, env, DELAI_DU_STEP.get(step, STEP_TIMEOUT)):
                echecs.append(step)
    return echecs


def remplir(module: str, departements: list[str]) -> int:
    """Dans le processus fils : `collectors.<module>.remplir_magasin`."""
    import importlib

    if module not in REMPLISSEURS.values():
        print(f"--remplir : « {module} » n'est pas un remplisseur du magasin")
        return 2
    departements = [d for d in departements if DEPARTEMENT.fullmatch(d)]
    echecs = importlib.import_module(f"collectors.{module}").remplir_magasin(departements)
    if echecs:
        print(f"  ✗ {module} : {', '.join(echecs)}")
    return 1 if echecs else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--communes", type=Path,
                        default=os.environ.get("VIGIE_COMMUNES_DEMANDEES"),
                        help="fichier des codes INSEE demandés, un par ligne")
    parser.add_argument("--step", action="append", choices=sorted({**STEPS_NATIONAUX, **REMPLISSEURS}),
                        help="limiter à ce step (répétable)")
    parser.add_argument("--a-blanc", action="store_true", help="dire ce qui serait fait")
    parser.add_argument("--accepter-retrecissement", action="append", default=[], metavar="DOSSIER/FICHIER",
                        help="accepter qu'un fichier republié fonde de plus de moitié (répétable)")
    parser.add_argument("--remplir", nargs="+", metavar=("MODULE", "DEPARTEMENT"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not os.environ.get("VIGIE_NATIONAL_STORE"):
        print("VIGIE_NATIONAL_STORE n'est pas défini : l'écrivain ne devine pas le magasin.")
        return 2
    if args.remplir:
        return remplir(args.remplir[0], args.remplir[1:])
    magasin = Path(os.environ["VIGIE_NATIONAL_STORE"]).resolve()
    lignes = lire_communes(Path(args.communes)) if args.communes else []
    communes = communes_par_departement(lignes)
    steps = args.step or [*STEPS_NATIONAUX, *REMPLISSEURS]
    print(f"Magasin {magasin} — {len(communes)} département(s) : {' '.join(communes) or 'aucun'}")
    if args.a_blanc:
        print(f"Steps : {' '.join(steps)}")
        return 0
    if not communes:
        print("Aucune commune demandée : rien à rafraîchir.")
        return 0

    for chemin in purger_parts(magasin):
        print(f"  .part abandonné retiré : {chemin.relative_to(magasin)}")
    debut = time.time()
    for accepte in args.accepter_retrecissement:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", accepte):
            print(f"--accepter-retrecissement : « {accepte} » n'est pas un DOSSIER/FICHIER du magasin")
            return 2
    echecs = rafraichir(communes, magasin, steps, args.accepter_retrecissement)
    contenu = {
        "ecritA": datetime.now(UTC).isoformat(timespec="seconds"),
        "dureeSecondes": round(time.time() - debut),
        "departements": [departement(c) for c in communes],
        "echecs": echecs,
        "fichiers": manifeste(magasin),
    }
    if not ecrire_atomiquement(magasin / "MANIFESTE.json",
                               json.dumps(contenu, ensure_ascii=False, indent=1).encode()):
        echecs.append("manifeste")
    print(f"\n{'✗' if echecs else '✓'} passe finie en {contenu['dureeSecondes']} s, "
          f"{len(contenu['fichiers'])} fichiers au manifeste"
          + (f" — échecs : {', '.join(echecs)}" if echecs else ""))
    return 1 if echecs else 0


if __name__ == "__main__":
    raise SystemExit(main())
