import functools
import shutil

import cv2
import numpy as np
import pymupdf
import pytest

from harness import corpus_io as c
from harness import run_module as rm
from modules import dedup as d

CFG = d.load_config()
EXACT = list(c.iter_docs(label="exact_duplicate"))
NEAR = list(c.iter_docs(label="visual_near_duplicate"))


def ids(fs): return {f.id for f in fs}


@functools.lru_cache(maxsize=2)
def corpus_index(root):
    """Index of the whole selected corpus, built once per test session (about 20 s)."""
    return d.build_index([(x["doc_id"], x["inst_id"], x["doc_type"], x["path"]) for x in c.iter_docs()])


def sig_from_shingles(sh, raster=False, sha="x"):
    return {"sha256": sha, "phash": None, "minhash": d.minhash(sh, CFG["n_perm"], CFG["seed"]),
            "n_shingles": len(sh), "raster": raster}


# ----------------------------------------------------------------------------- primitives
def test_minhash_estimates_jaccard():
    rs = np.random.RandomState(7)
    r = lambda k: set(rs.randint(0, 2 ** 31, k).astype(np.int64).tolist())
    for shared in (0, 40, 100, 160, 200):
        base = r(shared); a = base | r(200 - shared); b = base | r(200 - shared)
        true = len(a & b) / len(a | b)
        est = float((d.minhash(a) == d.minhash(b)).mean())
        assert abs(est - true) < 0.12, (true, est)
    assert d.minhash(set()) is None


def test_minhash_not_dominated_by_smallest_hash():
    """Regression: a (a*x+b) mod p family made one tiny shared hash decide every permutation."""
    a = {1, *range(1000, 1023)}; b = {1, *range(2000, 2023)}
    assert float((d.minhash(a) == d.minhash(b)).mean()) < 0.2


def test_shingles_and_phash_basics():
    assert d.shingles("one two", 3) == set()
    assert len(d.shingles("a b c d e", 3)) == 3
    assert d.phash_bits(np.full((200, 200), 255, np.uint8)) is None          # blank page: no hash
    rs = np.random.RandomState(0)
    img = (rs.rand(300, 300) * 255).astype(np.uint8)
    h = d.phash_bits(img, 16, 4)
    assert h.shape == (256,) and h.dtype == bool


# ----------------------------------------------------------------------------- exact / text
@pytest.mark.parametrize("e", EXACT, ids=lambda e: e["doc_id"])
def test_exact_duplicates_found_with_their_donor(e):
    idx = corpus_index(c.corpus_root())
    fs = d.analyze(e["path"], e["doc_id"], idx, e["inst_id"], e["doc_type"])
    ex = [f for f in fs if f.id == "exact_duplicate"]
    assert len(ex) == 1 and ex[0].kind == "soft"
    assert e["derived_from"]["doc_id"] in {m["doc_id"] for m in ex[0].evidence["matches"]}
    donor = c.get_entry(e["derived_from"]["doc_id"])                       # the donor sees the pair too
    assert e["doc_id"] in {m["doc_id"] for f in d.analyze(donor["path"], donor["doc_id"], idx, donor["inst_id"],
                                                          donor["doc_type"]) for m in f.evidence.get("matches", [])}
    assert e["sha256"] == e["derived_from"]["donor_sha256"] == c.get_entry(e["derived_from"]["doc_id"])["sha256"]


def test_no_unrelated_links_in_corpus():
    r = rm.evaluate("dedup")
    assert r["fp"][1] > 100 and r["fp"][0] == 0, r["fp_docs"]
    assert r["det"]["exact_duplicate"][0] == r["det"]["exact_duplicate"][1] >= 1


def test_text_near_duplicate_threshold_semantics():
    base = set(range(10_000, 10_100))
    near = (base - set(range(10_000, 10_010))) | set(range(20_000, 20_005))        # ~89% shared
    far = set(range(10_000, 10_030)) | set(range(30_000, 30_070))                  # 30% shared
    idx = d.Index(CFG)
    idx.add("A", "I1", "land_doc", sig_from_shingles(base, sha="a"))
    idx.add("B", "I2", "land_doc", sig_from_shingles(near, sha="b"))
    idx.add("C", "I3", "land_doc", sig_from_shingles(far, sha="c"))
    idx.add("D", "I4", "fire_cert", sig_from_shingles(base | {1}, sha="d"))         # other type
    m = d.find_matches(idx.get("A"), idx, "A", "I1", "land_doc")
    assert [x["doc_id"] for x in m["text"]] == ["B"] and not m["exact"] and not m["visual"]
    fs = d.analyze("unused", "A", idx, "I1", "land_doc")
    t = [f for f in fs if f.id == "text_near_duplicate"][0]
    assert t.kind == "soft" and t.tier == 3 and t.evidence["matches"][0]["similarity"] > CFG["min_jaccard"]


