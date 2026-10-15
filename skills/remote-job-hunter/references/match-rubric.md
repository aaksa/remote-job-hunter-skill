# Match rubric

The script's `prescore` only decides what gets read. Your score decides the ranking. Read the description before scoring; never score from the title alone.

## Hard gates
Check these first. A job that fails one is **not ranked**. Put it under "Filtered out" with the reason.
1. **Can't take it from where they live:** the job is limited to countries, regions or work permits the user doesn't have, e.g. "US only", "must be based in the EU", "must be authorized to work in the UK", or "W-2 employee". The script marks clear cases `eligible: no` and drops them. `unknown` means you decide from the description.
2. **Not remote:** hybrid, on-site, or "remote for the first 3 months then relocate".
3. **Deal-breaker hit:** anything in the profile's `deal_breakers`.
4. **Scam:** 2 or more scam signs (see `contacts-and-scams.md`).
5. **Timezone impossible:** the job requires overlap hours the user can't reasonably work, e.g. 8 hours of US Pacific time for someone at UTC+7. Partial overlap (2–4 hours) is not a gate; it lowers the score.

## Score (0–100)
| Part | Points | How |
|---|---|---|
| Skills | 35 | Share of the job's **required** skills the user has (from the resume), weighted toward the most-repeated ones. Nice-to-haves count half. |
| Seniority | 15 | 15 = the level matches the user's years and scope. 8 = one level off. 0 = two or more levels off. |
| Timezone & eligibility | 15 | 15 = worldwide or the user's region, with no overlap problem. 8 = partial overlap needed. 0 = unclear and probably restricted. |
| Domain | 10 | The user has worked in this industry or on this kind of product. |
| Salary | 10 | 10 = stated and at or above `salary_floor_usd`. 5 = not stated. 0 = stated and below the floor. |
| How recent | 10 | 10 = posted ≤ 2 days ago. 7 = ≤ 7 days. 4 = ≤ 14 days. 1 = older. |
| Company signals | 5 | Real company, clear process, salary stated, ATS or company email. 0 if any scam sign. |

Bands: **85+ apply today**, **70–84 strong**, **55–69 worth a try**, **below 55** list it but don't push it.

## For each ranked job write
- **Why it fits:** one line naming the 2–3 strongest matches, using the user's real experience (e.g. "4 yrs Flutter + shipped 2 fintech apps; they want Flutter + payments").
- **Gaps:** one line with what's missing or risky (e.g. "wants Kotlin native; 3h overlap with CET").
- **Posted:** the date and how long ago, e.g. `2026-10-13 (2d ago)`.

## JOBS.md format
```
# Remote jobs: <date>, last <days> days
Fetched <n> from <k> sources · kept <n> · ranked <n> · sources skipped: <list or none>

| # | Score | Title | Company | Source | Posted | Eligibility | Salary | Apply via | Contact | Why it fits | Gaps | ⚠ | Link |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 88 | Senior Flutter Developer | Acme | himalayas | 2026-10-14 (1d ago) | Worldwide | $60–80k | form | jobs@acme.com | ... | ... | | [job](url) |

## Apply today
<jobs scoring 85+ that were posted in the last 3 days>

## Filtered out
| Title | Company | Reason | Link |
```
- **Rows:** when ranked by score, ties go to the newer post. With `by date`, rows are sorted newest first and Score is still shown.
- **Contact:** the first email or apply link in the posting, or blank.
- **Link:** always the original job URL the source gave (Remote OK and Jobicy require this).
- **Source:** name it, so the user can see where each job came from.
