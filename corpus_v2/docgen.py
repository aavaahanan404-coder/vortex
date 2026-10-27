#!/usr/bin/env python3
"""
SIH26261 - Synthetic AICTE-style corpus generator
=================================================
Produces, from one seed:
  world/world.sqlite, world/world_postgres.sql  -> mock authority records (the "outside world")
  documents/<inst_id>/*.pdf                      -> synthetic application documents (clean + tampered)
  manifest.jsonl                                 -> per-document ground truth, bboxes, labels, expected adapter status
  institutions.json                              -> per-institution truth, mutations, expected rule outcomes
  rules/norms.yaml, rules/aicte_rules.yaml       -> editable norms + draft YAML rulebook

EVERYTHING IS SYNTHETIC. All IDs carry a SYN prefix, PANs use an invalid entity code,
and every PDF page carries a "SYNTHETIC TEST DATA" watermark/footer (disable with --no-watermark).
Norm values in norms.yaml are ILLUSTRATIVE - replace them with Approval Process Handbook values.
"""
import argparse, copy, hashlib, hmac, json, math, os, random, re, shutil, sqlite3, subprocess, sys
from datetime import date, timedelta
import cv2, numpy as np, yaml
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

W, H = A4
APP_YEAR = 2026
TODAY = date(2026, 10, 27)   # PDF metadata dates are capped below this (no future-dated files)
def set_years(y):
    global APP_YEAR, AY, PRIOR_AY, FY
    APP_YEAR = y; AY = f"{y}-{(y + 1) % 100:02d}"; PRIOR_AY = f"{y - 1}-{y % 100:02d}"; FY = f"{y - 2}-{(y - 1) % 100:02d}"
set_years(2026)
QUALITY_T = 300.0         # variance-of-Laplacian threshold used for the *expected* quality-gate label
MAX_SKEW = 5.0            # degrees
SCAN_DPI = 110

DEFAULT_NORMS = {
    "norms_version": "illustrative-v0",
    "WARNING": "Illustrative values (ratio/area figures taken from a third-party consultancy summary). "
               "Replace with Approval Process Handbook 2024-27 values before relying on results.",
    "programme_types": {
        "engineering": {"students_per_faculty": 15, "builtup_sqm_per_60_intake": 10000, "pcs_per_intake_seat": 1.0, "min_lease_years": 30},
        "mba":         {"students_per_faculty": 15, "builtup_sqm_per_60_intake": 2000,  "pcs_per_intake_seat": 0.5, "min_lease_years": 30},
        "polytechnic": {"students_per_faculty": 20, "builtup_sqm_per_60_intake": 5000,  "pcs_per_intake_seat": 1.0, "min_lease_years": 30},
    },
}

# ----------------------------------------------------------------------------- name pools
FIRST = "Arun Kavita Ramesh Sunita Vikram Anjali Suresh Meena Rajesh Priya Karthik Lakshmi Anil Deepa Manoj Shalini Sanjay Revathi Harish Pooja Naveen Divya Prakash Geeta Mohan Sudha Venkat Nisha Ashok Radha Kiran Swati Gopal Uma Dinesh Bhavna Satish Jaya Rohit Neha".split()
LAST = "Sharma Iyer Reddy Patel Nair Singh Gupta Menon Joshi Kulkarni Rao Das Chatterjee Mehta Pillai Naidu Desai Bhat Verma Choudhary Hegde Shetty Agarwal Banerjee Kapoor Yadav Thakur Mishra Pandey Srinivasan".split()
PATRON = ["Sri Venkateswara", "Vidya Sagar", "Nalanda", "Bharathi", "Kaveri", "Himalaya", "Saraswati", "Godavari", "Ananda", "Lotus Valley",
          "Sahyadri", "Aravali", "Narmada", "Gangotri", "Tungabhadra", "Brahmaputra", "Deccan", "Malabar", "Vindhya", "Shivalik",
          "Chola", "Hoysala", "Kakatiya", "Konark", "Ellora", "Ajanta", "Mandakini", "Kanchan", "Sunrise Heritage", "Pragati"]
KIND_NAME = {"engineering": ["College of Engineering", "Institute of Technology", "Institute of Engineering & Technology"],
             "mba": ["School of Management", "Institute of Management Studies", "Business School"],
             "polytechnic": ["Polytechnic College", "Institute of Polytechnic Studies", "Polytechnic"]}
STATES = {"Tamil Nadu": (["Coimbatore", "Madurai", "Salem", "Tiruchirappalli", "Vellore"], "6", "TN"),
          "Karnataka": (["Mysuru", "Belagavi", "Hubballi", "Mangaluru"], "5", "KA"),
          "Maharashtra": (["Pune", "Nagpur", "Nashik", "Kolhapur"], "4", "MH"),
          "Uttar Pradesh": (["Lucknow", "Kanpur", "Varanasi", "Agra"], "2", "UP"),
          "Gujarat": (["Ahmedabad", "Surat", "Vadodara", "Rajkot"], "3", "GJ"),
          "Rajasthan": (["Jaipur", "Jodhpur", "Kota", "Udaipur"], "3", "RJ"),
          "Telangana": (["Hyderabad", "Warangal", "Karimnagar"], "5", "TS"),
          "West Bengal": (["Kolkata", "Durgapur", "Siliguri"], "7", "WB")}
COURSES = {"engineering": [("B.E./B.Tech Computer Science & Engineering", "UG"), ("B.E./B.Tech Electronics & Communication Engineering", "UG"),
                           ("B.E./B.Tech Mechanical Engineering", "UG"), ("B.E./B.Tech Civil Engineering", "UG"),
                           ("B.E./B.Tech Information Technology", "UG"), ("B.E./B.Tech Electrical & Electronics Engineering", "UG")],
           "mba": [("Master of Business Administration (MBA)", "PG"), ("Post Graduate Diploma in Management (PGDM)", "PG")],
           "polytechnic": [("Diploma in Civil Engineering", "Diploma"), ("Diploma in Mechanical Engineering", "Diploma"),
                           ("Diploma in Electrical Engineering", "Diploma"), ("Diploma in Computer Engineering", "Diploma")]}
INTAKES = {"engineering": [60, 120, 180], "mba": [60, 120], "polytechnic": [60, 90]}
DESIG = ["Professor", "Associate Professor", "Assistant Professor", "Assistant Professor", "Assistant Professor", "Lecturer"]
QUAL = {"engineering": ["Ph.D.", "M.E./M.Tech", "M.E./M.Tech", "M.Tech"], "mba": ["Ph.D.", "MBA", "MBA", "M.Com + NET"], "polytechnic": ["M.E./M.Tech", "B.E./B.Tech", "B.E./B.Tech", "Ph.D."]}
CA_FIRMS = ["Raman & Associates", "Venkatesh Iyer & Co.", "Gupta Mehta & Partners", "Nair & Nair", "S. K. Das & Associates", "Patel Shah & Co.", "Kulkarni & Rao LLP", "Menon Pillai & Co."]
REQUIRED_DOCS = ["society_reg", "land_doc", "building_plan", "fire_cert", "aff_letter", "faculty_list", "infra_statement", "financial", "affidavit"]
NAME_KIND = {"society_reg": "soc", "land_doc": "soc"}  # all other docs carry the institution name
GEN_POOL = ["Crystal Reports 13.0", "Microsoft Word 2019", "iText 5.5.13", "JasperReports 6.20", "wkhtmltopdf 0.12.6", "Apache FOP 2.6"]
TAMPER_CREATORS = ["Adobe Photoshop 25.2 (Windows)", "ilovepdf.com", "Foxit PhantomPDF 12", "Sejda PDF Editor"]


# ----------------------------------------------------------------------------- helpers
def iso(d): return d.isoformat() if isinstance(d, date) else d
def D(d, style):
    if style == "dmy": return d.strftime("%d/%m/%Y")
    if style == "dMy": return d.strftime("%d-%b-%Y")
    return d.strftime("%d %B %Y")

def inr(n):
    s = str(int(n))
    if len(s) <= 3: return "Rs. " + s
    last3, rest, parts = s[-3:], s[:-3], []
    while len(rest) > 2: parts.insert(0, rest[-2:]); rest = rest[:-2]
    if rest: parts.insert(0, rest)
    return "Rs. " + ",".join(parts + [last3])

def norm_name(s):
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    m = {"coll": "college", "inst": "institute", "engg": "engineering"}
    return " ".join(m.get(t, t) for t in s.split())

def noisy(name, rng):
    v = rng.choice(["upper", "amp", "abbr", "case"])
    if v == "upper": return name.upper()
    if v == "amp": return name.replace(" & ", " and ") if " & " in name else name.replace(" and ", " & ")
    if v == "abbr": return name.replace("College", "Coll.").replace("Institute", "Inst.").replace("Engineering", "Engg.")
    return name.replace(" of ", " Of ")

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(65536), b""): h.update(b)
    return h.hexdigest()

def pdate(d, hh=10, mm=30): return f"D:{d.strftime('%Y%m%d')}{hh:02d}{mm:02d}00+05'30'"

def set_meta(path, created, modified, creator, producer, title):
    r = PdfReader(path); w = PdfWriter(clone_from=r)
    w.add_metadata({"/Title": title, "/Creator": creator, "/Producer": producer, "/Subject": "SYNTHETIC TEST DOCUMENT",
                    "/CreationDate": pdate(created), "/ModDate": pdate(modified, 16, 5)})
    with open(path, "wb") as f: w.write(f)


