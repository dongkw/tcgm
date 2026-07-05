document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const button = form.querySelector("button[type='submit']");
  if (button) {
    button.disabled = true;
    button.dataset.originalText = button.textContent || "";
    button.textContent = "执行中";
  }
});
