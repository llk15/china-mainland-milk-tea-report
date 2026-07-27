const DATA_URL = "data/chagee/chagee_drinks.json";
const PAGE_SIZE = 50;

const state = {
  records: [],
  filtered: [],
  visible: PAGE_SIZE,
  sortKey: "energy",
  sortDirection: "asc",
};

const elements = {
  form: document.querySelector("#filter-form"),
  search: document.querySelector("#search"),
  category: document.querySelector("#category"),
  grade: document.querySelector("#grade"),
  cup: document.querySelector("#cup"),
  temperature: document.querySelector("#temperature"),
  sweetness: document.querySelector("#sweetness"),
  reportOnly: document.querySelector("#report-only"),
  sortButtons: document.querySelectorAll(".sort-button"),
  results: document.querySelector("#results"),
  resultCopy: document.querySelector("#result-copy"),
  loadMore: document.querySelector("#load-more"),
  updated: document.querySelector("#last-updated"),
  statRecords: document.querySelector("#stat-records"),
  statProducts: document.querySelector("#stat-products"),
  statEnergy: document.querySelector("#stat-energy"),
  statCoverage: document.querySelector("#stat-coverage"),
  heroProducts: document.querySelector("#hero-products"),
  heroRecords: document.querySelector("#hero-records"),
  heroReports: document.querySelector("#hero-reports"),
  dialog: document.querySelector("#detail-dialog"),
  dialogCategory: document.querySelector("#dialog-category"),
  dialogTitle: document.querySelector("#dialog-title"),
  dialogSpecs: document.querySelector("#dialog-specs"),
  dialogNutrition: document.querySelector("#dialog-nutrition"),
  dialogReport: document.querySelector("#dialog-report"),
};

const numericFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
const countFormat = new Intl.NumberFormat("zh-CN");
const gradeOrder = { A: 1, B: 2, C: 3, D: 4, "": 9 };

function escapeHTML(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function valueOrDash(value) {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "number" ? numericFormat.format(value) : escapeHTML(value);
}

function uniqueValues(key) {
  return [...new Set(state.records.map((record) => record[key]).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, "zh-CN"));
}

function fillSelect(select, values) {
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  });
}

function selectedFilters() {
  return {
    search: elements.search.value.trim().toLocaleLowerCase("zh-CN"),
    category: elements.category.value,
    grade: elements.grade.value,
    cup: elements.cup.value,
    temperature: elements.temperature.value,
    sweetness: elements.sweetness.value,
    reportOnly: elements.reportOnly.checked,
  };
}

function compareNullable(left, right, direction = 1) {
  const leftMissing = left === null || left === undefined || left === "";
  const rightMissing = right === null || right === undefined || right === "";
  if (leftMissing && rightMissing) return 0;
  if (leftMissing) return 1;
  if (rightMissing) return -1;
  return (left - right) * direction;
}

function sortRecords(records) {
  const key = state.sortKey;
  const factor = state.sortDirection === "desc" ? -1 : 1;
  return records.sort((a, b) => {
    let result;
    if (key === "name") {
      result = a.name.localeCompare(b.name, "zh-CN") * factor;
    } else if (key === "grade") {
      result = ((gradeOrder[a.grade] ?? 9) - (gradeOrder[b.grade] ?? 9)) * factor;
    } else {
      result = compareNullable(a[key], b[key], factor);
    }
    return result || a.name.localeCompare(b.name, "zh-CN");
  });
}

function updateSortIndicators() {
  elements.sortButtons.forEach((button) => {
    const active = button.dataset.sort === state.sortKey;
    const header = button.closest("th");
    const indicator = button.querySelector(".sort-indicator");
    button.classList.toggle("is-active", active);
    header.setAttribute(
      "aria-sort",
      active ? (state.sortDirection === "asc" ? "ascending" : "descending") : "none",
    );
    indicator.textContent = active ? (state.sortDirection === "asc" ? "↑" : "↓") : "↕";
  });
}

function applyFilters() {
  const filters = selectedFilters();
  const filtered = state.records.filter((record) => {
    const haystack = [
      record.name,
      record.category,
      record.cup,
      record.temperature,
      record.sweetness,
      record.tea,
      record.milk,
    ].join(" ").toLocaleLowerCase("zh-CN");

    return (!filters.search || haystack.includes(filters.search))
      && (!filters.category || record.category === filters.category)
      && (!filters.grade || record.grade === filters.grade)
      && (!filters.cup || record.cup === filters.cup)
      && (!filters.temperature || record.temperature === filters.temperature)
      && (!filters.sweetness || record.sweetness === filters.sweetness)
      && (!filters.reportOnly || Boolean(record.report));
  });

  state.filtered = sortRecords(filtered);
  state.visible = PAGE_SIZE;
  render();
}

function renderSummary() {
  const records = state.filtered;
  const products = new Set(records.map((record) => record.name)).size;
  const energies = records.map((record) => record.energy).filter((value) => value !== null);
  const reports = records.filter((record) => record.report).length;
  const average = energies.length
    ? energies.reduce((sum, value) => sum + value, 0) / energies.length
    : null;

  elements.statRecords.textContent = countFormat.format(records.length);
  elements.statProducts.textContent = countFormat.format(products);
  elements.statEnergy.textContent = average === null ? "—" : numericFormat.format(average);
  elements.statCoverage.textContent = records.length ? `${Math.round((reports / records.length) * 100)}%` : "—";
  elements.resultCopy.innerHTML = `找到 <strong>${countFormat.format(records.length)}</strong> 条规格记录`;
}

