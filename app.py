"""lanes: a kanban board for any team, with columns, swimlanes and quiet cards."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import struct
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import segno
from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup, escape
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

__version__ = "0.5.3"

ROOT = Path(__file__).resolve().parent

# Settings come from the environment, or from a .env file next to this one.
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _key, _sep, _value = _line.partition("=")
        if _sep and not _line.lstrip().startswith("#"):
            os.environ.setdefault(_key.strip(), _value.strip())
    time.tzset()

DATA = Path(os.environ.get("LANES_DATA", str(ROOT / "data")))
DEV = os.environ.get("LANES_DEV") == "1"
BASE_URL = os.environ.get("LANES_URL", "http://127.0.0.1:8092").rstrip("/")
INVITE_DAYS = 7
COLOR = re.compile(r"#[0-9a-fA-F]{6}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  pw_hash TEXT, totp_secret TEXT, is_admin INTEGER NOT NULL DEFAULT 0,
  invite_token TEXT, invite_expires INTEGER, created_at INTEGER NOT NULL,
  pic INTEGER NOT NULL DEFAULT 0, theme TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS pictures (
  user_id INTEGER PRIMARY KEY REFERENCES users(id), type TEXT NOT NULL, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS workspaces (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at INTEGER NOT NULL,
  logo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS logos (
  workspace_id INTEGER PRIMARY KEY REFERENCES workspaces(id), type TEXT NOT NULL,
  data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS members (
  workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
  user_id INTEGER NOT NULL REFERENCES users(id), PRIMARY KEY (workspace_id, user_id));
CREATE TABLE IF NOT EXISTS boards (
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
  name TEXT NOT NULL, archive_days INTEGER NOT NULL DEFAULT 14,
  next_ref INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS columns (
  id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL REFERENCES boards(id),
  name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#8a93a3', wip INTEGER,
  done INTEGER NOT NULL DEFAULT 0, pos REAL NOT NULL);
CREATE TABLE IF NOT EXISTS lanes (
  id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL REFERENCES boards(id),
  name TEXT NOT NULL, pos REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cards (
  id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL REFERENCES boards(id),
  ref INTEGER NOT NULL, column_id INTEGER NOT NULL REFERENCES columns(id),
  lane_id INTEGER REFERENCES lanes(id), title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '', pos REAL NOT NULL,
  created_by INTEGER REFERENCES users(id), created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL, done_at INTEGER, due TEXT);
CREATE INDEX IF NOT EXISTS cards_board ON cards(board_id, column_id);
CREATE TABLE IF NOT EXISTS card_people (
  card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id), PRIMARY KEY (card_id, user_id));
CREATE TABLE IF NOT EXISTS labels (
  id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL REFERENCES boards(id),
  name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#8a93a3');
CREATE TABLE IF NOT EXISTS card_labels (
  card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
  PRIMARY KEY (card_id, label_id));
CREATE TABLE IF NOT EXISTS subtasks (
  id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  ref INTEGER, title TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, pos REAL NOT NULL);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL,
  created_at INTEGER NOT NULL);
"""

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ.get("LANES_SECRET") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not DEV,
    PERMANENT_SESSION_LIFETIME=14 * 24 * 3600,
)

FAILS = {}  # client address -> times of failed logins


def now() -> int:
    return int(time.time())


def today() -> str:
    return time.strftime("%Y-%m-%d")


def parse_day(text: str) -> str | None:
    """Read 2026-11-14, 14.11.2026 or 14.11 (this year) as 2026-11-14. Anything else is None."""
    text = text.strip()
    if re.fullmatch(r"\d{1,2}\.\d{1,2}\.?", text):
        text = text.rstrip(".") + "." + today()[:4]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


# Database


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DATA / "lanes.db"), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc) -> None:
    con = g.pop("db", None)
    if con is not None:
        con.close()


def init_db() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(SCHEMA)
        # Columns added after 0.1.0; an older database gets them here.
        for table, col, kind in (
            ("users", "pic", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "theme", "TEXT NOT NULL DEFAULT ''"),
            ("workspaces", "logo", "INTEGER NOT NULL DEFAULT 0"),
            ("cards", "due", "TEXT"),
        ):
            if col not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")


def bump(con: sqlite3.Connection, bid: int) -> None:
    """Mark a board as changed, so open pages reload it when their tab comes back."""
    con.execute("UPDATE boards SET version = version + 1 WHERE id = ?", (bid,))


# Login: password plus a six-digit code from an authenticator app


