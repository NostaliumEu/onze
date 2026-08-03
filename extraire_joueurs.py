#!/usr/bin/env python3
"""
ONZE — extraction du dataset de joueurs depuis Wikidata.

    pip install requests
    python extraire_joueurs.py > joueurs.js

Puis remplacer le tableau PLAYERS de onze.html par le contenu de joueurs.js.

POURQUOI CE SCRIPT PLUTÔT QU'UNE LISTE ÉCRITE À LA MAIN
Le mercato d'été 2026 court jusqu'au 1er septembre : n'importe quel instantané
écrit aujourd'hui sera faux dans quelques semaines. L'intérêt n'est pas que
Wikidata soit parfait — il ne l'est pas — mais que le script soit RELANÇABLE.
Une exécution hebdomadaire, et les données restent à jour sans intervention.

LIMITE CONNUE
Wikidata a aussi du retard sur les transferts récents, parfois plusieurs
semaines. Pour un jeu quotidien c'est acceptable ; pour un mode compétitif,
prévoir une liste manuelle de corrections appliquée après extraction.
"""

import json
import re
import sys
import time
from datetime import date

import requests

ENDPOINT = "https://query.wikidata.org/sparql"

# Joueurs à exclure à la main : Wikidata laisse passer quelques fiches
# périmées qu'aucun filtre automatique ne rattrape. Ajoute les noms ici.
EXCLUSIONS = set()

# Wikidata donne "Royaume-Uni" comme citoyenneté : inutilisable pour du foot,
# où les quatre nations jouent séparément. Sans pays plus précis, on écarte.
NAT_TROP_VAGUE = {"Royaume-Uni", "United Kingdom"}

# Wikidata renvoie parfois le nom officiel de l'État plutôt que l'usuel.
NAT_ALIAS = {
    "Royaume de Danemark": "Danemark",
    "Royaume des Pays-Bas": "Pays-Bas",
    "Royaume d'Espagne": "Espagne",
    "Royaume de Belgique": "Belgique",
    "Royaume du Maroc": "Maroc",
    "République française": "France",
    "République fédérale d'Allemagne": "Allemagne",
    "République italienne": "Italie",
    "République portugaise": "Portugal",
    "République tchèque": "Tchéquie",
    "République de Corée": "Corée du Sud",
    "République démocratique du Congo": "RD Congo",
}

# ⚠ À VÉRIFIER avant la première exécution : ouvre chaque identifiant sur
# wikidata.org pour confirmer qu'il pointe bien vers la bonne compétition.
# Les identifiants ci-dessous sont donnés de mémoire et peuvent être erronés.
CHAMPIONNATS = {
    "Q13394":  "Ligue 1",
    "Q9448":   "Premier League",
    "Q324867": "Liga",
    "Q15804":  "Serie A",
    "Q82595":  "Bundesliga",
}

# Correspondance par mots-clés et non par libellé exact : le service de
# libellés Wikidata renvoie l'anglais quand la traduction française manque,
# et les valeurs vont du très générique ("défenseur") au très précis
# ("arrière latéral droit"). L'ordre compte : le plus spécifique d'abord.
# Le chiffre est un rang de précision, utilisé quand un joueur porte
# plusieurs postes : on garde le plus informatif.
POSTES = [
    (("gardien", "goalkeeper"),                                  "GB",  3),
    (("arriere droit", "right-back", "right back", "lateral droit"), "DD", 3),
    (("arriere gauche", "left-back", "left back", "lateral gauche"), "DG", 3),
    (("defenseur central", "centre-back", "center-back", "central defender",
      "stoppeur", "centerhalf", "centre half", "libero", "sweeper"), "DC", 3),
    (("milieu defensif", "defensive midfield"),                  "MDF", 3),
    (("milieu offensif", "attacking midfield",
      "meneur de jeu", "meneur", "playmaker"),                   "MO",  3),
    (("demi-centre", "demi centre", "central midfield"),         "MC",  3),
    (("ailier droit", "right winger"),                           "AD",  3),
    (("ailier gauche", "left winger"),                           "AG",  3),
    (("avant-centre", "centre-forward", "center-forward", "striker"), "BU", 3),
    (("ailier", "winger"),                                       "AD",  2),
    # Génériques : on ne devine pas un poste précis à partir d'un libellé vague.
    (("milieu", "midfield"),                                     "MIL", 1),
    (("defenseur", "defender", "arriere", "back"),               "DEF", 1),
    (("attaquant", "forward"),                                   "ATT", 1),
]