def test_cross_institution_only_and_override():
    sh = set(range(500, 600))
    idx = d.Index(CFG)
    idx.add("A", "I1", "land_doc", sig_from_shingles(sh, sha="s")); idx.add("B", "I1", "land_doc", sig_from_shingles(sh, sha="s"))
    assert ids(d.analyze("u", "A", idx, "I1", "land_doc")) == {"dedup_ok"}
    cfg = {**CFG, "cross_institution_only": False}
    idx2 = d.Index(cfg)
    idx2.add("A", "I1", "land_doc", sig_from_shingles(sh, sha="s")); idx2.add("B", "I1", "land_doc", sig_from_shingles(sh, sha="s"))
    assert "exact_duplicate" in ids(d.analyze("u", "A", idx2, "I1", "land_doc", cfg))


def test_resaved_pdf_is_text_duplicate_not_exact(tmp_path):
    """Same content, different bytes (re-saved with new metadata) -> text near-duplicate, not exact."""
    src = next(e for e in c.iter_docs(clean_only=True) if e["doc_type"] == "faculty_list" and not c.is_scan(e))
    out = tmp_path / "resaved.pdf"
    with pymupdf.open(src["path"]) as doc:
        doc.set_metadata({"creator": "someone else"}); doc.save(str(out), garbage=4, deflate=True)
    assert d.sha256_file(out) != d.sha256_file(src["path"])
    ents = list(c.iter_docs(doc_type="faculty_list"))
    idx = d.build_index([(x["doc_id"], x["inst_id"], x["doc_type"], x["path"]) for x in ents])
    fs = d.analyze(out, "new-upload", idx, "OTHER-INST", "faculty_list")
    assert "text_near_duplicate" in ids(fs) and "exact_duplicate" not in ids(fs)
    assert src["doc_id"] in {m["doc_id"] for f in fs for m in f.evidence["matches"]}


# ----------------------------------------------------------------------------- visual
def _scan_png(e, tmp_path, name, transform=None):
    """Page image of a scan, optionally degraded the way re-saving does, as a PNG file."""
    g = d.render_page1_gray(e["path"], 900)
    if transform:
        g = transform(g)
    p = tmp_path / name
    cv2.imwrite(str(p), g)
    return p


def test_visual_catches_resaved_scan_copies_and_not_other_scans(tmp_path):
    scans = [e for e in c.iter_docs(clean_only=True) if c.is_scan(e)]
    a, other = scans[0], scans[1]
    idx = d.Index(CFG)
    for e in scans:
        idx.add(e["doc_id"], e["inst_id"], e["doc_type"], d.signature(e["path"], CFG, e["doc_type"]))
    # NOT covered, by measurement (report): brightness offsets that clip the page background move the
    # distance to 0.05-0.10 (+18 levels: median 0.052), and any rotation (0.5 degrees: 0.11).
    variants = {"jpeg": lambda g: cv2.imdecode(cv2.imencode(".jpg", g, [cv2.IMWRITE_JPEG_QUALITY, 40])[1], 0),
                "lower_contrast": lambda g: np.clip((g.astype(np.float32) - 128) * 0.85 + 128, 0, 255).astype(np.uint8),
                "scaled": lambda g: cv2.resize(cv2.resize(g, (600, int(g.shape[0] * 600 / g.shape[1]))), (900, g.shape[0]))}
    for name, fn in variants.items():
        p = _scan_png(a, tmp_path, f"{name}.png", fn)
        fs = d.analyze(p, "upload", idx, "OTHER-INST", a["doc_type"])
        v = [f for f in fs if f.id == "visual_near_duplicate"]
        assert v and v[0].tier == 4 and v[0].kind == "soft", name          # image heuristic: never hard
        assert a["doc_id"] in {m["doc_id"] for m in v[0].evidence["matches"]}, name
    clean_other = tmp_path / "other.png"
    cv2.imwrite(str(clean_other), d.render_page1_gray(other["path"], 900))
    fs = d.analyze(clean_other, "upload2", idx, "OTHER-INST", other["doc_type"])
    assert {m["doc_id"] for f in fs for m in f.evidence.get("matches", [])} <= {other["doc_id"]}