# ----------------------------------------------------------------------------- PDF writer with bbox capture
class DW:
    def __init__(s, path, doc_id, style, wm=True, splice=None):
        s.c = canvas.Canvas(path, pagesize=A4); s.id = doc_id; s.st = style; s.page = 1; s.fields = {}; s.wm = wm
        s.splice = splice or {}; s.y = H - 60; s._start()

    def _start(s):
        c = s.c
        if s.wm:
            c.saveState(); c.setFillColor(colors.Color(0.6, 0.6, 0.6, alpha=0.18)); c.setFont("Helvetica-Bold", 52)
            c.translate(W / 2, H / 2); c.rotate(38); c.drawCentredString(0, 0, "SYNTHETIC TEST DATA"); c.restoreState()
        if s.st.get("border"):
            c.setStrokeColorRGB(*s.st.get("band", (0.2, 0.2, 0.2))); c.setLineWidth(2); c.rect(22, 22, W - 44, H - 44); c.setLineWidth(1)

    def _footer(s):
        s.c.setFillColorRGB(0.45, 0.45, 0.45); s.c.setFont("Helvetica", 6.5)
        s.c.drawString(30, 12, f"SYNTHETIC TEST DOCUMENT - NOT A REAL RECORD | {s.id} | page {s.page}"); s.c.setFillColorRGB(0, 0, 0)

    def newpage(s):
        s._footer(); s.c.showPage(); s.page += 1; s._start(); s.y = H - 50

    def room(s, h):
        if s.y - h < 55: s.newpage(); return True
        return False

    def put(s, key, text, x, y, font=None, size=9.5):
        font = font or s.st["font"]; text = str(text); c = s.c
        w = stringWidth(text, font, size); extra = {}
        c.setFont(font, size); c.setFillColorRGB(0, 0, 0)
        if key in s.splice:   # cover-up forgery: original text stays underneath a white patch
            under = str(s.splice[key]); wu = stringWidth(under, font, size); c.drawString(x, y, under)
            c.setFillColorRGB(1, 1, 1); c.rect(x - 1, y - 3, max(w, wu) + 3, size + 3, stroke=0, fill=1); c.setFillColorRGB(0, 0, 0)
            c.setFont("Courier-Bold", size); c.drawString(x, y, text); w = max(w, wu)
            extra = {"spliced": True, "hidden_original": under}
        else:
            c.drawString(x, y, text)
        s.fields[key] = {"value": text, "bbox": [round(x, 1), round(H - (y + size * 0.8), 1), round(w, 1), round(size, 1)], "page": s.page, **extra}

    def header(s, authority, sub=None, title=None):
        c = s.c; band = s.st.get("band", (0.1, 0.2, 0.4))
        c.setFillColorRGB(*band); c.rect(0, H - 70, W, 70, stroke=0, fill=1); c.setFillColorRGB(1, 1, 1)
        c.setFont(s.st["bold"], 12.5); c.drawCentredString(W / 2, H - 33, authority)
        if sub: c.setFont(s.st["font"], 8.5); c.drawCentredString(W / 2, H - 51, sub)
        c.setFillColorRGB(0, 0, 0); s.y = H - 102
        if title:
            c.setFont(s.st["bold"], 14); c.drawCentredString(W / 2, s.y, title)
            tw = stringWidth(title, s.st["bold"], 14); c.line(W / 2 - tw / 2, s.y - 3, W / 2 + tw / 2, s.y - 3); s.y -= 30

    def kv(s, key, label, value):
        s.room(20); s.c.setFont(s.st["bold"], 9); s.c.drawString(50, s.y, label)
        s.put(key, value, 205, s.y, size=9.5); s.fields[key]["label"] = label; s.y -= 19

    def para(s, text, size=9.5, leading=13.5, x=50, width=None):
        width = width or (W - 100); words = text.split(); line = ""
        for wd in words:
            t = (line + " " + wd).strip()
            if stringWidth(t, s.st["font"], size) > width:
                s.room(leading); s.c.setFont(s.st["font"], size); s.c.drawString(x, s.y, line); s.y -= leading; line = wd
            else: line = t
        if line: s.room(leading); s.c.setFont(s.st["font"], size); s.c.drawString(x, s.y, line); s.y -= leading
        s.y -= 6

    def table(s, name, cols, rows, size=8, rh=15, x0=40):
        def head():
            s.c.setFillColorRGB(0.88, 0.9, 0.93); s.c.rect(x0, s.y - 4, sum(c[2] for c in cols), rh, stroke=1, fill=1); s.c.setFillColorRGB(0, 0, 0)
            x = x0
            for _, t, w in cols: s.c.setFont(s.st["bold"], size); s.c.drawString(x + 3, s.y, t); x += w
            s.y -= rh
        s.room(rh * 3); head()
        for i, row in enumerate(rows):
            if s.room(rh): head()
            x = x0; rk = row.get("_k", i)
            for k, _, w in cols:
                s.c.rect(x, s.y - 4, w, rh, stroke=1, fill=0)
                txt = str(row.get(k, ""))
                while stringWidth(txt, s.st["font"], size) > w - 5 and len(txt) > 3: txt = txt[:-2]
                s.put(f"{name}[{rk}].{k}" if isinstance(rk, int) else f"{name}.{rk}.{k}", txt, x + 3, s.y, size=size); x += w
            s.y -= rh
        s.y -= 8

    def qr(s, payload, x, y, size=78):
        wdg = QrCodeWidget(payload); b = wdg.getBounds(); bw = b[2] - b[0]
        d = Drawing(size, size, transform=[size / bw, 0, 0, size / bw, 0, 0]); d.add(wdg); renderPDF.draw(d, s.c, x, y)
        s.fields["qr"] = {"value": payload, "bbox": [x, round(H - (y + size), 1), size, size], "page": s.page}

    def seal_sign(s, x, y):
        s.room(70); y = min(y, s.y - 30); c = s.c
        c.setStrokeColorRGB(0.1, 0.2, 0.6); c.circle(x + 30, y, 28); c.setFont("Helvetica-Bold", 6); c.drawCentredString(x + 30, y + 3, "OFFICIAL SEAL"); c.drawCentredString(x + 30, y - 6, "(SYNTHETIC)")
        s.fields["seal"] = {"value": None, "bbox": [x + 2, round(H - (y + 28), 1), 56, 56], "page": s.page}
        p = c.beginPath(); p.moveTo(x + 100, y - 5); p.curveTo(x + 115, y + 25, x + 130, y - 25, x + 150, y + 10); p.curveTo(x + 160, y + 20, x + 170, y - 10, x + 190, y + 2)
        c.setStrokeColorRGB(0.1, 0.1, 0.5); c.drawPath(p, stroke=1, fill=0); c.setStrokeColorRGB(0, 0, 0)
        s.fields["signature"] = {"value": None, "bbox": [x + 100, round(H - (y + 25), 1), 90, 40], "page": s.page}
        c.setFont(s.st["font"], 8); c.drawString(x + 100, y - 30, "Authorised Signatory"); s.y = y - 45

    def finish(s): s._footer(); s.c.save(); return s.fields, s.page


# ----------------------------------------------------------------------------- world model
TABLES = {
    "institutions": [("inst_id", "TEXT"), ("name", "TEXT"), ("type", "TEXT"), ("city", "TEXT"), ("state", "TEXT"), ("pin", "TEXT"), ("society_id", "TEXT"), ("application_type", "TEXT"), ("synthetic", "INTEGER")],
    "society_registry": [("society_id", "TEXT"), ("reg_no", "TEXT"), ("name", "TEXT"), ("reg_date", "TEXT"), ("status", "TEXT"), ("president", "TEXT"), ("secretary", "TEXT"), ("state", "TEXT"), ("synthetic", "INTEGER")],
    "land_records": [("survey_no", "TEXT"), ("holder_inst_id", "TEXT"), ("holder_society_id", "TEXT"), ("owner_name", "TEXT"), ("area_acres", "REAL"), ("tenure", "TEXT"), ("lease_from", "TEXT"), ("lease_to", "TEXT"), ("city", "TEXT"), ("state", "TEXT"), ("status", "TEXT"), ("synthetic", "INTEGER")],
    "building_approvals": [("cert_no", "TEXT"), ("inst_id", "TEXT"), ("authority", "TEXT"), ("issued_on", "TEXT"), ("valid_to", "TEXT"), ("sanctioned_builtup_sqm", "INTEGER"), ("status", "TEXT")],
    "fire_certs": [("cert_no", "TEXT"), ("inst_id", "TEXT"), ("authority", "TEXT"), ("issued_on", "TEXT"), ("valid_to", "TEXT"), ("status", "TEXT")],
    "university_affiliations": [("aff_no", "TEXT"), ("inst_id", "TEXT"), ("university", "TEXT"), ("course", "TEXT"), ("approved_intake", "INTEGER"), ("valid_from", "TEXT"), ("valid_to", "TEXT"), ("status", "TEXT")],
    "faculty_registry": [("faculty_id", "TEXT"), ("name", "TEXT"), ("pan", "TEXT"), ("qualification", "TEXT"), ("designation", "TEXT"), ("synthetic", "INTEGER")],
    "faculty_employment": [("faculty_id", "TEXT"), ("inst_id", "TEXT"), ("from_date", "TEXT"), ("to_date", "TEXT")],
    "aicte_history": [("inst_id", "TEXT"), ("academic_year", "TEXT"), ("course", "TEXT"), ("level", "TEXT"), ("approved_intake", "INTEGER"), ("approval_ref", "TEXT"), ("status", "TEXT")],
    "ca_registry": [("firm_no", "TEXT"), ("firm_name", "TEXT"), ("status", "TEXT")],
    "financials": [("inst_id", "TEXT"), ("fy", "TEXT"), ("ca_firm_no", "TEXT"), ("total_income", "INTEGER"), ("total_expenditure", "INTEGER"), ("surplus", "INTEGER"), ("fd_amount", "INTEGER")],
    "issuer_keys": [("issuer", "TEXT"), ("key_id", "TEXT"), ("secret_hex", "TEXT")],
    "scenario_flags": [("source", "TEXT"), ("record_key", "TEXT"), ("behavior", "TEXT"), ("note", "TEXT")],
}

class Ctx:
    def __init__(s, rng, norms):
        s.rng = rng; s.norms = norms; s.T = {t: [] for t in TABLES}; s.soc = []; s.fac_n = 0; s.names = set(); s.n_soc = 0
        s.keys = {i: rng.getrandbits(128).to_bytes(16, "big").hex() for i in ("building", "fire", "aicte")}
        for i, k in s.keys.items(): s.T["issuer_keys"].append({"issuer": i, "key_id": f"SYN-{i.upper()}-K1", "secret_hex": k})
        for n, f in enumerate(CA_FIRMS): s.T["ca_registry"].append({"firm_no": f"SYN-CA-{300100 + n * 17}", "firm_name": f, "status": "active"})