def sans_accents(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t.lower())
                   if unicodedata.category(c) != "Mn")


def poste_depuis(libelle):
    """Renvoie (code, rang) ou (None, 0) si aucun mot-clé ne correspond."""
    t = sans_accents(libelle)
    for mots, code, rang in POSTES:
        if any(m in t for m in mots):
            return code, rang
    return None, 0


CONTINENTS = {
    # Europe
    "France": "Europe", "Angleterre": "Europe", "Espagne": "Europe",
    "Italie": "Europe", "Allemagne": "Europe", "Portugal": "Europe",
    "Pays-Bas": "Europe", "Belgique": "Europe", "Croatie": "Europe",
    "Serbie": "Europe", "Pologne": "Europe", "Norvège": "Europe",
    "Suède": "Europe", "Danemark": "Europe", "Suisse": "Europe",
    "Autriche": "Europe", "Écosse": "Europe", "Turquie": "Europe",
    "Géorgie": "Europe", "Hongrie": "Europe", "Ukraine": "Europe",
    "Grèce": "Europe", "Roumanie": "Europe", "Bulgarie": "Europe",
    "Tchéquie": "Europe", "Slovaquie": "Europe", "Slovénie": "Europe",
    "Bosnie-Herzégovine": "Europe", "Monténégro": "Europe", "Albanie": "Europe",
    "Kosovo": "Europe", "Macédoine du Nord": "Europe", "Islande": "Europe",
    "Irlande": "Europe", "Irlande du Nord": "Europe", "Pays de Galles": "Europe",
    "Finlande": "Europe", "Russie": "Europe", "Biélorussie": "Europe",
    "Estonie": "Europe", "Lettonie": "Europe", "Lituanie": "Europe",
    "Luxembourg": "Europe", "Chypre": "Europe", "Malte": "Europe",
    "Moldavie": "Europe", "Arménie": "Europe", "Azerbaïdjan": "Europe",

    # Amérique du Sud
    "Brésil": "Amérique du Sud", "Argentine": "Amérique du Sud",
    "Uruguay": "Amérique du Sud", "Colombie": "Amérique du Sud",
    "Équateur": "Amérique du Sud", "Chili": "Amérique du Sud",
    "Pérou": "Amérique du Sud", "Paraguay": "Amérique du Sud",
    "Venezuela": "Amérique du Sud", "Bolivie": "Amérique du Sud",
    "Suriname": "Amérique du Sud",

    # Amérique du Nord et Caraïbes
    "États-Unis": "Amérique du Nord", "Canada": "Amérique du Nord",
    "Mexique": "Amérique du Nord", "Costa Rica": "Amérique du Nord",
    "Honduras": "Amérique du Nord", "Panama": "Amérique du Nord",
    "Salvador": "Amérique du Nord", "Guatemala": "Amérique du Nord",
    "Jamaïque": "Amérique du Nord", "Haïti": "Amérique du Nord",
    "Cuba": "Amérique du Nord", "République dominicaine": "Amérique du Nord",
    "Trinité-et-Tobago": "Amérique du Nord", "Curaçao": "Amérique du Nord",

    # Afrique
    "Maroc": "Afrique", "Sénégal": "Afrique", "Nigeria": "Afrique",
    "Algérie": "Afrique", "Égypte": "Afrique", "Ghana": "Afrique",
    "Cameroun": "Afrique", "Côte d'Ivoire": "Afrique", "Mali": "Afrique",
    "Tunisie": "Afrique", "Gabon": "Afrique", "Guinée": "Afrique",
    "Guinée-Bissau": "Afrique", "Guinée équatoriale": "Afrique",
    "Burkina Faso": "Afrique", "Gambie": "Afrique", "Liberia": "Afrique",
    "RD Congo": "Afrique", "Congo": "Afrique", "Angola": "Afrique",
    "Cap-Vert": "Afrique", "Comores": "Afrique", "Kenya": "Afrique",
    "Tanzanie": "Afrique", "Zambie": "Afrique", "Zimbabwe": "Afrique",
    "Afrique du Sud": "Afrique", "Togo": "Afrique", "Bénin": "Afrique",
    "Niger": "Afrique", "Tchad": "Afrique", "Mauritanie": "Afrique",
    "Sierra Leone": "Afrique", "Madagascar": "Afrique", "Mozambique": "Afrique",
    "Libye": "Afrique", "Soudan": "Afrique", "Ouganda": "Afrique",
    "Burundi": "Afrique", "Somalie": "Afrique", "Namibie": "Afrique",

    # Asie
    "Japon": "Asie", "Corée du Sud": "Asie", "Iran": "Asie",
    "Arabie saoudite": "Asie", "Israël": "Asie", "Koweït": "Asie",
    "Qatar": "Asie", "Émirats arabes unis": "Asie", "Irak": "Asie",
    "Syrie": "Asie", "Liban": "Asie", "Jordanie": "Asie",
    "Ouzbékistan": "Asie", "Chine": "Asie", "Indonésie": "Asie",
    "Thaïlande": "Asie", "Philippines": "Asie", "Inde": "Asie",
    "Viêt Nam": "Asie", "Malaisie": "Asie",

    # Océanie
    "Australie": "Océanie", "Nouvelle-Zélande": "Océanie",
}


