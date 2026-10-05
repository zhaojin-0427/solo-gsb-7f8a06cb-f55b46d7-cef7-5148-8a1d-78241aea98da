"use strict";

// 跨时区交接记录核验 —— 前端逻辑（原生 JS，无依赖）。

const MIN_ROWS = 2;
const MAX_ROWS = 12;

const tbody = document.getElementById("rows");
const rowCountEl = document.getElementById("row-count");
const addBtn = document.getElementById("add-row");
const verifyBtn = document.getElementById("verify");
const alertBox = document.getElementById("alert");
const resultPanel = document.getElementById("result-panel");
const resultBody = document.getElementById("result-rows");
const tzDatalist = document.getElementById("tz-list");

// 默认示例（2024-11-03 美国东部秋拨，01:30 为歧义时刻）。
let seq = 0;
let rows = [];

function addRow(data) {
  if (rows.length >= MAX_ROWS) return;
  rows.push({
    id: ++seq,
    local: data?.local ?? "",
    timezone: data?.timezone ?? "",
    min_seconds: data?.min_seconds ?? "",
    max_seconds: data?.max_seconds ?? "",
  });
  render();
  clearConclusion();
}

function removeRow(id) {
  if (rows.length <= MIN_ROWS) return;
  rows = rows.filter((r) => r.id !== id);
  render();
  clearConclusion();
}

function render() {
  tbody.innerHTML = "";
  rows.forEach((r, idx) => {
    const tr = document.createElement("tr");
    tr.dataset.id = r.id;
    tr.dataset.row = String(idx + 1);

    const isFirst = idx === 0;
    tr.innerHTML = `
      <td class="col-row">${idx + 1}</td>
      <td>
        <input type="text" name="local" value="${escapeAttr(r.local)}"
               placeholder="YYYY-MM-DD HH:mm" autocomplete="off" />
      </td>
      <td>
        <input type="text" name="timezone" list="tz-list"
               value="${escapeAttr(r.timezone)}"
               placeholder="Asia/Shanghai" autocomplete="off" />
      </td>
      <td>
        <input type="number" name="min_seconds" value="${escapeAttr(r.min_seconds)}"
               min="0" max="86400" step="1" ${isFirst ? "disabled" : ""}
               placeholder="${isFirst ? "—" : "0"}" />
      </td>
      <td>
        <input type="number" name="max_seconds" value="${escapeAttr(r.max_seconds)}"
               min="0" max="86400" step="1" ${isFirst ? "disabled" : ""}
               placeholder="${isFirst ? "—" : "86400"}" />
      </td>
      <td class="col-op">
        <button type="button" class="btn-ghost-danger" data-action="remove"
                title="删除此行" ${isFirst || rows.length <= MIN_ROWS ? "disabled" : ""}>×</button>
      </td>
    `;

    tr.querySelectorAll("input").forEach((input) => {
      input.addEventListener("input", () => {
        r[input.name] = input.value;
        clearConclusion();
      });
    });
    tr.querySelector('[data-action="remove"]').addEventListener("click", () => removeRow(r.id));

    tbody.appendChild(tr);
  });

  rowCountEl.textContent = String(rows.length);
  addBtn.disabled = rows.length >= MAX_ROWS;
}

