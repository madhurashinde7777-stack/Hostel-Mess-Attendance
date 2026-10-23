"""
Hostel Mess Attendance System - Backend (Flask JSON API)
--------------------------------------------------------
Students mark whether they will eat lunch/dinner and check in/out at the gate.
Meal counts update automatically for the mess manager. The rector only monitors.
The database is backed up every hour and backups are kept for 30 days.

The React frontend lives in ./frontend
  * Development : python app.py  +  (cd frontend && npm run dev)  -> http://localhost:5173
  * Production  : (cd frontend && npm run build)  then  python app.py -> http://localhost:5000
"""
import csv
import io
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

import backup
import db as database

# All times in the app (cut-offs, curfew, dates) are India time, also on cloud
# servers that run on UTC. On Windows the computer's own time zone is used.
os.environ.setdefault("TZ", "Asia/Kolkata")
if hasattr(time, "tzset"):
    time.tzset()

# ---------------------------------------------------------------- config ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = database.SQLITE_PATH   # only used when no PostgreSQL is configured
BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(BASE_DIR, "backups"))
BACKUP_KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS", "30"))
GATE_API_KEY = os.environ.get("GATE_API_KEY", "change-this-gate-key")
FRONTEND_DIST = os.path.join(BASE_DIR, "frontend", "dist")
STUDENTS_CSV = os.environ.get("STUDENTS_CSV", os.path.join(BASE_DIR, "students.csv"))
PLAN_DAYS = 7  # how many days ahead a student can plan meals

MEALS = ["lunch", "dinner"]
DEFAULT_SETTINGS = {
    "cutoff_lunch": "10:30",
    "cutoff_dinner": "17:00",
    "curfew": "21:00",
}
STATUS_LABEL = {"IN": "In hostel", "OUT_DAY": "Out for the day", "HOME": "Gone home"}
STATE_LABEL = {"coming": "Coming", "opted_out": "Not coming", "leave": "On leave"}

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # on the live site (HTTPS) set COOKIE_SECURE=1 so the login cookie is never sent over plain HTTP
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
)

# -------------------------------------------------------------- database ---
def connect():
    """A database connection: PostgreSQL (Supabase) if set in .env, else SQLite."""
    return database.connect()


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now():
    return datetime.now()


def now_str():
    return now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return date.today().isoformat()


def add_user(conn, username, name, room, role, password, biometric_id=None, admission_date=None):
    cur = conn.execute(
        "INSERT INTO users(username,name,room,role,password_hash,biometric_id,admission_date,active) "
        "VALUES(?,?,?,?,?,?,?,1) RETURNING id",
        (username, name, room, role, generate_password_hash(password), biometric_id or None,
         admission_date or None))
    uid = cur.fetchone()["id"]
    if role == "student":
        conn.execute("INSERT INTO presence(user_id,status,updated_at) VALUES(?,?,?)",
                     (uid, "IN", now_str()))
    return uid


def append_student_csv(username, name, room, admission_date, biometric_id):
    """Append a newly added student to students.csv so the file stays in sync with the database."""
    path = STUDENTS_CSV
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["username", "name", "room", "admission_date", "biometric_id"])
        writer.writerow([
            username,
            name,
            room or "",
            admission_date or "",
            biometric_id or "",
        ])


def read_students_csv(path=None):
    """Reads the real student list (students.csv). Columns:
    username, name, room, role (always "student"), admission_date (YYYY-MM-DD),
    biometric_id (optional). Rows with any other role are ignored."""
    path = path or STUDENTS_CSV
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = []
        for r in csv.DictReader(f):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            if (r.get("role") or "student").lower() != "student":
                print(f"  students.csv: skipped {r.get('username')} (role must be 'student')")
                continue
            if r.get("username") and r.get("name"):
                r["username"] = r["username"].lower()
                rows.append(r)
        return rows