# Correspondance par mots-clés et non par libellé exact : le service de
# libellés Wikidata renvoie l'anglais quand la traduction française manque,
# et les valeurs vont du très générique ("défenseur") au très précis
# ("arrière latéral droit"). L'ordre compte : le plus spécifique d'abord.
# Le chiffre est un rang de précision, utilisé quand un joueur porte
# plusieurs postes : on garde le plus informatif.



WIKI_API = "https://en.wikipedia.org/w/api.php"

MODELE_JOUEUR = re.compile(r"\{\{\s*[Ff]s player(.*?)\}\}", re.S)
CHAMP_NUM     = re.compile(r"\bno\s*=\s*(\d+)")
CHAMP_NOM     = re.compile(r"\bname\s*=\s*\[\[\s*([^\]|]+?)\s*[\]|]")
# Libellé affiché quand le lien est de la forme [[Cible|Affiché]]
ALIAS_LIEN    = re.compile(r"\bname\s*=\s*\[\[[^\]|]+\|\s*([^\]]+?)\s*\]\]")
# Les sections de prêts sortants utilisent le même modèle : on coupe avant.
SECTION_PRET  = re.compile(r"^=+\s*[^=]*\b([Oo]ut on loan|[Ll]oaned out)\b[^=]*=+\s*$", re.M)


def effectifs(titres):
    """{titre_club: {titre_joueur: numero}} pour une liste d'articles."""
    resultat = {}
    # L'API accepte 50 titres par appel ; on reste à 20 par prudence.
    for i in range(0, len(titres), 20):
        lot = titres[i:i + 20]
        try:
            r = requests.get(
                WIKI_API,
                params={"action": "query", "prop": "revisions", "rvprop": "content",
                        "rvslots": "main", "format": "json", "formatversion": "2",
                        "titles": "|".join(lot)},
                headers={"User-Agent": "ONZE-dataset/1.0 (jeu de devinette footballeurs)"},
                timeout=90,
            )
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", [])
        except Exception as e:
            print(f"    ⚠ effectifs indisponibles pour ce lot : {e}", file=sys.stderr)
            continue

        for page in pages:
            if "revisions" not in page:
                continue
            texte = page["revisions"][0]["slots"]["main"]["content"]

            coupe = SECTION_PRET.search(texte)
            if coupe:
                texte = texte[:coupe.start()]

            squad = {}
            for bloc in MODELE_JOUEUR.findall(texte):
                nom = CHAMP_NOM.search(bloc)
                if not nom:
                    continue
                num = CHAMP_NUM.search(bloc)
                cible = nom.group(1).strip()
                numero = int(num.group(1)) if num else 0
                # Deux clés : le titre d'article et sa forme normalisée. Les
                # modèles pointent souvent vers une redirection dont le nom
                # diffère du titre canonique enregistré dans Wikidata.
                squad[cible] = numero
                squad[sans_accents(cible)] = numero
                alias = ALIAS_LIEN.search(bloc)
                if alias:
                    squad[sans_accents(alias.group(1).strip())] = numero
            if squad:
                resultat[page["title"]] = squad
        time.sleep(0.5)
    return resultat


