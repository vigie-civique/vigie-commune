"""
guichets.py — Les guichets dont relève la commune (Annuaire de l'administration, DILA).

Où renouveler une carte d'identité, à quelle CPAM écrire, quel tribunal
judiciaire est compétent : l'Annuaire de l'administration de Service-Public
(DILA) le dit commune par commune. Sa « base de données locales » publie deux
fichiers dans une même archive : la COMPÉTENCE (pour chaque commune et chaque
type de guichet, les identifiants des services qui la couvrent) et les
SERVICES eux-mêmes (nom, adresse, téléphone, fiche de l'annuaire). Ce
collecteur ne garde que quelques types, ceux qu'un habitant cherche.

─────────────────────────────────────────────────────────────────────────────
UN TYPE SANS GUICHET N'EST PAS UN TYPE ABSENT DU TERRITOIRE

L'annuaire ne rattache pas tout. La CAF en est l'exemple : chaque accueil n'y
est compétent que pour sa propre commune, si bien qu'une commune sans accueil
ne relève d'« aucune » CAF. L'export porte donc chaque type retenu, guichet ou
non, et la page dit « l'annuaire n'en rattache aucun » — jamais « il n'y en a
pas », cf. [[feedback-un-zero-qui-vient-dune-absence]].

AUCUNE PERSONNE

Chaque fiche peut porter `affectation_personne` (responsables nommés, avec
leur courriel) et des adresses électroniques parfois nominatives. Ni l'un ni
l'autre n'est LU : l'index du magasin ne retient que des champs de service.

UNE SOURCE QUI COUPE

Les serveurs de la DILA ferment parfois la connexion (step `justice`). Un
téléchargement manqué n'est pas un échec de passe : l'index précédent du
magasin sert. Sans index (premier passage coupé), le step est en ÉCHEC, sans
toucher à ce que la base contient déjà : rendu « vide », il passerait pour
abouti et la boucle ne le retenterait qu'au bout du TTL (cas du 20/10/2026).

Source : data.gouv.fr, jeu du Premier ministre (DILA), Licence Ouverte v2.
Mention exigée : Service-Public.gouv.fr / DILA, l'URL de téléchargement, le
nom et la date du fichier — tous trois exportés jusqu'à la page. L'archive
nationale (365 Mo en bzip2) est lue en flux ; seul un index réduit aux types
retenus est déposé au magasin partagé.

Usage :
  python3 -m collectors.guichets
  python3 -m collectors.guichets --insee 30140
  python3 -m collectors.guichets --stats
"""
import argparse
import json
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile

from .archive import HEADERS, fetch_json
from .config import NATIONAL_STORE, communes_du_step
from .db import get_conn, transaction
from .national_store import ecrire_atomiquement, est_frais

SOURCE = "guichets"
DATASET = "53699fe4a3a729239d206227"
PAGE_JEU = ("https://www.data.gouv.fr/datasets/"
            "service-public-gouv-fr-annuaire-de-ladministration-base-de-donnees-locales")
CACHE = NATIONAL_STORE / "guichets"
COMPETENCES = CACHE / "competences.json"   # {insee: {type: [id, …]}}
SERVICES = CACHE / "services.json"         # {id: {nom, adresse, …}}
META = CACHE / "meta.json"                 # fichier, date, url — écrit EN DERNIER

# L'archive est republiée chaque nuit ; un guichet ne déménage pas chaque mois.
CACHE_JOURS = 30

# Code de type de la DILA → libellé affiché, dans l'ordre de la page. Le code
# `tresorerie` désigne aujourd'hui les services de gestion comptable (SGC).
TYPES = {
    "mairie":          "Mairie",
    "france_services": "France services",
    "cpam":            "Assurance maladie (CPAM)",
    "caf":             "Allocations familiales (CAF)",
    "france_travail":  "France Travail",
    "prefecture":      "Préfecture",
    "sous_pref":       "Sous-préfecture",
    "tresorerie":      "Trésorerie (service de gestion comptable)",
    "tgi":             "Tribunal judiciaire",
}

# L'archive nomme ses fichiers par horodatage : on les reconnaît par la fin.
_ZIP_COMMUNES = re.compile(r"data\.gouv_commune\.zip$")
_JSON_SERVICES = re.compile(r"data\.gouv_local\.json$")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})_\d{6}-")

