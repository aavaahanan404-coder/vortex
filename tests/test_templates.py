import pytest

from harness import corpus_io as c
from modules import extract as X
from modules import templates_build as TB

CFG = TB.load_config()
TPL = TB.load_templates()


def sample(i, date="01/02/2020", extra=None):
    f = {"ref": {"value": f"SYN/RJ/{2000 + i}/{10000 + i}", "bbox": [205, 100, 90, 9.5], "page": 1, "label": "Reference No."},
         "date": {"value": date, "bbox": [205, 120, 48, 9.5], "page": 1, "label": "Date"},
         "status": {"value": "Active", "bbox": [205, 140, 26, 9.5], "page": 1, "label": "Status"},
         "total": {"value": str(100 + i), "bbox": [50, 300 + 17 * (i % 4), 20, 9.5], "page": 1, "label": "Total"},
         "seal": {"value": None, "bbox": [60, 400, 50, 50], "page": 1}}
    for r in range(1 + i % 3):
        f[f"rows[{r}].name"] = {"value": f"Course {r}", "bbox": [53, 200 + 17 * r, 60, 8.5], "page": 1}
        f[f"rows[{r}].n"] = {"value": str(60 * (r + 1)), "bbox": [443, 200 + 17 * r, 14, 8.5], "page": 1}
    if extra:
        f.update(extra)
    return f


def test_shape_pattern_and_numeric():
    p = TB.shape_pattern(["SYN-REG/RJ/2003/13427", "SYN-REG/GJ/1999/9001"], 6)
    import re
    assert re.fullmatch(p, "SYN-REG/MH/2010/55555") and not re.fullmatch(p, "SYN-REG/MH/2010")
    names = ["Anil Gupta", "Mary Ann Smith", "R. K. Rao", "Dr. A B Chandra Rao", "Li", "Anil-Kumar Gupta", "St. John of God College"]
    assert TB.shape_pattern(names, 3) is None                                  # free text: no pattern
    assert TB.is_numeric(["12/05/2020", "01/01/1999"]) and TB.is_numeric(["Rs. 4,96,03,427"]) and not TB.is_numeric(["Active"])


def test_build_templates_structure():
    t = TB.build_templates([("doc", sample(i), (595.3, 841.9)) for i in range(40)], {**CFG, "min_enum_docs": 30})["doc"]
    sc = t["scalars"]
    assert set(sc) == {"ref", "date", "status", "total"}                       # seal is not a field
    assert sc["ref"]["label"] == "Reference No." and sc["ref"]["value_x"] == 205 and sc["ref"]["pattern"]
    assert sc["status"]["enum"] == ["Active"] and sc["date"]["numeric"] is True
    assert sc["total"]["y_variable"] is True and sc["ref"]["y_variable"] is False   # moves with the table length
    tb = t["tables"]["rows"]
    assert tb["columns"] == {"name": 53.0, "n": 443.0} and tb["page_y0"] == {1: 200.0} and tb["row_pitch"] == 17.0
    assert t["page_size"] == [595.3, 841.9] and t["n_train_docs"] == 40


def test_optional_scalars():
    docs = [("doc", sample(i, extra={"seller": {"value": "X Y", "bbox": [205, 160, 40, 9.5], "page": 1, "label": "Seller"}} if i % 2 else None),
             (595, 842)) for i in range(40)]
    t = TB.build_templates(docs, CFG)["doc"]["scalars"]
    assert t["seller"]["optional"] is True and t["ref"]["optional"] is False


def test_builder_refuses_held_out_corpus():
    from harness import build_templates as B
    with pytest.raises(SystemExit):
        B.main(["--corpus", "corpus_test_v2", "--out", "unused.yaml"])


def test_committed_templates_come_from_the_train_split(tmp_path):
    from harness import build_templates as B
    out = tmp_path / "t.yaml"
    old = c.corpus_root()
    try:
        B.main(["--corpus", "corpus_v2", "--out", str(out)])
    finally:
        c.set_corpus(old)
    import yaml
    assert yaml.safe_load(out.read_text(encoding="utf-8")) == TPL


# ----------------------------------------------------------------------------- layout check
def _fields(dt="fire_cert"):
    e = c.first_doc(dt, clean_only=True, digital=True)
    return X.extract(e["path"], e["doc_id"], dt, TPL)[0].fields, e


def test_clean_documents_have_no_layout_findings():
    n = 0
    for e in c.iter_docs(clean_only=True):
        if c.is_scan(e):
            continue
        f = X.extract(e["path"], e["doc_id"], e["doc_type"], TPL)[0].fields
        bad = [x.id for x in TB.check_layout(f, TPL[e["doc_type"]], e["doc_id"], "text_layer", CFG) if x.kind != "info"]
        assert not bad, (e["doc_id"], bad)
        n += 1
        if n >= 120:
            break


def test_shifted_missing_overlapping_and_out_of_bounds_fields_are_flagged():
    f, e = _fields()
    tpl = TPL["fire_cert"]
    ok = TB.check_layout(f, tpl, "x", "text_layer", CFG)
    assert [x.id for x in ok] == ["layout_ok"]
    key = "issued_on"
    moved = {**f, key: {**f[key], "bbox": [f[key]["bbox"][0], f[key]["bbox"][1] + 25, *f[key]["bbox"][2:]]}}
    s = [x for x in TB.check_layout(moved, tpl, "x", "text_layer", CFG) if x.id == "layout_shifted"]
    assert len(s) == 1 and s[0].kind == "soft" and s[0].tier == 3 and s[0].evidence["field"] == key
    gone = {k: v for k, v in f.items() if k != "valid_to"}
    assert "field_missing" in {x.id for x in TB.check_layout(gone, tpl, "x", "text_layer", CFG)}
    a, b = f["cert_no"], f["issued_on"]
    over = {**f, "issued_on": {**b, "bbox": list(a["bbox"])}}
    assert "fields_overlap" in {x.id for x in TB.check_layout(over, tpl, "x", "text_layer", CFG)}
    oob = {**f, "cert_no": {**a, "bbox": [900, a["bbox"][1], 50, 9]}}
    assert "field_out_of_bounds" in {x.id for x in TB.check_layout(oob, tpl, "x", "text_layer", CFG)}


def test_raster_tolerance_is_wider():
    f, _ = _fields()
    key = "issued_on"
    b = f[key]["bbox"]
    f2 = {**f, key: {**f[key], "bbox": [b[0] + 8, b[1] + 6, b[2], b[3]]}}
    tpl = TPL["fire_cert"]
    assert "layout_shifted" in {x.id for x in TB.check_layout(f2, tpl, "x", "text_layer", CFG)}
    assert "layout_shifted" not in {x.id for x in TB.check_layout(f2, tpl, "x", "ocr", CFG)}
