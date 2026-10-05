"use strict";

const MIN_ROWS = 2;
const MAX_ROWS = 12;

const body = document.getElementById("entry-body");
const addBtn = document.getElementById("add-btn");
const exampleBtn = document.getElementById("example-btn");
const clearBtn = document.getElementById("clear-btn");
const verifyBtn = document.getElementById("verify-btn");
const errorPanel = document.getElementById("error-panel");
const resultPanel = document.getElementById("result-panel");
const resultTitle = document.getElementById("result-title");
const resultSummary = document.getElementById("result-summary");
const resultBody = document.getElementById("result-body");
const resultTable = document.getElementById("result-table");

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "className") node.className = v;
    else node.setAttribute(k, v);
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

function makeRow() {
  const tr = el("tr", { className: "entry-row" });
  tr.appendChild(el("td", { className: "row-idx" }));
  tr.appendChild(el("td", {}, null)).appendChild(
    el("input", { type: "text", className: "f-local", placeholder: "2024-11-03 01:30", autocomplete: "off" })
  );
  const tzTd = el("td");
  tzTd.appendChild(
    el("input", { type: "text", className: "f-tz", list: "tz-list", placeholder: "America/New_York", autocomplete: "off" })
  );
  tr.appendChild(tzTd);

  const minTd = el("td");
  minTd.appendChild(el("input", { type: "number", className: "f-min", min: "0", max: "86400", step: "1", placeholder: "0" }));
  tr.appendChild(minTd);

  const maxTd = el("td");
  maxTd.appendChild(el("input", { type: "number", className: "f-max", min: "0", max: "86400", step: "1", placeholder: "86400" }));
  tr.appendChild(maxTd);

  const actTd = el("td");
  actTd.appendChild(el("button", { type: "button", className: "btn btn-del f-del" }, "删除"));
  tr.appendChild(actTd);
  return tr;
}

function refreshRows() {
  const rows = body.querySelectorAll(".entry-row");
  rows.forEach((tr, i) => {
    tr.querySelector(".row-idx").textContent = String(i + 1);
    const isFirst = i === 0;
    ["f-min", "f-max"].forEach((cls) => {
      const input = tr.querySelector("." + cls);
      input.disabled = isFirst;
      input.placeholder = isFirst ? "—" : (cls === "f-min" ? "0" : "86400");
    });
    tr.querySelector(".f-del").disabled = rows.length <= MIN_ROWS;
  });
  addBtn.disabled = rows.length >= MAX_ROWS;
}

function addRow() {
  if (body.querySelectorAll(".entry-row").length >= MAX_ROWS) return;
  body.appendChild(makeRow());
  refreshRows();
  clearConclusions();
}

body.addEventListener("click", (e) => {
  if (e.target.classList.contains("f-del")) {
    e.target.closest(".entry-row").remove();
    refreshRows();
    clearConclusions();
  }
});

addBtn.addEventListener("click", addRow);

// 任意编辑后立即清除旧结论（错误高亮与结果表）
body.addEventListener("input", () => {
  clearFieldErrors();
  resultPanel.hidden = true;
  errorPanel.hidden = true;
});

function clearFieldErrors() {
  body.querySelectorAll(".input-error").forEach((n) => n.classList.remove("input-error"));
  body.querySelectorAll(".row-error").forEach((n) => n.classList.remove("row-error"));
}

function parseSeconds(value) {
  if (value.trim() === "") return null;
  if (!/^[+-]?\d+$/.test(value.trim())) return value; // 原样字符串，由服务端报格式错误
  return Number(value.trim());
}

function collect() {
  const records = [];
  body.querySelectorAll(".entry-row").forEach((tr, i) => {
    const rec = {
      local: tr.querySelector(".f-local").value.trim(),
      timezone: tr.querySelector(".f-tz").value.trim(),
    };
    if (i > 0) {
      rec.min_seconds = parseSeconds(tr.querySelector(".f-min").value);
      rec.max_seconds = parseSeconds(tr.querySelector(".f-max").value);
    }
    records.push(rec);
  });
  return records;
}

function showErrors(payload) {
  clearFieldErrors();
  errorPanel.replaceChildren();
  errorPanel.appendChild(el("h2", {}, "录入内容存在问题"));
  const ul = el("ul");
  const rows = body.querySelectorAll(".entry-row");
  for (const e of payload.errors || []) {
    const li = el("li");
    li.textContent = (e.row ? `第 ${e.row} 行 · ${e.field || ""}：` : "整体：") + e.message;
    ul.appendChild(li);
    if (e.row) {
      const tr = rows[e.row - 1];
      if (tr) {
        tr.classList.add("row-error");
        if (e.field) {
          const input = tr.querySelector(".f-" + e.field.replace("_seconds", "").replace("timezone", "tz").replace("local", "local"));
          if (input) input.classList.add("input-error");
        }
      }
    }
  }
  errorPanel.appendChild(ul);
  errorPanel.hidden = false;
  resultPanel.hidden = true;
}

