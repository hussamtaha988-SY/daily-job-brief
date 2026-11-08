from __future__ import annotations

"""Job sources. All free, no API keys.

Gulf onsite/hybrid comes from LinkedIn's public (guest) job search.
Remote worldwide comes from public job-board APIs and RSS feeds.
If one source fails the run continues with the others.
"""

import concurrent.futures as cf
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse, urlunparse

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from utils import clean_html, truncate

log = logging.getLogger("sources")

LINKEDIN_SEARCH = ("https://www.linkedin.com/jobs-guest/jobs/api/"
                   "seeMoreJobPostings/search")


@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str
    work_mode: str = "onsite"        # onsite | hybrid | remote
    region: str = "gulf"             # gulf | remote
    posted: datetime | None = None
    description: str = ""
    salary: str = ""
    country: str = ""
    id: str = ""
    score: float = 0.0
    family: str = ""
    matched_skills: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    seniority: str = ""            # LinkedIn "Seniority level" (verified)
    employment_type: str = ""      # Full-time / Contract / Part-time
    window_hours: int = 0          # per-search freshness override (0 = use defaults)


    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "work_mode": self.work_mode,
            "region": self.region,
            "source": self.source,
            "url": self.url,
            "posted": self.posted.strftime("%Y-%m-%d") if self.posted else "",
            "salary": self.salary,
            "seniority": self.seniority,
            "employment_type": self.employment_type,
            "score": round(self.score),
            "family": self.family,
            "matched_skills": self.matched_skills,
            "description": self.description[:4000],
            "reasons": self.reasons,
        }


