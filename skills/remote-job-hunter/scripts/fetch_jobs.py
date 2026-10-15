#!/usr/bin/env python3
"""Fetch remote jobs from public feeds, normalize them, pull out contacts, flag scams and prefilter.

Standard library only. Run with `python3 -I`:

    python3 -I fetch_jobs.py --profile ~/.claude/remote-job-hunter/profile.md \
        --days 14 --out .remote-job-hunter/2026-10-15/raw.json

Source groups: boards (Remotive, RemoteOK, We Work Remotely, Himalayas, Jobicy, Working Nomads),
communities (Hacker News "Who is hiring?") and watchlist (Greenhouse, Lever, Ashby).
A source that fails is skipped with a warning; it never stops the run.
"""
import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

UA = "macos:remote-job-hunter:v1.0 (personal job search script)"
TIMEOUT = 40
SOURCE_RANK = {"greenhouse": 0, "lever": 0, "ashby": 0, "himalayas": 1, "jobicy": 1, "remotive": 1,
               "remoteok": 1, "weworkremotely": 1, "workingnomads": 1, "hn": 2, "web": 3}
FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "proton.me", "protonmail.com",
             "icloud.com", "aol.com", "mail.com", "gmx.com", "yandex.com", "live.com"}
WORLDWIDE = re.compile(r"\b(worldwide|anywhere|global(ly)?|international|all countries|any ?where in the world|fully remote, any)\b", re.I)
RESTRICTED = {
    "US": r"\b(us|u\.s\.|usa|united states|us[- ]only|us[- ]based|americas?|north america)\b",
    "Canada": r"\bcanada\b", "UK": r"\b(uk|united kingdom|britain|england)\b",
    "Europe": r"\b(europe|eu|eea|emea|european)\b", "LATAM": r"\b(latam|latin america|south america)\b",
    "Germany": r"\bgermany\b", "France": r"\bfrance\b", "Spain": r"\bspain\b", "Netherlands": r"\bnetherlands\b",
    "Poland": r"\bpoland\b", "Brazil": r"\bbrazil\b", "Mexico": r"\bmexico\b", "India": r"\bindia\b",
    "Australia": r"\baustralia\b", "APAC": r"\b(apac|asia[- ]pacific)\b", "Asia": r"\basia\b",
    "Africa": r"\bafrica\b", "Philippines": r"\bphilippines\b", "Indonesia": r"\bindonesia\b",
    "Singapore": r"\bsingapore\b", "Japan": r"\b(japan|jp)\b",
    "China": r"\b(china|prc)\b", "Vietnam": r"\bvietnam\b", "Malaysia": r"\bmalaysia\b", "Thailand": r"\bthailand\b",
}
REGION_CONTAINS = {"Asia": {"APAC", "Indonesia", "Philippines", "Singapore", "Japan", "India", "China", "Vietnam", "Malaysia",
                            "Thailand"},
                   "APAC": {"Indonesia", "Philippines", "Singapore", "Japan", "Australia", "India"},
                   "Europe": {"UK", "Germany", "France", "Spain", "Netherlands", "Poland"},
                   "LATAM": {"Brazil", "Mexico"}}