def requirements(norms, typ, intake):
    n = norms["programme_types"][typ]
    return {"faculty": math.ceil(intake / n["students_per_faculty"]), "builtup": math.ceil(n["builtup_sqm_per_60_intake"] * intake / 60), "pcs": math.ceil(intake * n["pcs_per_intake_seat"])}

def qr_payload(X, issuer, cert_no, inst_id, valid_iso, bad_sig=False):
    sig = hmac.new(bytes.fromhex(X.keys[issuer]), f"{cert_no}|{inst_id}|{valid_iso}".encode(), hashlib.sha256).hexdigest()[:20]
    if bad_sig: sig = "".join(random.Random(cert_no).choice("0123456789abcdef") for _ in range(20))
    return f"SYNQR1|{issuer}|{cert_no}|{inst_id}|{valid_iso}|{sig}"

def pname(rng): return f"{rng.choice(FIRST)} {rng.choice(LAST)}"

def make_inst(idx, X):
    rng = X.rng; norms = X.norms
    typ = rng.choices(["engineering", "mba", "polytechnic"], [0.5, 0.25, 0.25])[0]
    state = rng.choice(list(STATES)); cities, pinp, ab = STATES[state]; city = rng.choice(cities)
    while True:
        patron = rng.choice(PATRON); name = f"{patron} {rng.choice(KIND_NAME[typ])}"
        if name not in X.names: X.names.add(name); break
    inst_id = f"SYN-I{idx:04d}"; app_date = date(APP_YEAR, 1, 10) + timedelta(days=rng.randint(0, 40))
    pin = pinp + "".join(rng.choice("0123456789") for _ in range(5))
    # society (some societies run two institutions)
    if X.soc and rng.random() < 0.2: soc = rng.choice(X.soc)
    else:
        X.n_soc += 1
        soc = {"society_id": f"SYN-S{X.n_soc:04d}", "reg_no": f"SYN-REG/{ab}/{rng.randint(1985, 2022)}/{rng.randint(1000, 99999):05d}",
               "name": f"{patron} Educational {rng.choice(['Society', 'Trust', 'Foundation'])}", "reg_date": date(rng.randint(1985, 2022), rng.randint(1, 12), rng.randint(1, 28)),
               "status": "active", "president": pname(rng), "secretary": pname(rng), "state": state, "synthetic": 1}
        X.soc.append(soc); X.T["society_registry"].append(soc)
    # courses
    pool = COURSES[typ][:]; rng.shuffle(pool)
    k = rng.randint(2, 4) if typ != "mba" else rng.randint(1, 2); k = min(k, len(pool))
    courses = [{"course": c, "level": l, "intake": rng.choice(INTAKES[typ])} for c, l in pool[:k]]
    total = sum(c["intake"] for c in courses); req = requirements(norms, typ, total)
    app_type = "existing" if rng.random() < 0.75 else "new"
    I = {"id": inst_id, "name": name, "typ": typ, "state": state, "city": city, "pin": pin, "patron": patron, "soc": soc, "app_date": app_date,
         "app_type": app_type, "courses": courses, "mut": [], "doc_labels": {}, "opts": {}, "missing": [], "reuse": {}, "files": {}, "ab": ab}
    X.T["institutions"].append({"inst_id": inst_id, "name": name, "type": typ, "city": city, "state": state, "pin": pin, "society_id": soc["society_id"], "application_type": app_type, "synthetic": 1})
    # land
    tenure = rng.choice(["owned", "lease"]); acres = round(rng.uniform(10, 30) if typ == "engineering" else rng.uniform(3, 12), 2)
    survey = f"SYN-SY/{ab}/{rng.randint(10, 999)}/{rng.randint(1, 20)}{rng.choice('ABC')}"
    lf = app_date - timedelta(days=rng.randint(365, 3650)); ly = rng.choice([30, 33, 45, 60, 99]) if tenure == "lease" else None
    owner = soc["name"] if tenure == "owned" else f"{rng.choice(LAST)} Family Trust"
    land_row = {"survey_no": survey, "holder_inst_id": inst_id, "holder_society_id": soc["society_id"], "owner_name": owner, "area_acres": acres, "tenure": tenure,
                "lease_from": iso(lf) if ly else None, "lease_to": iso(lf + timedelta(days=365 * ly)) if ly else None, "city": city, "state": state, "status": "registered", "synthetic": 1}
    X.T["land_records"].append(land_row)
    # building + fire
    b_valid = app_date + timedelta(days=rng.randint(400, 2500)); f_valid = app_date + timedelta(days=rng.randint(45, 720))
    b_instr = round(req["builtup"] * rng.uniform(1.05, 1.35)); instr = int(b_instr * 0.62); admin = int(b_instr * 0.13); amen = b_instr - instr - admin
    b_row = {"cert_no": f"SYN-BPA/{ab}/{rng.randint(2005, 2024)}/{rng.randint(1000, 99999)}", "inst_id": inst_id, "authority": f"{city} Municipal Corporation (Synthetic)", "issued_on": iso(b_valid - timedelta(days=365 * rng.randint(5, 10))),
             "valid_to": iso(b_valid), "sanctioned_builtup_sqm": b_instr + rng.randint(0, 800), "status": "valid"}
    f_row = {"cert_no": f"SYN-FSC/{ab}/{rng.randint(2023, 2026)}/{rng.randint(10000, 999999)}", "inst_id": inst_id, "authority": f"{state} Fire & Emergency Services (Synthetic)",
             "issued_on": iso(f_valid - timedelta(days=365 * rng.choice([1, 2, 3]))), "valid_to": iso(f_valid), "status": "valid"}
    X.T["building_approvals"].append(b_row); X.T["fire_certs"].append(f_row)
    # affiliation
    uni = f"{state} State Technical University" if typ != "polytechnic" else f"Board of Technical Education, {state}"; aff_no = f"SYN-AFF/{ab}/{rng.randint(2010, 2025)}/{rng.randint(100, 9999)}"
    af_from = app_date - timedelta(days=rng.randint(30, 900)); af_to = app_date + timedelta(days=rng.randint(200, 1000)); aff_rows = []
    for c in courses:
        r = {"aff_no": aff_no, "inst_id": inst_id, "university": uni, "course": c["course"], "approved_intake": c["intake"], "valid_from": iso(af_from), "valid_to": iso(af_to), "status": "valid"}
        X.T["university_affiliations"].append(r); aff_rows.append(r)
    # faculty
    nfac = req["faculty"] + rng.randint(0, 4); faculty = []
    for _ in range(nfac):
        X.fac_n += 1; fid = f"SYN-F{X.fac_n:06d}"
        f = {"faculty_id": fid, "name": pname(rng), "pan": f"SYN{''.join(rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ') for _ in range(2))}{rng.randint(1000, 9999)}{rng.choice('ABCDEFGH')}",
             "qualification": rng.choice(QUAL[typ]), "designation": rng.choice(DESIG), "synthetic": 1}
        doj = app_date - timedelta(days=rng.randint(200, 6000)); f["doj"] = doj
        X.T["faculty_registry"].append({k: f[k] for k in ("faculty_id", "name", "pan", "qualification", "designation", "synthetic")})
        X.T["faculty_employment"].append({"faculty_id": fid, "inst_id": inst_id, "from_date": iso(doj), "to_date": None}); faculty.append(f)
    # financials
    income = int(total * rng.uniform(60000, 140000)); exp = int(income * rng.uniform(0.7, 0.95)); firm = rng.choice(X.T["ca_registry"]); fd = int(income * rng.uniform(0.1, 0.4) / 1000) * 1000
    fin_row = {"inst_id": inst_id, "fy": FY, "ca_firm_no": firm["firm_no"], "total_income": income, "total_expenditure": exp, "surplus": income - exp, "fd_amount": fd}; X.T["financials"].append(fin_row)
    # prior approvals
    hist = []
    if app_type == "existing":
        ref = f"SYN-1-{rng.randint(10**9, 10**10 - 1)}"
        for c in courses:
            r = {"inst_id": inst_id, "academic_year": PRIOR_AY, "course": c["course"], "level": c["level"], "approved_intake": c["intake"], "approval_ref": ref, "status": "approved"}
            X.T["aicte_history"].append(r); hist.append(r)
    I["w"] = {"land": land_row, "bld": b_row, "fire": f_row, "aff": aff_rows, "fin": fin_row, "hist": hist, "firm": firm}
    # printed (what documents show) - starts equal to truth, with benign formatting noise
    nm = lambda s: noisy(s, rng) if rng.random() < 0.2 else s
    I["pr"] = {
        "names": {dt: (nm(soc["name"]) if NAME_KIND.get(dt) == "soc" else nm(name)) for dt in REQUIRED_DOCS + ["eoa"]},
        "land": {"doc_no": f"SYN-DEED/{ab}/{rng.randint(100000, 999999)}", "survey_no": survey, "area_acres": acres, "tenure": tenure, "lease_from": lf, "lease_years": ly, "owner": owner, "reg_date": lf},
        "building": {"cert_no": b_row["cert_no"], "issued_on": date.fromisoformat(b_row["issued_on"]), "valid_to": b_valid, "sanctioned_sqm": b_row["sanctioned_builtup_sqm"]},
        "fire": {"cert_no": f_row["cert_no"], "issued_on": date.fromisoformat(f_row["issued_on"]), "valid_to": f_valid},
        "aff": {"aff_no": aff_no, "university": uni, "valid_from": af_from, "valid_to": af_to, "courses": [{"course": c["course"], "approved_intake": c["intake"]} for c in courses]},
        "faculty": [dict(f) for f in faculty], "infra": {"instr": instr, "admin": admin, "amen": amen, "pcs": math.ceil(req["pcs"] * rng.uniform(1.05, 1.5))},
        "fin": {"firm_no": firm["firm_no"], "firm": firm["firm_name"], "income": income, "exp": exp, "surplus": income - exp, "fd": fd, "udin": f"SYN-UDIN-{rng.randint(10**7, 10**8 - 1)}"},
        "applied": [dict(c) for c in courses], "app_id": f"SYN-APP-{rng.randint(10**6, 10**7 - 1)}",
        "eoa": {"ref": f"SYN/{ab}/1-{rng.randint(10**9, 10**10 - 1)}/{APP_YEAR - 1}/EOA", "date": date(APP_YEAR - 1, 4, rng.randint(1, 28)), "courses": [{"course": c["course"], "level": c["level"], "intake": c["intake"]} for c in courses]} if app_type == "existing" else None,
        "fire_qr_valid": None, "bld_qr_valid": None}
    I["docs"] = REQUIRED_DOCS + (["eoa"] if app_type == "existing" else [])
    return I