def interroger(qid, essais=3):
    """Interroge le endpoint, avec relances. Remonte le corps de l'erreur :
    sans ça, un plantage SPARQL se traduit par un simple 'HTTP 500' illisible."""
    for tentative in range(1, essais + 1):
        try:
            r = requests.get(
                ENDPOINT,
                params={"query": REQUETE % ("wd:" + qid), "format": "json"},
                headers={
                    "User-Agent": "ONZE-dataset/1.0 (jeu de devinette footballeurs)",
                    "Accept": "application/sparql-results+json",
                },
                timeout=180,
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} — {r.text[:300]}")
            return r.json()["results"]["bindings"]
        except Exception as e:
            print(f"    tentative {tentative}/{essais} échouée : {e}", file=sys.stderr)
            if tentative == essais:
                raise
            time.sleep(5 * tentative)


def age_au(naissance_iso, aujourdhui):
    n = date.fromisoformat(naissance_iso[:10])
    return aujourdhui.year - n.year - (
        (aujourdhui.month, aujourdhui.day) < (n.month, n.day)
    )


def construire():
    aujourdhui = date.today()
    joueurs = {}
    postes_inconnus = {}

    # Une requête par championnat : une requête globale dépasse
    # systématiquement le délai maximum du endpoint public.
    for qid, nom_champ in CHAMPIONNATS.items():
        print(f"\n▸ {nom_champ} ({qid})", file=sys.stderr)
        try:
            lignes = interroger(qid)
        except Exception as e:
            print(f"  ✗ abandon : {e}", file=sys.stderr)
            continue

        # Effectifs actuels des clubs concernés, depuis Wikipédia.
        clubs = sorted({l["artClub"]["value"] for l in lignes if "artClub" in l})
        print(f"  {len(clubs)} clubs — lecture des effectifs Wikipédia…", file=sys.stderr)
        squads = effectifs(clubs)
        dans_effectif = {}
        for squad in squads.values():
            dans_effectif.update(squad)
        # Un joueur produit plusieurs clés : on compte les titres canoniques.
        listes = {k for k in dans_effectif if k and k[0].isupper()}
        print(f"  {len(squads)} effectifs lus · {len(listes)} joueurs listés",
              file=sys.stderr)
        apparies = set()

        # Compteurs de rejet : sans eux, un dataset vide est indébuggable.
        rejets = {"libelle": 0, "naissance": 0, "poste": 0, "age": 0,
                  "nation": 0, "exclu": 0, "hors_effectif": 0}
        gardes = 0

        for l in lignes:
            nom = l["joueurLabel"]["value"]
            if nom.startswith("Q") and nom[1:].isdigit():
                rejets["libelle"] += 1
                continue
            if "naissance" not in l:
                rejets["naissance"] += 1
                continue

            libelle_poste = l.get("posteLabel", {}).get("value", "")
            poste, rang = poste_depuis(libelle_poste)
            if not poste:
                rejets["poste"] += 1
                if libelle_poste:
                    postes_inconnus[libelle_poste] = postes_inconnus.get(libelle_poste, 0) + 1
                continue

            # ── Filtres, du moins coûteux au plus discriminant ──
            if nom in EXCLUSIONS:
                rejets["exclu"] += 1
                continue

            # Le filtre décisif : présent ou non dans l'effectif publié.
            # Si aucun effectif n'a pu être lu, on ne filtre pas — mieux vaut
            # un dataset bruité qu'un dataset vide.
            art = l.get("artJoueur", {}).get("value", "")
            cle = next((k for k in (art, sans_accents(art), sans_accents(nom))
                        if k in dans_effectif), None)
            if dans_effectif and cle is None:
                rejets["hors_effectif"] += 1
                continue
            if cle:
                apparies.add(cle)

            nat = NAT_ALIAS.get(l["natLabel"]["value"], l["natLabel"]["value"])
            if nat in NAT_TROP_VAGUE:
                rejets["nation"] += 1
                continue

            try:
                age = age_au(l["naissance"]["value"], aujourdhui)
            except ValueError:
                rejets["naissance"] += 1
                continue
            if not 15 <= age <= 41:
                rejets["age"] += 1
                continue

            # ── Valeurs retenues ──
            try:
                numero = int(float(l.get("numero", {}).get("value", 0)))
            except ValueError:
                numero = 0
            # Wikipédia tient les numéros à jour, Wikidata presque jamais :
            # le numéro de l'effectif publié prime quand il existe.
            numero = dans_effectif.get(cle, 0) if cle else numero

            # Année d'arrivée : facultative, 0 si absente.
            try:
                depuis = int(l["debut"]["value"][:4])
            except (KeyError, ValueError):
                depuis = 0
            # Un joueur ressort plusieurs fois : plusieurs postes, ou des prêts
            # dont la date de fin n'a jamais été renseignée. On garde le poste
            # le plus précis, et le numéro s'il finit par apparaître.
            ancien = joueurs.get(nom)
            if ancien:
                if rang > ancien["_rang"]:
                    ancien["poste"] = poste
                    ancien["_rang"] = rang
                if numero and not ancien["num"]:
                    ancien["num"] = numero
                # Plusieurs appartenances ouvertes : la plus récente est le club
                # actuel. Les anciennes sont des fiches jamais clôturées.
                if depuis > ancien["depuis"]:
                    ancien.update(club=l["clubLabel"]["value"], champ=nom_champ,
                                  depuis=depuis)
                continue

            joueurs[nom] = {
                "nom": nom,
                "nat": nat,
                "cont": CONTINENTS.get(nat, "Autre"),
                "champ": nom_champ,
                "club": l["clubLabel"]["value"],
                "poste": poste,
                "age": age,
                "num": numero,
                "depuis": depuis,
                "_rang": rang,
            }
            gardes += 1

        print(f"  {len(lignes)} lignes reçues · {gardes} joueurs retenus", file=sys.stderr)

        # Diagnostic clé : les joueurs présents dans l'effectif publié mais
        # jamais rapprochés d'une fiche Wikidata. Si ce nombre est élevé,
        # le problème est l'appariement, pas les données.
        orphelins = sorted(n for n in listes if n not in apparies
                           and sans_accents(n) not in apparies)
        if orphelins:
            print(f"  ⚠ {len(orphelins)} joueurs d'effectif sans fiche appariée, ex. : "
                  + ", ".join(orphelins[:6]), file=sys.stderr)

        if any(rejets.values()):
            detail = " · ".join(f"{k}: {v}" for k, v in rejets.items() if v)
            print(f"  rejets — {detail}", file=sys.stderr)
        time.sleep(1)  # courtoisie envers le endpoint public

    if postes_inconnus:
        top = sorted(postes_inconnus.items(), key=lambda x: -x[1])[:12]
        print("\n⚠ libellés de poste non reconnus (les plus fréquents) :", file=sys.stderr)
        for lib, n in top:
            print(f"    {n:5}×  {lib}", file=sys.stderr)

    for j in joueurs.values():
        j.pop("_rang", None)
    return sorted(joueurs.values(), key=lambda j: j["nom"])


if __name__ == "__main__":
    sortie = sys.argv[1] if len(sys.argv) > 1 else "joueurs.json"

    print("Extraction Wikidata…", file=sys.stderr)
    data = construire()

    if len(data) < 100:
        print(f"✗ seulement {len(data)} joueurs — extraction jugée incomplète, "
              f"fichier non écrit.", file=sys.stderr)
        sys.exit(1)

    inconnus = {j["nat"] for j in data if j["cont"] == "Autre"}
    if inconnus:
        print(f"⚠ nationalités à ajouter dans CONTINENTS : "
              f"{', '.join(sorted(inconnus))}", file=sys.stderr)

    with open(sortie, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"✔ {len(data)} joueurs écrits dans {sortie}", file=sys.stderr)
