import sqlite3, uuid, smtplib, os
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, flash
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "office-hours-secret")

# ── Config (set these in Render Environment Variables) ────
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")

COURSES        = ["BTM 2000", "BTM 3850"]
INSTRUCTORS    = ["Tracy G", "Kerry G", "Sandip S"]
SLOT_HOURS     = list(range(10, 15))   # 10 AM → 2 PM start (ends 3 PM)
SEATS_PER_SLOT = 3

# ── Database ───────────────────────────────────────────────
DB = "bookings.db"

def get_db():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

with get_db() as _c:
    _c.execute("""CREATE TABLE IF NOT EXISTS bookings(
        id TEXT PRIMARY KEY, name TEXT, email TEXT, course TEXT,
        instructor TEXT, slot_date TEXT, slot_hour INTEGER,
        status TEXT DEFAULT 'confirmed', token TEXT,
        reminder_sent INTEGER DEFAULT 0, created_at TEXT)""")
    _c.commit()

# ── Helpers ────────────────────────────────────────────────
def slot_label(h):
    s = datetime.strptime(str(h),   "%H").strftime("%-I:%M %p")
    e = datetime.strptime(str(h+1), "%H").strftime("%-I:%M %p")
    return f"{s} – {e}"

def count(date, hour):
    with get_db() as c:
        return c.execute(
            "SELECT COUNT(*) FROM bookings WHERE slot_date=? AND slot_hour=? AND status='confirmed'",
            (date, hour)).fetchone()[0]

def by_token(token):
    with get_db() as c:
        r = c.execute("SELECT * FROM bookings WHERE token=?", (token,)).fetchone()
    return dict(r) if r else None

def slots_for(date, exclude_id=None):
    out = []
    for h in SLOT_HOURS:
        with get_db() as c:
            q = "SELECT COUNT(*) FROM bookings WHERE slot_date=? AND slot_hour=? AND status='confirmed'"
            args = [date, h]
            if exclude_id:
                q += " AND id!=?"
                args.append(exclude_id)
            n = c.execute(q, args).fetchone()[0]
        avail = SEATS_PER_SLOT - n
        out.append({"hour": h, "label": slot_label(h),
                    "avail": avail, "full": avail <= 0})
    return out

# ── Email ──────────────────────────────────────────────────
def send(to, subject, body):
    if not SMTP_USER:
        print(f"[EMAIL SKIPPED] {subject} → {to}")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = SMTP_USER
        msg["To"]      = to
        msg.attach(MIMEText(body, "html"))
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [to], msg.as_string())
        print(f"[EMAIL SENT] {subject}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")

def tr(k, v):
    return f'<tr><td style="padding:8px;border:1px solid #E5E7EB;background:#F9FAFB;width:120px"><b>{k}</b></td><td style="padding:8px;border:1px solid #E5E7EB">{v}</td></tr>'

def mail_confirm(b):
    url  = url_for("manage", token=b["token"], _external=True)
    lbl  = slot_label(b["slot_hour"])
    body = f"""<div style="font-family:sans-serif;max-width:500px;margin:0 auto">
<h2 style="color:#2563EB">✅ Booking Confirmed</h2>
<p>Hi <b>{b['name']}</b>, your office-hours slot is booked.</p>
<table style="border-collapse:collapse;width:100%;margin:16px 0">
{tr("Date", b['slot_date'])}{tr("Time", lbl)}{tr("Course", b['course'])}{tr("Instructor", b['instructor'])}
</table>
<a href="{url}" style="display:inline-block;padding:10px 20px;background:#2563EB;color:#fff;border-radius:6px;text-decoration:none">
Manage / Cancel Booking</a>
<p style="color:#6B7280;font-size:13px;margin-top:14px">You will receive a reminder 1 hour before your slot.</p>
</div>"""
    send(b["email"], "Office Hours — Booking Confirmed", body)

def mail_cancel(b):
    lbl  = slot_label(b["slot_hour"])
    home = url_for("index", _external=True)
    body = f"""<div style="font-family:sans-serif;max-width:500px;margin:0 auto">
<h2 style="color:#DC2626">❌ Booking Cancelled</h2>
<p>Hi <b>{b['name']}</b>, your slot on <b>{b['slot_date']}</b> at <b>{lbl}</b> has been cancelled.</p>
<a href="{home}" style="display:inline-block;padding:10px 20px;background:#2563EB;color:#fff;border-radius:6px;text-decoration:none">Book a New Slot</a>
</div>"""
    send(b["email"], "Office Hours — Booking Cancelled", body)

def mail_reminder(b):
    lbl  = slot_label(b["slot_hour"])
    body = f"""<div style="font-family:sans-serif;max-width:500px;margin:0 auto">
<h2 style="color:#D97706">⏰ Reminder: 1 Hour to Your Slot</h2>
<p>Hi <b>{b['name']}</b>, your office-hours session starts in <b>1 hour</b>.</p>
<p>📅 <b>{b['slot_date']}</b> at <b>{lbl}</b> with <b>{b['instructor']}</b></p>
</div>"""
    send(b["email"], "Reminder — Office Hours in 1 Hour", body)

