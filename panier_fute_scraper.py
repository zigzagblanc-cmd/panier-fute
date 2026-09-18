#!/usr/bin/env python3
"""
panier_fute_scraper.py — pour Panier futé

Interroge le point d'accès PUBLIC mais NON OFFICIEL de Flipp (backflipp.wishabi.com),
qui alimente aussi reebee (même entreprise depuis le rachat de 2022), pour récupérer
les rabais actifs des magasins choisis à un code postal donné.

⚠️ À LIRE AVANT D'UTILISER CE SCRIPT
- Ce point d'accès n'est ni documenté ni garanti par Flipp/Wishabi. Il peut changer
  ou cesser de fonctionner sans préavis — c'est le compromis pour que ce soit gratuit.
- Vous êtes responsable du respect des conditions d'utilisation de Flipp.
- L'approche (flyers -> flyer -> items) a été recoupée à partir de plusieurs projets
  open source indépendants : github.com/Kiizon/flippscrape,
  github.com/grunt3714-lgtm/pricewise (MIT, doc à jour avril 2026), et un script de
  référence plus ancien (gist jQwotos/37c54992881824d7084680ee91c9633c). Elle n'a PAS
  été testée en conditions réelles par Claude au moment d'écrire ce script.
- Si le script tourne mais que le fichier de sortie est vide, lancez-le avec
  --debug : il écrira la réponse brute de l'API dans debug_flyers.json pour que vous
  puissiez ajuster les noms de champs ci-dessous (voir FIELD MAP) en quelques minutes.

Usage local :
    python panier_fute_scraper.py J2G1A1 --stores "IGA,Maxi,Metro,Super C"

Usage GitHub Actions : voir .github/workflows/rabais-hebdo.yml — le code postal et
la liste de magasins sont pris dans les variables de dépôt (Settings > Secrets and
variables > Actions > Variables), pas écrits en dur ici, pour ne pas exposer votre
code postal dans un dépôt public.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

BACKEND = "https://backflipp.wishabi.com/flipp"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PanierFute/1.0; +personal-use)"}
REQUEST_DELAY_SEC = 0.6  # on ne martèle pas le serveur

# --- FIELD MAP ---------------------------------------------------------
# Noms de champs attendus dans les réponses JSON de Flipp, d'après les sources
# citées plus haut. Si --debug montre des noms différents, ajustez ici.
FLYER_LIST_KEYS = ["flyers", "items"]          # clé contenant la liste de circulaires
FLYER_MERCHANT_KEYS = ["merchant", "merchant_name"]
FLYER_ID_KEYS = ["id", "flyer_id"]
ITEM_LIST_KEYS = ["flyer_items", "items"]      # clé contenant la liste d'articles d'un circulaire
ITEM_NAME_KEYS = ["name", "clean_name"]
ITEM_PRICE_KEYS = ["current_price", "price"]
ITEM_VALID_FROM_KEYS = ["valid_from"]
ITEM_VALID_TO_KEYS = ["valid_to"]
# ------------------------------------------------------------------------


def first_present(d, keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d.get(k)
    return None


def normalize(s):
    return (s or "").strip().lower()


def get_flyers(postal_code, session):
    resp = session.get(f"{BACKEND}/flyers", params={"postal_code": postal_code}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    for key in FLYER_LIST_KEYS:
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


def get_flyer_items(flyer_id, postal_code, session):
    resp = session.get(f"{BACKEND}/flyers/{flyer_id}", params={"postal_code": postal_code}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    for key in ITEM_LIST_KEYS:
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


def parse_price(raw):
    if raw is None:
        return None
    try:
        return round(float(str(raw).replace("$", "").replace(",", ".").strip()), 2)
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(description="Génère rabais.json pour Panier futé à partir de Flipp.")
    parser.add_argument("postal_code", nargs="?", default=os.environ.get("PANIER_FUTE_POSTAL_CODE"))
    parser.add_argument("--stores", default=os.environ.get("PANIER_FUTE_STORES", "IGA,Maxi,Metro,Super C"))
    parser.add_argument("--out", default="data/rabais.json")
    parser.add_argument("--debug", action="store_true", help="Écrit la réponse brute dans debug_flyers.json")
    args = parser.parse_args()

    if not args.postal_code:
        print("Erreur : fournissez un code postal (argument, ou variable PANIER_FUTE_POSTAL_CODE).", file=sys.stderr)
        sys.exit(1)

    postal_code = args.postal_code.replace(" ", "").upper()
    wanted_stores = [normalize(s) for s in args.stores.split(",") if s.strip()]

    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"Recherche des circulaires pour {postal_code}...")
    try:
        flyers = get_flyers(postal_code, session)
    except requests.RequestException as e:
        print(f"Erreur réseau en récupérant les circulaires : {e}", file=sys.stderr)
        sys.exit(2)

    if args.debug:
        os.makedirs("debug", exist_ok=True)
        with open("debug/debug_flyers.json", "w", encoding="utf-8") as f:
            json.dump(flyers, f, ensure_ascii=False, indent=2)
        print("Mode debug : réponse brute écrite dans debug/debug_flyers.json")

    if not flyers:
        print("Aucune donnée reçue — vérifiez le code postal, ou lancez avec --debug pour inspecter la réponse.", file=sys.stderr)

    matched = []
    for f in flyers:
        merchant = first_present(f, FLYER_MERCHANT_KEYS)
        if normalize(merchant) in wanted_stores or any(normalize(merchant).find(s) != -1 or s.find(normalize(merchant)) != -1 for s in wanted_stores if s):
            fid = first_present(f, FLYER_ID_KEYS)
            if fid:
                matched.append({"id": fid, "merchant": merchant})

    print(f"{len(matched)} circulaire(s) correspondant à vos magasins ({args.stores}).")

    results = []
    for flyer in matched:
        try:
            items = get_flyer_items(flyer["id"], postal_code, session)
        except requests.RequestException as e:
            print(f"  ! Impossible de lire le circulaire {flyer['id']} ({flyer['merchant']}) : {e}", file=sys.stderr)
            continue

        for item in items:
            name = first_present(item, ITEM_NAME_KEYS)
            price = parse_price(first_present(item, ITEM_PRICE_KEYS))
            if not name or price is None:
                continue  # bannière publicitaire ou item sans prix affiché : on l'ignore
            results.append({
                "ingredient": str(name).strip(),
                "magasin": flyer["merchant"],
                "prix": price,
                "valide_du": first_present(item, ITEM_VALID_FROM_KEYS),
                "valide_au": first_present(item, ITEM_VALID_TO_KEYS),
                "familial": False,
            })
        time.sleep(REQUEST_DELAY_SEC)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    output = {
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "code_postal": postal_code,
        "magasins_demandes": args.stores.split(","),
        "rabais": results,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"{len(results)} rabais écrits dans {args.out}")
    if len(results) == 0 and flyers:
        print("Des circulaires ont été trouvées mais aucun article n'a pu être extrait — "
              "relancez avec --debug et ajustez le FIELD MAP en haut du script.", file=sys.stderr)


if __name__ == "__main__":
    main()
