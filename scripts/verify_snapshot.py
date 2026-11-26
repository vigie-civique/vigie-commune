#!/usr/bin/env python3
"""Contrôleur d'étanchéité du snapshot public — écrit comme un adversaire.

Indépendant de build_public_snapshot.py : il ne partage aucun code avec le
builder, lit config/publication_rules.json et attaque le répertoire publié.
Si le builder et le contrôleur ont le même bug, la fuite passe — c'est
précisément pourquoi ce fichier ne doit jamais importer le builder ni être
« aligné » sur lui. Toute assertion retirée ici est une décision éditoriale.

Usage :
    venv/bin/python3 scripts/verify_snapshot.py [DIR ...] [--json]
    (défaut : dashboard/static/public_api, public/static/data, public/build)

Sortie : rapport groupé par règle, exit 1 à la moindre violation bloquante.
Avec `--json`, le même rapport sort en JSON, chaque cas décomposé (fichier,
champ, identifiants) pour que l'atelier puisse renvoyer vers la fiche fautive.
Le rapport texte intégral y figure sous `rapport` : le format machine n'est pas
un résumé, il ne masque rien.
À câbler à la fin de toute génération/déploiement : un build qui fuit échoue.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# `VIGIE_RULES` — même surcharge que dans le builder : les tests tournent sur
# l'exemple versionné. Le contrôleur reste indépendant du builder, il lit
# seulement le même fichier de règles.
RULES = json.loads(Path(os.environ.get("VIGIE_RULES")
                        or ROOT / "config" / "publication_rules.json").read_text())

PUBLIC_CONFIDENCE = set(RULES["confidence"]["public"])
PRIVATE_MARKERS = [m.lower() for m in RULES["relations"]["private_markers"]]
RELATION_ALLOWLIST = set(RULES["relations"]["public_allowlist"]) | set(
    RULES["relations"].get("relevance_allowlist", [])
)
PUBLIC_EVENT_SOURCES = set(RULES["events"]["public_sources"])
EXCLUDED_EVENT_TYPES = set(RULES["events"]["exclude_types"])
GENERIC_DOMAINS = set(RULES["urls"]["exclude_generic_domains"])

# Fichiers qui n'ont RIEN à faire dans un répertoire publié.
#
# Les rebuts du système de fichiers sont là pour de bon : `.DS_Store` traîne dans
# n'importe quel répertoire ouvert dans le Finder, et il porte les noms de TOUS
# les fichiers que le dossier a contenus — y compris ceux qu'on a retirés du
# public. Un snapshot du 26/07 en portait un ; le contrôleur l'a laissé passer,
# et il serait parti chez l'hébergeur. Les `._machin` (AppleDouble) sont le même
# problème sur un volume non-HFS, `Thumbs.db` son équivalent Windows.
FORBIDDEN_FILENAMES = re.compile(
    r"(^review_report.*\.json$|^qa_report.*\.json$|\.db$|\.sqlite3?$|^\.env"
    r"|^\.DS_Store$|^\._|^Thumbs\.db$)", re.I
)
# Chaînes qui trahissent une fuite d'environnement local ou de secret.
LEAKY_STRINGS = re.compile(
    r"file://|/Users/|/home/|sk-ant-|api[_-]?key|X-Admin-Key|Bearer\s+ey", re.I
)
# Encodage abîmé parti en ligne : « L'EUZIAÃÂRE » s'affichait dans le fil
# d'accueil du site public le 23/08/2026. La règle est volontairement dupliquée
# ici plutôt qu'importée de `collectors.nom_normalise` — ce fichier ne doit
# jamais dépendre du code qu'il contrôle, sans quoi un défaut du builder se
# contrôlerait lui-même.
#
# Le motif est une SÉQUENCE, jamais un caractère isolé : « CHÂTEAU DE LA
# TOUR », « THÉÂTRE » et « Âne » sont du français correct et figurent dans le
# corpus. Une règle qui bloquerait sur « Â » seul refuserait de publier la
# moitié des lieux-dits.
_CONT = ("\x80-\xbf"                     # continuations rendues par latin-1
         "\u20ac\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160"
         "\u2039\u0152\u017d\u2018\u2019\u201c\u201d\u2022\u2013\u2014"
         "\u02dc\u2122\u0161\u203a\u0153\u017e\u0178")   # …et par cp1252
MOJIBAKE = re.compile(
    f"[\xc2\xc3][{_CONT}]"              # utf-8 à deux octets relu en latin-1
    f"|\xe2[{_CONT}]{{2}}"               # à trois octets : « â€™ », « â€" »
    "|A[\x80-\x96\x98-\x9f]"          # repli Sitadel : « Ã » dépilé en « A »
    "|\ufffd"                            # caractère de remplacement
)
# Clés interdites où qu'elles apparaissent dans un objet publié.
FORBIDDEN_KEYS = {"personnes_citees", "birth_date", "date_naissance", "adresse_personnelle",
                  "birth_year", "annee_naissance"}
# Nuance politique attribuée par le préfet : article 9 du RGPD, hors 9.2.e
# (avis DPO du 17/11/2026). Refusée dans les seuls fichiers des élections, pas
# en clé interdite partout : recherche_texte.json a pour clés les mots des
# actes, et « nuance » est un mot courant ; un acte qui l'emploie aurait fait
# refuser toute la publication (relecture de #304).
NUANCE = "nuance"


def check_nuance(data, rep, loc):
    listes = data.get("listes") if isinstance(data, dict) else None
    for liste in listes if isinstance(listes, list) else []:
        if isinstance(liste, dict) and NUANCE in liste:
            rep.error("nuance politique publiée", f"{loc}: liste {liste.get('libelle')!r}")
            return

# ── Ce que le TEXTE publié ne doit pas dire ──────────────────────────────────
# Jusqu'au 06/10/2026 ce contrôleur ne lisait aucun contenu : des clés, des
# types, des identifiants. Un snapshot piégé — vingt-huit marqueurs de naissance
# et de domicile semés dans les titres, les notes, les objets de marché — en
# ressortait « OK — étanchéité vérifiée » (revue externe du 05/10).
#
# Deux voisinages, écrits ICI et pas importés du masque du builder : s'ils
# partageaient ses motifs, ils en partageraient les angles morts. Une naissance
# est « né(e) le » suivi d'une date, ou « date de naissance » suivie d'une date ;
# un domicile est un verbe de domicile suivi, dans la même phrase, d'un numéro
# de voie. Ce que le builder a masqué (« né le [date masquée] ») ne porte plus
# de date et ne déclenche rien.
_MOIS = (r"(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre"
         r"|octobre|novembre|d[ée]cembre)")
_DATE = rf"(?:\d{{1,2}}\s*[/.\-]\s*\d{{1,2}}\s*[/.\-]\s*\d{{4}}|\d{{1,2}}(?:er)?\s+{_MOIS}\s+\d{{4}})"
_VOIE = (r"(?:grande\s+rue|rue|chemin|all[ée]e|impasse|route|avenue|place|boulevard"
         r"|quai|lotissement|mont[ée]e|traverse|passage|hameau)")
TEXTES_INTERDITS = (
    ("date de naissance publiée", re.compile(
        rf"\bn[ée](?:\(e\)|e)?\s+le\s+{_DATE}|\bdate\s+de\s+naissance\s*:?\s*{_DATE}", re.I)),
    ("domicile publié", re.compile(
        r"\b(?:domicili[ée]e?s?|demeurant|r[ée]sidant)\b[^.;\[\"<]{0,60}?"
        rf"\b\d{{1,4}}\s*(?:bis|ter)?\s*,?\s*{_VOIE}\b", re.I)),
    # Sans voie : une civilité, un verbe de domicile, puis une commune suivie de
    # son code postal — « Madame X, son épouse, demeurant au moment de la
    # transaction à Testonville (99001} » est sorti en clair le 26/10/2026. La
    # civilité écarte le siège d'une personne morale (« l'association
    # domiciliée à Testonville (99001) »).
    ("domicile publié", re.compile(
        r"(?:\bM\.|\bMM\.|\bMmes?\b|\bMonsieur\b|\bMadame\b)[^.;\[\"<]{0,90}?"
        r"\b(?i:domicili[ée]e?s?|demeurant|r[ée]sidant)\b[^.;\[\"<]{0,60}?"
        r"\b(?:à|au|en)\s+[A-ZÀ-Þ][^.;\[\"<(]{0,40}?[({]\s*\d{2}\s?\d{3}\s*[)}\]]")),
)
# Clés à signaler (non bloquant) si elles portent une valeur.
SUSPECT_KEYS = {"email", "mail", "telephone", "phone", "tel"}

MAX_EXAMPLES = 8
# Cas détaillés par règle dans la sortie `--json`. Au-delà, seul le compte est
# rendu : l'atelier affiche « 1 229 cas », pas 1 229 lignes.
MAX_CAS = 50

# Un exemple est écrit `<fichier>: <détail>` partout, sauf pour les renvois
# sortants qui portent `<fichier> · <champ> : <détail>`. Ces deux formes sont le
# contrat de sortie du contrôleur ; les relire ici lui évite d'être réécrit à
# vingt endroits pour ajouter un identifiant à chaque appel.
_RE_FICHE_ENTITE = re.compile(r"(?:^|/)entite/(\d+)\.json")
_RE_ID = re.compile(r"\bid=(\d+)")
_RE_IDS_LISTE = re.compile(r"ex\. \[([0-9,\s]+)\]")


def analyser_cas(exemple):
    """Décompose un exemple en fichier, champ, objet visé et identifiants.

    Rien n'est deviné : ce qui n'est pas reconnu reste `None`, et `message`
    porte toujours l'exemple entier. Un contrôle mal décomposé ne doit pas
    devenir un contrôle amputé.
    """
    texte = str(exemple)
    fichier = champ = None
    reste = texte
    if " · " in texte:
        fichier, _, apres = texte.partition(" · ")
        champ, _, reste = apres.partition(" : ")
        champ = champ.strip() or None
    elif ": " in texte:
        fichier, _, reste = texte.partition(": ")
    else:
        fichier = texte
    fichier = (fichier or "").strip() or None

    identifiants = []
    objet = None
    m = _RE_FICHE_ENTITE.search(fichier or "")
    if m:
        objet, identifiants = "entite", [int(m.group(1))]
    else:
        liste = _RE_IDS_LISTE.search(reste)
        if liste:
            objet = "entite"      # tous les champs de CHAMPS_RENVOI visent une entité
            identifiants = [int(n) for n in liste.group(1).replace(" ", "").split(",") if n]
        else:
            ids = _RE_ID.findall(reste)
            if ids:
                identifiants = [int(n) for n in ids]
                objet = "evenement" if (fichier or "").startswith("events") else None

    return {"message": texte, "fichier": fichier, "champ": champ,
            "objet": objet, "identifiants": identifiants}


class Report:
    def __init__(self):
        self.errors = {}   # règle -> [exemples]
        self.warnings = {}
        self.files = 0

    def _add(self, bucket, rule, example):
        bucket.setdefault(rule, []).append(example)

    def error(self, rule, example):
        self._add(self.errors, rule, example)

    def warn(self, rule, example):
        self._add(self.warnings, rule, example)

    def lignes(self):
        out = []
        for label, bucket in (("BLOQUANT", self.errors), ("avertissement", self.warnings)):
            for rule, examples in sorted(bucket.items()):
                out.append(f"[{label}] {rule} — {len(examples)} cas")
                for ex in examples[:MAX_EXAMPLES]:
                    out.append(f"    {ex}")
                if len(examples) > MAX_EXAMPLES:
                    out.append(f"    … {len(examples) - MAX_EXAMPLES} autres")
        return out

    def dump(self):
        for ligne in self.lignes():
            print(ligne)

    def groupes(self, bucket):
        """Le même contenu, décomposé — une entrée par règle, ses cas dedans.

        Le rapport texte reste la référence : ceci n'en retire rien, il ajoute
        ce qu'une interface peut suivre. `total` compte TOUS les cas, `cas` en
        détaille au plus MAX_CAS — une règle violée 1 229 fois se dit en un
        nombre, pas en 1 229 lignes envoyées au navigateur.
        """
        return [
            {
                "regle": regle,
                "total": len(exemples),
                "cas": [analyser_cas(ex) for ex in exemples[:MAX_CAS]],
            }
            for regle, exemples in sorted(bucket.items())
        ]


def domain_of(url):
    m = re.match(r"https?://([^/]+)", str(url))
    # `removeprefix`, pas `lstrip("www.")` : lstrip retire des CARACTÈRES, et
    # « web.fr » devenait « eb.fr ».
    return m.group(1).lower().removeprefix("www.") if m else None


# Le site de la commune n'est le site d'aucune entité tierce : il est générique
# pour l'instance, que `publication_rules.json` le dise ou non. Lasalle l'avait
# écrit à la main, Saillans et Brassac non. Lu dans `instance.json` directement
# — le contrôleur n'importe pas la config du moteur.
_INSTANCE = Path(os.environ.get("VIGIE_INSTANCE") or ROOT / "config" / "instance.json")
COMMUNE_DOMAIN = (domain_of(json.loads(_INSTANCE.read_text()).get("commune_url", ""))
                  if _INSTANCE.is_file() else None)
if COMMUNE_DOMAIN:
    GENERIC_DOMAINS.add(COMMUNE_DOMAIN)


def sans_site_de_la_mairie(data):
    """territoire.json, moins le site du guichet de la mairie de la commune.

    Seule exception à la règle des domaines génériques : la mairie sur place
    porte le site de la commune, c'est le sien. Une mairie d'ailleurs, ou un
    autre domaine, reste contrôlé.
    """
    g = data.get("guichets") if isinstance(data, dict) else None
    if not isinstance(g, dict) or not COMMUNE_DOMAIN:
        return data
    types = []
    for t in g.get("types") or []:
        if isinstance(t, dict) and t.get("type") == "mairie":
            t = {**t, "guichets": [
                {k: v for k, v in x.items() if k != "site"}
                if isinstance(x, dict) and x.get("sur_place") is True
                and domain_of(x.get("site")) == COMMUNE_DOMAIN else x
                for x in t.get("guichets") or []]}
        types.append(t)
    return {**data, "guichets": {**g, "types": types}}


# Clés sous lesquelles une URL est un site ATTRIBUÉ à une entité (la règle des
# domaines génériques ne vise que ça — pas les citations de source page_url etc.)
# (`url`/`page_url`/`source_url` sont des citations d'actes ou d'articles, pas
# des sites attribués — actualite.json cite lasalle.fr en permanence, c'est licite.)
ENTITY_URL_KEYS = {"urls", "website", "site"}


def walk(node, path, rep, loc, parent_key=None):
    """Parcourt récursivement tout objet JSON et applique les règles de fond."""
    if isinstance(node, dict):
        for key in node:
            if key in FORBIDDEN_KEYS:
                rep.error("clé interdite publiée", f"{loc}: clé '{key}'")
            elif key.lower() in SUSPECT_KEYS and node[key]:
                rep.warn("clé de contact avec valeur", f"{loc}: {key}={node[key]!r}")

        conf = node.get("confidence")
        if conf is not None and conf not in PUBLIC_CONFIDENCE:
            rep.error(
                "confidence hors whitelist publique",
                f"{loc}: confidence={conf!r} name={node.get('name') or node.get('title')!r}",
            )

        rtype = node.get("relation_type")
        if rtype is not None:
            low = str(rtype).lower()
            if any(m in low for m in PRIVATE_MARKERS):
                rep.error("relation avec marqueur privé", f"{loc}: {rtype!r}")
            elif rtype not in RELATION_ALLOWLIST:
                rep.error("relation_type hors allowlist", f"{loc}: {rtype!r}")
        role = node.get("role")
        if role and any(m in str(role).lower() for m in PRIVATE_MARKERS):
            rep.error("rôle avec marqueur privé", f"{loc}: {role!r}")

        if node.get("type") == "person" or node.get("entity_type") == "person":
            if node.get("lat") or node.get("lng") or node.get("coordinates"):
                rep.error(
                    "personne avec coordonnées",
                    f"{loc}: {node.get('name')!r} lat={node.get('lat')} lng={node.get('lng')}",
                )
        if node.get("has_public_location") is False and (node.get("lat") or node.get("lng")):
            rep.error(
                "coordonnées publiées malgré has_public_location=false",
                f"{loc}: {node.get('name')!r}",
            )

        for key, value in node.items():
            walk(value, path, rep, loc, parent_key=key)
    elif isinstance(node, list):
        for item in node:
            walk(item, path, rep, loc, parent_key=parent_key)
    elif isinstance(node, str):
        if parent_key in ENTITY_URL_KEYS and node.startswith(("http://", "https://")):
            dom = domain_of(node)
            if dom and any(dom == g or dom.endswith("." + g) for g in GENERIC_DOMAINS):
                rep.error("URL de domaine générique attribuée à une entité", f"{loc}: {node}")


def check_events_file(data, rep, loc):
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        return
    for ev in events:
        src = ev.get("source")
        if src is not None and src not in PUBLIC_EVENT_SOURCES:
            rep.error("event de source hors allowlist", f"{loc}: id={ev.get('id')} source={src!r}")
        if ev.get("type") in EXCLUDED_EVENT_TYPES:
            rep.error("event de type exclu", f"{loc}: id={ev.get('id')} type={ev.get('type')!r}")


def check_file(fp, rep, base):
    loc = str(fp.relative_to(base))
    raw = fp.read_text(errors="replace")
    for m in set(LEAKY_STRINGS.findall(raw)):
        # Retrouver un extrait pour le rapport
        idx = raw.lower().find(m.lower())
        rep.error("chaîne locale ou secret", f"{loc}: …{raw[max(0, idx - 20):idx + 60]!r}…")
    # Bloquant, pas un avertissement : un nom de lieu abîmé est faux, il est
    # recopié tel quel par ceux qui citent le site, et il se corrige en amont
    # (`collectors.nom_normalise.reparer_encodage`) — pas à l'affichage.
    for m in MOJIBAKE.finditer(raw):
        idx = m.start()
        rep.error("encodage abîmé publié",
                  f"{loc}: …{raw[max(0, idx - 30):idx + 30]!r}…")
        break   # une occurrence suffit à refuser le fichier
    if fp.suffix in (".json", ".geojson", ".html"):
        for regle, motif in TEXTES_INTERDITS:
            m = motif.search(raw)
            if m:
                rep.error(regle, f"{loc}: …{raw[max(0, m.start() - 40):m.end() + 20]!r}…")
    if fp.suffix not in (".json", ".geojson"):
        return
    if fp.name == "__data.json":
        # Sérialisation SvelteKit `devalue` : clés→indices, illisible structurellement.
        # Le contenu vient du snapshot déjà contrôlé ; on se limite à un signal texte.
        for token in ('"hypothesis"', '"probable"', '"unverified"', '"retracted"'):
            if token in raw:
                rep.error("token de confidence privée dans un __data.json", f"{loc}: {token}")
        if fp.parent.name == "elections" and f'"{NUANCE}"' in raw:
            rep.error("nuance politique publiée", f"{loc}: clé '{NUANCE}'")
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        rep.error("JSON invalide", f"{loc}: {exc}")
        return
    if fp.name == "territoire.json":
        data = sans_site_de_la_mairie(data)
    if fp.name == "elections.json":
        check_nuance(data, rep, loc)
    walk(data, fp, rep, loc)
    if fp.name == "events.json":
        check_events_file(data, rep, loc)


# ── Invariant de périmètre ───────────────────────────────────────────────────
# Le site d'une commune publie les fiches de cette commune. Si la majorité des
# fiches publiées relève d'ailleurs, le classement C1/C2 n'a pas tourné et le
# site est devenu l'annuaire de l'intercommunalité — c'est arrivé le 14/08/2026
# sur deux instances neuves.
#
# Mesuré ce jour-là sur quatre snapshots : instances correctes 94,4 % et 94,6 %,
# instances non classées 23,8 % et 57,4 %. Le seuil bloquant est donc placé à la
# majorité simple, qui est aussi la règle éditoriale énonçable : un site
# communal doit publier majoritairement sa commune. L'avertissement à 80 %
# signale la dérive avant qu'elle ne devienne bloquante.
#
# Le contrôle lit `entity_index.json` et la commune déclarée dans les règles :
# il ne touche pas à la base et n'importe pas le builder — même principe que le
# reste de ce fichier. Il porte donc sur le RÉSULTAT publié, et attraperait une
# fuite même si le builder classait correctement puis publiait de travers.
PART_MIN_BLOQUANTE = 0.50
PART_MIN_AVERTISSEMENT = 0.80


def check_perimetre(base, rep):
    fp = base / "entity_index.json"
    if not fp.is_file():
        return
    commune = (RULES.get("project") or {}).get("commune")
    try:
        entities = json.loads(fp.read_text()).get("entities") or []
    except json.JSONDecodeError:
        return  # déjà signalé par check_file

    communes = [e.get("c") for e in entities if e.get("c")]
    if not communes:
        return
    if not commune:
        # Une configuration qui manque ferme, elle n'ouvre pas : des fiches
        # sortent, et rien ne dit de quelle commune elles devraient être.
        # C'était un avertissement, et c'était l'état de la CI — le contrôle
        # né du 14/08 n'y était donc jamais joué.
        rep.error("périmètre non vérifiable",
                  f"{fp.name}: {len(communes)} fiche(s) situées, et "
                  "publication_rules.project.commune absent")
        return
    par_commune = {}
    for c in communes:
        par_commune[c] = par_commune.get(c, 0) + 1
    part = par_commune.get(commune, 0) / len(communes)
    top, n_top = max(par_commune.items(), key=lambda kv: kv[1])

    detail = (f"{commune} = {par_commune.get(commune, 0)}/{len(communes)} "
              f"({part:.1%}) ; commune la plus publiée : {top} ({n_top})")
    if top != commune or part < PART_MIN_BLOQUANTE:
        rep.error(
            "le site publie majoritairement une autre commune "
            "(classement de périmètre non appliqué ?)", detail)
    elif part < PART_MIN_AVERTISSEMENT:
        rep.warn("part de la commune inférieure aux instances de référence", detail)


def check_statut(base, rep):
    """
    Un snapshot doit DIRE ce qu'est l'instance qui le produit.

    Sans ce contrôle, la quatrième instance naîtrait sans statut et publierait
    des pages muettes sur ce qu'elles sont — le défaut du 03/09 sur `origine`,
    à l'identique : un champ qu'on croit rempli parce qu'il l'est ailleurs.

    ⚠️ La liste des états est RECOPIÉE ici, à dessein. Ce contrôle est écrit
    comme un adversaire du générateur : s'il importait `collectors.statut`, il
    accepterait par construction tout ce que le générateur produit, y compris
    un état ajouté par erreur. Le contrôleur dit ce qu'il accepte ; c'est au
    moteur de s'y conformer.
    """
    ETATS_CONNUS = {"demonstration", "constitution", "tenue"}

    fp = base / "stats.json"
    if not fp.is_file():
        return
    try:
        stats = json.loads(fp.read_text())
    except json.JSONDecodeError:
        return  # déjà signalé par check_file

    s = stats.get("statut")
    if not isinstance(s, dict) or s.get("type") not in ETATS_CONNUS:
        rep.error(
            "le snapshot ne déclare pas le statut de l'instance",
            f"{fp.name}: statut.type attendu parmi {sorted(ETATS_CONNUS)}, "
            f"lu : {(s or {}).get('type') if isinstance(s, dict) else s!r}. "
            "Déclarer « statut » dans config/instance.json.")
        return

    # « Tenue » est la seule des trois qui promette une relecture humaine. Une
    # promesse sans titulaire n'engage personne : elle se nomme, ou elle ne se
    # dit pas.
    if s["type"] == "tenue" and not (s.get("tenue_par") or "").strip():
        rep.error(
            "l'instance se déclare tenue sans dire par qui",
            f"{fp.name}: statut.tenue_par est vide. Renseigner « tenue_par » "
            "dans config/instance.json, ou déclarer « constitution ».")

    if not s.get("derniere_collecte"):
        rep.warn(
            "statut annoncé sans date de dernière collecte",
            f"{fp.name}: un statut sans date est une promesse invérifiable — "
            "la table collector_runs est-elle vide ?")


def check_fiches_orphelines(base, rep):
    """Toute fiche servie doit correspondre à une entité publiée.

    Ce contrôle manquait, et son absence a laissé passer la seule vraie fuite du
    dispositif : le 19/08/2026, deux sites en ligne servaient des fiches
    `entite/<id>.json` d'entités écartées par le filtre, personnes physiques
    comprises. Le builder écrivait par-dessus l'ancien contenu sans purger, et la
    synchro miroir recopiait les périmées. Rien ne criait, parce que la PAGE de
    ces entités rendait bien 404 : seul le fichier de données restait joignable.

    D'où la règle que ce fichier applique partout ailleurs et qui valait ici
    aussi : contrôler ce qui est SERVI, jamais ce que le builder croit avoir
    produit. Le builder purge désormais — ce contrôle est là pour le cas où il
    cesserait de le faire.
    """
    dossier = base / "entite"
    fp = base / "entities.json"
    if not dossier.is_dir() or not fp.is_file():
        return
    try:
        entities = json.loads(fp.read_text()).get("entities") or []
    except json.JSONDecodeError:
        return  # déjà signalé par check_file
    publies = {str(e.get("id")) for e in entities}
    # Toutes signalées : c'est `Report.dump()` qui tronque l'affichage et annonce
    # le compte exact. Tronquer ici ferait mentir ce compte.
    for f in sorted(dossier.glob("*.json"), key=lambda p: p.stem):
        if f.stem not in publies:
            rep.error("fiche servie pour une entité non publiée", f"entite/{f.name}")


# Copies du builder, gardées identiques par un essai
# (`test_les_copies_du_controleur_sont_celles_du_builder`).
ROLES_SANS_FAIT = ("demandeur", "bénéficiaire", "beneficiaire")
# États d'un flux qui n'attestent aucun versement (`fiches.ETATS_SANS_VERSEMENT`).
ETATS_SANS_VERSEMENT = ("demande", "annule")
TYPES_ACTES_COLLECTIVITE = ("deliberation", "deliberation_cc", "decision_delegation",
                            "decision_delegation_cc")
DESIGNE_UNE_PERSONNE = re.compile(r"\b(un particulier|une particuli[eè]re|un agent|une agente"
                                  r"|une personne)\b", re.I)


def cle_de_nom(nom) -> str:
    """Copie de `fiches.cle_de_nom` (essai `test_les_copies_du_controleur_sont_celles_du_builder`)."""
    import hashlib
    import unicodedata
    s = unicodedata.normalize("NFKD", str(nom or "")).encode("ascii", "ignore").decode()
    s = " ".join(s.upper().replace("-", " ").split())
    return hashlib.sha256(s.encode()).hexdigest() if s else ""


# Copies du builder (`fiches.FICHIERS_EMPREINTE`, `fiches.empreinte_snapshot`,
# rétention), gardées identiques par un essai.
FICHIERS_EMPREINTE = ("entities.json", "flows.json", "marches.json")
RETENTION_RELEVES_JOURS = 30
RELEVES_EI_MINIMUM = 20


def empreinte_snapshot(base) -> str:
    import hashlib
    h = hashlib.sha256()
    for nom in FICHIERS_EMPREINTE:
        fp = Path(base) / nom
        h.update(nom.encode() + b"\0" + (fp.read_bytes() if fp.is_file() else b"") + b"\0")
    return h.hexdigest()


def lire_controle_ei(base, rep):
    """Le relevé de CE snapshot dans `audits/controle_ei.json`, que le builder
    écrit HORS du snapshot (`VIGIE_AUDITS` le déplace), sous l'empreinte du
    contenu servi (`empreinte_snapshot`).

    Un snapshot construit (il a son `stats.json`) sans relevé est une ERREUR : le
    contrôle des noms d'EI et des payeurs ne peut pas se faire, et le laisser
    passer en avertissement coupait le filet sans bruit (avis vigie-securite du
    25/11/2026). Le message dit pourquoi : fichier absent, ou aucun relevé pour
    cette empreinte (snapshot modifié après sa construction, construit par un
    autre moteur, ou relevé purgé). Sans `stats.json` (répertoire monté à la
    main), un avertissement et un contrôle dégradé (tout flux reçu compte, les
    noms ne sont pas vérifiés)."""
    chemin = Path(os.environ.get("VIGIE_AUDITS") or ROOT / "audits") / "controle_ei.json"
    if not (base / "entities.json").is_file():
        return None  # pas un snapshot de fiches : rien à contrôler
    construit = (base / "stats.json").is_file()
    regle = "relevé des entreprises individuelles absent pour ce snapshot"
    cause = None
    try:
        releves = json.loads(chemin.read_text(encoding="utf-8")).get("releves")
    except FileNotFoundError:
        releves = None
        cause = (f"{chemin} introuvable : construction sans l'étape `controle_ei`, ou "
                 "relevé écrit ailleurs (VIGIE_AUDITS)")
    except OSError as e:
        releves = None
        cause = f"{chemin} illisible ({e.__class__.__name__} : droits d'accès ?)"
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        releves = None
        cause = f"{chemin} illisible (JSON invalide, ou racine qui n'est pas un objet)"
    if isinstance(releves, dict):
        empreinte = empreinte_snapshot(base)
        controle = releves.get(empreinte)
        if isinstance(controle, dict):
            return controle
        dates = sorted(str(v.get("construit_le") or "?") for v in releves.values()
                       if isinstance(v, dict))
        cause = (f"aucun relevé pour l'empreinte {empreinte[:12]}… parmi {len(dates)}"
                 + (f" (construits du {dates[0]} au {dates[-1]})" if dates else "")
                 + " : snapshot modifié après sa construction, construit par un autre "
                 f"moteur, ou relevé purgé (rétention {RETENTION_RELEVES_JOURS} jours, "
                 f"{RELEVES_EI_MINIMUM} au moins). Reconstruire l'aperçu.")
    elif releves is None and cause is None:
        cause = f"{chemin} : clé « releves » absente ou nulle"
    elif releves is not None:
        cause = f"{chemin} : clé « releves » mal formée (un objet est attendu)"
    if construit:
        rep.error(regle, cause)
    else:
        rep.warn("snapshot sans stats.json : contrôle des EI dégradé", cause)
    return None


# Un nom publié et l'identifiant qui dit QUI il désigne.
NOMS_ET_RENVOIS = (("titulaire_nom", "titulaire_id"), ("from_name", "from_id"),
                   ("to_name", "to_id"))


# Copie de `fiches.MOT_RE` et `fiches.mots_normalises`.
MOT_RE = r"\w+(?:['’]\w+)*\.?"


def mots_normalises(texte):
    import unicodedata
    sortie = []
    for m in re.finditer(MOT_RE, str(texte or "")):
        brut = m.group(0)
        mot = unicodedata.normalize("NFKD", brut.rstrip(".").replace("’", "'"))
        mot = mot.encode("ascii", "ignore").decode().upper()
        if not mot:
            continue
        initiale = brut.endswith(".") and len(mot) == 1
        fin = m.end() if initiale or not brut.endswith(".") else m.end() - 1
        sortie.append((m.start(), fin, mot, initiale))
    return sortie


#: Les textes libres où un nom d'EI écartée ne doit pas se lire, dans les objets
#: d'ARGENT seulement (`est_argent`) : ce sont eux que le builder masque. Un acte,
#: la description d'une compétence intercommunale ne passent pas par ce masque,
#: et les y chercher refuserait une instance pour un texte que ce brief ne
#: traite pas. `acteur_nom` (`actualite.json`) reprend un titulaire : il n'est lu
#: que pour les EI que la commune ne désigne pas.
TEXTES_LIBRES = ("objet", "lieu_exec", "description", "note_revue", "title", "titre")
TEXTES_LIBRES_ECARTEES = TEXTES_LIBRES + ("acteur_nom",)
# Copies de `fiches.MOTS_VIDES`, `fiches.SEPARATEUR_RE`, `fiches.jetons_lies`.
MOTS_VIDES = frozenset({"A", "AU", "AUX", "D", "DE", "DES", "DU", "EN", "ET", "L", "LA",
                        "LE", "LES", "PAR", "POUR", "SOUS", "SUR", "UN", "UNE",
                        # Les mots de la mention qui remplace un nom.
                        "ENTREPRISE", "INDIVIDUELLE"})
SEPARATEUR_RE = r"[\s\-‐–]*"


def jetons_lies(texte):
    texte = str(texte or "")
    sep = re.compile(SEPARATEUR_RE)
    sortie, fin_prec, lie_possible = [], None, False
    for d, f, mot, initiale in mots_normalises(texte):
        lie = lie_possible and bool(sep.fullmatch(texte[fin_prec:d]))
        if mot in MOTS_VIDES and not initiale:
            lie_possible, fin_prec = lie, f
            continue
        sortie.append((d, f, mot, initiale, lie))
        fin_prec, lie_possible = f, True
    return sortie


def fenetres(texte, longueur_max):
    """Chaque suite de 2 à `longueur_max` mots pleins liés (`jetons_lies`, sans
    initiale) du texte, dans l'ordre."""
    jetons = jetons_lies(texte)
    sortie = []
    for i in range(len(jetons)):
        if jetons[i][3]:
            continue
        suite = [jetons[i][2]]
        for j in range(i + 1, min(i + longueur_max, len(jetons))):
            if jetons[j][3] or not jetons[j][4]:
                break
            suite.append(jetons[j][2])
            sortie.append(tuple(suite))
    return sortie