def seed(conn):
    """First run: create rector + mess logins and the students.
    If students.csv exists the real students are loaded, otherwise 10 demo students."""
    add_user(conn, "rector", "Hostel Rector", None, "rector", "rector123")
    add_user(conn, "mess", "Mess Manager", None, "mess", "mess123")
    real = read_students_csv()
    if real:
        for r in real:
            add_user(conn, r["username"], r["name"], r.get("room"), "student", "pass123",
                     biometric_id=r.get("biometric_id") or None,
                     admission_date=r.get("admission_date") or None)
        return
    names = ["Aditi Patil", "Sneha Kulkarni", "Priya Deshmukh", "Kavya Joshi", "Riya Shah",
             "Neha Pawar", "Pooja Jadhav", "Anjali More", "Shruti Gaikwad", "Isha Kale"]
    for i, n in enumerate(names, start=1):
        add_user(conn, f"s{100 + i}", n, f"{100 + (i + 1) // 2}", "student", "pass123",
                 biometric_id=f"FP{100 + i}")


def init_db():
    conn = connect()
    database.create_schema(conn)   # creates missing tables, upgrades older ones
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (k, v))
    if conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
        seed(conn)
    conn.commit()
    conn.close()


def get_settings(conn):
    return {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM settings")}


def at_time(day, hhmm):
    hh, mm = map(int, hhmm.split(":"))
    return datetime.combine(date.fromisoformat(day), datetime.min.time()).replace(hour=hh, minute=mm)


def is_locked(settings, day, meal):
    """After a meal's cut-off time, students can no longer change that meal."""
    return now() >= at_time(day, settings[f"cutoff_{meal}"])


# ------------------------------------------------------------ core logic ---
def meal_state(on_leave, away_now, opted_out):
    """away_now = student is OUT_DAY or HOME (physically not in hostel for meals)."""
    if on_leave or away_now:
        return "leave"
    if opted_out:
        return "opted_out"
    return "coming"


def room_order(row):
    """Sort key: room 2 before room 10 (numbers first), then by name."""
    room = str(row["room"] if row["room"] is not None else "").strip()
    return (0, int(room), "", row["name"]) if room.isdigit() else (1, 0, room, row["name"])


def leave_covers(conn, uid, day):
    return conn.execute(
        "SELECT 1 FROM leaves WHERE user_id=? AND from_day<=? AND (to_day IS NULL OR to_day>=?)",
        (uid, day, day)).fetchone() is not None


def compute_day(conn, day):
    """Meal status of every active student for one day + summary counts."""
    students = conn.execute(
        """SELECT u.id, u.username, u.name, u.room, u.biometric_id,
                  COALESCE(p.status,'IN') AS status, p.updated_at
           FROM users u LEFT JOIN presence p ON p.user_id=u.id
           WHERE u.role='student' AND u.active=1""").fetchall()
    students = sorted(students, key=room_order)
    on_leave = {r["user_id"] for r in conn.execute(
        "SELECT user_id FROM leaves WHERE from_day<=? AND (to_day IS NULL OR to_day>=?)", (day, day))}
    optout = {(r["user_id"], r["meal"]) for r in conn.execute(
        "SELECT user_id, meal FROM meal_optout WHERE day=?", (day,))}
    is_today = day == today_str()

    summary = {m: {"coming": 0, "opted_out": 0, "leave": 0} for m in MEALS}
    rows = []
    for s in students:
        meals = {}
        for m in MEALS:
            # OUT_DAY = went out, won't eat; HOME = gone home, won't eat
            away = is_today and s["status"] in ("OUT_DAY", "HOME")
            st = meal_state(s["id"] in on_leave, away, (s["id"], m) in optout)
            meals[m] = st
            summary[m][st] += 1
        rows.append({**dict(s), "meals": meals})
    return {"day": day, "total": len(students), "summary": summary, "students": rows}


def freeze_due_meals(conn):
    """At each meal's cut-off, store the final count that the mess will cook for."""
    settings = get_settings(conn)
    day = today_str()
    data = None
    for m in MEALS:
        if not is_locked(settings, day, m):
            continue
        if conn.execute("SELECT 1 FROM final_counts WHERE day=? AND meal=?", (day, m)).fetchone():
            continue
        data = data or compute_day(conn, day)
        conn.execute("INSERT INTO final_counts(day,meal,count,total,frozen_at) VALUES(?,?,?,?,?)",
                     (day, m, data["summary"][m]["coming"], data["total"], now_str()))
    conn.commit()


def refreeze_today(conn):
    """Recompute and overwrite today's frozen counts to reflect current presence.
    Called whenever a student's status changes so the mess board stays accurate."""
    settings = get_settings(conn)
    day = today_str()
    data = compute_day(conn, day)
    for m in MEALS:
        if not is_locked(settings, day, m):
            continue   # not yet frozen — live count is already correct
        conn.execute(
            "INSERT INTO final_counts(day,meal,count,total,frozen_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(day,meal) DO UPDATE SET count=excluded.count, total=excluded.total, frozen_at=excluded.frozen_at",
            (day, m, data["summary"][m]["coming"], data["total"], now_str()))
    conn.commit()


def meal_board(conn, day):
    freeze_due_meals(conn)
    settings = get_settings(conn)
    data = compute_day(conn, day)
    finals = {r["meal"]: r for r in conn.execute("SELECT * FROM final_counts WHERE day=?", (day,))}
    board = []
    for m in MEALS:
        f = finals.get(m)
        live = data["summary"][m]["coming"]
        locked = is_locked(settings, day, m)
        board.append({
            "meal": m,
            "cutoff": settings[f"cutoff_{m}"],
            "locked": locked,
            "live": live,
            # Always show the live count — the frozen count is kept for history
            # but the displayed number is always the real-time figure so that
            # status changes (OUT_DAY / HOME) are reflected immediately.
            "final": f["count"] if f else None,
            "show": live,
            "opted_out": data["summary"][m]["opted_out"],
            "leave": data["summary"][m]["leave"],
        })
    return data, board


def set_presence(conn, uid, action, source):
    """Record a gate movement and keep leaves in sync with it."""
    if action not in STATUS_LABEL:
        raise ValueError("bad action")
    day = today_str()
    yday = (date.today() - timedelta(days=1)).isoformat()
    conn.execute(
        """INSERT INTO presence(user_id,status,updated_at) VALUES(?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at""",
        (uid, action, now_str()))
    conn.execute("INSERT INTO gate_log(user_id,action,source,ts) VALUES(?,?,?,?)",
                 (uid, action, source, now_str()))
    if action == "HOME" and not leave_covers(conn, uid, day):
        # open-ended leave: meals stay cancelled until she checks back in
        conn.execute("INSERT INTO leaves(user_id,from_day,to_day,reason,created_at) VALUES(?,?,NULL,?,?)",
                     (uid, day, "Went home (gate)", now_str()))
    elif action == "IN":
        # she is back: end any leave covering today (future planned leaves stay)
        conn.execute("DELETE FROM leaves WHERE user_id=? AND from_day=?", (uid, day))
        conn.execute("UPDATE leaves SET to_day=? WHERE user_id=? AND from_day<? AND (to_day IS NULL OR to_day>=?)",
                     (yday, uid, day, day))
    conn.commit()
    # Immediately update frozen counts so the mess board reflects the change
    refreeze_today(conn)


def punch(conn, uid, source):
    """One scan at the gate (biometric / ID). Toggles in <-> out.
    Going out while a planned leave covers today counts as going home."""
    row = conn.execute("SELECT COALESCE(status,'IN') s FROM presence WHERE user_id=?", (uid,)).fetchone()
    current = row["s"] if row else "IN"
    if current == "IN":
        action = "HOME" if leave_covers(conn, uid, today_str()) else "OUT_DAY"
    else:
        action = "IN"
    set_presence(conn, uid, action, source)
    return action


def find_student(conn, code):
    return conn.execute(
        "SELECT * FROM users WHERE role='student' AND active=1 AND (username=? OR biometric_id=?)",
        (code, code)).fetchone()


def plan_days():
    return [(date.today() + timedelta(days=i)).isoformat() for i in range(PLAN_DAYS)]


def day_label(day):
    d = date.fromisoformat(day)
    if d == date.today():
        return "Today"
    if d == date.today() + timedelta(days=1):
        return "Tomorrow"
    return d.strftime("%a %d %b")


# ------------------------------------------------------------ API helpers ---
def err(message, status=400):
    return jsonify(error=message), status


def body():
    return request.get_json(silent=True) or {}


def api_login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if "uid" not in session:
                return err("Please log in.", 401)
            if roles and session.get("role") not in roles:
                return err("You don't have access to this page.", 403)
            return fn(*a, **kw)
        return wrapper
    return deco


def user_json(u):
    return {"id": u["id"], "username": u["username"], "name": u["name"],
            "room": u["room"], "role": u["role"]}


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return err("Not found", 404)
    return spa_index()


@app.get("/api/health")
def api_health():
    try:
        conn = connect()
        cur = conn.execute("SELECT COUNT(*) AS n FROM users")
        row = cur.fetchone()
        conn.close()
        return jsonify({
            "status": "ok",
            "database": database.describe(),
            "users_count": row["n"] if row else 0
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "database": database.describe(),
            "error": str(e)
        }), 500


# ------------------------------------------------------------------ auth ---
@app.post("/api/login")
def api_login():
    b = body()
    u = get_db().execute("SELECT * FROM users WHERE username=? AND active=1",
                         (str(b.get("username", "")).strip().lower(),)).fetchone()
    if not u or not check_password_hash(u["password_hash"], str(b.get("password", "")).strip()):
        return err("Wrong username or password.", 401)
    session.clear()
    session.update(uid=u["id"], role=u["role"], name=u["name"])
    return jsonify(user=user_json(u))


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def api_me():
    if "uid" not in session:
        return err("Not logged in", 401)
    u = get_db().execute("SELECT * FROM users WHERE id=? AND active=1", (session["uid"],)).fetchone()
    if not u:
        session.clear()
        return err("Not logged in", 401)
    return jsonify(user=user_json(u), meals=MEALS, status_label=STATUS_LABEL,
                   state_label=STATE_LABEL, server_time=now_str())


@app.post("/api/password")
@api_login_required()
def api_password():
    b = body()
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    new = str(b.get("new", ""))
    if not check_password_hash(u["password_hash"], str(b.get("old", ""))):
        return err("Current password is wrong.")
    if len(new) < 6:
        return err("New password must be at least 6 characters.")
    db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), u["id"]))
    db.commit()
    return jsonify(ok=True, message="Password changed.")


