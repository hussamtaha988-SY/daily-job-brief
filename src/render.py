from __future__ import annotations
import re

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from sources import Job
from utils import local_date, local_datetime, normalize

log = logging.getLogger("render")

# Static Arabic glossary — no AI, no API key, no quota.
TITLE_AR = {
    "post-editor": "محرر لاحق للترجمة الآلية",
    "post editor": "محرر لاحق للترجمة الآلية",
    "conference interpreter": "مترجم مؤتمرات",
    "simultaneous interpreter": "مترجم فوري",
    "consecutive interpreter": "مترجم تتبعي",
    "community interpreter": "مترجم مجتمعي",
    "legal interpreter": "مترجم قانوني",
    "medical interpreter": "مترجم طبي",
    "translation project manager": "مدير مشاريع الترجمة",
    "translation manager": "مدير الترجمة",
    "translation reviewer": "مراجع ترجمة",
    "translation reviser": "منقّح ترجمة",
    "translation editor": "محرر ترجمة",
    "translation specialist": "أخصائي ترجمة",
    "translation coordinator": "منسق ترجمة",
    "localization project manager": "مدير مشاريع التعريب",
    "localization manager": "مدير التعريب",
    "localization editor": "محرر تعريب",
    "terminologist": "أخصائي مصطلحات",
    "terminology specialist": "أخصائي مصطلحات",
    "transcreator": "أخصائي صياغة إبداعية",
    "legal translator": "مترجم قانوني",
    "medical translator": "مترجم طبي",
    "technical translator": "مترجم تقني",
    "literary translator": "مترجم أدبي",
    "freelance translator": "مترجم مستقل",
    "arabic translator": "مترجم عربي",
    "subtitle editor": "محرر ترجمة مرئية",
    "captioner": "كاتب تعليقات نصية",
    "reviser": "منقّح",
    "administrative officer": "مسؤول إداري",
    "administration officer": "مسؤول إداري",
    "administrative assistant": "مساعد إداري",
    "admin assistant": "مساعد إداري",
    "administration assistant": "مساعد إداري",
    "administrative coordinator": "منسق إداري",
    "admin coordinator": "منسق إداري",
    "office administrator": "مسؤول إدارة مكتب",
    "office coordinator": "منسق مكتب",
    "office manager": "مدير مكتب",
    "executive assistant": "مساعد تنفيذي",
    "personal assistant": "مساعد شخصي",
    "executive secretary": "سكرتير تنفيذي",
    "administrative secretary": "سكرتير إداري",
    "office secretary": "سكرتير مكتب",
    "team assistant": "مساعد فريق",
    "business support officer": "مسؤول دعم الأعمال",
    "legal assistant": "مساعد قانوني",
    "legal officer": "مسؤول قانوني",
    "legal administrator": "مسؤول إدارة قانونية",
    "legal secretary": "سكرتير قانوني",
    "paralegal": "مساعد قانوني",
    "contracts administrator": "مسؤول عقود",
    "contract administrator": "مسؤول عقود",
    "contracts officer": "مسؤول عقود",
    "contracts coordinator": "منسق عقود",
    "legal translator": "مترجم قانوني",
    "document controller": "مراقب مستندات",
    "documents controller": "مراقب مستندات",
    "documentation officer": "مسؤول توثيق",
    "documentation specialist": "أخصائي توثيق",
    "records officer": "مسؤول سجلات",
    "records administrator": "مسؤول إدارة السجلات",
    "archivist": "أمين أرشيف",
    "corporate services officer": "مسؤول خدمات الشركات",
    "corporate services assistant": "مساعد خدمات الشركات",
    "business setup consultant": "مستشار تأسيس الشركات",
    "business setup officer": "مسؤول تأسيس الشركات",
    "company secretary": "أمين سر الشركة",
    "compliance assistant": "مساعد امتثال",
    "compliance officer": "مسؤول امتثال",
    "public relations officer": "مسؤول علاقات حكومية",
    "pro officer": "مسؤول علاقات حكومية",
    "government relations officer": "مسؤول علاقات حكومية",
    "government liaison officer": "مسؤول ارتباط حكومي",
    "government officer": "مسؤول معاملات حكومية",
    "translator": "مترجم",
    "translation officer": "مسؤول ترجمة",
    "translation specialist": "أخصائي ترجمة",
    "translation coordinator": "منسق ترجمة",
    "arabic translator": "مترجم عربي",
    "english translator": "مترجم إنجليزي",
    "interpreter": "مترجم فوري",
    "bilingual secretary": "سكرتير ثنائي اللغة",
    "bilingual assistant": "مساعد ثنائي اللغة",
    "proofreader": "مدقق لغوي",
    "copy editor": "محرر تدقيق",
    "content editor": "محرر محتوى",
    "editor": "محرر",
    "linguist": "أخصائي لغوي",
    "localization specialist": "أخصائي تعريب",
    "localisation specialist": "أخصائي تعريب",
    "subtitler": "مترجم ترجمة مرئية",
    "transcriptionist": "أخصائي تفريغ نصي",
    "quality control officer": "مسؤول مراقبة الجودة",
    "quality assurance officer": "مسؤول تأكيد الجودة",
    "client coordinator": "منسق عملاء",
    "client relations officer": "مسؤول علاقات العملاء",
    "client services officer": "مسؤول خدمات العملاء",
    "client support officer": "مسؤول دعم العملاء",
    "customer service officer": "مسؤول خدمة العملاء",
    "customer relations officer": "مسؤول علاقات العملاء",
    "operations coordinator": "منسق عمليات",
    "operations officer": "مسؤول عمليات",
    "project coordinator": "منسق مشاريع",
    "program coordinator": "منسق برامج",
    "programme coordinator": "منسق برامج",
    "scheduling coordinator": "منسق مواعيد",
    "service delivery coordinator": "منسق تقديم الخدمات",
    "coordinator": "منسق",
    "administrator": "مسؤول إداري",
    "assistant": "مساعد",
    "officer": "مسؤول",
    "secretary": "سكرتير",
}

