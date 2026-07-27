const params = new URLSearchParams(window.location.search);
const rawURL = params.get("url") || "";
const name = params.get("name") || "检验报告";
const specs = params.get("specs") || "";

const elements = {
  title: document.querySelector("#report-title"),
  specs: document.querySelector("#report-specs"),
  loading: document.querySelector("#report-loading"),
  image: document.querySelector("#report-image"),
  pdf: document.querySelector("#report-pdf"),
  error: document.querySelector("#report-error"),
  original: document.querySelector("#original-report"),
};

function validReportURL(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && url.hostname === "chagee-public.oss-cn-beijing.aliyuncs.com"
      ? url
      : null;
  } catch {
    return null;
  }
}

function showError() {
  elements.loading.hidden = true;
  elements.image.hidden = true;
  elements.pdf.hidden = true;
  elements.error.hidden = false;
}

function init() {
  const reportURL = validReportURL(rawURL);
  elements.title.textContent = name;
  elements.specs.textContent = specs || "品牌公开检验报告";
  document.title = `${name}检验报告 · 中国大陆奶茶报告`;

  if (!reportURL) {
    showError();
    return;
  }

  elements.original.href = reportURL.href;
  elements.original.hidden = false;

  if (reportURL.pathname.toLowerCase().endsWith(".pdf")) {
    elements.pdf.src = reportURL.href;
    elements.pdf.hidden = false;
    elements.loading.hidden = true;
    return;
  }

  elements.image.alt = `${name}${specs ? `（${specs}）` : ""}检验报告`;
  elements.image.addEventListener("load", () => {
    elements.loading.hidden = true;
    elements.image.hidden = false;
  });
  elements.image.addEventListener("error", showError);
  elements.image.src = reportURL.href;
}

init();