# ----------------------------------------------------------------------------- mutations
def add_label(I, dt, **kw): I["doc_labels"].setdefault(dt, []).append(kw)

def m_apply(I, name, X, insts):
    """Apply one mutation; returns False if not applicable."""
    rng = X.rng; pr = I["pr"]; w = I["w"]; ad = I["app_date"]; norms = X.norms
    total = lambda: sum(c["intake"] for c in pr["applied"])
    req = requirements(norms, I["typ"], total())
    if name == "expired_fire_cert":
        d = ad - timedelta(days=rng.randint(20, 400)); pr["fire"]["valid_to"] = d; w["fire"]["valid_to"] = iso(d); w["fire"]["status"] = "expired"
        add_label(I, "fire_cert", kind="policy", name=name, rule="R004")
    elif name == "faculty_shortfall":
        keep = max(3, req["faculty"] - rng.randint(2, 6)); pr["faculty"] = pr["faculty"][:keep]; add_label(I, "faculty_list", kind="policy", name=name, rule="R001")
    elif name == "builtup_shortfall":
        f = rng.uniform(0.55, 0.9); tot = int(req["builtup"] * f); pr["infra"]["instr"] = int(tot * .62); pr["infra"]["admin"] = int(tot * .13); pr["infra"]["amen"] = tot - pr["infra"]["instr"] - pr["infra"]["admin"]
        add_label(I, "infra_statement", kind="policy", name=name, rule="R002")
    elif name == "pc_shortfall":
        pr["infra"]["pcs"] = int(req["pcs"] * rng.uniform(0.4, 0.9)); add_label(I, "infra_statement", kind="policy", name=name, rule="R003")
    elif name == "name_mismatch":
        dt = rng.choice([d for d in I["docs"] if d != "land_doc" or True]); kind = NAME_KIND.get(dt, "inst"); base = I["soc"]["name"] if kind == "soc" else I["name"]
        other = rng.choice([p for p in PATRON if p != I["patron"]]); pr["names"][dt] = base.replace(I["patron"], other); add_label(I, dt, kind="policy", name=name, rule="R008")
    elif name == "intake_exceeds_affiliation":
        c = rng.choice(pr["applied"]); c["intake"] += 60; add_label(I, "affidavit", kind="policy", name=name, rule="R007")
    elif name == "land_area_mismatch":
        pr["land"]["area_acres"] = round(pr["land"]["area_acres"] * rng.uniform(1.15, 1.6), 2); add_label(I, "land_doc", kind="policy", name=name, rule="R009")
    elif name == "short_lease":
        if pr["land"]["tenure"] != "lease": return False
        pr["land"]["lease_years"] = rng.choice([10, 15, 20, 25]); add_label(I, "land_doc", kind="policy", name=name, rule="R006")
    elif name == "financial_arithmetic_error":
        pr["fin"]["surplus"] += rng.choice([-1, 1]) * rng.randint(10, 90) * 10000; add_label(I, "financial", kind="policy", name=name, rule="R014")
    elif name == "unregistered_certificate":
        pr["fire"]["cert_no"] = f"SYN-FSC/{I['ab']}/{APP_YEAR}/{rng.randint(10**6, 10**7 - 1)}"; I["opts"].setdefault("fire_cert", {})["qr_bad_sig"] = True
        add_label(I, "fire_cert", kind="policy", name=name, rule="R013"); add_label(I, "fire_cert", kind="forensic", name="qr_bad_signature", tier=1)
    elif name == "missing_document":
        used = {l for l in I["doc_labels"]}; cands = [d for d in REQUIRED_DOCS if d not in used]
        if not cands: return False
        dt = rng.choice(cands); I["missing"].append(dt); I["docs"].remove(dt); add_label(I, dt, kind="policy", name=name, rule="R010")
    elif name == "editor_metadata":
        dt = rng.choice(I["docs"]); I["opts"].setdefault(dt, {})["meta_edit"] = True; add_label(I, dt, kind="forensic", name=name, tier=4)
    elif name == "spliced_expiry_forgery":
        past = ad - timedelta(days=rng.randint(30, 300)); fut = ad + timedelta(days=rng.randint(200, 700))
        w["fire"]["valid_to"] = iso(past); w["fire"]["status"] = "expired"; pr["fire"]["valid_to"] = fut
        I["opts"].setdefault("fire_cert", {}).update({"splice": {"valid_to": D(past, "dMy")}, "qr_valid": iso(past), "meta_edit": True})
        add_label(I, "fire_cert", kind="forensic", name=name, tier=1, rule="R016", note="printed validity extended via cover-up; QR + registry keep original")
    elif name == "incremental_edit_forgery":
        past = ad - timedelta(days=rng.randint(30, 300)); fut = ad + timedelta(days=rng.randint(200, 700))
        w["fire"]["valid_to"] = iso(past); w["fire"]["status"] = "expired"; pr["fire"]["valid_to"] = fut
        I["opts"].setdefault("fire_cert", {}).update({"incr_orig": past, "incr_edit": {"key": "valid_to", "new": D(fut, "dMy")}, "qr_valid": iso(past)})
        add_label(I, "fire_cert", kind="forensic", name=name, tier=1, rule="R016", note="validity extended by appended incremental update; QR + registry keep original")
    elif name == "qr_date_mismatch":
        d = pr["building"]["valid_to"]; pr["building"]["valid_to"] = d + timedelta(days=rng.randint(700, 1500)); I["opts"].setdefault("building_plan", {})["qr_valid"] = iso(d)
        w["bld"]["valid_to"] = iso(d); add_label(I, "building_plan", kind="forensic", name=name, tier=1, rule="R016")
    elif name == "qr_bad_signature":
        I["opts"].setdefault("building_plan", {})["qr_bad_sig"] = True; add_label(I, "building_plan", kind="forensic", name=name, tier=1)
    elif name in ("scan_degraded", "copy_move_scan", "scan_clean"):
        dt = rng.choice([d for d in ("society_reg", "building_plan", "fire_cert", "land_doc", "affidavit", "eoa") if d in I["docs"]])
        if "scan" in I["opts"].get(dt, {}): return False
        sev = {"scan_clean": ("ok", 0.0, rng.uniform(-0.8, 0.8)), "copy_move_scan": ("ok", 0.4, rng.uniform(-1, 1)),
               "scan_degraded": rng.choice([("blur", 2.4, 0.5), ("skewed", 0.5, rng.choice([-1, 1]) * rng.uniform(6.5, 11)), ("blur+skew", 2.0, 7.0)])}[name]
        I["opts"].setdefault(dt, {})["scan"] = {"sev": sev[0], "blur": sev[1], "skew": sev[2], "copy_move": name == "copy_move_scan", "seed": rng.randint(0, 10**6)}
        add_label(I, dt, kind="forensic" if name != "scan_clean" else "control", name=name, tier=4)
    else: raise ValueError(name)
    return True

def m_cross(I, name, X, insts):
    """Cross-institution mutations (need donors); run after all intra-institution mutations."""
    rng = X.rng; pr = I["pr"]
    donors = [d for d in insts if d is not I and d["state"] != "" and not d["mut_cross"]]
    if name == "double_employed_faculty":
        d = rng.choice([x for x in donors if len(x["pr"]["faculty"]) > 5 and x is not I]); borrowed = [dict(f) for f in rng.sample(d["w"]["_fac"], 3)]
        pr["faculty"] += borrowed; add_label(I, "faculty_list", kind="policy", name=name, rule="R011", donor=d["id"]); return True
    if name == "shared_land_parcel":
        d = rng.choice(donors); pr["land"]["survey_no"] = d["pr"]["land"]["survey_no"]; add_label(I, "land_doc", kind="policy", name=name, rule="R012", donor=d["id"]); return True
    if name == "certificate_reuse":
        c = [x for x in donors if "fire_cert" in x["docs"] and "fire_cert" not in x["opts"]]
        if not c: return False
        d = rng.choice(c); I["reuse"]["fire_cert"] = {"donor": d["id"], "mode": "exact"}
        pr["fire"] = copy.deepcopy(d["pr"]["fire"]); pr["names"]["fire_cert"] = d["pr"]["names"]["fire_cert"]
        add_label(I, "fire_cert", kind="policy", name=name, rule="R015", donor=d["id"]); add_label(I, "fire_cert", kind="forensic", name="exact_duplicate", tier=3, donor=d["id"]); return True
    if name == "near_duplicate_land":
        c = [x for x in donors if "land_doc" in x["docs"] and "scan" not in x["opts"].get("land_doc", {})]
        if not c: return False
        d = rng.choice(c); I["reuse"]["land_doc"] = {"donor": d["id"], "mode": "near"}
        pr["land"] = copy.deepcopy(d["pr"]["land"]); pr["names"]["land_doc"] = d["pr"]["names"]["land_doc"]
        add_label(I, "land_doc", kind="policy", name=name, rule="R012"); add_label(I, "land_doc", kind="forensic", name="visual_near_duplicate", tier=3, donor=d["id"]); return True
    return False