def _sha(texte):
    import hashlib
    return hashlib.sha256(texte.encode()).hexdigest()


def groupes_de_mots(texte, publies=()):
    """Les empreintes des groupes de 2 à 4 mots distincts rangés de chaque
    fenêtre (ce que `fiches.empreintes_de_mots` met au relevé), sauf ceux qui
    tiennent dans le nom d'une fiche publiée (`publies`, ensembles de mots)."""
    sortie = set()
    for f in fenetres(texte, 4):
        mots = set(f)
        if len(mots) >= 2 and not any(mots <= p for p in publies):
            sortie.add(_sha(" ".join(sorted(mots))))
    return sortie


def suites_de_mots(texte, publies=()):
    """Les empreintes des suites exactes de 2 à 6 mots (`fiches.empreinte_de_suite`)."""
    return {_sha(" ".join(f)) for f in fenetres(texte, 6)
            if not any(set(f) <= p for p in publies)}


def check_noms_d_ei_ecartees(base, rep, controle):
    """Aucun nom publié ne désigne une entreprise individuelle absente de
    `entities.json` (brief `ei-flux-marches`, 24/11/2026). Le relevé du builder
    porte les EMPREINTES des noms. Lu dans TOUS les fichiers JSON servis :
    - `titulaire_nom`, `from_name`, `to_name` : un nom d'EI écartée dont
      l'identifiant voisin ne renvoie pas à une fiche publiée est une fuite. Le
      nom d'une EI C2 que la commune désigne (`ei_nommees`) n'est admis que sur
      un marché de la commune (`portee == commune`) ou un flux qu'elle verse
      (`to_name`, `sens == sortant`), décision de Julien du 25/11/2026 ;
    - le texte libre (`TEXTES_LIBRES`) : aucune fenêtre de deux à quatre mots
      n'y forme un groupe de mots d'un tel nom (`mots_ei`, `mots_ei_nommees`),
      quel que soit l'ordre (avis vigie-securite du 25/11/2026, A3)."""
    controle = controle or {}
    empreintes = set(controle.get("ei_ecartees") or [])
    nommees = set(controle.get("ei_nommees") or [])
    mots = set(controle.get("mots_ei") or [])
    mots_nommees = set(controle.get("mots_ei_nommees") or [])
    suites = set(controle.get("suites_ei") or [])
    suites_nommees = set(controle.get("suites_ei_nommees") or [])
    if not (empreintes or nommees or mots or mots_nommees or suites or suites_nommees):
        return
    try:
        fiches = json.loads((base / "entities.json").read_text()).get("entities") or []
    except (OSError, json.JSONDecodeError, AttributeError):
        fiches = []
    publies = {e.get("id") for e in fiches}
    # Les noms publiés : une suite de mots qui tient dans l'un d'eux n'est pas
    # une fuite (le builder ne la masque pas, `argent.masquer_noms`).
    # Sauf les noms des exploitants d'une EI sans fiche (`exploitants_ecartes`) :
    # l'élu « Jean DUPONT » n'épargne pas le nom de son EI écartée (avis
    # vigie-dpo du 25/11/2026), même règle que `fiches.mots_des_fiches_publiees`.
    exploitants = set(controle.get("exploitants_ecartes") or [])
    # Sur SA ligne, une extrémité publiée épargne son nom, exploitant compris
    # (`argent.avec_extremites`, tour 6 de la relecture).
    mots_par_id = {e.get("id"): tuple(
        m for c in ("name", "short_name")
        for m in [frozenset(x[2] for x in jetons_lies(e.get(c)) if not x[3])] if len(m) >= 2)
        for e in fiches}
    EXTREMITES = ("from_id", "to_id", "titulaire_id", "acheteur_id", "acteur_id")
    noms_publies = tuple({m for e in fiches for c in ("name", "short_name")
                          for m in [frozenset(x[2] for x in jetons_lies(e.get(c)) if not x[3])]
                          if len(m) >= 2 and _sha(" ".join(sorted(m))) not in exploitants})

    def est_argent(objet):
        # Flux, marché, leurs entrées d'actualité, et l'acte `marché_public`
        # (avis de marché, dont le titre est l'objet : avis vigie-dpo).
        return ("amount" in objet or "titulaire_nom" in objet
                or objet.get("genre") in ("marché", "argent")
                or objet.get("type") == "marché_public")

    def admis(objet, nom):
        return ((nom == "titulaire_nom" and objet.get("portee") == "commune")
                or (nom == "to_name" and objet.get("sens") == "sortant"))

    def parcourir(objet, fichier):
        if isinstance(objet, dict):
            for nom, renvoi in NOMS_ET_RENVOIS:
                valeur = objet.get(nom)
                if not isinstance(valeur, str) or objet.get(renvoi) in publies:
                    continue
                cle = cle_de_nom(valeur)
                if cle in empreintes or (cle in nommees and not admis(objet, nom)):
                    rep.error("nom d'une entreprise individuelle non publiée",
                              f"{fichier} · {nom}")
            for champ in TEXTES_LIBRES_ECARTEES if est_argent(objet) else ():
                valeur = objet.get(champ)
                if not isinstance(valeur, str):
                    continue
                # L'entrée d'actualité d'un FLUX : son titre et sa note sont la
                # description et la note du flux, déjà contrôlées dans flows.json
                # avec ses DEUX extrémités. L'entrée n'en porte qu'une
                # (`acteur_id`) : les relire ici refuserait à tort « Don de Marc
                # Duprat », épargné sur sa ligne par `from_id` (avis vigie-securite,
                # tour 6).
                if objet.get("genre") == "argent" and champ in ("titre", "note_revue"):
                    continue
                # `acteur_nom` d'une fiche publiée la désigne, elle (avis
                # vigie-securite du 25/11/2026, a).
                if champ == "acteur_nom" and objet.get("acteur_id") in publies:
                    continue
                epargnes = noms_publies + tuple(
                    m for c in EXTREMITES if objet.get(c) in publies
                    for m in mots_par_id.get(objet.get(c), ()))
                groupes = groupes_de_mots(valeur, epargnes)
                suites_t = suites_de_mots(valeur, epargnes)
                if (groupes & mots or suites_t & suites
                        or (champ in TEXTES_LIBRES
                            and (groupes & mots_nommees or suites_t & suites_nommees))):
                    rep.error("nom d'une entreprise individuelle non publiée dans un texte libre",
                              f"{fichier} · {champ}")
            for v in objet.values():
                parcourir(v, fichier)
        elif isinstance(objet, list):
            for v in objet:
                parcourir(v, fichier)

    for fp in sorted(base.rglob("*.json")):
        try:
            donnees = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        parcourir(donnees, str(fp.relative_to(base)))


