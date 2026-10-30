"""Build the 2-minute pitch deck (docs/slides/pitch.pptx) from the README images.

    /tmp/pptx-venv/bin/python docs/slides/build_slides.py     # needs python-pptx, pillow

Import into Google Slides: File -> Import slides -> Upload -> pitch.pptx.
Speaker notes hold the spoken script; roughly 60 s of slides, then the live demo.
"""

from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Emu, Inches, Pt

DOCS = Path(__file__).resolve().parents[1]
OUT = DOCS / "slides" / "pitch.pptx"

NAVY = RGBColor(0x1F, 0x3A, 0x5F)
INK = RGBColor(0x1F, 0x2A, 0x37)
GREY = RGBColor(0x5B, 0x64, 0x72)
LIGHT = RGBColor(0xF6, 0xF7, 0xF9)
RED = RGBColor(0xC6, 0x28, 0x28)
GREEN = RGBColor(0x2E, 0x7D, 0x32)
BLUE = RGBColor(0x15, 0x65, 0xC0)
AMBER = RGBColor(0xF0, 0xB4, 0x29)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
W, H = prs.slide_width, prs.slide_height
BLANK = prs.slide_layouts[6]


def text(slide, x, y, w, h, s, size=18, bold=False, color=INK, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font="Inter"):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.05)
    lines = s if isinstance(s, list) else [s]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        r = p.add_run()
        r.text = line
        r.font.size, r.font.bold, r.font.color.rgb, r.font.name = Pt(size), bold, color, font
    return box


def bullets(slide, x, y, w, h, items, size=18, color=INK, gap=8):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        head, _, rest = item.partition("|")
        r = p.add_run(); r.text = "•  " + head; r.font.size = Pt(size); r.font.bold = True; r.font.color.rgb = color; r.font.name = "Inter"
        if rest:
            r2 = p.add_run(); r2.text = " " + rest.strip(); r2.font.size = Pt(size); r2.font.color.rgb = GREY; r2.font.name = "Inter"
    return box


def card(slide, x, y, w, h, fill, line=None, radius=True):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE, x, y, w, h)
    shp.fill.solid(); shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line; shp.line.width = Pt(1.5)
    if radius:
        shp.adjustments[0] = 0.08
    shp.shadow.inherit = False
    return shp


