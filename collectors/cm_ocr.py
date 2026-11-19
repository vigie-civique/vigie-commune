"""
cm_ocr.py — Collecteur CR du Conseil Municipal diffusés en PDF SCANNÉ (image).

Les procès-verbaux les plus anciens de brassac.fr (avant 2014) sont des PDF
*scannés*, sans couche texte. ⚠️ Ce chemin en ligne de commande n'écrit plus
(audit du 26/10/2026) : il découpait avec l'ancien découpeur, sans `cle_acte`, et
doublonnait ce que `conseils --ocr` écrit. Le dry-run reste (il montre le
découpage) ; `--commit` renvoie à `python3 -m collectors.conseils --ocr`. Les
fonctions de ce module servent `conseils`. Historique de la lecture :

  1. résout l'entrée en (PDF, url de CR, date) — page CR, url PDF, fichier local, ou --discover ;
  2. télécharge le PDF (cache data/cm_records_pdf/) ;
  3. OCR *conditionnel* : seulement si le PDF n'a pas de couche texte exploitable
     (ocrmypdf -l fra, cache .ocr.pdf) — un PDF déjà textuel est parsé tel quel ;
  4. extrait le texte (pdfplumber) et découpe en délibérations
     (marqueurs « 48/2026 : n° 4713 », repli sur le splitter de cm_parser) ;
  5. upsert idempotent d'un event type='deliberation' par délib + un event
     ombrelle type='conseil_municipal' (titre + lien PDF).

Périmètre : on publie les TITRES des délibérations (la page publique n'affiche pas
le corps OCR). Les montants OCR — peu fiables — sont conservés en métadonnée
`montants_ocr` (flag `ocr:true`) pour validation atelier ; AUCUN financial_flow
n'est créé automatiquement (règle : ne jamais publier du non vérifié).

Prérequis système : ocrmypdf + tesseract + langue française. La langue FR doit être
installée dans le tessdata SYSTÈME (qui contient les configs hocr/txt), pas dans un
TESSDATA_PREFIX isolé. Vérifier : `tesseract --list-langs` doit lister `fra`.

Usage :
  python3 -m collectors.cm_ocr --pdf-url https://.../PV.pdf --date 2013-09-02
  python3 -m collectors.cm_ocr --file data/cm_records_pdf/PV.pdf --date 2013-09-02 --commit
  python3 -m collectors.cm_ocr --discover                 # PV scannés non parsés
  python3 -m collectors.cm_ocr --discover --commit --limit 3
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

from .config import COMMUNE_URL, HEADERS
from .db import transaction, get_conn
from .cm_parser import (
    categorize, extract_vote, extract_amounts, extract_names,
    split_into_deliberations, _parse_french_date, _date_from_filename,
)

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "cm_records_pdf"
# La découverte s'appuie sur le catalogue rendu par le connecteur, pas sur une
# page de listing propre à un site.
SOURCE = urllib.parse.urlparse(COMMUNE_URL).netloc.removeprefix("www.")

# En-dessous de ce nombre de caractères extraits, on considère le PDF « scanné » → OCR.
MIN_TEXT_CHARS = 400

# Les options d'`ocrmypdf`, à UN SEUL endroit : elles étaient recopiées ici et
# dans `conseils.ocr`, et deux copies d'un réglage divergent au premier
# ajustement. La langue n'en fait pas partie — elle se passe à l'appel.
#
# `--oversample 400` a été ajouté le 02/09/2026, après mesure. Les pages
# scannées de l'instance de référence portent DEUX images superposées : un fond
# en couleur à 150 ou 200 ppi et un calque de texte bitonal à 300 ppi. La
# rastérisation par défaut n'atteignait pas 300 et jetait la finesse du calque —
# celle qui porte précisément les lettres.
#
# Mesuré sur 80 pages de dix procès-verbaux, au vocabulaire que la commune écrit
# elle-même (`~/Claude/scripts/vigie_banc_ocr.py`) : les formes inconnues
# DISTINCTES tombent de 1 046 à 931, soit −11 %. « Bernard » lu « bemard » 9
# fois puis 3, « préfecture » lue « prefeciure » 5 fois puis 1, « nombre de » et
# « conseillers » entrelacés 8 fois puis 4. Le gain suit exactement la présence
# du calque à 300 ppi : les documents à 200 ppi n'y gagnent rien, ce qui est la
# preuve du mécanisme et non une coïncidence.
#
# ⚠️ La part de mots lus, elle, ne monte que de 93,5 % à 94,2 %. C'est le bon
# ordre de grandeur pour une masse dominée par des mots courants déjà justes, et
# c'est pourquoi l'indicateur retenu est le nombre de formes ABÎMÉES DISTINCTES :
# une moyenne écrasée par ce qui marche déjà ne dit rien de ce qui casse.
#
# ⚠️ Coûte le double de temps. Les `.ocr.pdf` déjà en cache ne sont PAS refaits :
# le bénéfice ne vient qu'à une nouvelle océrisation.
#
# `--rotate-pages` a été ajouté le 23/10/2026, avec un seuil abaissé à 5. Les
# délibérations de la CC du 16/04/2026 portent des pages A3 scannées couchées
# (90°) sous un bandeau de télétransmission resté droit : lues telles quelles,
# elles ont donné 48 fausses délibérations publiées (« WON 2 1 HANOIQNI »).
# Tesseract y voit bien la rotation, mais avec une confiance de 12,86 — sous le
# seuil par défaut d'ocrmypdf (14), parce que le bandeau droit tire l'estimation
# vers 0°. Mesuré au seuil 5 : les mots courants lus doublent sur ce document
# (234 → 468), et ne bougent pas d'un mot sur un PV normal de la même séance.
OPTIONS_OCRMYPDF = ["--force-ocr", "--output-type", "pdf",
                    "--optimize", "0", "--jobs", "4", "--oversample", "400",
                    "--rotate-pages", "--rotate-pages-threshold", "5"]

# Un PV signé électroniquement : ocrmypdf ≥ 16 REFUSE de l'océriser
# (`DigitalSignatureError`), puisque le PDF produit ne porte plus une signature
# valide. 13 PV de Saillans refusés ainsi sur le Mac le 27/10/2026. Décision de
# Julien (01/11/2026) : invalider la signature sur la copie de travail. C'est
# déjà le cas : ocrmypdf écrit le `.ocr.pdf` À CÔTÉ, l'original téléchargé reste
# la pièce, intacte, signature comprise. L'option n'est passée que si l'outil la
# connaît : la 14.0.1 de Debian 12 (VPS) n'a ni l'option ni le refus.
OPTION_SIGNATURE = "--invalidate-digital-signatures"


@functools.cache
def options_ocrmypdf() -> list[str]:
    """`OPTIONS_OCRMYPDF`, plus l'invalidation de signature si l'ocrmypdf installé la connaît."""
    try:
        # Une sonde, pas une océrisation : l'exécutable par son chemin.
        aide = subprocess.run([shutil.which("ocrmypdf") or "ocrmypdf", "--help"],
                              capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        aide = ""
    return OPTIONS_OCRMYPDF + ([OPTION_SIGNATURE] if OPTION_SIGNATURE in aide else [])

# Le dossier temporaire d'`ocrmypdf`, DANS l'instance et non dans /tmp.
# ocrmypdf y pose un `ocrmypdf.io.*` par document, qu'il ne retire pas quand
# on le tue (délai dépassé, Ctrl-C, session SSH coupée). Le 23/10, une OCR
# lancée à la main sous le compte de l'atelier en a laissé 4,0 Gio dans /tmp
# du VPS, que `PrivateTmp` des unités ne couvre pas. Ici, ce qui reste d'un
# passage interrompu est balayé au passage suivant.
TMP_OCR = ROOT / "data" / "tmp-ocr"
# Plus vieux que ceci, un résidu ne peut appartenir à une OCR en cours : à
# dix secondes par page (`conseils.delai_ocr`), il y faudrait 2 000 pages.
RESIDU_OCR_HEURES = 6


def environnement_ocr() -> dict:
    """L'environnement d'un appel à `ocrmypdf`, son TMPDIR balayé."""
    TMP_OCR.mkdir(parents=True, exist_ok=True)
    limite = time.time() - RESIDU_OCR_HEURES * 3600
    for residu in TMP_OCR.iterdir():
        try:
            if residu.lstat().st_mtime < limite:
                if residu.is_dir() and not residu.is_symlink():
                    shutil.rmtree(residu, ignore_errors=True)
                else:
                    residu.unlink(missing_ok=True)
        except OSError:
            continue
    return {**os.environ, "TMPDIR": str(TMP_OCR)}
# Découpe : « N° DEL 2606_02 », « DEL 2606_02 », etc. → capture l'identifiant AAMM_XX.
# Le séparateur est ce que l'OCR a bien voulu lire du trait d'union bas : le CM
# du 30/06/2026 sort « 2606.08 » et « 2606 _18 », et ces deux délibérations
# passaient au travers du découpage (l'ancien motif n'admettait qu'UN caractère
# de séparation). On tolère donc jusqu'à trois caractères parmi espace . _ -.
# Découpe : « 48/2026 : n° 4713 : » — la numérotation de Brassac, la même que
# celle des PV textuels lus par `conseils`. L'OCR abîme les séparateurs,
# d'où la tolérance sur les caractères entre les deux nombres.
DEL_RE = re.compile(r"(\d{1,3})\s*/\s*(20\d{2})\s*[\s.:_–-]{0,3}(?:n[°ºo]\s*\d{3,5})?", re.I)
_HEADER = re.compile(
    r"DELIBERATION|REGISTRE|REPUBLIQUE|DEPARTEMENT|CONSEIL\s+MUNICIPAL|"
    r"EXTRAIT|SEANCE|SÉANCE|PRESIDENCE|CONVOCATION", re.I)
_TITLE_FIXES = [(r"Î\s*ERE|ÎERE|1ERE", "1ÈRE"), (r"\bSEIL\b", "CONSEIL"),
                (r"—\s*—", "—"), (r"\s{2,}", " ")]


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _http(url: str, timeout: int = 60) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout).read()


def _fetch_text(url: str) -> str:
    return _http(url).decode("utf-8", "replace")


def _pdf_link_from_cr(cr_url: str) -> str | None:
    """Extrait le lien .pdf d'une page de compte rendu."""
    html = _fetch_text(cr_url)
    m = re.search(r'href="([^"]+\.pdf[^"]*)"', html, re.I)
    if not m:
        return None
    return urllib.parse.urljoin(cr_url, m.group(1))