def test_visual_not_applied_between_digital_pages():
    """Digital pages of one template are all near-identical in pHash: the visual channel is raster-only."""
    e = c.first_doc("faculty_list", clean_only=True, digital=True)
    idx = d.Index(CFG)
    s = d.signature(e["path"], CFG, "faculty_list")
    idx.add("A", "I1", "faculty_list", s)
    twin = {**s, "sha256": "different"}
    idx.add("B", "I2", "faculty_list", twin)
    assert not d.find_matches(s, idx, "A", "I1", "faculty_list")["visual"]
    cfg = {**CFG, "visual_raster_only": False}
    idx2 = d.Index(cfg); idx2.add("A", "I1", "faculty_list", s); idx2.add("B", "I2", "faculty_list", twin)
    assert d.find_matches(s, idx2, "A", "I1", "faculty_list")["visual"]


@pytest.mark.parametrize("e", NEAR, ids=lambda e: e["doc_id"])
def test_rerendered_near_duplicate_scans_found_with_donor(e):
    """Field-region channel: a re-scanned templated page is matched to its digital donor."""
    idx = corpus_index(c.corpus_root())
    fs = d.analyze(e["path"], e["doc_id"], idx, e["inst_id"], e["doc_type"])
    v = [f for f in fs if f.id == "visual_near_duplicate"]
    assert v and v[0].kind == "soft" and v[0].tier == 4                       # image heuristic, never hard
    m = {x["doc_id"]: x for x in v[0].evidence["matches"]}
    assert e["derived_from"]["doc_id"] in m and m[e["derived_from"]["doc_id"]]["method"] == "field_regions"
    donor = c.get_entry(e["derived_from"]["doc_id"])                          # the digital donor sees the pair too
    dv = [f for f in d.analyze(donor["path"], donor["doc_id"], idx, donor["inst_id"], donor["doc_type"])
          if f.id == "visual_near_duplicate"]
    assert dv and e["doc_id"] in {x["doc_id"] for x in dv[0].evidence["matches"]}


def test_new_upload_scan_of_a_donor_is_matched_but_unrelated_scan_is_not(tmp_path):
    """A fresh upload (not in the index): the donor page re-scanned at 110 dpi with noise and JPEG."""
    idx = corpus_index(c.corpus_root())
    donor = NEAR[0]
    src = c.get_entry(donor["derived_from"]["doc_id"])
    with pymupdf.open(src["path"]) as doc:
        pm = doc[0].get_pixmap(dpi=110, colorspace=pymupdf.csGRAY, alpha=False)
    g = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.width).astype(np.float32)
    rs = np.random.RandomState(5)
    g = cv2.GaussianBlur(g, (0, 0), 0.6) + rs.normal(0, 4, g.shape)
    g = cv2.imdecode(cv2.imencode(".jpg", np.clip(g, 0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 70])[1], 0)
    png = tmp_path / "rescan.png"; cv2.imwrite(str(png), g)
    pdf = tmp_path / "rescan.pdf"
    with pymupdf.open() as out:
        pg = out.new_page(width=src["page_size_pt"][0], height=src["page_size_pt"][1])
        pg.insert_image(pg.rect, filename=str(png)); out.save(str(pdf))
    fs = d.analyze(pdf, "upload", idx, "OTHER-INST", src["doc_type"])
    v = [f for f in fs if f.id == "visual_near_duplicate"]
    assert v and src["doc_id"] in {x["doc_id"] for x in v[0].evidence["matches"]}
    other = next(e for e in c.iter_docs(clean_only=True) if c.is_scan(e) and e["doc_type"] == src["doc_type"]
                 or (c.is_scan(e) and e["doc_type"] in ("fire_cert", "eoa", "society_reg")))
    assert "visual_near_duplicate" not in ids(d.analyze(other["path"], "upload2", idx, "OTHER-INST", other["doc_type"]))