def check_ei_sans_fait(base, rep, controle=None):
    """Une entreprise individuelle publiée doit être désignée par un fait publié
    que `SECURITY.md` nomme : un marché, un paiement ou une subvention publics
    VERS elle, ou un acte de la collectivité qui la cite par son nom. Ni une
    relation, ni une annonce BODACC, ni un registre (`sitadel`, `gpu`…), ni le
    rôle `demandeur`, ni un acte dont le titre publié désigne quelqu'un par son
    rôle ne comptent. Décision du 24/11/2026.

    Lu dans ce qui est SERVI : le paquet `entite/<id>.json` de chaque fiche
    (`flows`, `marches`, `liens`). Filet indépendant de la requête
    `ids_retenus_par_un_fait`, qui lit la base et non ce qui sort. Il vérifie
    aussi qu'aucun paquet d'EI ne liste un acte au titre masqué ou au rôle
    `demandeur`, qui ré-identifierait la personne.

    Un flux ne compte que s'il vient d'une PARTIE PUBLIQUE : la liste est dans
    le relevé du builder (`controle`, cf. `lire_controle_ei`), le snapshot ne
    portant pas la forme juridique. Sans relevé (mode dégradé, averti), tout
    flux dont l'EI est destinataire compte. Un flux demandé ou annulé (`etat`) ne
    compte pas. Il lit `nature == individuelle` :
    forme 1000 quelle que soit l'activité, ou forme inconnue présumée EI
    (`nature_entreprise`)."""
    publiques = (set((controle or {}).get("parties_publiques") or [])
                 if controle is not None else None)
    fp = base / "entities.json"
    dossier = base / "entite"
    if not fp.is_file() or not dossier.is_dir():
        return
    try:
        entities = json.loads(fp.read_text()).get("entities") or []
    except json.JSONDecodeError:
        return  # déjà signalé par check_file
    try:
        evenements = json.loads((base / "events.json").read_text()).get("events") or []
    except (OSError, json.JSONDecodeError):
        evenements = []
    titres = {ev.get("id"): ev.get("title") or "" for ev in evenements}
    for e in entities:
        if e.get("nature") != "individuelle":
            continue
        paquet = dossier / f"{e.get('id')}.json"
        try:
            donnees = json.loads(paquet.read_text())
        except (OSError, json.JSONDecodeError):
            donnees = {}
        liens = donnees.get("liens") or []
        for a in liens:
            if (a.get("role") in ROLES_SANS_FAIT
                    or DESIGNE_UNE_PERSONNE.search(a.get("title") or "")):
                rep.error("fiche d'entreprise individuelle liée à un acte au titre masqué "
                          "ou à son rôle de demandeur ou de bénéficiaire", f"entite/{e.get('id')}.json")
                break
        for f in donnees.get("flows") or []:
            if (DESIGNE_UNE_PERSONNE.search(f.get("description") or "")
                    or DESIGNE_UNE_PERSONNE.search(titres.get(f.get("acte_id"), ""))):
                rep.error("fiche d'entreprise individuelle portant un flux à la description "
                          "ou à l'acte masqué", f"entite/{e.get('id')}.json")
                break
        # Une subvention demandée ou annulée n'est pas un versement : elle ne
        # désigne pas l'EI (audit du 25/11/2026, H3). Un flux sans état ne compte
        # pas non plus : le builder en pose toujours un.
        faits = (sum(1 for f in donnees.get("flows") or [] if f.get("to_id") == e.get("id")
                     and f.get("etat") not in (*ETATS_SANS_VERSEMENT, None)
                     and (publiques is None or f.get("from_id") in publiques))
                 + sum(1 for m in donnees.get("marches") or []
                       if m.get("titulaire_id") == e.get("id"))
                 + sum(1 for a in liens
                       if a.get("type") in TYPES_ACTES_COLLECTIVITE
                       and a.get("role") not in ROLES_SANS_FAIT
                       and not DESIGNE_UNE_PERSONNE.search(a.get("title") or "")))
        if not faits:
            rep.error("entreprise individuelle publiée sans marché, flux ni acte qui la désigne",
                      f"entite/{e.get('id')}.json")


