// The board's small helpers: drag cards and columns, fold columns and lanes, keys, and a
// reload when the tab comes back after someone changed the board. Every helper waits for
// an event; nothing runs on a timer. Without this script, forms and links still do it all.
document.documentElement.classList.add("js");
const board = document.getElementById("board");
const bid = board.dataset.board;
const csrf = document.querySelector("input[name=csrf]").value;
const here = location.pathname + location.search;

function post(action, fields) {
  const form = document.createElement("form");
  form.method = "post";
  form.action = action;
  for (const [name, value] of Object.entries({ csrf, next: here, ...fields })) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.append(input);
  }
  document.body.append(form);
  saveScroll();
  form.submit();
}

// Keep the scroll place across the page loads that every change makes.
const scrollKey = "lanes:scroll:" + location.pathname;
function saveScroll() {
  try {
    sessionStorage.setItem(scrollKey, JSON.stringify([board.scrollLeft, board.scrollTop]));
  } catch (e) {}
}
let [scrollX, scrollY] = [0, 0];
try {
  [scrollX, scrollY] = JSON.parse(sessionStorage.getItem(scrollKey) || "[0,0]");
} catch (e) {}
function restoreScroll() {
  board.scrollLeft = scrollX;
  board.scrollTop = scrollY;
}
restoreScroll();
// WebKit (Safari, gaze) can run this before the style sheet is in, when the board cannot
// scroll yet. Then try once more when the page has loaded.
if (board.scrollTop !== scrollY) addEventListener("load", restoreScroll, { once: true });
document.addEventListener("submit", saveScroll);
document.addEventListener("click", (e) => e.target.closest("a") && saveScroll());

// Forms that save as soon as a box is ticked or a choice is made.
document.addEventListener("change", (e) => {
  const form = e.target.closest("form[data-autosubmit], form.rename");
  if (form) form.requestSubmit();
});

// Column and lane names show as plain text, so a slip of the finger or a password manager
// cannot change them. A click turns a name into a box; Enter or a click elsewhere saves it,
// and Esc puts the old name back.
for (const input of board.querySelectorAll("form.rename input[name=name]")) {
  const name = document.createElement("button");
  name.type = "button";
  name.className = "name";
  name.title = "Click to rename";
  name.textContent = input.value;
  input.replaceWith(name);
  name.addEventListener("click", () => {
    name.replaceWith(input);
    input.focus();
    input.select();
  });
  // A changed name is being saved, and the page reloads with it.
  input.addEventListener("blur", () => input.value === input.defaultValue && input.replaceWith(name));
}

// Folding: each person's own choice, kept in this browser.
const foldKey = "lanes:fold:" + bid;
let folded = new Set();
try {
  folded = new Set(JSON.parse(localStorage.getItem(foldKey) || "[]"));
} catch (e) {}
function showFolds() {
  for (const el of board.querySelectorAll("[data-col]")) {
    el.classList.toggle("folded", folded.has("c" + el.dataset.col));
  }
  for (const el of board.querySelectorAll("[data-fold-row]")) {
    const key = el.dataset.foldRow;
    el.classList.toggle("folded", folded.has(key));
    const head = el.previousElementSibling;
    if (head && head.classList.contains("lanehead")) head.classList.toggle("folded", folded.has(key));
  }
}
board.addEventListener("click", (e) => {
  const button = e.target.closest("button.fold");
  if (!button) return;
  const key = button.dataset.fold;
  folded.has(key) ? folded.delete(key) : folded.add(key);
  try {
    localStorage.setItem(foldKey, JSON.stringify([...folded]));
  } catch (e) {}
  showFolds();
});
showFolds();

// Dragging with pointer events, since some browsers have no desktop drag support.
// Touch is left alone, so a finger still scrolls; the card panel moves cards there.
let drag = null;
for (const el of board.querySelectorAll(".tile, .tile *")) el.draggable = false;

function under(e, selector) {
  const el = document.elementFromPoint(e.clientX, e.clientY);
  return el && el.closest(selector);
}

function mark(el) {
  if (drag.over) drag.over.classList.remove("over");
  if (el) el.classList.add("over");
  drag.over = el;
}

board.addEventListener("pointerdown", (e) => {
  if (e.button !== 0 || e.pointerType === "touch") return;
  if (e.target.closest("input, button, select, textarea, summary, details, form")) return;
  const tile = e.target.closest(".tile");
  const head = !tile && e.target.closest(".colhead");
  if (!tile && !head) return;
  drag = { el: tile || head, kind: tile ? "card" : "column", x: e.clientX, y: e.clientY };
});

document.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.x;
  const dy = e.clientY - drag.y;
  if (!drag.moved && Math.hypot(dx, dy) < 6) return; // a small wobble is still a click
  drag.moved = true;
  drag.el.classList.add("dragging");
  drag.el.style.transform = drag.kind === "card" ? `translate(${dx}px, ${dy}px)` : `translateX(${dx}px)`;
  mark(under(e, drag.kind === "card" ? ".cell" : ".colhead"));
});

