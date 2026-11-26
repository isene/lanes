// Forms outside the board: save a choice in the background as soon as it is made, and shrink a picture before it
// goes to the server. A phone photo of several MB becomes 256 pixels: a square for a person,
// the whole picture for a workspace logo.
document.documentElement.classList.add("js");

async function shrink(file, how) {
  const image = await createImageBitmap(file);
  const canvas = document.createElement("canvas");
  if (how === "cover") {
    const side = Math.min(image.width, image.height);
    canvas.width = canvas.height = 256;
    canvas.getContext("2d").drawImage(image, (image.width - side) / 2, (image.height - side) / 2,
      side, side, 0, 0, 256, 256);
  } else {
    const scale = Math.min(1, 256 / Math.max(image.width, image.height));
    canvas.width = Math.round(image.width * scale);
    canvas.height = Math.round(image.height * scale);
    canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
  }
  const type = how === "cover" ? "image/jpeg" : "image/png"; // PNG keeps a logo's clear parts
  const blob = await new Promise((done) => canvas.toBlob(done, type, 0.88));
  return new File([blob], how === "cover" ? "picture.jpg" : "logo.png", { type });
}

// A choice saves in the background; the page takes the new colours from the server's answer.
async function quietSave(form) {
  try {
    const answer = await fetch(form.action, { method: "POST", body: new FormData(form) });
    if (!answer.ok || new URL(answer.url).pathname === "/login") throw new Error(answer.status);
    const page = new DOMParser().parseFromString(await answer.text(), "text/html");
    const theme = page.documentElement.dataset.theme;
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    const head = page.querySelector("header"); // a team's colour shows at once
    if (head) document.querySelector("header").setAttribute("style", head.getAttribute("style") || "");
  } catch (e) {
    form.submit();
  }
}

document.addEventListener("change", async (e) => {
  const form = e.target.closest("form");
  if (form && form.matches("[data-autosubmit]")) return quietSave(form);
  const how = e.target.dataset.shrink;
  if (!how || !e.target.files.length) return;
  try {
    const files = new DataTransfer();
    files.items.add(await shrink(e.target.files[0], how));
    e.target.files = files.files;
  } catch (err) {} // send the picture as it is; the server says if it is too big
  form.requestSubmit();
});