# Champs dont la valeur est un identifiant d'entité, et qui PROMETTENT donc une
# fiche `/entite/<id>`. La liste est explicite plutôt que déduite d'un suffixe :
# `id`, `from_id` ou `event_id` désignent autre chose, et un contrôle qui se
# trompe de champ finit désactivé.
CHAMPS_RENVOI = (
    "entity_id", "entite_id", "acteur_id", "person_id",
    "demandeur_entity_id", "titulaire_id", "acheteur_id",
    "titulaire_entity_id", "acheteur_entity_id", "autre_id",
    "from_id", "to_id",
)

# Fichiers qui portent volontairement un identifiant sans promettre de fiche.
# Un enregistrement s'en dispense en portant `"fiche": false` — voir
# `elus_rne.json`, où la composition d'un conseil municipal est publiée (donnée
# du RNE, registre public) sans que chaque élu ait droit à une page.
CLE_SANS_FICHE = "fiche"


def check_renvois_sortants(base, rep):
    """Tout identifiant publié qui promet une fiche doit désigner une fiche.

    Symétrique de `check_fiches_orphelines`, et il manquait. Celui-là vérifiait
    qu'aucune fiche n'est servie sans entité publiée ; personne ne vérifiait le
    sens inverse — un JSON public qui renvoie vers une fiche ABSENTE.

    Le défaut a vécu en production sur deux sites : `/elus` liait
    `/entite/<id>` pour tous les conseillers municipaux des communes de
    l'intercommunalité, dont 78 à 90 % n'ont pas de fiche (le filtre de
    périmètre ne l'accorde qu'à ceux qui siègent au conseil communautaire).
    152 liens morts sur Lasalle, 176 sur Brassac, 191 sur Saillans. Le
    contrôleur rendait « 0 violation » : il regardait le contenu des fiches, pas
    les promesses de lien entre les fichiers qu'il produit.

    C'est un invariant de CONTRAT DE DONNÉES, pas d'affichage : le snapshot est
    publié sous ODbL et lu par des tiers, qui suivent les identifiants sans
    avoir de page pour leur pardonner.

    Relevé par un audit externe le 21/08/2026. Second passage du même
    audit : le contrôle ne lisait que la racine et `fiche: false`
    dispensait tout un sous-arbre — deux façons d'annoncer un invariant
    plus large que ce qu'il tenait. Corrigé le jour même.
    """
    fp = base / "entities.json"
    if not fp.is_file():
        return
    try:
        entities = json.loads(fp.read_text()).get("entities") or []
    except json.JSONDecodeError:
        return  # déjà signalé par check_file
    publies = {e.get("id") for e in entities}
    if not publies:
        return  # base vide : rien à promettre, rien à contrôler

    morts = {}          # (fichier, champ) → {ids}
    occurrences = {}    # (fichier, champ) → compte

    def parcourir(noeud, fichier):
        if isinstance(noeud, dict):
            # Un enregistrement peut dire explicitement qu'il ne promet rien —
            # mais la dispense ne couvre QUE ses propres champs de renvoi. Elle
            # coupait la descente entière : un objet imbriqué sous une ligne
            # `fiche: false` échappait au contrôle sans l'avoir demandé.
            # `elus_rne.json` est plat aujourd'hui, il ne le restera pas.
            dispense = noeud.get(CLE_SANS_FICHE) is False
            for cle, val in noeud.items():
                if cle in CHAMPS_RENVOI and isinstance(val, int):
                    if not dispense and val not in publies:
                        morts.setdefault((fichier, cle), set()).add(val)
                        occurrences[(fichier, cle)] = occurrences.get((fichier, cle), 0) + 1
                else:
                    parcourir(val, fichier)
        elif isinstance(noeud, list):
            for v in noeud:
                parcourir(v, fichier)

    # `rglob`, pas `glob` : les fiches `entite/<id>.json` sont servies comme le
    # reste et portent des renvois (`relations`, `flows`, `marches`) que la
    # racine n'a pas. Un contrôle limité à la racine annonçait un invariant
    # qu'il ne tenait que sur une partie du snapshot — et `relations.json`
    # peut être propre pendant qu'une fiche pré-résolue renvoie dans le vide.
    for f in sorted(base.rglob("*.json")):
        rel = f.relative_to(base)
        if "node_modules" in rel.parts:
            continue
        try:
            parcourir(json.loads(f.read_text()), str(rel))
        except json.JSONDecodeError:
            continue  # déjà signalé par check_file

    for (fichier, champ), ids in sorted(morts.items()):
        rep.error(
            "renvoi vers une fiche non publiée",
            f"{fichier} · {champ} : {len(ids)} identifiant(s) mort(s), "
            f"{occurrences[(fichier, champ)]} occurrence(s) "
            f"— ex. {sorted(ids)[:5]}")


