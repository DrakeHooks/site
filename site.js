(() => {
  const elThemeBtn = document.getElementById("themeToggle");
  const elThemeLabel = elThemeBtn?.querySelector(".toggle__label");
  const elNoDate = document.getElementById("no_date");
  const elNoDateHidden = document.getElementById("no_date_hidden");
  const elDate = document.getElementById("post_date");

  const elFiles = document.getElementById("files");
  const elFileCount = document.getElementById("fileCount");

  function applyThemeLabel() {
    const theme = document.documentElement.dataset.theme || "light";
    if (elThemeLabel) elThemeLabel.textContent = (theme === "dark") ? "Light" : "Dark";
  }

  elThemeBtn?.addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || "light";
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("uploader_theme", next);
    applyThemeLabel();
  });

  applyThemeLabel();

  function syncNoDate() {
    const checked = !!elNoDate?.checked;
    if (elNoDateHidden) elNoDateHidden.value = checked ? "1" : "0";
    if (elDate) {
      elDate.disabled = checked;
      elDate.style.opacity = checked ? "0.5" : "1";
    }
  }

  elNoDate?.addEventListener("change", syncNoDate);
  syncNoDate();

  function updateFileCount() {
    const n = elFiles?.files?.length || 0;
    if (!elFileCount) return;
    elFileCount.textContent = n ? `${n} file${n === 1 ? "" : "s"} selected` : "No files selected";
  }

  elFiles?.addEventListener("change", updateFileCount);
  updateFileCount();
})();