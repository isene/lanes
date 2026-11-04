import json
import os
import re
import sys
import tempfile
from io import BytesIO
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ.update(LANES_DATA=str(TMP / "data"), LANES_DEV="1", LANES_SECRET="test")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.security import check_password_hash

import app as lanes

lanes.init_db()


def user(email, name, admin=False):
    """Make a user and finish the invite, so the user is logged in."""
    with lanes.connect() as con:
        uid = lanes.find_or_add_user(con, email, name, admin)
        link = lanes.make_invite(con, uid)
        secret = con.execute("SELECT totp_secret FROM users WHERE id = ?", (uid,)).fetchone()[0]
    client = lanes.app.test_client()
    path = link.replace(lanes.BASE_URL, "")
    client.get(path)
    r = post(
        client,
        path,
        password="correct horse battery",
        password2="correct horse battery",
        code=lanes.totp(secret),
    )
    assert r.status_code == 302
    client.get("/")  # as the browser does after the redirect; it sets a new form token
    return client, uid


def post(client, path, **form):
    with client.session_transaction() as s:
        form.setdefault("csrf", s.get("csrf", ""))
    return client.post(path, data=form)


def last_id(location):
    return int(re.search(r"/(\d+)$", location.split("?")[0]).group(1))


def workspace_with_board(client, name="Team"):
    r = post(client, "/w/new", name=name)
    wid = last_id(r.headers["Location"])
    r = post(client, f"/w/{wid}/boards/new", name="Work")
    return wid, last_id(r.headers["Location"])


def q(sql, *args):
    with lanes.connect() as con:
        return con.execute(sql, args).fetchall()


def test_totp_matches_rfc_6238():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # the RFC test key, in base32
    assert lanes.totp(secret, 59) == "287082"


def test_pages_need_login_and_a_form_token():
    client = lanes.app.test_client()
    assert client.get("/").headers["Location"].endswith("/login")
    assert client.post("/login", data={"email": "x"}).status_code == 400


def test_outsiders_cannot_see_or_change_a_board():
    ada, _ = user("ada@example.com", "Ada Lovelace", admin=True)
    eve, _ = user("eve@example.com", "Eve")
    wid, bid = workspace_with_board(ada)
    post(
        ada,
        f"/b/{bid}/cards/new",
        title="Secret plan",
        column=q("SELECT id FROM columns WHERE board_id = ?", bid)[0][0],
    )
    cid = q("SELECT id FROM cards WHERE board_id = ?", bid)[0][0]
    col = q("SELECT id FROM columns WHERE board_id = ?", bid)[0][0]
    for path in (
        f"/w/{wid}",
        f"/b/{bid}",
        f"/b/{bid}/archive",
        f"/b/{bid}/version",
        f"/b/{bid}/colors.css",
        f"/b/{bid}?card={cid}",
    ):
        assert eve.get(path).status_code == 404, path
    for path in (
        f"/card/{cid}/move",
        f"/card/{cid}/edit",
        f"/card/{cid}/delete",
        f"/column/{col}",
        f"/b/{bid}/cards/new",
        f"/w/{wid}/invite",
    ):
        assert (
            post(eve, path, column=col, title="x", name="x", email="x@example.com").status_code
            == 404
        ), path
    assert b"Secret plan" not in eve.get("/").data
    assert post(eve, "/w/new", name="Mine").status_code == 403  # only an admin makes workspaces


