#!/usr/bin/env python3
"""
Vinya outreach mailer
Personalized, throttled cold email to agricultural producers, sent through Gmail
as luis@hinosinvestments.com, with CAN-SPAM compliance and deliverability safeguards.

Commands:
  preview          Render every pending email into ./previews (sends nothing)
  send             Send to contacts not yet emailed, respecting the daily cap
  check-replies    Scan the inbox for STOP replies and bounces; suppress them
  optout EMAIL     Manually add an address to the suppression list
  stats            Show totals

Password: set the environment variable GMAIL_APP_PASSWORD (a Google App Password,
not your normal Gmail password). It is never stored in this file.
"""

import argparse
import csv
import email
import hashlib
import html
import imaplib
import os
import random
import re
import smtplib
import ssl
import sys
import time
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path

# ============================ CONFIGURATION ============================
CONFIG = {
    # Sending route. "gmail" = log in to Gmail and send as your verified alias.
    # If mail-tester.com shows SPF/DKIM/DMARC failing or "via gmail.com",
    # switch to "godaddy" and fill the godaddy_* values below.
    "route": "gmail",

    "gmail_user": "luisenrique.hinostroza@gmail.com",
    "gmail_smtp": ("smtp.gmail.com", 465),
    "gmail_imap": "imap.gmail.com",

    "godaddy_user": "luis@hinosinvestments.com",
    # Microsoft 365 from GoDaddy: ("smtp.office365.com", 587)
    # GoDaddy Professional Email: ("smtpout.secureserver.net", 465)
    "godaddy_smtp": ("smtpout.secureserver.net", 465),
    "godaddy_imap": "imap.secureserver.net",

    "password_env": "GMAIL_APP_PASSWORD",

    "from_name": "Luis Hinostroza",
    "from_addr": "luis@hinosinvestments.com",
    "reply_to": "luis@hinosinvestments.com",
    "msgid_domain": "hinosinvestments.com",

    # REQUIRED by U.S. CAN-SPAM law: a valid postal address where you receive mail
    # (street address, P.O. Box, or registered virtual mailbox). Sending is blocked
    # until this is filled in.
    "postal_address": "537 Hardwood Circle, Orlando, FL 32828",

    # Deliverability safeguards
    "daily_cap": 15,
    "min_delay_s": 90,        # random pause between emails
    "max_delay_s": 240,
    "weekdays_only": True,
}

FREE_MAIL = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com",
    "msn.com", "live.com", "comcast.net", "att.net", "verizon.net", "sbcglobal.net",
    "bellsouth.net", "charter.net", "cox.net", "earthlink.net", "me.com", "ymail.com",
    "protonmail.com", "frontier.com", "centurylink.net", "windstream.net",
}
DEFAULT_REASON = ("You're receiving this because your business contact information "
                  "is publicly listed as an agricultural producer.")
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")

BASE = Path(__file__).resolve().parent
TEMPLATE = BASE / "email_template.txt"
SUBJECTS = BASE / "subjects.txt"
SENT_LOG = BASE / "sent_log.csv"
SUPPRESS = BASE / "suppression.csv"
PREVIEWS = BASE / "previews"


# ============================ DATA FILES ============================
def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def append_csv(path, row, fields):
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def suppressed_set():
    return {r["email"].strip().lower() for r in read_csv(SUPPRESS) if r.get("email")}


def add_suppression(addr, reason):
    addr = addr.strip().lower()
    if addr and addr not in suppressed_set():
        append_csv(SUPPRESS, {"email": addr, "reason": reason,
                              "date": datetime.now().isoformat(timespec="seconds")},
                   ["email", "reason", "date"])
        return True
    return False


def sent_rows():
    return read_csv(SENT_LOG)


def sent_emails():
    return {r["email"].lower() for r in sent_rows() if r.get("status") == "sent"}


def sent_domains():
    doms = set()
    for e in sent_emails():
        d = e.split("@")[-1]
        if d not in FREE_MAIL:
            doms.add(d)
    return doms


def sent_today_count():
    today = date.today().isoformat()
    return sum(1 for r in sent_rows()
               if r.get("status") == "sent" and r.get("timestamp", "").startswith(today))


