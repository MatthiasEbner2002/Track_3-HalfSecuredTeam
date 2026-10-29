import pytest

from app.config import LAW_SOURCE
from app.rag.corpus import parse

pytestmark = pytest.mark.skipif(not LAW_SOURCE.exists(), reason=f"{LAW_SOURCE} not saved")


@pytest.fixture(scope="module")
def corpus():
    return {c.id: c for c in parse()}


def test_covers_all_of_chapter_iv(corpus):
    articles = {int(c.article) for c in corpus.values() if c.kind != "recital"}
    assert articles == set(range(50, 82))


def test_no_empty_chunks(corpus):
    assert all(c.text for c in corpus.values())


def test_paragraph_keeps_own_text_and_points_get_chunks(corpus):
    assert corpus["Art. 53(1)"].text.startswith("1. Health data access bodies shall only grant access")
    assert "(a)" not in corpus["Art. 53(1)"].text
    point = corpus["Art. 53(1)(d)"]
    assert point.kind == "point"
    assert point.text.startswith("(d) education or teaching activities")
    assert point.lead_in == corpus["Art. 53(1)"].text


def test_unnumbered_article_points(corpus):
    assert [i for i in corpus if i.startswith("Art. 54(")] == [f"Art. 54({x})" for x in "abcde"]
    assert corpus["Art. 54(c)"].text == "(c) carrying out advertising or marketing activities;"


def test_roman_sub_points_stay_in_their_point(corpus):
    assert "\n(ii) training, testing and evaluation of algorithms" in corpus["Art. 53(1)(e)"].text
    assert "Art. 53(1)(i)" not in corpus


def test_letter_i_after_h_is_a_point(corpus):
    assert corpus["Art. 51(1)(i)"].text.startswith("(i) data from wellness applications")


def test_recitals(corpus):
    recitals = [c for c in corpus.values() if c.kind == "recital"]
    assert len(recitals) == 115
    assert corpus["Recital 62"].text.startswith("Any attempt to use electronic health data")