# --------------------------------------------------------------- student ---
@app.get("/api/student")
@api_login_required("student")
def api_student():
    db = get_db()
    uid = session["uid"]
    settings = get_settings(db)
    me = db.execute("""SELECT u.*, COALESCE(p.status,'IN') status, p.updated_at
                       FROM users u LEFT JOIN presence p ON p.user_id=u.id WHERE u.id=?""", (uid,)).fetchone()
    plan = []
    for d in plan_days():
        on_leave = leave_covers(db, uid, d)
        opt = {r["meal"] for r in db.execute("SELECT meal FROM meal_optout WHERE user_id=? AND day=?", (uid, d))}
        cells = [{"meal": m,
                  "state": meal_state(on_leave, d == today_str() and me["status"] == "HOME", m in opt),
                  "locked": is_locked(settings, d, m),
                  "cutoff_at": at_time(d, settings[f"cutoff_{m}"]).strftime("%Y-%m-%dT%H:%M:%S")}
                 for m in MEALS]
        plan.append({"day": d, "label": day_label(d), "cells": cells})
    leaves = [dict(r) for r in db.execute(
        "SELECT * FROM leaves WHERE user_id=? AND (to_day IS NULL OR to_day>=?) ORDER BY from_day",
        (uid, today_str()))]
    return jsonify(me={**user_json(me), "status": me["status"], "updated_at": me["updated_at"]},
                   plan=plan, leaves=leaves, settings=settings, today=today_str(),
                   max_day=plan_days()[-1])


