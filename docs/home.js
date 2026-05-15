const heroVersion = document.getElementById("heroVersion");
const heroRowCount = document.getElementById("heroRowCount");
const heroPresentCount = document.getElementById("heroPresentCount");
const heroAbsentCount = document.getElementById("heroAbsentCount");

function writeSignal(el, value) {
  if (!el) return;
  el.textContent = value;
}

function bootHome() {
  const data = window.TRAITS_DATA;
  if (!data) return;
  writeSignal(heroVersion, data.version_info || "Unknown");
  writeSignal(heroRowCount, String(data.row_count || 0));
  writeSignal(heroPresentCount, String(data.present_count || 0));
  writeSignal(heroAbsentCount, String(data.absent_count || 0));
}

bootHome();
