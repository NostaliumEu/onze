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
import sys
import time
from datetime import date

import requests

ENDPOINT = "https://query.wikidata.org/sparql"

# Une appartenance commencée avant cette date est considérée périmée.
# Six ans conserve les joueurs fidèles à un même club sans ramener les
# fiches jamais clôturées des années 2010. Combiné au plafond d'âge,
# c'est ce qui écarte les retraités.
DEPUIS = f"{date.today().year - 6}-01-01T00:00:00Z"

# Wikidata donne "Royaume-Uni" comme citoyenneté : inutilisable pour du foot,
# où les quatre nations jouent séparément. Sans pays plus précis, on écarte.
NAT_TROP_VAGUE = {"Royaume-Uni", "United Kingdom"}

# ⚠ À VÉRIFIER avant la première exécution : ouvre chaque identifiant sur
# wikidata.org pour confirmer qu'il pointe bien vers la bonne compétition.
# Les identifiants ci-dessous sont donnés de mémoire et peuvent être erronés.
CHAMPIONNATS = {
    "Q13394":  "Ligue 1",
    "Q9448":   "Premier League",
    "Q324867": "Liga",
    "Q15804":  "Serie A",
    "Q82595":  "Bundesliga",
    "Q194052": "Primeira Liga",
    "Q223832": "Eredivisie",
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
    (("defenseur central", "centre-back", "center-back", "central defender"), "DC", 3),
    (("milieu defensif", "defensive midfield"),                  "MDF", 3),
    (("milieu offensif", "attacking midfield"),                  "MO",  3),
    (("ailier droit", "right winger"),                           "AD",  3),
    (("ailier gauche", "left winger"),                           "AG",  3),
    (("avant-centre", "centre-forward", "center-forward", "striker"), "BU", 3),
    (("ailier", "winger"),                                       "AD",  2),
    (("milieu", "midfield"),                                     "MC",  1),
    (("defenseur", "defender", "arriere", "back"),               "DC",  1),
    (("attaquant", "forward"),                                   "BU",  1),
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
    "France": "Europe", "Angleterre": "Europe", "Espagne": "Europe",
    "Italie": "Europe", "Allemagne": "Europe", "Portugal": "Europe",
    "Pays-Bas": "Europe", "Belgique": "Europe", "Croatie": "Europe",
    "Serbie": "Europe", "Pologne": "Europe", "Norvège": "Europe",
    "Suède": "Europe", "Danemark": "Europe", "Suisse": "Europe",
    "Autriche": "Europe", "Écosse": "Europe", "Turquie": "Europe",
    "Géorgie": "Europe", "Hongrie": "Europe", "Ukraine": "Europe",
    "Brésil": "Amérique du Sud", "Argentine": "Amérique du Sud",
    "Uruguay": "Amérique du Sud", "Colombie": "Amérique du Sud",
    "Équateur": "Amérique du Sud", "Chili": "Amérique du Sud",
    "États-Unis": "Amérique du Nord", "Canada": "Amérique du Nord",
    "Mexique": "Amérique du Nord",
    "Maroc": "Afrique", "Sénégal": "Afrique", "Nigeria": "Afrique",
    "Algérie": "Afrique", "Égypte": "Afrique", "Ghana": "Afrique",
    "Cameroun": "Afrique", "Côte d'Ivoire": "Afrique", "Mali": "Afrique",
    "Japon": "Asie", "Corée du Sud": "Asie", "Iran": "Asie",
    "Australie": "Océanie",
}

REQUETE = """
SELECT DISTINCT ?joueurLabel ?natLabel ?clubLabel ?ligue ?posteLabel ?naissance ?numero
WHERE {
  VALUES ?ligue { %s }

  ?club wdt:P118 ?ligue .

  # Appartenance à un club SANS date de fin = club actuel
  ?joueur p:P54 ?appartenance .
  ?appartenance ps:P54 ?club .
  FILTER NOT EXISTS { ?appartenance pq:P582 ?fin . }

  # Un contrat sans date de fin ne suffit pas : des milliers d'appartenances
  # anciennes n'ont jamais été clôturées sur Wikidata. On exige donc une date
  # de début récente, ce qui écarte les joueurs partis depuis longtemps.
  ?appartenance pq:P580 ?debut .
  FILTER(?debut >= "%s"^^xsd:dateTime)

  ?joueur wdt:P27 ?nat .
  ?joueur wdt:P569 ?naissance .

  OPTIONAL { ?joueur wdt:P413 ?poste . }
  OPTIONAL { ?appartenance pq:P1618 ?numero . }

  SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en". }
}
"""


def interroger(qid, essais=3):
    """Interroge le endpoint, avec relances. Remonte le corps de l'erreur :
    sans ça, un plantage SPARQL se traduit par un simple 'HTTP 500' illisible."""
    for tentative in range(1, essais + 1):
        try:
            r = requests.get(
                ENDPOINT,
                params={"query": REQUETE % ("wd:" + qid, DEPUIS), "format": "json"},
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

        # Compteurs de rejet : sans eux, un dataset vide est indébuggable.
        rejets = {"libelle": 0, "naissance": 0, "poste": 0, "age": 0, "nation": 0}
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

            try:
                age = age_au(l["naissance"]["value"], aujourdhui)
            except ValueError:
                rejets["naissance"] += 1
                continue
            if not 15 <= age <= 41:
                rejets["age"] += 1
                continue

            try:
                numero = int(float(l.get("numero", {}).get("value", 0)))
            except ValueError:
                numero = 0
            # Le numéro reste facultatif : P1618 n'est renseigné que sur une
            # minorité de fiches, y compris pour des titulaires. Le jeu affiche
            # "—" et neutralise la comparaison quand il manque.

            nat = l["natLabel"]["value"]
            if nat in NAT_TROP_VAGUE:
                rejets["nation"] += 1
                continue
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
                "_rang": rang,
            }
            gardes += 1

        print(f"  {len(lignes)} lignes reçues · {gardes} joueurs retenus", file=sys.stderr)
        sans_num = sum(1 for j in joueurs.values() if j["champ"] == nom_champ and not j["num"])
        if gardes:
            print(f"  dont {sans_num} sans numéro de maillot (affichés « — »)", file=sys.stderr)
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