# ============================ CONTACTS ============================
def load_contacts(path, state=None):
    rows = read_csv(Path(path))
    seen, out, skipped = set(), [], []
    for r in rows:
        c = {k: (r.get(k) or "").strip() for k in
             ["business", "contact_name", "email", "state", "crop", "personal_note", "source"]}
        c["email"] = c["email"].lower()
        if not c["business"] or not EMAIL_RE.match(c["email"]):
            skipped.append((c["email"] or "(blank)", "missing business or invalid email"))
            continue
        if c["email"] in seen:
            skipped.append((c["email"], "duplicate in file"))
            continue
        if state and c["state"].upper() != state.upper():
            continue
        seen.add(c["email"])
        out.append(c)
    return out, skipped


def pending_queue(contacts, allow_same_domain=False):
    sup, done, doms = suppressed_set(), sent_emails(), sent_domains()
    queue, run_doms, skipped = [], set(), []
    for c in contacts:
        e = c["email"]
        d = e.split("@")[-1]
        if e in sup:
            skipped.append((e, "suppressed"))
            continue
        if e in done:
            skipped.append((e, "already emailed"))
            continue
        if not allow_same_domain and d not in FREE_MAIL and (d in doms or d in run_doms):
            skipped.append((e, "someone at this company was already contacted"))
            continue
        run_doms.add(d)
        queue.append(c)
    return queue, skipped


# ============================ RENDERING ============================
def pick_subject(c, subjects):
    h = int(hashlib.md5(c["email"].encode()).hexdigest(), 16)
    return subjects[h % len(subjects)].format(business=c["business"])


def personal_line(c):
    if c["personal_note"]:
        return "\n" + c["personal_note"] + "\n"
    if c["crop"]:
        return (f"\nFor an operation growing {c['crop']}, the same approach applies to lots "
                f"in cold storage, packing, and transit.\n")
    return ""


def render(c, template, subjects):
    greeting = c["contact_name"] or f"{c['business']} team"
    reason = (f"You're receiving this because {c['business']} is listed as {c['source']}."
              if c["source"] else DEFAULT_REASON)
    body = template.format(
        greeting=greeting,
        personal_line=personal_line(c),
        postal_address=CONFIG["postal_address"] or "[POSTAL ADDRESS REQUIRED]",
        footer_reason=reason,
    )
    body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    return pick_subject(c, subjects), body


def to_html(text):
    main, _, footer = text.partition("\n---\n")

    def fmt(block):
        esc = html.escape(block)
        esc = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', esc)
        esc = re.sub(r"([\w.+-]+@[\w-]+\.[\w.]+)", r'<a href="mailto:\1">\1</a>', esc)
        paras = [p.replace("\n", "<br>") for p in esc.strip().split("\n\n")]
        return "".join(f"<p>{p}</p>" for p in paras)

    out = ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
           'line-height:1.5;color:#222;">' + fmt(main))
    if footer:
        out += ('<hr style="border:none;border-top:1px solid #ddd;margin:20px 0;">'
                '<div style="font-size:11px;color:#777;">' + fmt(footer) + "</div>")
    return out + "</div>"


def build_message(c, subject, body):
    m = EmailMessage()
    m["From"] = formataddr((CONFIG["from_name"], CONFIG["from_addr"]))
    m["To"] = formataddr((c["contact_name"] or c["business"], c["email"]))
    m["Reply-To"] = CONFIG["reply_to"]
    m["Subject"] = subject
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=CONFIG["msgid_domain"])
    m["List-Unsubscribe"] = f"<mailto:{CONFIG['reply_to']}?subject=STOP>"
    m.set_content(body)
    m.add_alternative(to_html(body), subtype="html")
    return m


# ============================ CONNECTIONS ============================
def creds():
    pw = os.environ.get(CONFIG["password_env"])
    if not pw:
        sys.exit(f"Set the {CONFIG['password_env']} environment variable first (see README).")
    if CONFIG["route"] == "godaddy":
        return CONFIG["godaddy_user"], pw, CONFIG["godaddy_smtp"], CONFIG["godaddy_imap"]
    return CONFIG["gmail_user"], pw, CONFIG["gmail_smtp"], CONFIG["gmail_imap"]


def smtp_connect():
    user, pw, (host, port), _ = creds()
    ctx = ssl.create_default_context()
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=60)
    else:
        s = smtplib.SMTP(host, port, timeout=60)
        s.starttls(context=ctx)
    s.login(user, pw)
    return s