def _lire(base, nom, cle):
    """La liste publiée sous `cle` dans `nom`, ou None si le fichier manque."""
    fp = base / nom
    if not fp.is_file():
        return None
    try:
        data = json.loads(fp.read_text())
    except json.JSONDecodeError:
        return None  # déjà signalé par check_file
    liste = data.get(cle) if isinstance(data, dict) else None
    return liste if isinstance(liste, list) else None


def check_personnes(base, rep):
    """Une personne n'a de fiche qu'au titre d'un lien publié.

    Les règles ne publient une personne physique que pour un rôle — mandat,
    fonction, lien avec une structure payée. Ce rôle est une relation : une
    fiche de personne que `relations.json` ne relie à rien est sortie sans son
    titre à paraître, et personne ne peut dire pourquoi elle est là.
    """
    entites = _lire(base, "entities.json", "entities")
    relations = _lire(base, "relations.json", "relations")
    if not entites or relations is None:
        return
    reliees = {bout for r in relations for bout in (r.get("from_id"), r.get("to_id"))}
    for e in entites:
        if e.get("type") == "person" and e.get("id") not in reliees:
            rep.error("personne publiée sans aucun lien publié",
                      f"entities.json: id={e.get('id')} {e.get('name')!r}")


def check_popolo(base, rep):
    """`popolo.json` est la vue des mandats : chaque personne y a le sien.

    Une personne sans `membership` y passe pour un élu sans fonction ; elle
    paraît ailleurs à un autre titre (un gérant inscrit à SIRENE), pas ici.
    """
    persons = _lire(base, "popolo.json", "persons")
    memberships = _lire(base, "popolo.json", "memberships")
    if persons is None or memberships is None:
        return
    mandatees = {m.get("person_id") for m in memberships}
    for p in persons:
        if p.get("id") not in mandatees:
            rep.error("personne sans mandat dans popolo.json",
                      f"popolo.json: {p.get('id')} {p.get('name')!r}")