# ---------- helpers ----------


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.links = [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr"):
            self.out.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_data(self, data):
        self.out.append(data)


def strip_html(text):
    """Return (plain text, links) from an HTML fragment. Also handles double-escaped HTML."""
    if not text:
        return "", []
    if "&lt;" in text and "<" not in text:
        text = html.unescape(text)
    p = _Text()
    p.feed(text)
    plain = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    plain = re.sub(r"\n\s*\n+", "\n", plain).strip()
    return plain, p.links


def to_iso(value):
    """Parse epoch seconds/ms, ISO 8601 or RFC 822 into an ISO UTC string. None if unparseable."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            v = float(value)
            if v > 1e12:
                v /= 1000.0
            return datetime.fromtimestamp(v, timezone.utc).isoformat(timespec="seconds")
        s = str(value).strip()
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            from email.utils import parsedate_to_datetime
            d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError):
        return None


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def make(source, sid, title, company, url, posted, description_html="", **extra):
    text, links = strip_html(description_html)
    rec = {
        "id": "%s:%s" % (source, sid), "source": source, "title": (title or "").strip(),
        "company": (company or "").strip(), "url": url, "apply_url": extra.pop("apply_url", None) or url,
        "posted_at": to_iso(posted), "location_raw": extra.pop("location_raw", "") or "",
        "salary_min": extra.pop("salary_min", None), "salary_max": extra.pop("salary_max", None),
        "salary_currency": extra.pop("salary_currency", None), "salary_text": extra.pop("salary_text", None),
        "tags": [t for t in (extra.pop("tags", None) or []) if t], "description_text": text,
        "links": links, "poster": extra.pop("poster", None),
    }
    rec.update(extra)
    return rec

# ---------- network ----------


class Fetcher:
    """Downloads URLs, or reads saved files when a fixtures directory is given (used by the tests)."""

    def __init__(self, fixtures=None):
        self.fixtures = fixtures

    def get(self, url, fixture, retries=1):
        if self.fixtures:
            path = os.path.join(self.fixtures, fixture)
            if not os.path.exists(path):
                raise IOError("no fixture %s" % fixture)
            with open(path, encoding="utf-8") as f:
                return f.read()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    return r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < retries:
                    time.sleep(30)
                    continue
                raise

    def json(self, url, fixture):
        return json.loads(self.get(url, fixture))

# ---------- sources ----------


def src_remotive(f, keys):
    data = f.json("https://remotive.com/api/remote-jobs", "remotive.json")
    return [make("remotive", j["id"], j.get("title"), j.get("company_name"), j.get("url"),
                 j.get("publication_date"), j.get("description", ""), location_raw=j.get("candidate_required_location"),
                 salary_text=j.get("salary") or None, tags=j.get("tags"), job_type=j.get("job_type"))
            for j in data.get("jobs", [])]


def src_remoteok(f, keys):
    data = f.json("https://remoteok.com/api", "remoteok.json")
    out = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue
        out.append(make("remoteok", j.get("id"), j.get("position"), j.get("company"), j.get("url"),
                        j.get("epoch") or j.get("date"), j.get("description", ""), location_raw=j.get("location"),
                        apply_url=j.get("url"), salary_min=j.get("salary_min") or None,
                        salary_max=j.get("salary_max") or None, salary_currency="USD" if j.get("salary_min") else None,
                        tags=j.get("tags"), attribution="Source: Remote OK (link back to the job URL)"))
    return out


def src_wwr(f, keys):
    root = ET.fromstring(f.get("https://weworkremotely.com/remote-jobs.rss", "weworkremotely.rss"))
    out = []
    for it in root.iter("item"):
        g = lambda tag: (it.findtext(tag) or "").strip()
        title = g("title")
        company, _, role = title.partition(":")
        if not role:
            company, role = "", title
        loc = ", ".join(x for x in (g("region"), g("country"), g("state")) if x)
        out.append(make("weworkremotely", g("guid") or g("link"), role, company, g("link"), g("pubDate"),
                        g("description"), location_raw=loc,
                        tags=[s.strip() for s in g("skills").split(",")] + [g("category")], job_type=g("type")))
    return out


def src_himalayas(f, keys):
    out, seen = [], set()
    queries = keys[:5] or [""]
    for i, q in enumerate(queries):
        url = "https://himalayas.app/jobs/api/search?q=%s" % urllib.parse.quote(q) if q else "https://himalayas.app/jobs/api?limit=20"
        try:
            data = f.json(url, "himalayas_%d.json" % i if q else "himalayas.json")
        except Exception as e:  # one failing query should not lose the others
            warn("himalayas query %r: %s" % (q, e))
            continue
        for j in data.get("jobs", []):
            gid = j.get("guid") or j.get("applicationLink")
            if gid in seen:
                continue
            seen.add(gid)
            loc = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
            out.append(make("himalayas", gid, j.get("title"), j.get("companyName"), j.get("applicationLink"),
                            j.get("pubDate"), j.get("description", ""), location_raw=loc,
                            salary_min=j.get("minSalary"), salary_max=j.get("maxSalary"),
                            salary_currency=j.get("currency"), tags=j.get("categories"),
                            seniority=j.get("seniority"), timezones=j.get("timezoneRestrictions")))
    return out


def src_jobicy(f, keys):
    out, seen = [], set()
    queries = [""] + [k for k in keys[:4] if " " not in k]
    for i, q in enumerate(queries):
        url = "https://jobicy.com/api/v2/remote-jobs?count=100" + ("&tag=%s" % urllib.parse.quote(q) if q else "")
        try:
            data = f.json(url, "jobicy_%d.json" % i if q else "jobicy.json")
        except Exception as e:
            warn("jobicy tag %r: %s" % (q, e))
            continue
        for j in data.get("jobs", []):
            if j.get("id") in seen:
                continue
            seen.add(j.get("id"))
            out.append(make("jobicy", j.get("id"), j.get("jobTitle"), j.get("companyName"), j.get("url"),
                            j.get("pubDate"), j.get("jobDescription", ""), location_raw=j.get("jobGeo"),
                            salary_min=j.get("salaryMin"), salary_max=j.get("salaryMax"),
                            salary_currency=j.get("salaryCurrency"), tags=j.get("jobIndustry"),
                            seniority=[j.get("jobLevel")] if j.get("jobLevel") else None,
                            attribution="Source: Jobicy (link back to the job URL)"))
    return out


def src_workingnomads(f, keys):
    data = f.json("https://www.workingnomads.com/api/exposed_jobs/", "workingnomads.json")
    return [make("workingnomads", j.get("url"), j.get("title"), j.get("company_name"), j.get("url"),
                 j.get("pub_date"), j.get("description", ""), location_raw=j.get("location"),
                 tags=[t.strip() for t in (j.get("tags") or "").split(",")] + [j.get("category_name")])
            for j in data]


def src_hn(f, keys):
    stories = f.json("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10",
                     "hn_stories.json")
    story = next((h for h in stories.get("hits", []) if h.get("title", "").startswith("Ask HN: Who is hiring?")), None)
    if not story:
        warn("hn: no 'Who is hiring?' thread found")
        return []
    item = f.json("https://hn.algolia.com/api/v1/items/%s" % story["objectID"], "hn_item.json")
    out = []
    for c in item.get("children", []):
        raw = c.get("text") or ""
        if not raw or not re.search(r"\bremote\b", raw, re.I):
            continue
        first = html.unescape(re.split(r"<p>", raw, maxsplit=1)[0])
        first = re.sub(r"<[^>]+>", "", first).strip()
        parts = [p.strip() for p in re.split(r"\s+[|•]\s+|\s+[—–]\s+", first) if p.strip()]
        company = parts[0] if parts else ""
        role = next((p for p in parts[1:] if re.search(r"engineer|developer|designer|manager|lead|scientist|analyst|"
                                                       r"architect|devops|sre|writer|marketer|support|head of|director",
                                                       p, re.I)), first[:140])
        out.append(make("hn", c.get("id"), role, company, "https://news.ycombinator.com/item?id=%s" % c.get("id"),
                        c.get("created_at_i") or c.get("created_at"), raw, location_raw=first,
                        poster="hn:" + (c.get("author") or ""), hn_thread=story.get("title"), headline=first[:300]))
    return out


def src_greenhouse(f, slug):
    data = f.json("https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % slug, "greenhouse_%s.json" % slug)
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        if not re.search(r"remote|anywhere|distributed|home[- ]based|work from home|wfh", loc, re.I):
            continue
        out.append(make("greenhouse", "%s:%s" % (slug, j.get("id")), j.get("title"), j.get("company_name") or slug,
                        j.get("absolute_url"), j.get("first_published") or j.get("updated_at"), j.get("content", ""),
                        location_raw=loc, ats="greenhouse"))
    return out


def src_lever(f, slug):
    data = f.json("https://api.lever.co/v0/postings/%s?mode=json" % slug, "lever_%s.json" % slug)
    out = []
    for j in data:
        cats = j.get("categories") or {}
        loc = ", ".join(cats.get("allLocations") or [cats.get("location") or ""])
        if (j.get("workplaceType") or "").lower() != "remote" and not re.search(r"remote", loc, re.I):
            continue
        body = (j.get("description") or "") + "".join(
            "<h3>%s</h3>%s" % (l.get("text", ""), l.get("content", "")) for l in j.get("lists") or []) + (j.get("additional") or "")
        sal = j.get("salaryRange") or {}
        out.append(make("lever", "%s:%s" % (slug, j.get("id")), j.get("text"), slug, j.get("hostedUrl"), j.get("createdAt"),
                        body, apply_url=j.get("applyUrl"), location_raw="%s %s" % (loc, j.get("country") or ""),
                        salary_min=sal.get("min"), salary_max=sal.get("max"), salary_currency=sal.get("currency"),
                        ats="lever"))
    return out


def src_ashby(f, slug):
    data = f.json("https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true" % slug, "ashby_%s.json" % slug)
    out = []
    for j in data.get("jobs", []):
        if not (j.get("isRemote") or (j.get("workplaceType") or "").lower() == "remote") or j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or [] if isinstance(s, dict)]
        comp = (j.get("compensation") or {}).get("scrapeableCompensationSalarySummary")
        out.append(make("ashby", "%s:%s" % (slug, j.get("id")), j.get("title"), slug, j.get("jobUrl"), j.get("publishedAt"),
                        j.get("descriptionHtml", ""), apply_url=j.get("applyUrl"), location_raw=", ".join(l for l in locs if l),
                        salary_text=comp, ats="ashby"))
    return out


ATS_FUNCS = {"greenhouse": src_greenhouse, "lever": src_lever, "ashby": src_ashby}
ATS_URL = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
]


def parse_ats_url(url):
    """Turn a company job page URL into ('greenhouse'|'lever'|'ashby', slug), or None."""
    for ats, rx in ATS_URL:
        m = rx.search(url or "")
        if m and m.group(1).lower() not in ("v1", "embed", "jobs"):
            return ats, urllib.parse.unquote(m.group(1))
    return None

# ---------- enrichment ----------


EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
NOT_EMAIL_TLD = {"png", "jpg", "jpeg", "gif", "svg", "webp"}
NOISE_EMAIL = re.compile(r"@(indeed|linkedin|example|sentry|wixpress|glassdoor)\.|^(privacy|dpo|gdpr|legal|accommodations?|"
                         r"accessibility|noreply|no-reply|donotreply|abuse|security)@", re.I)
NOISE_LINK = re.compile(r"privacy|policy|cookie|diversity|/blog|eeo|accommodation|accessibility|terms|benefits|"
                        r"linkedin\.com|twitter\.com|x\.com|facebook\.com|instagram\.com", re.I)


def deobfuscate(text):
    t = re.sub(r"\s*[\[({<]\s*at\s*[\])}>]\s*", "@", text, flags=re.I)
    t = re.sub(r"\s*[\[({<]\s*dot\s*[\])}>]\s*", ".", t, flags=re.I)
    t = re.sub(r"\b([\w.+-]+)\s+(?:at|AT)\s+([\w-]+)\s+(?:dot|DOT)\s+([a-z]{2,})\b", r"\1@\2.\3", t)
    return t


def find_emails(text):
    found = []
    for e in EMAIL.findall(deobfuscate(text or "")):
        e = e.strip(".").lower()
        if e.rsplit(".", 1)[-1] in NOT_EMAIL_TLD or e in found or NOISE_EMAIL.search(e):
            continue
        found.append(e)
    return found


def contacts_for(rec):
    text = rec["description_text"] + "\n" + " ".join(rec["links"])
    contacts = [{"type": "email", "value": e, "from": "posting"} for e in find_emails(text)]
    for l in rec["links"]:
        mail = l[7:].split("?")[0].lower() if l.lower().startswith("mailto:") else ""
        if mail and mail not in {c["value"] for c in contacts} and not NOISE_EMAIL.search(mail):
            contacts.append({"type": "email", "value": mail, "from": "posting"})
    home = urllib.parse.urlparse(rec["url"] or "").netloc
    apply_links = [l for l in rec["links"] if not l.lower().startswith("mailto:") and urllib.parse.urlparse(l).netloc != home
                   and not NOISE_LINK.search(l) and re.search(r"apply|careers|jobs|greenhouse|lever\.co|ashbyhq|workable|"
                                                        r"smartrecruiters|bamboohr|recruitee|forms\.gle|typeform", l, re.I)]
    for l in dict.fromkeys(apply_links):
        contacts.append({"type": "apply_link", "value": l, "from": "posting"})
    if rec.get("poster"):
        contacts.append({"type": "poster", "value": rec["poster"], "from": rec["source"]})
    return contacts


def how_to_apply(rec):
    text = rec["description_text"].lower()
    has_email = any(c["type"] == "email" for c in rec["contacts"])
    if rec.get("ats"):
        return "form"
    if has_email and re.search(r"\b(email|e-mail)\b[^.\n]{0,40}\b(us|me|at|to)\b|send (us |me )?(your )?(cv|resume|"
                               r"portfolio|application)|apply (by|via) e-?mail", text):
        return "email"
    if rec["source"] not in ("hn", "web"):
        return "form"
    if any(parse_ats_url(c["value"]) for c in rec["contacts"] if c["type"] == "apply_link"):
        return "form"
    if has_email and re.search(r"\b(email|e-mail|send|reach out|contact|apply)\b", text):
        return "email"
    if has_email:
        return "email"
    return "link"


SCAM_RULES = [
    ("pay_to_work", r"(registration|training|onboarding|starter|application|processing)\s+(fee|kit|cost)|pay\s+(for|a)\s+"
                    r"(training|kit|equipment|fee|registration)|purchase\s+(equipment|software|a laptop)|refundable deposit"),
    ("crypto_or_check", r"(paid|pay|payment|salary|compensation)\s+(is\s+)?(in|via|with|through)\s+(crypto|bitcoin|btc|"
                        r"usdt|ethereum|gift cards?)|cashier'?s check|check deposit|mobile deposit|payment by check|"
                        r"send you a check"),
    ("money_mule", r"\b(reship|reshipping|package forwarding|receive packages|money transfer agent|payment processing agent|"
                   r"process payments for)\b"),
    ("too_good_to_be_true", r"(no experience|no skills|anyone can|easy money|work \d+ (min|minutes|hours) a day)"
                            r".{0,120}\$\s?\d{2,}|\$\s?\d{2,}.{0,120}(no experience|no skills|anyone can|easy money)"),
]


def scam_flags(rec):
    text = (rec["title"] + "\n" + rec["description_text"]).lower()
    flags = [name for name, rx in SCAM_RULES if re.search(rx, text, re.I | re.S)]
    off_platform = re.search(r"\b(telegram|whatsapp|signal|wickr|kik)\b", text)
    official = any(c["type"] in ("email", "apply_link") for c in rec["contacts"]) or rec.get("ats")
    if off_platform and not official:
        flags.append("off_platform_contact_only")
    emails = [c["value"] for c in rec["contacts"] if c["type"] == "email"]
    if emails and all(e.split("@")[1] in FREE_MAIL for e in emails) and \
            re.search(r"\b(inc|llc|ltd|corp|corporation|fortune|leading|global company|enterprise)\b", text):
        flags.append("free_email_for_company")
    return flags


def eligibility(rec, profile):
    """Return ('yes'|'no'|'unknown', note) for whether the user may take this job from where they live."""
    loc = rec["location_raw"] or ""
    title = rec["title"]
    text = loc + " " + title
    if rec["source"] in ("hn", "web"):
        text += " " + rec["description_text"][:600]
    if re.search(r"\b(hybrid|on-?site|in[- ]office)\b", title + " " + loc, re.I) and not re.search(r"\bremote\b", title + " " + loc, re.I):
        return "no", "not remote (hybrid/on-site)"
    mine = {norm(x) for x in profile.get("eligible_regions", []) + [profile.get("country", "")] if x}

    def ok(name):
        return norm(name) in mine or any(norm(m) in mine for m in REGION_CONTAINS.get(name, ()))
    in_loc = [n for n, rx in RESTRICTED.items() if re.search(rx, loc, re.I)]
    if rec["source"] not in ("hn", "web") and in_loc and not any(ok(n) for n in in_loc) and not WORLDWIDE.search(loc):
        return "no", "restricted to " + ", ".join(in_loc)  # the location field beats region words in the title
    in_title = [n for n, rx in RESTRICTED.items() if re.search(rx, title, re.I)]
    if in_title and not any(ok(n) for n in in_title):
        return "no", "restricted to " + ", ".join(in_title)
    found = [n for n, rx in RESTRICTED.items() if re.search(rx, text, re.I)]
    for name in found:
        if ok(name):
            return "yes", name
    if any(m and re.search(r"\b%s\b" % re.escape(m), norm(text)) for m in mine):
        return "yes", "matches your regions"
    if WORLDWIDE.search(text) or (rec["source"] == "himalayas" and loc == "Worldwide"):
        return "yes", "worldwide"
    if found:
        return "no", "restricted to " + ", ".join(found)
    plain = norm(loc)
    if rec["source"] not in ("hn", "web") and plain and plain not in ("remote", "remote remote") and \
            not re.search(r"time ?zone|utc|gmt|\bcet\b|\best\b|\bpst\b|\bhours?\b", loc, re.I):
        return "no", "restricted to " + loc[:60]
    return "unknown", loc[:80] or "not stated"


# ---------- profile, tracker, scoring ----------


GENERIC_TITLE_WORDS = {"developer", "engineer", "engineering", "senior", "junior", "lead", "staff", "principal", "software",
                       "full", "stack", "remote", "specialist", "manager", "mid", "level", "head", "the", "and"}
LIST_KEYS = {"titles", "core_skills", "secondary_skills", "exclude_words", "eligible_regions", "watchlist",
             "deal_breakers"}


def load_profile(path):
    """Read `key: value` lines from profile.md. List keys are comma separated."""
    prof = {k: [] for k in LIST_KEYS}
    if not path:
        return prof
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*[-*]?\s*`?([a-z_]+)`?\s*:\s*(.*?)\s*$", line)
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            if key in LIST_KEYS:
                prof[key] = [v.strip() for v in val.split(",") if v.strip()]
            else:
                prof[key] = val
    return prof


def seen_ids(tracker):
    if not tracker or not os.path.exists(os.path.expanduser(tracker)):
        return set()
    with open(os.path.expanduser(tracker), newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r["id"] for r in rows} | {r.get("dedupe_key") for r in rows if r.get("dedupe_key")}


def dedupe_key(rec):
    return norm(rec["company"]) + "|" + norm(rec["title"])


def word_hit(word, text):
    """Whole-word match that treats "front-end", "front end" and "frontend" alike."""
    w = word.lower()
    for t in (text, text.replace("-", ""), text.replace("-", " ")):
        for v in {w, w.replace("-", ""), w.replace(" ", "")}:
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(v), t):
                return True
    return False


def prescore(rec, profile, now, days):
    title = rec["title"].lower()
    body = (title + " " + " ".join(rec["tags"]).lower() + " " + rec["description_text"].lower())
    s, title_hit = 0, 0
    if any(word_hit(t, title) for t in profile["titles"]):
        s, title_hit = 40, 2
    else:
        words = {w for t in profile["titles"] for w in norm(t).split() if len(w) > 2 and w not in GENERIC_TITLE_WORDS}
        if words & set(norm(title).split()):
            s, title_hit = 20, 1
    core = sum(1 for k in profile["core_skills"] if word_hit(k, body))
    sec = sum(1 for k in profile["secondary_skills"] if word_hit(k, body))
    s += min(30, core * 6) + min(10, sec * 2)
    if rec["posted_at"]:
        age = (now - datetime.fromisoformat(rec["posted_at"])).total_seconds() / 86400
        s += max(0, 20 * (1 - age / max(days, 1)))
    return round(s, 1), core, sec, title_hit


def run(args):
    profile = load_profile(args.profile)
    now = datetime.now(timezone.utc) if not args.now else datetime.fromisoformat(args.now)
    f = Fetcher(args.fixtures)
    keys = profile["titles"][:3] + profile["core_skills"][:2]
    groups = set(args.sources.split(","))
    jobs = []
    tasks = []
    if "boards" in groups:
        tasks += [("remotive", src_remotive), ("remoteok", src_remoteok), ("weworkremotely", src_wwr),
                  ("himalayas", src_himalayas), ("jobicy", src_jobicy), ("workingnomads", src_workingnomads)]
    if "communities" in groups:
        tasks += [("hn", src_hn)]
    if "watchlist" in groups:
        entries = list(profile["watchlist"])
        if args.watchlist_urls and os.path.exists(args.watchlist_urls):
            with open(args.watchlist_urls, encoding="utf-8") as fh:
                for line in fh:
                    hit = parse_ats_url(line.strip())
                    if hit:
                        entries.append("%s:%s" % hit)
        for entry in dict.fromkeys(e.strip() for e in entries):  # Lever slugs are case-sensitive
            ats, _, slug = entry.partition(":")
            ats = ats.lower()
            if ats in ATS_FUNCS and slug:
                tasks.append(("%s:%s" % (ats, slug), lambda f, k, a=ats, s=slug: ATS_FUNCS[a](f, s)))
    report = {}

    def go(task):
        name, fn = task
        try:
            got = fn(f, keys)
            return name, got, None
        except Exception as e:
            return name, [], "%s: %s" % (type(e).__name__, e)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, got, err in pool.map(go, tasks):
            report[name] = {"fetched": len(got)} if not err else {"fetched": 0, "error": err}
            if err:
                warn("%s skipped (%s)" % (name, err))
            jobs += got
    for path in args.merge or []:
        got = load_merge(path)
        report["merged:" + os.path.basename(path)] = {"fetched": len(got)}
        jobs += got

    seen = seen_ids(args.tracker)
    excludes = [w.lower() for w in profile["exclude_words"] + profile["deal_breakers"]]
    kept, dropped = {}, {"too_old": 0, "no_date": 0, "seen": 0, "excluded": 0, "ineligible": 0, "scam": 0,
                         "irrelevant": 0, "duplicate": 0}
    for rec in jobs:
        rec.setdefault("links", [])
        given = rec.get("contacts") or []
        rec["contacts"] = given + [c for c in contacts_for(rec) if c not in given]
        rec["how_to_apply"] = how_to_apply(rec)
        rec["scam_flags"] = scam_flags(rec)
        rec["eligible"], rec["eligibility_note"] = eligibility(rec, profile)
        rec["dedupe_key"] = dedupe_key(rec)
        max_age = max(args.days, 35) if rec["source"] == "hn" else args.days  # the HN thread is monthly
        if not rec["posted_at"]:
            dropped["no_date"] += 1
            continue
        if now - datetime.fromisoformat(rec["posted_at"]) > timedelta(days=max_age):
            dropped["too_old"] += 1
            continue
        if rec["id"] in seen or rec["dedupe_key"] in seen:
            dropped["seen"] += 1
            continue
        if any(word_hit(w, rec["title"].lower()) for w in excludes):
            dropped["excluded"] += 1
            continue
        if rec["eligible"] == "no" and not args.keep_ineligible:
            dropped["ineligible"] += 1
            continue
        if len(rec["scam_flags"]) >= 2:
            dropped["scam"] += 1
            continue
        rec["prescore"], rec["core_hits"], rec["secondary_hits"], rec["title_hit"] = prescore(rec, profile, now, args.days)
        if not rec["title_hit"] and not rec["core_hits"] and (profile["titles"] or profile["core_skills"]):
            dropped["irrelevant"] += 1
            continue
        k = rec["dedupe_key"]
        if k in kept:
            dropped["duplicate"] += 1
            a, b = kept[k], rec
            best, other = (a, b) if SOURCE_RANK.get(a["source"], 9) <= SOURCE_RANK.get(b["source"], 9) else (b, a)
            best.setdefault("also_on", []).append(other["url"])
            best["contacts"] += [c for c in other["contacts"] if c not in best["contacts"]]
            kept[k] = best
        else:
            kept[k] = rec
    ranked = sorted(kept.values(), key=lambda r: (r["prescore"], r["posted_at"] or ""), reverse=True)
    shortlist = ranked[:args.top]
    for r in shortlist:
        r["description_text"] = r["description_text"][:args.max_chars]
        r.pop("links", None)
    out = {"generated_at": now.isoformat(timespec="seconds"), "days": args.days, "sources": report,
           "counts": {"fetched": len(jobs), "kept": len(kept), "shortlisted": len(shortlist), "dropped": dropped},
           "jobs": shortlist}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(json.dumps({"out": args.out, **out["counts"], "sources": report}, indent=1))
    return out


def load_merge(path):
    """Read jobs from an earlier raw.json or from a sub-agent's JSON list, filling in any missing fields."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for r in data.get("jobs", []) if isinstance(data, dict) else data:
        if not isinstance(r, dict) or not r.get("title") or not r.get("url"):
            continue
        if r.get("source") == "reddit" or "reddit.com" in r["url"]:  # Reddit is not used (rate limits)
            continue
        rec = make(r.get("source") or "web", r.get("id", "").split(":", 1)[-1] or r["url"], r["title"], r.get("company"),
                   r["url"], r.get("posted_at"), "", apply_url=r.get("apply_url"), location_raw=r.get("location_raw"),
                   salary_min=r.get("salary_min"), salary_max=r.get("salary_max"),
                   salary_currency=r.get("salary_currency"), salary_text=r.get("salary_text"), tags=r.get("tags"),
                   poster=r.get("poster"))
        rec["description_text"] = r.get("description_text") or ""
        for k in ("contacts", "ats", "headline", "attribution", "found_by", "also_on"):
            if r.get(k):
                rec[k] = r[k]
        out.append(rec)
    return out


def warn(msg):
    print("warning: " + msg, file=sys.stderr)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--profile", help="profile.md with key: value lines")
    p.add_argument("--out", required=True, help="where to write the shortlist JSON")
    p.add_argument("--days", type=int, default=14, help="drop posts older than this (HN uses at least 35)")
    p.add_argument("--top", type=int, default=40, help="how many jobs to keep after prescoring")
    p.add_argument("--sources", default="boards,communities,watchlist", help="comma list of groups: boards, communities, watchlist (or none)")
    p.add_argument("--tracker", help="tracker.csv; jobs already in it are skipped")
    p.add_argument("--watchlist-urls", help="text file of company job page URLs found by web search")
    p.add_argument("--merge", nargs="*", help="also read jobs from these JSON files (earlier runs or sub-agent finds)")
    p.add_argument("--keep-ineligible", action="store_true", help="keep jobs restricted to regions you are not in")
    p.add_argument("--max-chars", type=int, default=6000, help="truncate each description to this length")
    p.add_argument("--fixtures", help=argparse.SUPPRESS)
    p.add_argument("--now", help=argparse.SUPPRESS)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    main()
