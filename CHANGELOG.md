# Changelog

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
