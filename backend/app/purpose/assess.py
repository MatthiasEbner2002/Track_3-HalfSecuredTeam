"""Assess a request's purpose against Art. 54(1), Art. 53(1) and the SPE justification.

Engines, tried in the order of `engines` in config.yaml (or EHDS_PURPOSE_ENGINES):
- llm:   IBM Bob (cloud language model) judges every point and quotes the request.
- nli:   a local natural-language-inference model scores one statement per point.
- rules: a few unambiguous phrases only.
The first engine that is available and answers decides. The rule floor always
applies on top: a rule hit rejects whatever the engine said.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.models import DataRequest, Evidence
from app.purpose import llm, nli

log = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).with_name("config.yaml")
_SENTENCE = re.compile(r"[^.;!?]+[.;!?]?")


@dataclass
class Assessment:
    prohibited: list[Evidence] = field(default_factory=list)
    allowed: list[Evidence] = field(default_factory=list)
    spe_justification: Evidence | None = None
    flags: list[str] = field(default_factory=list)
    method: str = "rules"  # engine that decided: "llm", "nli" or "rules"


@lru_cache(maxsize=1)
def config() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    for group in (cfg["rules"]["prohibited"], [cfg["rules"]["spe_justification"]]):
        for rule in group:
            rule["patterns"] = [re.compile(p, re.IGNORECASE) for p in rule["any_of"]]
    return cfg


def engines() -> list[str]:
    if os.environ.get("EHDS_PURPOSE_RULES_ONLY") == "1":
        return ["rules"]
    if env := os.environ.get("EHDS_PURPOSE_ENGINES"):
        return [e.strip() for e in env.split(",") if e.strip()]
    return list(config()["engines"])


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.findall(text) if s.strip()] or [text]


def _labels(cfg: dict) -> dict[str, str]:
    return {p["point"]: p["label"] for p in cfg["prohibited"] + cfg["allowed"]}


# ---------------------------------------------------------------- rules


def _rule_hit(patterns: list[re.Pattern[str]], text: str) -> str | None:
    """The sentence that matches one of the patterns, or None."""
    for pattern in patterns:
        if m := pattern.search(text):
            return next((s for s in _sentences(text) if pattern.search(s)), text[m.start() : m.end()])
    return None


def _by_rules(request: DataRequest, cfg: dict) -> Assessment:
    text = f"{request.purpose} {request.question}"
    result = Assessment(method="rules")
    labels = _labels(cfg)
    for rule in cfg["rules"]["prohibited"]:
        if (phrase := _rule_hit(rule["patterns"], text)) and rule["point"] not in {e.point for e in result.prohibited}:
            result.prohibited.append(Evidence(point=rule["point"], label=labels[rule["point"]], phrase=phrase, source="rule"))
    spe = cfg["rules"]["spe_justification"]
    if phrase := _rule_hit(spe["patterns"], f"{text} {' '.join(request.definitions)}"):
        result.spe_justification = Evidence(
            point="spe_justification", label=cfg["spe_justification"]["label"], phrase=phrase, source="rule"
        )
    return result


# ---------------------------------------------------------------- llm


def _by_llm(request: DataRequest, cfg: dict) -> Assessment:
    answer = llm.classify(request, cfg)
    labels = _labels(cfg)
    result = Assessment(method="llm")

    def evidence(item: dict) -> Evidence:
        if not item["quote"]:
            result.flags.append("unverified_quote")
        return Evidence(point=item["point"], label=labels[item["point"]], phrase=item["quote"] or request.purpose, source="llm")

    result.prohibited = [evidence(i) for i in answer["prohibited"]]
    result.allowed = [evidence(i) for i in answer["allowed"]]
    if answer["spe_justified"]:
        result.spe_justification = Evidence(
            point="spe_justification", label=cfg["spe_justification"]["label"],
            phrase=answer["spe_quote"] or request.purpose, source="llm",
        )
    return result


# ---------------------------------------------------------------- nli


def _best(model: str, premise: str, statements: list[str]) -> tuple[float, str]:
    scores = nli.entailment(model, premise, statements)
    i = max(range(len(scores)), key=scores.__getitem__)
    return scores[i], statements[i]


def _evidence_sentence(model: str, text: str, statement: str) -> str:
    """The sentence of `text` that supports the statement most, to quote to the reader."""
    sentences = _sentences(text)
    if len(sentences) == 1:
        return sentences[0]
    scores = [nli.entailment(model, s, [statement])[0] for s in sentences]
    return sentences[max(range(len(scores)), key=scores.__getitem__)]


def _by_nli(request: DataRequest, cfg: dict) -> Assessment:
    model, t = cfg["model"], cfg["thresholds"]
    premise = f"{request.applicant.organisation}. {request.purpose}"
    spe_premise = f"{request.purpose} {request.question}"
    result = Assessment(method="nli")

    # One batch per premise; _best() below then reads from the cache.
    nli.entailment(model, premise, [s for p in cfg["allowed"] + cfg["prohibited"] for s in p["statements"]])
    nli.entailment(model, spe_premise, cfg["spe_justification"]["statements"])

    allowed_best = 0.0
    for point in cfg["allowed"]:
        score, statement = _best(model, premise, point["statements"])
        allowed_best = max(allowed_best, score)
        if score >= t["allowed"]:
            phrase = _evidence_sentence(model, request.purpose, statement)
            result.allowed.append(Evidence(point=point["point"], label=point["label"], phrase=phrase, source="model", score=score))

    for point in cfg["prohibited"]:
        score, statement = _best(model, premise, point["statements"])
        if score >= t["reject"] or (score >= t["review"] and score > allowed_best):
            phrase = _evidence_sentence(model, request.purpose, statement)
            result.prohibited.append(Evidence(point=point["point"], label=point["label"], phrase=phrase, source="model", score=score))

    spe = cfg["spe_justification"]
    score, statement = _best(model, spe_premise, spe["statements"])
    if score >= t["spe"]:
        phrase = _evidence_sentence(model, spe_premise, statement)
        result.spe_justification = Evidence(point="spe_justification", label=spe["label"], phrase=phrase, source="model", score=score)
    return result


# ---------------------------------------------------------------- combine


def _run_engines(request: DataRequest, cfg: dict) -> Assessment:
    failed: list[str] = []
    for engine in engines():
        try:
            if engine == "llm" and llm.available(cfg["llm"]["command"]):
                result = _by_llm(request, cfg)
            elif engine == "nli" and nli.available(cfg["model"]):
                result = _by_nli(request, cfg)
            elif engine == "rules":
                result = _by_rules(request, cfg)
            else:
                continue
        except Exception as exc:  # timeout, bad answer, network: fall back to the next engine
            log.warning("purpose engine %s failed for %s: %s", engine, request.id, exc)
            failed.append(engine)
            continue
        result.flags += [f"{e}_failed" for e in failed]
        return result
    result = _by_rules(request, cfg)
    result.flags += [f"{e}_failed" for e in failed]
    return result


@lru_cache(maxsize=512)
def _assess(request_json: str) -> Assessment:
    request = DataRequest.model_validate_json(request_json)
    cfg = config()
    result = _run_engines(request, cfg)
    if result.method == "rules":
        result.flags.append("rules_only")
    else:
        # The rule floor always applies.
        floor = _by_rules(request, cfg)
        seen = {e.point for e in result.prohibited}
        result.prohibited += [e for e in floor.prohibited if e.point not in seen]
        if not result.spe_justification and floor.spe_justification and result.method == "nli":
            result.spe_justification = floor.spe_justification

    # Staff review when the only grounds for rejecting are borderline model scores.
    reject_at = cfg["thresholds"]["reject"]
    if result.prohibited and all(e.source == "model" and (e.score or 0) < reject_at for e in result.prohibited):
        result.flags.append("needs_review")
    if not result.allowed and not result.prohibited:
        result.flags.append("no_allowed_purpose")
    result.flags = list(dict.fromkeys(result.flags))
    return result


def assess(request: DataRequest) -> Assessment:
    """Assess a request; cached per request content, so repeated calls are free and identical.

    EHDS_PURPOSE_RULES_ONLY=1 skips both models (fast tests); EHDS_PURPOSE_ENGINES=nli,rules
    skips the cloud model.
    """
    return _assess(request.model_dump_json())


def clear_cache() -> None:
    _assess.cache_clear()