INTRA = ["expired_fire_cert", "faculty_shortfall", "builtup_shortfall", "pc_shortfall", "name_mismatch", "intake_exceeds_affiliation", "land_area_mismatch", "short_lease",
         "financial_arithmetic_error", "unregistered_certificate", "missing_document", "editor_metadata", "spliced_expiry_forgery", "incremental_edit_forgery", "qr_date_mismatch", "qr_bad_signature", "scan_degraded", "copy_move_scan"]
CROSS = ["double_employed_faculty", "shared_land_parcel", "certificate_reuse", "near_duplicate_land"]
GROUPS = {"fire": {"expired_fire_cert", "unregistered_certificate", "spliced_expiry_forgery", "incremental_edit_forgery", "certificate_reuse"}, "land": {"land_area_mismatch", "short_lease", "shared_land_parcel", "near_duplicate_land"},
          "fac": {"faculty_shortfall", "double_employed_faculty"}, "bld": {"qr_date_mismatch", "qr_bad_signature"}}
def group_of(n): return next((g for g, s in GROUPS.items() if n in s), n)


# ----------------------------------------------------------------------------- renderers
STY = {"a": {"font": "Helvetica", "bold": "Helvetica-Bold", "band": (0.1, 0.2, 0.4)}, "b": {"font": "Times-Roman", "bold": "Times-Bold", "band": (0.35, 0.1, 0.1), "border": True},
       "c": {"font": "Helvetica", "bold": "Helvetica-Bold", "band": (0.1, 0.35, 0.25)}, "d": {"font": "Times-Roman", "bold": "Times-Bold", "band": (0.3, 0.3, 0.3)}}

def R_society(dw, I, X):
    pr = I["pr"]; s = I["soc"]; dw.header(f"Registrar of Societies, {I['state']} (Synthetic)", "Registration Wing", "CERTIFICATE OF REGISTRATION")
    dw.para("This is to certify that the Society mentioned below stands registered under the Societies Registration Act (synthetic test document).")
    dw.kv("reg_no", "Registration No.", s["reg_no"]); dw.kv("society_name", "Name of the Society", pr["names"]["society_reg"]); dw.kv("reg_date", "Date of Registration", D(s["reg_date"], "dmy"))
    dw.kv("registered_office", "Registered Office", f"{I['city']}, {I['state']} - {I['pin']}"); dw.kv("president", "President", s["president"]); dw.kv("secretary", "Secretary", s["secretary"]); dw.kv("status", "Status", "Active")
    dw.seal_sign(60, dw.y - 40)

def R_land(dw, I, X):
    L = I["pr"]["land"]; lease = L["tenure"] == "lease"
    dw.header(f"Sub-Registrar Office, {I['city']} (Synthetic)", f"Government of {I['state']} - Stamps & Registration", "REGISTERED LEASE DEED" if lease else "REGISTERED SALE DEED")
    dw.kv("doc_no", "Document No.", L["doc_no"]); dw.kv("reg_date", "Date of Registration", D(L["reg_date"], "long")); dw.kv("survey_no", "Survey / Khasra No.", L["survey_no"])
    dw.kv("location", "Location", f"{I['city']}, {I['state']}"); dw.kv("area_acres", "Area of Land", f"{L['area_acres']:.2f} Acres")
    dw.kv("lessor" if lease else "seller", "Lessor" if lease else "Seller", L["owner"] if lease else f"{pname(random.Random(L['doc_no']))}")
    dw.kv("society_name", "Lessee" if lease else "Purchaser", I["pr"]["names"]["land_doc"]); dw.kv("tenure", "Nature of Title", "Lease" if lease else "Absolute Ownership")
    if lease: dw.kv("lease_from", "Lease Commencement", D(L["lease_from"], "long")); dw.kv("lease_years", "Lease Term (years)", L["lease_years"])
    dw.para("The land described above is conveyed for the exclusive purpose of establishing and running a technical educational institution (synthetic test document).")
    dw.seal_sign(60, dw.y - 40)

def R_building(dw, I, X):
    B = I["pr"]["building"]; o = I["opts"].get("building_plan", {})
    dw.header(f"{I['city']} Municipal Corporation (Synthetic)", "Town Planning Department", "BUILDING PLAN APPROVAL")
    dw.kv("cert_no", "Approval No.", B["cert_no"]); dw.kv("institution_name", "Applicant / Institution", I["pr"]["names"]["building_plan"]); dw.kv("survey_no", "Survey No.", I["pr"]["land"]["survey_no"])
    dw.kv("sanctioned_builtup_sqm", "Sanctioned Built-up Area (sq m)", B["sanctioned_sqm"]); dw.kv("issued_on", "Date of Approval", D(B["issued_on"], "long")); dw.kv("valid_to", "Valid Up To", D(B["valid_to"], "long"))
    dw.kv("usage", "Intended Use", "Educational (Technical Institution)")
    dw.qr(qr_payload(X, "building", B["cert_no"], I["id"], o.get("qr_valid", iso(B["valid_to"])), o.get("qr_bad_sig", False)), 60, dw.y - 85); dw.seal_sign(300, dw.y - 55)

def R_fire(dw, I, X):
    F = I["pr"]["fire"]; o = I["opts"].get("fire_cert", {})
    dw.header(f"{I['state']} Fire & Emergency Services (Synthetic)", "Fire Safety Wing", "FIRE SAFETY CERTIFICATE")
    dw.kv("cert_no", "Certificate No.", F["cert_no"]); dw.kv("institution_name", "Name of Premises", I["pr"]["names"]["fire_cert"]); dw.kv("address", "Address", f"{I['city']}, {I['state']} - {I['pin']}")
    dw.kv("occupancy", "Occupancy Class", "Educational"); dw.kv("issued_on", "Date of Issue", D(F["issued_on"], "dMy")); dw.kv("valid_to", "Valid Until", D(o.get("incr_orig", F["valid_to"]), "dMy"))
    dw.para("The premises have been inspected and found to comply with the applicable fire safety provisions (synthetic test document).")
    dw.qr(qr_payload(X, "fire", F["cert_no"], I["id"], o.get("qr_valid", iso(F["valid_to"])), o.get("qr_bad_sig", False)), 60, dw.y - 85); dw.seal_sign(300, dw.y - 55)

def R_aff(dw, I, X):
    A = I["pr"]["aff"]
    dw.header(A["university"] + " (Synthetic)", "Office of the Registrar - Affiliation Section", "LETTER OF AFFILIATION")
    dw.kv("aff_no", "Reference No.", A["aff_no"]); dw.kv("institution_name", "Institution", I["pr"]["names"]["aff_letter"]); dw.kv("valid_from", "Affiliation Valid From", D(A["valid_from"], "dmy")); dw.kv("valid_to", "Affiliation Valid To", D(A["valid_to"], "dmy"))
    dw.para(f"The above institution is granted affiliation for the programmes and intake mentioned below for the academic year {AY}, subject to compliance with university statutes (synthetic test document).")
    dw.table("courses", [("course", "Programme", 380), ("approved_intake", "Approved Intake", 110)], [{"course": c["course"], "approved_intake": c["approved_intake"]} for c in A["courses"]], size=8.5, rh=17, x0=50)
    dw.seal_sign(60, dw.y - 30)

def R_faculty(dw, I, X):
    dw.header(f"Faculty Details - Academic Year {AY}", "Annexure to online application (synthetic)", None); dw.y = H - 95
    dw.kv("institution_name", "Institution", I["pr"]["names"]["faculty_list"]); dw.kv("total_faculty", "Total Faculty Declared", len(I["pr"]["faculty"]))
    cols = [("sl", "Sl", 20), ("faculty_id", "Faculty ID", 76), ("name", "Name", 112), ("designation", "Designation", 82), ("qualification", "Qualification", 88), ("pan", "PAN", 66), ("doj", "Date of Joining", 58)]
    dw.table("faculty", cols, [{"sl": i + 1, "faculty_id": f["faculty_id"], "name": f["name"], "designation": f["designation"], "qualification": f["qualification"], "pan": f["pan"], "doj": D(f["doj"], "dmy")} for i, f in enumerate(I["pr"]["faculty"])], size=7.5, rh=15)

def R_infra(dw, I, X):
    f = I["pr"]["infra"]; tot = f["instr"] + f["admin"] + f["amen"]
    dw.header(f"Declaration of Land & Built-up Area - AY {AY}", "AICTE-format affidavit annexure (synthetic)", None); dw.y = H - 95
    dw.kv("institution_name", "Institution", I["pr"]["names"]["infra_statement"]); dw.kv("land_area_acres", "Total Land Area (Acres)", f"{I['pr']['land']['area_acres']:.2f}")
    rows = [{"_k": "instructional", "p": "Instructional Area", "a": f["instr"], "s": "Ready"}, {"_k": "administrative", "p": "Administrative Area", "a": f["admin"], "s": "Ready"},
            {"_k": "amenities", "p": "Amenities Area", "a": f["amen"], "s": "Ready"}, {"_k": "total", "p": "Total Built-up Area", "a": tot, "s": ""}]
    dw.table("area", [("p", "Particulars", 250), ("a", "Carpet/Built-up Area (sq m)", 150), ("s", "Completion", 90)], rows, size=8.5, rh=17, x0=50)
    dw.kv("pcs_available", "Number of PCs Available", f["pcs"]); dw.para("I/We declare that the above details are true and complete (synthetic test document).")
    dw.seal_sign(60, dw.y - 30)

def R_fin(dw, I, X):
    F = I["pr"]["fin"]
    dw.header(f"M/s {F['firm']}, Chartered Accountants (Synthetic)", f"Firm Registration No. {F['firm_no']}", f"AUDITED STATEMENT OF INCOME & EXPENDITURE FY {FY}")
    dw.kv("firm_no", "CA Firm Reg. No.", F["firm_no"]); dw.kv("udin", "UDIN", F["udin"]); dw.kv("institution_name", "Entity Audited", I["pr"]["names"]["financial"]); dw.kv("fy", "Financial Year", FY)
    rows = [{"_k": "income", "p": "Total Income", "a": inr(F["income"])}, {"_k": "expenditure", "p": "Total Expenditure", "a": inr(F["exp"])}, {"_k": "surplus", "p": "Surplus / (Deficit)", "a": inr(F["surplus"])}, {"_k": "fd", "p": "Fixed Deposits held", "a": inr(F["fd"])}]
    dw.table("fin", [("p", "Particulars", 300), ("a", "Amount", 190)], rows, size=9, rh=18, x0=50); dw.seal_sign(60, dw.y - 30)