@app.post("/api/student/meal")
@api_login_required("student")
def api_student_meal():
    b = body()
    db = get_db()
    uid = session["uid"]
    day, meal, choice = b.get("day"), b.get("meal"), b.get("choice")
    if day not in plan_days() or meal not in MEALS or choice not in ("yes", "no"):
        return err("Invalid request.")
    if is_locked(get_settings(db), day, meal):
        return err(f"{meal.title()} for {day_label(day)} is locked (cut-off passed).")
    if leave_covers(db, uid, day):
        return err("You are on leave that day. Cancel the leave or check in first.")
    if choice == "no":
        db.execute("INSERT INTO meal_optout(user_id,day,meal,updated_at) VALUES(?,?,?,?) "
                   "ON CONFLICT(user_id,day,meal) DO UPDATE SET updated_at=excluded.updated_at",
                   (uid, day, meal, now_str()))
    else:
        db.execute("DELETE FROM meal_optout WHERE user_id=? AND day=? AND meal=?", (uid, day, meal))
    db.commit()
    return jsonify(ok=True)


@app.post("/api/student/gate")
@api_login_required("student")
def api_student_gate():
    action = body().get("action")
    if action not in STATUS_LABEL:
        return err("Invalid action.")
    set_presence(get_db(), session["uid"], action, "student-app")
    return jsonify(ok=True, message=f"Status updated: {STATUS_LABEL[action]}.")