# ============================ COMMANDS ============================
def cmd_preview(args):
    contacts, bad = load_contacts(args.contacts, args.state)
    queue, skipped = pending_queue(contacts, args.allow_same_domain)
    template = TEMPLATE.read_text(encoding="utf-8")
    subjects = [s for s in SUBJECTS.read_text(encoding="utf-8").splitlines() if s.strip()]
    PREVIEWS.mkdir(exist_ok=True)
    for i, c in enumerate(queue, 1):
        subject, body = render(c, template, subjects)
        safe = re.sub(r"[^A-Za-z0-9]+", "_", c["business"])[:40]
        (PREVIEWS / f"{i:03d}_{safe}.txt").write_text(
            f"To: {c['email']}\nSubject: {subject}\n\n{body}", encoding="utf-8")
    print(f"{len(queue)} previews written to {PREVIEWS}")
    for e, why in bad + skipped:
        print(f"  skipped {e}: {why}")
    if not CONFIG["postal_address"]:
        print("\nWARNING: postal_address is empty. Fill it in before sending (required by law).")


def cmd_send(args):
    if not CONFIG["postal_address"]:
        sys.exit("postal_address is empty in CONFIG. A valid postal address is required by CAN-SPAM.")
    if CONFIG["weekdays_only"] and date.today().weekday() >= 5 and not args.force:
        sys.exit("Today is a weekend. Business outreach performs and delivers better on weekdays "
                 "(use --force to override).")

    contacts, bad = load_contacts(args.contacts, args.state)
    queue, skipped = pending_queue(contacts, args.allow_same_domain)
    remaining = CONFIG["daily_cap"] - sent_today_count()
    if args.limit:
        remaining = min(remaining, args.limit)
    if remaining <= 0:
        sys.exit(f"Daily cap of {CONFIG['daily_cap']} already reached today.")
    queue = queue[:remaining]
    if not queue:
        sys.exit("Nothing to send: every contact is already emailed or suppressed.")

    template = TEMPLATE.read_text(encoding="utf-8")
    subjects = [s for s in SUBJECTS.read_text(encoding="utf-8").splitlines() if s.strip()]
    print(f"{'DRY RUN - ' if args.dry_run else ''}Sending {len(queue)} email(s) "
          f"(cap {CONFIG['daily_cap']}/day, {sent_today_count()} already sent today)")

    server = None if args.dry_run else smtp_connect()
    fields = ["timestamp", "email", "business", "state", "subject", "status", "detail", "message_id"]

    for i, c in enumerate(queue, 1):
        subject, body = render(c, template, subjects)
        msg = build_message(c, subject, body)
        status, detail = "sent", ""
        if args.dry_run:
            status = "dry-run"
        else:
            for attempt in (1, 2):
                try:
                    server.send_message(msg)
                    break
                except smtplib.SMTPServerDisconnected:
                    if attempt == 2:
                        status, detail = "error", "disconnected twice"
                    else:
                        server = smtp_connect()
                except smtplib.SMTPRecipientsRefused as e:
                    status, detail = "refused", str(e)[:200]
                    add_suppression(c["email"], "refused by server")
                    break
                except smtplib.SMTPResponseException as e:
                    append_csv(SENT_LOG, {"timestamp": datetime.now().isoformat(timespec="seconds"),
                                          "email": c["email"], "business": c["business"],
                                          "state": c["state"], "subject": subject, "status": "error",
                                          "detail": f"{e.smtp_code} {e.smtp_error!r}"[:200],
                                          "message_id": msg["Message-ID"]}, fields)
                    sys.exit(f"Server returned {e.smtp_code}. Stopping the run to protect your "
                             f"sending reputation. Details: {e.smtp_error!r}")

        append_csv(SENT_LOG, {"timestamp": datetime.now().isoformat(timespec="seconds"),
                              "email": c["email"], "business": c["business"], "state": c["state"],
                              "subject": subject, "status": status, "detail": detail,
                              "message_id": msg["Message-ID"]}, fields)
        print(f"  [{i}/{len(queue)}] {status}: {c['business']} <{c['email']}>")

        if i < len(queue) and not args.dry_run:
            pause = random.uniform(CONFIG["min_delay_s"], CONFIG["max_delay_s"])
            print(f"      waiting {pause:.0f}s")
            time.sleep(pause)

    if server:
        server.quit()
    print("Done.")