function fmtSeconds(s) {
  if (s === null || s === undefined) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${s} 秒（${h} 时 ${m} 分 ${sec} 秒）`;
}

function showResult(payload) {
  errorPanel.hidden = true;
  resultPanel.classList.remove("ok", "bad");
  resultBody.replaceChildren();

  if (!payload.feasible) {
    resultPanel.classList.add("bad");
    resultTitle.textContent = "不存在满足全部约束的完整路径";
    resultSummary.textContent =
      `首个无法延伸的前缀为第 ${payload.first_bad_row} 行：` +
      `前 ${payload.first_bad_row} 条记录已无法选出满足区间约束的 UTC 序列，` +
      `后续行不再需要检查。请放宽该行（或更早行）的秒数区间。`;
    resultTable.style.display = "none";
    resultPanel.hidden = false;
    return;
  }

  resultPanel.classList.add("ok");
  resultTitle.textContent = `核验通过：${payload.rows.length} 条记录存在完整可行路径`;
  resultSummary.textContent =
    "下表为全部可行路径中 UTC 序列字典序最早的一条（整体择优，非逐行贪选）。";
  resultTable.style.display = "";

  payload.rows.forEach((r) => {
    const tr = el("tr");
    tr.appendChild(el("td", {}, String(r.row)));
    tr.appendChild(el("td", { className: "mono" }, r.utc + " UTC"));
    tr.appendChild(el("td", { className: "mono" }, r.offset));
    tr.appendChild(el("td", { className: "mono" }, r.tz_abbr || ""));

    const ambTd = el("td");
    if (r.ambiguous) {
      ambTd.appendChild(el("span", { className: "badge badge-ambig" }, `回拨第 ${r.occurrence} 次出现（fold=${r.fold}）`));
    } else {
      ambTd.appendChild(el("span", { className: "badge badge-single" }, "唯一"));
    }
    tr.appendChild(ambTd);

    tr.appendChild(el("td", { className: "mono" }, fmtSeconds(r.actual_seconds)));
    resultBody.appendChild(tr);
  });
  resultPanel.hidden = false;
}

verifyBtn.addEventListener("click", async () => {
  clearFieldErrors();
  errorPanel.hidden = true;
  resultPanel.hidden = true;
  verifyBtn.disabled = true;
  verifyBtn.textContent = "核验中…";
  try {
    const resp = await fetch("/api/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ records: collect() }),
    });
    const payload = await resp.json();
    if (payload.ok) {
      showResult(payload);
    } else {
      showErrors(payload);
    }
  } catch (err) {
    errorPanel.replaceChildren();
    errorPanel.appendChild(el("h2", {}, "请求失败"));
    errorPanel.appendChild(el("p", {}, String(err)));
    errorPanel.hidden = false;
  } finally {
    verifyBtn.disabled = false;
    verifyBtn.textContent = "核验";
  }
});

// 非贪选示例（4 行，均为纽约 2024-11-03 回拨日）：
//   1) 01:30（回拨歧义）→ 05:30Z(fold=0) / 06:30Z(fold=1)
//   2) 02:30（回拨已结束，唯一 EST）→ 07:30Z，区间 [0,3600]
//   3) 03:30（唯一）→ 08:30Z，区间 [0,3600]
//   4) UTC 08:30（唯一）→ 08:30Z，区间 [0,3600]
// 逐行贪选第 1 行较小候选 05:30Z，则到第 2 行为 7200 秒，超出 3600
// 上限而走死；只有第 1 行改取 06:30Z，06:30→07:30→08:30→08:30
// （3600 / 3600 / 0 秒）才完整可行。字典序最早的可行路径必须整体回搜索取。
const EXAMPLE = [
  { local: "2024-11-03 01:30", timezone: "America/New_York" },
  { local: "2024-11-03 02:30", timezone: "America/New_York", min_seconds: 0, max_seconds: 3600 },
  { local: "2024-11-03 03:30", timezone: "America/New_York", min_seconds: 0, max_seconds: 3600 },
  { local: "2024-11-03 08:30", timezone: "UTC", min_seconds: 0, max_seconds: 3600 },
];

exampleBtn.addEventListener("click", () => {
  body.replaceChildren();
  EXAMPLE.forEach((data) => {
    const tr = makeRow();
    body.appendChild(tr);
    tr.querySelector(".f-local").value = data.local;
    tr.querySelector(".f-tz").value = data.timezone;
    if (data.min_seconds !== undefined) {
      tr.querySelector(".f-min").value = data.min_seconds;
      tr.querySelector(".f-max").value = data.max_seconds;
    }
  });
  refreshRows();
  clearConclusions();
});

clearBtn.addEventListener("click", () => {
  body.replaceChildren();
  for (let i = 0; i < MIN_ROWS; i++) body.appendChild(makeRow());
  refreshRows();
  clearConclusions();
});

function clearConclusions() {
  clearFieldErrors();
  errorPanel.hidden = true;
  resultPanel.hidden = true;
}

// 常用时区 datalist 辅助输入
(function addTzList() {
  const dl = el("datalist", { id: "tz-list" });
  [
    "Asia/Shanghai", "Asia/Tokyo", "Asia/Singapore", "Asia/Hong_Kong",
    "Asia/Kolkata", "Asia/Dubai", "Europe/London", "Europe/Paris",
    "Europe/Berlin", "America/New_York", "America/Chicago",
    "America/Denver", "America/Los_Angeles", "America/Sao_Paulo",
    "Australia/Sydney", "Pacific/Auckland", "UTC",
  ].forEach((z) => dl.appendChild(el("option", { value: z })));
  document.body.appendChild(dl);
})();

// 初始化两行
for (let i = 0; i < MIN_ROWS; i++) body.appendChild(makeRow());
refreshRows();
