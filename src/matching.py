from __future__ import annotations

"""Filter and rank jobs against the candidate profile.

Hard filters (a job is dropped, not just scored lower):
  * older than the freshness window
  * restricted to GCC nationals
  * above/below the target seniority
  * title matches none of the target job families
  * remote job restricted to a country the candidate cannot work from
"""

import logging
import json
from pathlib import Path
import re
from collections import defaultdict
from datetime import timedelta

from sources import Job, canonical_job_url
from utils import normalize, now_utc

log = logging.getLogger("matching")


def _has(text: str, needles) -> bool:
    return any(n in text for n in needles)


def _family_match(title_norm: str, families: dict) -> tuple[str, int, str]:
    """Return (family_key, weight, matched_title). Longest title wins."""
    best = ("", 0, "")
    for key, fam in families.items():
        for t in fam.get("titles", []):
            tn = normalize(t)
            if tn and tn in title_norm:
                if len(tn) > len(best[2]):
                    best = (key, int(fam.get("weight", 5)), tn)
    return best


def _remote_eligible(location: str, profile: dict) -> bool:
    low = (location or "").lower().strip()
    if not low:
        return True                      # unspecified -> assume open
    allowed = [a.lower() for a in profile.get("remote_allowed_regions", [])]
    blocked = [b.lower() for b in profile.get("remote_blocked_regions", [])]
    if any(a in low for a in allowed):
        return True
    if any(b in low for b in blocked):
        return False
    # A bare country list we do not recognise: keep only if it looks global.
    return len(low) > 40 or "," not in low


# ------------------------------------------------------------------
# Jobs the candidate already applied to (imported from the earlier
# GulfCareer platform, and editable by hand). Never show them again.
# ------------------------------------------------------------------
_APPLIED_CACHE: dict[str, set[str]] = {}


def load_applied(cfg: dict) -> set[str]:
    rel = cfg.get("settings", {}).get("applied_file", "data/applied_ids.json")
    if rel in _APPLIED_CACHE:
        return _APPLIED_CACHE[rel]
    ids: set[str] = set()
    path = Path(__file__).resolve().parent.parent / rel
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data if isinstance(data, list) else data.get("applied", []):
                if isinstance(item, str):
                    ids.add(item.strip().lower())
                elif isinstance(item, dict):
                    for key in ("id", "url", "linkedin_id"):
                        v = str(item.get(key, "")).strip().lower()
                        if v:
                            ids.add(v)
    except Exception:
        pass
    _APPLIED_CACHE[rel] = ids
    return ids


def load_seen(cfg: dict) -> set[str]:
    rel = cfg.get("settings", {}).get("seen_file", "data/seen_ids.json")
    key = "seen:" + rel
    if key in _APPLIED_CACHE:
        return _APPLIED_CACHE[key]
    ids: set[str] = set()
    path = Path(__file__).resolve().parent.parent / rel
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            ids = {str(x).strip().lower() for x in data}
    except Exception:
        pass
    _APPLIED_CACHE[key] = ids
    return ids


def remember(cfg: dict, jobs: list[Job]) -> int:
    """Append the IDs we actually published, so tomorrow's issue is all-new."""
    settings = cfg.get("settings", {})
    if not settings.get("repeat_suppression", True):
        return 0
    rel = settings.get("seen_file", "data/seen_ids.json")
    path = Path(__file__).resolve().parent.parent / rel
    seen = load_seen(cfg)
    for j in jobs:
        for key in (j.id, _numeric(j.url)):
            if key:
                seen.add(key.lower())
    ordered = sorted(seen)[-3000:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ordered, indent=0) + "\n", encoding="utf-8")
    except Exception:
        return 0
    _APPLIED_CACHE["seen:" + rel] = set(ordered)
    return len(ordered)


def _numeric(url: str) -> str:
    m = re.search(r"(\d{8,})", url or "")
    return m.group(1) if m else ""


def _already_applied(job: Job, applied: set[str]) -> bool:
    if not applied:
        return False
    cands = {job.id.lower(), (job.id or "").replace("li-", "").replace("li_", "").lower()}
    url = (job.url or "").lower().split("?")[0].rstrip("/")
    cands.add(url)
    m = re.search(r"(\d{8,})", url)
    if m:
        cands.add(m.group(1))
    return bool(cands & applied)


