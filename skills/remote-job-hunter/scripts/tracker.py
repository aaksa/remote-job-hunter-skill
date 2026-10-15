#!/usr/bin/env python3
"""Keep tracker.csv: every job seen, its score and where the application stands.

    python3 -I tracker.py --tracker ~/.claude/remote-job-hunter/tracker.csv add <run>/jobs.json
    python3 -I tracker.py --tracker ... set <job id> applied --note "sent via Greenhouse"
    python3 -I tracker.py --tracker ... summary [--followup-days 14]
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime

FIELDS = ["id", "dedupe_key", "source", "title", "company", "url", "apply_url", "posted_at", "first_seen",
          "score", "how_to_apply", "contact", "status", "status_date", "notes"]
STATUSES = ["new", "shortlisted", "tailored", "applied", "interview", "offer", "rejected", "skipped"]


def today():
    return datetime.now().date().isoformat()  # the user's local date


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save(path, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def first_contact(job):
    for kind in ("email", "apply_link", "poster"):
        for c in job.get("contacts") or []:
            if c.get("type") == kind:
                return c.get("value", "")
    return ""


def cmd_add(path, jobs_file):
    rows = load(path)
    known = {r["id"] for r in rows}
    with open(jobs_file, encoding="utf-8") as f:
        data = json.load(f)
    added = 0
    for j in data["jobs"] if isinstance(data, dict) else data:
        if j["id"] in known:
            continue
        rows.append({"id": j["id"], "dedupe_key": j.get("dedupe_key", ""), "source": j.get("source", ""),
                     "title": j.get("title", ""), "company": j.get("company", ""), "url": j.get("url", ""),
                     "apply_url": j.get("apply_url", ""), "posted_at": j.get("posted_at", ""), "first_seen": today(),
                     "score": j.get("score", j.get("prescore", "")), "how_to_apply": j.get("how_to_apply", ""),
                     "contact": first_contact(j), "status": "new", "status_date": today(), "notes": ""})
        known.add(j["id"])
        added += 1
    save(path, rows)
    print("added %d, tracker now has %d jobs" % (added, len(rows)))


def set_status(path, job_id, status, note=""):
    """Change one job's status. Returns False if the job is not in the tracker or the status is unknown."""
    if status not in STATUSES:
        return False
    rows = load(path)
    for r in rows:
        if r["id"] == job_id:
            r["status"], r["status_date"] = status, today()
            if note:
                r["notes"] = (r["notes"] + " | " if r["notes"] else "") + "%s %s" % (today(), note)
            save(path, rows)
            return True
    return False


def cmd_set(path, job_id, status, note):
    if status not in STATUSES:
        sys.exit("status must be one of: " + ", ".join(STATUSES))
    if not set_status(path, job_id, status, note):
        sys.exit("no job with id %s in %s" % (job_id, path))
    print("%s -> %s" % (job_id, status))


def cmd_summary(path, followup_days):
    rows = load(path)
    counts = {s: 0 for s in STATUSES}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("jobs tracked: %d" % len(rows))
    print("  " + "  ".join("%s %d" % (s, n) for s, n in counts.items() if n))
    now = datetime.now().date()
    due = [r for r in rows if r["status"] == "applied" and r["status_date"]
           and (now - datetime.fromisoformat(r["status_date"]).date()).days >= followup_days]
    if due:
        print("\nno reply after %d+ days, follow up:" % followup_days)
        for r in due:
            print("  - %s at %s (applied %s) %s %s" % (r["title"], r["company"], r["status_date"], r["contact"], r["url"]))


def main(argv=None):
    p = argparse.ArgumentParser(description="Track remote job applications")
    p.add_argument("--tracker", default="~/.claude/remote-job-hunter/tracker.csv")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("jobs_file")
    s = sub.add_parser("set")
    s.add_argument("job_id")
    s.add_argument("status")
    s.add_argument("--note", default="")
    m = sub.add_parser("summary")
    m.add_argument("--followup-days", type=int, default=14)
    args = p.parse_args(argv)
    path = os.path.expanduser(args.tracker)
    if args.cmd == "add":
        cmd_add(path, args.jobs_file)
    elif args.cmd == "set":
        cmd_set(path, args.job_id, args.status, args.note)
    else:
        cmd_summary(path, args.followup_days)


if __name__ == "__main__":
    main()
