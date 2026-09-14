import sqlite3, uuid, os, urllib.request, urllib.error, json, threading
from datetime import datetime
from flask import Flask, request, redirect, url_for, flash, get_flashed_messages
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "office-hours-secret")

COURSES        = ["BTM 2000", "BTM 3850"]
INSTRUCTORS    = ["Tracy G", "Kerry G", "Sandip S"]
SLOT_HOURS     = list(range(10, 15))
SEATS_PER_SLOT = 3
DB             = "bookings.db"

# Lab staff who receive ALL booking notifications
LAB_EMAILS = [
    os.environ.get("LAB_EMAIL_1", ""),   # you
    os.environ.get("LAB_EMAIL_2", ""),   # your partner
]
LAB_EMAILS = [e for e in LAB_EMAILS if e]  # remove blanks

# ── Database ───────────────────────────────────────────────
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
    s = datetime.strptime(str(h),   "%H").strftime("%I:%M %p").lstrip("0")
    e = datetime.strptime(str(h+1), "%H").strftime("%I:%M %p").lstrip("0")
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
                q += " AND id!=?"; args.append(exclude_id)
            n = c.execute(q, args).fetchone()[0]
        avail = SEATS_PER_SLOT - n
        out.append({"hour": h, "label": slot_label(h), "avail": avail, "full": avail <= 0})
    return out

def flash_html():
    msgs = get_flashed_messages(with_categories=True)
    if not msgs: return ""
    styles = {"success": "#DCFCE7;color:#15803D;border:1px solid #86efac",
              "error":   "#FEE2E2;color:#DC2626;border:1px solid #fca5a5",
              "info":    "#FEF3C7;color:#D97706;border:1px solid #fcd34d"}
    html = '<ul style="list-style:none;margin-bottom:20px;display:flex;flex-direction:column;gap:8px">'
    for cat, msg in msgs:
        st = styles.get(cat, styles["info"])
        html += f'<li style="padding:12px 16px;border-radius:10px;font-size:14px;font-weight:500;background:{st}">{msg}</li>'
    return html + "</ul>"

# ── Shared CSS & layout ────────────────────────────────────
CSS = """
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#F3F5F8;--surface:#fff;--border:#DDE2EB;--text:#111827;--muted:#6B7280;
      --accent:#2563EB;--accent-h:#1D4ED8;--accent-l:#EFF6FF;
      --success:#15803D;--success-l:#DCFCE7;--danger:#DC2626;--danger-l:#FEE2E2;--warn:#D97706}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Inter",sans-serif;background:var(--bg);color:var(--text);font-size:15px;line-height:1.6;min-height:100vh}
a{color:var(--accent);text-decoration:none}
header{background:var(--surface);border-bottom:1px solid var(--border);padding:0 32px;height:60px;
       display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:50}
.logo{font-family:"DM Serif Display",serif;font-size:20px}
.dot{display:inline-block;width:9px;height:9px;background:var(--accent);border-radius:50%;
     margin-right:8px;vertical-align:middle;position:relative;top:-1px}
main{max-width:820px;margin:0 auto;padding:40px 24px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;
      padding:32px;box-shadow:0 1px 8px rgba(0,0,0,.08);margin-bottom:24px}
.card-title{font-family:"DM Serif Display",serif;font-size:24px;margin-bottom:6px}
.card-sub{color:var(--muted);font-size:14px;margin-bottom:24px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:580px){.form-grid{grid-template-columns:1fr}}
.fg{display:flex;flex-direction:column;gap:5px}
label{font-size:13px;font-weight:500;color:var(--muted)}
input,select{padding:10px 13px;border:1.5px solid var(--border);border-radius:8px;
             background:var(--surface);color:var(--text);font-family:inherit;
             font-size:14px;outline:none;transition:border-color .15s;width:100%}
input:focus,select:focus{border-color:var(--accent)}
.slots-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:10px;margin-bottom:24px}
.slot-card{border:1.5px solid var(--border);border-radius:9px;padding:14px 12px;cursor:pointer;
           transition:all .15s;text-align:left;background:var(--surface);display:block}
.slot-card:hover:not(.full){border-color:var(--accent);background:var(--accent-l)}
.slot-card.selected{border-color:var(--accent);background:var(--accent-l);
                    box-shadow:0 0 0 3px rgba(37,99,235,.15)}
.slot-card.full{opacity:.5;cursor:not-allowed;background:var(--bg)}
input[type=radio].sr{display:none}
.slot-time{font-weight:600;font-size:14px}
.slot-avail{font-size:12px;margin-top:3px;color:var(--muted)}
.slot-card.full .slot-avail{color:var(--danger)}
.btn{display:inline-flex;align-items:center;gap:7px;padding:10px 22px;border:none;
     border-radius:8px;font-family:inherit;font-size:14px;font-weight:600;
     cursor:pointer;transition:all .15s;text-decoration:none}
.btn-primary{background:var(--accent);color:#fff}.btn-primary:hover{background:var(--accent-h)}
.btn-ghost{background:transparent;border:1.5px solid var(--border);color:var(--muted)}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}
.btn-danger{background:var(--danger);color:#fff}
.badge{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:600}
.badge-ok{background:var(--success-l);color:var(--success)}
.badge-no{background:var(--danger-l);color:var(--danger)}
.divider{height:1px;background:var(--border);margin:20px 0}
.detail-row{display:flex;gap:8px;margin-bottom:8px;font-size:14px}
.detail-label{font-weight:500;min-width:110px;color:var(--muted)}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:24px}
</style>"""

