import json
import jsonschema
import pytest
from contracts import (Finding, ExtractedDoc, FINDING_SCHEMA, EXTRACTED_DOC_SCHEMA,
                       findings_to_json, findings_from_json)


def mk(**kw):
    base = dict(id="blurry", module="quality", kind="soft", tier=4, p=0.7,
                message="too blurry", doc_id="D1", bbox=(1, 1, 2, 3, 4))
    base.update(kw)
    return Finding(**base)


def test_roundtrip_and_schema():
    f = mk(evidence={"vol": 12.5})
    d = f.to_json()
    jsonschema.validate(d, FINDING_SCHEMA)
    assert Finding.from_json(json.loads(json.dumps(d))) == f


def test_list_roundtrip():
    fs = [mk(), mk(id="x", bbox=None)]
    assert findings_from_json(findings_to_json(fs)) == fs


def test_tier4_cannot_be_hard():
    with pytest.raises(ValueError):
        mk(kind="hard", tier=4)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(mk().to_json() | {"kind": "hard"}, FINDING_SCHEMA)


@pytest.mark.parametrize("kw", [dict(kind="bogus"), dict(tier=9), dict(p=1.5),
                                dict(p=-0.1), dict(bbox=(1, 2))])
def test_validation(kw):
    with pytest.raises(ValueError):
        mk(**kw)


def test_extracted_doc():
    e = ExtractedDoc("D", "fire_cert", {"valid_to": {"value": "x", "bbox": [1, 2, 3, 4],
                                                      "page": 1, "conf": 0.9, "source": "text"}})
    jsonschema.validate(e.to_json(), EXTRACTED_DOC_SCHEMA)
    assert ExtractedDoc.from_json(e.to_json()) == e
