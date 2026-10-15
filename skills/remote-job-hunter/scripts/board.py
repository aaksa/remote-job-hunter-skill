#!/usr/bin/env python3
"""Build the job board: one HTML page with every ranked job, its status and one-click apply buttons.

    python3 -I board.py build --runs .remote-job-hunter            # writes .remote-job-hunter/board.html
    python3 -I board.py serve --runs .remote-job-hunter --port 8765 # same page; status changes save to tracker.csv

The page reads every `<run>/jobs.json`, the tracker and any tailored files in `<run>/<company-slug>/`.
Form jobs get an Apply button that opens the application page. Email jobs get Email and Gmail buttons that
open a pre-filled draft in the user's own mail; nothing is ever sent from the page.
"""
import argparse
import glob
import html
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_jobs  # noqa: E402
import tracker  # noqa: E402

HOME = os.path.expanduser("~/.claude/remote-job-hunter")
SOURCE_NAMES = {"himalayas": "Himalayas", "workingnomads": "Working Nomads", "remotive": "Remotive",
                "weworkremotely": "We Work Remotely", "hn": "Hacker News", "web": "Web", "greenhouse": "Greenhouse",
                "ashby": "Ashby", "jobicy": "Jobicy", "lever": "Lever", "remoteok": "Remote OK"}


def slug(company):
    """The folder name `tailor` uses for a company: lowercase, anything else becomes '-'."""
    return re.sub(r"[^a-z0-9]+", "-", (company or "job").lower()).strip("-") or "job"


def safe_url(u):
    return u if isinstance(u, str) and re.match(r"^(https?:|mailto:)", u, re.I) else ""


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def strip_md(text):
    """Cover letters are Markdown; mail drafts want plain text."""
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text).strip()


def first(contacts, kind):
    return next((c["value"] for c in contacts or [] if c.get("type") == kind and c.get("value")), "")


def mail_draft(job, profile, cover):
    name = profile.get("full_name", "")
    subject = "Application: %s%s" % (job["title"], " (%s)" % name if name else "")
    greeting = "Hi %s team," % job["company"] if job["company"] else "Hi,"
    if cover:
        body = cover if cover.lower().startswith(("hi", "hello", "dear")) else greeting + "\n\n" + cover
    else:
        body = "%s\n\nI'd like to apply for the %s role I saw on %s. My resume is attached.\n" % (
            greeting, job["title"], SOURCE_NAMES.get(job["source"], job["source"]))
    sign = "\n".join(x for x in (name, profile.get("email", ""), profile.get("phone", ""),
                                 profile.get("linkedin", ""), profile.get("portfolio", "")) if x)
    body = body.rstrip()
    if sign and name not in body[-200:]:
        body += "\n\n" + sign
    return subject, body


TAILOR_FILES = [("resume", "Resume PDF", ".pdf"), ("resume", "Resume DOCX", ".docx"), ("changes", "Changes", ".md"),
                ("answers", "Answers", ".md"), ("cover-letter", "Cover letter", ".md")]
