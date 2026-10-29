"""Parse the EUR-Lex HTML into retrieval chunks: Chapter IV paragraphs and points, and recitals.

EUR-Lex markup: each article is <div id="art_54">, its title is
<p class="oj-sti-art">, numbered paragraphs are <div id="053.001">, recitals are
<div id="rct_62">. Every list marker such as "(a)" or "(62)" sits in its own <p>.

Each paragraph becomes one "paragraph" chunk with its own text (the lead-in
and any closing sentence) plus one "point" chunk per point. Sub-points
("(i)", "(ii)" under a letter) stay inside their point. An article without
numbered paragraphs (Art. 54) counts as one paragraph with id "Art. 54".

Build the store with:  python -m app.rag.corpus
"""

import html
import re
from pathlib import Path

from app.config import LAW_DB_PATH, LAW_SOURCE
from app.models import LawParagraph

CHAPTER_IV = range(50, 82)  # Articles 50-81: secondary use

_ARTICLE = re.compile(r'<div class="eli-subdivision" id="art_(\d+)">')
_RECITAL = re.compile(r'<div class="eli-subdivision" id="rct_(\d+)">')
_PARAGRAPH = re.compile(r'<div id="(\d{3})\.(\d{3})">')
_TITLE = re.compile(r'<p class="oj-sti-art">(.*?)</p>', re.S)
_TEXT = re.compile(r'<p class="oj-normal">(.*?)</p>', re.S)
_NOTE = re.compile(r'<span class="oj-super oj-note-tag">.*?</span>', re.S)
_MARKER = re.compile(r"^\(([a-z]{1,4}|\d+)\)$")


def _clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", _NOTE.sub("", fragment))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _lines(block: str) -> list[tuple[str, str]]:
    """(marker, text) per paragraph line; marker is "" for plain text, else e.g. "b" or "ii"."""
    lines: list[tuple[str, str]] = []
    marker = ""
    for raw in _TEXT.findall(block):
        text = _clean(raw)
        if m := _MARKER.match(text):
            marker = m.group(1)
            continue
        lines.append((marker, text))
        marker = ""
    return lines


def _split(lines: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str]]]:
    """Paragraph text without points, and (letter, text) per point.

    A marker is a new point only if it is the next letter in sequence, so "(i)"
    after "(h)" is a point while "(i)" after "(e)" is a sub-point of (e).
    """
    own: list[str] = []
    points: list[tuple[str, list[str]]] = []
    for marker, text in lines:
        expected = chr(ord(points[-1][0]) + 1) if points else "a"
        if marker == expected:
            points.append((marker, [f"({marker}) {text}"]))
        elif marker and points:
            points[-1][1].append(f"({marker}) {text}")
        else:
            own.append(text)
    return "\n".join(own), [(letter, "\n".join(body)) for letter, body in points]


def _chunks(para_id: str, article: str, title: str, block: str) -> list[LawParagraph]:
    own, points = _split(_lines(block))
    chunks = [LawParagraph(id=para_id, article=article, article_title=title, text=own)] if own else []
    chunks += [
        LawParagraph(id=f"{para_id}({letter})", kind="point", article=article,
                     article_title=title, text=text, lead_in=own)
        for letter, text in points
    ]
    return chunks


def _articles(page: str) -> list[LawParagraph]:
    starts = [(m.start(), m.group(1)) for m in _ARTICLE.finditer(page)]
    chunks: list[LawParagraph] = []
    for (start, number), (end, _) in zip(starts, starts[1:] + [(len(page), "")]):
        if int(number) not in CHAPTER_IV:
            continue
        body = page[start:end]
        title_match = _TITLE.search(body)
        title = _clean(title_match.group(1)) if title_match else ""

        numbered = list(_PARAGRAPH.finditer(body))
        if not numbered:
            text_start = title_match.end() if title_match else 0
            chunks += _chunks(f"Art. {number}", number, title, body[text_start:])
            continue
        bounds = [m.start() for m in numbered] + [len(body)]
        for m, para_end in zip(numbered, bounds[1:]):
            chunks += _chunks(f"Art. {number}({int(m.group(2))})", number, title, body[m.end():para_end])
    return chunks


def _recitals(page: str) -> list[LawParagraph]:
    starts = [(m.start(), m.group(1)) for m in _RECITAL.finditer(page)]
    first_article = _ARTICLE.search(page)
    end_of_recitals = first_article.start() if first_article else len(page)
    chunks = []
    for (start, number), (end, _) in zip(starts, starts[1:] + [(end_of_recitals, "")]):
        text = " ".join(text for _, text in _lines(page[start:end]))
        chunks.append(LawParagraph(id=f"Recital {number}", kind="recital", article="", text=text))
    return chunks


def parse(source: Path = LAW_SOURCE) -> list[LawParagraph]:
    page = source.read_text(encoding="utf-8")
    return _articles(page) + _recitals(page)


if __name__ == "__main__":
    from app.rag.ollama import OllamaEmbedder
    from app.rag.store import save

    corpus = parse()
    save(corpus, OllamaEmbedder())
    print(f"Embedded {len(corpus)} chunks into {LAW_DB_PATH}")