# ── Téléchargement + OCR ───────────────────────────────────────────────────────

def _safe_name(url_or_path: str, date: str | None) -> str:
    base = Path(urllib.parse.urlparse(url_or_path).path).stem or "CM"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:60]
    return f"{date + '_' if date else ''}{base}.pdf"


def download_pdf(pdf_url: str, date: str | None) -> Path:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    dest = PDF_DIR / _safe_name(pdf_url, date)
    if not dest.exists() or dest.stat().st_size == 0:
        dest.write_bytes(_http(pdf_url))
    return dest


def _extract_pages(pdf: Path) -> list[str]:
    with pdfplumber.open(pdf) as f:
        return [p.extract_text() or "" for p in f.pages]


def _extract_text(pdf: Path) -> str:
    return "\n".join(_extract_pages(pdf))


def _check_ocr_tooling(lang: str) -> None:
    if not shutil.which("ocrmypdf"):
        sys.exit("ocrmypdf introuvable — `brew install ocrmypdf` (installe aussi tesseract).")
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    if lang not in langs:
        sys.exit(f"Langue tesseract '{lang}' absente. Copier {lang}.traineddata dans le tessdata "
                 f"SYSTÈME (cf. `tesseract --list-langs`). Ex :\n"
                 f"  curl -sSL -o /tmp/{lang}.traineddata "
                 f"https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata\n"
                 f"  cp /tmp/{lang}.traineddata \"$(brew --prefix tesseract)/share/tessdata/\"")


