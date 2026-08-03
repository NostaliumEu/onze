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

POSTES = {
    "gardien de but": "GB",
    "défenseur central": "DC",
    "arrière droit": "DD",
    "arrière gauche": "DG",
    "milieu défensif": "MDF",
    "milieu de terrain": "MC",
    "milieu central": "MC",
    "milieu offensif": "MO",
    "ailier gauche": "AG",
    "ailier droit": "AD",
    "avant-centre": "BU",
    "attaquant": "BU",
}

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

  ?joueur wdt:P27 ?nat .
  ?joueur wdt:P569 ?naissance .

  OPTIONAL { ?joueur wdt:P413 ?poste . }
  OPTIONAL { ?appartenance pq:P1618 ?numero . }

  SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en". }
}
"""


def interroger(ligues):
    valeurs = " ".join("wd:" + q for q in ligues)
    r = requests.get(
        ENDPOINT,
        params={"query": REQUETE % valeurs, "format": "json"},
        headers={"User-Agent": "ONZE-dataset/1.0 (jeu de devinette)"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["results"]["bindings"]


def age_au(naissance_iso, aujourdhui):
    n = date.fromisoformat(naissance_iso[:10])
    return aujourdhui.year - n.year - (
        (aujourdhui.month, aujourdhui.day) < (n.month, n.day)
    )


def construire():
    aujourdhui = date.today()
    joueurs = {}

    # Une requête par championnat : le endpoint public coupe à 60 s,
    # une requête globale dépasse systématiquement ce délai.
    for qid, nom_champ in CHAMPIONNATS.items():
        print(f"  {nom_champ}…", file=sys.stderr)
        try:
            lignes = interroger([qid])
        except Exception as e:
            print(f"  ⚠ échec {nom_champ} : {e}", file=sys.stderr)
            continue

        for l in lignes:
            nom = l["joueurLabel"]["value"]
            # Les entités sans libellé traduit ressortent en "Q12345"
            if nom.startswith("Q") and nom[1:].isdigit():
                continue
            if "naissance" not in l:
                continue

            poste_brut = l.get("posteLabel", {}).get("value", "").lower()
            poste = POSTES.get(poste_brut)
            if not poste:
                continue  # sans poste exploitable, la ligne est inutilisable

            nat = l["natLabel"]["value"]
            age = age_au(l["naissance"]["value"], aujourdhui)
            if not 15 <= age <= 45:
                continue

            try:
                numero = int(float(l.get("numero", {}).get("value", 0)))
            except ValueError:
                numero = 0

            # Un joueur peut ressortir plusieurs fois (prêts mal fermés) :
            # on garde l'entrée la plus complète.
            existant = joueurs.get(nom)
            if existant and existant["num"] and not numero:
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
            }
        time.sleep(1)  # courtoisie envers le endpoint public

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