@app.post("/api/student/leave")
@api_login_required("student")
def api_student_leave():
    b = body()
    f, t = str(b.get("from_day", "")), str(b.get("to_day", ""))
    try:
        fd, td = date.fromisoformat(f), date.fromisoformat(t)
    except ValueError:
        return err("Please choose valid dates.")
    if fd < date.today() or td < fd:
        return err("Leave must start today or later and end on/after the start date.")
    db = get_db()
    db.execute("INSERT INTO leaves(user_id,from_day,to_day,reason,created_at) VALUES(?,?,?,?,?)",
               (session["uid"], f, t, str(b.get("reason", "")).strip()[:200], now_str()))
    db.commit()
    return jsonify(ok=True, message="Leave saved. Your meals for those days are cancelled automatically.")


@app.delete("/api/student/leave/<int:lid>")
@api_login_required("student")
def api_student_leave_cancel(lid):
    db = get_db()
    lv = db.execute("SELECT * FROM leaves WHERE id=? AND user_id=?", (lid, session["uid"])).fetchone()
    if not lv:
        return err("Leave not found.", 404)
    if lv["from_day"] <= today_str() and lv["to_day"] is None:
        return err("You are marked as gone home. Tap \"I'm back\" when you return.")
    if lv["from_day"] < today_str():
        return err("This leave has already started. Tap \"I'm back\" when you return.")
    db.execute("DELETE FROM leaves WHERE id=?", (lid,))
    db.commit()
    return jsonify(ok=True, message="Leave cancelled.")


# ---------------------------------------------------------------- rector ---
@app.get("/api/rector/dashboard")
@api_login_required("rector")
def api_rector_dashboard():
    db = get_db()
    settings = get_settings(db)
    data, board = meal_board(db, today_str())
    counts = {k: sum(1 for s in data["students"] if s["status"] == k) for k in STATUS_LABEL}
    return jsonify(total=data["total"], board=board, counts=counts, students=data["students"],
                   past_curfew=now() >= at_time(today_str(), settings["curfew"]),
                   curfew=settings["curfew"], updated_at=now_str())


@app.post("/api/rector/presence/<int:uid>")
@api_login_required("rector")
def api_rector_presence(uid):
    action = body().get("action")
    if action not in STATUS_LABEL:
        return err("Invalid action.")
    set_presence(get_db(), uid, action, "rector")
    return jsonify(ok=True)


@app.get("/api/rector/students")
@api_login_required("rector")
def api_rector_students():
    rows = get_db().execute(
        """SELECT id, username, name, room, biometric_id, admission_date, active FROM users
           WHERE role='student'""").fetchall()
    rows = sorted(rows, key=lambda r: (-r["active"],) + room_order(r))
    return jsonify(students=[dict(r) for r in rows])


@app.post("/api/rector/students")
@api_login_required("rector")
def api_rector_student_add():
    b = body()
    username = str(b.get("username", "")).strip().lower()
    name = str(b.get("name", "")).strip()
    room = str(b.get("room", "")).strip()
    admission_date = str(b.get("admission_date", "")).strip() or None
    biometric_id = str(b.get("biometric_id", "")).strip() or None
    password = str(b.get("password", "")).strip() or "pass123"
    if not username or not name:
        return err("Username and full name are required.")
    db = get_db()
    try:
        add_user(db, username, name, room, "student", password, biometric_id, admission_date)
        db.commit()
    except database.IntegrityError:
        db.rollback()
        return err("Username or biometric ID already exists.")
    # Keep students.csv in sync with the database
    try:
        append_student_csv(username, name, room, admission_date, biometric_id)
    except Exception as csv_err:
        # CSV write failure must NOT undo the DB record — just log it
        app.logger.warning("Could not append to students.csv: %s", csv_err)
    return jsonify(ok=True, message=f"Student {name} added.")