def picture(slide, path, x, y, w=None, h=None):
    """Place an image scaled to fit the given box, keeping its aspect ratio."""
    iw, ih = Image.open(path).size
    if w is not None and h is not None:
        scale = min(w / iw, h / ih)
        pw, ph = int(iw * scale), int(ih * scale)
        return slide.shapes.add_picture(str(path), x + (w - pw) // 2, y + (h - ph) // 2, pw, ph)
    return slide.shapes.add_picture(str(path), x, y, width=w, height=h)


def header(slide, title, kicker=None):
    card(slide, 0, 0, W, Inches(1.15), NAVY, radius=False)
    text(slide, Inches(0.6), Inches(0.2), Inches(9.2), Inches(0.6), title, 26, True, WHITE)
    if kicker:
        text(slide, Inches(0.6), Inches(0.72), Inches(9.2), Inches(0.4), kicker, 13, False, RGBColor(0xDB, 0xE6, 0xF3))
    text(slide, Inches(9.3), Inches(0.35), Inches(3.5), Inches(0.5), "HalfSecuredTeam · Track 3", 12, False, RGBColor(0xDB, 0xE6, 0xF3), PP_ALIGN.RIGHT)


def footer(slide):
    text(slide, Inches(0.6), H - Inches(0.45), Inches(12), Inches(0.3),
         "EHDS · Regulation (EU) 2025/327 · synthetic data only", 10, False, GREY)


def notes(slide, s):
    slide.notes_slide.notes_text_frame.text = s


# ---------------------------------------------------------------- 1 title
s = prs.slides.add_slide(BLANK)
card(s, 0, 0, W, H, NAVY, radius=False)
text(s, Inches(0.9), Inches(1.9), Inches(11.5), Inches(1.6), "Health data access that explains itself", 40, True, WHITE)
text(s, Inches(0.9), Inches(3.6), Inches(11.5), Inches(1.0),
     "An automated Health Data Access Body for the European Health Data Space", 22, False, RGBColor(0xDB, 0xE6, 0xF3))
text(s, Inches(0.9), Inches(5.6), Inches(11.5), Inches(0.5), "HalfSecuredTeam · Track 3 · Regulation (EU) 2025/327", 16, False, RGBColor(0xDB, 0xE6, 0xF3))
notes(s, "Hi everyone. We are HalfSecuredTeam and we built an automated Health Data Access Body for the "
         "European Health Data Space: an app that decides health-data requests the way the law requires, "
         "and shows exactly why. (≈10 s)")

# ---------------------------------------------------------------- 2 problem
s = prs.slides.add_slide(BLANK)
header(s, "The problem: slow access, two kinds of people", "researchers, engineers and the access body")
cw, ch, top = Inches(3.8), Inches(3.6), Inches(1.6)
for i, (title, body, col) in enumerate([
    ("Researchers", ["know the medicine and the question", "do not write database queries", "often only know what they want to achieve"], BLUE),
    ("Engineers", ["write the queries", "do not know ICD-10 from ATC", "cannot judge if a purpose is lawful"], AMBER),
    ("The access body", ["must decide every request against the law", "must protect patients: small groups, opt-outs", "today: weeks of manual work"], RED),
]):
    x = Inches(0.6) + i * (cw + Inches(0.35))
    card(s, x, top, cw, ch, LIGHT, col)
    text(s, x + Inches(0.25), top + Inches(0.2), cw - Inches(0.5), Inches(0.5), title, 20, True, col)
    bullets(s, x + Inches(0.25), top + Inches(0.8), cw - Inches(0.5), ch - Inches(1), body, 16, INK, 6)
card(s, Inches(0.6), Inches(5.55), Inches(12.1), Inches(1.1), NAVY)
text(s, Inches(0.9), Inches(5.6), Inches(11.5), Inches(1.0),
     "We sit at the intersection: plain words in, a lawful, explained, privacy-safe answer out.",
     20, True, WHITE, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
footer(s)
notes(s, "Researchers know the medicine and what they want to find out, but not how to query a database. "
         "Engineers can write the query but do not know the medical codes or whether the purpose is even "
         "allowed. And the access body in between has to check every request against the regulation and "
         "protect patients. Today that takes weeks. We sit at the intersection of all three. (≈20 s)")

# ---------------------------------------------------------------- 2b what we did about it
s = prs.slides.add_slide(BLANK)
header(s, "What we did about it", "the same three people, one app")
cw, ch, top = Inches(3.8), Inches(3.9), Inches(1.5)
for i, (title, body, col) in enumerate([
    ("Researchers", ["write the request in plain words, in any EU language",
                     "one click: the AI drafts the definitions they could not phrase",
                     "the answer comes back in seconds, with the reason"], BLUE),
    ("Engineers", ["no one writes a query per request",
                   "fixed rules turn the definitions into SQL; codes come from the request",
                   "an LLM steps in only where the rules give up, under the same checks"], AMBER),
    ("The access body", ["the law check quotes the article, verbatim from the regulation",
                         "small-cell rule and opt-outs enforced inside the query",
                         "records only in a secure room: images, no download, audit log"], RED),
]):
    x = Inches(0.6) + i * (cw + Inches(0.35))
    card(s, x, top, cw, ch, LIGHT, col)
    text(s, x + Inches(0.25), top + Inches(0.2), cw - Inches(0.5), Inches(0.5), title, 20, True, col)
    bullets(s, x + Inches(0.25), top + Inches(0.8), cw - Inches(0.5), ch - Inches(1), body, 15, INK, 8)
card(s, Inches(0.6), Inches(5.7), Inches(12.1), Inches(1.0), NAVY)
text(s, Inches(0.9), Inches(5.7), Inches(11.5), Inches(1.0),
     "Decisions in seconds instead of weeks — every one of them explained, and identical on every run.",
     18, True, WHITE, PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
footer(s)
notes(s, "So what did we do about it? Researchers write the request in plain words, in any EU language, and "
         "if they cannot phrase the definitions, one click drafts them. Engineers are out of the loop: fixed "
         "rules turn the definitions into SQL, and an AI only steps in where the rules give up, under the "
         "same checks. And the access body gets a decision that quotes the article from the regulation, with "
         "the privacy rules enforced inside the query and records locked in a secure room. Seconds instead "
         "of weeks, and the same answer every time. (≈25 s)")

# ---------------------------------------------------------------- 3 how it works
s = prs.slides.add_slide(BLANK)
header(s, "What we built: one pipeline, three outcomes", "request → law check → statistics or secure room; attackers blocked")
picture(s, DOCS / "how-it-works.png", Inches(0.4), Inches(1.3), Inches(12.5), Inches(5.6))
footer(s)
notes(s, "A request in any EU language is translated once. An AI picks the matching rule from the actual law "
         "text and quotes it. Prohibited purposes are rejected with the quote. Approved requests are parsed "
         "by rules into SQL; statistics go through the small-cell rule, record requests are copied into a "
         "secure room with no download. Anyone flooding the service is blocked. (≈20 s)")

# ---------------------------------------------------------------- 4 why better
s = prs.slides.add_slide(BLANK)
header(s, "Why this is better than a black box", "grounded, fast, consistent, safe")
bullets(s, Inches(0.6), Inches(1.5), Inches(6.3), Inches(5), [
    "No technical burden.| Plain words in; the AI drafts the definitions the access body can compute.",
    "Explainable, not a verdict.| Every decision quotes the article, verbatim from the law text.",
    "Numbers from code, not from the AI.| Same answer on every run; 10/10 consistent across 3 runs.",
    "Privacy enforced in the query.| <10, suppressed, opt-outs out — no answer can bypass it.",
    "Any EU language, one rulebook.| German, Swedish, Greek → English once, then the same checks.",
    "Audit and abuse control.| Every SPE action logged; flooding addresses blocked (429).",
], 17, INK, 10)
picture(s, DOCS / "ui-reject.png", Inches(7.2), Inches(1.4), Inches(5.7), Inches(5.4))
footer(s)
notes(s, "Why is this better than a black box? There is no technical burden: the applicant writes in plain "
         "words and the AI even drafts the definitions. Every decision quotes the article verbatim, so it is "
         "explainable. The numbers come from code, not from the AI, so they are the same every run. Privacy "
         "rules are enforced inside the query, and everything is logged. (≈20 s)")

# ---------------------------------------------------------------- 5 demo
s = prs.slides.add_slide(BLANK)
card(s, 0, 0, W, H, NAVY, radius=False)
text(s, Inches(0.9), Inches(1.2), Inches(11.5), Inches(1.0), "Demo", 44, True, WHITE)
steps = [
    ("R02", "Insurer wants to price premiums", "Rejected — Art. 54(b) quoted", RED),
    ("R01", "Diabetes prevalence by region", "Statistics, small cells hidden", GREEN),
    ("R11", "Same, written in German", "Translated once, same rulebook", AMBER),
    ("R12", "Heart-failure records for a model", "Secure room, download → 403", BLUE),
    ("flood", "30 requests in 0.15 s", "Blocked from #21 — 429", RED),
]
cw = Inches(2.3)
for i, (tag, what, result, col) in enumerate(steps):
    x = Inches(0.9) + i * (cw + Inches(0.15))
    card(s, x, Inches(2.6), cw, Inches(3.2), WHITE)
    card(s, x, Inches(2.6), cw, Inches(0.7), col)
    text(s, x, Inches(2.6), cw, Inches(0.7), tag, 20, True, WHITE, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    text(s, x + Inches(0.15), Inches(3.45), cw - Inches(0.3), Inches(1.2), what, 15, False, INK, PP_ALIGN.CENTER)
    text(s, x + Inches(0.15), Inches(4.6), cw - Inches(0.3), Inches(1.1), result, 15, True, col, PP_ALIGN.CENTER)
text(s, Inches(0.9), Inches(6.2), Inches(11.5), Inches(0.5), "Live, on 20,000 synthetic patients. Switch to the screen capture now.", 16, False, RGBColor(0xDB, 0xE6, 0xF3))
notes(s, "Now the demo, in four steps and one attack. (switch to screen capture; ≈50 s)\n"
         "R02: the insurer. Submit. Rejected, Art. 54(b), quoted.\n"
         "R01: diabetes by region. Submit. The table, with the small-cell note.\n"
         "R11: the same request in German. The decision shows the English text next to it.\n"
         "R12: records for a prediction model. Approved into the secure room; open it, browse; "
         "click Download: 403, logged.\n"
         "Finally run flood.py in a terminal: 20 served, then 429 with Retry-After.")

# ---------------------------------------------------------------- 6 what works / what does not
s = prs.slides.add_slide(BLANK)
header(s, "What works — and what does not yet", "we say both, as asked")
card(s, Inches(0.6), Inches(1.5), Inches(5.9), Inches(4.9), LIGHT, GREEN)
text(s, Inches(0.85), Inches(1.65), Inches(5.5), Inches(0.5), "Works", 20, True, GREEN)
bullets(s, Inches(0.85), Inches(2.2), Inches(5.5), Inches(4), [
    "10 / 10 decisions and articles| on the task requests",
    "6 / 6 numbers, 0 privacy leaks| small-cell rule, opt-outs",
    "10 / 10 consistent| across 3 runs, slowest answer 5.7 s",
    "34 / 36 on unseen requests| incl. 6 in German; every prohibited purpose caught",
    "Secure room| only named records, images only, download 403 + audit log",
], 15, INK, 6)
card(s, Inches(6.85), Inches(1.5), Inches(5.9), Inches(4.9), LIGHT, RED)
text(s, Inches(7.1), Inches(1.65), Inches(5.5), Inches(0.5), "Not yet", 20, True, RED)
bullets(s, Inches(7.1), Inches(2.2), Inches(5.5), Inches(4), [
    "Sometimes too strict:| a study about insurance premiums was rejected for mentioning them",
    "Point within Art. 53 can vary| between runs; decision and articles do not",
    "Staff submission list| is a placeholder, lost on restart",
    "Screens can be photographed| — true of every SPE",
], 15, INK, 6)
text(s, Inches(0.6), Inches(6.55), Inches(12), Inches(0.4),
     "github.com/MatthiasEbner2002/Track_3-HalfSecuredTeam", 14, True, NAVY)
footer(s)
notes(s, "What works: all ten task decisions, all six numbers, no leaks, consistent across runs, and 34 of 36 "
         "requests we had never seen. What does not yet: the law check is sometimes too strict, and the staff "
         "list is still a placeholder. Everything is in the repository. Thank you. (≈15 s)")

OUT.parent.mkdir(exist_ok=True)
prs.save(OUT)
print(f"wrote {OUT} ({len(prs.slides)} slides)")
