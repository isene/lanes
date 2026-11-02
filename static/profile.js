// The profile page: save a choice as soon as it is made, and shrink a picture before it goes
// to the server, so a phone photo of several MB becomes a square of 256 pixels.
document.documentElement.classList.add("js");

document.addEventListener("change", async (e) => {
  const form = e.target.closest("form");
  if (form && form.matches("[data-autosubmit]")) return form.requestSubmit();
  if (e.target.name !== "picture" || !e.target.files.length) return;
  try {
    const image = await createImageBitmap(e.target.files[0]);
    const side = Math.min(image.width, image.height);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 256;
    canvas.getContext("2d").drawImage(image, (image.width - side) / 2, (image.height - side) / 2,
      side, side, 0, 0, 256, 256);
    const blob = await new Promise((done) => canvas.toBlob(done, "image/jpeg", 0.88));
    const files = new DataTransfer();
    files.items.add(new File([blob], "picture.jpg", { type: "image/jpeg" }));
    e.target.files = files.files;
  } catch (err) {} // send the picture as it is; the server says if it is too big
  form.requestSubmit();
});