# ── Reminder scheduler ─────────────────────────────────────
def check_reminders():
    now = datetime.now()
    with get_db() as c:
        rows = c.execute(
            "SELECT * FROM bookings WHERE status='confirmed' AND reminder_sent=0"
        ).fetchall()
    for r in rows:
        b = dict(r)
        slot_dt = datetime.strptime(f"{b['slot_date']} {b['slot_hour']}:00", "%Y-%m-%d %H:%M")
        diff = (slot_dt - now).total_seconds()
        if 0 < diff <= 3600:
            mail_reminder(b)
            with get_db() as c:
                c.execute("UPDATE bookings SET reminder_sent=1 WHERE id=?", (b["id"],))
                c.commit()

sched = BackgroundScheduler()
sched.add_job(check_reminders, "interval", minutes=5)
sched.start()

# ── Routes ─────────────────────────────────────────────────
@app.route("/")
def index():
    today = datetime.now().date().isoformat()
    date  = request.args.get("date", today)
    return render_template("index.html", date=date, today=today,
                           slots=slots_for(date), courses=COURSES, instructors=INSTRUCTORS)

@app.route("/book", methods=["POST"])
def book():
    name   = request.form.get("name", "").strip()
    email  = request.form.get("email", "").strip()
    course = request.form.get("course", "")
    instr  = request.form.get("instructor", "")
    date   = request.form.get("slot_date", "")
    h_str  = request.form.get("slot_hour", "")
    errs   = []
    if not name:                 errs.append("Name is required.")
    if "@" not in email:         errs.append("Valid email is required.")
    if course not in COURSES:    errs.append("Select a valid course.")
    if instr not in INSTRUCTORS: errs.append("Select a valid instructor.")
    if not h_str:                errs.append("Select a time slot.")
    hour = int(h_str) if h_str else 0
    if not errs and count(date, hour) >= SEATS_PER_SLOT:
        errs.append("That slot is now full — please pick another.")
    if errs:
        for e in errs: flash(e, "error")
        return redirect(url_for("index", date=date))
    bid = str(uuid.uuid4())
    tok = str(uuid.uuid4())
    with get_db() as c:
        c.execute("""INSERT INTO bookings
            (id,name,email,course,instructor,slot_date,slot_hour,token,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (bid, name, email, course, instr, date, hour, tok, datetime.now().isoformat()))
        c.commit()
    b = by_token(tok)
    mail_confirm(b)
    flash("Slot confirmed! Check your email for your manage link.", "success")
    return redirect(url_for("manage", token=tok))

@app.route("/manage/<token>")
def manage(token):
    b = by_token(token)
    if not b:
        flash("Booking not found.", "error")
        return redirect(url_for("index"))
    return render_template("manage.html", booking=b, label=slot_label(b["slot_hour"]))

@app.route("/edit/<token>", methods=["GET", "POST"])
def edit(token):
    b = by_token(token)
    if not b or b["status"] != "confirmed":
        flash("Booking not found or already cancelled.", "error")
        return redirect(url_for("index"))
    today = datetime.now().date().isoformat()
    date  = request.args.get("date", b["slot_date"])
    if request.method == "POST":
        nd   = request.form.get("slot_date", "")
        nh_s = request.form.get("slot_hour", "")
        nh   = int(nh_s) if nh_s else 0
        with get_db() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM bookings WHERE slot_date=? AND slot_hour=? AND status='confirmed' AND id!=?",
                (nd, nh, b["id"])).fetchone()[0]
        if n >= SEATS_PER_SLOT:
            flash("That slot is full. Pick another.", "error")
            return redirect(url_for("edit", token=token, date=nd))
        with get_db() as c:
            c.execute("""UPDATE bookings SET name=?,email=?,course=?,instructor=?,
                         slot_date=?,slot_hour=?,reminder_sent=0 WHERE id=?""",
                (request.form.get("name","").strip(), request.form.get("email","").strip(),
                 request.form.get("course",""), request.form.get("instructor",""),
                 nd, nh, b["id"]))
            c.commit()
        updated = by_token(token)
        mail_confirm(updated)
        flash("Booking updated! New confirmation email sent.", "success")
        return redirect(url_for("manage", token=token))
    return render_template("edit.html", booking=b, slots=slots_for(date, b["id"]),
                           date=date, today=today, courses=COURSES, instructors=INSTRUCTORS)

@app.route("/cancel/<token>", methods=["POST"])
def cancel(token):
    b = by_token(token)
    if not b or b["status"] != "confirmed":
        flash("Nothing to cancel.", "error")
        return redirect(url_for("index"))
    with get_db() as c:
        c.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (b["id"],))
        c.commit()
    mail_cancel(b)
    flash("Your booking has been cancelled.", "info")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=False)