function specTags(record) {
  return [record.category, record.cup, record.temperature, record.sweetness, record.tea, record.milk]
    .filter(Boolean)
    .map((value) => `<span>${escapeHTML(value)}</span>`)
    .join("");
}

function reportViewerURL(record) {
  const specs = [record.cup, record.temperature, record.sweetness].filter(Boolean).join(" · ");
  const params = new URLSearchParams({
    url: record.report,
    name: record.name,
    specs,
  });
  return `report.html?${params.toString()}`;
}

function recordRow(record, index) {
  const grade = record.grade || "—";
  const gradeClass = record.grade ? `grade-${record.grade.toLowerCase()}` : "grade-none";
  const report = record.report
    ? `<a class="report-link" href="${escapeHTML(reportViewerURL(record))}" target="_blank" rel="noopener noreferrer">报告</a>`
    : `<span aria-label="暂无报告">—</span>`;

  return `
    <tr>
      <td>
        <span class="drink-name">${escapeHTML(record.name)}</span>
        <span class="spec-line">${specTags(record)}</span>
      </td>
      <td>${valueOrDash(record.energy)}</td>
      <td>${valueOrDash(record.protein)}</td>
      <td>${valueOrDash(record.carbs)}</td>
      <td>${valueOrDash(record.fat)}</td>
      <td>${valueOrDash(record.caffeine)}</td>
      <td>${valueOrDash(record.polyphenols)}</td>
      <td><span class="grade ${gradeClass}">${grade}</span></td>
      <td>${report}<button class="detail-button" type="button" data-index="${index}">详情</button></td>
    </tr>
  `;
}

function renderRows() {
  const visibleRecords = state.filtered.slice(0, state.visible);
  if (!visibleRecords.length) {
    elements.results.innerHTML = `<tr><td class="empty-cell" colspan="9">没有符合条件的记录，试试清除部分筛选。</td></tr>`;
  } else {
    elements.results.innerHTML = visibleRecords.map(recordRow).join("");
  }
  elements.loadMore.hidden = state.visible >= state.filtered.length;
  if (!elements.loadMore.hidden) {
    elements.loadMore.textContent = `显示更多（还有 ${countFormat.format(state.filtered.length - state.visible)} 条）`;
  }
}

function render() {
  renderSummary();
  renderRows();
}

function nutritionItem(label, value, unit) {
  const hasValue = value !== null && value !== undefined && value !== "";
  return `<div><span>${label}</span><strong>${valueOrDash(value)}${hasValue && unit ? ` ${unit}` : ""}</strong></div>`;
}

function openDetail(record) {
  elements.dialogCategory.textContent = record.category;
  elements.dialogTitle.textContent = record.name;
  elements.dialogSpecs.innerHTML = specTags(record);
  elements.dialogNutrition.innerHTML = [
    nutritionItem("热量", record.energy, "kcal"),
    nutritionItem("蛋白质", record.protein, "g"),
    nutritionItem("碳水化合物", record.carbs, "g"),
    nutritionItem("脂肪", record.fat, "g"),
    nutritionItem("咖啡因", record.caffeine, "mg"),
    nutritionItem("茶多酚", record.polyphenols, "mg"),
    nutritionItem("反式脂肪", record.transFat, "g"),
    nutritionItem("GI", record.gi, ""),
    nutritionItem("营养等级", record.grade || null, ""),
  ].join("");

  elements.dialogReport.hidden = !record.report;
  if (record.report) elements.dialogReport.href = reportViewerURL(record);
  elements.dialog.showModal();
}

function bindEvents() {
  elements.form.addEventListener("input", applyFilters);
  elements.form.addEventListener("reset", () => window.setTimeout(applyFilters));
  elements.sortButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      if (state.sortKey === key) {
        state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
      } else {
        state.sortKey = key;
        state.sortDirection = "asc";
      }
      updateSortIndicators();
      applyFilters();
    });
  });
  elements.loadMore.addEventListener("click", () => {
    state.visible += PAGE_SIZE;
    renderRows();
  });
  elements.results.addEventListener("click", (event) => {
    const button = event.target.closest("[data-index]");
    if (button) openDetail(state.filtered[Number(button.dataset.index)]);
  });
  document.querySelector("#dialog-close").addEventListener("click", () => elements.dialog.close());
  elements.dialog.addEventListener("click", (event) => {
    if (event.target === elements.dialog) elements.dialog.close();
  });
}

async function init() {
  try {
    let payload = window.CHAGEE_DATA;
    if (!payload) {
      const response = await fetch(DATA_URL);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      payload = await response.json();
    }
    state.records = payload.records;

    fillSelect(elements.category, uniqueValues("category"));
    fillSelect(elements.cup, uniqueValues("cup"));
    fillSelect(elements.temperature, uniqueValues("temperature"));
    fillSelect(elements.sweetness, uniqueValues("sweetness"));

    const reportCount = state.records.filter((record) => record.report).length;
    elements.heroProducts.textContent = countFormat.format(payload.meta.products);
    elements.heroRecords.textContent = countFormat.format(payload.meta.records);
    elements.heroReports.textContent = countFormat.format(reportCount);
    elements.updated.textContent = `霸王茶姬数据更新于 ${payload.meta.updatedAt.slice(0, 10)}`;

    bindEvents();
    applyFilters();
  } catch (error) {
    console.error(error);
    elements.updated.textContent = "数据加载失败";
    elements.results.innerHTML = `
      <tr><td class="empty-cell" colspan="9">
        暂时无法载入数据。请刷新页面；若问题持续，请在 GitHub 提交 Issue。
      </td></tr>
    `;
  }
}

init();