def test_cards_move_between_columns_and_lanes_and_leave_for_the_archive():
    bob, _ = user("bob@example.com", "Bob Builder", admin=True)
    _wid, bid = workspace_with_board(bob)
    todo, doing, done = [
        r[0] for r in q("SELECT id FROM columns WHERE board_id = ? ORDER BY pos", bid)
    ]
    post(bob, f"/b/{bid}/cards/new", title="Before lanes", column=todo)
    post(bob, f"/b/{bid}/lanes/new", name="Stability")
    post(bob, f"/b/{bid}/lanes/new", name="Support")
    stab, supp = [r[0] for r in q("SELECT id FROM lanes WHERE board_id = ? ORDER BY pos", bid)]
    first = q("SELECT id, lane_id, ref FROM cards WHERE board_id = ?", bid)[0]
    assert first["lane_id"] == stab and first["ref"] == 1  # the first lane takes in the loose cards
    post(bob, f"/b/{bid}/cards/new", title="Patch servers", column=todo, lane=supp)
    cid = q("SELECT id FROM cards WHERE title = 'Patch servers'")[0][0]
    v0 = q("SELECT version FROM boards WHERE id = ?", bid)[0][0]
    r = post(bob, f"/card/{cid}/move", column=done, to=str(stab), before="", next=f"/b/{bid}?by=")
    assert r.headers["Location"].endswith(f"/b/{bid}?by=")
    card = q("SELECT * FROM cards WHERE id = ?", cid)[0]
    assert card["column_id"] == done and card["lane_id"] == stab and card["done_at"]
    assert q("SELECT version FROM boards WHERE id = ?", bid)[0][0] > v0
    assert b"Patch servers" in bob.get(f"/b/{bid}").data
    with lanes.connect() as con:  # 20 days later, past the board's 14
        con.execute("UPDATE cards SET done_at = done_at - 20 * 86400 WHERE id = ?", (cid,))
    assert b"Patch servers" not in bob.get(f"/b/{bid}").data
    assert b"Patch servers" in bob.get(f"/b/{bid}/archive").data
    assert b"Patch servers" in bob.get(f"/b/{bid}?card={cid}").data  # the panel still opens it
    post(bob, f"/card/{cid}/move", column=doing)
    assert q("SELECT done_at FROM cards WHERE id = ?", cid)[0][0] is None
    post(bob, f"/column/{doing}/delete")
    assert q("SELECT id FROM columns WHERE id = ?", doing)  # it still has a card


def test_people_view_moves_a_card_from_one_person_to_another():
    cat, cat_id = user("cat@example.com", "Cat", admin=True)
    wid, bid = workspace_with_board(cat)
    post(cat, f"/w/{wid}/invite", name="Dan", email="dan@example.com")
    dan_id = q("SELECT id FROM users WHERE email = 'dan@example.com'")[0][0]
    todo = q("SELECT id FROM columns WHERE board_id = ? ORDER BY pos", bid)[0][0]
    post(
        cat,
        f"/b/{bid}/cards/new",
        title="Renew certificate",
        column=todo,
        by="person",
        row=str(cat_id),
    )
    cid = q("SELECT id FROM cards WHERE title = 'Renew certificate'")[0][0]
    assert [r[0] for r in q("SELECT user_id FROM card_people WHERE card_id = ?", cid)] == [cat_id]
    post(
        cat,
        f"/card/{cid}/move",
        column=todo,
        by="person",
        **{"from": str(cat_id), "to": str(dan_id)},
    )
    assert [r[0] for r in q("SELECT user_id FROM card_people WHERE card_id = ?", cid)] == [dan_id]
    page = cat.get(f"/b/{bid}?by=person").data.decode()
    assert "Renew certificate" in page and "Nobody" in page


def test_sub_tasks_show_open_ones_and_a_count():
    eva, _ = user("eva@example.com", "Eva", admin=True)
    _wid, bid = workspace_with_board(eva)
    todo = q("SELECT id FROM columns WHERE board_id = ? ORDER BY pos", bid)[0][0]
    post(eva, f"/b/{bid}/cards/new", title="Backup", column=todo)
    cid = q("SELECT id FROM cards WHERE title = 'Backup'")[0][0]
    for t in ("Find a tool", "Test restore"):
        post(eva, f"/card/{cid}/subtasks/new", title=t)
    sid = q("SELECT id FROM subtasks WHERE title = 'Find a tool'")[0][0]
    post(eva, f"/subtask/{sid}", done="1")
    page = eva.get(f"/b/{bid}").data.decode()
    assert "1/2" in page and "Test restore" in page and "Find a tool" not in page


def taiga_export():
    who = ["fay@example.com", "Fay Flow"]
    return {
        "name": "Infra",
        "memberships": [{"email": "fay@example.com", "user": "fay@example.com"}],
        "us_statuses": [
            {
                "name": "Backlog",
                "order": 0,
                "is_closed": False,
                "color": "#E44057",
                "wip_limit": None,
            },
            {"name": "Done", "order": 1, "is_closed": True, "color": "#A8E440", "wip_limit": None},
        ],
        "swimlanes": [{"name": "Stability", "order": 0}, {"name": "Support", "order": 1}],
        "default_swimlane": "Stability",
        "task_statuses": [
            {"name": "New", "is_closed": False},
            {"name": "Closed", "is_closed": True},
        ],
        "user_stories": [
            {
                "ref": 13,
                "subject": "Clean up DNS",
                "description": "Three domains.",
                "status": "Backlog",
                "swimlane": "Support",
                "kanban_order": 2,
                "owner": "fay@example.com",
                "assigned_users": ["fay@example.com"],
                "tags": [["dns", "#3366ff"]],
                "created_date": "2024-06-24T09:14:11+0000",
                "finish_date": None,
                "modified_date": "2024-06-25T09:14:11+0000",
                "attachments": [{}],
                "history": [
                    {
                        "user": who,
                        "comment": "Started.",
                        "created_at": "2024-06-26T10:00:00+0000",
                        "delete_comment_date": None,
                    }
                ],
            },
            {
                "ref": 1,
                "subject": "Move to Taiga",
                "description": "",
                "status": "Done",
                "swimlane": None,
                "kanban_order": 1,
                "owner": "fay@example.com",
                "assigned_users": [],
                "tags": [],
                "created_date": "2024-06-24T09:14:11+0000",
                "finish_date": "2024-08-14T08:48:14+0000",
                "modified_date": "2024-08-14T08:48:14+0000",
                "attachments": [],
                "history": [],
            },
        ],
        "tasks": [
            {
                "ref": 72,
                "user_story": 13,
                "subject": "Drop old domain",
                "status": "Closed",
                "us_order": 1,
                "attachments": [],
            },
            {
                "ref": 74,
                "user_story": 13,
                "subject": "Add a check",
                "status": "New",
                "us_order": 2,
                "attachments": [],
            },
        ],
    }


