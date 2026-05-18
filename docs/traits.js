let traitData = null;
let activeFilter = "all";
let activeId = null;

const searchInput = document.getElementById("searchInput");
const traitList = document.getElementById("traitList");
const matchCount = document.getElementById("matchCount");
const filterButtons = Array.from(document.querySelectorAll(".filter-chip"));
const versionValue = document.getElementById("versionValue");
const rowCountValue = document.getElementById("rowCountValue");
const presentCountValue = document.getElementById("presentCountValue");
const absentCountValue = document.getElementById("absentCountValue");
const detailEmpty = document.getElementById("detailEmpty");
const detailCard = document.getElementById("detailCard");
const detailType = document.getElementById("detailType");
const detailId = document.getElementById("detailId");
const detailLabel = document.getElementById("detailLabel");
const detailParents = document.getElementById("detailParents");
const detailChildren = document.getElementById("detailChildren");
const detailMapped = document.getElementById("detailMapped");
const detailIri = document.getElementById("detailIri");

function nodeMatches(node, query) {
  if (!query) return true;
  const haystack = [
    node.id,
    node.label,
    ...node.direct_super_labels,
    ...node.direct_child_labels,
    ...node.mapped_labels,
  ].join(" ").toLowerCase();
  return haystack.includes(query);
}

function getVisibleNodes() {
  const query = String(searchInput.value || "").trim().toLowerCase();
  return traitData.nodes.filter((node) => {
    if (activeFilter !== "all" && node.type !== activeFilter) return false;
    return nodeMatches(node, query);
  });
}

function renderTag(label, className) {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = label;
  return span;
}

function selectTrait(traitId) {
  activeId = traitId;
  renderTraitList();
  renderDetail();
}

function renderTraitList() {
  const visibleNodes = getVisibleNodes();
  matchCount.textContent = String(visibleNodes.length);
  traitList.innerHTML = "";

  if (!visibleNodes.length) {
    const empty = document.createElement("p");
    empty.className = "hero-copy";
    empty.textContent = "No traits match the current search and filter.";
    traitList.appendChild(empty);
    return;
  }

  if (!activeId || !visibleNodes.some((node) => node.id === activeId)) {
    activeId = visibleNodes[0].id;
  }

  for (const node of visibleNodes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `trait-item${node.id === activeId ? " active" : ""}`;
    button.addEventListener("click", () => selectTrait(node.id));

    const title = document.createElement("span");
    title.className = "trait-item-title";
    title.textContent = node.label;

    const meta = document.createElement("div");
    meta.className = "trait-item-meta";
    meta.appendChild(renderTag(node.type, `tag ${node.type === "absent" ? "absent" : ""}`));

    const idSpan = document.createElement("span");
    idSpan.textContent = node.id;
    meta.appendChild(idSpan);

    const countSpan = document.createElement("span");
    countSpan.textContent = `${node.mapped_ids.length} mapped`;
    meta.appendChild(countSpan);

    button.appendChild(title);
    button.appendChild(meta);
    traitList.appendChild(button);
  }
}

function renderPillList(container, labels, ids) {
  container.innerHTML = "";
  if (!labels.length) {
    const empty = document.createElement("span");
    empty.className = "pill";
    empty.textContent = "None";
    container.appendChild(empty);
    return;
  }

  labels.forEach((label, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pill-button";
    button.textContent = ids[index] ? `${label} (${ids[index]})` : label;
    if (ids[index]) {
      button.addEventListener("click", () => selectTrait(ids[index]));
    }
    container.appendChild(button);
  });
}

function renderDetail() {
  const node = traitData.nodes.find((entry) => entry.id === activeId);
  if (!node) {
    detailCard.classList.add("hidden");
    detailEmpty.classList.remove("hidden");
    return;
  }

  detailEmpty.classList.add("hidden");
  detailCard.classList.remove("hidden");
  detailType.textContent = node.type === "present" ? "Present Trait" : "Absent Trait";
  detailId.textContent = node.id;
  detailLabel.textContent = node.label;
  detailIri.href = node.iri;

  renderPillList(detailParents, node.direct_super_labels, node.direct_supers);
  renderPillList(detailChildren, node.direct_child_labels, node.direct_children);

  detailMapped.innerHTML = "";
  node.mapped_labels.forEach((label, index) => {
    const item = document.createElement("li");
    const mappedId = node.mapped_ids[index];
    item.textContent = mappedId ? `${label} (${mappedId})` : label;
    detailMapped.appendChild(item);
  });
}

function updateStats() {
  versionValue.textContent = traitData.version_info || "Unknown";
  rowCountValue.textContent = String(traitData.row_count || 0);
  presentCountValue.textContent = String(traitData.present_count || 0);
  absentCountValue.textContent = String(traitData.absent_count || 0);
}

function attachEvents() {
  searchInput.addEventListener("input", () => {
    renderTraitList();
    renderDetail();
  });

  filterButtons.forEach((button) => {
    button.addEventListener("click", () => {
      activeFilter = button.dataset.filter || "all";
      filterButtons.forEach((entry) => entry.classList.toggle("active", entry === button));
      renderTraitList();
      renderDetail();
    });
  });
}

async function boot() {
  if (window.TRAITS_DATA) {
    traitData = window.TRAITS_DATA;
  } else {
    const response = await fetch("traits-data.json", { cache: "no-store" });
    traitData = await response.json();
  }
  updateStats();
  attachEvents();
  renderTraitList();
  renderDetail();
}

boot().catch((error) => {
  traitList.innerHTML = "";
  const message = document.createElement("p");
  message.className = "hero-copy";
  message.textContent = `Could not load traits-data.json: ${error}`;
  traitList.appendChild(message);
});
