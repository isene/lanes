# lanes

A kanban board for any team: columns, swimlanes, and cards that show only
what matters. Public domain.

## Why another board

Trello is clean but has no swimlanes. Taiga has swimlanes but wastes the
screen. On one real Taiga board:

- Most cards spend three of their four lines on empty fields: a blank face,
  "N/E" for no estimate, and "Not assigned".
- Done sub-tasks stay on the card, crossed out. One card lists 15, and 13
  of them are done.
- Done has 71 cards and never empties.
- The page needs 70% zoom to fit seven columns.

## What lanes does differently

1. A card shows a field only when it has a value. A card with just a title
   is one line.
2. Sub-tasks show as a bar ("13 of 15 done") and list only the open ones.
3. Done moves old cards to an archive after 14 days. Each board sets its
   own number of days, and the archive can be searched.
4. The board fits the screen. Columns share the width, and any column or
   lane folds to a thin strip.
5. You set up the board on the board itself. Click a column name to rename
   it, drag a column to move it, and press + for a new column or lane.
   There is no settings page.
6. One click turns the lanes into one lane per person, or one per label.
7. A filter bar picks people and labels. The filter is part of the link, so
   you can send someone a filtered view.
8. A card opens in a side panel, so the board stays in view.

## First version

Enough to move one team off Taiga.

- Log in with a password and a code from a phone app; new users join by an
  invite link.
- Workspaces: one per team, each with its own members and boards. Nobody
  sees a workspace they are not a member of, admins included. An admin sets
  one up with a name and a chief; the admin page lists each workspace's
  name, chiefs and size, never its boards.
- A chief removes members and makes other members chiefs. Any member picks
  the workspace's colour, which tints the bar at the top.
- Boards with columns (name, colour, a limit on how many cards), lanes and
  cards.
- Cards: a number (#123), title, text, people, labels, sub-tasks, a due date,
  files and comments. One attached picture can show on the card. A card past its date turns red, and a red dot on each person's picture
  counts their late cards.
- `app.py overdue WORKSPACE-ID` prints the late cards by person, ready to post to a chat.
- With `LANES_WEBHOOK_URL` and `LANES_WEBHOOK_SECRET` set, lanes posts that list to a chat
  webhook at 09:00 on weekdays (local time; set `TZ`). `LANES_WEBHOOK_HOUR` moves the hour and
  `LANES_WEBHOOK_WORKSPACE` picks the workspace (default 1). A day with no late cards posts nothing.
- `app.py export WORKSPACE-ID FILE` writes one workspace, with its boards, cards, files and
  people, to a new database. A fresh lanes runs it as `data/lanes.db`. Logins stay behind:
  `app.py invite EMAIL` gives each person a new link, and the chiefs are the admins there.
- `app.py move WORKSPACE-ID URL` marks a workspace as moved to another lanes server. Its
  people get a page with the new link and can no longer log in here. An empty URL undoes it.
- A profile page for name, picture, password, and light or dark colours.
- A workspace logo at the top left that leads back to the last board you opened.
- New in the bar (or the n key) makes a card, and a new card opens in the card
  panel, ready for people, labels and a due date picked from a calendar.
- Drag cards between columns and lanes with the mouse. On a phone, the
  card panel moves them.
- Import a Taiga project export. Taiga's statuses become columns, its
  swimlanes become lanes, its user stories become cards and its tasks
  become sub-tasks. Card numbers stay the same.
- The board reloads its cards when you come back to its tab. Nothing runs
  while you are away.

## Later

- Files on cards, extra fields per board.
- Live changes while you look at the board.
- Import from Trello.
- Mail when someone names you in a comment.
- Company login, for a company that runs its own copy.

## How it is built

- Python, Flask and SQLite: one folder and one database file, easy to move
  to another server.
- Plain JavaScript with no build step.
- No timers in the page, and no work on the server while nobody uses it.
- Public domain (Unlicense). Team data never goes in this repo.

## Run it on your own machine

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
LANES_DEV=1 .venv/bin/python app.py adduser you@example.com "Your Name" --admin
LANES_DEV=1 .venv/bin/python app.py      # http://127.0.0.1:8092
```

`adduser` prints an invite link. Open it to set a password and a phone code.

## Settings

They go in `.env` next to `app.py`.

| Key | Meaning |
|-----|---------|
| `LANES_SECRET` | Long random string that signs the login cookie |
| `LANES_URL` | The public address, used in invite links |
| `LANES_PORT` | Port, 8092 by default |
| `LANES_HOST` | Address to listen on, 127.0.0.1 by default |
| `LANES_DATA` | Folder for the database, `data/` by default |

## Inside another program

A Python program that already has its own login can show lanes under an address of its own,
such as `/board`. It loads `app.py`, and for each request of a person who is logged in it sets
`SCRIPT_NAME`, and puts the person in the WSGI environ before it calls `app.wsgi_app`:

```python
environ["lanes.user"] = {
    "mail": "ada@example.com", "name": "Ada Lovelace",
    "chief": True,            # may tidy the workspace
    "workspace": "Team",      # the name of the one workspace, made at the first visit
    "home": "/", "home_name": "Intranet",  # the link back, where lanes has "Log out"
}
```

lanes then asks for no password and makes no invite links. Everyone who comes this way is a
member of the one workspace. A browser cannot set that key, since what it sends arrives as
`HTTP_...`. Give lanes a session cookie of its own (`SESSION_COOKIE_NAME`, `SESSION_COOKIE_PATH`),
or it overwrites the cookie of the program around it.

## A town

A workspace can send cards to another program, here called the town, such as a
company's help desk. The admin page links a workspace to a town: its name, its
address and the key it gave. "Save and make a key for the town" shows, once, the
address and key the town calls back with.

- Up: "Send to <town>" in a card posts `{ref, title, body}` to `<town>/api/tickets`
  with `Authorization: Bearer <the town's key>`. The ref is `card-<id>`.
- Writing to the town about a sent card posts `{comment}` to `<town>/api/tickets/<ref>`.
- Back: the town posts `{event, ticket, changes}` to `/api/town/<workspace id>` with
  the key lanes gave. The card shows the ticket's state and the town's comments.
- Down: the town posts `{ref, title, body, url}` to the same address. lanes makes a
  card in the first column of the workspace's first board, once per ref.

Comments on the card itself stay in the team. A failed call is not tried again.

## Container

The `Dockerfile` runs lanes as user 10000 on port 8080, with the database in `/data`:

```sh
docker build -t lanes .
docker run -p 8080:8080 -v lanes-data:/data -e LANES_SECRET=... -e LANES_URL=https://lanes.example.com lanes
```

In Kubernetes, a Service named `lanes` sets `LANES_PORT` to `tcp://...` in the pod and lanes
fails to start. Set `enableServiceLinks: false` in the pod spec.

## Server

First setup, as the app's own user, on a server with Apache and certbot:

```sh
git clone https://github.com/isene/lanes ~/lanes && cd ~/lanes
# write .env, then once as root:
sudo sh deploy/root-setup.sh lanes.example.com you@example.com
.venv/bin/python app.py adduser you@example.com "Your Name" --admin
```

The root script installs the Python packages, the system service, Apache and
the certificate. It also lets the app's user restart the service.

Deploy a change:

```sh
cd ~/lanes && git pull && .venv/bin/pip install -q -r requirements.txt && sudo systemctl restart lanes
```

All data is in one file, `data/lanes.db`. Back it up.
