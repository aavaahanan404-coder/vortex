"""Blind double-review routing for risky applications (pure state machine; the API stores the state).

Input : a case (id, composite risk score, category) and the reviewer pool; reviewers' decisions as they arrive.
Output: a plain-JSON state dict and these operations:
          needs_double_review(score, category)  high risk -> two independent reviewers, otherwise one
          open_case(case_id, score, category)   -> state (phase "unassigned")
          assign(state, pool, rng)              two DIFFERENT reviewers drawn at random from the pool (rng is injected: reproducible)
          submit(state, reviewer, decision, notes, pool, rng) records one decision; when both are in: agreement -> resolved,
                                                disagreement -> a THIRD reviewer (not one of the first two) is assigned; the third
                                                decision settles it by majority; three different outcomes go to a committee
          view(state, reviewer)                 what that reviewer may see: while the review is blind the machine score, category,
                                                reasons, the colleague's decision and notes are withheld; after reveal everything is visible
Decisions are approve / reject / request_info; the machine never decides (an unreviewed case is never approved or rejected).
Does NOT: authenticate reviewers (the API does), notify anyone, or persist state.
Phases: unassigned -> blind -> (resolved | awaiting_third -> (resolved | committee)); single review: blind -> resolved.
"""
from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import Optional

import yaml

MODULE = "review"
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "review.yaml"


class ReviewError(ValueError):
    """An operation the state machine refuses (wrong reviewer, repeated submit, unknown decision ...)."""


def load_config(path: Optional[str | Path] = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def needs_double_review(score: float, category: str, cfg: Optional[dict] = None) -> bool:
    cfg = cfg or load_config()
    return score >= cfg["double_review_min_score"] or category in cfg["double_review_categories"]


def open_case(case_id: str, score: float, category: str, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    return {"case_id": case_id, "score": score, "category": category, "reasons": None, "double": needs_double_review(score, category, cfg),
            "phase": "unassigned", "assigned": [], "decisions": {}, "notes": {}, "outcome": None, "history": []}


def _pick(pool: list[str], taken: list[str], rng: random.Random) -> str:
    free = sorted(set(pool) - set(taken))
    if not free:
        raise ReviewError("not enough reviewers in the pool for an independent review")
    return rng.choice(free)


def assign(state: dict, pool: list[str], rng: Optional[random.Random] = None, cfg: Optional[dict] = None) -> dict:
    """Draw the reviewers (two if the case is high risk, else one). Returns a new state."""
    rng = rng or random.Random()
    if state["phase"] != "unassigned":
        raise ReviewError("case is already assigned")
    s = copy.deepcopy(state)
    for _ in range(2 if s["double"] else 1):
        s["assigned"].append(_pick(pool, s["assigned"], rng))
    s["phase"] = "blind"
    s["history"].append({"event": "assigned", "reviewers": list(s["assigned"])})
    return s


def submit(state: dict, reviewer: str, decision: str, notes: str = "", pool: Optional[list[str]] = None,
           rng: Optional[random.Random] = None, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    if decision not in cfg["decisions"]:
        raise ReviewError(f"decision must be one of {cfg['decisions']}")
    if state["phase"] not in ("blind", "awaiting_third"):
        raise ReviewError(f"case is {state['phase']}: no decisions are accepted")
    if reviewer not in state["assigned"]:
        raise ReviewError("this reviewer is not assigned to the case")
    if reviewer in state["decisions"]:
        raise ReviewError("this reviewer has already submitted")
    s = copy.deepcopy(state)
    s["decisions"][reviewer] = decision
    s["notes"][reviewer] = notes
    s["history"].append({"event": "submitted", "reviewer": reviewer})
    need = len(s["assigned"])
    if len(s["decisions"]) < need:
        return s
    votes = [s["decisions"][r] for r in s["assigned"]]
    if need == 1 or len(set(votes)) == 1:
        s["phase"], s["outcome"] = "resolved", votes[0]
    elif need == 2:                                              # the two disagree: a third, independent reviewer breaks the tie
        s["assigned"].append(_pick(pool or [], s["assigned"], rng or random.Random()))
        s["phase"] = "awaiting_third"
        s["history"].append({"event": "disagreement", "third": s["assigned"][-1]})
    else:
        top = max(set(votes), key=votes.count)
        if votes.count(top) >= 2:
            s["phase"], s["outcome"] = "resolved", top
        else:
            s["phase"], s["outcome"] = "committee", None           # three different outcomes: a person above the reviewers decides
    return s


def revealed(state: dict, reviewer: str) -> bool:
    """Is the blind phase over for this reviewer? Resolved / committee cases are open to the assigned reviewers; on a disagreement
    the first two reviewers see each other's decisions, while the tie-breaker stays blind until they have submitted themselves."""
    if state["phase"] in ("resolved", "committee"):
        return True
    return state["phase"] == "awaiting_third" and reviewer in state["assigned"][:2]


def view(state: dict, reviewer: str, cfg: Optional[dict] = None) -> dict:
    """The case as `reviewer` may see it. While the review is blind the machine score/category/reasons and the colleagues'
    decisions and notes are withheld; a reviewer sees only their own decision. Once the case is resolved or with the committee
    everything is visible. A person who is not assigned sees nothing of the review."""
    cfg = cfg or load_config()
    if reviewer not in state["assigned"]:
        raise ReviewError("this reviewer is not assigned to the case")
    open_ = revealed(state, reviewer)
    out = {"case_id": state["case_id"], "phase": state["phase"], "your_decision": state["decisions"].get(reviewer),
           "your_notes": state["notes"].get(reviewer), "n_reviewers": len(state["assigned"]),
           "submitted": sorted(state["decisions"]) if open_ else len(state["decisions"])}
    if open_:
        out.update({"score": state["score"], "category": state["category"], "reasons": state["reasons"], "outcome": state["outcome"],
                    "decisions": dict(state["decisions"]), "notes": dict(state["notes"])})
    else:
        out.update({f: None for f in ("score", "category", "reasons")})
    return out