def score_job(job: Job, cfg: dict) -> Job:
    profile = cfg["profile"]
    settings = cfg["settings"]
    families = profile["families"]

    title_norm = normalize(job.title)
    desc_norm = normalize(job.description)
    combined = f"{title_norm} {desc_norm}"
    raw_title = (job.title or "").lower()
    raw_all = f"{raw_title} {(job.description or '').lower()} {(job.location or '').lower()}"

    # ---- hard filters -------------------------------------------------
    if _already_applied(job, load_applied(cfg)):
        job.score = -1
        job.reasons = ["already-applied"]
        return job

    if cfg.get("settings", {}).get("repeat_suppression", True):
        seen = load_seen(cfg)
        keys = {job.id.lower(), (job.url or "").lower().split("?")[0].rstrip("/"), _numeric(job.url)}
        if keys & seen:
            job.score = -1
            job.reasons = ["already-sent"]
            return job

    # LinkedIn's own seniority label, when we fetched the job detail page.
    # Far more reliable than guessing from the title.
    if job.seniority:
        sen = job.seniority.lower()
        if any(w in sen for w in ("director", "executive", "internship")):
            job.score = -1
            job.reasons = ["seniority:verified"]
            return job

    if job.posted:
        if job.window_hours:
            window = int(job.window_hours)
        elif job.source.startswith("موقع الشركة"):
            window = int(settings.get("company_board_hours_window", 336))
        elif job.region == "remote":
            window = int(settings.get("remote_hours_window", 168))
        else:
            window = int(settings.get("hours_window", 48))
        cutoff = now_utc() - timedelta(hours=window)
        if job.posted < cutoff:
            job.score = -1
            job.reasons = ["old"]
            return job

    nat = [n.lower() for n in profile.get("exclude_nationality", [])]
    if _has(raw_all, nat):
        job.score = -1
        job.reasons = ["nationality-restricted"]
        return job

    senior = [s.lower() for s in profile.get("exclude_seniority", [])]
    # Check the title only: a description mentioning "senior stakeholders" is fine.
    if _has(raw_title, senior):
        job.score = -1
        job.reasons = ["seniority"]
        return job

    # A role that names a working language the candidate does not have.
    langs = [l.lower() for l in profile.get("exclude_languages", [])]
    hit_lang = next((l for l in langs if re.search(rf"\b{re.escape(l)}\b", raw_title)), "")
    if hit_lang:
        job.score = -1
        job.reasons = [f"language:{hit_lang}"]
        return job

    # Linguist / transcription / language-analyst postings are almost always
    # tied to one specific language. If it is not Arabic or English, skip.
    if re.search(r"\b(linguist|transcriptionist|transcriber|language analyst|"
                 r"operational language|cryptologic language)\b", raw_title):
        if not re.search(r"\b(arabic|english|arab|bilingual)\b", raw_title):
            job.score = -1
            job.reasons = ["language:not-arabic-english"]
            return job

    # A hard language requirement buried in the description, e.g.
    # "native German speaker" / "fluency in Mandarin is required".
    if job.description:
        desc_low = job.description.lower()
        for l in langs:
            if re.search(rf"\b(native|fluent|fluency|proficien\w*|mother tongue)\b[^.]{{0,45}}\b{re.escape(l)}\b", desc_low) \
               or re.search(rf"\b{re.escape(l)}\b[^.]{{0,30}}\b(required|mandatory|essential|is a must)\b", desc_low):
                job.score = -1
                job.reasons = [f"language-desc:{l}"]
                return job

    fam_key, fam_weight, fam_title = _family_match(title_norm, families)
    if not fam_key:
        job.score = -1
        job.reasons = ["off-target"]
        return job

    if job.region == "remote" and not _remote_eligible(job.location, profile):
        job.score = -1
        job.reasons = ["remote-geo-blocked"]
        return job

    # Arbeitnow non-remote European jobs are not usable from Dubai.
    if job.region == "other":
        job.score = -1
        job.reasons = ["onsite-outside-gulf"]
        return job

    # ---- scoring ------------------------------------------------------
    score = 0.0
    reasons: list[str] = []

    score += fam_weight * 3                                   # up to 30
    reasons.append(f"family:{fam_key}")

    skills = [s.lower() for s in profile.get("skills", [])]
    matched = [s for s in skills if s in combined]
    # Skills found in the title itself are stronger signals.
    title_skills = [s for s in matched if s in title_norm]
    score += min(len(matched), 8) * 2.0                       # up to 16
    score += min(len(title_skills), 3) * 2.0                  # up to 6
    job.matched_skills = matched[:6]

    bilingual_terms = ("arabic", "bilingual", "translat", "interpret", "عربي", "ترجم")
    if _has(combined, bilingual_terms):
        score += 12
        reasons.append("bilingual")

    # Location preference
    loc_low = (job.location or "").lower()
    if job.region == "gulf":
        if any(x in loc_low for x in ("dubai", "abu dhabi", "sharjah", "ajman",
                                      "ras al", "fujairah", "umm al", "united arab emirates")):
            score += 18
            reasons.append("uae")
        elif "saudi" in loc_low or "riyadh" in loc_low or "jeddah" in loc_low:
            score += 11
            reasons.append("saudi")
        else:
            score += 9
            reasons.append("gulf")
    else:
        score += 12
        reasons.append("remote")

    if job.work_mode == "hybrid":
        score += 2

    # Title precision: a short, on-target title beats a long kitchen-sink one.
    if fam_title and len(title_norm) <= len(fam_title) + 22:
        score += 8
    elif fam_title:
        score += 4

    # Recency
    if job.posted:
        age_h = max(0.0, (now_utc() - job.posted).total_seconds() / 3600)
        score += max(0.0, 10.0 - age_h / 6.0)

    job.family = fam_key
    job.score = min(100.0, score)
    job.reasons = reasons
    return job