def canonical_job_url(url: str) -> str:
    try:
        p = urlparse(url)
        path = p.path.rstrip("/")
        return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", "", ""))
    except Exception:
        return url


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = dtparser.parse(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _session(settings: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": settings.get("user_agent", "Mozilla/5.0")})
    return s


# ------------------------------------------------------------------
# LinkedIn public search — the only source that covers Gulf onsite jobs
# ------------------------------------------------------------------
def fetch_linkedin(keyword: str, settings: dict, location: str = "",
                   remote: bool = False, start: int = 0,
                   tpr: str = "r86400", window_hours: int = 0) -> list[Job]:
    params = {
        "keywords": keyword,
        "f_TPR": tpr,
        "start": str(start),
    }
    if remote:
        params["f_WT"] = "2"        # remote filter
        if location:
            params["location"] = location
    elif location:
        params["location"] = location
        params["f_WT"] = "1,3"      # onsite + hybrid
    qs = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
    url = f"{LINKEDIN_SEARCH}?{qs}"
    sess = _session(settings)
    retries = int(settings.get("linkedin_retries", 2))
    html = ""
    for attempt in range(retries + 1):
        try:
            resp = sess.get(url, timeout=settings.get("request_timeout", 25))
        except Exception as e:
            log.warning("LinkedIn failed for %r / %s: %s", keyword, location or "remote", e)
            return []
        if resp.status_code == 200:
            html = resp.text
            break
        if resp.status_code == 429 and attempt < retries:
            wait = 6.0 * (attempt + 1) ** 2      # 6s, 24s
            log.info("LinkedIn rate-limited, waiting %.0fs (%d/%d)", wait, attempt + 1, retries)
            time.sleep(wait)
            continue
        log.warning("LinkedIn HTTP %s for %r / %s", resp.status_code, keyword, location or "remote")
        return []
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".job-search-card") or soup.select("div.base-card")
    jobs: list[Job] = []
    for c in cards:
        a = c.select_one("a.base-card__full-link") or c.select_one("a[href*='/jobs/view/']")
        t = c.select_one(".base-search-card__title") or c.select_one(".sr-only")
        if not a or not t:
            continue
        title = t.get_text(" ", strip=True)
        comp_el = c.select_one(".base-search-card__subtitle")
        loc_el = c.select_one(".job-search-card__location")
        time_el = c.select_one("time")
        company = comp_el.get_text(" ", strip=True) if comp_el else ""
        # Some employers post "Company | Role, City" as the title.
        if " | " in title:
            head, _, tail = title.partition(" | ")
            if company and head.strip().lower() in company.strip().lower() and len(tail) > 8:
                title = tail.strip()
        loc = loc_el.get_text(" ", strip=True) if loc_el else (location or "")
        link = canonical_job_url(a.get("href", ""))
        urn = c.get("data-entity-urn", "") or ""
        jid = urn.rsplit(":", 1)[-1] if urn else link.rsplit("-", 1)[-1]
        posted = _parse_date(time_el.get("datetime") if time_el else None)
        low = f"{title} {loc}".lower()
        mode = "remote" if (remote or "remote" in low) else ("hybrid" if "hybrid" in low else "onsite")
        jobs.append(Job(
            title=title, company=company, location=loc, url=link,
            source="LinkedIn", work_mode=mode,
            region="remote" if remote else "gulf",
            posted=posted, id=f"li-{jid}", country=location,
            window_hours=window_hours,
        ))
    return jobs


def fetch_linkedin_all(cfg: dict) -> list[Job]:
    settings = cfg["settings"]
    src = cfg["sources"].get("linkedin", {})
    if not src.get("enabled", True):
        return []
    pause = float(settings.get("linkedin_pause", 2.5))
    out: list[Job] = []
    calls = 0

    if src.get("gulf", True):
        query = cfg.get("gulf_query", "administrative OR translator")
        tpr = cfg.get("gulf_tpr", "r86400")
        for loc in cfg.get("gulf_locations", []):
            for page in range(int(loc.get("pages", 1))):
                jobs = fetch_linkedin(query, settings, location=loc["name"],
                                      start=page * 25, tpr=tpr)
                out.extend(jobs)
                calls += 1
                log.info("LinkedIn %s page %d -> %d", loc["name"], page + 1, len(jobs))
                time.sleep(pause)

    if src.get("remote", True):
        query = cfg.get("remote_query", "translator OR translation")
        tpr = cfg.get("remote_tpr", "r604800")
        for loc in cfg.get("remote_locations", ["Worldwide"]):
            jobs = fetch_linkedin(query, settings, location=loc, remote=True, tpr=tpr)
            out.extend(jobs)
            calls += 1
            log.info("LinkedIn remote %s -> %d", loc, len(jobs))
            time.sleep(pause)

    # Dedicated professional-translation sweep. Separate query so translator
    # and interpreter roles are not crowded out by administrative postings.
    if src.get("translation", True) and cfg.get("translation_query"):
        tq = cfg["translation_query"]
        ttpr = cfg.get("translation_tpr", "r604800")
        twin = int(settings.get("translation_hours_window", 168))
        for loc in cfg.get("translation_gulf_locations", []):
            jobs = fetch_linkedin(tq, settings, location=loc, tpr=ttpr, window_hours=twin)
            out.extend(jobs)
            calls += 1
            log.info("LinkedIn translation %s -> %d", loc, len(jobs))
            time.sleep(pause)
        for loc in cfg.get("translation_remote_locations", []):
            jobs = fetch_linkedin(tq, settings, location=loc, remote=True, tpr=ttpr,
                                  window_hours=twin)
            out.extend(jobs)
            calls += 1
            log.info("LinkedIn translation remote %s -> %d", loc, len(jobs))
            time.sleep(pause)

    log.info("LinkedIn total: %d raw jobs from %d calls", len(out), calls)
    return out


# ------------------------------------------------------------------
# Remote job APIs
# ------------------------------------------------------------------
def _get_json(url: str, settings: dict):
    sess = _session(settings)
    resp = sess.get(url, timeout=settings.get("request_timeout", 25))
    resp.raise_for_status()
    return resp.json()


def fetch_remotive(cfg: dict) -> list[Job]:
    settings = cfg["settings"]
    url = cfg["sources"]["remotive"]["url"]
    try:
        data = _get_json(url, settings)
    except Exception as e:
        log.warning("Remotive failed: %s", e)
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(Job(
            title=clean_html(j.get("title", "")),
            company=clean_html(j.get("company_name", "")),
            location=j.get("candidate_required_location", "") or "Worldwide",
            url=j.get("url", ""),
            source="Remotive", work_mode="remote", region="remote",
            posted=_parse_date(j.get("publication_date")),
            description=truncate(clean_html(j.get("description", "")), 1200),
            salary=j.get("salary", "") or "",
            id=f"rm-{j.get('id')}",
        ))
    log.info("Remotive -> %d", len(out))
    return out


def fetch_remoteok(cfg: dict) -> list[Job]:
    settings = cfg["settings"]
    url = cfg["sources"]["remoteok"]["url"]
    try:
        data = _get_json(url, settings)
    except Exception as e:
        log.warning("RemoteOK failed: %s", e)
        return []
    out = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        out.append(Job(
            title=clean_html(j.get("position", "")),
            company=clean_html(j.get("company", "")),
            location=j.get("location", "") or "Worldwide",
            url=j.get("url", "") or j.get("apply_url", ""),
            source="RemoteOK", work_mode="remote", region="remote",
            posted=_parse_date(j.get("date")),
            description=truncate(clean_html(j.get("description", "")), 1200),
            salary=j.get("salary", "") or "",
            id=f"ro-{j.get('id')}",
        ))
    log.info("RemoteOK -> %d", len(out))
    return out


def fetch_himalayas(cfg: dict) -> list[Job]:
    settings = cfg["settings"]
    url = cfg["sources"]["himalayas"]["url"]
    try:
        data = _get_json(url, settings)
    except Exception as e:
        log.warning("Himalayas failed: %s", e)
        return []
    out = []
    for j in data.get("jobs", []):
        locs = j.get("locationRestrictions") or []
        loc = ", ".join(locs) if locs else "Worldwide"
        out.append(Job(
            title=clean_html(j.get("title", "")),
            company=clean_html(j.get("companyName", "")),
            location=loc,
            url=j.get("applicationLink", "") or j.get("guid", ""),
            source="Himalayas", work_mode="remote", region="remote",
            posted=_parse_date(j.get("pubDate")),
            description=truncate(clean_html(j.get("excerpt", "") or j.get("description", "")), 1200),
            id=f"hm-{j.get('id') or j.get('guid','')}",
        ))
    log.info("Himalayas -> %d", len(out))
    return out


def fetch_jobicy(cfg: dict) -> list[Job]:
    settings = cfg["settings"]
    url = cfg["sources"]["jobicy"]["url"]
    try:
        data = _get_json(url, settings)
    except Exception as e:
        log.warning("Jobicy failed: %s", e)
        return []
    out = []
    for j in data.get("jobs", []):
        geo = j.get("jobGeo", "") or "Anywhere"
        out.append(Job(
            title=clean_html(j.get("jobTitle", "")),
            company=clean_html(j.get("companyName", "")),
            location=geo,
            url=j.get("url", ""),
            source="Jobicy", work_mode="remote", region="remote",
            posted=_parse_date(j.get("pubDate")),
            description=truncate(clean_html(j.get("jobExcerpt", "") or j.get("jobDescription", "")), 1200),
            id=f"jb-{j.get('id')}",
        ))
    log.info("Jobicy -> %d", len(out))
    return out


def fetch_arbeitnow(cfg: dict) -> list[Job]:
    settings = cfg["settings"]
    url = cfg["sources"]["arbeitnow"]["url"]
    try:
        data = _get_json(url, settings)
    except Exception as e:
        log.warning("Arbeitnow failed: %s", e)
        return []
    out = []
    for j in data.get("data", []):
        remote = bool(j.get("remote"))
        out.append(Job(
            title=clean_html(j.get("title", "")),
            company=clean_html(j.get("company_name", "")),
            location=j.get("location", "") or "",
            url=j.get("url", ""),
            source="Arbeitnow",
            work_mode="remote" if remote else "onsite",
            region="remote" if remote else "other",
            posted=_parse_date(j.get("created_at")),
            description=truncate(clean_html(j.get("description", "")), 1200),
            id=f"an-{j.get('slug','')}",
        ))
    log.info("Arbeitnow -> %d", len(out))
    return out


def fetch_wwr(cfg: dict) -> list[Job]:
    settings = cfg["settings"]
    out = []
    sess = _session(settings)
    for feed in cfg["sources"]["weworkremotely"].get("feeds", []):
        try:
            resp = sess.get(feed, timeout=settings.get("request_timeout", 25))
            parsed = feedparser.parse(resp.content)
        except Exception as e:
            log.warning("WWR feed failed %s: %s", feed, e)
            continue
        for e in parsed.entries:
            title = clean_html(e.get("title", ""))
            company, _, role = title.partition(":")
            out.append(Job(
                title=clean_html(role or title),
                company=clean_html(company),
                location=e.get("region", "") or "Worldwide",
                url=e.get("link", ""),
                source="WeWorkRemotely", work_mode="remote", region="remote",
                posted=_parse_date(e.get("published")),
                description=truncate(clean_html(e.get("summary", "")), 1200),
                id=f"ww-{abs(hash(e.get('link','')))}",
            ))
    log.info("WeWorkRemotely -> %d", len(out))
    return out


# ------------------------------------------------------------------
# Company career boards (Greenhouse / Lever) — direct, stable, key-free.
# Reused from the user's earlier GulfCareer platform.
# ------------------------------------------------------------------
GULF_HINTS = ("uae", "united arab emirates", "emirates", "dubai", "abu dhabi",
              "sharjah", "ajman", "ras al khaimah", "fujairah", "umm al quwain",
              "saudi", "riyadh", "jeddah", "dammam", "khobar", "qatar", "doha",
              "kuwait", "bahrain", "manama", "oman", "muscat")


def _is_gulf(loc: str) -> bool:
    low = (loc or "").lower()
    return any(h in low for h in GULF_HINTS)


def fetch_greenhouse(cfg: dict) -> list[Job]:
    settings = cfg["settings"]
    boards = cfg.get("company_boards", {}).get("greenhouse", [])
    s = _session(settings)
    out: list[Job] = []
    for b in boards:
        slug, label = b["slug"], b["name"]
        try:
            r = s.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
                      timeout=int(settings.get("request_timeout", 25)))
            if r.status_code != 200:
                continue
            for j in r.json().get("jobs", []):
                loc = (j.get("location") or {}).get("name", "")
                if not _is_gulf(loc):
                    continue
                desc = ""
                if j.get("content"):
                    import html as _html
                    raw = _html.unescape(j["content"])
                    desc = BeautifulSoup(raw, "lxml").get_text(" ", strip=True)
                out.append(Job(
                    title=j.get("title", ""), company=label, location=loc,
                    url=j.get("absolute_url", ""), source=f"موقع الشركة ({slug})",
                    work_mode="hybrid" if "hybrid" in f"{j.get('title','')} {desc[:400]}".lower() else "onsite",
                    region="gulf", posted=_parse_date(j.get("updated_at")),
                    description=desc[:4000], id=f"gh-{slug}-{j.get('id')}",
                ))
        except Exception as e:
            log.warning("Greenhouse %s: %s", slug, e)
    return out