# Ce qu'une coupure du serveur peut lever en cours de lecture.
_COUPURES = (OSError, EOFError, urllib.error.URLError, tarfile.TarError,
             zipfile.BadZipFile, ValueError)


def _premier(liste, cle="valeur") -> str | None:
    for el in liste or []:
        v = (el.get(cle) or "").strip()
        if v:
            return v
    return None


def _adresse(adresses) -> str | None:
    """L'adresse physique (pas la boîte postale), en une ligne."""
    for a in adresses or []:
        if a.get("type_adresse") != "Adresse":
            continue
        voie = ", ".join(p for p in (a.get("complement1", "").strip(),
                                     a.get("numero_voie", "").strip()) if p)
        ville = " ".join(p for p in (a.get("code_postal", "").strip(),
                                     a.get("nom_commune", "").strip()) if p)
        return ", ".join(p for p in (voie, ville) if p) or None
    return None


def service_public(s: dict) -> dict:
    """Les seuls champs repris d'une fiche : ceux du SERVICE, aucun d'une personne."""
    return {
        "nom": (s.get("nom") or "").strip(),
        "commune": s.get("code_insee_commune"),
        "adresse": _adresse(s.get("adresse")),
        "telephone": _premier(s.get("telephone")),
        "site": _premier(s.get("site_internet")),
        "fiche": s.get("url_service_public"),
        "maj": (s.get("date_modification_datetime") or "")[:10] or None,
    }


def lire_competences(zf: zipfile.ZipFile) -> dict[str, dict[str, list[str]]]:
    """Un fichier par commune ; seuls les types retenus sont gardés."""
    index = {}
    for nom in zf.namelist():
        if not nom.endswith(".json"):
            continue
        for c in json.loads(zf.read(nom)).get("commune", []):
            types = {t["code_type_service_local"]: list(t.get("organisme") or [])
                     for t in c.get("type_service_local", [])
                     if t.get("code_type_service_local") in TYPES}
            index[c["code_insee_commune"]] = types
    return index


def lire_services(flux, ids: set[str]) -> dict[str, dict]:
    """Lit le fichier des services EN FLUX (270 Mo d'un bloc = 1,5 Go de mémoire)."""
    import ijson
    retenus = {}
    for s in ijson.items(flux, "service.item"):
        if s.get("id") in ids and str(s.get("statut_de_diffusion")).lower() == "true":
            retenus[s["id"]] = service_public(s)
    return retenus


def _url_archive() -> str:
    fiche = fetch_json(f"https://www.data.gouv.fr/api/1/datasets/{DATASET}/",
                       source=SOURCE, timeout=60)
    for res in fiche.get("resources", []):
        url = res.get("url") or ""
        if url.endswith(".tar.bz2"):
            return url
    raise RuntimeError("Annuaire : pas d'archive de la base locale au catalogue")


def _telecharger() -> None:
    """Lit l'archive nationale en flux et dépose l'index réduit au magasin."""
    url = _url_archive()
    competences = services = fichier = None
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=300) as r, \
            tarfile.open(fileobj=r, mode="r|bz2") as tar:
        for membre in tar:
            if not membre.isfile():
                continue
            flux = tar.extractfile(membre)
            if _ZIP_COMMUNES.search(membre.name):
                # Un zip se lit par la fin : il faut le poser sur disque.
                with tempfile.TemporaryFile() as tmp:
                    shutil.copyfileobj(flux, tmp, 1 << 20)
                    tmp.seek(0)
                    with zipfile.ZipFile(tmp) as zf:
                        competences = lire_competences(zf)
            elif _JSON_SERVICES.search(membre.name):
                if competences is None:
                    raise RuntimeError("Annuaire : services avant compétences, ordre inattendu")
                ids = {i for types in competences.values() for l in types.values() for i in l}
                services = lire_services(flux, ids)
                fichier = membre.name.lstrip("./")
    if competences is None or services is None:
        raise RuntimeError("Annuaire : l'archive ne porte pas ses deux fichiers")
    m = _DATE.search(fichier)
    meta = {"fichier": fichier, "date": m[1] if m else None, "url": url}
    for chemin, contenu in ((COMPETENCES, competences), (SERVICES, services), (META, meta)):
        ecrire_atomiquement(chemin, json.dumps(contenu, ensure_ascii=False).encode())
    print(f"  [guichets] index déposé : {len(competences)} communes, "
          f"{len(services)} guichets ({fichier})")