def test_field_channel_needs_enough_digital_documents():
    """With fewer digital documents of the type than field_min_digital there are no boxes: no match."""
    e = NEAR[0]
    donor = c.get_entry(e["derived_from"]["doc_id"])
    idx = d.build_index([(x["doc_id"], x["inst_id"], x["doc_type"], x["path"]) for x in (e, donor)])
    assert idx._type_cache(e["doc_type"]) is None
    assert "visual_near_duplicate" not in ids(d.analyze(e["path"], e["doc_id"], idx, e["inst_id"], e["doc_type"]))


# ----------------------------------------------------------------------------- field-value channel
LAND = {"doc_no": "SYN-DEED/TS/320487", "reg_date": "09 October 2019", "survey_no": "SYN-SY/TS/138/8A", "location": "Warangal, Telangana",
        "area_acres": "11.67 Acres", "seller": "Rajesh Mishra", "society_name": "Hoysala Educational Society", "tenure": "Absolute Ownership"}


def vindex(rows, cfg=CFG):
    """rows: (doc_id, inst, raster, fields) -> Index with fields only (no pixels): isolates the value channel."""
    idx = d.Index(cfg)
    for did, inst, raster, f in rows:
        sig = {"sha256": did, "phash": None, "minhash": None, "n_shingles": 0, "raster": raster, "path": None,
               "fields": d.scalar_fields(f, cfg["fv_min_conf"])}
        idx.add(did, inst, "land_doc", sig)
    return idx


def filler(n=12):
    """unrelated land documents so that template-constant values (tenure) are recognised as such"""
    return [(f"F{i}", f"IF{i}", False, {**LAND, "doc_no": f"SYN-DEED/XX/{i}", "survey_no": f"SYN-SY/XX/{i}", "seller": f"Seller {i}",
                                         "society_name": f"Society {i}", "location": f"Town {i}", "reg_date": f"{i + 1} May 2018",
                                         "area_acres": f"{i + 3}.10 Acres"}) for i in range(n)]


def test_value_channel_matches_a_rescan_even_with_ocr_typos():
    typo = {**LAND, "doc_no": "SYN-DEED/TS/32O487", "seller": "Rajesh Mishra"}           # O for 0: fuzzy equal
    idx = vindex(filler() + [("DONOR", "I1", False, LAND), ("SCAN", "I2", True, typo)])
    fs = d.analyze("u", "SCAN", idx, "I2", "land_doc")
    v = [f for f in fs if f.id == "visual_near_duplicate"][0]
    m = v.evidence["matches"][0]
    assert m["doc_id"] == "DONE".replace("DONE", "DONOR") and m["method"] == "field_values" and m["similarity"] == 1.0
    assert v.tier == 4 and v.kind == "soft"
    assert "tenure" not in m["fields_equal"]                                          # template-constant values are ignored
    back = [f for f in d.analyze("u", "DONOR", idx, "I1", "land_doc") if f.id == "visual_near_duplicate"]
    assert back and back[0].evidence["matches"][0]["doc_id"] == "SCAN"


def test_value_channel_ignores_same_entity_documents_that_differ_in_one_field():
    """Two institutions of one society share its registration data but not the registered office: not a copy."""
    other = {**LAND, "location": "Rajkot, Gujarat"}
    idx = vindex(filler() + [("A", "I1", False, LAND), ("B", "I2", True, other)])
    assert "visual_near_duplicate" not in {f.id for f in d.analyze("u", "B", idx, "I2", "land_doc")}


def test_unreadable_field_cannot_hide_a_difference():
    """A field the extractor could not read (low conf) is left out: with too little left the pair is undecided, not a match."""
    scan = {k: ({"value": v, "conf": 0.3} if k == "location" else {"value": v, "conf": 0.95}) for k, v in LAND.items()}
    donor = {**LAND, "location": "Rajkot, Gujarat"}
    idx = vindex(filler() + [("A", "I1", False, donor), ("B", "I2", True, scan)])
    assert d.scalar_fields(scan, 0.6).get("location") is None
    assert "visual_near_duplicate" not in {f.id for f in d.analyze("u", "B", idx, "I2", "land_doc")}
    vm, undecided = idx.value_matches(idx.get("B"), "B", "I2", "land_doc")
    assert not vm and "A" in undecided