def totp(secret: str, at: float | None = None) -> str:
    counter = int((time.time() if at is None else at) // 30)
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def totp_ok(secret: str | None, code: str | None) -> bool:
    code = re.sub(r"\s", "", code or "")
    if not secret or not code:
        return False
    at = time.time()
    return any(hmac.compare_digest(totp(secret, at + d * 30), code) for d in (-1, 0, 1))


def make_invite(con: sqlite3.Connection, uid: int) -> str:
    """Give a user a fresh invite link. It also logs out the old login."""
    token = secrets.token_urlsafe(24)
    secret = base64.b32encode(secrets.token_bytes(20)).decode()
    con.execute(
        "UPDATE users SET pw_hash = NULL, totp_secret = ?, invite_token = ?,"
        " invite_expires = ? WHERE id = ?",
        (secret, token, now() + INVITE_DAYS * 86400, uid),
    )
    con.commit()
    return f"{BASE_URL}/invite/{token}"


def find_or_add_user(con: sqlite3.Connection, email: str, name: str, admin: bool = False) -> int:
    email = email.strip().lower()
    row = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        return row["id"]
    cur = con.execute(
        "INSERT INTO users (email, name, is_admin, created_at) VALUES (?, ?, ?, ?)",
        (email, name.strip() or email.split("@")[0], int(admin), now()),
    )
    return cur.lastrowid


def current_user() -> sqlite3.Row | None:
    if "user" not in g:
        uid = session.get("uid")
        g.user = (
            db()
            .execute("SELECT * FROM users WHERE id = ? AND pw_hash IS NOT NULL", (uid,))
            .fetchone()
            if uid
            else None
        )
    return g.user


def too_many_fails(addr: str) -> bool:
    recent = [t for t in FAILS.get(addr, []) if t > time.time() - 900]
    FAILS[addr] = recent
    return len(recent) >= 10


def log_in(uid: int) -> None:
    session.clear()
    session.permanent = True
    session["uid"] = uid


@app.before_request
def guard():
    if request.method == "POST":
        token = session.get("csrf", "")
        if not token or not hmac.compare_digest(token, request.form.get("csrf", "")):
            abort(400)
    if request.endpoint not in ("login", "invite", "static") and not current_user():
        return redirect(url_for("login"))
    return None


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; frame-ancestors 'none';"
        " form-action 'self'; base-uri 'none'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@app.context_processor
def template_values():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    me = current_user()
    return {"csrf": session["csrf"], "me": me, "now": now(), "crumb": me and crumb(me)}


def crumb(me: sqlite3.Row) -> dict | None:
    """The workspace at the top left of every page, and its link: back to the last board opened."""
    con, last = db(), session.get("board", 0)
    w = (
        g.get("ws")
        or con.execute(
            "SELECT w.* FROM boards b JOIN workspaces w ON w.id = b.workspace_id"
            " JOIN members m ON m.workspace_id = w.id WHERE b.id = ? AND m.user_id = ?",
            (last, me["id"]),
        ).fetchone()
    )
    if not w:
        return None
    b = con.execute(
        "SELECT id FROM boards WHERE workspace_id = ? ORDER BY id = ? DESC, id LIMIT 1",
        (w["id"], last),
    ).fetchone()
    href = url_for("board", bid=b["id"]) if b else url_for("workspace", wid=w["id"])
    return {"w": w, "href": href}


@app.template_filter("when")
def when(ts: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


@app.template_filter("day")
def day(ts: int | None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""


@app.template_filter("initials")
def initials(name: str) -> str:
    parts = name.split()
    return (parts[0][:1] + (parts[-1][:1] if len(parts) > 1 else "")).upper()


@app.template_global()
def avatar(uid: int, name: str, pic: int = 0, size: str = "") -> Markup:
    """A person's picture, or their initials on a colour of their own."""
    if pic:
        src = url_for("picture", uid=uid, v=pic)
        return Markup('<img class="avatar {}" src="{}" alt="" title="{}">').format(size, src, name)
    return Markup('<span class="avatar {} a{}" title="{}">{}</span>').format(
        size, uid % 8, name, initials(name)
    )


@app.template_global()
def ws_logo(w: sqlite3.Row) -> Markup:
    """A workspace's logo, or its initials on a colour of its own."""
    if w["logo"]:
        src = url_for("workspace_logo", wid=w["id"], v=w["logo"])
        return Markup('<img class="wslogo" src="{}" alt="">').format(src)
    return Markup('<span class="wslogo a{}">{}</span>').format(w["id"] % 8, initials(w["name"]))


@app.template_filter("md")
def render(text: str | None) -> Markup:
    """Show plain text with headings, bold, lists and links. Everything else is escaped."""
    lines = []
    for line in str(escape(text or "")).split("\n"):
        heading = re.match(r"#{1,6} (.*)", line)
        lines.append(f"<h4>{heading.group(1)}</h4>" if heading else line + "<br>")
    out = "\n".join(lines)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', out)
    out = re.sub(r'(?<!href=")(https?://[^\s<)\]]+)', r'<a href="\1">\1</a>', out)
    return Markup(out)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        addr = request.remote_addr or "?"
        if too_many_fails(addr):
            flash("Too many failed tries. Wait 15 minutes.")
            return render_template("login.html"), 429
        email = request.form.get("email", "").strip().lower()
        user = (
            db()
            .execute("SELECT * FROM users WHERE email = ? AND pw_hash IS NOT NULL", (email,))
            .fetchone()
        )
        if (
            user
            and check_password_hash(user["pw_hash"], request.form.get("password", ""))
            and totp_ok(user["totp_secret"], request.form.get("code"))
        ):
            log_in(user["id"])
            return redirect(url_for("home"))
        FAILS.setdefault(addr, []).append(time.time())
        flash("Wrong email, password or code.")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/invite/<token>", methods=["GET", "POST"])
def invite(token):
    con = db()
    user = con.execute(
        "SELECT * FROM users WHERE invite_token = ? AND invite_expires > ?",
        (token, now()),
    ).fetchone()
    if not user:
        flash("This invite link has expired or was used. Ask for a new one.")
        return redirect(url_for("login"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < 12:
            flash("Use at least 12 characters.")
        elif password != request.form.get("password2"):
            flash("The two passwords differ.")
        elif not totp_ok(user["totp_secret"], request.form.get("code")):
            flash("The code is wrong. Check that the phone clock is right, and try the next code.")
        else:
            con.execute(
                "UPDATE users SET pw_hash = ?, invite_token = NULL, invite_expires = NULL"
                " WHERE id = ?",
                (generate_password_hash(password), user["id"]),
            )
            con.commit()
            log_in(user["id"])
            return redirect(url_for("home"))
    uri = "otpauth://totp/lanes:{}?secret={}&issuer=lanes".format(
        quote(user["email"]), user["totp_secret"]
    )
    qr = Markup(segno.make(uri, error="m").svg_inline(scale=5))
    return render_template("invite.html", user=user, qr=qr)


# Access: a user sees a workspace, its boards and their cards only as a member


def get_workspace(wid: int) -> sqlite3.Row:
    row = (
        db()
        .execute(
            "SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id = w.id"
            " WHERE w.id = ? AND m.user_id = ?",
            (wid, current_user()["id"]),
        )
        .fetchone()
    )
    if row is None:
        abort(404)
    return row


def get_board(bid: int) -> sqlite3.Row:
    row = (
        db()
        .execute(
            "SELECT b.* FROM boards b JOIN members m ON m.workspace_id = b.workspace_id"
            " WHERE b.id = ? AND m.user_id = ?",
            (bid, current_user()["id"]),
        )
        .fetchone()
    )
    if row is None:
        abort(404)
    return row


def get_card(cid: int) -> sqlite3.Row:
    row = db().execute("SELECT * FROM cards WHERE id = ?", (cid,)).fetchone()
    if row is None:
        abort(404)
    get_board(row["board_id"])
    return row


def get_part(table: str, pid: int) -> sqlite3.Row:
    """A column, lane or label, after checking that its board is the user's."""
    assert table in ("columns", "lanes", "labels")
    row = db().execute(f"SELECT * FROM {table} WHERE id = ?", (pid,)).fetchone()
    if row is None:
        abort(404)
    get_board(row["board_id"])
    return row


def back(bid: int):
    """Go back to the board view the form came from, or to the plain board."""
    nxt = request.form.get("next", "")
    if re.match(rf"/b/{bid}(\?|$|/archive)", nxt):
        return redirect(nxt)
    return redirect(url_for("board", bid=bid))


def int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Workspaces and boards


@app.route("/")
def home():
    me = current_user()
    spaces = (
        db()
        .execute(
            "SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id = w.id"
            " WHERE m.user_id = ? ORDER BY w.name",
            (me["id"],),
        )
        .fetchall()
    )
    boards = {
        w["id"]: db()
        .execute("SELECT * FROM boards WHERE workspace_id = ? ORDER BY name", (w["id"],))
        .fetchall()
        for w in spaces
    }
    return render_template("home.html", spaces=spaces, boards=boards)


@app.route("/w/new", methods=["POST"])
def new_workspace():
    admin_only()
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("home"))
    con = db()
    wid = con.execute(
        "INSERT INTO workspaces (name, created_at) VALUES (?, ?)", (name, now())
    ).lastrowid
    con.execute(
        "INSERT INTO members (workspace_id, user_id) VALUES (?, ?)", (wid, current_user()["id"])
    )
    con.commit()
    return redirect(url_for("workspace", wid=wid))


@app.route("/w/<int:wid>")
def workspace(wid):
    w = g.ws = get_workspace(wid)
    con = db()
    boards = con.execute(
        "SELECT * FROM boards WHERE workspace_id = ? ORDER BY name", (wid,)
    ).fetchall()
    people = con.execute(
        "SELECT u.* FROM users u JOIN members m ON m.user_id = u.id"
        " WHERE m.workspace_id = ? ORDER BY u.name",
        (wid,),
    ).fetchall()
    return render_template("workspace.html", w=w, boards=boards, people=people)


@app.route("/w/<int:wid>/invite", methods=["POST"])
def workspace_invite(wid):
    get_workspace(wid)
    email = request.form.get("email", "").strip()
    name = request.form.get("name", "").strip()
    if "@" not in email or not name:
        flash("Give both a name and an email.")
        return redirect(url_for("workspace", wid=wid))
    con = db()
    uid = find_or_add_user(con, email, name)
    con.execute("INSERT OR IGNORE INTO members (workspace_id, user_id) VALUES (?, ?)", (wid, uid))
    con.commit()
    user = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user["pw_hash"]:
        flash(f"{user['name']} already has a login and is now a member.")
    else:
        flash(f"Invite link for {user['name']}: {make_invite(con, uid)}")
    return redirect(url_for("workspace", wid=wid))


@app.route("/w/<int:wid>/remove/<int:uid>", methods=["POST"])
def workspace_remove(wid, uid):
    """An admin takes someone off the workspace. Their cards and comments keep their name."""
    get_workspace(wid)
    admin_only()
    if uid == current_user()["id"]:
        abort(400)
    con = db()
    user = con.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
    con.execute("DELETE FROM members WHERE workspace_id = ? AND user_id = ?", (wid, uid))
    con.execute("UPDATE boards SET version = version + 1 WHERE workspace_id = ?", (wid,))
    con.commit()
    if user:
        flash(f"{user['name']} is no longer a member.")
    return redirect(url_for("workspace", wid=wid))


@app.route("/w/<int:wid>/link/<int:uid>", methods=["POST"])
def workspace_link(wid, uid):
    """A new invite link. Members may make one only for a person who has not logged in yet."""
    get_workspace(wid)
    con = db()
    user = con.execute(
        "SELECT u.* FROM users u JOIN members m ON m.user_id = u.id"
        " WHERE u.id = ? AND m.workspace_id = ?",
        (uid, wid),
    ).fetchone()
    if user is None:
        abort(404)
    if user["pw_hash"] and not current_user()["is_admin"]:
        abort(403)
    flash(f"Invite link for {user['name']}: {make_invite(con, uid)}")
    return redirect(url_for("workspace", wid=wid))


DEFAULT_COLUMNS = [("To do", "#e47c40", 0), ("Doing", "#e4ce40", 0), ("Done", "#7cc243", 1)]


@app.route("/w/<int:wid>/boards/new", methods=["POST"])
def new_board(wid):
    get_workspace(wid)
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("workspace", wid=wid))
    con = db()
    bid = con.execute(
        "INSERT INTO boards (workspace_id, name, created_at) VALUES (?, ?, ?)",
        (wid, name, now()),
    ).lastrowid
    for pos, (col, color, done) in enumerate(DEFAULT_COLUMNS):
        con.execute(
            "INSERT INTO columns (board_id, name, color, done, pos) VALUES (?, ?, ?, ?, ?)",
            (bid, col, color, done, pos),
        )
    con.commit()
    return redirect(url_for("board", bid=bid))


@app.route("/b/<int:bid>/edit", methods=["POST"])
def edit_board(bid):
    get_board(bid)
    con = db()
    name = request.form.get("name", "").strip()
    days = int_or_none(request.form.get("archive_days"))
    if name:
        con.execute("UPDATE boards SET name = ? WHERE id = ?", (name, bid))
    if days is not None and 0 < days <= 3650:
        con.execute("UPDATE boards SET archive_days = ? WHERE id = ?", (days, bid))
    bump(con, bid)
    con.commit()
    return back(bid)


@app.route("/b/<int:bid>/version")
def board_version(bid):
    return Response(str(get_board(bid)["version"]), mimetype="text/plain")


@app.route("/b/<int:bid>/colors.css")
def board_colors(bid):
    """Column and label colours, as a style sheet, since the page allows no inline styles."""
    get_board(bid)
    con = db()
    rules = [
        f".col-{r['id']} {{ --c: {r['color']}; }}"
        for r in con.execute("SELECT id, color FROM columns WHERE board_id = ?", (bid,))
    ]
    rules += [
        f".lab-{r['id']} {{ --c: {r['color']}; }}"
        for r in con.execute("SELECT id, color FROM labels WHERE board_id = ?", (bid,))
    ]
    resp = Response("\n".join(rules) + "\n", mimetype="text/css")
    resp.headers["Cache-Control"] = "private, max-age=86400"  # the link carries the version
    return resp


def board_parts(bid: int):
    con = db()
    cols = con.execute("SELECT * FROM columns WHERE board_id = ? ORDER BY pos", (bid,)).fetchall()
    lanes = con.execute("SELECT * FROM lanes WHERE board_id = ? ORDER BY pos", (bid,)).fetchall()
    labels = con.execute("SELECT * FROM labels WHERE board_id = ? ORDER BY name", (bid,)).fetchall()
    return cols, lanes, labels


def workspace_people(wid: int) -> list:
    return (
        db()
        .execute(
            "SELECT u.* FROM users u JOIN members m ON m.user_id = u.id"
            " WHERE m.workspace_id = ? ORDER BY u.name",
            (wid,),
        )
        .fetchall()
    )


def card_facts(bid: int, where: str = "", args: tuple = ()) -> list:
    """The board's cards with their people, labels, sub-task and comment counts."""
    con = db()
    rows = con.execute(
        "SELECT c.*, col.done AS col_done,"
        " (SELECT COUNT(*) FROM subtasks s WHERE s.card_id = c.id) AS n_sub,"
        " (SELECT COUNT(*) FROM subtasks s WHERE s.card_id = c.id AND s.done) AS n_sub_done,"
        " (SELECT COUNT(*) FROM comments m WHERE m.card_id = c.id) AS n_comments"
        " FROM cards c JOIN columns col ON col.id = c.column_id"
        f" WHERE c.board_id = ? {where} ORDER BY c.pos",
        (bid, *args),
    ).fetchall()
    people: dict = {}
    for r in con.execute(
        "SELECT p.card_id, u.id, u.name, u.pic FROM card_people p JOIN users u ON u.id = p.user_id"
        " JOIN cards c ON c.id = p.card_id WHERE c.board_id = ? ORDER BY u.name",
        (bid,),
    ):
        people.setdefault(r["card_id"], []).append(r)
    labels: dict = {}
    for r in con.execute(
        "SELECT cl.card_id, l.id, l.name FROM card_labels cl JOIN labels l ON l.id = cl.label_id"
        " WHERE l.board_id = ? ORDER BY l.name",
        (bid,),
    ):
        labels.setdefault(r["card_id"], []).append(r)
    subs: dict = {}
    for r in con.execute(
        "SELECT s.card_id, s.ref, s.title FROM subtasks s JOIN cards c ON c.id = s.card_id"
        " WHERE c.board_id = ? AND NOT s.done ORDER BY s.pos",
        (bid,),
    ):
        subs.setdefault(r["card_id"], []).append(r)
    day = today()
    return [
        dict(
            r,
            late=bool(r["due"] and r["due"] < day and not r["col_done"]),
            people=people.get(r["id"], []),
            labels=labels.get(r["id"], []),
            open_subs=subs.get(r["id"], []),
        )
        for r in rows
    ]


def archived(b: sqlite3.Row) -> tuple:
    """SQL that leaves out cards that have sat in a done column longer than the board allows."""
    return (
        "AND NOT (col.done = 1 AND c.done_at IS NOT NULL AND c.done_at < ?)",
        (now() - b["archive_days"] * 86400,),
    )


def matches(c: dict, q: str) -> bool:
    q = q.lower().lstrip("#")
    return q in c["title"].lower() or q in c["body"].lower() or q == str(c["ref"])


@app.route("/b/<int:bid>")
def board(bid):
    b = get_board(bid)
    session["board"] = bid
    cols, lanes, labels = board_parts(bid)
    people = workspace_people(b["workspace_id"])
    where, args = archived(b)
    cards = card_facts(bid, where, args)
    per_column: dict = {}
    late: dict = {}  # person -> cards past their date
    for c in cards:
        per_column[c["column_id"]] = per_column.get(c["column_id"], 0) + 1
        for p in c["people"] if c["late"] else []:
            late[p["id"]] = late.get(p["id"], 0) + 1

    by = request.args.get("by", "")
    who = request.args.getlist("who", type=int)
    lab = request.args.getlist("label", type=int)
    q = request.args.get("q", "").strip()
    shown = [
        c
        for c in cards
        if (not who or any(p["id"] in who for p in c["people"]))
        and (not lab or any(lb["id"] in lab for lb in c["labels"]))
        and (not q or matches(c, q))
    ]

    # Rows: the board's lanes, or one row per person or label. A card shows in every row it fits.
    if by == "person":
        rows = [(str(p["id"]), p["name"]) for p in people] + [("none", "Nobody")]
        keys = lambda c: [str(p["id"]) for p in c["people"]] or ["none"]
    elif by == "label":
        rows = [(str(lb["id"]), lb["name"]) for lb in labels] + [("none", "No label")]
        keys = lambda c: [str(lb["id"]) for lb in c["labels"]] or ["none"]
    else:
        by = ""
        rows = [(str(ln["id"]), ln["name"]) for ln in lanes] or [("", "")]
        keys = lambda c: [str(c["lane_id"] or "")]
    cells: dict = {}
    for c in shown:
        for k in keys(c):
            cells.setdefault((k, c["column_id"]), []).append(c)
    in_row = {k: sum(len(v) for (rk, _), v in cells.items() if rk == k) for k, _ in rows}

    open_card = None
    cid = request.args.get("card", type=int)
    if cid:
        open_card = card_panel(bid, cid)
    n_archived = (
        db()
        .execute(
            "SELECT COUNT(*) FROM cards c JOIN columns col ON col.id = c.column_id"
            " WHERE c.board_id = ? AND col.done = 1 AND c.done_at < ?",
            (bid, args[0]),
        )
        .fetchone()[0]
    )
    w = g.ws = (
        db().execute("SELECT * FROM workspaces WHERE id = ?", (b["workspace_id"],)).fetchone()
    )

    def view(**change) -> str:
        """A link to this view with some settings changed; None removes one."""
        params = {"by": by, "who": who, "label": lab, "q": q}
        params.update(change)
        clean = {k: v for k, v in params.items() if v not in ("", None, [])}
        return url_for("board", bid=bid) + ("?" + urlencode(clean, doseq=True) if clean else "")

    return render_template(
        "board.html",
        b=b,
        w=w,
        cols=cols,
        lanes=lanes,
        labels=labels,
        people=people,
        rows=rows,
        cells=cells,
        in_row=in_row,
        per_column=per_column,
        late=late,
        by=by,
        who=who,
        lab=lab,
        q=q,
        n_shown=len(shown),
        n_cards=len(cards),
        n_archived=n_archived,
        open_card=open_card,
        view=view,
        here=request.full_path.rstrip("?"),
    )


def card_panel(bid: int, cid: int) -> dict | None:
    con = db()
    c = con.execute("SELECT * FROM cards WHERE id = ? AND board_id = ?", (cid, bid)).fetchone()
    if c is None:
        return None
    return {
        "c": c,
        "people": {
            r["user_id"]
            for r in con.execute("SELECT user_id FROM card_people WHERE card_id = ?", (cid,))
        },
        "labels": {
            r["label_id"]
            for r in con.execute("SELECT label_id FROM card_labels WHERE card_id = ?", (cid,))
        },
        "subtasks": con.execute(
            "SELECT * FROM subtasks WHERE card_id = ? ORDER BY pos", (cid,)
        ).fetchall(),
        "comments": con.execute(
            "SELECT m.*, u.name, u.pic FROM comments m JOIN users u ON u.id = m.user_id"
            " WHERE card_id = ? ORDER BY m.id",
            (cid,),
        ).fetchall(),
        "author": con.execute("SELECT name FROM users WHERE id = ?", (c["created_by"],)).fetchone(),
    }


@app.route("/b/<int:bid>/archive")
def archive(bid):
    b = get_board(bid)
    q = request.args.get("q", "").strip()
    cutoff = now() - b["archive_days"] * 86400
    cards = card_facts(bid, "AND col.done = 1 AND c.done_at < ?", (cutoff,))
    cards = sorted(
        (c for c in cards if not q or matches(c, q)), key=lambda c: c["done_at"], reverse=True
    )
    w = g.ws = (
        db().execute("SELECT * FROM workspaces WHERE id = ?", (b["workspace_id"],)).fetchone()
    )
    return render_template("archive.html", b=b, w=w, cards=cards, q=q)


# Columns and lanes


def place(con: sqlite3.Connection, table: str, bid: int, before: int | None) -> float:
    """A position just before another column or lane, or after the last one."""
    rows = con.execute(f"SELECT id, pos FROM {table} WHERE board_id = ? ORDER BY pos", (bid,))
    rows = rows.fetchall()
    for i, r in enumerate(rows):
        if r["id"] == before:
            return (rows[i - 1]["pos"] + r["pos"]) / 2 if i else r["pos"] - 1
    return (rows[-1]["pos"] + 1) if rows else 0


@app.route("/b/<int:bid>/columns/new", methods=["POST"])
def new_column(bid):
    get_board(bid)
    name = request.form.get("name", "").strip()
    if name:
        con = db()
        con.execute(
            "INSERT INTO columns (board_id, name, pos) VALUES (?, ?, ?)",
            (bid, name, place(con, "columns", bid, None)),
        )
        bump(con, bid)
        con.commit()
    return back(bid)


@app.route("/column/<int:pid>", methods=["POST"])
def edit_column(pid):
    col = get_part("columns", pid)
    con = db()
    f = request.form
    if f.get("name", "").strip():
        con.execute("UPDATE columns SET name = ? WHERE id = ?", (f["name"].strip(), pid))
    if COLOR.fullmatch(f.get("color", "")):
        con.execute("UPDATE columns SET color = ? WHERE id = ?", (f["color"], pid))
    if "wip" in f:
        wip = int_or_none(f["wip"])
        con.execute(
            "UPDATE columns SET wip = ? WHERE id = ?", (wip if wip and wip > 0 else None, pid)
        )
    if "settings" in f:  # the settings form sends this, so an unticked box means "not done"
        done = int(f.get("done") == "1")
        if done != col["done"]:
            con.execute("UPDATE columns SET done = ? WHERE id = ?", (done, pid))
            con.execute(
                "UPDATE cards SET done_at = ? WHERE column_id = ?", (now() if done else None, pid)
            )
    bump(con, col["board_id"])
    con.commit()
    return back(col["board_id"])


@app.route("/column/<int:pid>/move", methods=["POST"])
def move_column(pid):
    col = get_part("columns", pid)
    con = db()
    before = int_or_none(request.form.get("before"))
    if before != pid:
        pos = place(con, "columns", col["board_id"], before)
        con.execute("UPDATE columns SET pos = ? WHERE id = ?", (pos, pid))
        bump(con, col["board_id"])
        con.commit()
    return back(col["board_id"])


@app.route("/column/<int:pid>/delete", methods=["POST"])
def delete_column(pid):
    col = get_part("columns", pid)
    con = db()
    if con.execute("SELECT 1 FROM cards WHERE column_id = ?", (pid,)).fetchone():
        flash("Move the cards out of the column first, archived ones too.")
    elif (
        con.execute(
            "SELECT COUNT(*) FROM columns WHERE board_id = ?", (col["board_id"],)
        ).fetchone()[0]
        == 1
    ):
        flash("A board needs at least one column.")
    else:
        con.execute("DELETE FROM columns WHERE id = ?", (pid,))
        bump(con, col["board_id"])
        con.commit()
    return back(col["board_id"])


@app.route("/b/<int:bid>/lanes/new", methods=["POST"])
def new_lane(bid):
    get_board(bid)
    name = request.form.get("name", "").strip()
    if name:
        con = db()
        lid = con.execute(
            "INSERT INTO lanes (board_id, name, pos) VALUES (?, ?, ?)",
            (bid, name, place(con, "lanes", bid, None)),
        ).lastrowid
        # The first lane takes in the cards that had none.
        con.execute(
            "UPDATE cards SET lane_id = ? WHERE board_id = ? AND lane_id IS NULL", (lid, bid)
        )
        bump(con, bid)
        con.commit()
    return back(bid)


@app.route("/lane/<int:pid>", methods=["POST"])
def edit_lane(pid):
    lane = get_part("lanes", pid)
    name = request.form.get("name", "").strip()
    if name:
        con = db()
        con.execute("UPDATE lanes SET name = ? WHERE id = ?", (name, pid))
        bump(con, lane["board_id"])
        con.commit()
    return back(lane["board_id"])


@app.route("/lane/<int:pid>/move", methods=["POST"])
def move_lane(pid):
    lane = get_part("lanes", pid)
    con = db()
    before = int_or_none(request.form.get("before"))
    if before != pid:
        con.execute(
            "UPDATE lanes SET pos = ? WHERE id = ?",
            (place(con, "lanes", lane["board_id"], before), pid),
        )
        bump(con, lane["board_id"])
        con.commit()
    return back(lane["board_id"])


@app.route("/lane/<int:pid>/delete", methods=["POST"])
def delete_lane(pid):
    lane = get_part("lanes", pid)
    con = db()
    if con.execute("SELECT 1 FROM cards WHERE lane_id = ?", (pid,)).fetchone():
        flash("Move the cards out of the lane first, archived ones too.")
    else:
        con.execute("DELETE FROM lanes WHERE id = ?", (pid,))
        bump(con, lane["board_id"])
        con.commit()
    return back(lane["board_id"])


@app.route("/b/<int:bid>/labels/new", methods=["POST"])
def new_label(bid):
    get_board(bid)
    name = request.form.get("name", "").strip()
    color = request.form.get("color", "")
    if name:
        con = db()
        lid = con.execute(
            "INSERT INTO labels (board_id, name, color) VALUES (?, ?, ?)",
            (bid, name, color if COLOR.fullmatch(color) else "#8a93a3"),
        ).lastrowid
        cid = int_or_none(request.form.get("card"))
        if cid and get_card(cid)["board_id"] == bid:
            con.execute("INSERT INTO card_labels (card_id, label_id) VALUES (?, ?)", (cid, lid))
        bump(con, bid)
        con.commit()
    return back(bid)


@app.route("/label/<int:pid>/delete", methods=["POST"])
def delete_label(pid):
    label = get_part("labels", pid)
    con = db()
    con.execute("DELETE FROM labels WHERE id = ?", (pid,))
    bump(con, label["board_id"])
    con.commit()
    return back(label["board_id"])


# Cards


def card_pos(con: sqlite3.Connection, column_id: int, lane_id, before: int | None, cid=0) -> float:
    """A position just before another card in the column, or after the last card."""
    if before:
        b = con.execute(
            "SELECT pos FROM cards WHERE id = ? AND column_id = ?", (before, column_id)
        ).fetchone()
        if b:
            prev = con.execute(
                "SELECT MAX(pos) FROM cards WHERE column_id = ? AND pos < ? AND id != ?",
                (column_id, b["pos"], cid),
            ).fetchone()[0]
            return (prev + b["pos"]) / 2 if prev is not None else b["pos"] - 1
    last = con.execute(
        "SELECT MAX(pos) FROM cards WHERE column_id = ? AND id != ?", (column_id, cid)
    ).fetchone()[0]
    return (last or 0) + 1


def set_column(con: sqlite3.Connection, c: sqlite3.Row, col: sqlite3.Row) -> None:
    """Move a card to a column, and start or stop its time in a done column."""
    if col["id"] == c["column_id"]:
        return
    con.execute(
        "UPDATE cards SET column_id = ?, done_at = ? WHERE id = ?",
        (col["id"], (c["done_at"] or now()) if col["done"] else None, c["id"]),
    )


@app.route("/b/<int:bid>/cards/new", methods=["POST"])
def new_card(bid):
    get_board(bid)
    con = db()
    title = request.form.get("title", "").strip()
    col = con.execute(
        "SELECT * FROM columns WHERE id = ? AND board_id = ?",
        (int_or_none(request.form.get("column")), bid),
    ).fetchone()
    lane = con.execute(
        "SELECT id FROM lanes WHERE id = ? AND board_id = ?",
        (int_or_none(request.form.get("lane")), bid),
    ).fetchone()
    has_lanes = con.execute("SELECT 1 FROM lanes WHERE board_id = ?", (bid,)).fetchone()
    if not title or col is None or (has_lanes and lane is None):
        return back(bid)
    ref = con.execute("SELECT next_ref FROM boards WHERE id = ?", (bid,)).fetchone()[0]
    con.execute("UPDATE boards SET next_ref = next_ref + 1 WHERE id = ?", (bid,))
    cid = con.execute(
        "INSERT INTO cards (board_id, ref, column_id, lane_id, title, pos, created_by,"
        " created_at, updated_at, done_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            bid,
            ref,
            col["id"],
            lane["id"] if lane else None,
            title,
            card_pos(con, col["id"], None, None),
            current_user()["id"],
            now(),
            now(),
            now() if col["done"] else None,
        ),
    ).lastrowid
    # Who adds a card in a person's row takes that person along; the same for a label's row.
    row = request.form.get("row", "")
    if request.form.get("by") == "person" and row.isdigit():
        add_person(con, bid, cid, int(row))
    elif request.form.get("by") == "label" and row.isdigit():
        add_label(con, bid, cid, int(row))
    bump(con, bid)
    con.commit()
    return back(bid)


def add_person(con: sqlite3.Connection, bid: int, cid: int, uid: int) -> None:
    ok = con.execute(
        "SELECT 1 FROM members m JOIN boards b ON b.workspace_id = m.workspace_id"
        " WHERE b.id = ? AND m.user_id = ?",
        (bid, uid),
    ).fetchone()
    if ok:
        con.execute(
            "INSERT OR IGNORE INTO card_people (card_id, user_id) VALUES (?, ?)", (cid, uid)
        )


def add_label(con: sqlite3.Connection, bid: int, cid: int, lid: int) -> None:
    if con.execute("SELECT 1 FROM labels WHERE id = ? AND board_id = ?", (lid, bid)).fetchone():
        con.execute(
            "INSERT OR IGNORE INTO card_labels (card_id, label_id) VALUES (?, ?)", (cid, lid)
        )


@app.route("/card/<int:cid>/move", methods=["POST"])
def move_card(cid):
    """Drop a card in a cell. In a person or label view, the row change swaps the person or label."""
    c = get_card(cid)
    bid = c["board_id"]
    con = db()
    f = request.form
    col = con.execute(
        "SELECT * FROM columns WHERE id = ? AND board_id = ?", (int_or_none(f.get("column")), bid)
    ).fetchone()
    if col is None:
        return back(bid)
    by, src, dst = f.get("by", ""), f.get("from", ""), f.get("to", "")
    lane_id = c["lane_id"]
    if not by and dst.isdigit():
        lane = con.execute(
            "SELECT id FROM lanes WHERE id = ? AND board_id = ?", (int(dst), bid)
        ).fetchone()
        lane_id = lane["id"] if lane else lane_id
    elif by in ("person", "label") and src != dst:
        table, key = ("card_people", "user_id") if by == "person" else ("card_labels", "label_id")
        if src.isdigit():
            con.execute(f"DELETE FROM {table} WHERE card_id = ? AND {key} = ?", (cid, int(src)))
        if dst.isdigit():
            (add_person if by == "person" else add_label)(con, bid, cid, int(dst))
    pos = card_pos(con, col["id"], lane_id, int_or_none(f.get("before")), cid)
    con.execute(
        "UPDATE cards SET lane_id = ?, pos = ?, updated_at = ? WHERE id = ?",
        (lane_id, pos, now(), cid),
    )
    set_column(con, c, col)
    bump(con, bid)
    con.commit()
    return back(bid)


@app.route("/card/<int:cid>/edit", methods=["POST"])
def edit_card(cid):
    c = get_card(cid)
    bid = c["board_id"]
    con = db()
    f = request.form
    if f.get("title", "").strip():
        con.execute("UPDATE cards SET title = ? WHERE id = ?", (f["title"].strip(), cid))
    if "body" in f:
        con.execute("UPDATE cards SET body = ? WHERE id = ?", (f["body"].strip(), cid))
    if "due" in f:  # an empty box clears the date
        due = parse_day(f["due"])
        if due or not f["due"].strip():
            con.execute("UPDATE cards SET due = ? WHERE id = ?", (due, cid))
        else:
            flash("Write the date as 2026-11-14 or 14.11.2026.")
    col = con.execute(
        "SELECT * FROM columns WHERE id = ? AND board_id = ?", (int_or_none(f.get("column")), bid)
    ).fetchone()
    if col is not None and col["id"] != c["column_id"]:
        con.execute(
            "UPDATE cards SET pos = ? WHERE id = ?",
            (card_pos(con, col["id"], None, None, cid), cid),
        )
        set_column(con, c, col)
    lane = con.execute(
        "SELECT id FROM lanes WHERE id = ? AND board_id = ?", (int_or_none(f.get("lane")), bid)
    ).fetchone()
    if lane is not None:
        con.execute("UPDATE cards SET lane_id = ? WHERE id = ?", (lane["id"], cid))
    if "people" in f:  # the people form sends this, so no ticks means nobody
        con.execute("DELETE FROM card_people WHERE card_id = ?", (cid,))
        for uid in f.getlist("person", type=int):
            add_person(con, bid, cid, uid)
    if "labels" in f:
        con.execute("DELETE FROM card_labels WHERE card_id = ?", (cid,))
        for lid in f.getlist("label", type=int):
            add_label(con, bid, cid, lid)
    con.execute("UPDATE cards SET updated_at = ? WHERE id = ?", (now(), cid))
    bump(con, bid)
    con.commit()
    return back(bid)


@app.route("/card/<int:cid>/delete", methods=["POST"])
def delete_card(cid):
    c = get_card(cid)
    con = db()
    con.execute("DELETE FROM cards WHERE id = ?", (cid,))
    bump(con, c["board_id"])
    con.commit()
    flash(f"Deleted #{c['ref']} {c['title']}.")
    return redirect(url_for("board", bid=c["board_id"]))


@app.route("/card/<int:cid>/subtasks/new", methods=["POST"])
def new_subtask(cid):
    c = get_card(cid)
    title = request.form.get("title", "").strip()
    if title:
        con = db()
        last = con.execute("SELECT MAX(pos) FROM subtasks WHERE card_id = ?", (cid,)).fetchone()[0]
        con.execute(
            "INSERT INTO subtasks (card_id, title, pos) VALUES (?, ?, ?)",
            (cid, title, (last or 0) + 1),
        )
        bump(con, c["board_id"])
        con.commit()
    return back(c["board_id"])


def get_subtask(sid: int) -> tuple:
    s = db().execute("SELECT * FROM subtasks WHERE id = ?", (sid,)).fetchone()
    if s is None:
        abort(404)
    return s, get_card(s["card_id"])


@app.route("/subtask/<int:sid>", methods=["POST"])
def edit_subtask(sid):
    _s, c = get_subtask(sid)
    con = db()
    con.execute(
        "UPDATE subtasks SET done = ? WHERE id = ?", (int(request.form.get("done") == "1"), sid)
    )
    bump(con, c["board_id"])
    con.commit()
    return back(c["board_id"])


@app.route("/subtask/<int:sid>/delete", methods=["POST"])
def delete_subtask(sid):
    _s, c = get_subtask(sid)
    con = db()
    con.execute("DELETE FROM subtasks WHERE id = ?", (sid,))
    bump(con, c["board_id"])
    con.commit()
    return back(c["board_id"])


@app.route("/card/<int:cid>/comment", methods=["POST"])
def comment(cid):
    c = get_card(cid)
    body = request.form.get("body", "").strip()
    if body:
        con = db()
        con.execute(
            "INSERT INTO comments (card_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (cid, current_user()["id"], body, now()),
        )
        con.execute("UPDATE cards SET updated_at = ? WHERE id = ?", (now(), cid))
        bump(con, c["board_id"])
        con.commit()
    return back(c["board_id"])


# Import from Taiga: Admin, Project, Export gives a JSON file


def taiga_time(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z").timestamp())
    except ValueError:
        return None


def import_taiga(con: sqlite3.Connection, wid: int, data: dict, uid: int) -> tuple:
    """Make a board from a Taiga project export. Returns the board id and what was left out."""
    names = {}  # email -> name, from the people who wrote history
    for item in data.get("user_stories", []) + data.get("tasks", []):
        for h in item.get("history") or []:
            user = h.get("user")
            if isinstance(user, list) and len(user) == 2 and user[0]:
                names[user[0].lower()] = user[1]

    users = {}

    def person(email) -> int | None:
        if not email or "@" not in str(email):
            return None
        email = str(email).lower()
        if email not in users:
            users[email] = find_or_add_user(con, email, names.get(email, ""))
            con.execute(
                "INSERT OR IGNORE INTO members (workspace_id, user_id) VALUES (?, ?)",
                (wid, users[email]),
            )
        return users[email]

    for m in data.get("memberships", []):
        person(m.get("email") or m.get("user"))

    bid = con.execute(
        "INSERT INTO boards (workspace_id, name, created_at) VALUES (?, ?, ?)",
        (wid, str(data.get("name") or "Taiga board"), now()),
    ).lastrowid
    cols = {}
    for s in sorted(data.get("us_statuses", []), key=lambda s: s.get("order", 0)):
        color = s.get("color") or ""
        cols[s["name"]] = (
            con.execute(
                "INSERT INTO columns (board_id, name, color, wip, done, pos) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    bid,
                    s["name"],
                    color if COLOR.fullmatch(color) else "#8a93a3",
                    s.get("wip_limit") or None,
                    int(bool(s.get("is_closed"))),
                    s.get("order", 0),
                ),
            ).lastrowid,
            bool(s.get("is_closed")),
        )
    if not cols:
        raise ValueError("The file has no user story statuses, so it is no Taiga project export.")
    lanes = {}
    for s in sorted(data.get("swimlanes", []), key=lambda s: s.get("order", 0)):
        lanes[s["name"]] = con.execute(
            "INSERT INTO lanes (board_id, name, pos) VALUES (?, ?, ?)",
            (bid, s["name"], s.get("order", 0)),
        ).lastrowid
    default_lane = lanes.get(data.get("default_swimlane")) or next(iter(lanes.values()), None)
    labels = {}
    task_done = {s["name"]: bool(s.get("is_closed")) for s in data.get("task_statuses", [])}

    cards = {}
    left_out = {"files": 0}
    first_col = next(iter(cols.values()))
    for us in data.get("user_stories", []):
        col_id, done = cols.get(us.get("status"), first_col)
        created = taiga_time(us.get("created_date")) or now()
        finished = (
            taiga_time(us.get("finish_date")) or taiga_time(us.get("modified_date")) or created
        )
        cid = con.execute(
            "INSERT INTO cards (board_id, ref, column_id, lane_id, title, body, pos, created_by,"
            " created_at, updated_at, done_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bid,
                us["ref"],
                col_id,
                lanes.get(us.get("swimlane"), default_lane),
                us.get("subject") or f"#{us['ref']}",
                us.get("description") or "",
                us.get("kanban_order") or 0,
                person(us.get("owner")),
                created,
                taiga_time(us.get("modified_date")) or created,
                finished if done else None,
            ),
        ).lastrowid
        cards[us["ref"]] = cid
        for email in us.get("assigned_users") or [us.get("assigned_to")]:
            pid = person(email)
            if pid:
                con.execute(
                    "INSERT OR IGNORE INTO card_people (card_id, user_id) VALUES (?, ?)", (cid, pid)
                )
        for tag in us.get("tags") or []:
            name, color = (tag + [None])[:2] if isinstance(tag, list) else (tag, None)
            if name not in labels:
                labels[name] = con.execute(
                    "INSERT INTO labels (board_id, name, color) VALUES (?, ?, ?)",
                    (bid, name, color if color and COLOR.fullmatch(color) else "#8a93a3"),
                ).lastrowid
            con.execute(
                "INSERT OR IGNORE INTO card_labels (card_id, label_id) VALUES (?, ?)",
                (cid, labels[name]),
            )
        for h in us.get("history") or []:
            if h.get("comment") and not h.get("delete_comment_date"):
                user = h.get("user")
                pid = person(user[0] if isinstance(user, list) else user) or uid
                con.execute(
                    "INSERT INTO comments (card_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
                    (cid, pid, h["comment"], taiga_time(h.get("created_at")) or created),
                )
        left_out["files"] += len(us.get("attachments") or [])

    tasks = sorted(data.get("tasks", []), key=lambda t: t.get("us_order") or 0)
    for pos, t in enumerate(tasks):
        cid = cards.get(t.get("user_story"))
        if cid is None:
            continue
        con.execute(
            "INSERT INTO subtasks (card_id, ref, title, done, pos) VALUES (?, ?, ?, ?, ?)",
            (
                cid,
                t.get("ref"),
                t.get("subject") or "",
                int(task_done.get(t.get("status"), False)),
                pos,
            ),
        )
        left_out["files"] += len(t.get("attachments") or [])
    refs = [x.get("ref") or 0 for x in data.get("user_stories", []) + tasks]
    con.execute("UPDATE boards SET next_ref = ? WHERE id = ?", (max(refs, default=0) + 1, bid))
    return bid, left_out


@app.route("/w/<int:wid>/import", methods=["POST"])
def import_board(wid):
    get_workspace(wid)
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect(url_for("workspace", wid=wid))
    con = db()
    try:
        data = json.load(f.stream)
        bid, left_out = import_taiga(con, wid, data, current_user()["id"])
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        con.rollback()
        flash(f"Could not read that file as a Taiga export: {e}")
        return redirect(url_for("workspace", wid=wid))
    con.commit()
    msg = "Imported."
    if left_out["files"]:
        msg += f" {left_out['files']} attached files were left out; lanes has no files yet."
    flash(msg + " New people can log in once you send them an invite link from the workspace page.")
    return redirect(url_for("board", bid=bid))


# Users


def admin_only() -> None:
    if not current_user()["is_admin"]:
        abort(403)


# Profile: each person's name, picture, colours and password

PICTURE_TYPES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
)
PICTURE_MAX = 1024 * 1024  # the page shrinks a picture to 256 pixels before it sends it