def R_aff_davit(dw, I, X):
    pr = I["pr"]; dw.header(f"Application Summary & Affidavit - AY {AY}", "Institution declaration (synthetic)", None); dw.y = H - 95
    dw.kv("app_id", "Application ID", pr["app_id"]); dw.kv("institution_name", "Institution", pr["names"]["affidavit"]); dw.kv("society_name", "Trust / Society", I["soc"]["name"])
    dw.kv("application_date", "Date of Application", D(I["app_date"], "dmy")); dw.kv("institution_type", "Institution Type", I["typ"].title())
    dw.table("applied", [("course", "Programme Applied", 300), ("level", "Level", 70), ("intake", "Intake", 70)], pr["applied"], size=8.5, rh=17, x0=50)
    dw.kv("total_intake", "Total Intake Applied", sum(c["intake"] for c in pr["applied"]))
    dw.para("I/We will abide by all norms laid down in the Approval Process Handbook; the Faculty norms and Faculty:Student ratio are maintained and the data uploaded is true and complete (synthetic test document).")
    dw.seal_sign(60, dw.y - 30)

def R_eoa(dw, I, X):
    E = I["pr"]["eoa"]; o = I["opts"].get("eoa", {})
    dw.header("All India Council for Technical Education (SYNTHETIC SAMPLE)", f"Extension of Approval for the Academic Year {PRIOR_AY}", "EXTENSION OF APPROVAL (EoA)")
    dw.kv("ref_no", "Reference No.", E["ref"]); dw.kv("approval_date", "Date of Approval", D(E["date"], "dMy")); dw.kv("institution_name", "Institution", I["pr"]["names"]["eoa"])
    dw.table("courses", [("course", "Programme", 330), ("level", "Level", 60), ("intake", "Approved Intake", 100)], E["courses"], size=8.5, rh=17, x0=50)
    dw.para("Computer generated document. No signature is required. (synthetic test document)")
    dw.qr(qr_payload(X, "aicte", E["ref"], I["id"], iso(E["date"])), 60, dw.y - 85)

RENDER = {"society_reg": (R_society, "a"), "land_doc": (R_land, "b"), "building_plan": (R_building, "c"), "fire_cert": (R_fire, "c"), "aff_letter": (R_aff, "d"),
          "faculty_list": (R_faculty, "a"), "infra_statement": (R_infra, "a"), "financial": (R_fin, "d"), "affidavit": (R_aff_davit, "a"), "eoa": (R_eoa, "a")}
DOC_NAMES = {"society_reg": "01_society_registration", "land_doc": "02_land_document", "building_plan": "03_building_plan_approval", "fire_cert": "04_fire_safety_certificate", "aff_letter": "05_affiliation_letter",
             "faculty_list": "06_faculty_list", "infra_statement": "07_infrastructure_statement", "financial": "08_audited_financials", "affidavit": "09_application_affidavit", "eoa": "10_prior_eoa_letter"}


def apply_incremental_edit(path, fields, edit):
    """Append an incremental update that covers a value with a white patch and prints a new value (classic 'sticker' edit).
    The original revision bytes stay at the start of the file; the original text remains in the text layer."""
    import io
    f = fields[edit["key"]]; x, y, w, h = f["bbox"]; pg = f["page"] - 1; orig = f["value"]; size = h; base = H - y - size * 0.8
    buf = io.BytesIO(); c = canvas.Canvas(buf, pagesize=A4); c.setFillColorRGB(1, 1, 1); c.rect(x - 1, base - 3, max(w, 70) + 4, size + 3, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0); c.setFont("Courier-Bold", size); c.drawString(x, base, edit["new"]); c.save(); buf.seek(0)
    wr = PdfWriter(fileobj=path, incremental=True); wr.pages[pg].merge_page(PdfReader(buf).pages[0]); wr.write(path)
    f.update({"value": edit["new"], "spliced": True, "hidden_original": orig, "via_incremental_update": True})

# ----------------------------------------------------------------------------- scan / image forensics
def rasterize(pdf, dpi=SCAN_DPI):
    base = pdf[:-4] + "_r"; subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1", "-singlefile", pdf, base], check=True)
    img = cv2.imread(base + ".png"); os.remove(base + ".png"); return img

def vol(img): return float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
def vol_norm(img, width=1000):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); g = cv2.resize(g, (width, int(g.shape[0] * width / g.shape[1])), interpolation=cv2.INTER_AREA if g.shape[1] > width else cv2.INTER_LINEAR)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())

def save_img_pdf(img, path, quality=82):
    tmp = path[:-4] + "_t.jpg"; cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY, quality]); Image.open(tmp).convert("RGB").save(path, "PDF", resolution=SCAN_DPI); os.remove(tmp)

def degrade(img, sc, fields):
    r = np.random.RandomState(sc["seed"]); h, w = img.shape[:2]; M = cv2.getRotationMatrix2D((w / 2, h / 2), sc["skew"], 1.0)
    out = cv2.warpAffine(img, M, (w, h), borderValue=(255, 255, 255))
    if sc["blur"] > 0: out = cv2.GaussianBlur(out, (0, 0), sc["blur"])
    out = np.clip(out.astype(np.float32) + r.normal(0, 4, out.shape) + r.uniform(-6, 6), 0, 255).astype(np.uint8)
    s = SCAN_DPI / 72.0; moved = {}
    for k, f in fields.items():
        if f.get("page") != 1: continue
        x, y, bw, bh = f["bbox"]; pts = np.array([[x * s, y * s, 1], [(x + bw) * s, y * s, 1], [x * s, (y + bh) * s, 1], [(x + bw) * s, (y + bh) * s, 1]]) @ M.T
        moved[k] = [round(float(pts[:, 0].min()), 1), round(float(pts[:, 1].min()), 1), round(float(np.ptp(pts[:, 0])), 1), round(float(np.ptp(pts[:, 1])), 1)]
    return out, moved

def copy_move(img, seed):
    r = np.random.RandomState(seed); h, w = img.shape[:2]; ph, pw = 26, 150; sy = int(h * 0.30) + r.randint(0, int(h * 0.10)); sx = int(w * 0.30)
    dy = int(h * 0.62) + r.randint(0, 30); dx = int(w * 0.30) + r.randint(-20, 20); out = img.copy(); out[dy:dy + ph, dx:dx + pw] = img[sy:sy + ph, sx:sx + pw]
    mask = np.zeros((h, w), np.uint8); mask[dy:dy + ph, dx:dx + pw] = 255; return out, mask, {"src": [sx, sy, pw, ph], "dst": [dx, dy, pw, ph]}


# ----------------------------------------------------------------------------- oracle (expected rule outcomes) & adapter statuses
def evaluate(I, X):
    pr, ad, N = I["pr"], I["app_date"], X.norms; present = set(I["docs"]); res = {}
    T = X.T; total = sum(c["intake"] for c in pr["applied"]); req = requirements(N, I["typ"], total)
    def need(*d): return all(x in present for x in d)
    def R(rid, r, detail=""): res[rid] = {"result": r, "detail": detail}
    NE = "not_evaluable"
    R("R001", ("pass" if len(pr["faculty"]) >= req["faculty"] else "fail") if need("faculty_list", "affidavit") else NE, f"faculty {len(pr['faculty'])} vs required {req['faculty']}")
    ib = pr["infra"]; tb = ib["instr"] + ib["admin"] + ib["amen"]
    R("R002", ("pass" if tb >= req["builtup"] else "fail") if need("infra_statement", "affidavit") else NE, f"built-up {tb} vs required {req['builtup']}")
    R("R003", ("pass" if ib["pcs"] >= req["pcs"] else "fail") if need("infra_statement", "affidavit") else NE, f"pcs {ib['pcs']} vs required {req['pcs']}")
    R("R004", ("pass" if pr["fire"]["valid_to"] >= ad else "fail") if need("fire_cert") else NE, f"fire valid_to {pr['fire']['valid_to']} vs application {ad}")
    R("R005", ("pass" if pr["building"]["valid_to"] >= ad else "fail") if need("building_plan") else NE, "")
    L = pr["land"]
    if not need("land_doc"): R("R006", NE)
    elif L["tenure"] != "lease": R("R006", "not_applicable", "owned land")
    else: R("R006", "pass" if L["lease_years"] >= N["programme_types"][I["typ"]]["min_lease_years"] else "fail", f"lease {L['lease_years']}y")
    aff = {r["course"]: r["approved_intake"] for r in T["university_affiliations"] if r["inst_id"] == I["id"]}
    bad = [c["course"] for c in pr["applied"] if c["intake"] > aff.get(c["course"], 0)]
    R("R007", ("pass" if not bad else "fail") if need("affidavit") else NE, ",".join(bad))
    soc_n, inst_n = norm_name(I["soc"]["name"]), norm_name(I["name"]); badn = [d for d in present if norm_name(pr["names"][d]) != (soc_n if NAME_KIND.get(d) == "soc" else inst_n)]
    R("R008", "pass" if not badn else "fail", ",".join(badn))
    lr = next((r for r in T["land_records"] if r["survey_no"] == L["survey_no"]), None)
    R("R009", (("pass" if lr and abs(lr["area_acres"] - L["area_acres"]) < 0.005 else "fail")) if need("land_doc") else NE, "")
    miss = [d for d in REQUIRED_DOCS if d not in present]; R("R010", "pass" if not miss else "fail", ",".join(miss))
    emp = {r["faculty_id"]: r for r in T["faculty_employment"] if r["to_date"] is None}; badf = [f["faculty_id"] for f in pr["faculty"] if emp.get(f["faculty_id"], {}).get("inst_id") != I["id"]]
    R("R011", ("pass" if not badf else "fail") if need("faculty_list") else NE, ",".join(badf))
    R("R012", (("pass" if lr and lr["holder_inst_id"] == I["id"] else "fail")) if need("land_doc") else NE, "")
    fr = next((r for r in T["fire_certs"] if r["cert_no"] == pr["fire"]["cert_no"]), None); br = next((r for r in T["building_approvals"] if r["cert_no"] == pr["building"]["cert_no"]), None)
    R("R013", ("pass" if fr and br else "fail") if need("fire_cert", "building_plan") else NE, "")
    R("R014", ("pass" if pr["fin"]["surplus"] == pr["fin"]["income"] - pr["fin"]["exp"] else "fail") if need("financial") else NE, "")
    found = [r for r in (fr, br) if r]
    R("R015", (("pass" if all(r["inst_id"] == I["id"] for r in found) else "fail") if found else NE) if need("fire_cert", "building_plan") else NE, "certificate issued to another institution")
    mism = []
    if need("fire_cert") and fr and fr["valid_to"] != iso(pr["fire"]["valid_to"]): mism.append("fire.valid_to")
    if need("building_plan") and br and br["valid_to"] != iso(pr["building"]["valid_to"]): mism.append("building.valid_to")
    R("R016", "pass" if not mism else "fail", ",".join(mism)); return res

