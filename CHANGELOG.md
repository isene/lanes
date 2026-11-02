# Changelog

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