def dedupe(jobs: list[Job]) -> list[Job]:
    seen_url: set[str] = set()
    seen_key: dict[str, Job] = {}
    for j in sorted(jobs, key=lambda x: x.score, reverse=True):
        if j.score < 0:
            continue
        url = canonical_job_url(j.url)
        if url in seen_url:
            continue
        key = f"{normalize(j.company)[:40]}|{normalize(j.title)[:60]}"
        if key in seen_key:
            continue
        seen_url.add(url)
        seen_key[key] = j
    return list(seen_key.values())


def select(jobs: list[Job], cfg: dict) -> list[Job]:
    settings = cfg["settings"]
    min_score = float(settings.get("min_score", 30))
    max_gulf = int(settings.get("max_gulf", 12))
    max_remote = int(settings.get("max_remote", 8))
    max_total = int(settings.get("max_items", 20))
    max_company = int(settings.get("max_per_company", 2))

    pool = sorted([j for j in jobs if j.score >= min_score],
                  key=lambda x: x.score, reverse=True)
    per_company: dict[str, int] = defaultdict(int)
    chosen: list[Job] = []
    counts = {"gulf": 0, "remote": 0}

    def take(j: Job) -> bool:
        if len(chosen) >= max_total:
            return False
        cap = max_gulf if j.region == "gulf" else max_remote
        if counts.get(j.region, 0) >= cap:
            return False
        ck = normalize(j.company)[:40]
        if per_company[ck] >= max_company:
            return False
        chosen.append(j)
        per_company[ck] += 1
        counts[j.region] = counts.get(j.region, 0) + 1
        return True

    # Pass 1 — reserve seats for professional translation / interpreting so a
    # flood of administrative vacancies cannot push them out of the issue.
    reserved = int(settings.get("min_translation", 0))
    if reserved:
        picked = 0
        for j in pool:
            if picked >= reserved:
                break
            if j.family == "translation" and take(j):
                picked += 1

    # Pass 2 — fill the remaining seats by score across all families.
    for j in pool:
        if len(chosen) >= max_total:
            break
        if j in chosen:
            continue
        take(j)

    # Gulf first (the candidate lives in Dubai), then remote; score order inside.
    chosen.sort(key=lambda x: (0 if x.region == "gulf" else 1, -x.score))
    for i, j in enumerate(chosen, 1):
        j.id = f"J{i:02d}"
    return chosen


def rejection_stats(jobs: list[Job]) -> dict[str, int]:
    stats: dict[str, int] = defaultdict(int)
    for j in jobs:
        if j.score < 0:
            for r in j.reasons or ["unknown"]:
                stats[r] += 1
    return dict(stats)