def adapter_status(I, dt, X):
    """Expected SourceAdapter terminal status (+reason) for the document, ignoring scenario flags."""
    pr, T, ad = I["pr"], X.T, I["app_date"]
    if dt in ("infra_statement", "affidavit"): return "not_applicable", "no external issuer (Tier 3 internal consistency only)"
    if dt == "society_reg":
        s = next((r for r in T["society_registry"] if r["reg_no"] == I["soc"]["reg_no"]), None)
        ok = s and norm_name(s["name"]) == norm_name(pr["names"][dt]); return ("verified", "") if ok else ("mismatch_detected", "society name differs from registry")
    if dt == "land_doc":
        r = next((r for r in T["land_records"] if r["survey_no"] == pr["land"]["survey_no"]), None)
        if not r: return "mismatch_detected", "no land record"
        if r["holder_inst_id"] != I["id"]: return "mismatch_detected", "parcel held by another institution"
        if abs(r["area_acres"] - pr["land"]["area_acres"]) > 0.005: return "mismatch_detected", "area differs"
        return "verified", ""
    if dt in ("fire_cert", "building_plan"):
        k = "fire" if dt == "fire_cert" else "building"; tb = "fire_certs" if dt == "fire_cert" else "building_approvals"; r = next((r for r in T[tb] if r["cert_no"] == pr[k]["cert_no"]), None)
        if not r: return "mismatch_detected", "certificate not in registry"
        if r["inst_id"] != I["id"]: return "mismatch_detected", "certificate issued to another institution"
        if r["valid_to"] != iso(pr[k]["valid_to"]): return "mismatch_detected", "validity differs from registry"
        return ("expired", "valid_to before application date") if date.fromisoformat(r["valid_to"]) < ad else ("verified", "")
    if dt == "aff_letter":
        rows = [r for r in T["university_affiliations"] if r["aff_no"] == pr["aff"]["aff_no"]]; return ("verified", "") if rows else ("mismatch_detected", "no affiliation record")
    if dt == "eoa":
        h = {r["course"]: r["approved_intake"] for r in T["aicte_history"] if r["inst_id"] == I["id"] and r["academic_year"] == PRIOR_AY}
        ok = all(h.get(c["course"]) == c["intake"] for c in pr["eoa"]["courses"]); return ("verified", "") if ok else ("mismatch_detected", "prior intake differs")
    if dt == "financial":
        f = next((r for r in T["financials"] if r["inst_id"] == I["id"]), None); ok = f and f["total_income"] == pr["fin"]["income"] and f["total_expenditure"] == pr["fin"]["exp"] and f["ca_firm_no"] == pr["fin"]["firm_no"]
        return ("verified", "") if ok else ("mismatch_detected", "figures differ from filed financials")
    if dt == "faculty_list":
        emp = {r["faculty_id"]: r["inst_id"] for r in T["faculty_employment"] if r["to_date"] is None}; bad = [f for f in pr["faculty"] if emp.get(f["faculty_id"]) != I["id"]]
        return ("verified", "") if not bad else ("mismatch_detected", f"{len(bad)} faculty not employed here per registry")


# ----------------------------------------------------------------------------- DB emission
def sql_val(v):
    if v is None: return "NULL"
    if isinstance(v, (int, float)): return str(v)
    return "'" + str(iso(v)).replace("'", "''") + "'"

def emit_db(X, outdir):
    os.makedirs(outdir, exist_ok=True); path = os.path.join(outdir, "world.sqlite")
    if os.path.exists(path): os.remove(path)
    con = sqlite3.connect(path); pg = ["-- SYNTHETIC mock-authority world DB (PostgreSQL). Safe to load into a dedicated schema.", "BEGIN;"]
    for t, cols in TABLES.items():
        con.execute(f"CREATE TABLE {t} ({', '.join(f'{c} {ty}' for c, ty in cols)})")
        pg.append(f"CREATE TABLE {t} ({', '.join(f'{c} ' + ('DOUBLE PRECISION' if ty == 'REAL' else ty) for c, ty in cols)});")
        for r in X.T[t]:
            vals = [r.get(c) for c, _ in cols]; vals = [iso(v) if isinstance(v, date) else v for v in vals]
            con.execute(f"INSERT INTO {t} VALUES ({','.join('?' * len(cols))})", vals); pg.append(f"INSERT INTO {t} VALUES ({', '.join(sql_val(v) for v in vals)});")
    con.commit(); con.close(); pg.append("COMMIT;"); open(os.path.join(outdir, "world_postgres.sql"), "w").write("\n".join(pg))