SERVABLE = {".pdf": "application/pdf", ".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
LISTENER_FRESH = 20  # seconds: how recent the session's heartbeat must be to count as listening


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_path(runs_dir):
    return os.path.join(runs_dir, "tailor-state.json")


def queue_path(runs_dir):
    return os.path.join(runs_dir, "tailor-queue.jsonl")


def heartbeat_path(runs_dir):
    return os.path.join(runs_dir, ".listener")


def load_state(runs_dir):
    try:
        with open(state_path(runs_dir), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(runs_dir, state):
    tmp = state_path(runs_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, state_path(runs_dir))


def set_tailor_state(runs_dir, job_id, state, msg=""):
    st = load_state(runs_dir)
    st[job_id] = {"state": state, "at": now_iso(), "msg": msg[:300]}
    save_state(runs_dir, st)


def listening(runs_dir):
    try:
        return datetime.now().timestamp() - os.path.getmtime(heartbeat_path(runs_dir)) < LISTENER_FRESH
    except OSError:
        return False


def tailor_files(runs_dir, folder):
    """Links to the tailored files in a job's folder, as paths relative to the runs folder."""
    found = []
    for stem, label, ext in TAILOR_FILES:
        hits = sorted(p for p in glob.glob(os.path.join(folder, "*" + ext)) if stem in os.path.basename(p).lower())
        if hits:
            found.append({"label": label, "path": os.path.relpath(hits[0], runs_dir).replace(os.sep, "/")})
    return found


def load_jobs(runs_dir, tracker_path, profile):
    jobs = {}
    for run in sorted(d for d in glob.glob(os.path.join(runs_dir, "*")) if os.path.isdir(d)):
        path = os.path.join(run, "jobs.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for j in data["jobs"] if isinstance(data, dict) else data:
            j["_run"] = run
            jobs[j["id"]] = j  # a later run replaces an earlier one
    rows = {r["id"]: r for r in tracker.load(tracker_path)} if tracker_path else {}
    state = load_state(runs_dir)
    out = []
    for j in jobs.values():
        contacts = j.get("contacts") or []
        folder = os.path.join(j["_run"], slug(j.get("company")))
        cover = strip_md(read_text(os.path.join(folder, "cover-letter.md")))
        pdfs = sorted(glob.glob(os.path.join(folder, "*.pdf")))
        resume = next((p for p in pdfs if "resume" in os.path.basename(p).lower()), pdfs[0] if pdfs else "")
        email = first(contacts, "email")
        how = j.get("how_to_apply") or "link"
        apply_link = first(contacts, "apply_link")
        apply_url = apply_link if j.get("source") in ("hn", "web") and apply_link else (j.get("apply_url") or j.get("url"))
        tstate = state.get(j["id"], {})
        rec = {
            "id": j["id"], "rank": j.get("rank"), "score": j.get("score", j.get("prescore")),
            "title": j.get("title", ""), "company": j.get("company", ""), "source": j.get("source", ""),
            "sourceName": SOURCE_NAMES.get(j.get("source"), j.get("source", "")), "posted": j.get("posted_at") or "",
            "eligibility": j.get("eligibility_display") or j.get("eligibility_note") or "",
            "salary": j.get("salary_display") or j.get("salary_text") or "", "how": how,
            "why": j.get("why", ""), "gaps": j.get("gaps", ""), "flags": j.get("scam_flags") or [],
            "url": safe_url(j.get("url")), "applyUrl": safe_url(apply_url), "email": email,
            "run": os.path.basename(j["_run"]), "status": "new", "statusDate": "", "notes": "",
            "cover": cover, "resume": os.path.abspath(resume) if resume else "",
            "folder": os.path.abspath(folder) if os.path.isdir(folder) else "",
            "files": tailor_files(runs_dir, folder) if os.path.isdir(folder) else [],
            "tailor": tstate.get("state") or ("done" if cover else ""), "tailorMsg": tstate.get("msg", ""),
        }
        row = rows.get(j["id"])
        if row:
            rec.update(status=row.get("status") or "new", statusDate=row.get("status_date", ""), notes=row.get("notes", ""))
        if email:
            subject, body = mail_draft(rec, profile, cover)
            q = urllib.parse.quote
            rec["mailto"] = "mailto:%s?subject=%s&body=%s" % (q(email, safe="@"), q(subject), q(body))
            rec["gmail"] = "https://mail.google.com/mail/?view=cm&fs=1&to=%s&su=%s&body=%s" % (
                q(email, safe="@"), q(subject), q(body))
            rec["subject"], rec["body"] = subject, body
        out.append(rec)
    out.sort(key=lambda r: (-(r["score"] or 0), r["posted"]), reverse=False)
    return out


def board_data(runs_dir, tracker_path, profile_path):
    profile = fetch_jobs.load_profile(profile_path) if profile_path and os.path.exists(os.path.expanduser(profile_path)) else {}
    return profile, {"jobs": load_jobs(runs_dir, tracker_path, profile), "name": profile.get("full_name", ""),
                     "statuses": tracker.STATUSES, "built": now_iso(), "listening": listening(runs_dir)}


def render(runs_dir, tracker_path, profile_path):
    profile, data = board_data(runs_dir, tracker_path, profile_path)
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    title = "Job board" + (" · " + html.escape(profile.get("full_name", "")) if profile.get("full_name") else "")
    return PAGE.replace("{{TITLE}}", title).replace("{{DATA}}", blob)


def build(args):
    out = args.out or os.path.join(args.runs, "board.html")
    page = render(args.runs, args.tracker, args.profile)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(os.path.abspath(out))
    return out


def request_tailor(runs_dir, tracker_path, profile_path, job_id):
    """Queue a tailor request for the listening Claude session. Returns the new state, or None for an unknown job."""
    _, data = board_data(runs_dir, tracker_path, profile_path)
    job = next((j for j in data["jobs"] if j["id"] == job_id), None)
    if not job:
        return None
    cur = load_state(runs_dir).get(job_id, {}).get("state")
    if cur in ("queued", "working"):
        return cur
    entry = {"id": job_id, "title": job["title"], "company": job["company"], "run": job["run"],
             "folder": os.path.join(job["run"], slug(job["company"])), "at": now_iso()}
    with open(queue_path(runs_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    set_tailor_state(runs_dir, job_id, "queued")
    return "queued"


def make_server(args):
    """The local server: the board, status saves, tailor requests and the tailored files."""
    runs, trk, prof = args.runs, args.tracker, args.profile
    runs_real = os.path.realpath(runs)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype, extra=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False), "application/json")

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html", "/board.html"):
                self._send(200, render(runs, trk, prof), "text/html; charset=utf-8")
            elif path == "/api/ping":
                self._json(200, {"ok": True})
            elif path == "/api/data":
                self._json(200, board_data(runs, trk, prof)[1])
            elif path == "/api/state":
                self._json(200, {"listening": listening(runs), "state": load_state(runs)})
            elif path.startswith("/files/"):
                rel = urllib.parse.unquote(path[len("/files/"):])
                full = os.path.realpath(os.path.join(runs_real, rel))
                ext = os.path.splitext(full)[1].lower()
                if not full.startswith(runs_real + os.sep) or ext not in SERVABLE or not os.path.isfile(full):
                    return self._send(404, "not found", "text/plain")
                with open(full, "rb") as f:
                    self._send(200, f.read(), SERVABLE[ext],
                               {"Content-Disposition": 'inline; filename="%s"' % os.path.basename(full).replace('"', "")})
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            # JSON only: a browser can't send this cross-site without a CORS preflight, which we never allow.
            path = urllib.parse.urlparse(self.path).path
            if path not in ("/api/status", "/api/tailor") or \
                    not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(400, {"ok": False})
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 10000)
                msg = json.loads(self.rfile.read(n))
                job_id = str(msg["id"])
            except (ValueError, KeyError):
                return self._json(400, {"ok": False})
            if path == "/api/status":
                ok = tracker.set_status(trk, job_id, str(msg.get("status", "")), str(msg.get("note", ""))[:200])
                return self._json(200 if ok else 404, {"ok": ok})
            got = request_tailor(runs, trk, prof, job_id)
            self._json(200 if got else 404, {"ok": bool(got), "state": got, "listening": listening(runs)})

        def log_message(self, *a):
            pass

    return ThreadingHTTPServer(("127.0.0.1", args.port), Handler)


def serve(args):
    httpd = make_server(args)
    print("job board: http://127.0.0.1:%d  (Ctrl+C to stop)" % httpd.server_address[1], flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return httpd


def listen(args):
    """For the Claude session: print one line per tailor request, including any still waiting, and keep a heartbeat
    so the board can show that Claude is listening. Run it under the Monitor tool."""
    seen = set()
    for job_id, st in load_state(args.runs).items():
        if st.get("state") == "queued":
            seen.add(job_id)
            print("TAILOR %s" % job_id, flush=True)
    qpath = queue_path(args.runs)
    pos = os.path.getsize(qpath) if os.path.exists(qpath) else 0
    while True:
        with open(heartbeat_path(args.runs), "w") as f:
            f.write(now_iso())
        if os.path.exists(qpath) and os.path.getsize(qpath) > pos:
            with open(qpath, encoding="utf-8") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            for line in chunk.splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if load_state(args.runs).get(entry["id"], {}).get("state") == "queued":
                    print("TAILOR %s | %s | %s | folder %s" % (entry["id"], entry["title"], entry["company"],
                                                                 entry["folder"]), flush=True)
        if args.once:
            return
        time.sleep(1)


def main(argv=None):
    p = argparse.ArgumentParser(description="Build or serve the job board")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("build", "serve", "listen", "tailor-state"):
        s = sub.add_parser(name)
        s.add_argument("--runs", default=".remote-job-hunter", help="folder holding the dated run folders")
        s.add_argument("--tracker", default=os.path.join(HOME, "tracker.csv"))
        s.add_argument("--profile", default=os.path.join(HOME, "profile.md"))
        if name == "build":
            s.add_argument("--out", help="default: <runs>/board.html")
        elif name == "serve":
            s.add_argument("--port", type=int, default=8765)
        elif name == "listen":
            s.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
        else:
            s.add_argument("job_id")
            s.add_argument("state", choices=["queued", "working", "done", "error"])
            s.add_argument("--msg", default="")
    args = p.parse_args(argv)
    args.tracker = os.path.expanduser(args.tracker)
    if args.cmd == "build":
        return build(args)
    if args.cmd == "serve":
        return serve(args)
    if args.cmd == "listen":
        return listen(args)
    set_tailor_state(args.runs, args.job_id, args.state, args.msg)
    print("%s -> %s" % (args.job_id, args.state))


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;--accent:#2f5d50;--accent-ink:#fff;
--hi:#1f7a4d;--good:#2f6f8f;--mid:#a86a12;--low:#8a8984;--warn:#a2401c;--chip:#efede7}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;--muted:#a3a29b;--line:#33322f;
--accent:#7fb8a4;--accent-ink:#10201b;--hi:#5cc08b;--good:#6fb3d6;--mid:#e0a64a;--low:#8f8e88;--warn:#ec8a62;--chip:#2a2a27}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
.wrap{max-width:1080px;margin:0 auto;padding:0 16px}
.top{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:18px 0 6px;flex-wrap:wrap}
h1{font-size:20px;margin:0;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 0 12px}
.tabs{display:flex;gap:4px;flex-wrap:wrap}
.tab{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:5px 12px;font:inherit;font-size:13px;cursor:pointer}
.tab[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.tab b{font-weight:600;margin-left:4px;opacity:.7}
input[type=search],select{font:inherit;font-size:13px;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
input[type=search]{flex:1;min-width:160px}
.note{margin:12px 0 0;padding:8px 12px;border-radius:8px;background:var(--chip);color:var(--muted);font-size:13px}
main{padding:14px 0 60px}
.job{display:grid;grid-template-columns:56px 1fr auto;gap:14px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px}
.score{width:48px;height:48px;border-radius:10px;display:grid;place-items:center;font-weight:700;font-size:17px;color:#fff}
.s-hi{background:var(--hi)}.s-good{background:var(--good)}.s-mid{background:var(--mid)}.s-low{background:var(--low)}
.rank{display:block;text-align:center;color:var(--muted);font-size:12px;margin-top:4px}
.title{font-weight:600;font-size:16px;margin:0}
.title a{color:inherit;text-decoration:none}.title a:hover{text-decoration:underline}
.meta{color:var(--muted);font-size:13px;margin:2px 0 6px;display:flex;flex-wrap:wrap;gap:4px 12px}
.why,.gaps{margin:2px 0;font-size:14px}
.why::before{content:"Why: ";font-weight:600}
.gaps::before{content:"Gaps: ";font-weight:600;color:var(--warn)}
.flag{display:inline-block;margin-top:6px;font-size:12px;color:var(--warn);border:1px solid currentColor;border-radius:6px;padding:1px 6px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.chip{font-size:12px;background:var(--chip);border-radius:6px;padding:2px 8px;color:var(--muted)}
.actions{display:flex;flex-direction:column;gap:6px;align-items:stretch;min-width:170px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;font:inherit;font-size:13px;font-weight:600;padding:7px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);text-decoration:none;cursor:pointer;white-space:nowrap}
.btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.btn.done{background:var(--hi);border-color:var(--hi);color:#fff}
.btn:hover{filter:brightness(1.05)}
.row2{display:flex;gap:6px}.row2 .btn{flex:1}
.btn.tailor{border-color:var(--accent);color:var(--accent)}
.btn[disabled]{opacity:.7;cursor:default}
.files{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.file{font-size:12px;font-weight:600;color:var(--accent);border:1px solid var(--line);border-radius:6px;padding:2px 8px;text-decoration:none}
.file:hover{border-color:var(--accent)}
.err{color:var(--warn)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--low)}.dot.on{background:var(--hi)}
@keyframes pulse{50%{opacity:.45}}.busy{animation:pulse 1.4s ease-in-out infinite}
.status{width:100%}
.st-applied .score,.st-interview .score,.st-offer .score{outline:3px solid var(--hi);outline-offset:2px}
.st-rejected,.st-skipped{opacity:.55}
.empty{color:var(--muted);text-align:center;padding:40px}
.toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:8px 14px;border-radius:8px;font-size:13px;opacity:0;transition:opacity .2s;pointer-events:none}
.toast.on{opacity:1}
@media (max-width:720px){header{position:static}h1{font-size:18px}.job{grid-template-columns:48px 1fr}.actions{grid-column:1/-1;flex-direction:row;flex-wrap:wrap;min-width:0}.actions>*{flex:1 1 140px}}
</style>
</head>
<body>
<header><div class="wrap">
  <div class="top"><h1 id="h"></h1><span class="sub" id="built"></span></div>
  <div class="bar">
    <div class="tabs" id="tabs"></div>
    <input type="search" id="q" placeholder="Search title, company, skill…" aria-label="Search jobs">
    <select id="sort" aria-label="Sort"><option value="score">Best match</option><option value="date">Newest</option></select>
  </div>
</div></header>
<div class="wrap"><p class="note" id="mode"></p><main id="list"></main></div>
<div class="toast" id="toast" role="status"></div>
<script id="data" type="application/json">{{DATA}}</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const KEY = 'rjh-status';
const GROUPS = [
  ['todo', 'To apply', s => ['new','shortlisted','tailored'].includes(s)],
  ['applied', 'Applied', s => s === 'applied'],
  ['interview', 'Interview / offer', s => ['interview','offer'].includes(s)],
  ['closed', 'Skipped / rejected', s => ['skipped','rejected'].includes(s)],
  ['all', 'All', s => true],
];
let tab = 'todo', server = false, local = {};
try { local = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { local = {}; }
const $ = id => document.getElementById(id);
const el = (tag, attrs = {}, kids = []) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'text') n.textContent = v; else if (k === 'on') for (const [ev, f] of Object.entries(v)) n.addEventListener(ev, f);
    else n.setAttribute(k, v);
  }
  for (const k of [].concat(kids)) if (k) n.append(k);
  return n;
};
const status = j => (!server && local[j.id]) ? local[j.id].status : j.status;
function ago(iso) {
  if (!iso) return '';
  const d = Math.floor((Date.now() - new Date(iso)) / 864e5);
  return iso.slice(0, 10) + ' · ' + (d <= 0 ? 'today' : d + 'd ago');
}
function toast(msg) { const t = $('toast'); t.textContent = msg; t.classList.add('on'); setTimeout(() => t.classList.remove('on'), 1800); }
async function setStatus(j, s, note) {
  if (server) {
    try {
      const r = await fetch('/api/status', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: j.id, status: s, note: note || ''})});
      if (!r.ok) throw new Error();
      j.status = s; toast('Saved to tracker: ' + s);
    } catch (e) { toast('Could not save. Is board.py serve still running?'); }
  } else {
    local[j.id] = {status: s, at: new Date().toISOString()};
    try { localStorage.setItem(KEY, JSON.stringify(local)); } catch (e) {}
    toast('Saved in this browser: ' + s);
  }
  draw();
}
async function copy(text, what) {
  try { await navigator.clipboard.writeText(text); toast(what + ' copied'); }
  catch (e) { prompt('Copy ' + what, text); }
}
function band(s) { return s >= 85 ? 's-hi' : s >= 70 ? 's-good' : s >= 55 ? 's-mid' : 's-low'; }
function card(j) {
  const s = status(j);
  const acts = [];
  if (j.how === 'email' && j.email) {
    acts.push(el('div', {class: 'row2'}, [
      el('a', {class: 'btn primary', href: j.mailto, title: 'Opens a draft in your mail app', on: {click: () => after(j)}, text: 'Email'}),
      el('a', {class: 'btn primary', href: j.gmail, target: '_blank', rel: 'noopener', title: 'Opens a Gmail draft', on: {click: () => after(j)}, text: 'Gmail'}),
    ]));
    acts.push(el('button', {class: 'btn', type: 'button', on: {click: () => copy(j.email, 'Email address')}, text: 'Copy ' + j.email}));
  } else if (j.applyUrl) {
    acts.push(el('a', {class: 'btn primary', href: j.applyUrl, target: '_blank', rel: 'noopener', on: {click: () => after(j)}, text: 'Apply ↗'}));
  }
  if (server) {
    const busy = {queued: '⏳ Queued for Claude…', working: '✍️ Claude is tailoring…'}[j.tailor];
    acts.push(busy
      ? el('button', {class: 'btn tailor busy', type: 'button', disabled: '', text: busy})
      : el('button', {class: 'btn tailor', type: 'button', on: {click: () => tailor(j)}, text: j.tailor === 'done' ? '↻ Re-tailor' : '✨ Tailor resume'}));
  }
  if (j.cover) acts.push(el('button', {class: 'btn', type: 'button', on: {click: () => copy(j.cover, 'Cover letter')}, text: 'Copy cover letter'}));
  if (j.resume) {
    const pdf = (j.files || []).find(f => f.label === 'Resume PDF');
    acts.push(el('button', {class: 'btn', type: 'button', title: 'Copies ' + j.resume + ' and opens the PDF in a new tab', on: {click: () => {
      if (pdf) window.open(fileUrl(pdf.path), '_blank', 'noopener');  // open first, while the click still counts
      copy(j.resume, 'Resume path');
    }}, text: pdf ? 'Copy path & open resume ↗' : 'Copy resume path'}));
  }
  if (j._justApplied && !['applied','interview','offer'].includes(s))
    acts.push(el('button', {class: 'btn done', type: 'button', on: {click: () => setStatus(j, 'applied', j.how)}, text: '✓ Mark applied'}));
  const sel = el('select', {class: 'status', 'aria-label': 'Status', on: {change: e => setStatus(j, e.target.value)}},
    D.statuses.map(x => { const o = el('option', {value: x, text: x[0].toUpperCase() + x.slice(1)}); if (x === s) o.selected = true; return o; }));
  acts.push(sel);
  const chips = [];
  if (!server && !j.cover && ['new','shortlisted'].includes(s)) chips.push(el('span', {class: 'chip', text: 'Not tailored yet: ask Claude "tailor ' + (j.rank || '') + '"'}));
  if (j.tailor === 'queued' && !listeningNow) chips.push(el('span', {class: 'chip', text: 'Waiting: ask Claude to "open the job board" so it starts listening'}));
  if (j.tailor === 'error') chips.push(el('span', {class: 'chip err', text: 'Tailoring failed' + (j.tailorMsg ? ': ' + j.tailorMsg : '')}));
  if (j.cover) chips.push(el('span', {class: 'chip', text: 'Tailored ✓'}));
  if (j.notes) chips.push(el('span', {class: 'chip', text: j.notes}));
  return el('article', {class: 'job st-' + s}, [
    el('div', {}, [el('div', {class: 'score ' + band(j.score || 0), text: String(j.score ?? '–')}), j.rank ? el('span', {class: 'rank', text: '#' + j.rank}) : null]),
    el('div', {}, [
      el('h2', {class: 'title'}, [j.url ? el('a', {href: j.url, target: '_blank', rel: 'noopener', text: j.title}) : j.title]),
      el('div', {class: 'meta'}, [el('span', {text: j.company}), el('span', {text: j.sourceName}), el('span', {text: ago(j.posted)}),
        j.eligibility ? el('span', {text: '🌏 ' + j.eligibility}) : null, j.salary ? el('span', {text: '💰 ' + j.salary}) : null,
        el('span', {text: j.how === 'email' ? '✉ email' : j.how === 'form' ? '📝 form' : '🔗 link'})]),
      j.why ? el('p', {class: 'why', text: j.why}) : null,
      j.gaps ? el('p', {class: 'gaps', text: j.gaps}) : null,
      j.flags.length ? el('span', {class: 'flag', text: '⚠ ' + j.flags.join(', ').replaceAll('_', ' ')}) : null,
      chips.length ? el('div', {class: 'chips'}, chips) : null,
      j.files && j.files.length ? el('div', {class: 'files'}, j.files.map(f =>
        el('a', {class: 'file', href: fileUrl(f.path), target: '_blank', rel: 'noopener', text: f.label}))) : null,
    ]),
    el('div', {class: 'actions'}, acts),
  ]);
}
function fileUrl(path) { return (server ? '/files/' : '') + path.split('/').map(encodeURIComponent).join('/'); }
function after(j) { j._justApplied = true; setTimeout(draw, 300); }
let listeningNow = !!D.listening;
async function tailor(j) {
  try {
    const r = await fetch('/api/tailor', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: j.id})});
    const res = await r.json();
    if (!res.ok) throw new Error();
    j.tailor = res.state; listeningNow = res.listening;
    toast(res.listening ? 'Sent to Claude' : 'Queued: Claude will start when it is listening');
  } catch (e) { toast('Could not queue. Is the board server running?'); }
  draw();
}
async function poll() {
  try {
    const r = await (await fetch('/api/state')).json();
    let changed = r.listening !== listeningNow;
    listeningNow = r.listening;
    for (const j of D.jobs) {
      const st = (r.state[j.id] || {}).state;
      if (st && st !== j.tailor) changed = true;
    }
    if (changed) {
      const fresh = await (await fetch('/api/data')).json();
      const keep = Object.fromEntries(D.jobs.filter(j => j._justApplied).map(j => [j.id, true]));
      D.jobs = fresh.jobs.map(j => Object.assign(j, keep[j.id] ? {_justApplied: true} : {}));
      showMode(); draw();
    }
  } catch (e) {}
}
function showMode() {
  const m = $('mode'); m.replaceChildren();
  if (server) {
    m.append(el('span', {class: 'dot' + (listeningNow ? ' on' : '')}),
      listeningNow ? 'Claude is listening: Tailor buttons work. ' : 'Claude is not listening: Tailor requests wait until you ask Claude to "open the job board". ',
      'Status changes save to your tracker. Email buttons open a draft; attach your resume and press Send yourself.');
  } else {
    m.textContent = 'Opened as a file: status changes are saved in this browser only, and Tailor needs the live board. Ask Claude to "open the job board". Email buttons open a draft; attach your resume and press Send yourself.';
  }
}
function draw() {
  const q = $('q').value.trim().toLowerCase();
  const tabs = $('tabs'); tabs.replaceChildren();
  for (const [key, label, test] of GROUPS) {
    const n = D.jobs.filter(j => test(status(j))).length;
    tabs.append(el('button', {class: 'tab', type: 'button', 'aria-pressed': String(key === tab), on: {click: () => { tab = key; draw(); }}}, [label, el('b', {text: String(n)})]));
  }
  const test = GROUPS.find(g => g[0] === tab)[2];
  let list = D.jobs.filter(j => test(status(j)) && (!q || (j.title + ' ' + j.company + ' ' + j.why + ' ' + j.gaps + ' ' + j.sourceName).toLowerCase().includes(q)));
  list.sort($('sort').value === 'date' ? (a, b) => (b.posted || '').localeCompare(a.posted || '') : (a, b) => (b.score || 0) - (a.score || 0) || (b.posted || '').localeCompare(a.posted || ''));
  $('list').replaceChildren(...(list.length ? list.map(card) : [el('p', {class: 'empty', text: 'Nothing here.'})]));
}
$('h').textContent = 'Remote job board' + (D.name ? ' · ' + D.name : '');
$('built').textContent = D.jobs.length + ' jobs · updated ' + new Date(D.built).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'});
$('q').addEventListener('input', draw); $('sort').addEventListener('change', draw);
(async () => {
  if (location.protocol.startsWith('http')) {
    try { server = (await fetch('/api/ping')).ok; } catch (e) {}
  }
  showMode();
  draw();
  if (server) setInterval(poll, 3000);
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