def check_conflits(base, rep):
    """Une « situation à vérifier » ne cite que ce qui est publié par ailleurs.

    `conflits.json` croise un lien, un versement et un acte. Il les lisait en
    base, par une vue qui ne regarde ni la confiance ni le sort de l'acte : une
    relation `hypothesis`, absente de `relations.json`, y sortait comme
    situation, et le déport citait le titre brut d'un acte non publié (revue
    externe du 05/10/2026). Le contrôleur ne voyait rien — il n'a aucune règle
    sur ce fichier, qui ne porte ni `confidence` ni `relation_type`.

    Trois promesses, donc : le lien est dans `relations.json` sous l'un des
    rôles annoncés, le versement dans `flows.json`, l'acte du déport dans
    `events.json`. Un fichier de référence absent n'excuse rien : sans lui, un
    cas ne peut pas être fondé.
    """
    cas = _lire(base, "conflits.json", "cas")
    if not cas:
        return
    liens = {(frozenset((r.get("from_id"), r.get("to_id"))), r.get("relation_type"))
             for r in _lire(base, "relations.json", "relations") or []}
    flux = {f.get("id") for f in _lire(base, "flows.json", "flows") or []}
    actes = {e.get("id") for e in _lire(base, "events.json", "events") or []}
    for c in cas:
        paire = frozenset((c.get("person_id"), c.get("entite_id")))
        qui = f"personne id={c.get('person_id')} / structure id={c.get('entite_id')}"
        if not any((paire, role) in liens for role in c.get("roles_entite") or []):
            rep.error("conflit fondé sur un lien non publié",
                      f"conflits.json: {qui} — aucun lien "
                      f"{c.get('roles_entite')} dans relations.json")
        if c.get("flux_id") is not None and c["flux_id"] not in flux:
            rep.error("conflit citant un versement non publié",
                      f"conflits.json: {qui} — flux {c['flux_id']} absent de flows.json")
        deport = c.get("deport") or {}
        if deport and deport.get("event_id") not in actes:
            rep.error("déport citant un acte non publié",
                      f"conflits.json: {qui} — acte {deport.get('event_id')} "
                      "absent de events.json")


# Une civilité suivie d'un nom en capitales, écrite ici et non importée de
# `scripts/snapshot/textes.py` : le contrôle ne doit pas partager les angles
# morts du filtre qu'il contrôle.
_CIVILITE_NOM = re.compile(
    r"(?<![\w])(?:Monsieur|Madame|Mademoiselle|MM\.|M\.|Mmes?|Mlle)\s+"
    r"(?:et\s+(?:Madame|Monsieur|Mme|M\.)\s+)?((?:[A-ZÀ-Ÿ][\w'’\-]*\s+){0,3}[A-ZÀ-Ÿ]{2,}[\w'’\-]*)")
# Recopiés de `_ACTE_DE_MANDAT` et `_ACTE_ENVERS_UN_PARTICULIER` (textes.py),
# pas importés. Plus étroits, ils refuseraient un titre que le filtre a gardé à
# bon droit : les deux fichiers se modifient ensemble, un essai les compare.
_TITRE_DE_MANDAT = re.compile(
    r"d[ée]signation|[ée]lection|\b[ée]lue?s?\b|\bd[ée]l[ée]gu[ée]e?s?\b"
    r"|d[ée]l[ée]gations?\s+(?:de|du|des|au|aux|à)\b|indemnit[ée]s?\s+de\s+fonction"
    r"|installation\s+du\s+conseil|\bd[ée]mission|\bprocuration|secr[ée]taire\s+de\s+s[ée]ance"
    r"|\badjointe?s?\b|\bsuppl[ée]ante?s?\b", re.I)
_TITRE_ENVERS_UN_PARTICULIER = re.compile(
    r"\baides?\b|\bsubvention|\bprime\b|fa[çc]ade|\bvente\b|\bcession\b|\bacquisition\b"
    r"|\bbail\b|\bloyer|\bterrain\b|\bparcelle|\bpermis\b|\bsecours\b|\bremise\b", re.I)
_CIVILITES_MOTS = {"et", "m", "mm", "mme", "mmes", "mlle", "monsieur", "madame", "mademoiselle"}


def check_titres_nominatifs(base, rep):
    """Le titre d'un acte ne nomme qu'une personne publiée (règle du 27/10/2026).

    Ailleurs, le titre désigne la personne par son rôle : « un particulier »,
    « un agent ». Une personne est publiée si elle a sa fiche (`entities.json`)
    ou un mandat (`popolo.json`). Sur un patronyme seul, partagé avec une
    personne publiée, le doute profite au titre : c'est peut-être elle.

    Un patronyme seul que rien de publié ne porte n'est qu'un avertissement :
    le filtre nomme aussi qui a SIÉGÉ sous un mandat clos, que ni les fiches ni
    `popolo.json` ne gardent (relevé sur l'aperçu de Brassac, 27/10/2026), et un
    patronyme seul n'identifie pas assez pour bloquer une publication.
    """
    actes = _lire(base, "events.json", "events")
    if not actes:
        return
    noms = [p.get("name") for p in (_lire(base, "entities.json", "entities") or [])
            if p.get("type") == "person"]
    noms += [p.get("name") for p in (_lire(base, "popolo.json", "persons") or [])]
    publies = {_nom_cle(n) for n in noms if n}
    mots_publies = {m for n in publies for m in n.split()}
    for e in actes:
        # L'agenda annonce : son titre nomme qui la source veut faire connaître,
        # et le builder le laisse tel quel (14/11/2026). Le type est écrit ici,
        # pas importé du builder.
        if e.get("type") == "local_event":
            continue
        for m in _CIVILITE_NOM.finditer(e.get("title") or ""):
            # « M. ET Mme NOM » : la civilité et le « et » ne sont pas le nom.
            mots = set(_nom_cle(m.group(1)).split()) - _CIVILITES_MOTS
            if any(set(n.split()) <= mots or mots <= set(n.split())
                   for n in publies if len(n.split()) >= 2) and len(mots) >= 2:
                continue
            # Un patronyme seul partagé avec une personne publiée n'est elle
            # que dans un titre qui parle d'un mandat, et jamais pour un couple
            # (14/11/2026 : « M. ET MME NOM », bénéficiaires d'une aide, étaient
            # servis dans le titre parce qu'un élu portait ce nom).
            couple = bool(re.search(r"(?i)\bet\s+(?:mme|madame|m\.|monsieur)", m.group(0)))
            if (len(mots) == 1 and mots <= mots_publies and not couple
                    and _TITRE_DE_MANDAT.search(e.get("title") or "")
                    and not _TITRE_ENVERS_UN_PARTICULIER.search(e.get("title") or "")):
                continue
            if len(mots) == 1 and mots <= mots_publies:
                rep.error("titre d'acte nommant un homonyme possible d'une personne publiée",
                          f"events.json: id={e.get('id')}")
                continue
            (rep.warn if len(mots) == 1 else rep.error)(
                "titre d'acte nommant une personne non publiée",
                f"events.json: id={e.get('id')} — {m.group(0)!r}")


# Écrits ici et non importés de `scripts/snapshot/recherche.py` : le contrôle
# ne doit pas partager les angles morts de l'index qu'il contrôle.
_MOT_DU_TEXTE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿŒœÆæ]+")
_APRES_CIVILITE = re.compile(
    r"(?<![A-Za-zÀ-ÿ])(?:monsieur|madame|mademoiselle|messieurs|mesdames|mm\.|m\.|mmes?"
    r"|mlle|me|ma[iî]tre|dr|docteur|consorts|veuve|famille|[ée]poux|[ée]pouse)\.?\s+"
    r"(?=(\S+)(?:\s+(\S+))?)", re.IGNORECASE)


def _cle_index(mot):
    """La clé sous laquelle l'index range un mot — recopié, pas importé."""
    import unicodedata
    s = mot.lower().replace("œ", "oe").replace("æ", "ae")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s[:-1] if len(s) > 3 and s[-1] in "sx" else s