# ----------------------------------------------------------------------------- main build
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=80); ap.add_argument("--seed", type=int, default=26261); ap.add_argument("--out", default="out")
    ap.add_argument("--anomaly-rate", type=float, default=0.5); ap.add_argument("--no-watermark", action="store_true"); ap.add_argument("--norms", default=None)
    ap.add_argument("--app-year", type=int, default=2026, help="application year (AY = year-(year+1))"); ap.add_argument("--today", default="2026-10-27", help="PDF metadata dates are capped below this")
    ap.add_argument("--graded-sources", type=int, default=24, help="source docs for the graded-blur calibration set (0=off)"); a = ap.parse_args()
    global TODAY; set_years(a.app_year); TODAY = date.fromisoformat(a.today)
    rng = random.Random(a.seed); np.random.seed(a.seed); norms = yaml.safe_load(open(a.norms)) if a.norms else DEFAULT_NORMS; X = Ctx(rng, norms)
    shutil.rmtree(a.out, ignore_errors=True); os.makedirs(os.path.join(a.out, "documents")); os.makedirs(os.path.join(a.out, "rules"))
    yaml.safe_dump(norms, open(os.path.join(a.out, "rules", "norms.yaml"), "w"), sort_keys=False)
    insts = [make_inst(i + 1, X) for i in range(a.n)]
    for I in insts: I["w"]["_fac"] = [f for f in I["pr"]["faculty"]]; I["mut_cross"] = []
    # pass 1: choose + apply intra mutations
    queue = []
    for I in insts:
        r = rng.random()
        if r < a.anomaly_rate:
            k = 1 if rng.random() < 0.7 else 2; chosen, groups = [], set()
            for _ in range(60):
                if len(chosen) >= k: break
                if not queue: queue.extend(rng.sample(INTRA + CROSS + ["scan_degraded", "copy_move_scan"], len(INTRA + CROSS) + 2))
                m = queue.pop(0); g = group_of(m)
                if g in groups or m in chosen: continue
                chosen.append(m); groups.add(g)
            for m in chosen:
                if m in CROSS: I["mut_cross"].append(m)
                elif m_apply(I, m, X, insts): I["mut"].append(m)
        elif r < a.anomaly_rate + 0.12:   # negative controls: clean scan / benign noise already in names
            if m_apply(I, "scan_clean", X, insts): I["mut"].append("scan_clean")
    for I in insts:   # pass 2: cross-institution
        pend, I["mut_cross"] = I["mut_cross"], []
        for m in pend:
            if m_cross(I, m, X, insts): I["mut"].append(m)
    # scenario flags: simulated source behaviour on ~8% of institutions
    for I in insts:
        if rng.random() < 0.08:
            beh, src, key = rng.choice([("always_503", "fire", I["pr"]["fire"]["cert_no"]), ("pending_then_ok", "building", I["pr"]["building"]["cert_no"]), ("flaky_then_ok", "university", I["pr"]["aff"]["aff_no"]), ("ambiguous_match", "society", I["soc"]["reg_no"])])
            X.T["scenario_flags"].append({"source": src, "record_key": key, "behavior": beh, "note": f"{I['id']}"}); I["flag"] = (src, key, beh)
            if beh == "ambiguous_match":   # decoy near-duplicate society so the lookup is genuinely ambiguous
                dec = dict(I["soc"]); dec.update(society_id=f"SYN-S9{I['id'][-4:]}", reg_no=I["soc"]["reg_no"] + "/B", name=I["soc"]["name"].replace("Educational", "Educational & Charitable")); X.T["society_registry"].append(dec)
    FLAG_DOC = {"fire": "fire_cert", "building": "building_plan", "university": "aff_letter", "society": "society_reg"}
    # pass 3: render
    manifest = []; wm = not a.no_watermark; ndoc = 0
    for I in insts:
        d = os.path.join(a.out, "documents", I["id"]); os.makedirs(d); issued_ref = I["app_date"] - timedelta(days=rng.randint(20, 120))
        for dt in I["docs"]:
            fn, sty = RENDER[dt]; o = I["opts"].get(dt, {}); doc_id = f"{I['id']}-{dt}"; path = os.path.join(d, DOC_NAMES[dt] + ".pdf")
            dw = DW(path, doc_id, STY[sty], wm, o.get("splice")); fn(dw, I, X); fields, pages = dw.finish()
            created = issued_ref + timedelta(days=rng.randint(0, 20)); modified = min(created + timedelta(days=rng.randint(20, 200)), TODAY - timedelta(days=1)) if o.get("meta_edit") else created
            creator = rng.choice(TAMPER_CREATORS) if o.get("meta_edit") else "SynthIssuer/" + rng.choice(GEN_POOL); set_meta(path, created, modified, creator, creator if o.get("meta_edit") else "SynthIssuer PDF Engine 1.0", f"{DOC_NAMES[dt]} {I['id']}")
            if o.get("incr_edit"): apply_incremental_edit(path, fields, o["incr_edit"])
            ent = {"doc_id": doc_id, "inst_id": I["id"], "doc_type": dt, "file": os.path.relpath(path, a.out), "pages": pages, "page_size_pt": [round(W, 1), round(H, 1)], "fields": fields,
                   "pdf_meta": {"creator": creator, "created": iso(created), "modified": iso(modified)}, "labels": I["doc_labels"].get(dt, []), "quality": None, "derived_from": None}
            st, why = adapter_status(I, dt, X); ent["expected_adapter"] = {"status": st, "reason": why}
            fl = I.get("flag")
            if fl and FLAG_DOC[fl[0]] == dt:
                beh = fl[2]; ent["expected_adapter"] = {"status": {"always_503": "source_unavailable", "ambiguous_match": "manual_review_required", "pending_then_ok": "verification_pending", "flaky_then_ok": st}[beh],
                                                       "reason": f"scenario_flag:{beh}", "final_status_after_polling": st if beh in ("pending_then_ok", "flaky_then_ok") else None}
            sc = o.get("scan")
            if sc:   # rasterise page 1, degrade, optional copy-move
                img = rasterize(path); out, moved = degrade(img, sc, fields); lab = {"severity": sc["sev"], "blur_sigma": sc["blur"], "skew_deg": round(sc["skew"], 2), "dpi": SCAN_DPI}
                if sc["copy_move"]:
                    out, mask, info = copy_move(out, sc["seed"]); mp = path[:-4] + "_cm_mask.png"; cv2.imwrite(mp, mask); lab["copy_move"] = info; lab["mask_file"] = os.path.relpath(mp, a.out)
                v = vol(out); lab["variance_of_laplacian"] = round(v, 1); lab["vol_norm1000"] = round(vol_norm(out), 1); lab["expected_gate"] = "pass" if (v >= QUALITY_T and abs(sc["skew"]) <= MAX_SKEW) else "fail"; lab["gate_params"] = {"T_vol": QUALITY_T, "max_skew_deg": MAX_SKEW}
                save_img_pdf(out, path); lab["pixel_bboxes_page1"] = moved; ent["quality"] = lab; ent["is_scan"] = True; ent["pdf_meta"] = {"creator": "SynthScanner 3000", "created": iso(created), "modified": iso(created)}
                ent["fields"] = {k: v2 for k, v2 in fields.items() if v2.get("page") == 1}; ent["pages"] = 1
            ent["sha256"] = sha256_file(path); I["files"][dt] = ent; manifest.append(ent); ndoc += 1
    # pass 4: reuse (byte-exact copies and near-duplicate re-scans)
    byid = {I["id"]: I for I in insts}
    for I in insts:
        for dt, ru in I["reuse"].items():
            donor = byid[ru["donor"]]["files"][dt]; ent = I["files"][dt]; src = os.path.join(a.out, donor["file"]); dst = os.path.join(a.out, ent["file"])
            if ru["mode"] == "exact": shutil.copyfile(src, dst)
            else:
                img = rasterize(src); img = cv2.GaussianBlur(cv2.convertScaleAbs(img, alpha=1.06, beta=4), (0, 0), 0.6); save_img_pdf(img, dst, 80); ent["is_scan"] = True; v = vol(img)
                ent["quality"] = {"severity": "near_dup_rescan", "blur_sigma": 0.6, "skew_deg": 0.0, "dpi": SCAN_DPI, "variance_of_laplacian": round(v, 1), "vol_norm1000": round(vol_norm(img), 1),
                                  "expected_gate": "pass" if v >= QUALITY_T else "fail", "gate_params": {"T_vol": QUALITY_T, "max_skew_deg": MAX_SKEW}}
                ent["pdf_meta"] = {"creator": "SynthScanner 3000", "created": ent["pdf_meta"]["created"], "modified": ent["pdf_meta"]["created"]}
            ent["fields"] = copy.deepcopy(donor["fields"]); ent["derived_from"] = {"doc_id": donor["doc_id"], "mode": ru["mode"], "donor_sha256": donor["sha256"]}; ent["sha256"] = sha256_file(dst)
            if ru["mode"] == "exact": assert ent["sha256"] == donor["sha256"]
            st, why = adapter_status(I, dt, X); ent["expected_adapter"] = {"status": st, "reason": why}
    # graded-blur scans for quality-gate calibration (separate manifest so the main clean set is untouched)
    graded = []
    if a.graded_sources:
        rng2 = random.Random(a.seed + 1); cands = [I for I in insts if not I["mut"] and not I["reuse"]]; rng2.shuffle(cands)
        gdir = os.path.join(a.out, "documents_extra", "graded_blur"); os.makedirs(gdir, exist_ok=True); picked = []
        for I in cands:
            dts = [d for d in ("fire_cert", "building_plan", "society_reg", "affidavit", "eoa") if d in I["docs"] and not I["files"][d].get("is_scan")]
            if dts: picked.append((I, rng2.choice(dts)))
            if len(picked) >= a.graded_sources: break
        for I, dt in picked:
            base = I["files"][dt]; img0 = rasterize(os.path.join(a.out, base["file"]))
            for sg in (0.0, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
                out, _ = degrade(img0, {"seed": rng2.randint(0, 10**6), "blur": sg, "skew": 0.0}, {}); v = vol(out); fn = os.path.join(gdir, f"{base['doc_id']}__gb{sg:g}.pdf"); save_img_pdf(out, fn)
                graded.append({"doc_id": f"{base['doc_id']}__gb{sg:g}", "base_doc_id": base["doc_id"], "inst_id": I["id"], "doc_type": dt, "file": os.path.relpath(fn, a.out), "pages": 1, "is_scan": True,
                               "fields": {k: v2 for k, v2 in base["fields"].items() if v2.get("page") == 1}, "labels": [{"kind": "quality", "name": "graded_blur", "blur_sigma": sg}],
                               "quality": {"severity": "graded", "blur_sigma": sg, "skew_deg": 0.0, "dpi": SCAN_DPI, "variance_of_laplacian": round(v, 1), "vol_norm1000": round(vol_norm(out), 1),
                                           "expected_gate": "pass" if v >= QUALITY_T else "fail", "gate_params": {"T_vol": QUALITY_T, "max_skew_deg": MAX_SKEW}},
                               "sha256": sha256_file(fn), "expected_adapter": base["expected_adapter"], "derived_from": None,
                               "note": "field bboxes are PDF points of the source page (no geometric change); for OCR-accuracy-vs-blur calibration"})
        with open(os.path.join(a.out, "manifest_graded_blur.jsonl"), "w") as f:
            for e in graded: f.write(json.dumps(e, default=iso) + "\n")
    # oracle + outputs
    summary = {"corpus_version": 2, "app_year": APP_YEAR, "graded_blur_docs": len(graded), "institutions": len(insts), "documents": ndoc, "mutation_counts": {}, "rule_fail_counts": {}}; inst_out = []; problems = []
    EXP = {"expired_fire_cert": "R004", "faculty_shortfall": "R001", "builtup_shortfall": "R002", "pc_shortfall": "R003", "name_mismatch": "R008", "intake_exceeds_affiliation": "R007", "land_area_mismatch": "R009",
           "short_lease": "R006", "financial_arithmetic_error": "R014", "unregistered_certificate": "R013", "missing_document": "R010", "double_employed_faculty": "R011", "shared_land_parcel": "R012",
           "certificate_reuse": "R015", "near_duplicate_land": "R012", "spliced_expiry_forgery": "R016", "incremental_edit_forgery": "R016", "qr_date_mismatch": "R016"}
    for I in insts:
        res = evaluate(I, X)
        for m in I["mut"]: summary["mutation_counts"][m] = summary["mutation_counts"].get(m, 0) + 1
        for r, v in res.items():
            if v["result"] == "fail": summary["rule_fail_counts"][r] = summary["rule_fail_counts"].get(r, 0) + 1
        for m in I["mut"]:   # self-check: every policy mutation must flip its rule
            if m in EXP and res[EXP[m]]["result"] != "fail": problems.append((I["id"], m, EXP[m], res[EXP[m]]))
        if not [m for m in I["mut"] if m in EXP]:
            bad = {r: v for r, v in res.items() if v["result"] == "fail"}
            if bad: problems.append((I["id"], "CLEAN-BUT-FAILS", bad))
        inst_out.append({"inst_id": I["id"], "name": I["name"], "type": I["typ"], "state": I["state"], "society_id": I["soc"]["society_id"], "application_type": I["app_type"], "application_date": iso(I["app_date"]),
                         "applied_courses": I["pr"]["applied"], "mutations": I["mut"], "documents_present": I["docs"], "documents_missing": I["missing"], "expected_rules": res,
                         "scenario_flag": I.get("flag"), "required": requirements(norms, I["typ"], sum(c["intake"] for c in I["pr"]["applied"]))})
    emit_db(X, os.path.join(a.out, "world"))
    with open(os.path.join(a.out, "manifest.jsonl"), "w") as f:
        for e in manifest: f.write(json.dumps(e, default=iso) + "\n")
    json.dump(inst_out, open(os.path.join(a.out, "institutions.json"), "w"), indent=1, default=iso)
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    print(json.dumps(summary, indent=1)); print("SELFCHECK PROBLEMS:", len(problems))
    for p in problems[:15]: print("  ", p)
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(main())