MODE_AR = {"onsite": "حضوري", "hybrid": "هجين", "remote": "عن بُعد"}
REGION_AR = {"gulf": "الخليج", "remote": "عن بُعد"}
SKILL_AR = {
    "trados": "ترادوس",
    "sdl trados": "SDL Trados",
    "memoq": "memoQ",
    "phrase": "Phrase",
    "memsource": "Memsource",
    "wordfast": "Wordfast",
    "across": "Across",
    "cat tools": "أدوات الترجمة بمساعدة الحاسوب",
    "translation memory": "ذاكرة الترجمة",
    "terminology management": "إدارة المصطلحات",
    "glossary": "المسارد المصطلحية",
    "subtitling": "الترجمة المرئية",
    "post-editing": "التحرير اللاحق للترجمة الآلية",
    "mtpe": "التحرير اللاحق للترجمة الآلية",
    "localization": "التعريب والأقلمة",
    "language quality assurance": "ضبط الجودة اللغوية",
    "simultaneous interpreting": "الترجمة الفورية",
    "consecutive interpreting": "الترجمة التتبعية",
    "conference interpreting": "ترجمة المؤتمرات",
    "legal translation": "الترجمة القانونية",
    "medical translation": "الترجمة الطبية",
    "technical translation": "الترجمة التقنية",
    "literary translation": "الترجمة الأدبية",
    "transcreation": "الصياغة الإبداعية",
    "english to arabic": "من الإنجليزية إلى العربية",
    "arabic to english": "من العربية إلى الإنجليزية",
    "translation": "ترجمة", "interpreting": "ترجمة فورية", "arabic": "عربي",
    "english": "إنجليزي", "bilingual": "ثنائي اللغة", "proofreading": "تدقيق",
    "quality assurance": "تأكيد الجودة", "document control": "ضبط المستندات",
    "records management": "إدارة السجلات", "archiving": "أرشفة",
    "business correspondence": "مراسلات", "client coordination": "تنسيق عملاء",
    "calendar management": "إدارة مواعيد", "meeting scheduling": "جدولة اجتماعات",
    "ms office": "أوفيس", "excel": "إكسل", "outlook": "أوتلوك",
    "confidentiality": "سرية", "contract translation": "ترجمة عقود",
    "legal terminology": "مصطلحات قانونية", "corporate documentation": "مستندات شركات",
    "notarial": "توثيق", "moa": "عقد تأسيس", "poa": "وكالة", "noc": "عدم ممانعة",
    "memorandum of association": "عقد تأسيس", "power of attorney": "وكالة",
}


def title_arabic(title: str) -> str:
    norm = normalize(title)
    best_key = ""
    for key in TITLE_AR:
        if key in norm and len(key) > len(best_key):
            best_key = key
    return TITLE_AR.get(best_key, "")


SENIORITY_AR = {
    "internship": "تدريب",
    "entry level": "مستوى مبتدئ",
    "associate": "مشارك",
    "mid-senior level": "متوسط إلى متقدم",
    "director": "مدير",
    "executive": "تنفيذي",
}


_CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef\uac00-\ud7af]+")


def readable_company(name: str) -> str:
    """Drop Chinese/Japanese/Korean glyphs the reader cannot use; keep the rest."""
    cleaned = _CJK.sub(" ", name or "")
    return re.sub(r"\s{2,}", " ", cleaned).strip(" -|,·") or (name or "")


def build_items(jobs: list[Job], cfg: dict) -> list[dict]:
    families = cfg["profile"]["families"]
    items = []
    for j in jobs:
        fam = families.get(j.family, {})
        items.append({
            **j.public_dict(),
            "company": readable_company(j.company),
            "title_ar": title_arabic(j.title),
            "family_ar": fam.get("label_ar", ""),
            "family_en": fam.get("label_en", ""),
            "mode_ar": MODE_AR.get(j.work_mode, j.work_mode),
            "region_ar": REGION_AR.get(j.region, j.region),
            "skills_ar": [SKILL_AR.get(s, s) for s in j.matched_skills],
            "seniority_ar": SENIORITY_AR.get((j.seniority or "").lower(), j.seniority),
        })
    return items


def render_html(items: list[dict], cfg: dict, template_dir: str | Path,
                failed_sources: list[str] | None = None, stats: dict | None = None) -> str:
    env = Environment(loader=FileSystemLoader(str(template_dir)),
                      autoescape=select_autoescape(["html", "xml"]))
    template = env.get_template("issue.html")
    tz = cfg["settings"].get("timezone", "Asia/Dubai")
    gulf = [i for i in items if i["region"] == "gulf"]
    remote = [i for i in items if i["region"] == "remote"]
    return template.render(
        date=local_date(tz),
        generated=local_datetime(tz),
        items=items, gulf=gulf, remote=remote,
        count=len(items), gulf_count=len(gulf), remote_count=len(remote),
        translation_count=sum(1 for i in items if i.get("family") == "translation"),
        subject_prefix=cfg.get("email", {}).get("subject_prefix", "Daily Job Brief"),
        send_time=cfg["settings"].get("send_time_local", "05:00"),
        failed_sources=failed_sources or [],
        stats=stats or {},
        portal_links=cfg.get("portal_links", []),
    )


def render_pdf(html: str, out_pdf: str | Path) -> bool:
    try:
        from weasyprint import HTML
        HTML(string=html).write_pdf(str(out_pdf))
        return True
    except Exception as e:
        log.warning("PDF generation failed: %s", e)
        return False