def fetch_lever(cfg: dict) -> list[Job]:
    settings = cfg["settings"]
    boards = cfg.get("company_boards", {}).get("lever", [])
    s = _session(settings)
    out: list[Job] = []
    for b in boards:
        slug, label = b["slug"], b["name"]
        try:
            r = s.get(f"https://api.lever.co/v0/postings/{slug}?mode=json",
                      timeout=int(settings.get("request_timeout", 25)))
            if r.status_code != 200:
                continue
            for j in r.json():
                cats = j.get("categories") or {}
                loc = cats.get("location", "") or ""
                if not _is_gulf(loc):
                    continue
                desc = j.get("descriptionPlain") or ""
                if not desc and j.get("description"):
                    desc = BeautifulSoup(j["description"], "lxml").get_text(" ", strip=True)
                extra = " ".join(
                    BeautifulSoup(l.get("content", ""), "lxml").get_text(" ", strip=True)
                    for l in (j.get("lists") or [])
                )
                ts = j.get("createdAt")
                posted = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None
                out.append(Job(
                    title=j.get("text", ""), company=label, location=loc,
                    url=j.get("hostedUrl", ""), source=f"موقع الشركة ({slug})",
                    work_mode="hybrid" if "hybrid" in f"{cats.get('commitment','')} {desc[:400]}".lower() else "onsite",
                    region="gulf", posted=posted,
                    description=f"{desc} {extra}"[:4000], id=f"lv-{slug}-{j.get('id')}",
                ))
        except Exception as e:
            log.warning("Lever %s: %s", slug, e)
    return out


