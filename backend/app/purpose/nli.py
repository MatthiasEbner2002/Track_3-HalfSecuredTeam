"""Local natural-language-inference (NLI) model: how strongly does a text support a statement?

The model runs offline on CPU and only ever sees request text (purpose, question,
organisation), never patient data. Scores are deterministic and cached, so the
same request always gets the same answer.

If torch/transformers are not installed or the model cannot be loaded, available()
returns False and callers fall back to rules.
"""

import os
import threading
from functools import lru_cache

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _load(name: str):
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError:
        return None
    try:
        torch.set_num_threads(int(os.environ.get("EHDS_NLI_THREADS", min(8, os.cpu_count() or 1))))
        tokenizer = AutoTokenizer.from_pretrained(name)
        # float32: some checkpoints are stored in float16, which is very slow on CPU.
        model = AutoModelForSequenceClassification.from_pretrained(name, dtype=torch.float32).eval()
    except Exception:  # offline without a cached model, corrupt download, ...
        return None
    labels = {v.lower(): int(k) for k, v in model.config.id2label.items()}
    # Probability of "entailment" over all classes (entailment/neutral/contradiction or
    # entailment/not_entailment). Neutral must count against entailment, otherwise
    # unrelated statements score high.
    return torch, tokenizer, model, labels["entailment"]


def available(name: str) -> bool:
    return _load(name) is not None


_cache: dict[tuple[str, str, str], float] = {}


def entailment(name: str, premise: str, hypotheses: list[str]) -> list[float]:
    """Probability (0..1) that `premise` entails each hypothesis."""
    loaded = _load(name)
    if loaded is None:
        raise RuntimeError(f"NLI model {name!r} is not available")
    torch, tokenizer, model, entail = loaded
    missing = [h for h in hypotheses if (name, premise, h) not in _cache]
    if missing:
        with _lock, torch.inference_mode():
            batch = tokenizer([premise] * len(missing), missing, return_tensors="pt", padding=True, truncation=True)
            probs = model(**batch).logits.softmax(dim=-1)[:, entail].tolist()
        for h, p in zip(missing, probs):
            _cache[(name, premise, h)] = round(float(p), 4)
    return [_cache[(name, premise, h)] for h in hypotheses]