def _text_of(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    for p in parts:
        if p.get_content_type() == "text/plain" and not p.get("Content-Disposition"):
            raw = p.get_payload(decode=True) or b""
            return raw.decode(p.get_content_charset() or "utf-8", errors="replace")
    return ""


def _fresh_reply(text):
    lines = []
    for ln in text.splitlines():
        if ln.startswith(">") or re.match(r"^On .+wrote:$", ln.strip()):
            break
        lines.append(ln)
    return "\n".join(lines)[:600]


def cmd_check_replies(args):
    user, pw, _, imap_host = creds()
    targets = sent_emails()
    since = (date.today() - timedelta(days=args.days)).strftime("%d-%b-%Y")
    M = imaplib.IMAP4_SSL(imap_host)
    M.login(user, pw)
    M.select("INBOX", readonly=True)
    _, data = M.search(None, f"(SINCE {since})")
    stops = bounces = replies = 0
    for num in data[0].split():
        _, msgdata = M.fetch(num, "(RFC822)")
        m = email.message_from_bytes(msgdata[0][1])
        frm = parseaddr(m.get("From", ""))[1].lower()
        subj = str(make_header(decode_header(m.get("Subject", ""))))
        text = _text_of(m)
        if "mailer-daemon" in frm or "postmaster" in frm:
            failed = m.get("X-Failed-Recipients", "")
            found = {a.strip().lower() for a in failed.split(",") if a.strip()}
            found |= {e for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text.lower()) if e in targets}
            for a in found:
                if add_suppression(a, "bounce"):
                    bounces += 1
                    print(f"  bounce -> suppressed {a}")
        elif frm in targets:
            fresh = _fresh_reply(text)
            if re.search(r"\b(stop|unsubscribe|remove me|opt[\s-]?out|no thanks)\b",
                         subj + "\n" + fresh, re.I):
                if add_suppression(frm, "requested stop"):
                    stops += 1
                    print(f"  STOP -> suppressed {frm}")
            else:
                replies += 1
                print(f"  REPLY from {frm}: {subj}")
    M.logout()
    print(f"\n{replies} reply(ies), {stops} stop request(s), {bounces} bounce(s) in the last {args.days} days.")


def cmd_test(args):
    template = TEMPLATE.read_text(encoding="utf-8")
    subjects = [s for s in SUBJECTS.read_text(encoding="utf-8").splitlines() if s.strip()]
    c = {"business": "Example Family Farms", "contact_name": "", "email": args.email.lower(),
         "state": "", "crop": "strawberries", "personal_note": "", "source": ""}
    subject, body = render(c, template, subjects)
    server = smtp_connect()
    server.send_message(build_message(c, subject, body))
    server.quit()
    print(f"Test email sent to {args.email}")


def cmd_optout(args):
    ok = add_suppression(args.email, "manual")
    print("Suppressed." if ok else "Already suppressed.")


def cmd_stats(args):
    rows = sent_rows()
    sent = [r for r in rows if r.get("status") == "sent"]
    print(f"Sent total:        {len(sent)}")
    print(f"Sent today:        {sent_today_count()} / cap {CONFIG['daily_cap']}")
    print(f"Refused/errors:    {sum(1 for r in rows if r.get('status') in ('refused', 'error'))}")
    sup = read_csv(SUPPRESS)
    print(f"Suppressed:        {len(sup)}")
    bounces = sum(1 for r in sup if r.get("reason") in ("bounce", "refused by server"))
    if sent:
        rate = 100 * bounces / len(sent)
        print(f"Bounce rate:       {rate:.1f}%" + ("   <-- above 2%: clean your list before sending more"
                                                   if rate > 2 else ""))


def main():
    p = argparse.ArgumentParser(description="Vinya outreach mailer")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("preview", "send"):
        s = sub.add_parser(name)
        s.add_argument("--contacts", default=str(BASE / "contacts.csv"))
        s.add_argument("--state", help="only contacts in this state, e.g. CA")
        s.add_argument("--allow-same-domain", action="store_true",
                       help="allow emailing a second person at the same company")
        if name == "send":
            s.add_argument("--limit", type=int, help="send at most N in this run")
            s.add_argument("--dry-run", action="store_true", help="simulate without sending")
            s.add_argument("--force", action="store_true", help="allow sending on weekends")

    s = sub.add_parser("check-replies")
    s.add_argument("--days", type=int, default=14)
    s = sub.add_parser("test")
    s.add_argument("email")
    s = sub.add_parser("optout")
    s.add_argument("email")
    sub.add_parser("stats")

    args = p.parse_args()
    {"preview": cmd_preview, "send": cmd_send, "check-replies": cmd_check_replies,
     "test": cmd_test, "optout": cmd_optout, "stats": cmd_stats}[args.cmd](args)


if __name__ == "__main__":
    main()