def test_value_channel_needs_a_raster_side_and_another_institution():
    idx = vindex(filler() + [("A", "I1", False, LAND), ("B", "I2", False, LAND)])            # digital-digital: text channel's job
    assert not idx.value_matches(idx.get("B"), "B", "I2", "land_doc")[0]
    idx = vindex(filler() + [("A", "I1", False, LAND), ("B", "I1", True, LAND)])             # same institution
    assert not idx.value_matches(idx.get("B"), "B", "I1", "land_doc")[0]


def test_scalar_fields_drops_tables_and_junk():
    f = {"a": {"value": "X 1", "conf": 1.0}, "rows[0].c": {"value": "t", "conf": 1.0}, "seal": {"value": "s"}, "qr": "q",
         "e": {"value": "", "conf": 1.0}, "low": {"value": "zz", "conf": 0.1}}
    assert d.scalar_fields(f) == {"a": "x1"}
    assert d.values_agree("syndeed32o487", "syndeed320487", CFG) and not d.values_agree("abc", "abd", CFG)


@pytest.mark.skipif(not __import__("modules.extract", fromlist=["x"]).ocr_available(), reason="Tesseract not installed")
@pytest.mark.parametrize("e", NEAR, ids=lambda e: e["doc_id"])
def test_near_duplicate_scans_are_matched_from_extracted_values(e):
    from harness import run_module as rm
    idx = value_index(c.corpus_root())
    fs = d.analyze(e["path"], e["doc_id"], idx, e["inst_id"], e["doc_type"])
    m = [x for f in fs if f.id == "visual_near_duplicate" for x in f.evidence["matches"] if x["doc_id"] == e["derived_from"]["doc_id"]]
    assert m, "donor not found"
    assert m[0]["method"] in ("field_values", "field_regions")


@functools.lru_cache(maxsize=2)
def value_index(root):
    from harness import run_module as rm
    return rm._dedup_prepare(list(c.iter_docs()))


@pytest.mark.skipif(not __import__("modules.extract", fromlist=["x"]).ocr_available(), reason="Tesseract not installed")
def test_value_channel_has_no_false_links_in_the_corpus():
    from harness import run_module as rm
    idx = value_index(c.corpus_root())
    ents = list(c.iter_docs())
    bad = []
    expected = {e["doc_id"] for e in ents if e.get("derived_from")} | {e["derived_from"]["doc_id"] for e in ents if e.get("derived_from")}
    for e in ents:
        if e["doc_id"] in expected:
            continue
        fs = d.analyze(e["path"], e["doc_id"], idx, e["inst_id"], e["doc_type"])
        got = [(f.id, [m["doc_id"] for m in f.evidence["matches"]]) for f in fs if f.id in ("visual_near_duplicate", "text_near_duplicate", "exact_duplicate")]
        got = [g for g in got if not set(g[1]) <= expected]
        if c.is_clean(e) and got:
            bad.append((e["doc_id"], got))
    assert not bad, bad[:5]


# ----------------------------------------------------------------------------- robustness / cli
def test_bad_inputs(tmp_path):
    idx = d.Index(CFG)
    fs = d.analyze(tmp_path / "missing.pdf", "x", idx, "I", "land_doc")
    assert fs[0].id == "dedup_unreadable" and fs[0].kind == "info"
    (tmp_path / "bad.pdf").write_bytes(b"not a pdf")
    sig = d.signature(tmp_path / "bad.pdf", CFG)               # hashing works on any bytes; page/text just absent
    assert sig["phash"] is None and sig["minhash"] is None
    assert ids(d.analyze(tmp_path / "bad.pdf", "x", idx, "I", "land_doc")) == {"dedup_ok"}      # empty index
    assert d.build_index([("a", "i", "t", tmp_path / "missing.pdf")], CFG).docs == []


def test_cli(tmp_path, capsys):
    e = EXACT[0]
    copy = tmp_path / "copy.pdf"; shutil.copy(e["path"], copy)
    assert d.main([str(copy), "--against", e["path"]]) == 0
    assert '"exact_duplicate"' in capsys.readouterr().out