def test_taiga_import_keeps_columns_lanes_numbers_and_sub_tasks():
    gus, _ = user("gus@example.com", "Gus", admin=True)
    r = post(gus, "/w/new", name="Infra team")
    wid = last_id(r.headers["Location"])
    with gus.session_transaction() as s:
        token = s["csrf"]
    r = gus.post(
        f"/w/{wid}/import",
        data={"csrf": token, "file": (BytesIO(json.dumps(taiga_export()).encode()), "export.json")},
    )
    bid = last_id(r.headers["Location"])
    assert [
        (c["name"], c["done"])
        for c in q("SELECT * FROM columns WHERE board_id = ? ORDER BY pos", bid)
    ] == [("Backlog", 0), ("Done", 1)]
    lanes_ = {r["name"]: r["id"] for r in q("SELECT * FROM lanes WHERE board_id = ?", bid)}
    dns = q("SELECT * FROM cards WHERE board_id = ? AND ref = 13", bid)[0]
    old = q("SELECT * FROM cards WHERE board_id = ? AND ref = 1", bid)[0]
    assert dns["lane_id"] == lanes_["Support"] and old["lane_id"] == lanes_["Stability"]
    assert old["done_at"] and dns["done_at"] is None
    assert [
        (s["ref"], s["done"])
        for s in q("SELECT * FROM subtasks WHERE card_id = ? ORDER BY pos", dns["id"])
    ] == [(72, 1), (74, 0)]
    fay = q("SELECT * FROM users WHERE email = 'fay@example.com'")[0]
    assert fay["name"] == "Fay Flow" and fay["pw_hash"] is None
    assert q("SELECT 1 FROM members WHERE workspace_id = ? AND user_id = ?", wid, fay["id"])
    assert q("SELECT body FROM comments WHERE card_id = ?", dns["id"])[0][0] == "Started."
    assert q("SELECT next_ref FROM boards WHERE id = ?", bid)[0][0] == 75
    css = gus.get(f"/b/{bid}/colors.css").data.decode()
    assert "#E44057" in css and "#3366ff" in css
    flashed = gus.get(f"/b/{bid}").data.decode()
    assert "1 attached files were left out" in flashed


def test_render_escapes_html_and_links_urls():
    out = str(lanes.render("<script>x</script> see https://example.com/a?b=1&c=2"))
    assert "<script>" not in out and '<a href="https://example.com/a?b=1&amp;c=2">' in out


PNG = b"\x89PNG\r\n\x1a\n" + bytes(40)  # only the first bytes are read


def test_profile_sets_name_colours_picture_and_password():
    client, uid = user("pia@example.com", "Pia")
    post(client, "/me", name="Pia Hansen")
    post(client, "/me", theme="dark")
    post(client, "/me", theme="purple")  # not one of the choices, so nothing changes
    assert tuple(q("SELECT name, theme FROM users WHERE id = ?", uid)[0]) == ("Pia Hansen", "dark")
    assert 'data-theme="dark"' in client.get("/me").get_data(as_text=True)

    post(client, "/me", picture=(BytesIO(b"<svg onload=alert(1)>"), "x.svg"))
    assert q("SELECT pic FROM users WHERE id = ?", uid)[0][0] == 0
    post(client, "/me", picture=(BytesIO(PNG), "me.png"))
    pic = q("SELECT pic FROM users WHERE id = ?", uid)[0][0]
    r = client.get(f"/u/{uid}/picture?v={pic}")
    assert pic and r.mimetype == "image/png" and r.data == PNG
    assert f'src="/u/{uid}/picture?v={pic}"' in client.get("/me").get_data(as_text=True)
    post(client, "/me", remove_picture="1")
    assert client.get(f"/u/{uid}/picture").status_code == 404

    new = "a much longer password"
    post(client, "/me", password="wrong", new_password=new, new_password2=new)
    assert check_password_hash(
        q("SELECT pw_hash FROM users WHERE id = ?", uid)[0][0], "correct horse battery"
    )
    post(client, "/me", password="correct horse battery", new_password=new, new_password2=new)
    assert check_password_hash(q("SELECT pw_hash FROM users WHERE id = ?", uid)[0][0], new)


