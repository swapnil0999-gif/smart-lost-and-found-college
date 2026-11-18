document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("image-input");
  const fileName = document.getElementById("file-name");
  const preview = document.getElementById("image-preview");
  if (input) {
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (!file) { fileName.textContent = "No image selected"; preview.style.display = "none"; return; }
      fileName.textContent = file.name;
      if (file.size > 8 * 1024 * 1024) {
        fileName.textContent = "File is larger than 8 MB";
        input.value = "";
        return;
      }
      if (file.type.startsWith("image/")) {
        preview.src = URL.createObjectURL(file);
        preview.style.display = "block";
      }
    });
  }
  if (window.CAMPUSFIND_LOGGED_IN) {
    const badge = document.getElementById("notification-count");
    if (badge) {
      fetch("/api/notifications/unread").then(r => r.ok ? r.json() : null).then(data => {
        if (data) badge.textContent = data.count;
      }).catch(() => {});
    }
  }
});