def layout(title, body, extra_js=""):
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>{CSS}</head>
<body>
<header>
  <div class="logo"><span class="dot"></span>Office Hours Booker</div>
  <nav><a href="/" style="font-size:13px;font-weight:500;color:var(--muted)">Book a Slot</a></nav>
</header>
<main>{flash_html()}{body}</main>
{extra_js}
</body></html>"""

# ── Email ──────────────────────────────────────────────────
def _send_worker(to, subject, body):
    """Send email via Resend API (works on Render free tier)."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    sender  = os.environ.get("RESEND_FROM", "")
    if not api_key or not sender:
        print(f"[EMAIL SKIPPED] RESEND_API_KEY or RESEND_FROM not set.")
        return
    try:
        payload = json.dumps({
            "from":    sender,
            "to":      [to],
            "subject": subject,
            "html":    body
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type":  "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
            print(f"[EMAIL SENT] {subject} → {to} | id: {resp.get('id')}")
    except urllib.error.HTTPError as e:
        print(f"[EMAIL ERROR] HTTP {e.code}: {e.read().decode()}")
    except Exception as e:
        print(f"[EMAIL ERROR] {type(e).__name__}: {e}")

def send(to, subject, body):
    threading.Thread(target=_send_worker, args=(to, subject, body), daemon=True).start()

def tr(k, v):
    return (f'<tr><td style="padding:8px;border:1px solid #E5E7EB;background:#F9FAFB;'
            f'width:120px"><b>{k}</b></td>'
            f'<td style="padding:8px;border:1px solid #E5E7EB">{v}</td></tr>')

def mail_confirm(b):
    url = url_for("manage", token=b["token"], _external=True)
    lbl = slot_label(b["slot_hour"])
    body = (f'<div style="font-family:sans-serif;max-width:500px;margin:0 auto">'
            f'<h2 style="color:#2563EB">✅ Booking Confirmed</h2>'
            f'<p>Hi <b>{b["name"]}</b>, your office-hours slot is booked.</p>'
            f'<table style="border-collapse:collapse;width:100%;margin:16px 0">'
            f'{tr("Date",b["slot_date"])}{tr("Time",lbl)}{tr("Course",b["course"])}{tr("Instructor",b["instructor"])}'
            f'</table><a href="{url}" style="display:inline-block;padding:10px 20px;'
            f'background:#2563EB;color:#fff;border-radius:6px;text-decoration:none">'
            f'Manage / Cancel Booking</a>'
            f'<p style="color:#6B7280;font-size:13px;margin-top:14px">You will receive a reminder 1 hour before your slot.</p>'
            f'</div>')
    send(b["email"], "Office Hours — Booking Confirmed", body)

def mail_lab_staff(b, action="new"):
    """Email both lab staff members about every booking or cancellation."""
    if not LAB_EMAILS:
        print("[EMAIL SKIPPED] No lab emails set in environment variables.")
        return
    lbl   = slot_label(b["slot_hour"])
    icon  = "🔔 New Booking" if action == "new" else "❌ Booking Cancelled"
    color = "#2563EB"        if action == "new" else "#DC2626"
    subj  = f"New Booking – {b['name']} ({b['course']})" if action == "new" else f"Cancelled – {b['name']} ({b['course']})"
    body  = (f'<div style="font-family:sans-serif;max-width:500px;margin:0 auto">'
             f'<h2 style="color:{color}">{icon}</h2>'
             f'<table style="border-collapse:collapse;width:100%;margin:16px 0">'
             f'{tr("Student",    b["name"])}'
             f'{tr("Email",      b["email"])}'
             f'{tr("Course",     b["course"])}'
             f'{tr("Instructor", b["instructor"])}'
             f'{tr("Date",       b["slot_date"])}'
             f'{tr("Time",       lbl)}'
             f'</table></div>')
    for addr in LAB_EMAILS:
        send(addr, subj, body)

def mail_cancel(b):
    lbl = slot_label(b["slot_hour"])
    home = url_for("index", _external=True)
    body = (f'<div style="font-family:sans-serif;max-width:500px;margin:0 auto">'
            f'<h2 style="color:#DC2626">❌ Booking Cancelled</h2>'
            f'<p>Hi <b>{b["name"]}</b>, your slot on <b>{b["slot_date"]}</b> at <b>{lbl}</b> has been cancelled.</p>'
            f'<a href="{home}" style="display:inline-block;padding:10px 20px;background:#2563EB;'
            f'color:#fff;border-radius:6px;text-decoration:none">Book a New Slot</a></div>')
    send(b["email"], "Office Hours — Booking Cancelled", body)

def mail_reminder(b):
    lbl = slot_label(b["slot_hour"])
    body = (f'<div style="font-family:sans-serif;max-width:500px;margin:0 auto">'
            f'<h2 style="color:#D97706">⏰ Reminder: 1 Hour to Your Slot</h2>'
            f'<p>Hi <b>{b["name"]}</b>, your session starts in <b>1 hour</b>.</p>'
            f'<p>📅 <b>{b["slot_date"]}</b> at <b>{lbl}</b> with <b>{b["instructor"]}</b></p></div>')
    send(b["email"], "Reminder — Office Hours in 1 Hour", body)

# ── Reminder scheduler ─────────────────────────────────────
def check_reminders():
    now = datetime.now()
    with get_db() as c:
        rows = c.execute(
            "SELECT * FROM bookings WHERE status='confirmed' AND reminder_sent=0").fetchall()
    for r in rows:
        b = dict(r)
        slot_dt = datetime.strptime(f"{b['slot_date']} {b['slot_hour']}:00", "%Y-%m-%d %H:%M")
        if 0 < (slot_dt - now).total_seconds() <= 3600:
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
    sl    = slots_for(date)

    slot_cards = ""
    for s in sl:
        full_cls = "full" if s["full"] else ""
        seat_txt = "Full" if s["full"] else f'{s["avail"]} seat{"s" if s["avail"]!=1 else ""} left'
        dis      = "disabled" if s["full"] else ""
        slot_cards += (f'<label class="slot-card {full_cls}" id="lbl-{s["hour"]}">'
                       f'<input type="radio" class="sr" name="sp" value="{s["hour"]}" {dis} onchange="pick({s["hour"]})">'
                       f'<div class="slot-time">{s["label"]}</div>'
                       f'<div class="slot-avail">{seat_txt}</div></label>')

    course_opts = "".join(f'<option value="{c}">{c}</option>' for c in COURSES)
    instr_opts  = "".join(f'<option value="{i}">{i}</option>' for i in INSTRUCTORS)

    body = f"""
<div class="card">
  <div class="card-title">Book an Office Hours Slot</div>
  <div class="card-sub">Pick a date, choose an open slot (max 3 students per hour), and fill in your details.</div>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px;flex-wrap:wrap">
    <label for="dp" style="font-size:13px;font-weight:500;color:var(--muted)">Date</label>
    <input type="date" id="dp" value="{date}" min="{today}"
           style="padding:9px 12px;border:1.5px solid var(--border);border-radius:8px;
                  font-family:inherit;font-size:14px;outline:none;max-width:200px;width:auto">
  </div>
  <p style="font-size:13px;font-weight:500;color:var(--muted);margin-bottom:12px">Slots for {date}</p>
  <div class="slots-grid">{slot_cards}</div>
  <div class="divider"></div>
  <form method="POST" action="/book" id="bf">
    <input type="hidden" name="slot_date" value="{date}">
    <input type="hidden" name="slot_hour" id="sh" value="">
    <div class="form-grid">
      <div class="fg"><label>Full Name</label>
        <input type="text" name="name" placeholder="e.g. Maria Chen" required></div>
      <div class="fg"><label>Email Address</label>
        <input type="email" name="email" placeholder="you@university.edu" required></div>
      <div class="fg"><label>Course</label>
        <select name="course" required>
          <option value="">Select course...</option>{course_opts}</select></div>
      <div class="fg"><label>Instructor</label>
        <select name="instructor" required>
          <option value="">Select instructor...</option>{instr_opts}</select></div>
    </div>
    <div style="margin-top:24px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <button type="submit" class="btn btn-primary" id="sb" disabled>Confirm Booking</button>
      <span id="hint" style="font-size:13px;color:var(--muted)">Select a time slot above to continue.</span>
    </div>
  </form>
</div>"""

    js = """<script>
document.getElementById("dp").addEventListener("change",function(){
  window.location.href="/?date="+this.value;});
function pick(h){
  document.querySelectorAll(".slot-card").forEach(c=>c.classList.remove("selected"));
  var l=document.getElementById("lbl-"+h);
  if(l&&!l.classList.contains("full")){
    l.classList.add("selected");
    document.getElementById("sh").value=h;
    document.getElementById("sb").disabled=false;
    document.getElementById("hint").textContent="Slot selected — fill in your details and confirm.";
  }
}
document.getElementById("bf").addEventListener("submit",function(e){
  if(!document.getElementById("sh").value){
    e.preventDefault();
    var h=document.getElementById("hint");
    h.style.color="var(--danger)";h.textContent="Please select a time slot first.";}});
</script>"""
    return layout("Book Office Hours", body, js)


@app.route("/book", methods=["POST"])
def book():
    name   = request.form.get("name","").strip()
    email  = request.form.get("email","").strip()
    course = request.form.get("course","")
    instr  = request.form.get("instructor","")
    date   = request.form.get("slot_date","")
    h_str  = request.form.get("slot_hour","")
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
            (bid,name,email,course,instr,date,hour,tok,datetime.now().isoformat()))
        c.commit()
    b = by_token(tok)
    mail_confirm(b)
    mail_lab_staff(b, "new")
    flash("Slot confirmed! Check your email for your manage link.", "success")
    return redirect(url_for("manage", token=tok))


@app.route("/manage/<token>")
def manage(token):
    b = by_token(token)
    if not b:
        flash("Booking not found.", "error")
        return redirect(url_for("index"))
    lbl    = slot_label(b["slot_hour"])
    status = "Confirmed" if b["status"]=="confirmed" else "Cancelled"
    badge  = "badge-ok"  if b["status"]=="confirmed" else "badge-no"
    actions = ""
    if b["status"] == "confirmed":
        actions = f"""<div class="divider"></div>
<div class="actions">
  <a href="/edit/{b['token']}" class="btn btn-ghost">Edit / Rebook</a>
  <form method="POST" action="/cancel/{b['token']}"
        onsubmit="return confirm('Cancel this booking?')">
    <button type="submit" class="btn btn-danger">Cancel Booking</button>
  </form>
</div>"""
    else:
        actions = '<div style="margin-top:20px"><a href="/" class="btn btn-primary">Book a New Slot</a></div>'

    body = f"""
<div class="card">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">
    <div class="card-title" style="margin-bottom:0">Your Booking</div>
    <span class="badge {badge}">{status}</span>
  </div>
  <div class="card-sub">Your office-hours details. Edit or cancel below.</div>
  <div class="detail-row"><span class="detail-label">Name</span><span>{b['name']}</span></div>
  <div class="detail-row"><span class="detail-label">Email</span><span>{b['email']}</span></div>
  <div class="detail-row"><span class="detail-label">Course</span><span>{b['course']}</span></div>
  <div class="detail-row"><span class="detail-label">Instructor</span><span>{b['instructor']}</span></div>
  <div class="detail-row"><span class="detail-label">Date</span><span>{b['slot_date']}</span></div>
  <div class="detail-row"><span class="detail-label">Time</span><span>{lbl}</span></div>
  {actions}
</div>
<p style="font-size:13px;color:var(--muted)">Bookmark this page to manage your booking any time.</p>"""
    return layout("Your Booking", body)


@app.route("/edit/<token>", methods=["GET","POST"])
def edit(token):
    b = by_token(token)
    if not b or b["status"] != "confirmed":
        flash("Booking not found or already cancelled.", "error")
        return redirect(url_for("index"))
    today = datetime.now().date().isoformat()
    date  = request.args.get("date", b["slot_date"])

    if request.method == "POST":
        nd   = request.form.get("slot_date","")
        nh_s = request.form.get("slot_hour","")
        nh   = int(nh_s) if nh_s else 0
        with get_db() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM bookings WHERE slot_date=? AND slot_hour=? AND status='confirmed' AND id!=?",
                (nd,nh,b["id"])).fetchone()[0]
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
        mail_confirm(by_token(token))
        flash("Booking updated! New confirmation email sent.", "success")
        return redirect(url_for("manage", token=token))

    sl = slots_for(date, b["id"])
    slot_cards = ""
    for s in sl:
        is_cur   = s["hour"]==b["slot_hour"] and date==b["slot_date"]
        full_cls = "full" if s["full"] else ""
        sel_cls  = "selected" if is_cur else ""
        dis      = "disabled" if s["full"] else ""
        chk      = "checked"  if is_cur else ""
        seat_txt = "Full" if s["full"] else f'{s["avail"]} seats left'
        slot_cards += (f'<label class="slot-card {full_cls} {sel_cls}" id="lbl-{s["hour"]}">'
                       f'<input type="radio" class="sr" name="sp" value="{s["hour"]}" {dis} {chk} onchange="pick({s["hour"]})">'
                       f'<div class="slot-time">{s["label"]}</div>'
                       f'<div class="slot-avail">{seat_txt}</div></label>')

    course_opts = "".join(
        f'<option value="{c}" {"selected" if c==b["course"] else ""}>{c}</option>' for c in COURSES)
    instr_opts  = "".join(
        f'<option value="{i}" {"selected" if i==b["instructor"] else ""}>{i}</option>' for i in INSTRUCTORS)
    sh_val = b["slot_hour"] if date == b["slot_date"] else ""

    body = f"""
<div class="card">
  <div class="card-title">Edit Your Booking</div>
  <div class="card-sub">Change your slot, course, instructor, or details. A new confirmation email will be sent.</div>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px;flex-wrap:wrap">
    <label for="dp" style="font-size:13px;font-weight:500;color:var(--muted)">Date</label>
    <input type="date" id="dp" value="{date}" min="{today}"
           style="padding:9px 12px;border:1.5px solid var(--border);border-radius:8px;
                  font-family:inherit;font-size:14px;outline:none;max-width:200px;width:auto">
  </div>
  <p style="font-size:13px;font-weight:500;color:var(--muted);margin-bottom:12px">Slots for {date}</p>
  <div class="slots-grid">{slot_cards}</div>
  <div class="divider"></div>
  <form method="POST" action="/edit/{token}">
    <input type="hidden" name="slot_date" value="{date}">
    <input type="hidden" name="slot_hour" id="sh" value="{sh_val}">
    <div class="form-grid">
      <div class="fg"><label>Full Name</label>
        <input type="text" name="name" value="{b['name']}" required></div>
      <div class="fg"><label>Email Address</label>
        <input type="email" name="email" value="{b['email']}" required></div>
      <div class="fg"><label>Course</label>
        <select name="course" required><option value="">Select...</option>{course_opts}</select></div>
      <div class="fg"><label>Instructor</label>
        <select name="instructor" required><option value="">Select...</option>{instr_opts}</select></div>
    </div>
    <div class="actions">
      <button type="submit" class="btn btn-primary">Save Changes</button>
      <a href="/manage/{token}" class="btn btn-ghost">Go Back</a>
    </div>
  </form>
</div>"""

    js = f"""<script>
document.getElementById("dp").addEventListener("change",function(){{
  window.location.href="/edit/{token}?date="+this.value;}});
function pick(h){{
  document.querySelectorAll(".slot-card").forEach(c=>c.classList.remove("selected"));
  var l=document.getElementById("lbl-"+h);
  if(l&&!l.classList.contains("full")){{l.classList.add("selected");document.getElementById("sh").value=h;}}
}}
</script>"""
    return layout("Edit Booking", body, js)


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
    mail_lab_staff(b, "cancel")
    flash("Your booking has been cancelled.", "info")
    return redirect(url_for("index"))



