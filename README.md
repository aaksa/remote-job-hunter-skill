# The remote job hunter skill

Give Claude your resume once. It finds remote jobs that fit, shows when each was posted, ranks them by how well they
match, tailors your resume for the ones you pick, and fills in the application right up to the Submit button. You press
Submit.

```
/remote-job-hunter setup ~/Documents/my-resume.pdf
/remote-job-hunter search            # deep search, last 14 days, ranked by match
/remote-job-hunter search 3 by date  # last 3 days, newest first
/remote-job-hunter tailor 2          # tailored resume + cover letter + screening answers for job #2
/remote-job-hunter apply 2           # fill the form (or draft the email), stop before Submit
/remote-job-hunter status            # what you applied to, and who to follow up with
/remote-job-hunter board             # open the job board and apply with one click
```

## The job board

Every search also builds `.remote-job-hunter/board.html`, one page with every ranked job:

- **Apply ↗** opens the application form. A **✓ Mark applied** button then appears.
- **Email** / **Gmail** (jobs that ask for an email) open a ready draft in your mail app or Gmail, addressed to the
  posting's email, with the subject, your tailored cover letter and your signature. You attach the resume and press
  Send. The page never sends anything, so no password or API key is stored in it.
- A status menu (new → tailored → applied → interview → offer), tabs, search, and sort by best match or newest.
- **✨ Tailor resume** asks the Claude session that opened the board to tailor your resume for that job. The card goes
  Queued → Claude is tailoring → Ready, then links to the resume PDF/DOCX, the change list, screening answers and the cover
  letter, saved in that job's folder. The board shows a green dot while Claude is listening.

`/remote-job-hunter board` runs it at `http://127.0.0.1:8765` so status changes save straight to your tracker and
Tailor buttons reach Claude. You can
also double-click `board.html`; then statuses are kept in that browser only.

## Where it looks

| Group | Sources | How |
|---|---|---|
| Remote job boards | Remotive, Remote OK, We Work Remotely, Himalayas, Jobicy, Working Nomads | public APIs and RSS |
| Communities | Hacker News "Who is hiring?" | HN Algolia API |
| Hidden company job pages | Greenhouse, Lever, Ashby, Workable, SmartRecruiters | a sub-agent searches the web for remote roles at companies that never post on job boards; each company's whole board is then pulled through its public API |
| Community scout | matching HN comments and community job boards for your field (e.g. Python.org jobs, Flutter and game-dev job boards) | a second sub-agent |

The two sub-agents and the fetch script run at the same time. LinkedIn, Indeed, Glassdoor and Wellfound are not
scraped (they need a login and forbid it), and Reddit is skipped because it rate-limits after a few requests. Paste any job URL from them and the skill treats it like any other job.

## What you get

`.remote-job-hunter/<date>/JOBS.md`: a ranked table with **score, title, company, source, posted date ("2d ago"),
eligibility, salary, how to apply (form / email), contact, why it fits, gaps, scam warnings and the link**, plus an
"Apply today" list and a "Filtered out" list with reasons.

- **Matching:** a quick keyword prefilter cuts about 1,000 posts to 40. Claude then reads each one and scores it out of 100:
  skills 35, seniority 15, timezone and eligibility 15, domain 10, salary 10, how recent 10, company signals 5. Jobs you
  can't take from where you live (e.g. "US only"), hybrid or on-site roles, and scams are filtered out before ranking.
- **Contacts:** emails (including `jobs [at] company [dot] com`) and apply links, taken only from the posting itself. It never guesses email addresses or looks people up elsewhere.
- **Scam filter:** pay-to-work fees, crypto or check payments, reshipping, "no experience, $50/hr", Telegram-only contact,
  and Gmail addresses for "global companies". One sign gets a ⚠; two or more and the post is dropped.
- **Tailored resume:** reordered and reworded toward the job, in ATS-safe PDF + DOCX, with a `changes.md` that ties every
  line back to your original. Nothing is invented; missing skills are listed as gaps, not added.
- **Applying:** Claude fills Greenhouse, Lever, Ashby, Workable and other forms in the built-in browser and stops before
  Submit, logins, CAPTCHAs, consent boxes and demographic questions. For email jobs it drafts the email and sends only
  after you say yes to that exact email.
- **Tracker:** `~/.claude/remote-job-hunter/tracker.csv` remembers every job, so the next search shows only new ones.
  `status` lists applications with no reply after 14 days and drafts follow-ups.

Your resume, profile and tracker stay in `~/.claude/remote-job-hunter/`, outside this repo.

## Install

```bash
claude plugin marketplace add aaksa/remote-job-hunter-skill
```

```bash
claude plugin install remote-job-hunter-skill@remote-job-hunter-skill
```

Or copy `skills/remote-job-hunter/` into `~/.claude/skills/`.

## Requirements

- Python 3.9+ (standard library only)
- Claude Code with WebSearch (for the hidden company job pages), the built-in browser (for filling forms), and the `pdf` /
  `docx` skills (for the tailored resume files)

## Tests

```bash
python3 -m unittest discover tests
```

The tests run offline on small sample responses in `tests/fixtures/` that have the same shape as each real source.

## Limits

- Remotive's free API returns only a small, delayed set of jobs.
- Reddit is not searched: its API needs a login and its RSS feeds rate-limit after a few requests.
- Eligibility comes from what the posting says. "Anywhere in the World" on We Work Remotely sometimes hides a US-only
  requirement, so Claude reads the description before ranking.

## License

MIT
