# Resume and cover letter rules

## The one rule
**Never invent.** Every skill, employer, title, date, number, tool and achievement in the tailored files must already be in `resume-master.md` or have been told to you by the user in this conversation. The job's wording may only replace the user's wording when both mean the same true thing. For example, the master says "built REST APIs in Express" and the job says "Node.js backend services": writing "built Node.js backend services (Express REST APIs)" is fine. Adding "GraphQL" because the job wants it is not.

If the job needs something the user lacks, leave it out of the resume and list it under Gaps. If the user says they do have it, add it and note "added by user on <date>" in `changes.md`.

## Tailoring moves (allowed)
- **Reorder:** the most relevant role, project and bullets go first. Move a matching side project above a less relevant job if the resume has a Projects section.
- **Cut:** drop bullets, skills and old roles that don't help this job. Keep it to 1 page under 7 years of experience, 2 pages at most otherwise.
- **Reword:** use the job's exact terms for things the user really did, so ATS keyword matching finds them (e.g. "CI/CD" vs "deployment pipelines").
- **Lead with results:** start bullets with a verb and keep the user's real numbers. If the master has no number, don't make one up.
- **Summary:** write 2–3 lines at the top, aimed at this role and built only from master facts. Include the target title if the user's experience supports it.
- **Skills section:** order it so the job's required skills the user has come first.
- **Remote signals:** if the master shows remote work, timezone overlap, async tools or distributed teams, make them visible. Many remote employers check for this.

## ATS-safe format
- One column. No tables, text boxes, icons, photos, or skill bars.
- Standard headings: Summary, Experience, Projects, Skills, Education, Certifications.
- Dates as `Mon YYYY – Mon YYYY` and the same format everywhere.
- Contact line at the top: name, email, phone, city + country, timezone (e.g. "Jakarta, Indonesia · UTC+7 · open to remote"), LinkedIn, portfolio/GitHub.
- Standard fonts. Export to PDF (to upload) and DOCX (some ATSs parse it better) with the `pdf` and `docx` skills.
- File name: `<Full-Name>-<Company>-Resume.pdf`.

## changes.md
One line per change:
```
- [reworded] "Built Node.js backend services (Express REST APIs)" ← master: "Built REST APIs in Express" (Experience › Acme)
- [moved] Project "Kasir app" above Experience › Beta Corp
- [cut] Skill "Photoshop"
- [summary] built from: Experience › Acme bullets 1–2, Skills
```
Before showing the files, read `changes.md` against the master yourself. Delete anything that doesn't trace back.

## Cover letter (cover-letter.md)
- Under 200 words. No "I am writing to apply for…", no "I'm excited to…", and no "passionate, dynamic, results-driven" filler.
- **Line 1:** the strongest proof that matches their main need, with a real number or link from the master.
- **Middle:** 2–3 short sentences pairing their top needs with things the user has done, in the job's words.
- **Remote line:** the user's timezone and real overlap hours with the team, plus any remote/async experience.
- **Close:** one line asking for a call, then the portfolio/GitHub link if the master has one.
- If the posting asks for something specific (a word in the subject line, an answer to a question, a link), do it exactly. It is often a test of whether applicants read the post.
- Address a named person only if the posting names them.

## Screening answers (answers.md)
- Answer every question in order. Mark each one `[from resume]` or `[needs your input]`.
- Salary expectation: use `salary_floor_usd` and the job's range if both exist. Otherwise mark `[needs your input]`.
- Work authorization, visa and relocation: answer only from the profile. Never claim a permit the user doesn't have.
- "Why us": 2 sentences about something specific in the posting or on the company's site, not general praise.