def picture_type(data: bytes) -> str | None:
    """The type of a PNG, JPEG, GIF or WebP picture, read from its first bytes; None for others."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((kind for magic, kind in PICTURE_TYPES if data.startswith(magic)), None)


def read_picture(file) -> tuple[str, bytes] | None:
    """An uploaded picture as (type, data), or None after a message saying what is wrong."""
    data = file.read(PICTURE_MAX + 1)
    kind = picture_type(data)
    if len(data) > PICTURE_MAX:
        flash("That picture is over 1 MB. Pick a smaller one.")
    elif not kind:
        flash("Use a PNG, JPEG, GIF or WebP picture.")
    else:
        return kind, data
    return None


def serve_picture(row: sqlite3.Row | None) -> Response:
    if not row:
        abort(404)
    resp = Response(row["data"], mimetype=row["type"])
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"  # the link has a version
    return resp


@app.route("/u/<int:uid>/picture")
def picture(uid):
    return serve_picture(
        db().execute("SELECT type, data FROM pictures WHERE user_id = ?", (uid,)).fetchone()
    )


@app.route("/w/<int:wid>/logo", methods=["GET", "POST"])
def workspace_logo(wid):
    get_workspace(wid)  # members only
    con = db()
    if request.method == "GET":
        return serve_picture(
            con.execute("SELECT type, data FROM logos WHERE workspace_id = ?", (wid,)).fetchone()
        )
    if request.form.get("remove_logo"):
        con.execute("DELETE FROM logos WHERE workspace_id = ?", (wid,))
        con.execute("UPDATE workspaces SET logo = 0 WHERE id = ?", (wid,))
    elif request.files.get("logo"):
        pic = read_picture(request.files["logo"])
        if pic:
            con.execute(
                "INSERT OR REPLACE INTO logos (workspace_id, type, data) VALUES (?, ?, ?)",
                (wid, *pic),
            )
            con.execute("UPDATE workspaces SET logo = ? WHERE id = ?", (now(), wid))
    con.commit()
    return redirect(url_for("workspace", wid=wid))


@app.route("/me", methods=["GET", "POST"])
def profile():
    if request.method == "GET":
        return render_template("profile.html")
    con, me, f = db(), current_user(), request.form
    if "name" in f:
        name = f["name"].strip()[:80]
        if name:
            con.execute("UPDATE users SET name = ? WHERE id = ?", (name, me["id"]))
    if f.get("theme") in ("", "light", "dark"):
        con.execute("UPDATE users SET theme = ? WHERE id = ?", (f["theme"], me["id"]))
    if f.get("remove_picture"):
        con.execute("DELETE FROM pictures WHERE user_id = ?", (me["id"],))
        con.execute("UPDATE users SET pic = 0 WHERE id = ?", (me["id"],))
    elif request.files.get("picture"):
        pic = read_picture(request.files["picture"])
        if pic:
            con.execute(
                "INSERT OR REPLACE INTO pictures (user_id, type, data) VALUES (?, ?, ?)",
                (me["id"], *pic),
            )
            con.execute("UPDATE users SET pic = ? WHERE id = ?", (now(), me["id"]))
    if "password" in f:
        new = f.get("new_password", "")
        if not check_password_hash(me["pw_hash"], f["password"]):
            flash("The present password is wrong.")
        elif len(new) < 12:
            flash("Use at least 12 characters.")
        elif new != f.get("new_password2"):
            flash("The two new passwords differ.")
        else:
            con.execute(
                "UPDATE users SET pw_hash = ? WHERE id = ?",
                (generate_password_hash(new), me["id"]),
            )
            flash("Your password is changed.")
    con.commit()
    nxt = f.get("next", "")  # the colour switch at the top goes back to the page it was on
    return redirect(nxt if re.fullmatch(r"/(?![/\\])[^\\]*", nxt) else url_for("profile"))


@app.route("/admin")
def admin():
    admin_only()
    # Someone taken off every workspace has left; the account stays for their old comments.
    users = (
        db()
        .execute(
            "SELECT * FROM users u WHERE u.is_admin"
            " OR EXISTS (SELECT 1 FROM members m WHERE m.user_id = u.id) ORDER BY name"
        )
        .fetchall()
    )
    return render_template("admin.html", users=users)


@app.route("/admin/<int:uid>/invite", methods=["POST"])
def admin_invite(uid):
    admin_only()
    user = db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user is None:
        abort(404)
    flash("New invite link for {}: {}".format(user["name"], make_invite(db(), uid)))
    return redirect(url_for("admin"))


def overdue(con: sqlite3.Connection, wid: int) -> str:
    """A message that lists a workspace's cards past their date, by person. Empty if there are none."""
    rows = con.execute(
        "SELECT c.id, c.ref, c.title, c.due, c.board_id, u.name FROM cards c"
        " JOIN columns col ON col.id = c.column_id JOIN boards b ON b.id = c.board_id"
        " LEFT JOIN card_people p ON p.card_id = c.id LEFT JOIN users u ON u.id = p.user_id"
        " WHERE b.workspace_id = ? AND c.due < ? AND NOT col.done"
        " ORDER BY u.name IS NULL, u.name, c.due, c.ref",
        (wid, today()),
    ).fetchall()
    lines, who = [], False
    for r in rows:
        if r["name"] != who:
            who = r["name"]
            lines.append(f"@{who}" if who else "Nobody")
        link = f"{BASE_URL}/b/{r['board_id']}?card={r['id']}"
        lines.append(f"- #{r['ref']} {r['title']} (due {r['due']}) {link}")
    return "Cards past their date:\n" + "\n".join(lines) if lines else ""


def main(args: list) -> None:
    init_db()
    if args[:1] == ["adduser"] and len(args) >= 3:
        with closing(connect()) as con:
            name = " ".join(a for a in args[2:] if a != "--admin")
            uid = find_or_add_user(con, args[1], name, "--admin" in args)
            print(make_invite(con, uid))
    elif args[:1] == ["invite"] and len(args) == 2:
        with closing(connect()) as con:
            row = con.execute("SELECT id FROM users WHERE email = ?", (args[1].lower(),)).fetchone()
            print(make_invite(con, row["id"]) if row else "No such user.")
    elif args[:1] == ["overdue"] and len(args) == 2:
        with closing(connect()) as con:
            print(overdue(con, int(args[1])))
    elif not args:
        from waitress import serve

        serve(app, host="127.0.0.1", port=int(os.environ.get("LANES_PORT", "8092")), threads=8)
    else:
        print("usage: app.py [adduser EMAIL NAME [--admin] | invite EMAIL | overdue WORKSPACE-ID]")


if __name__ == "__main__":
    main(sys.argv[1:])
