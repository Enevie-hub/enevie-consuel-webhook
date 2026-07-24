"""
Webhook ENEVIE - Generation automatique du Dossier Technique CONSUEL
=======================================================================
Gere deux configurations Bourgeois Global :
  - Micro-onduleurs seuls, sans batterie (gabarit SC144C2-2)
  - Onduleur hybride + batterie Aura (gabarit SC144C-5)
"""

import os
import re
import io
import glob
import unicodedata
import requests
import traceback
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

MONDAY_API_TOKEN = os.environ["MONDAY_API_TOKEN"]
MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_FILE_UPLOAD_URL = "https://api.monday.com/v2/file"

BOARD_ID = "5089742583"
COL_PROJETS = "texte1"
COL_ADRESSE = "lieu"
COL_TELEPHONE = "numero"
COL_DATE_SIGNATURE = "timeline"
COL_CONSUEL_STATUS = "statut1__1"
COL_FILES = "file_mm5hhfhc"

MO_TEMPLATE_PATH = "DT_MO_template.pdf"
BATTERY_TEMPLATE_PATH = "DT_battery_clean.pdf"
SIGNATURE_PATH = "signature_transparent.png"
STAMP_PATH = "tampon_enevie_transparent.png"

INSTALLATEUR_NOM = "ENEVIE"
INSTALLATEUR_TEL = "0787119030"

MODULE_ISC_BNPI = 15.64
MODULE_VOC = 44.7

AURA_MODELS = {
    "5": {"nom": "AURA 5KM BG", "nb_modules_batterie": 1, "capacite_kwh": 5.12},
    "10": {"nom": "AURA 10KM BG", "nb_modules_batterie": 2, "capacite_kwh": 10.24},
}

SCHEMA_KEYWORDS = {
    (3.0, 3, "HMS-1000"): ["3kwc", "hms1000"],
    (6.0, 6, "HMS-1000"): ["6kwc", "hms1000"],
}

# Schemas Aura : cle = (puissance_kwc, modele_aura ex "5" ou "10")
SCHEMA_KEYWORDS_AURA = {
    (3.0, "5"): ["3kwc", "aura5km"],
    (3.0, "10"): ["3kwc", "aura10km"],
    (6.0, "5"): ["6kwc", "aura5km"],
    (6.0, "10"): ["6kwc", "aura10km"],
}


def get_schema_path_aura(puissance_kwc, modele_aura_kw):
    keywords = SCHEMA_KEYWORDS_AURA.get((puissance_kwc, modele_aura_kw))
    if not keywords:
        return None
    for path in glob.glob("*.pdf"):
        if all(kw in _normalize(path) for kw in keywords):
            return path
    return None