@app.route("/test-email")
def test_email():
    """Visit this URL to test if email is working."""
    results = []
    api_key = os.environ.get("RESEND_API_KEY", "")
    sender  = os.environ.get("RESEND_FROM", "")

    results.append(f"RESEND_API_KEY set: {'✅ YES' if api_key else '❌ NOT SET'}")
    results.append(f"RESEND_FROM set: {sender if sender else '❌ NOT SET'}")
    results.append(f"LAB_EMAIL_1: {os.environ.get('LAB_EMAIL_1', '❌ NOT SET')}")
    results.append(f"LAB_EMAIL_2: {os.environ.get('LAB_EMAIL_2', 'not set (optional)')}")
    results.append(f"LAB_EMAILS list: {LAB_EMAILS}")
    results.append("---")

    if not api_key or not sender or not LAB_EMAILS:
        results.append("❌ Cannot send — set RESEND_API_KEY, RESEND_FROM and LAB_EMAIL_1 in Render Environment.")
    else:
        for addr in LAB_EMAILS:
            try:
                payload = json.dumps({
                    "from":    sender,
                    "to":      [addr],
                    "subject": "✅ Test — Office Hours App Email Working",
                    "html":    "<p>Test email from your Office Hours Booking app. Email is working! ✅</p>"
                }).encode("utf-8")
                req = urllib.request.Request(
                    "https://api.resend.com/emails",
                    data=payload,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    resp = json.loads(r.read())
                    results.append(f"✅ Test email sent to {addr} | id: {resp.get('id')}")
            except urllib.error.HTTPError as e:
                results.append(f"❌ Failed to {addr}: HTTP {e.code} — {e.read().decode()}")
            except Exception as e:
                results.append(f"❌ Failed to {addr}: {e}")

    return f"<pre style=\"font-family:monospace;font-size:14px;padding:24px;line-height:2\">" + "\n".join(results) + "</pre>"

if __name__ == "__main__":
    app.run(debug=False)