function escapeAttr(v) {
  return String(v).replace(/[&"<>]/g, (c) =>
    ({ "&": "&amp;", '"': "&quot;", "<": "&lt;", ">": "&gt;" })[c]);
}

// 任何编辑都清除旧结论。
function clearConclusion() {
  alertBox.classList.add("hidden");
  alertBox.className = "alert hidden";
  alertBox.innerHTML = "";
  resultPanel.classList.add("hidden");
  resultBody.innerHTML = "";
  tbody.querySelectorAll("tr.row-error").forEach((tr) => {
    tr.classList.remove("row-error");
    const p = tr.querySelector(".row-msg");
    if (p) p.remove();
  });
}

function markRowError(rowNo, message) {
  const tr = tbody.querySelector(`tr[data-row="${rowNo}"]`);
  if (!tr) return;
  tr.classList.add("row-error");
  const p = document.createElement("p");
  p.className = "row-msg";
  p.textContent = message;
  tr.querySelector("td:nth-child(2)").appendChild(p);
}

async function runVerify() {
  clearConclusion();

  const payload = rows.map((r, i) => {
    const item = {
      local: r.local.trim(),
      timezone: r.timezone.trim(),
    };
    if (i > 0) {
      item.min_seconds = r.min_seconds === "" ? null : Number(r.min_seconds);
      item.max_seconds = r.max_seconds === "" ? null : Number(r.max_seconds);
    }
    return item;
  });

  let data;
  try {
    const resp = await fetch("/api/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    data = await resp.json();
  } catch (e) {
    showErrorAlert(1, "请求失败：" + e.message);
    return;
  }

  if (data.status === "error") {
    showErrorAlert(data.row, data.message);
    markRowError(data.row, data.message);
    return;
  }
  if (data.status === "infeasible") {
    showInfeasible(data);
    return;
  }
  if (data.status === "ok") {
    renderResult(data);
  }
}

function showErrorAlert(row, message) {
  alertBox.className = "alert alert-error";
  alertBox.innerHTML = `<h3>第 ${row} 行录入有误</h3><div>${escapeHtml(message)}</div>`;
}

function showInfeasible(data) {
  markRowError(data.first_bad_row, data.reason);
  alertBox.className = "alert alert-infeasible";
  let html = `<h3>无完整可行路径 —— 首个无法延伸的前缀：第 1～${data.first_bad_row} 行</h3>
              <div>${escapeHtml(data.reason)}</div>`;
  if (Array.isArray(data.details) && data.details.length) {
    html += "<ul>";
    for (const d of data.details) html += `<li>${escapeHtml(d.message)}</li>`;
    html += "</ul>";
  }
  alertBox.innerHTML = html;
}

function renderResult(data) {
  resultBody.innerHTML = "";
  data.rows.forEach((row) => {
    const tr = document.createElement("tr");
    const interval =
      row.min_seconds === null
        ? '<span class="muted">—</span>'
        : `[${row.min_seconds}, ${row.max_seconds}]`;
    const gap =
      row.gap_seconds === null
        ? '<span class="muted">—</span>'
        : `<span class="gap-ok">${row.gap_seconds}</span>`;

    let note = "";
    if (row.candidate_count > 1) {
      note = `<span class="badge badge-amb">秋拨歧义：${row.candidate_count} 个 UTC 候选，采用 fold=${row.fold}</span>`;
    } else {
      note = `<span class="muted">唯一候选（fold=${row.fold}）</span>`;
    }

    tr.innerHTML = `
      <td>${row.row}</td>
      <td>${escapeHtml(row.local)}</td>
      <td>${escapeHtml(row.timezone)}</td>
      <td>${escapeHtml(row.utc)}</td>
      <td>${escapeHtml(row.offset)} <span class="muted">${escapeHtml(row.offset_abbrev)}</span></td>
      <td>${interval}</td>
      <td>${gap}</td>
      <td>${note}</td>
    `;
    resultBody.appendChild(tr);
  });
  resultPanel.classList.remove("hidden");
}

function escapeHtml(v) {
  return String(v).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

// 初始化
addBtn.addEventListener("click", () => addRow());
verifyBtn.addEventListener("click", runVerify);

addRow({
  local: "2024-11-03 01:30",
  timezone: "America/New_York",
});
addRow({
  local: "2024-11-03 22:00",
  timezone: "Asia/Shanghai",
  min_seconds: "27000",
  max_seconds: "30600",
});

// 拉取 IANA 时区列表用于自动补全（失败也不影响手输）。
fetch("/api/timezones")
  .then((r) => r.json())
  .then((data) => {
    for (const name of data.timezones || []) {
      const opt = document.createElement("option");
      opt.value = name;
      tzDatalist.appendChild(opt);
    }
  })
  .catch(() => {});