def _normalize(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.lower()


def get_schema_path(puissance_kwc, nb_micro_onduleurs, modele_mo):
    keywords = SCHEMA_KEYWORDS.get((puissance_kwc, nb_micro_onduleurs, modele_mo))
    if not keywords:
        return None
    for path in glob.glob("*.pdf"):
        if all(kw in _normalize(path) for kw in keywords):
            return path
    return None


def parse_projets(texte_projets):
    m_puissance = re.search(r"(\d+(?:[.,]\d+)?)\s*kw", texte_projets, re.IGNORECASE)
    if not m_puissance:
        raise ValueError(f"Impossible de determiner la puissance dans : {texte_projets}")
    puissance_kwc = float(m_puissance.group(1).replace(",", "."))
    nb_modules = round(puissance_kwc * 1000 / 500)

    has_aura = bool(re.search(r"\bAURA\b", texte_projets, re.IGNORECASE))
    is_mo_bg = bool(re.search(r"\bMO\s*BG\b", texte_projets, re.IGNORECASE))
    has_other_battery = bool(re.search(r"\bbatt\b", texte_projets, re.IGNORECASE)) and not has_aura

    if has_other_battery:
        raise NotImplementedError(f"Batterie non-Aura detectee, a traiter manuellement : {texte_projets}")

    if has_aura:
        m_aura_kw = re.search(r"AURA\s*(\d+)", texte_projets, re.IGNORECASE)
        if not m_aura_kw or m_aura_kw.group(1) not in AURA_MODELS:
            raise NotImplementedError(f"Modele Aura non reconnu ou non gere : {texte_projets}")
        aura = AURA_MODELS[m_aura_kw.group(1)]
        modules_par_string = round(nb_modules / 2)
        return {
            "type": "aura",
            "puissance_kwc": puissance_kwc,
            "nb_modules": nb_modules,
            "modules_par_string": modules_par_string,
            "marque_modele_complet": f"{aura['nom']} - BOURGEOIS GLOBAL",
            "nb_modules_batterie": aura["nb_modules_batterie"],
            "capacite_kwh": aura["capacite_kwh"],
            "modele_aura_kw": m_aura_kw.group(1),
        }

    if not is_mo_bg:
        raise NotImplementedError(f"Config non reconnue (ni MO BG ni Aura) : {texte_projets}")

    nb_micro_onduleurs = round(nb_modules / 2)
    return {
        "type": "mo",
        "puissance_kwc": puissance_kwc,
        "nb_modules": nb_modules,
        "nb_micro_onduleurs": nb_micro_onduleurs,
        "modele_mo": "HMS-1000",
        "marque_modele_complet": "BOURGEOIS GLOBAL - HMS-1000 (MOBG 1000HMS)",
    }


def get_item_data(item_id):
    query = """
    query ($itemId: [ID!]) {
      items (ids: $itemId) { name column_values { id text } }
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


def split_adresse(adresse_complete):
    parts = adresse_complete.split(",")
    voie = parts[0].strip() if parts else ""
    code_postal, commune = "", ""
    if len(parts) > 1:
        cp_commune = parts[1].strip().split(" ", 1)
        code_postal = cp_commune[0] if cp_commune else ""
        commune = cp_commune[1] if len(cp_commune) > 1 else ""
    return voie, code_postal, commune


def format_date(date_signature):
    try:
        return datetime.fromisoformat(date_signature[:10]).strftime("%d/%m/%Y")
    except Exception:
        return date_signature


def build_fields_mo(client_data, materiel):
    voie, cp, commune = split_adresse(client_data["adresse"])
    date_str = format_date(client_data["date_signature"])
    fields = [
        {"page_number":1,"entry_bounding_box":[123,82,300,91],"entry_text":{"text":INSTALLATEUR_NOM,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[81,103,300,112],"entry_text":{"text":INSTALLATEUR_NOM,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[96,138,383,147],"entry_text":{"text":client_data["nom_client"],"font_size":9}},
        {"page_number":1,"entry_bounding_box":[100,160,558,169],"entry_text":{"text":voie,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[134,187,184,196],"entry_text":{"text":cp,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[194,187,431,196],"entry_text":{"text":commune,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[481,187,558,196],"entry_text":{"text":client_data["telephone"],"font_size":9}},
        {"page_number":1,"entry_bounding_box":[129.9,204.2,137.9,213.2],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[226.1,259.3,234.1,268.3],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[272.2,284.4,280.2,293.4],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[272.3,322.2,280.3,331.2],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[266.9,349.5,274.9,358.5],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[212.3,400.1,220.3,409.1],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[397.5,404.0,405.5,413.0],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[298.7,404.4,383.1,413.4],"entry_text":{"text":date_str,"font_size":8}},
        {"page_number":1,"entry_bounding_box":[222.5,662.9,266.7,671.9],"entry_text":{"text":str(MODULE_ISC_BNPI),"font_size":8}},
        {"page_number":1,"entry_bounding_box":[374.9,662.9,405.9,671.9],"entry_text":{"text":str(MODULE_VOC),"font_size":8}},
        {"page_number":1,"entry_bounding_box":[185.8,680.3,216.9,689.3],"entry_text":{"text":"4","font_size":8}},
        {"page_number":1,"entry_bounding_box":[326.1,680.3,370,689.3],"entry_text":{"text":"1500","font_size":8}},
        {"page_number":1,"entry_bounding_box":[251.1,697.0,259.1,706.0],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[293.0,714.9,343.0,723.9],"entry_text":{"text":str(materiel["nb_micro_onduleurs"]),"font_size":8}},
        {"page_number":1,"entry_bounding_box":[349.0,714.5,357.0,723.5],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[112.2,731.8,510.6,740.8],"entry_text":{"text":materiel["marque_modele_complet"],"font_size":8}},
        {"page_number":1,"entry_bounding_box":[115.6,779.4,123.6,788.4],"entry_text":{"text":"X","font_size":8}},
        {"page_number":2,"entry_bounding_box":[134.3,78.9,142.3,87.9],"entry_text":{"text":"X","font_size":8}},
        {"page_number":2,"entry_bounding_box":[271.2,127.4,279.3,136.4],"entry_text":{"text":"X","font_size":8}},
        {"page_number":2,"entry_bounding_box":[37.9,205.3,45.9,214.3],"entry_text":{"text":"X","font_size":8}},
        {"page_number":3,"entry_bounding_box":[131.9,228.1,262.5,237.1],"entry_text":{"text":INSTALLATEUR_NOM,"font_size":9}},
        {"page_number":3,"entry_bounding_box":[46.6,250.2,262.5,259.2],"entry_text":{"text":INSTALLATEUR_TEL,"font_size":9}},
    ]
    return fields, date_str


def build_fields_aura(client_data, materiel):
    voie, cp, commune = split_adresse(client_data["adresse"])
    date_str = format_date(client_data["date_signature"])
    uocmax = round(materiel["modules_par_string"] * MODULE_VOC, 1)
    cb_capacite_15 = materiel["capacite_kwh"] <= 15

    fields = [
        {"page_number":1,"entry_bounding_box":[121.3,85.5,300,94.5],"entry_text":{"text":INSTALLATEUR_NOM,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[81.1,108.1,300,117.1],"entry_text":{"text":INSTALLATEUR_NOM,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[95.8,149.3,400,158.3],"entry_text":{"text":client_data["nom_client"],"font_size":9}},
        {"page_number":1,"entry_bounding_box":[118.6,170.3,500,179.3],"entry_text":{"text":voie,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[134.5,191.3,184.7,200.3],"entry_text":{"text":cp,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[193.8,191.3,431,200.3],"entry_text":{"text":commune,"font_size":9}},
        {"page_number":1,"entry_bounding_box":[481.2,191.3,558.7,200.3],"entry_text":{"text":client_data["telephone"],"font_size":9}},
        {"page_number":1,"entry_bounding_box":[129.9,208.4,137.9,217.4],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[327.2,266.0,335.2,275.0],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[295.6,280.8,303.6,289.8],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[159.3,361.8,167.3,370.8],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[212.3,403.0,220.3,412.0],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[397.5,406.9,405.5,415.9],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[298.7,407.3,383.1,416.3],"entry_text":{"text":date_str,"font_size":8}},
        {"page_number":1,"entry_bounding_box":[246.3,678.8,275.9,687.8],"entry_text":{"text":str(MODULE_ISC_BNPI),"font_size":8}},
        {"page_number":1,"entry_bounding_box":[344.8,678.8,375.9,687.8],"entry_text":{"text":str(uocmax),"font_size":8}},
        {"page_number":1,"entry_bounding_box":[235.0,696.3,253.7,705.3],"entry_text":{"text":"6","font_size":8}},
        {"page_number":1,"entry_bounding_box":[324.0,696.3,345.1,705.3],"entry_text":{"text":"1500","font_size":8}},
        {"page_number":1,"entry_bounding_box":[234.8,712.8,242.8,721.8],"entry_text":{"text":"X","font_size":8}},
        {"page_number":1,"entry_bounding_box":[314.7,730.8,353.4,739.8],"entry_text":{"text":"1000","font_size":8}},
        {"page_number":1,"entry_bounding_box":[410.6,730.8,437.8,739.8],"entry_text":{"text":"25","font_size":8}},
        {"page_number":2,"entry_bounding_box":[340.3,70.3,365.2,79.3],"entry_text":{"text":"500","font_size":8}},
        {"page_number":2,"entry_bounding_box":[415.1,70.3,442.2,79.3],"entry_text":{"text":"125","font_size":8}},
        {"page_number":2,"entry_bounding_box":[405.6,138.0,436.7,147.0],"entry_text":{"text":"1","font_size":8}},
        {"page_number":2,"entry_bounding_box":[194.3,154.6,202.3,163.6],"entry_text":{"text":"X","font_size":8}},
        {"page_number":2,"entry_bounding_box":[112.2,171.9,530,180.9],"entry_text":{"text":materiel["marque_modele_complet"],"font_size":8}},
        {"page_number":2,"entry_bounding_box":[114.0,202.6,122.0,211.6],"entry_text":{"text":"X","font_size":8}},
        {"page_number":2,"entry_bounding_box":[37.9,466.1,45.9,475.1],"entry_text":{"text":"X","font_size":8}},
        {"page_number":2,"entry_bounding_box":[101.2,517.2,109.2,526.2],"entry_text":{"text":"X","font_size":8}},
        {"page_number":2,"entry_bounding_box":[97.3,610.7,105.4,619.7],"entry_text":{"text":"X","font_size":8}},
        {"page_number":2,"entry_bounding_box":[133.5,680.9,141.5,689.9],"entry_text":{"text":"X","font_size":8}},
        {"page_number":3,"entry_bounding_box":[131.7,94.9,139.7,103.9],"entry_text":{"text":"X","font_size":8}},
        {"page_number":3,"entry_bounding_box":[303.9,183.7,340,192.7],"entry_text":{"text":"48","font_size":8}},
        {"page_number":3,"entry_bounding_box":[355.8,202.2,393.2,211.2],"entry_text":{"text":str(materiel["nb_modules_batterie"]),"font_size":8}},
        {"page_number":3,"entry_bounding_box":[37.9,252.2,45.9,261.2],"entry_text":{"text":"X","font_size":8}},
        {"page_number":3,"entry_bounding_box":[332.4,266.0,340.4,275.0] if cb_capacite_15 else [408.8,266.0,416.8,275.0],"entry_text":{"text":"X","font_size":8}},
        {"page_number":3,"entry_bounding_box":[351.7,421.2,373,430.2],"entry_text":{"text":"20","font_size":8}},
        {"page_number":3,"entry_bounding_box":[383.7,422.5,389.9,429.4],"entry_text":{"text":"X","font_size":8}},
        {"page_number":3,"entry_bounding_box":[349.9,521.1,368.6,530.1],"entry_text":{"text":"125","font_size":8}},
        {"page_number":3,"entry_bounding_box":[424.1,522.3,430.4,529.3],"entry_text":{"text":"X","font_size":7}},
        {"page_number":3,"entry_bounding_box":[469.7,538.7,475.9,545.7],"entry_text":{"text":"X","font_size":7}},
        {"page_number":4,"entry_bounding_box":[118,76,127,86],"entry_text":{"text":"X","font_size":8}},
        {"page_number":4,"entry_bounding_box":[270,121,279,131],"entry_text":{"text":"X","font_size":8}},
        {"page_number":4,"entry_bounding_box":[37,200,46,210],"entry_text":{"text":"X","font_size":8}},
        {"page_number":4,"entry_bounding_box":[132,416,300,428],"entry_text":{"text":INSTALLATEUR_NOM,"font_size":9}},
        {"page_number":4,"entry_bounding_box":[49,437,250,449],"entry_text":{"text":INSTALLATEUR_TEL,"font_size":9}},
    ]
    return fields, date_str


def fill_pdf(template_path, fields):
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader, PdfWriter
    pdf_w, pdf_h = 595.32, 841.92
    reader = PdfReader(template_path)
    overlays = {}
    for i in range(1, len(reader.pages) + 1):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(pdf_w, pdf_h))
        for f in [f for f in fields if f["page_number"] == i]:
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


def add_signature_stamp(pdf_bytes, date_str, sig_page_index, sig_coords, stamp_coords, date_coords):
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader, PdfWriter
    pdf_w, pdf_h = 595.32, 841.92
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pdf_w, pdf_h))
    sig_x, sig_bottom, sig_w = sig_coords
    sig_h = sig_w * (87 / 207)
    c.drawImage(SIGNATURE_PATH, sig_x, pdf_h - sig_bottom, width=sig_w, height=sig_h, mask="auto", preserveAspectRatio=True)
    stamp_x, stamp_top, stamp_w = stamp_coords
    stamp_h = stamp_w * (400 / 761)
    c.drawImage(STAMP_PATH, stamp_x, pdf_h - (stamp_top + stamp_h), width=stamp_w, height=stamp_h, mask="auto", preserveAspectRatio=True)
    date_x, date_top = date_coords
    c.setFont("Helvetica", 9)
    c.drawString(date_x, pdf_h - date_top, date_str)
    c.save()
    buf.seek(0)
    overlay_reader = PdfReader(buf)
    base_reader = PdfReader(pdf_bytes)
    writer = PdfWriter()
    for i, page in enumerate(base_reader.pages):
        if i == sig_page_index:
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out


def upload_to_monday(item_id, pdf_bytes, filename):
    query = """
    mutation ($file: File!) {
      add_file_to_column (item_id: %s, column_id: "%s", file: $file) { id }
    }
    """ % (item_id, COL_FILES)
    files = {"query": (None, query), "variables[file]": (filename, pdf_bytes, "application/pdf")}
    resp = requests.post(MONDAY_FILE_UPLOAD_URL, headers={"Authorization": MONDAY_API_TOKEN}, files=files, timeout=60)
    resp.raise_for_status()
    return resp.json()


def update_status_to_cree(item_id):
    mutation = """
    mutation ($boardId: ID!, $itemId: ID!, $columnId: String!, $value: JSON!) {
      change_column_value (board_id: $boardId, item_id: $itemId, column_id: $columnId, value: $value) { id }
    }
    """
    requests.post(
        MONDAY_API_URL,
        json={"query": mutation, "variables": {"boardId": BOARD_ID, "itemId": item_id, "columnId": COL_CONSUEL_STATUS, "value": '{"label":"Créé"}'}},
        headers={"Authorization": MONDAY_API_TOKEN},
        timeout=30,
    )


@app.route("/webhook/consuel", methods=["POST"])
def webhook_consuel():
    payload = request.json
    if "challenge" in payload:
        return jsonify({"challenge": payload["challenge"]})
    try:
        event = payload["event"]
        item_id = str(event["pulseId"])
        new_label = (event.get("value") or {}).get("label", {}).get("text", "")
        if new_label != "A faire":
            return jsonify({"status": "skipped", "reason": f"Statut '{new_label}' != 'A faire'"}), 200

        client_data = get_item_data(item_id)
        materiel = parse_projets(client_data["projets"])

        if materiel["type"] == "mo":
            fields, date_str = build_fields_mo(client_data, materiel)
            pdf_no_sig = fill_pdf(MO_TEMPLATE_PATH, fields)
            pdf_final = add_signature_stamp(pdf_no_sig, date_str, 2, (320, 312, 85), (458, 245, 110), (317, 333))
            schema_path = get_schema_path(materiel["puissance_kwc"], materiel["nb_micro_onduleurs"], materiel["modele_mo"])
        else:
            fields, date_str = build_fields_aura(client_data, materiel)
            pdf_no_sig = fill_pdf(BATTERY_TEMPLATE_PATH, fields)
            pdf_final = add_signature_stamp(pdf_no_sig, date_str, 3, (350, 390, 85), (465, 330, 100), (314, 411))
            schema_path = get_schema_path_aura(materiel["puissance_kwc"], materiel["modele_aura_kw"])

        filename = f"DT_{client_data['nom_client'].replace(' ', '_')}.pdf"
        upload_to_monday(item_id, pdf_final, filename)

        if schema_path and os.path.exists(schema_path):
            with open(schema_path, "rb") as f:
                schema_bytes = io.BytesIO(f.read())
            upload_to_monday(item_id, schema_bytes, f"Schema_{client_data['nom_client'].replace(' ', '_')}.pdf")

        update_status_to_cree(item_id)
        return jsonify({"status": "ok", "item_id": item_id, "type": materiel["type"]}), 200

    except NotImplementedError as e:
        print(f"[SKIPPED] {e}")
        return jsonify({"status": "skipped", "reason": str(e)}), 200
    except Exception as e:
        print(f"[ERROR] {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "reason": str(e)}), 500


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "enevie-consuel-webhook"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
