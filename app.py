"""
Webhook ENEVIE - Génération automatique du Dossier Technique CONSUEL (config Micro-Onduleurs sans batterie)
=============================================================================================================

CE QUE FAIT CE SCRIPT :
1. Reçoit un webhook de Monday.com quand la colonne "Consuel ST" passe à "A faire"
2. Va chercher les données de l'item sur Monday (client, adresse, PROJETS, date de signature)
3. Parse la colonne PROJETS pour déterminer puissance + matériel
4. Remplit le PDF SC144C2-2 (micro-onduleurs sans batterie) avec ces données
5. Ajoute signature + tampon + date
6. Réuploade le PDF dans la colonne "DT & Schéma générés"
7. Repasse "Consuel ST" à "Créé"

LIMITES DE CETTE PREMIÈRE VERSION :
- Ne gère QUE la config "micro-onduleurs sans batterie" (SC144C2-2). Les dossiers avec
  batterie Aura ou d'autres marques (Sofar, Fox, Thaleos) doivent encore être traités
  manuellement pour l'instant.
- Le gabarit PDF vierge (BLANK_TEMPLATE_PATH) doit être déployé à côté de ce script.
- Les coordonnées de champs sont câblées en dur pour ce gabarit précis. Si CONSUEL change
  le formulaire, il faudra refaire l'extraction de coordonnées (voir méthode utilisée en
  conversation avec Claude : extract_form_structure.py du skill PDF d'Anthropic, ou
  équivalent pdfplumber maison).
"""

import os
import re
import io
import base64
import requests
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

# ============================================================
# CONFIGURATION - à adapter
# ============================================================
MONDAY_API_TOKEN = os.environ["MONDAY_API_TOKEN"]  # à définir dans Render > Environment
MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_FILE_UPLOAD_URL = "https://api.monday.com/v2/file"

BOARD_ID = "5089742583"  # SUIVI DOSSIERS 2026
COL_PROJETS = "texte1"
COL_ADRESSE = "lieu"
COL_TELEPHONE = "numero"  # à vérifier/ajuster : id réel de la colonne téléphone
COL_DATE_SIGNATURE = "timeline"  # à vérifier/ajuster
COL_CONSUEL_STATUS = "statut1__1"  # "Consuel ST"
COL_FILES = "file_mm5hhfhc"  # "DT & Schéma générés"

BLANK_TEMPLATE_PATH = "DT_MO_template.pdf"  # gabarit vierge SC144C2-2, à copier à côté de app.py
SIGNATURE_PATH = "signature_transparent.png"
STAMP_PATH = "tampon_enevie_transparent.png"

INSTALLATEUR_NOM = "ENEVIE"
INSTALLATEUR_TEL = "0787119030"

# Base de données matériel Bourgeois Global (à compléter avec Sofar/Fox/Thaleos plus tard)
MODULE_ISC_BNPI = 15.64
MODULE_VOC = 44.7


# ============================================================
# ETAPE 1 : Parsing de la colonne PROJETS
# ============================================================
def parse_projets(texte_projets: str):
    """
    Exemples gérés :
      "6kw BG 500wc + MO BG"           -> 6kWc, 6x HMS-1000
      "3kw BG 500wc + MO BG + PAC..."  -> 3kWc, 3x HMS-1000 (ignore PAC/BT)
    Règle : "MO BG" sans précision = toujours HMS-1000 (1 unité gère 2 panneaux de 500Wc)
    """
    m_puissance = re.search(r"(\d+(?:[.,]\d+)?)\s*kw", texte_projets, re.IGNORECASE)
    if not m_puissance:
        raise ValueError(f"Impossible de déterminer la puissance dans : {texte_projets}")
    puissance_kwc = float(m_puissance.group(1).replace(",", "."))
    nb_modules = round(puissance_kwc * 1000 / 500)

    has_battery = bool(re.search(r"\bbatt\b|\bAURA\b", texte_projets, re.IGNORECASE))
    is_mo_bg = bool(re.search(r"\bMO\s*BG\b", texte_projets, re.IGNORECASE))

    if has_battery:
        # Dossier avec batterie -> pas géré par ce script pour l'instant
        raise NotImplementedError("Dossier avec batterie détecté : à traiter manuellement (gabarit SC144C-5).")

    if not is_mo_bg:
        raise NotImplementedError(f"Config non reconnue (ni MO BG ni batterie) : {texte_projets}")

    nb_micro_onduleurs = round(nb_modules / 2)

    return {
        "puissance_kwc": puissance_kwc,
        "nb_modules": nb_modules,
        "nb_micro_onduleurs": nb_micro_onduleurs,
        "modele_mo": "HMS-1000",
        "marque_modele_complet": f"BOURGEOIS GLOBAL - HMS-1000 (MOBG 1000HMS)",
    }