def _index() -> tuple[dict, dict, dict] | None:
    """L'index du magasin, rafraîchi s'il a vieilli ; None s'il n'y en a aucun."""
    if not est_frais(META, CACHE_JOURS):
        try:
            _telecharger()
        except _COUPURES as e:
            print(f"  ⚠ [guichets] annuaire injoignable ({type(e).__name__}: {e})"
                  + (" — index précédent réutilisé" if META.is_file() else ""))
    if not META.is_file():
        return None
    return (json.loads(COMPETENCES.read_text()), json.loads(SERVICES.read_text()),
            json.loads(META.read_text()))


def lignes_de(insee: str, competences: dict, services: dict) -> list[tuple]:
    """(insee, type, id, nom, commune, adresse, telephone, site, fiche, maj)."""
    lignes = []
    for type_, ids in (competences.get(insee) or {}).items():
        for i in ids:
            s = services.get(i)
            if s and s["nom"]:
                lignes.append((insee, type_, i, s["nom"], s["commune"], s["adresse"],
                               s["telephone"], s["site"], s["fiche"], s["maj"]))
    return lignes


def ensure_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS guichets (
            insee        TEXT NOT NULL,   -- la commune desservie
            type         TEXT NOT NULL,   -- code de type de la DILA (mairie, cpam…)
            service_id   TEXT NOT NULL,   -- identifiant de la fiche de l'annuaire
            nom          TEXT NOT NULL,
            commune      TEXT,            -- code INSEE où le guichet est installé
            adresse      TEXT,
            telephone    TEXT,
            site         TEXT,
            fiche_url    TEXT,
            maj          TEXT,            -- dernière mise à jour de la fiche
            releve_le    TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (insee, type, service_id)
        );
        -- Une ligne par commune trouvée dans le fichier : c'est elle qui dit
        -- qu'un type sans guichet a été CHERCHÉ, et quel fichier citer.
        CREATE TABLE IF NOT EXISTS guichets_releves (
            insee        TEXT PRIMARY KEY,
            fichier      TEXT,
            date_fichier TEXT,
            source_url   TEXT,
            releve_le    TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def run(insee: str | None = None) -> int:
    index = _index()
    if index is None:
        # Un `return 0` serait noté `empty`, donc abouti : la source paraîtrait
        # fraîche 30 jours sans avoir rien relevé. L'échec la fait retenter demain.
        raise RuntimeError("Annuaire injoignable et aucun index au magasin : rien n'est relevé")
    competences, services, meta = index
    codes = {insee} if insee else set(communes_du_step(SOURCE))
    total = 0
    with transaction() as conn:
        ensure_table(conn)
        for code in sorted(codes):
            if code not in competences:
                print(f"  [guichets] {code} absente du fichier")
                continue
            # La compétence change (un accueil ferme) : on remplace, on n'empile pas.
            conn.execute("DELETE FROM guichets WHERE insee=?", (code,))
            lignes = lignes_de(code, competences, services)
            conn.executemany(
                "INSERT INTO guichets (insee, type, service_id, nom, commune, adresse,"
                " telephone, site, fiche_url, maj) VALUES (?,?,?,?,?,?,?,?,?,?)", lignes)
            conn.execute(
                "INSERT OR REPLACE INTO guichets_releves (insee, fichier, date_fichier, source_url)"
                " VALUES (?,?,?,?)", (code, meta["fichier"], meta["date"], meta["url"]))
            sans = [t for t in TYPES if t not in {l[1] for l in lignes}]
            print(f"  [guichets] {code} : {len(lignes)} guichet(s)"
                  + (f", aucun rattaché pour {', '.join(sans)}" if sans else ""))
            total += len(lignes)
    return total


def stats():
    conn = get_conn()
    ensure_table(conn)
    for r in conn.execute("""
        SELECT insee, type, COUNT(*) n FROM guichets GROUP BY insee, type ORDER BY insee, type
    """):
        print(f"  {r['insee']} {r['type']:16} {r['n']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--insee", help="une seule commune")
    p.add_argument("--stats", action="store_true")
    a = p.parse_args()
    if a.stats:
        stats()
    else:
        run(a.insee)
