"""
Per-vacancy application pack: English letter, Arabic letter, a short LinkedIn
note to the recruiter, and a factual ATS keyword check.

Adapted from the user's earlier GulfCareer platform. Two deliberate changes:

  1. Nothing is invented. The old version suggested bullets such as
     "exceed quarterly KPIs by 25%+" — a claim the candidate never made.
     Here every sentence comes either from the configured CV evidence or
     from words that actually appear in the vacancy.
  2. Letters are family-aware, so a translator vacancy gets translation
     evidence, not administrative evidence.

Zero cost: pure templates, no AI call, no API key.
Contact details are read from environment variables (GitHub Secrets), never
from the public repository.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

log = logging.getLogger("cover_letters")

# Words too common to tell us anything about a vacancy.
STOP = {
    "the", "and", "for", "with", "you", "your", "our", "are", "will", "have",
    "has", "this", "that", "from", "their", "they", "who", "all", "any",
    "can", "able", "work", "working", "team", "role", "job", "candidate",
    "candidates", "experience", "years", "strong", "good", "excellent",
    "skills", "skill", "ability", "including", "etc", "must", "should",
    "would", "within", "across", "other", "related", "required", "requirements",
    "responsibilities", "qualifications", "preferred", "plus", "well", "new",
    "using", "use", "ensure", "support", "provide", "manage", "management",
    "duties", "description", "company", "position", "apply", "please",
}


def _contact() -> tuple[str, str, str]:
    name = os.getenv("CV_NAME", "").strip() or "[Your name]"
    phone = os.getenv("CV_PHONE", "").strip() or "[Your phone]"
    email = (os.getenv("DIGEST_TO", "") or os.getenv("GMAIL_USER", "")).strip()
    email = email.split(",")[0].split(";")[0].strip() or "[Your email]"
    return name, phone, email


def _keywords(text: str) -> list[str]:
    """Content words of a vacancy, longest first, deduplicated."""
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", (text or "").lower())
    seen: dict[str, None] = {}
    for w in words:
        if w not in STOP:
            seen.setdefault(w, None)
    return list(seen)


# Skill names that are also ordinary English words. Counting them as a match
# would be a false positive ("across the region" is not the CAT tool Across).
AMBIGUOUS = {"across", "phrase", "word", "powerpoint", "sdl"}


def _present(term: str, desc_raw: str, desc_low: str) -> bool:
    """Word-boundary match; ambiguous tool names must appear capitalised."""
    if term.lower() in AMBIGUOUS:
        return re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", desc_raw) is not None
    return re.search(rf"(?<![A-Za-z]){re.escape(term.lower())}(?![A-Za-z])", desc_low) is not None


def ats_check(item: dict, cfg: dict) -> dict:
    """Compare the advert against a curated professional vocabulary.

    Factual only: a term is 'matched' when it appears in both the advert and the
    candidate's skill list. Nothing is scored and no achievement is assumed.
    """
    desc_raw = item.get("description") or ""
    if not desc_raw:
        return {"matched": [], "missing": [],
                "note": "لم يُقرأ وصف هذه الوظيفة، فلا تحليل كلمات مفتاحية لها."}
    desc_low = desc_raw.lower()
    skills = [s.lower() for s in cfg["profile"].get("skills", [])]
    vocab = cfg.get("cover_letter", {}).get("ats_vocabulary", [])

    in_advert = [v for v in vocab if _present(v, desc_raw, desc_low)]
    matched, missing = [], []
    for v in in_advert:
        vl = v.lower()
        if any(vl == s or vl in s or s in vl for s in skills):
            matched.append(v)
        else:
            missing.append(v)
    note = ("هذه كلمات مهنية وردت في الإعلان وليست في قائمة مهاراتك. "
            "أضفها إلى سيرتك فقط إن كانت تنطبق عليك فعلًا: " + "، ".join(missing[:8])) \
        if missing else "كل المصطلحات المهنية الواردة في الإعلان موجودة في مهاراتك."
    return {"matched": matched[:12], "missing": missing[:8], "note": note}


def _evidence(item: dict, cfg: dict) -> list[str]:
    """Pick the CV evidence lines for this job's family, best match first."""
    cl = cfg.get("cover_letter", {})
    bank = cl.get("evidence", {})
    lines = list(bank.get(item.get("family", ""), [])) or list(bank.get("admin", []))
    desc = (item.get("description") or "").lower()
    if desc:
        lines.sort(key=lambda s: sum(1 for w in _keywords(s) if w in desc), reverse=True)
    return lines[:3]


def letter_en(item: dict, cfg: dict) -> str:
    cl = cfg.get("cover_letter", {})
    name, phone, email = _contact()
    title = item.get("title", "the advertised role")
    company = item.get("company", "your organisation")
    location = item.get("location", "")
    years = cl.get("years_experience", cfg["profile"].get("years_experience", 6))
    evidence = _evidence(item, cfg)
    matched = item.get("matched_skills") or []
    degrees = cl.get("degrees", "")

    where = f" in {location}" if location else ""
    opening = (f"I am applying for the {title} position at {company}{where}. "
               f"The role stood out because it calls for the combination I have "
               f"built my career on: careful handling of formal information and "
               f"clear communication in both Arabic and English.")

    body = f"My background covers {years} years in this field."
    if evidence:
        body += " My work has involved " + "; ".join(e.rstrip(".") for e in evidence) + "."
    if degrees:
        body += f" {degrees} underpin the accuracy this kind of work demands."

    if matched:
        fit = ("Reading your requirements, the closest points of overlap are "
               + ", ".join(matched[:5]) + ".")
    else:
        fit = ("I would bring a methodical approach, discretion with confidential "
               "material, and dependable follow-through.")

    closing = (f"I would welcome a conversation about how this experience can "
               f"support {company}. My CV is attached, and I am available for an "
               f"interview at your convenience.")

    return (f"Dear Hiring Team at {company},\n\n{opening}\n\n{body}\n\n{fit}\n\n"
            f"{closing}\n\nKind regards,\n{name}\n{phone} | {email}")


