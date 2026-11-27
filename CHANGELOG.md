# Changelog

## 0.15.0 (2026-11-27)

- A town may offer links on each card, such as a form for a risk, with the card's number and title
  filled in. They are set with the town's name and address.
- The town's notes show on a card before it is sent, so a risk added through such a link shows there.

## 0.14.1 (2026-11-26)

- Fixed: a workspace's colour never showed, and left the top bar with no background, so its white
  text could not be read. The page's security header stops a style attribute; the colour now comes
  from a small style sheet.

## 0.14.0 (2026-11-26)

- A program that has lanes mounted can hand over a colour and an emblem, so the board looks like
  the site around it. The colour tints the top bar; the emblem takes the place of the lanes logo.


## 0.13.0 (2026-11-26)

- `app.py export WORKSPACE-ID FILE` writes one workspace to a new database that a fresh lanes
  runs as it is. Passwords, phone codes and the link to a town stay behind, and the chiefs are
  the new copy's admins.

## 0.12.1 (2026-11-26)

- Inside another program lanes has no admin, so a chief links the workspace to a town, on the
  workspace's page.

## 0.12.0 (2026-11-26)

- A workspace can be linked to a town: another program, such as a company's help desk. A card
  then has "Send to" the town. The card shows the town's state and comments, and the team can
  write back. The town can also send tasks down as new cards. The admin page sets it up; the
  README has the calls.

## 0.11.0 (2026-11-26)

- Another program can have lanes inside it, under an address such as `/board`. That program
  owns the login: it hands lanes the person who is logged in, and lanes asks for no password.
  The person joins the one workspace such a lanes has. The README says how.
- Every link, form and background save works under such an address.

## 0.10.0 (2026-11-26)

- A workspace has chiefs. A chief removes members and makes other members chiefs; before,
  only an admin could remove anyone.
- The admin names a chief when setting up a workspace. The chief is then the first member,
  gets an invite link if new, and the admin stays outside the workspace.
- Each workspace picks its own colour, which tints the bar at the top of its pages.
- The admin page lists the workspaces with their chiefs and number of members, never their
  boards.

## 0.9.0 (2026-11-20)

- lanes posts the late cards of a workspace to a chat webhook at 09:00 on weekdays, from the
  server itself. It is off unless `LANES_WEBHOOK_URL` is set, and posts nothing on a day with no
  late cards.

## 0.8.1 (2026-11-14)

- Ticking a box, picking a column, lane or date, or typing in a field saves without reloading
  the page. The board behind the card panel updates in place, and the panel stays open where
  it was. So does the colour choice on the profile page. A file upload still reloads.

## 0.8.0 (2026-11-14)

- `app.py move WORKSPACE-ID URL` marks a workspace as moved to another lanes server. Its
  people get a page with the link to the new place, and their logins here stop. An admin
  can still log in to set up other teams.
- A `Dockerfile`, and `LANES_HOST` to listen on another address than 127.0.0.1.

## 0.7.0 (2026-11-04)

- Files on cards: add any number of files in the card panel, up to 20 MB each. A picture
  opens in the browser; any other file downloads.
- Tick "On the card" next to a picture to show it at the top of the card on the board. One
  picture at a time; ticking another moves it.
- A card with files shows 📎 and how many.

## 0.6.1 (2026-11-04)

- The due date box takes a typed date again (2026-11-14, 14.11.2026 or 14.11). A click on
  it opens a calendar drawn by the page, so it works the same in every browser. The
  browser's own date box took no typed ISO date, and its calendar ignored clicks in gaze.

## 0.6.0 (2026-11-04)

- A new card opens in the card panel at once, so its people, labels and date can be set
  without a click on it first.
- A New button in the bar makes a card in the first column and lane. The n key opens it.
- The due date is picked from a calendar. Typing a date still works.

## 0.5.4 (2026-11-03)

- Moving a card no longer throws the board back to the top in Safari and other WebKit
  browsers. They can run the page script before the style sheet is in, when the board
  cannot scroll yet, so the scroll place is set once more when the page has loaded.

## 0.5.3 (2026-11-03)

- The invite page shows the email next to the new password, and the password form on the
  profile page carries it too. A password manager needs it to save the password under the
  right account.

## 0.5.2 (2026-11-02)

- Column and lane names show as plain text, so a password manager no longer sees them as
  fields to fill, and a slip cannot rename them. A click turns a name into a box; Enter
  saves it and Esc puts the old name back.

## 0.5.1 (2026-11-02)

- Dates show as 2026-11-14 everywhere. The Due box takes 2026-11-14, 14.11.2026 or 14.11
  (this year), so the browser's language no longer decides the order.

## 0.5.0 (2026-11-02)

- A card can have a due date. It shows on the card, in red once the date has passed and the
  card is not done.
- A red dot on each person's picture in the filter bar counts their cards past their date.
- `app.py overdue WORKSPACE-ID` prints a message that lists the cards past their date, by
  person, with links. A script can post it to a team chat.

## 0.4.0 (2026-11-02)

- An admin can remove a member from the workspace page. Their cards and comments keep their
  name.

## 0.3.1 (2026-11-02)

- The Users page lists only people who are in a workspace (and admins). Someone who has
  left keeps their name on old comments but no longer shows in the list.

## 0.3.0 (2026-11-02)

- A sun or moon at the top switches between light and dark colours, and stays on the page.
- Each workspace can have a logo. It shows at the top left of every page, and a click on it
  goes back to the last board you opened.
- The board's menu links to the workspace page, for members, boards and import.

## 0.2.0 (2026-11-02)

- A profile page, reached from your name at the top: name, picture, password, and colours.
- Colours can be light, dark, or the same as the device. The choice follows you to every device.
- A picture is shrunk to 256 pixels in the browser before it is sent, and shows on cards.
- Fixed: the tick box in a column's menu took the whole width and squeezed its text.

## 0.1.0 (2026-11-02)

- Boards with columns, lanes and cards, inside workspaces that only their members see.
- Log in with a password and a phone code; new users join by an invite link.
- Cards show a field only when it has a value; sub-tasks show as a bar plus the open ones.
- Done cards go to an archive after a number of days that each board sets.
- One click shows one lane per person or per label; filters are part of the link.
- Drag cards and columns with the mouse; fold any column or lane to a strip.
- Import of a Taiga project export.
