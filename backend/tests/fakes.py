"""Stand-ins for Ollama so the default test run needs no model and no law text.

The chunk texts are placeholders, not the regulation.
"""

import hashlib
import re

from app.models import LawParagraph

CHUNKS = [
    LawParagraph(id="Art. 53(1)(a)", kind="point", article="53", lead_in="placeholder purposes:",
                 text="(a) placeholder public health and occupational health;"),
    LawParagraph(id="Art. 53(1)(e)", kind="point", article="53", lead_in="placeholder purposes:",
                 text="(e) placeholder scientific research related to health;"),
    LawParagraph(id="Art. 54(b)", kind="point", article="54", lead_in="placeholder prohibited:",
                 text="(b) placeholder decisions on insurance premiums or job offers;"),
    LawParagraph(id="Art. 54(c)", kind="point", article="54", lead_in="placeholder prohibited:",
                 text="(c) placeholder advertising or marketing activities;"),
    LawParagraph(id="Art. 66(2)", article="66", text="placeholder access only when the purpose cannot be achieved with anonymised data"),
    LawParagraph(id="Art. 66(3)", article="66", text="placeholder pseudonymised data when the applicant justifies patient-level data"),
    LawParagraph(id="Art. 68(1)(c)", kind="point", article="68", lead_in="placeholder data permit:",
                 text="(c) placeholder access in a secure processing environment;"),
    LawParagraph(id="Art. 69(1)", article="69", text="placeholder anonymised statistical format request"),
    LawParagraph(id="Art. 73(1)", article="73", text="placeholder secure processing environment, no download"),
    LawParagraph(id="Recital 62", kind="recital", article="", text="placeholder recital on prohibited uses"),
]


class FakeEmbedder:
    """Bag of words hashed into 512 dimensions: similar wording, similar vector."""

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * 512
        for word in re.findall(r"[a-z]+", text.lower()):
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % 512] += 1.0
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FakeLLM:
    """Answers the extract call with fixed facts and the match call with a fixed rule."""

    def __init__(self, rule_id: str = "Art. 53(1)(e)", words: str = "scientific research",
                 spe_quote: str | None = None):
        self.match_answer = {"rule_id": rule_id, "matching_words": words}
        self.spe_quote = spe_quote
        self.calls: list[tuple[str, str, dict]] = []

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        self.calls.append((system, user, schema))
        if "justified" in schema["properties"]:
            return {"quote": self.spe_quote or "", "justified": self.spe_quote is not None}
        if "end_use" in schema["properties"]:
            return {"actor": "a research group", "analysis": "count patients",
                    "end_use": "study diabetes", "affected_people": "nobody"}
        return self.match_answer