@app.post("/api/rector/students/<int:uid>/toggle")
@api_login_required("rector")
def api_rector_student_toggle(uid):
    db = get_db()
    db.execute("UPDATE users SET active=1-active WHERE id=? AND role='student'", (uid,))
    db.commit()
    return jsonify(ok=True)


@app.post("/api/rector/students/<int:uid>/reset")
@api_login_required("rector")
def api_rector_student_reset(uid):
    db = get_db()
    db.execute("UPDATE users SET password_hash=? WHERE id=? AND role='student'",
               (generate_password_hash("pass123"), uid))
    db.commit()
    return jsonify(ok=True, message="Password reset to pass123.")


@app.get("/api/rector/history")
@api_login_required("rector")
def api_rector_history():
    db = get_db()
    since = (date.today() - timedelta(days=30)).isoformat()
    by_day = {}
    for r in db.execute("SELECT * FROM final_counts WHERE day>=? ORDER BY day", (since,)):
        if r["meal"] in MEALS:
            by_day.setdefault(r["day"], {"day": r["day"], "total": r["total"]})[r["meal"]] = r["count"]
    log = db.execute("""SELECT g.id, g.action, g.source, g.ts, u.name, u.room
                        FROM gate_log g JOIN users u ON u.id=g.user_id
                        WHERE g.ts>=? ORDER BY g.ts DESC LIMIT 500""", (since,)).fetchall()
    return jsonify(days=list(by_day.values()), log=[dict(r) for r in log])