document.addEventListener("pointerup", (e) => {
  if (!drag) return;
  const { el, kind, moved, over } = drag;
  if (over) over.classList.remove("over");
  el.classList.remove("dragging");
  el.style.transform = "";
  drag = null;
  if (!moved) return;
  // The drag ends in a click on the card link; that click must not open the card.
  const block = (c) => c.preventDefault();
  document.addEventListener("click", block, true);
  setTimeout(() => document.removeEventListener("click", block, true));
  if (!over) return;
  if (kind === "card") dropCard(el, over, e);
  else if (over !== el) dropColumn(el, over, e);
});

document.addEventListener("pointercancel", () => {
  if (!drag) return;
  if (drag.over) drag.over.classList.remove("over");
  drag.el.classList.remove("dragging");
  drag.el.style.transform = "";
  drag = null;
});

function dropCard(tile, cell, e) {
  const before = [...cell.querySelectorAll(".tile")].find((t) => {
    if (t === tile) return false;
    const r = t.getBoundingClientRect();
    return e.clientY < r.top + r.height / 2;
  });
  const from = tile.closest(".cells").dataset.row;
  const to = cell.closest(".cells").dataset.row;
  post(`/card/${tile.dataset.card}/move`, {
    column: cell.dataset.col,
    before: before ? before.dataset.card : "",
    by: board.dataset.by,
    from,
    to,
  });
}

function dropColumn(head, target, e) {
  const r = target.getBoundingClientRect();
  let before = target;
  if (e.clientX > r.left + r.width / 2) {
    before = target.nextElementSibling;
    if (before && !before.classList.contains("colhead")) before = null;
  }
  if (before === head) return;
  post(`/column/${head.dataset.col}/move`, { before: before ? before.dataset.col : "" });
}

// The New menu in the bar: its box is ready to type in when it opens.
const newMenu = document.querySelector("details.new");
const newTitle = newMenu?.querySelector("input[name=title]");
newMenu?.addEventListener("toggle", () => newMenu.open && newTitle.focus());

// The due date: type it, or pick a day in the calendar that opens under the box.
// It is drawn here, since a browser's own date box takes no typed ISO date.
const due = document.querySelector("#cardpanel input[name=due]");
if (due) {
  const cal = document.createElement("div");
  cal.className = "cal";
  cal.hidden = true;
  due.after(cal);
  const iso = (d) =>
    [d.getFullYear(), d.getMonth() + 1, d.getDate()].map((n) => String(n).padStart(2, "0")).join("-");
  let shown; // the first day of the month on show
  const draw = () => {
    const y = shown.getFullYear();
    const m = shown.getMonth();
    const name = shown.toLocaleDateString("en-GB", { month: "long", year: "numeric" });
    let html =
      `<div class="calhead"><button type="button" data-step="-1" aria-label="Month before">‹</button>` +
      `<span>${name}</span><button type="button" data-step="1" aria-label="Month after">›</button></div>` +
      `<div class="calgrid">` +
      ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((d) => `<b>${d}</b>`).join("") +
      "<i></i>".repeat((new Date(y, m, 1).getDay() + 6) % 7); // weeks start on Monday
    for (let d = 1; d <= new Date(y, m + 1, 0).getDate(); d++) {
      const day = iso(new Date(y, m, d));
      const cls = [day === iso(new Date()) && "today", day === due.value && "on"].filter(Boolean);
      html += `<button type="button" data-day="${day}" class="${cls.join(" ")}">${d}</button>`;
    }
    cal.innerHTML = html + "</div>";
  };
  due.addEventListener("focus", () => {
    const d = /^\d{4}-\d{2}-\d{2}$/.test(due.value) ? new Date(due.value + "T00:00") : new Date();
    shown = new Date(d.getFullYear(), d.getMonth(), 1);
    draw();
    cal.hidden = false;
  });
  due.addEventListener("blur", () => (cal.hidden = true));
  cal.addEventListener("mousedown", (e) => e.preventDefault()); // the box keeps the focus
  cal.addEventListener("click", (e) => {
    const button = e.target.closest("button");
    if (!button) return;
    if (button.dataset.step) {
      shown.setMonth(shown.getMonth() + Number(button.dataset.step));
      draw();
    } else {
      due.value = button.dataset.day;
      cal.hidden = true;
      due.form.requestSubmit();
    }
  });
}

// Keys: n for a new card, / to search, Esc to close the card or leave a field.
document.addEventListener("keydown", (e) => {
  const typing = e.target.closest("input, textarea, select");
  if (e.key === "Escape") {
    if (typing) {
      if (e.target.form?.matches(".rename")) e.target.value = e.target.defaultValue;
      e.target.blur();
      e.target.closest("details.menu")?.removeAttribute("open");
    }
    else document.querySelector("#cardpanel .close")?.click();
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "n" && newMenu) {
    newMenu.open = true;
    newTitle.focus(); // now, or the first letter typed after n is lost
    e.preventDefault();
  } else if (e.key === "/") {
    document.querySelector(".search input[name=q]")?.focus();
    e.preventDefault();
  }
});

// When the tab comes back, ask once whether the board changed while it was away.
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState !== "visible") return;
  try {
    const res = await fetch(`/b/${bid}/version`, { credentials: "same-origin" });
    if (res.ok && (await res.text()).trim() !== board.dataset.version) {
      saveScroll();
      location.reload();
    }
  } catch (e) {}
});