def letter_ar(item: dict, cfg: dict) -> str:
    cl = cfg.get("cover_letter", {})
    name, phone, email = _contact()
    title = item.get("title_ar") or item.get("title", "الوظيفة المعلنة")
    company = item.get("company", "مؤسستكم")
    location = item.get("location", "")
    years = cl.get("years_experience", cfg["profile"].get("years_experience", 6))
    evidence_ar = cl.get("evidence_ar", {}).get(item.get("family", ""), [])
    matched = item.get("skills_ar") or item.get("matched_skills") or []

    where = f" في {location}" if location else ""
    lines = [
        f"السادة فريق التوظيف في {company} المحترمين،",
        "تحية طيبة وبعد،",
        f"أتقدم بطلبي لوظيفة ({title}){where}. ما جذبني إلى هذا الشاغر أنه يجمع بين "
        f"الدقة في التعامل مع المعلومات الرسمية والتواصل الواضح بالعربية والإنجليزية، "
        f"وهو جوهر خبرتي المهنية.",
    ]
    para = f"تمتد خبرتي إلى {years} سنوات في هذا المجال."
    if evidence_ar:
        para += " وقد شملت مهامي " + "؛ و".join(e.rstrip("؛.") for e in evidence_ar[:3]) + "."
    lines.append(para)
    if matched:
        lines.append("وبمقارنة متطلباتكم بخبرتي، فإن أقرب نقاط التقاطع هي: "
                     + "، ".join(matched[:5]) + ".")
    lines += [
        f"يسعدني أن أناقش معكم كيف يمكن لهذه الخبرة أن تخدم أعمال {company}. "
        f"سيرتي الذاتية مرفقة، وأنا جاهز للمقابلة في الوقت الذي يناسبكم.",
        "وتفضلوا بقبول فائق الاحترام،",
        name,
        f"{phone} | {email}",
    ]
    return "\n\n".join(lines)


def linkedin_note(item: dict, cfg: dict) -> str:
    name, phone, _ = _contact()
    title = item.get("title", "the role")
    company = item.get("company", "your team")
    matched = (item.get("matched_skills") or [])[:3]
    hook = f" My background includes {', '.join(matched)}." if matched else ""
    return (f"Hello,\n\nI have just applied for the {title} role at {company} and "
            f"wanted to introduce myself briefly.{hook}\n\nIf you are the right "
            f"person for this vacancy, I would be grateful for a quick look at my "
            f"profile. If not, could you point me to whoever handles it?\n\n"
            f"Thank you for your time,\n{name}")


def _rtl(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    pPr = paragraph._p.get_or_add_pPr()
    bidi = pPr.makeelement(qn("w:bidi"), {})
    pPr.append(bidi)
    for run in paragraph.runs:
        rPr = run._r.get_or_add_rPr()
        rtl = rPr.makeelement(qn("w:rtl"), {})
        rPr.append(rtl)
        sz = rPr.makeelement(qn("w:szCs"), {qn("w:val"): "24"})
        rPr.append(sz)


def build_pack(items: list[dict], cfg: dict, out_path: str | Path, date_str: str) -> Path | None:
    """One editable .docx holding a full application pack per vacancy."""
    cl = cfg.get("cover_letter", {})
    if not cl.get("enabled", True) or not items:
        return None
    langs = cl.get("languages", ["en", "ar"])
    limit = int(cl.get("max_letters", 20))

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    doc.add_heading(f"Application Pack — {date_str}", level=0)
    intro = doc.add_paragraph(
        f"{len(items[:limit])} vacancy. Each entry contains an English letter, an Arabic "
        f"letter, a short LinkedIn message, and an ATS keyword check. "
        f"Everything is grounded in your CV — edit freely before sending."
    )
    intro.runs[0].font.size = Pt(9)

    for n, item in enumerate(items[:limit], 1):
        doc.add_page_break()
        doc.add_heading(f"{n}. {item.get('title','')} — {item.get('company','')}", level=1)
        meta = doc.add_paragraph(
            f"{item.get('location','')} · match {item.get('score',0)}% · {item.get('url','')}"
        )
        meta.runs[0].font.size = Pt(9)

        if "en" in langs:
            doc.add_heading("Cover letter — English", level=2)
            for block in letter_en(item, cfg).split("\n\n"):
                doc.add_paragraph(block)

        if "ar" in langs:
            doc.add_heading("خطاب التقديم — عربي", level=2)
            for block in letter_ar(item, cfg).split("\n\n"):
                _rtl(doc.add_paragraph(block))

        if cl.get("linkedin_message", True):
            doc.add_heading("LinkedIn message to the recruiter", level=2)
            for block in linkedin_note(item, cfg).split("\n\n"):
                doc.add_paragraph(block)

        if cl.get("ats_keywords", True):
            doc.add_heading("ATS keyword check", level=2)
            chk = ats_check(item, cfg)
            if chk["matched"]:
                doc.add_paragraph("Present in your CV: " + ", ".join(chk["matched"]))
            if chk["missing"]:
                doc.add_paragraph("Terms in the advert not in your skill list: "
                                  + ", ".join(chk["missing"]))
            _rtl(doc.add_paragraph(chk["note"]))

    out_path = Path(out_path)
    doc.save(str(out_path))
    log.info("Application pack saved: %s (%d vacancies)", out_path, len(items[:limit]))
    return out_path