def check_index_du_texte(base, rep):
    """`recherche_texte.json` ne rend aucune personne retrouvable (13/11/2026).

    Le texte d'un acte garde les noms qu'il cite ; l'index de ses mots ne doit
    en porter aucun. Pour chaque acte, un mot de l'index ne doit apparaître
    dans son texte ni avec une majuscule, ni derrière une civilité : c'est la
    seule forme sous laquelle un nom s'écrit. Et l'index ne cite qu'un acte
    dont le texte est publié.
    """
    fp = base / "recherche_texte.json"
    if not fp.exists():
        return
    try:
        mots = json.loads(fp.read_text(encoding="utf-8")).get("mots") or {}
    except (ValueError, OSError):
        rep.error("index du texte illisible", "recherche_texte.json")
        return
    par_acte = {}
    for mot, ids in mots.items():
        for i in ids:
            par_acte.setdefault(i, set()).add(mot)
    for i, cles in sorted(par_acte.items()):
        extrait = base / "extrait" / f"{i}.json"
        if not extrait.exists():
            rep.error("index du texte citant un acte sans texte publié",
                      f"recherche_texte.json: acte {i}")
            continue
        texte = json.loads(extrait.read_text(encoding="utf-8")).get("texte") or ""
        suspects = {_cle_index(m) for m in _MOT_DU_TEXTE.findall(texte) if m != m.lower()}
        for m in _APRES_CIVILITE.finditer(texte):
            for groupe in m.groups():
                suspects.update(_cle_index(x) for x in _MOT_DU_TEXTE.findall(groupe or ""))
        fautifs = sorted(cles & suspects)
        if fautifs:
            rep.error("index du texte portant un mot écrit comme un nom dans l'acte",
                      f"recherche_texte.json: acte {i} — {len(fautifs)} mot(s)")
    index = _lire(base, "recherche_index.json", "index") or []
    for e in index:
        if e.get("i") is not None and not (base / "extrait" / f"{e['i']}.json").exists():
            rep.error("résultat de recherche renvoyant à un texte non publié",
                      f"recherche_index.json: i={e['i']}")


# ── Le texte des actes : ce qui touche une personne (14/11/2026) ─────────────
# Sept extraits d'aides à la rénovation de façades servaient le numéro et la rue
# du bien aidé, presque toujours le domicile du demandeur, alors que les
# mentions légales le disaient masqué. Deux voisinages, écrits ici : un texte
# qui parle d'une aide à une façade ne porte aucun numéro suivi d'un mot de
# voie ; et le texte d'un acte qui porte sur la situation d'une personne
# (créance effacée, sanction, secours) n'est pas publié du tout.
# « Façade » ou « ravalement » suffit, où que ce soit dans le texte : exiger
# « aide » dans la même phrase donnait à ce contrôle l'angle mort du filtre
# (relecture sécurité du 14/11/2026).
_AIDE_FACADE = re.compile(r"fa[çc]ades?|\bravalement", re.I)
# La route classée est exclue comme dans le filet du filtre : sans cela ce
# contrôle refusait un extrait que le filtre publie à bon droit, et arrêtait la
# passe de nuit (« 2 route départementale à aménager », relecture du 14/11).
_NUMERO_ET_VOIE = re.compile(
    rf"(?<![\w.,/])\d{{1,4}}\s*(?:bis|ter)?\s*,?\s+{_VOIE}\b"
    r"(?!\.?\s+(?:d[ée]partementale|nationale|communale|foresti[èe]re)\b)", re.I)
# Ce que le filtre retient TOUJOURS, et cela seulement : un mot qu'il ne retient
# que si une personne est nommée (licenciement, bail) bloquerait ici la
# publication d'un règlement.
_TEXTE_DE_VIE_PRIVEE = re.compile(
    r"admissions?\s+en\s+non[- ]valeurs?|cr[ée]ances?\s+[ée]teintes?|\bcuratelle\b"
    r"|sanction\s+disciplinaire|conseil\s+de\s+discipline|bail\s+d['’]habitation"
    r"|plac[ée]e?s?\s+en\s+cong[ée]", re.I)


def check_textes_des_actes(base, rep):
    """Aucun extrait ne sert l'adresse d'un bien aidé, ni le texte d'un acte
    qui porte sur la situation d'une personne."""
    dossier = base / "extrait"
    if not dossier.is_dir():
        return
    for fp in sorted(dossier.glob("*.json")):
        try:
            texte = json.loads(fp.read_text(encoding="utf-8")).get("texte") or ""
        except (ValueError, OSError):
            rep.error("extrait illisible", f"extrait/{fp.name}")
            continue
        if _AIDE_FACADE.search(texte) and _NUMERO_ET_VOIE.search(texte):
            rep.error("adresse d'un bien aidé publiée dans le texte d'un acte",
                      f"extrait/{fp.name}")
        if _TEXTE_DE_VIE_PRIVEE.search(texte):
            rep.error("texte publié d'un acte qui porte sur la situation d'une personne",
                      f"extrait/{fp.name}")


# Recopiés de `collectors/decisions.py`, pas importés : ce contrôle doit
# refuser une sortie même si le classement du collecteur dérive.
_DECISIONS = ("decision_delegation", "decision_delegation_cc")
_DECISIONS_COMPTEES = {"preemption_dia", "preemption_deleguee", "concession_funeraire",
                       "louage_particulier", "aide_individuelle", "justice", "personnel",
                       "nominative", "parcelle", "non_classee"}
_PARCELLE = re.compile(r"parcelle|cadastr|section\s+[A-Z]{1,2}\b"
                       r"|(?<!timent )(?<!classe )(?<!classes )(?<!bloc )(?<!lot )"
                       r"\b(?!R[DN]\b|DM\b|C[EMP]\d|M\d{2}\b)[A-Z]{1,2}\s?(?:n[°o]\s?)?\d{1,4}\b"
                       r"(?![\s\u00a0.]\d{3}|\s*(?:€|euros?\b)|[-/])")
# Ce qui désigne une personne dans un titre publié, relu sans le collecteur.
_TITRE_DE_PERSONNE = re.compile(
    r"\b(M|MM|Mmes?|Mlle|Mr|Dr|Me|MME|MLLE)\.?\s"
    r"|\b(?i:docteur|monsieur|madame|mademoiselle|ma[iî]tre|famille|consorts|veuve"
    r"|un particulier|une particuli[eè]re|un agent|une personne)\b"
    r"|\b(EARL|GAEC|SCEA|EIRL|EURL)\b")


def check_decisions_par_delegation(base, rep):
    """Une décision par délégation sort en titre seul ; une catégorie comptée
    ne sort pas ; aucun titre ne désigne une personne ou une parcelle ; une
    préemption sort sans sa pièce ni son numéro (15/11/2026)."""
    fp = base / "events.json"
    if not fp.exists():
        return
    try:
        events = json.loads(fp.read_text(encoding="utf-8")).get("events") or []
    except (ValueError, OSError):
        return
    for e in events:
        if e.get("type") not in _DECISIONS:
            continue
        loc = f"events.json#{e.get('id')}"
        if (e.get("categorie_delegation") or "") in _DECISIONS_COMPTEES:
            rep.error("décision d'une catégorie comptée publiée une à une", loc)
        if e.get("extrait") or (base / "extrait" / f"{e.get('id')}.json").exists():
            rep.error("texte publié d'une décision par délégation", loc)
        titre = e.get("title") or ""
        if _PARCELLE.search(titre):
            rep.error("décision publiée qui donne une parcelle", loc)
        if _TITRE_DE_PERSONNE.search(titre):
            rep.error("décision publiée qui désigne une personne", loc)
        if e.get("categorie_delegation") == "preemption_exercee" and (
                e.get("source_url") or e.get("pdf_url") or e.get("numero_acte")):
            rep.error("préemption publiée avec sa pièce ou son numéro", loc)


def _nom_cle(nom):
    """Casse, accents et ponctuation retirés — recopié, pas importé."""
    import unicodedata
    s = unicodedata.normalize("NFKD", nom or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def check_sens_flux(base, rep):
    """Le `sens` d'un flux doit s'accorder avec les NOMS de ses extrémités.

    Le générateur trouvait la commune par son nom exact ; SIRENE l'écrit en
    capitales. Il ne la trouvait pas, et sur les trois instances aucun flux ne
    sortait `sortant` : `/finances` affichait « 0 € versé » pour chaque année
    pendant que les fiches publiaient les subventions. Les flux déliés, eux,
    sortaient « entrant » (`None == None`). Rien ne le signalait. Relevé par un
    audit externe le 24/09/2026.

    Le contrôle relit les noms plutôt que les identifiants, que le générateur
    résout lui-même : ce qui a échoué une fois ne se contrôle pas par lui-même.
    """
    fp = base / "flows.json"
    commune = (RULES.get("project") or {}).get("commune")
    if not fp.is_file() or not commune:
        return
    try:
        d = json.loads(fp.read_text())
    except json.JSONDecodeError:
        return  # déjà signalé par check_file
    flows = d if isinstance(d, list) else d.get("flows") or []
    cle = _nom_cle(f"Commune de {commune}")

    faux = {}   # attendu → [ids]
    for f in flows:
        de, vers = _nom_cle(f.get("from_name")), _nom_cle(f.get("to_name"))
        if de == cle and vers != cle:
            attendu = "sortant"
        elif vers == cle and de != cle:
            attendu = "entrant"
        else:
            attendu = "tiers"
        if f.get("sens") != attendu:
            faux.setdefault(attendu, []).append(f.get("id"))

    for attendu, ids in sorted(faux.items()):
        rep.error(
            "sens de flux contraire aux noms de ses extrémités",
            f"{fp.name}: {len(ids)} flux attendus « {attendu} » — ex. {ids[:5]}")


def check_renvoi_des_flux(base, rep):
    """Un montant lu dans un compte rendu (`origine='verbatim'`) remonte à sa pièce.

    Revue d'architecture du 07/11/2026, C1 : `flows.json` ne portait que des
    étiquettes (« CR CM année »), et une contestation ne se tranchait qu'en
    fouillant les PV à la main. Un flux `verbatim` doit porter `acte_id` (un
    acte publié dans `events.json`) ou `document` (une URL externe). Un
    `acte_id` qui ne désigne aucun acte publié est un renvoi mort.

    Relu ici sans rien importer du builder : ce qui a manqué une fois ne se
    contrôle pas par lui-même.

    Le flux sans renvoi AVERTIT, il ne bloque pas : le 07/11, Lasalle en
    comptait entre 1 et 4 (sonde, k < 5 masqué) parmi 463 flux verbatim, et
    refuser la passe entière pour eux aurait figé tout le site. Le renvoi mort,
    lui, ne peut naître que d'un défaut du builder : il bloque.
    """
    flows = _lire(base, "flows.json", "flows")
    if not flows:
        return
    actes = {e.get("id") for e in _lire(base, "events.json", "events") or []}
    sans, morts = [], []
    for f in flows:
        acte, doc = f.get("acte_id"), f.get("document")
        if acte is not None and acte not in actes:
            morts.append(f.get("id"))
        if f.get("origine") == "verbatim" and acte is None and not (
                isinstance(doc, str) and doc.startswith(("https://", "http://"))):
            sans.append(f.get("id"))
    if sans:
        rep.warn("flux lu dans un compte rendu sans renvoi à sa pièce",
                  f"flows.json: {len(sans)} flux verbatim sans acte_id ni document "
                  f"— ex. {sans[:5]}")
    if morts:
        rep.error("flux qui renvoie à un acte non publié",
                  f"flows.json: {len(morts)} acte_id absents d'events.json — ex. {morts[:5]}")


# ── Le manifeste : la construction est-elle allée au bout ? ──────────────────
#
# Le 04/10/2026, un plantage du builder a laissé un brouillon tronqué —
# `stats.json` présent, `entite/` et `entity_index.json` absents — et l'atelier
# a construit le site dessus : aucune règle d'étanchéité ne voit ce qui MANQUE.
# Le builder écrit désormais `manifeste.json` en dernier, avec l'empreinte de
# chaque fichier. Sa règle est relue ICI, sans rien importer du builder :
#   - un répertoire qui porte `stats.json` (un snapshot) sans manifeste est une
#     construction interrompue ;
#   - tout fichier présent est au manifeste, tout fichier du manifeste est
#     présent, et son empreinte SHA-256 et sa taille concordent.
# Hors manifeste, comme hors de l'empreinte de la publication : `version.json`
# (écrit par la publication APRÈS le contrôle) et les rebuts du poste, qu'une
# autre règle refuse déjà.
MANIFESTE = "manifeste.json"
HORS_MANIFESTE = {MANIFESTE, "version.json"}
REBUTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db)$", re.I)