# ============================================================
# ETAPE 2 : Récupération des données de l'item Monday
# ============================================================
def get_item_data(item_id: str):
    query = """
    query ($itemId: [ID!]) {
      items (ids: $itemId) {
        name
        column_values {
          id
          text
        }
      }
    }
    """
    resp = requests.post(
        MONDAY_API_URL,
        json={"query": query, "variables": {"itemId": [item_id]}},
        headers={"Authorization": MONDAY_API_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()["data"]["items"][0]
    cols = {c["id"]: c["text"] for c in data["column_values"]}
    return {
        "nom_client": data["name"],
        "projets": cols.get(COL_PROJETS, ""),
        "adresse": cols.get(COL_ADRESSE, ""),
        "telephone": cols.get(COL_TELEPHONE, ""),
        "date_signature": cols.get(COL_DATE_SIGNATURE, ""),
    }


# ============================================================
# ETAPE 3 : Remplissage du PDF (mêmes coordonnées que validées en conversation)
# ============================================================
def build_fields(client_data, materiel):
    """Retourne la liste de champs à remplir, coordonnées EXACTES validées manuellement."""
    adresse_parts = client_data["adresse"].split(",")
    adresse_voie = adresse_parts[0].strip() if adresse_parts else ""
    code_postal = ""
    commune = ""
    if len(adresse_parts) > 1:
        cp_commune = adresse_parts[1].strip().split(" ", 1)
        code_postal = cp_commune[0] if cp_commune else ""
        commune = cp_commune[1] if len(cp_commune) > 1 else ""

    try:
        date_obj = datetime.fromisoformat(client_data["date_signature"][:10])
        date_str = date_obj.strftime("%d/%m/%Y")
    except Exception:
        date_str = client_data["date_signature"]

    # NOTE : coordonnées identiques à fields_mo_v2.json validé en conversation Claude
    fields = [
        {"page_number":1,"entry_bounding_box":[123,82,300,91],"entry_text":{"text":INSTALLATEUR_NOM,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[81,103,300,112],"entry_text":{"text":INSTALLATEUR_NOM,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[96,138,383,147],"entry_text":{"text":client_data["nom_client"],"font_size":9}},
        {"page_number":1,"entry_bounding_box":[100,160,558,169],"entry_text":{"text":adresse_voie,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[134,187,184,196],"entry_text":{"text":code_postal,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[194,187,431,196],"entry_text":{"text":commune,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[481,187,558,196],"entry_text":{"text":client_data["telephone"],"font_size":9}},
        {"page_number":1,"entry_bounding_box":[129.9,204.2,137.9,213.2],"entry_text":{"text":"X","font_size":8}},  # autoconsommation
        {"page_number":1,"entry_bounding_box":[226.1,259.3,234.1,268.3],"entry_text":{"text":"X","font_size":8}},  # toiture
        {"page_number":1,"entry_bounding_box":[272.2,284.4,280.2,293.4],"entry_text":{"text":"X","font_size":8}},  # pas de batterie
        {"page_number":1,"entry_bounding_box":[272.3,322.2,280.3,331.2],"entry_text":{"text":"X","font_size":8}},  # pas mode autonome
        {"page_number":1,"entry_bounding_box":[266.9,349.5,274.9,358.5],"entry_text":{"text":"X","font_size":8}},  # pas autres sources AC
        {"page_number":1,"entry_bounding_box":[212.3,400.1,220.3,409.1],"entry_text":{"text":"X","font_size":8}},  # A2 non
        {"page_number":1,"entry_bounding_box":[397.5,404.0,405.5,413.0],"entry_text":{"text":"X","font_size":8}},  # A3 signature marché
        {"page_number":1,"entry_bounding_box":[298.7,404.4,383.1,413.4],"entry_text":{"text":date_str,"font_size":8}},
        {"page_number":1,"entry_bounding_box":[222.5,662.9,266.7,671.9],"entry_text":{"text":str(MODULE_ISC_BNPI),"font_size":8}},
        {"page_number":1,"entry_bounding_box":[374.9,662.9,405.9,671.9],"entry_text":{"text":str(MODULE_VOC),"font_size":8}},
        {"page_number":1,"entry_bounding_box":[185.8,680.3,216.9,689.3],"entry_text":{"text":"4","font_size":8}},
        {"page_number":1,"entry_bounding_box":[326.1,680.3,370,689.3],"entry_text":{"text":"1500","font_size":8}},
        {"page_number":1,"entry_bounding_box":[251.1,697.0,259.1,706.0],"entry_text":{"text":"X","font_size":8}},  # temp 120C
        {"page_number":1,"entry_bounding_box":[293.0,714.9,343.0,723.9],"entry_text":{"text":str(materiel["nb_micro_onduleurs"]),"font_size":8}},
        {"page_number":1,"entry_bounding_box":[349.0,714.5,357.0,723.5],"entry_text":{"text":"X","font_size":8}},  # MO monophasé
        {"page_number":1,"entry_bounding_box":[112.2,731.8,510.6,740.8],"entry_text":{"text":materiel["marque_modele_complet"],"font_size":8}},
        {"page_number":1,"entry_bounding_box":[115.6,779.4,123.6,788.4],"entry_text":{"text":"X","font_size":8}},  # découplage intégré

        {"page_number":2,"entry_bounding_box":[134.3,78.9,142.3,87.9],"entry_text":{"text":"X","font_size":8}},  # puissance limitée
        {"page_number":2,"entry_bounding_box":[271.2,127.4,279.3,136.4],"entry_text":{"text":"X","font_size":8}},  # tableau principal
        {"page_number":2,"entry_bounding_box":[37.9,205.3,45.9,214.3],"entry_text":{"text":"X","font_size":8}},  # Cas 3 (toujours)

        {"page_number":3,"entry_bounding_box":[131.9,228.1,262.5,237.1],"entry_text":{"text":INSTALLATEUR_NOM,"font_size":9}},
        {"page_number":3,"entry_bounding_box":[46.6,250.2,262.5,259.2],"entry_text":{"text":INSTALLATEUR_TEL,"font_size":9}},
    ]
    return fields, date_str


def fill_pdf(fields):
    """Utilise reportlab pour créer un overlay puis pypdf pour le fusionner (même logique que la conversation)."""
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader, PdfWriter

    pdf_w, pdf_h = 595.32, 841.92
    reader = PdfReader(BLANK_TEMPLATE_PATH)
    num_pages = len(reader.pages)

    overlays = {}
    for i in range(1, num_pages + 1):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(pdf_w, pdf_h))
        page_fields = [f for f in fields if f["page_number"] == i]
        for f in page_fields:
            x0, top, x1, bottom = f["entry_bounding_box"]
            c.setFont("Helvetica", f["entry_text"]["font_size"])
            c.drawString(x0 + 1, pdf_h - bottom + 2, f["entry_text"]["text"])
        c.save()
        buf.seek(0)
        overlays[i] = PdfReader(buf).pages[0]

    writer = PdfWriter()
    for i, page in enumerate(reader.pages, start=1):
        if i in overlays:
            page.merge_page(overlays[i])
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out


def add_signature_stamp(pdf_bytes, date_str):
    """Ajoute signature + tampon + date sur la page 3 (page Signature/Cachet du gabarit MO)."""
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader, PdfWriter

    pdf_w, pdf_h = 595.32, 841.92
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pdf_w, pdf_h))

    sig_w = 85
    sig_h = sig_w * (87 / 207)
    c.drawImage(SIGNATURE_PATH, 320, pdf_h - 312, width=sig_w, height=sig_h, mask="auto", preserveAspectRatio=True)

    stamp_w = 110
    stamp_h = stamp_w * (400 / 761)
    c.drawImage(STAMP_PATH, 458, pdf_h - (245 + stamp_h), width=stamp_w, height=stamp_h, mask="auto", preserveAspectRatio=True)

    c.setFont("Helvetica", 9)
    c.drawString(317, pdf_h - 333, date_str)
    c.save()
    buf.seek(0)

    overlay_reader = PdfReader(buf)
    base_reader = PdfReader(pdf_bytes)
    writer = PdfWriter()
    for i, page in enumerate(base_reader.pages):
        if i == 2:  # page 3 (0-indexed)
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out


# Mapping (puissance, nb_micro_onduleurs, modele) -> fichier schéma unifilaire standard
# Clé explicite à 3 éléments pour ne jamais confondre avec une autre config de même puissance
# (ex: 6kWc peut aussi se faire en 3x HMS-2000, ce qui serait un schéma différent)
import glob
import unicodedata

# Mapping (puissance, nb_micro_onduleurs, modele) -> mots-clés à chercher dans le nom de fichier
# Recherche souple (insensible aux accents/casse) pour éviter les soucis d'encodage de noms de fichiers
SCHEMA_KEYWORDS = {
    (3.0, 3, "HMS-1000"): ["3kwc", "hms1000"],
    (6.0, 6, "HMS-1000"): ["6kwc", "hms1000"],
}


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.lower()


def get_schema_path(puissance_kwc: float, nb_micro_onduleurs: int, modele_mo: str):
    keywords = SCHEMA_KEYWORDS.get((puissance_kwc, nb_micro_onduleurs, modele_mo))
    if not keywords:
        return None
    for path in glob.glob("*.pdf"):
        norm = _normalize(path)
        if all(kw in norm for kw in keywords):
            return path
    return None


# ============================================================
# ETAPE 4 : Réupload vers Monday (multipart direct vers api.monday.com)
# ============================================================
def upload_to_monday(item_id: str, pdf_bytes, filename: str):
    query = """
    mutation ($file: File!) {
      add_file_to_column (item_id: %s, column_id: "%s", file: $file) {
        id
      }
    }
    """ % (item_id, COL_FILES)

    files = {
        "query": (None, query),
        "variables[file]": (filename, pdf_bytes, "application/pdf"),
    }
    resp = requests.post(
        MONDAY_FILE_UPLOAD_URL,
        headers={"Authorization": MONDAY_API_TOKEN},
        files=files,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def update_status_to_cree(item_id: str):
    mutation = """
    mutation ($boardId: ID!, $itemId: ID!, $columnId: String!, $value: JSON!) {
      change_column_value (board_id: $boardId, item_id: $itemId, column_id: $columnId, value: $value) {
        id
      }
    }
    """
    requests.post(
        MONDAY_API_URL,
        json={
            "query": mutation,
            "variables": {
                "boardId": BOARD_ID,
                "itemId": item_id,
                "columnId": COL_CONSUEL_STATUS,
                "value": '{"label":"Créé"}',
            },
        },
        headers={"Authorization": MONDAY_API_TOKEN},
        timeout=30,
    )


# ============================================================
# ROUTE WEBHOOK
# ============================================================
@app.route("/webhook/consuel", methods=["POST"])
def webhook_consuel():
    payload = request.json

    # Monday envoie un "challenge" la première fois pour vérifier l'URL : il faut le renvoyer tel quel
    if "challenge" in payload:
        return jsonify({"challenge": payload["challenge"]})

    try:
        event = payload["event"]
        item_id = str(event["pulseId"])

        # Ce webhook se déclenche à chaque changement de la colonne "Consuel ST" :
        # on ne traite que si la nouvelle valeur est "A faire"
        new_label = (event.get("value") or {}).get("label", {}).get("text", "")
        if new_label != "A faire":
            return jsonify({"status": "skipped", "reason": f"Statut '{new_label}' != 'A faire'"}), 200

        client_data = get_item_data(item_id)
        materiel = parse_projets(client_data["projets"])
        fields, date_str = build_fields(client_data, materiel)

        pdf_no_sig = fill_pdf(fields)
        pdf_final = add_signature_stamp(pdf_no_sig, date_str)

        filename = f"DT_{client_data['nom_client'].replace(' ', '_')}.pdf"
        upload_to_monday(item_id, pdf_final, filename)

        # Schéma unifilaire correspondant (si on a un gabarit standard pour cette config exacte)
        schema_path = get_schema_path(
            materiel["puissance_kwc"], materiel["nb_micro_onduleurs"], materiel["modele_mo"]
        )
        if not schema_path:
            print(f"[DEBUG] Schema non trouve. Fichiers PDF presents: {glob.glob('*.pdf')}")
        if schema_path and os.path.exists(schema_path):
            with open(schema_path, "rb") as f:
                schema_bytes = io.BytesIO(f.read())
            schema_filename = f"Schema_{client_data['nom_client'].replace(' ', '_')}.pdf"
            upload_to_monday(item_id, schema_bytes, schema_filename)

        update_status_to_cree(item_id)

        return jsonify({"status": "ok", "item_id": item_id}), 200

    except NotImplementedError as e:
        # Config pas encore gérée (batterie, autre marque) -> ne bloque pas, juste log
        return jsonify({"status": "skipped", "reason": str(e)}), 200
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "enevie-consuel-webhook"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
