# Applying

## Before anything
1. The job has tailored files in `<run>/<company-slug>/` (otherwise run `tailor` first).
2. Ask the user once for this application: "Fill the application for <title> at <company> with your details and tailored resume?" A yes covers filling the fields of **this** form only, not submitting and not other jobs.
3. Open the apply URL in the built-in browser: `preview_start` with `url`, then `read_page` / `find` to locate the fields. Prefer `form_input` with refs over clicking at pixel positions.

## Stop and hand over when you see
- a login, "create an account", "sign in with Google/LinkedIn", or a password field;
- a CAPTCHA, "I'm not a robot", or any bot check;
- terms, privacy consent or "I agree" boxes (the user ticks those);
- voluntary demographic or EEO questions (gender, race, veteran, disability), which are the user's choice;
- any request for payment, ID documents, bank details or a fee (also a scam sign);
- the final **Submit / Apply / Send** button.

When you stop, list: what you filled, what's left for the user, the file path to attach if upload didn't work, and any answer you were unsure of.

## Field map
| Field | Fill with |
|---|---|
| First / last / full name, email, phone, LinkedIn, GitHub, portfolio/website | `profile.md` contact details |
| Location / city / country | `country` (and city if in the profile) |
| Timezone | `timezone_utc` |
| Resume / CV upload | `<run>/<company-slug>/<Full-Name>-<Company>-Resume.pdf` |
| Cover letter (text box or upload) | `cover-letter.md` text, or a PDF of it |
| "Why do you want to work here?", custom questions | `answers.md`; leave `[needs your input]` ones empty and list them |
| Salary expectation | `answers.md`; leave empty if it is `[needs your input]` |
| Work authorization / visa / sponsorship | the profile only; never guess |
| "How did you hear about us?" | the source name (e.g. "Himalayas", "Hacker News", "We Work Remotely") |
| Start date / notice period | profile, or leave empty and list it |

### ATS notes
- **Greenhouse** (`job-boards.greenhouse.io/...`, `boards.greenhouse.io/...`): one page. Resume upload, or "enter manually" with a paste box. Custom questions are below the standard fields. Some boards add a CAPTCHA at Submit.
- **Lever** (`jobs.lever.co/<co>/<id>/apply`): one page. Resume upload first (it may autofill some fields; check them), then "Additional information" (put the cover letter there if there's no cover-letter box).
- **Ashby** (`jobs.ashbyhq.com/<co>/<id>/application`): one page. Resume upload may autofill fields; check them. Often has "Autofill from resume", which you should not use (fill from the profile instead).
- **Workable** (`apply.workable.com/...`): multi-step with "Next" buttons. Next is safe to click; the last step's Submit is not.
- **SmartRecruiters, Workday, iCIMS, Taleo, BambooHR:** these often need an account. Fill what you can without logging in, then hand over at the login.
- **Google Forms / Typeform:** fill the fields and stop before Submit.
- **Job board pages** (Remote OK, Jobicy, Himalayas, WWR, Working Nomads): these are listings, not forms. Follow their "Apply" link to the company's real form and continue there.

## Email applications
```
To: <email from the posting>
Subject: <follow the posting's rule exactly if it has one, else> Application: <Job title> (<Full name>, remote, UTC±X)

Hi <name if the posting gives one, else "<Company> team">,

<cover letter body, under 150 words>

Resume attached. Portfolio: <link>.

<Full name>
<email> · <phone> · <LinkedIn>
```
- Attach the tailored PDF.
- The board's **Email** / **Gmail** buttons open this draft directly in the user's mail; point the user to them first.
- Show the user the full email. Send only after a yes to **this** email, and only through a mail tool they have connected (load it with ToolSearch). Otherwise give them the text and the file path to send it themselves.
- One email per job. Never send the same email to many addresses.

## After sending
When the user confirms they submitted or sent it:
`python3 -I <skill>/scripts/tracker.py set <job id> applied --note "<form|email>"`.