# ── Le texte reconnu, gardé à côté du PDF océrisé ────────────────────────────
#
# Le cache de la reconnaissance optique était le PDF océrisé lui-même : une
# image par page plus sa couche de texte. Sur l'instance de référence, cela fait
# 10 Go pour 11 Mo de texte — et chaque passe de collecte rouvrait ces PDF pour
# en ressortir le même texte.
#
# Le texte est désormais écrit à côté (`x.ocr.pdf` → `x.ocr.txt`) et relu de
# là. Deux conséquences : la passe ne rouvre plus dix gigaoctets, et une
# instance peut vivre sur une machine qui n'a ni les PDF océrisés ni de quoi
# océriser — il lui suffit des textes.
#
# Sans manifeste, le texte ne vaut que s'il est AU MOINS aussi récent que le PDF
# océrisé quand celui-ci est là : une ré-océrisation adoptée (un `.ocr.pdf`
# remplacé par un meilleur) doit l'emporter sur le texte de l'ancienne.
#
# ── Le manifeste du texte de référence (`x.ocr.json`) ────────────────────────
#
# 24/10/2026, plan de l'architecte pour la lecture des montants (étape 2). Un
# rapport de montants lus sur le Mac doit pouvoir prouver qu'il a lu LE texte
# que tient la base — celui du VPS pour Lasalle — et pas un texte voisin :
# OCR Mac ≠ VPS (tesseract 5.5.2 / 5.3.0) est un défaut connu. La date d'un
# fichier ne prouve rien : une copie, un `rsync`, un `touch` la changent.
#
# Le manifeste dit donc qui a produit le texte (outils, options, machine), en
# donne les empreintes, et les positions de début de page dans le texte joint :
# le découpage à la page s'appuie dessus sans rouvrir le PDF.
#
# Quand il est là, c'est LUI qui décide, plus la date :
#   - rôle autre que « reference » → le texte n'est jamais lu ;
#   - empreinte du texte discordante → le texte a été retouché, il est écarté ;
#   - `.ocr.pdf` présent d'une autre taille → une réocérisation l'a remplacé.
# Sans manifeste, la règle de la date s'applique : les textes d'avant se lisent
# comme avant. Les textes déjà en base reçoivent un manifeste rétrospectif
# (`scripts/manifester_textes_ocr.py`), provenance « antérieure au manifeste ».
#
# ⚠️ Rien ici ne touche `metadata.ocr` d'un événement : `lu_par_ocr` compare
# `= 1`, et en faire un objet ferait retomber D5 sans bruit.
FORMAT_MANIFESTE = "vigie-ocr-texte/1"


