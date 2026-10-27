"""Shared contract for all SIH26261 verification modules.

This is the ONLY file modules have in common. It defines two dataclasses, JSON
(de)serialisation helpers and a JSON Schema. It does no I/O.

Trust tiers: 1 = cryptographic, 2 = registry lookup, 3 = internal consistency,
4 = ML / image heuristic (advisory only; may never be a "hard" finding).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

KINDS = ("hard", "soft", "info")
TIERS = (1, 2, 3, 4)


@dataclass
class Finding:
    id: str                       # e.g. "covered_text", "qr_mismatch", "blurry", "R004"
    module: str
    kind: str                     # "hard" | "soft" | "info"
    tier: int                     # 1 crypto, 2 registry, 3 internal consistency, 4 ML/advisory
    p: float                      # 0..1 confidence that something is wrong
    message: str                  # human-readable reason, no jargon
    doc_id: str
    bbox: Optional[tuple] = None  # (page, x, y, w, h) PDF points, top-left origin
    evidence: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {self.kind!r}")
        if self.tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {self.tier!r}")
        if not 0.0 <= float(self.p) <= 1.0:
            raise ValueError(f"p must be in [0,1], got {self.p!r}")
        if self.kind == "hard" and self.tier == 4:
            # Principle 1: ML / image heuristics are advisory and can never be hard.
            raise ValueError("Tier-4 (ML/heuristic) findings may not be kind='hard'")
        if self.bbox is not None:
            self.bbox = tuple(self.bbox)
            if len(self.bbox) != 5:
                raise ValueError("bbox must be (page, x, y, w, h)")

    def to_json(self) -> dict:
        d = asdict(self)
        d["bbox"] = list(self.bbox) if self.bbox is not None else None
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Finding":
        d = dict(d)
        if d.get("bbox") is not None:
            d["bbox"] = tuple(d["bbox"])
        return cls(**d)


@dataclass
class ExtractedDoc:
    doc_id: str
    doc_type: str
    fields: dict                  # key -> {value, bbox, page, conf, source}

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "ExtractedDoc":
        return cls(**d)


def findings_to_json(findings: list[Finding]) -> str:
    return json.dumps([f.to_json() for f in findings], indent=2, ensure_ascii=False)


def findings_from_json(s: str) -> list[Finding]:
    return [Finding.from_json(d) for d in json.loads(s)]


FINDING_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Finding",
    "type": "object",
    "required": ["id", "module", "kind", "tier", "p", "message", "doc_id"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "module": {"type": "string", "minLength": 1},
        "kind": {"enum": list(KINDS)},
        "tier": {"enum": list(TIERS)},
        "p": {"type": "number", "minimum": 0, "maximum": 1},
        "message": {"type": "string", "minLength": 1},
        "doc_id": {"type": "string"},
        "bbox": {"anyOf": [{"type": "null"},
                           {"type": "array", "items": {"type": "number"},
                            "minItems": 5, "maxItems": 5}]},
        "evidence": {"type": "object"},
    },
    # Tier 4 may never be hard (principle 1).
    "if": {"properties": {"tier": {"const": 4}}},
    "then": {"properties": {"kind": {"enum": ["soft", "info"]}}},
}

EXTRACTED_DOC_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ExtractedDoc",
    "type": "object",
    "required": ["doc_id", "doc_type", "fields"],
    "properties": {
        "doc_id": {"type": "string"},
        "doc_type": {"type": "string"},
        "fields": {"type": "object", "additionalProperties": {
            "type": "object",
            "properties": {"value": {}, "bbox": {}, "page": {"type": "integer"},
                           "conf": {"type": ["number", "null"]},
                           "source": {"type": ["string", "null"]}}}},
    },
}