def test_workspace_logo_and_the_way_back_to_the_board():
    ada, _ = user("ola@example.com", "Ola", admin=True)
    eve, _ = user("eva@example.com", "Eva")
    wid, bid = workspace_with_board(ada)
    post(ada, f"/w/{wid}/logo", logo=(BytesIO(PNG), "logo.png"))
    v = q("SELECT logo FROM workspaces WHERE id = ?", wid)[0][0]
    assert v and ada.get(f"/w/{wid}/logo?v={v}").data == PNG
    assert eve.get(f"/w/{wid}/logo").status_code == 404  # not a member

    ada.get(f"/b/{bid}")
    page = ada.get("/me").get_data(as_text=True)  # the top left leads back to the last board
    assert f'class="wslink" href="/b/{bid}"' in page and f"/w/{wid}/logo?v={v}" in page
    assert "wslink" not in eve.get("/me").get_data(as_text=True)

    r = post(ada, "/me", theme="dark", next=f"/b/{bid}?by=person")
    assert r.headers["Location"].endswith(f"/b/{bid}?by=person")
    r = post(ada, "/me", theme="light", next="//evil.example.com/")
    assert r.headers["Location"].endswith("/me")


def test_users_page_lists_only_people_in_a_workspace():
    hal, _ = user("hal@example.com", "Hal", admin=True)
    _, ivy = user("ivy@example.com", "Ivy Gone")
    wid, _ = workspace_with_board(hal)
    with lanes.connect() as con:
        con.execute("INSERT INTO members (workspace_id, user_id) VALUES (?, ?)", (wid, ivy))
    assert b"Ivy Gone" in hal.get("/admin").data
    with lanes.connect() as con:
        con.execute("DELETE FROM members WHERE user_id = ?", (ivy,))
    assert b"Ivy Gone" not in hal.get("/admin").data


def test_an_admin_takes_someone_off_a_workspace():
    jo, jo_id = user("jo@example.com", "Jo", admin=True)
    kim, kim_id = user("kim@example.com", "Kim")
    wid, _ = workspace_with_board(jo)
    post(jo, f"/w/{wid}/invite", name="Kim", email="kim@example.com")
    assert b"Remove</button>" in jo.get(f"/w/{wid}").data
    assert b"Remove</button>" not in kim.get(f"/w/{wid}").data
    assert post(kim, f"/w/{wid}/remove/{jo_id}").status_code == 403  # only an admin removes
    assert post(jo, f"/w/{wid}/remove/{jo_id}").status_code == 400  # never yourself
    post(jo, f"/w/{wid}/remove/{kim_id}")
    assert not q("SELECT 1 FROM members WHERE user_id = ?", kim_id)
    assert kim.get(f"/w/{wid}").status_code == 404