def empreinte(octets: bytes) -> str:
    return hashlib.sha256(octets).hexdigest()


def _empreinte_fichier(chemin: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(chemin, "rb") as f:
            for bloc in iter(lambda: f.read(1 << 20), b""):
                h.update(bloc)
        return h.hexdigest()
    except OSError:
        return None


def positions_pages(pages: list[str]) -> list[int]:
    """Début de chaque page dans `"\\n".join(pages)` — le texte gardé."""
    debuts, pos = [], 0
    for p in pages:
        debuts.append(pos)
        pos += len(p) + 1
    return debuts


@functools.lru_cache(maxsize=1)
def versions_outils() -> dict:
    def premiere_ligne(*cmd):
        if not shutil.which(cmd[0]):
            return None
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except (subprocess.SubprocessError, OSError):
            return None
        return ((r.stdout or r.stderr).strip().splitlines() or [None])[0]
    return {"ocrmypdf": premiere_ligne("ocrmypdf", "--version"),
            "tesseract": premiere_ligne("tesseract", "--version"),
            "pdftotext": premiere_ligne("pdftotext", "-v"),
            "pdfplumber": getattr(pdfplumber, "__version__", None)}


def lire_manifeste(ocr_pdf: Path) -> dict | None:
    """Le manifeste, ou None s'il n'y en a pas. Illisible = discordant : {}."""
    chemin = ocr_pdf.with_suffix(".json")
    if not chemin.exists():
        return None
    try:
        m = json.loads(chemin.read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def ecrire_manifeste(ocr_pdf: Path, texte: str, pages: list[str] | None, *,
                     provenance: str = "collecte", langue: str | None = "fra",
                     outils: dict | str | None = None) -> dict:
    original = ocr_pdf.with_name(ocr_pdf.name.removesuffix(".ocr.pdf") + ".pdf")
    debuts = positions_pages(pages) if pages is not None and "\n".join(pages) == texte else None
    m = {
        "format": FORMAT_MANIFESTE,
        "role": "reference",
        "provenance": provenance,
        "original": original.name,
        "original_sha256": _empreinte_fichier(original),
        "ocr_pdf_taille": ocr_pdf.stat().st_size if ocr_pdf.exists() else None,
        "texte_sha256": empreinte(texte.encode("utf-8")),
        "pages": debuts,
        "outils": outils if outils is not None else versions_outils(),
        "options_ocrmypdf": options_ocrmypdf() if provenance == "collecte" else None,
        "langue": langue,
        # La machine qui a PRODUIT le texte : connue seulement à la collecte.
        # Un manifeste rétrospectif ne dit que qui l'a écrit.
        "machine": socket.gethostname() if provenance == "collecte" else None,
        "manifeste_ecrit_par": socket.gethostname(),
        "ecrit_le": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    chemin = ocr_pdf.with_suffix(".json")
    provisoire = chemin.with_name(chemin.name + ".partiel")
    provisoire.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    provisoire.replace(chemin)
    return m


def texte_garde(ocr_pdf: Path) -> str | None:
    """Le texte déjà extrait de ce PDF océrisé, ou rien s'il est absent, périmé
    ou désavoué par son manifeste."""
    garde = ocr_pdf.with_suffix(".txt")
    try:
        if garde.stat().st_size == 0:
            return None
        manifeste = lire_manifeste(ocr_pdf)
        if manifeste is None:
            if ocr_pdf.exists() and ocr_pdf.stat().st_mtime > garde.stat().st_mtime:
                return None
            return garde.read_text(encoding="utf-8")
        if manifeste.get("role") != "reference":
            return None
        if ocr_pdf.exists() and manifeste.get("ocr_pdf_taille") not in (None, ocr_pdf.stat().st_size):
            return None
        texte = garde.read_text(encoding="utf-8")
        if empreinte(texte.encode("utf-8")) != manifeste.get("texte_sha256"):
            print(f"  [ocr] {garde.name} : empreinte ≠ manifeste, texte écarté")
            return None
        return texte
    except OSError:
        return None


def pages_gardees(ocr_pdf: Path, texte: str) -> list[str] | None:
    """Les pages de `texte`, recoupées aux débuts que garde le manifeste — ou rien.

    Rien quand le manifeste manque, n'a pas de pages, ou n'est pas celui de CE
    texte (empreinte) : le découpage à la page ne se fait que sur une preuve.
    """
    m = lire_manifeste(ocr_pdf) or {}
    debuts = m.get("pages")
    if not debuts or m.get("texte_sha256") != empreinte(texte.encode("utf-8")):
        return None
    fins = [d - 1 for d in debuts[1:]] + [len(texte)]
    pages = [texte[d:f] for d, f in zip(debuts, fins)]
    return pages if "\n".join(pages) == texte else None


def garder_texte(ocr_pdf: Path, texte: str, pages: list[str] | None = None,
                 langue: str = "fra") -> str:
    """Écrit le texte à côté du PDF océrisé, avec son manifeste, et le rend. Un
    texte vide n'est pas gardé : il dirait « déjà lu, rien dedans » d'un document
    qu'on n'a pas su lire."""
    if texte.strip():
        garde = ocr_pdf.with_suffix(".txt")
        provisoire = garde.with_name(garde.name + ".partiel")
        try:
            provisoire.write_text(texte, encoding="utf-8")
            provisoire.replace(garde)
            ecrire_manifeste(ocr_pdf, texte, pages, langue=langue)
        except OSError as e:           # répertoire en lecture seule : on s'en passe
            print(f"  [ocr] texte non gardé ({garde.name}) : {e}")
    return texte


def ensure_text(pdf: Path, lang: str = "fra") -> str:
    """Retourne le texte du PDF ; lance l'OCR si le PDF est scanné (peu/pas de texte)."""
    txt = _extract_text(pdf)
    if len(txt.strip()) >= MIN_TEXT_CHARS:
        return txt  # déjà textuel
    ocr_pdf = pdf.with_suffix(".ocr.pdf")
    if (garde := texte_garde(ocr_pdf)) is not None:
        return garde
    if not ocr_pdf.exists():
        _check_ocr_tooling(lang)
        print(f"  OCR ({lang}) → {ocr_pdf.name} …")
        subprocess.run(
            ["ocrmypdf", "-l", lang, *options_ocrmypdf(), str(pdf), str(ocr_pdf)],
            check=True, capture_output=True, env=environnement_ocr(),
        )
    pages = _extract_pages(ocr_pdf)
    return garder_texte(ocr_pdf, "\n".join(pages), pages, langue=lang)


# ── Parsing des délibérations ──────────────────────────────────────────────────

def _clean_title(body: str) -> str:
    for line in body.splitlines():
        s = line.strip(" .:-—")
        if len(s) < 12:
            continue
        letters = s.replace(" ", "")
        if letters and sum(c.isupper() for c in letters) >= 0.7 * len(letters) \
           and re.search(r"[A-ZÉÈÀÂÊÎÔÛ]{5}", s) and not _HEADER.search(s):
            for pat, rep in _TITLE_FIXES:
                s = re.sub(pat, rep, s)
            return s.strip()
    return "(titre non reconnu)"


def _prefix_from_date(date: str | None) -> str | None:
    """« 2026-06-30 » → « 2606 » (préfixe AAMM des n° DEL de ce CM)."""
    m = re.match(r"\d{2}(\d{2})-(\d{2})-\d{2}", date or "")
    return (m.group(1) + m.group(2)) if m else None


def parse_deliberations(txt: str, expected_prefix: str | None = None) -> list[dict]:
    """Découpe par marqueurs « 48/2026 : n° 4713 » ; repli sur cm_parser si absent.

    On ne garde que les délibérations DE CE CM : les n° DEL d'autres séances
    *cités* dans le corps sont ignorés en filtrant sur le préfixe AAMM courant
    (celui de la date, ou à défaut le préfixe majoritaire des marqueurs).
    """
    from collections import Counter
    marks = list(DEL_RE.finditer(txt))
    delibs: list[dict] = []
    if len(marks) >= 2:
        counts = Counter(m.group(1) for m in marks)
        target = expected_prefix if expected_prefix in counts else counts.most_common(1)[0][0]
        kept = [m for m in marks if m.group(1) == target]
        seen = set()
        for j, m in enumerate(kept):
            del_id = f"{m.group(1)}_{m.group(2)}"
            if del_id in seen:
                continue
            seen.add(del_id)
            end = kept[j + 1].start() if j + 1 < len(kept) else len(txt)
            body = txt[m.end(): end]
            title = _clean_title(body)
            delibs.append(_make_delib(f"DEL {del_id} — {title}", body, del_id))
    else:
        # Repli : splitter générique de cm_parser (sections en MAJUSCULES).
        for d in split_into_deliberations([l for l in txt.splitlines() if l.strip()]):
            body = "\n".join(d.get("paragraphes", []))
            delibs.append(_make_delib(d["titre"], body, None))
    return delibs


def _make_delib(titre: str, body: str, del_id: str | None) -> dict:
    cat, tags = categorize(titre)
    return {
        "titre": titre[:255],
        "texte": re.sub(r"\n{2,}", "\n", body).strip()[:6000],
        "categorie": cat, "tags": tags,
        "vote": extract_vote(body),
        "montants_ocr": [m["value"] for m in extract_amounts(body)][:8],
        "personnes_citees": list(dict.fromkeys(extract_names(body)))[:15],
        "del_id": del_id,
    }


def _derive_date(txt: str, url: str | None) -> str | None:
    return (_date_from_filename(url or "")
            or _parse_french_date(txt[:1500])
            or _parse_french_date(txt))


# ── Intégration DB ─────────────────────────────────────────────────────────────

def _upsert_delib(conn, date: str, cr_url: str, pdf_url: str, d: dict) -> str:
    import json
    meta = {
        "categorie": d["categorie"], "tags": d["tags"], "vote": d["vote"],
        "montants_ocr": d["montants_ocr"], "personnes_citees": d["personnes_citees"],
        "ocr": True, "del_id": d["del_id"], "pdf_url": pdf_url,
        "note": "Texte OCR (PDF scanné) — montants à valider avant publication de flux.",
    }
    c = conn.cursor()
    # Idempotence par n° de délibération, pas par titre : les titres sont
    # retouchés après coup (normalize_titres_ocr.py les passe en casse de
    # phrase, et met les fragments en quarantaine `fragment_ocr`). Rechercher
    # le titre brut ne retrouvait donc plus rien, et une seconde passe du
    # collecteur réinsérait tout le CM en double. Le n°, lui, ne bouge pas.
    # La recherche ignore le type : une délib mise en quarantaine doit être
    # mise à jour là où elle est, pas ressuscitée en double.
    row = None
    if d["del_id"]:
        row = c.execute(
            "SELECT id FROM events WHERE date=? AND source=?"
            " AND json_extract(metadata,'$.del_id')=?",
            (date, SOURCE, d["del_id"])).fetchone()
    if row is None:
        row = c.execute("SELECT id FROM events WHERE date=? AND title=? AND type='deliberation'",
                        (date, d["titre"])).fetchone()
    if row:
        # `COALESCE(?, source_url)` : un retraitement local (--file, sans URL
        # publique) ne doit pas effacer l'URL officielle déjà enregistrée.
        c.execute("UPDATE events SET content=?, metadata=?, source=?, "
                  "source_url=COALESCE(?, source_url) WHERE id=?",
                  (d["texte"], json.dumps(meta, ensure_ascii=False), SOURCE,
                   cr_url, row[0]))
        return "maj"
    c.execute("INSERT INTO events (type,date,title,content,source,source_url,metadata) "
              "VALUES ('deliberation',?,?,?,?,?,?)",
              (date, d["titre"], d["texte"], SOURCE, cr_url,
               json.dumps(meta, ensure_ascii=False)))
    return "new"


def _upsert_umbrella(conn, date: str, cr_url: str, pdf_url: str, delibs: list[dict]) -> str:
    import json
    c = conn.cursor()
    if c.execute("SELECT id FROM events WHERE date=? AND type='conseil_municipal'", (date,)).fetchone():
        return "skip"
    content = f"Conseil municipal du {date} — {len(delibs)} délibérations (source {SOURCE}) :\n" + \
              "\n".join(f"- {d['titre']}" for d in delibs)
    meta = json.dumps({"pdf_url": pdf_url, "page_url": cr_url, "ocr": True}, ensure_ascii=False)
    c.execute("INSERT INTO events (type,date,title,content,source,source_url,metadata) "
              "VALUES ('conseil_municipal',?,?,?,?,?,?)",
              (date, f"CM du {date}", content, SOURCE, cr_url, meta))
    return "new"


def _upsert_pv(conn, date: str, cr_url: str, pdf_url: str, txt: str) -> str:
    """Procès-verbal verbatim : un seul event conseil_municipal, texte intégral
    recherchable (FTS), SANS découpage en délibérations (structure non adaptée).

    Enrichit un event conseil_municipal *mince* déjà présent (stub d'un collecteur
    antérieur) en lui ajoutant le verbatim + le lien PDF, sans écraser son résumé.
    """
    import json
    body = txt.strip()[:60000]
    meta = json.dumps({"pdf_url": pdf_url, "page_url": cr_url, "ocr": True,
                       "note": "Procès-verbal verbatim — texte intégral, non découpé en délibérations."},
                      ensure_ascii=False)
    c = conn.cursor()
    row = c.execute(
        "SELECT id, content, metadata FROM events WHERE date=? AND type='conseil_municipal'", (date,)
    ).fetchone()
    if row:
        existing = (row["content"] or "").strip()
        already_rich = len(existing) >= 1500 or (row["metadata"] and "pdf_url" in row["metadata"])
        if already_rich:
            return "skip"
        merged = (existing + "\n\n— — —\n\n" if existing else "") + body
        c.execute("UPDATE events SET content=?, metadata=?, source_url=? WHERE id=?",
                  (merged[:60000], meta, cr_url, row["id"]))
        return "enrich"
    c.execute("INSERT INTO events (type,date,title,content,source,source_url,metadata) "
              "VALUES ('conseil_municipal',?,?,?,?,?,?)",
              (date, f"PV du CM du {date}", body, SOURCE, cr_url, meta))
    return "new"


def _own_del_count(txt: str, prefix: str | None) -> int:
    """Nombre de n° DEL appartenant au CM courant (préfixe AAMM)."""
    from collections import Counter
    counts = Counter(m.group(1) for m in DEL_RE.finditer(txt))
    if not counts:
        return 0
    if prefix and prefix in counts:
        return counts[prefix]
    return 0  # aucun DEL du mois courant → ce n'est pas une liasse d'extraits


# ── Orchestration d'un CR ──────────────────────────────────────────────────────

def process_cr(*, cr_url: str | None, pdf_url: str | None, file: str | None,
               date: str | None, lang: str, umbrella: bool, commit: bool) -> dict:
    if file:
        pdf = Path(file).resolve()
        if not pdf.exists():
            sys.exit(f"Fichier introuvable : {file}")
        # PAS de `pdf.as_uri()` en repli : un `file:///Users/...` finissait en
        # `source_url` des délibérations, c'est-à-dire un lien mort côté public
        # et une fuite de l'arborescence personnelle. Le cas s'était déjà produit
        # (repris une fois en base) et retraiter un PDF en cache avec
        # `--file` réécrasait la réparation. Sans URL publique, on n'en écrit pas.
        if not pdf_url:
            print("  ⚠ --file sans --pdf-url : aucune URL publique ne sera "
                  "enregistrée (préciser --url ou --pdf-url pour la source en ligne).")
    else:
        if cr_url and not pdf_url:
            pdf_url = _pdf_link_from_cr(cr_url)
            if not pdf_url:
                sys.exit(f"Aucun lien PDF trouvé sur {cr_url}")
        if not pdf_url:
            sys.exit("Fournir --url, --pdf-url ou --file.")
        print(f"  PDF : {pdf_url}")
        pdf = download_pdf(pdf_url, date)

    txt = ensure_text(pdf, lang)
    date = date or _derive_date(txt, cr_url or pdf_url)
    if not date:
        sys.exit("Date du CM introuvable — préciser --date AAAA-MM-JJ.")
    cr_url = cr_url or pdf_url
    prefix = _prefix_from_date(date)

    # Auto-détection : liasse d'extraits (≥2 n° DEL du mois courant) vs PV verbatim.
    is_pv = _own_del_count(txt, prefix) < 2
    if is_pv:
        print(f"  CM {date} : procès-verbal verbatim (pas de liasse d'extraits DEL) "
              f"→ 1 event conseil_municipal, texte intégral, pas de découpage.")
        if not commit:
            return {"date": date, "mode": "pv", "committed": False}
        with transaction() as conn:
            st = _upsert_pv(conn, date, cr_url, pdf_url, txt)
        print(f"  ✓ PV {st} (event conseil_municipal).")
        return {"date": date, "mode": "pv", "pv": st, "committed": True}

    delibs = parse_deliberations(txt, prefix)
    print(f"  CM {date} : {len(delibs)} délibérations")
    for d in delibs:
        v = "unanimité" if (d["vote"] or {}).get("unanimite") else \
            (f"{d['vote']['pour']} pour" if d["vote"] and d["vote"].get("pour") else "—")
        print(f"    [{d['categorie']:20}] {d['titre'][:64]}  · vote={v}")

    if not commit:
        return {"date": date, "mode": "delibs", "delibs": len(delibs), "committed": False}

    n_new = n_maj = 0
    with transaction() as conn:
        for d in delibs:
            st = _upsert_delib(conn, date, cr_url, pdf_url, d)
            n_new += st == "new"; n_maj += st == "maj"
        umb = _upsert_umbrella(conn, date, cr_url, pdf_url, delibs) if umbrella else "off"
    print(f"  ✓ {n_new} créées, {n_maj} MAJ, ombrelle={umb}")
    return {"date": date, "mode": "delibs", "delibs": len(delibs),
            "new": n_new, "maj": n_maj, "committed": True}


# ── Découverte des CR non encore parsés ────────────────────────────────────────

def discover(limit: int = 0) -> list[dict]:
    """PV catalogués sur le site dont aucune délibération n'est en base.

    La version d'origine lisait une page de listing puis, pour les comptes
    rendus délistés, balayait des dizaines de milliers d'URL à la recherche de
    slugs plausibles (`/CR/conseil-municipal-du-3-mars-2015`). Ici tous les PV
    depuis 2004 sont liés depuis la même page : le catalogue suffit, et le
    balayage a été supprimé plutôt que réécrit.
    """
    from .connecteurs import charger

    conn = get_conn(read_only=True)
    out = []
    for pv in charger().catalogue_pv('commune'):
        n = conn.execute(
            "SELECT COUNT(*) FROM events WHERE date=? AND type='deliberation'",
            (pv.date,),
        ).fetchone()[0]
        if n == 0:
            out.append({"cr_url": pv.url, "pdf_url": pv.url, "date": pv.date})
        if limit and len(out) >= limit:
            break
    conn.close()
    return out


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Collecteur CR CM en PDF scanné (OCR).")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--url", help="Page de compte rendu (le PDF est détecté)")
    src.add_argument("--pdf-url", help="URL directe du PDF")
    src.add_argument("--file", help="Chemin d'un PDF local")
    src.add_argument("--discover", action="store_true",
                     help="Repère les CR non encore parsés (via la page listing)")
    ap.add_argument("--date", help="Date du CM AAAA-MM-JJ (sinon auto)")
    ap.add_argument("--lang", default="fra", help="Langue OCR tesseract (défaut fra)")
    ap.add_argument("--no-umbrella", action="store_true", help="Ne pas créer l'event CM ombrelle")
    ap.add_argument("--limit", type=int, default=0, help="Max de CR en mode --discover")
    ap.add_argument("--commit", action="store_true", help="Écrit en base (sinon dry-run)")
    args = ap.parse_args()
    if args.commit:
        ap.error("--commit n'écrit plus ici : lancer `python3 -m collectors.conseils --ocr` "
                 "(découpage à la page, identité d'acte par `cle_acte`)")

    if args.discover:
        cands = discover(limit=args.limit)
        print(f"[cm_ocr] {len(cands)} CR sans délibération en base :")
        for c in cands:
            print(f"  {c['date']}  {c['cr_url']}")
        for c in cands:
            print(f"\n→ {c['pdf_url']}")
            process_cr(cr_url=None, pdf_url=c["pdf_url"], file=None, date=c["date"],
                       lang=args.lang, umbrella=not args.no_umbrella, commit=args.commit)
        if not args.commit:
            print("\n(dry-run — ajouter --commit pour écrire)")
        return

    process_cr(cr_url=args.url, pdf_url=args.pdf_url, file=args.file, date=args.date,
               lang=args.lang, umbrella=not args.no_umbrella, commit=args.commit)
    if not args.commit:
        print("(dry-run — ajouter --commit pour écrire)")


if __name__ == "__main__":
    main()
