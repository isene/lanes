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
  sees a workspace they are not a member of. An admin can remove a member.
- Boards with columns (name, colour, a limit on how many cards), lanes and
  cards.
- Cards: a number (#123), title, text, people, labels, sub-tasks and
  comments.
- A profile page for name, picture, password, and light or dark colours.
- A workspace logo at the top left that leads back to the last board you opened.
- Drag cards between columns and lanes with the mouse. On a phone, the
  card panel moves them.
- Import a Taiga project export. Taiga's statuses become columns, its
  swimlanes become lanes, its user stories become cards and its tasks
  become sub-tasks. Card numbers stay the same.
- The board reloads its cards when you come back to its tab. Nothing runs
  while you are away.

## Later

- Files on cards, extra fields per board, due dates.
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
| `LANES_PORT` | Port on 127.0.0.1, 8092 by default |
| `LANES_DATA` | Folder for the database, `data/` by default |

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