# ------------------------------------------------------------------
# LinkedIn job-detail endpoint — gives the REAL seniority level and the
# full description. Also reused from the user's earlier platform.
# ------------------------------------------------------------------
def _linkedin_numeric_id(job: Job) -> str:
    if job.id.startswith("li-") and job.id[3:].isdigit():
        return job.id[3:]
    m = re.search(r"(\d{8,})", job.url or "")
    return m.group(1) if m else ""


def enrich_with_details(jobs: list[Job], cfg: dict, limit: int = 26) -> int:
    """Fetch full details for the top LinkedIn candidates (in place)."""
    settings = cfg["settings"]
    if not cfg.get("sources", {}).get("linkedin", {}).get("details", True):
        return 0
    targets = [j for j in jobs if j.source == "LinkedIn" and j.score > 0][ :limit]
    if not targets:
        return 0
    s = _session(settings)
    s.headers.update({"Accept-Language": "en-US,en;q=0.9,ar;q=0.8"})
    pause = float(settings.get("detail_pause", 4.0))
    time.sleep(float(settings.get("detail_cooldown", 15)))
    ok = 0
    recovered = False
    for j in targets:
        jid = _linkedin_numeric_id(j)
        if not jid:
            continue
        try:
            r = s.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}",
                      timeout=int(settings.get("request_timeout", 25)))
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "lxml")
                el = soup.select_one(".show-more-less-html__markup")
                if el:
                    j.description = el.get_text(" ", strip=True)[:4000]
                for li in soup.select("li.description__job-criteria-item"):
                    h, sp = li.find("h3"), li.find("span")
                    if not h or not sp:
                        continue
                    k, v = h.get_text(strip=True), sp.get_text(strip=True)
                    if k == "Seniority level":
                        j.seniority = v
                    elif k == "Employment type":
                        j.employment_type = v
                ok += 1
            elif r.status_code == 429:
                if recovered:
                    log.warning("LinkedIn details rate-limited again; stopping enrichment.")
                    break
                recovered = True
                log.info("LinkedIn details rate-limited; resting 45s then resuming.")
                time.sleep(45)
                continue
        except Exception as e:
            log.warning("LinkedIn detail %s: %s", jid, e)
        time.sleep(pause)
    log.info("Enriched %d/%d LinkedIn jobs with full details", ok, len(targets))
    return ok


API_FETCHERS = {
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "himalayas": fetch_himalayas,
    "jobicy": fetch_jobicy,
    "arbeitnow": fetch_arbeitnow,
    "weworkremotely": fetch_wwr,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
}


def fetch_all(cfg: dict) -> tuple[list[Job], list[str]]:
    """Return (jobs, failed_sources)."""
    jobs: list[Job] = []
    failed: list[str] = []

    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(fn, cfg): name
            for name, fn in API_FETCHERS.items()
            if cfg["sources"].get(name, {}).get("enabled", True)
        }
        for fut in cf.as_completed(futures):
            name = futures[fut]
            try:
                res = fut.result()
                if not res:
                    failed.append(name)
                jobs.extend(res)
            except Exception as e:
                log.warning("Source %s errored: %s", name, e)
                failed.append(name)

    try:
        li = fetch_linkedin_all(cfg)
        if not li:
            failed.append("linkedin")
        jobs.extend(li)
    except Exception as e:
        log.warning("LinkedIn block failed: %s", e)
        failed.append("linkedin")

    return jobs, failed
