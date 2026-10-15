# Job sources

These were checked by calling each endpoint on 2026-10-15. `fetch_jobs.py` turns every source into the same record:

```
{id, source, title, company, url, apply_url, posted_at (ISO, UTC), location_raw,
 salary_min, salary_max, salary_currency, salary_text, tags[], description_text, poster,
 contacts[], how_to_apply (form|email|link), scam_flags[], eligible (yes|no|unknown), eligibility_note,
 dedupe_key, prescore, core_hits, secondary_hits, title_hit, also_on[]}
```

A source that is down, slow or changed is skipped with a warning; the run goes on.

## 1. Remote job boards (script, group `boards`)
| Source | Endpoint | Date field | Location field | Notes |
|---|---|---|---|---|
| Remotive | `https://remotive.com/api/remote-jobs` | `publication_date` | `candidate_required_location` | The free API only returns a small, delayed set (14 jobs on the test day). They ask for at most a few calls a day and a link back. |
| Remote OK | `https://remoteok.com/api` | `epoch` / `date` | `location` | The first item is a legal notice, so skip it. **Terms: link back to the Remote OK job URL and name Remote OK as the source.** About 100 latest jobs. |
| We Work Remotely | `https://weworkremotely.com/remote-jobs.rss` | `pubDate` (RFC 822) | `region`, `country`, `state` | The title is `Company: Role`. `region` often says "Anywhere in the World" even for US-only jobs, so read the description. |
| Himalayas | `https://himalayas.app/jobs/api/search?q=<keyword>` | `pubDate` (epoch) | `locationRestrictions[]` (empty = worldwide), `timezoneRestrictions[]` | 20 results per query. The script runs up to 5 queries (titles + core skills). Also has salary and seniority. |
| Jobicy | `https://jobicy.com/api/v2/remote-jobs?count=100&tag=<kw>` | `pubDate` | `jobGeo` | **Terms: credit Jobicy with a link, and apply buttons must go to the original job URL.** |
| Working Nomads | `https://www.workingnomads.com/api/exposed_jobs/` | `pub_date` | `location` | About 50 latest jobs. |

When you show jobs from Remote OK or Jobicy, the Link column is the job URL they gave and the Source column names them. That is enough to meet their terms for personal use.

## 2. Communities (script, group `communities`)
Reddit is **not used**. Since 2026 its JSON API needs a login, and its RSS feeds rate-limit (HTTP 429) after a few requests, so most subreddits failed on every run. The script also drops any Reddit post that a sub-agent returns.

### Hacker News
- The script finds the newest thread called "Ask HN: Who is hiring? (Month Year)" by user `whoishiring`: `https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10`.
- It then fetches all comments: `https://hn.algolia.com/api/v1/items/<id>`. Each top-level comment is one job. Only comments that mention "remote" are kept.
- The first line is usually `Company | Role | Location | Remote | Salary`, so the script takes the company from the first part and the role from the first part that looks like a job title.
- The thread is posted on the 1st of each month, so HN jobs are kept for at least 35 days whatever `--days` is.
- The old "Freelancer? Seeking freelancer?" thread is not posted any more.

## 3. Hidden company job pages (sub-agent "ATS hunter")
Many companies list remote roles only on their own job page. Copy this section word for word into the ATS hunter's brief.

> You are finding remote job openings on company job pages for this profile: titles <titles>; core skills <skills>; the candidate lives in <country> and can work for jobs open to <eligible_regions>.
> Use WebSearch (extended mode if offered). Run each of these, once per title (at most 3 titles), and also once with the top 2 core skills in place of the title:
> - `site:boards.greenhouse.io "<title>" remote`
> - `site:job-boards.greenhouse.io "<title>" remote`
> - `site:jobs.lever.co "<title>" remote`
> - `site:jobs.ashbyhq.com "<title>" remote`
> - `site:apply.workable.com "<title>" remote`
> - `site:jobs.smartrecruiters.com "<title>" remote`
> - `"<title>" remote "<region>" (careers OR jobs) -linkedin -indeed -glassdoor` for each eligible region other than Worldwide
>
> Keep results that are a single company's job page or a single job on one of those sites. Drop aggregator pages, LinkedIn, Indeed and Glassdoor. Drop results whose snippet says the role is on-site, hybrid, or limited to a country or region the candidate is not in.
> Return only URLs, one per line, at most 60, nothing else. Never open login pages and never fill in any form.

The script turns Greenhouse, Lever and Ashby URLs into board slugs (`parse_ats_url`) and pulls each company's full board through its public JSON API (group `watchlist`). That way you get every remote role at that company, with the real post date. Workable and SmartRecruiters URLs don't have a board API in the script: add them to `community.json` (next section) as single jobs if the community scout or you can read their date.

## 4. Company watchlist (script, group `watchlist`)
| ATS | Endpoint | Remote check | Date |
|---|---|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true` | `location.name` contains remote / anywhere / distributed | `first_published` |
| Lever | `https://api.lever.co/v0/postings/<slug>?mode=json` | `workplaceType == "remote"` or location contains remote | `createdAt` (ms) |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=true` | `isRemote` or `workplaceType == "Remote"` | `publishedAt` |

Watchlist entries in `profile.md` look like `greenhouse:gitlab, lever:spotify, ashby:linear`.

## 5. Community scout (sub-agent)
Copy this section word for word into the community scout's brief.

> You are finding remote job posts in online communities for this profile: titles <titles>; core skills <skills>; the candidate lives in <country> and can work for jobs open to <eligible_regions>. Only posts from the last <days> days (HN: the current month's thread).
> 1. Read the latest "Ask HN: Who is hiring?" thread for posts that match: search it with `site:news.ycombinator.com "<title>" remote "Who is hiring"`.
> 2. Find community job boards for the candidate's field and read their newest remote listings with WebFetch. Examples: the Python.org job board for Python roles, Flutter/Dart community job boards for Flutter roles, the Godot and Unity community job boards for game roles, and remote-work newsletters or company "we're hiring" blog posts. Search with `"<title>" remote hiring <Month Year> -linkedin -indeed -glassdoor`.
> 3. Keep only real hiring posts for remote roles the candidate could take. Skip people looking for work and posts asking applicants to pay anything. Skip Reddit entirely: do not search, open or return reddit.com pages.
> Return ONLY a JSON array, no prose. One object per job:
> `{"source": "hn"|"web", "id": "<stable id or URL>", "title": "...", "company": "... or empty", "url": "<post or comment URL>", "posted_at": "<ISO date; required, skip the post if unknown>", "location_raw": "<location words from the post>", "description_text": "<the post text, up to 3000 characters>", "poster": "hn:<name> or empty", "contacts": [{"type": "email"|"apply_link"|"poster", "value": "...", "from": "posting"}], "found_by": "community scout"}`
> Contacts must be copied from the post itself. Never guess an email address and never look the poster up elsewhere.

## Sources not used
Reddit is dropped because of rate limits (see section 2). LinkedIn, Indeed, Glassdoor, Wellfound and X need a login or a paid API, and their terms forbid scraping. If the user wants them, they can paste a job URL or description and you treat it as one more job: score it, tailor for it, apply.