@app.get("/api/export.csv")
@api_login_required("rector", "mess")
def api_export_csv():
    since = (date.today() - timedelta(days=30)).isoformat()
    rows = [r for r in get_db().execute("SELECT * FROM final_counts WHERE day>=? ORDER BY day, meal", (since,))
            if r["meal"] in MEALS]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "meal", "final_count", "total_students", "frozen_at"])
    for r in rows:
        w.writerow([r["day"], r["meal"], r["count"], r["total"], r["frozen_at"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=meal_counts_last_30_days.csv"})


@app.get("/api/rector/settings")
@api_login_required("rector")
def api_rector_settings():
    s = get_settings(get_db())
    return jsonify(settings={k: s[k] for k in DEFAULT_SETTINGS})


@app.put("/api/rector/settings")
@api_login_required("rector")
def api_rector_settings_save():
    b = body()
    for k in DEFAULT_SETTINGS:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(b.get(k, ""))):
            return err(f"Invalid time for {k.replace('_', ' ')}.")
    db = get_db()
    for k in DEFAULT_SETTINGS:
        db.execute("UPDATE settings SET value=? WHERE key=?", (b[k], k))
    db.commit()
    return jsonify(ok=True, message="Settings saved.")


@app.get("/api/rector/backups")
@api_login_required("rector")
def api_rector_backups():
    return jsonify(files=backup.list_backups(BACKUP_DIR), keep=BACKUP_KEEP_DAYS, database=database.describe(),
                   folder=os.path.abspath(BACKUP_DIR))


@app.post("/api/rector/backups")
@api_login_required("rector")
def api_rector_backup_now():
    path = backup.make_backup(BACKUP_DIR, BACKUP_KEEP_DAYS)
    return jsonify(ok=True, message=f"Backup saved: {os.path.basename(path)}")


@app.get("/api/rector/backups/<name>")
@api_login_required("rector")
def api_rector_backup_download(name):
    if not backup.NAME_RE.fullmatch(name):
        return err("Not found", 404)
    return send_from_directory(os.path.abspath(BACKUP_DIR), name, as_attachment=True)


# ------------------------------------------------------------------ mess ---
@app.get("/api/mess")
@api_login_required("mess", "rector")
def api_mess():
    db = get_db()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    data, board = meal_board(db, today_str())
    tdata = compute_day(db, tomorrow)
    return jsonify(board=board, total=data["total"], updated_at=now_str(),
                   tomorrow={m: tdata["summary"][m]["coming"] for m in MEALS})


@app.get("/api/counts")
@api_login_required("mess", "rector")
def api_counts():
    day = request.args.get("day", today_str())
    try:
        date.fromisoformat(day)
    except ValueError:
        return err("Invalid date.")
    data, board = meal_board(get_db(), day)
    return jsonify(day=day, total=data["total"], meals=board, updated_at=now_str())


# -------------------------------------------------- biometric / gate API ---
@app.post("/api/gate")
def api_gate():
    """For a fingerprint / RFID / QR device at the gate.
    POST JSON {"id": "<biometric_id or username>", "action": "IN|OUT_DAY|HOME" (optional)}
    Header: X-API-Key: <GATE_API_KEY>. Without "action" the scan toggles in/out."""
    if request.headers.get("X-API-Key") != GATE_API_KEY:
        return err("invalid api key", 401)
    b = body()
    db = get_db()
    s = find_student(db, str(b.get("id", "")).strip())
    if not s:
        return err("unknown student", 404)
    action = b.get("action")
    if action:
        if action not in STATUS_LABEL:
            return err("action must be IN, OUT_DAY or HOME")
        set_presence(db, s["id"], action, "gate-device")
    else:
        action = punch(db, s["id"], "gate-device")
    return jsonify(student=s["name"], room=s["room"], status=action, label=STATUS_LABEL[action])


# ------------------------------------------------ React frontend (built) ---
def spa_index():
    index = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(index):
        return send_from_directory(FRONTEND_DIST, "index.html")
    return Response(
        "<h2>Frontend not built yet</h2><p>Run <code>cd frontend</code>, <code>npm install</code>, "
        "<code>npm run build</code>, then restart <code>python app.py</code>.<br>"
        "Or for development run <code>npm run dev</code> and open http://localhost:5173</p>",
        mimetype="text/html")


@app.get("/")
@app.get("/<path:path>")
def spa(path=""):
    if path.startswith("api/"):
        return err("Not found", 404)
    full = os.path.join(FRONTEND_DIST, path)
    if path and os.path.isfile(full):
        return send_from_directory(FRONTEND_DIST, path)
    return spa_index()


@app.route("/api/cron", methods=["GET", "POST"])
def cron_trigger():
    """Triggered by an external scheduler to freeze meals passing cut-off."""
    try:
        conn = connect()
        freeze_due_meals(conn)
        conn.close()
        return jsonify({"status": "ok", "message": "Meal freeze executed successfully"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------- scheduler ---
def scheduler_loop():
    """Every minute: freeze counts that reached cut-off. Every hour: backup."""
    last_backup = None
    while True:
        try:
            conn = connect()
            freeze_due_meals(conn)
            conn.close()
            hour = now().strftime("%Y-%m-%d %H")
            if hour != last_backup:
                backup.make_backup(BACKUP_DIR, BACKUP_KEEP_DAYS)
                last_backup = hour
        except Exception:  # keep the loop alive
            app.logger.exception("scheduler error")
        time.sleep(60)


_scheduler_started = False


def start_scheduler():
    """Starts the background scheduler once per process.
    Locally `python app.py` calls it; on Railway gunicorn.conf.py calls it."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=scheduler_loop, daemon=True, name="scheduler").start()


if app.secret_key == "dev-secret-change-me" and os.environ.get("COOKIE_SECURE") == "1":
    print("WARNING: SECRET_KEY is not set. Add a long random SECRET_KEY variable before going live.")

if __name__ == "__main__":
    init_db()
    start_scheduler()
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n  MessMate backend running on http://localhost:{port}")
    print(f"  Database: {database.describe()}")
    if os.path.exists(os.path.join(FRONTEND_DIST, "index.html")):
        print(f"  Open the app:        http://localhost:{port}")
    else:
        print("  Frontend not built: run 'npm run dev' in the frontend folder and open http://localhost:5173")
    print("  Phones on the same Wi-Fi: http://<this-computer-IP>:%d\n" % port)
    app.run(host="0.0.0.0", port=port, debug=False)