def check_manifeste(base, rep):
    import hashlib
    chemin = base / MANIFESTE
    if not chemin.is_file():
        if (base / "stats.json").is_file():
            rep.error("snapshot sans manifeste — construction interrompue ou incomplète",
                      f"{MANIFESTE}: absent alors que stats.json est là")
        return
    try:
        manifeste = json.loads(chemin.read_text(encoding="utf-8"))
        listes = {f["chemin"]: f for f in manifeste["fichiers"]}
    except (ValueError, KeyError, TypeError) as e:
        rep.error("manifeste illisible", f"{MANIFESTE}: {e}")
        return
    if manifeste.get("total") != len(manifeste["fichiers"]):
        rep.error("manifeste incohérent",
                  f"{MANIFESTE}: total {manifeste.get('total')} pour "
                  f"{len(manifeste['fichiers'])} fichiers listés")
    presents = {}
    for fp in sorted(base.rglob("*")):
        rel = fp.relative_to(base).as_posix()
        if fp.is_file() and rel not in HORS_MANIFESTE and not REBUTS.match(fp.name):
            presents[rel] = fp
    for rel in sorted(set(presents) - set(listes)):
        rep.error("fichier absent du manifeste", f"{rel}: présent, non déclaré")
    for rel in sorted(set(listes) - set(presents)):
        rep.error("fichier du manifeste manquant", f"{rel}: déclaré, absent")
    for rel in sorted(set(listes) & set(presents)):
        brut = presents[rel].read_bytes()
        attendu = listes[rel]
        if (hashlib.sha256(brut).hexdigest() != attendu.get("sha256")
                or len(brut) != attendu.get("octets")):
            rep.error("empreinte différente de celle du manifeste",
                      f"{rel}: le fichier a changé depuis la construction")
        elif attendu.get("etape") is None:
            rep.warn("fichier qu'aucune étape de la construction n'a écrit",
                     f"{rel}: laissé par une construction précédente ?")


# Les clés qui désigneraient une cantine ou une installation : elles servent au
# rapprochement en base, aucune ne sort (avis DPO du 29/10/2026).
CLES_INTERDITES_DATA_GOUV = {"siret", "id", "cantine_id", "cuisine_centrale_siret",
                             "code_eic", "nominstallation", "applicant_id"}
SIRET_NU = re.compile(r"\b\d{14}\b")


def check_sources_data_gouv(base, rep):
    """Cantines, électricité, parc social : ce qui sort, relu sans le code d'export.

    Un second chemin, qui ne réutilise rien de `sources_data_gouv.py` : aucune
    clé d'identification, aucun SIRET, l'énergie d'une filière de moins de
    trois installations masquée, aucune répartition sous trois logements.
    """
    def cles(noeud):
        if isinstance(noeud, dict):
            yield from noeud
            for v in noeud.values():
                yield from cles(v)
        elif isinstance(noeud, list):
            for v in noeud:
                yield from cles(v)

    def bloc(nom, cle):
        fp = base / nom
        if not fp.is_file():
            return None
        try:
            data = json.loads(fp.read_text())
        except json.JSONDecodeError:
            return None
        return data.get(cle) if isinstance(data, dict) else None

    for nom, cle in (("territoire.json", "cantines"), ("environnement.json", "electricite"),
                     ("territoire.json", "rpls")):
        b = bloc(nom, cle)
        if not b:
            continue
        interdites = sorted(set(cles(b)) & CLES_INTERDITES_DATA_GOUV)
        if interdites:
            rep.error("clé d'identification dans un bloc data.gouv", f"{nom} · {cle} : {interdites}")
        if SIRET_NU.search(json.dumps(b)):
            rep.error("SIRET dans un bloc data.gouv", f"{nom} · {cle}")
    cantines = bloc("territoire.json", "cantines")
    if cantines and set((cantines.get("privees") or {})) - {"nombre"}:
        rep.error("cantines privées décrites au-delà de leur nombre", "territoire.json · cantines")
    elec = bloc("environnement.json", "electricite")
    for f in (elec or {}).get("filieres") or []:
        if (f.get("installations") or 0) < 3 and f.get("energie_injectee_kwh") is not None:
            rep.error("énergie d'une filière de moins de trois installations",
                      f"environnement.json · {f.get('filiere')}")
    rpls = bloc("territoire.json", "rpls")
    if rpls and (rpls.get("total") or 0) < 3 and rpls.get("repartitions"):
        rep.error("répartition du parc social sous trois logements", "territoire.json · rpls")


def check_dir(base, rep):
    for fp in sorted(base.rglob("*")):
        if not fp.is_file():
            continue
        rel = fp.relative_to(base)
        if "node_modules" in rel.parts:
            continue
        # Un répertoire caché n'a rien à faire dans un répertoire publié : un
        # site statique n'en sert aucun (hors `.well-known`). C'est ainsi que le
        # retour arrière `.data.precedent/` a été servi le 16/09/2026, et que
        # le transit de la publication (`.bascule/`) le serait s'il s'y
        # retrouvait. Le fichier caché, lui, reste affaire de `FORBIDDEN_FILENAMES`.
        cache = next((p for p in rel.parts[:-1]
                      if p.startswith(".") and p != ".well-known"), None)
        if cache:
            rep.error("répertoire caché dans le répertoire publié", str(rel))
            continue
        if FORBIDDEN_FILENAMES.search(fp.name):
            rep.error("fichier interdit dans le répertoire publié", str(rel))
            continue  # inutile de scanner : il doit disparaître
        rep.files += 1
        if fp.suffix in (".json", ".geojson", ".html", ".js", ".txt", ".md", ".css"):
            check_file(fp, rep, base)
    check_perimetre(base, rep)
    check_statut(base, rep)
    check_fiches_orphelines(base, rep)
    controle_ei = lire_controle_ei(base, rep)
    check_ei_sans_fait(base, rep, controle_ei)
    check_noms_d_ei_ecartees(base, rep, controle_ei)
    check_renvois_sortants(base, rep)
    check_sens_flux(base, rep)
    check_renvoi_des_flux(base, rep)
    check_personnes(base, rep)
    check_titres_nominatifs(base, rep)
    check_index_du_texte(base, rep)
    check_textes_des_actes(base, rep)
    check_decisions_par_delegation(base, rep)
    check_popolo(base, rep)
    check_conflits(base, rep)
    check_manifeste(base, rep)
    check_sources_data_gouv(base, rep)


def main(argv):
    en_json = "--json" in argv
    targets = [Path(a) for a in argv if not a.startswith("-")] or [
        ROOT / "dashboard" / "static" / "public_api",
        ROOT / "public" / "static" / "data",
        ROOT / "public" / "build",
    ]
    rep = Report()
    scanned = []
    for t in targets:
        if t.is_dir():
            scanned.append(t)
            check_dir(t, rep)
    if not scanned:
        absent = "Aucun répertoire de snapshot trouvé."
        if en_json:
            print(json.dumps({"ok": False, "repertoires": [], "fichiers": 0,
                              "erreurs": [], "avertissements": [],
                              "compte_erreurs": 0, "compte_avertissements": 0,
                              "rapport": absent}, ensure_ascii=False))
        else:
            print(absent, file=sys.stderr)
        return 2

    n_err = sum(len(v) for v in rep.errors.values())
    n_warn = sum(len(v) for v in rep.warnings.values())
    lignes = [f"Répertoires : {', '.join(str(s) for s in scanned)}",
              f"Fichiers inspectés : {rep.files}",
              *rep.lignes(),
              "",
              f"{n_err} violation(s) bloquante(s), {n_warn} avertissement(s).",
              "ÉCHEC — ne pas publier ce snapshot." if n_err
              else "OK — étanchéité vérifiée sur les règles connues."]

    if en_json:
        print(json.dumps({
            "ok": not n_err,
            "repertoires": [str(s) for s in scanned],
            "fichiers": rep.files,
            "erreurs": rep.groupes(rep.errors),
            "avertissements": rep.groupes(rep.warnings),
            "compte_erreurs": n_err,
            "compte_avertissements": n_warn,
            "rapport": "\n".join(lignes),
        }, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lignes))
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