def test_a_card_past_its_date_shows_red_and_is_listed_by_person():
    uma, uma_id = user("uma@example.com", "Uma Ink", admin=True)
    wid, bid = workspace_with_board(uma)
    todo, _doing, done = [
        r[0] for r in q("SELECT id FROM columns WHERE board_id = ? ORDER BY pos", bid)
    ]
    post(uma, f"/b/{bid}/cards/new", title="Renew domain", column=todo)
    post(uma, f"/b/{bid}/cards/new", title="Order disks", column=todo)
    late, soon = [r[0] for r in q("SELECT id FROM cards WHERE board_id = ? ORDER BY id", bid)]
    post(uma, f"/card/{late}/edit", people="1", person=str(uma_id))
    post(uma, f"/card/{late}/edit", due="2020-01-05")
    post(uma, f"/card/{soon}/edit", due="2999-12-31")
    page = uma.get(f"/b/{bid}").data.decode()
    assert 'class="due late"' in page and "2020-01-05" in page and "2999-12-31" in page
    assert '<span class="dot">1</span>' in page
    with lanes.connect() as con:
        message = lanes.overdue(con, wid)
    assert message.splitlines()[1:] == [
        "@Uma Ink",
        f"- #1 Renew domain (due 2020-01-05) {lanes.BASE_URL}/b/{bid}?card={late}",
    ]
    post(uma, f"/card/{late}/move", column=done)  # done is never late
    with lanes.connect() as con:
        assert lanes.overdue(con, wid) == ""
    assert 'class="dot"' not in uma.get(f"/b/{bid}").data.decode()
    post(uma, f"/card/{soon}/edit", due="14.11.2030")  # Norwegian order
    assert q("SELECT due FROM cards WHERE id = ?", soon)[0][0] == "2030-11-14"
    post(uma, f"/card/{soon}/edit", due="31.02.2030")  # no such day: the date stays
    assert q("SELECT due FROM cards WHERE id = ?", soon)[0][0] == "2030-11-14"
    post(uma, f"/card/{soon}/edit", due="")
    assert q("SELECT due FROM cards WHERE id = ?", soon)[0][0] is None
    assert lanes.parse_day("3.12") == lanes.today()[:4] + "-12-03"


def test_password_forms_name_the_account_for_password_managers():
    with lanes.connect() as con:
        uid = lanes.find_or_add_user(con, "vic@example.com", "Vic")
        link = lanes.make_invite(con, uid)
    page = lanes.app.test_client().get(link.replace(lanes.BASE_URL, "")).data.decode()
    assert 'value="vic@example.com" readonly autocomplete="username"' in page
    wes, _ = user("wes@example.com", "Wes")
    assert (
        'value="wes@example.com" readonly autocomplete="username"' in wes.get("/me").data.decode()
    )


def test_a_new_card_opens_in_the_panel():
    xen, _ = user("xen@example.com", "Xen New", admin=True)
    _wid, bid = workspace_with_board(xen)
    col = q("SELECT id FROM columns WHERE board_id = ? ORDER BY pos", bid)[0][0]
    r = post(
        xen, f"/b/{bid}/cards/new", title="Plan", column=col, next=f"/b/{bid}?by=person&card=1"
    )
    cid = q("SELECT id FROM cards WHERE board_id = ? AND title = 'Plan'", bid)[0][0]
    assert r.headers["Location"].endswith(f"/b/{bid}?by=person&card={cid}")
    page = xen.get(r.headers["Location"]).get_data(as_text=True)
    assert 'id="cardpanel"' in page and 'class="due"' in page
    assert 'class="menu new"' in page


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a6e50000000049454e44ae426082"
)


def test_files_on_a_card_and_the_picture_it_shows():
    yan, _ = user("yan@example.com", "Yan Files", admin=True)
    _wid, bid = workspace_with_board(yan)
    col = q("SELECT id FROM columns WHERE board_id = ? ORDER BY pos", bid)[0][0]
    post(yan, f"/b/{bid}/cards/new", title="Shots", column=col)
    cid = q("SELECT id FROM cards WHERE board_id = ? AND title = 'Shots'", bid)[0][0]
    page = (BytesIO(b"<script>alert(1)</script>"), "notes.html")
    post(yan, f"/card/{cid}/files", file=[(BytesIO(PNG), "shot.png"), page])
    pic, doc = [r[0] for r in q("SELECT id FROM attachments WHERE card_id = ? ORDER BY id", cid)]
    r = yan.get(f"/file/{pic}")
    assert r.mimetype == "image/png" and r.headers["Content-Disposition"].startswith("inline")
    r = yan.get(f"/file/{doc}")  # a web page someone attached downloads; it never runs here
    assert r.mimetype == "application/octet-stream"
    assert r.headers["Content-Disposition"] == "attachment; filename*=UTF-8''notes.html"
    post(yan, f"/card/{cid}/edit", covers="1", cover=str(doc))  # not a picture
    assert q("SELECT cover FROM cards WHERE id = ?", cid)[0][0] is None
    post(yan, f"/card/{cid}/edit", covers="1", cover=str(pic))
    board = yan.get(f"/b/{bid}").get_data(as_text=True)
    assert f'class="cover" src="/file/{pic}"' in board and "📎 2" in board
    zed, _ = user("zed@example.com", "Zed Outside")
    assert zed.get(f"/file/{pic}").status_code == 404
    post(yan, f"/file/{pic}/delete")
    assert q("SELECT cover FROM cards WHERE id = ?", cid)[0][0] is None
    post(yan, f"/card/{cid}/delete")
    assert q("SELECT COUNT(*) FROM attachments WHERE card_id = ?", cid)[0][0] == 0
