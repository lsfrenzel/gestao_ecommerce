/* Utilitários de interface: menu lateral, confirmação, gráficos (Chart.js) e formsets dinâmicos. */
(function () {
  "use strict";

  const brlFormatter = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  const numFormatter = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
  window.fmtBRL = (v) => (v === null || v === undefined || v === "" ? "—" : brlFormatter.format(Number(v)));
  window.fmtNum = (v) => (v === null || v === undefined || v === "" ? "—" : numFormatter.format(Number(v)));

  window.PALETTE = ["#1d4e89", "#0f9d58", "#f59e0b", "#d93025", "#7c3aed", "#0891b2", "#64748b", "#db2777",
                    "#65a30d", "#ea580c"];

  /** Cria um gráfico. `money` formata eixos/tooltip em R$. */
  window.makeChart = function (id, config, opts) {
    const el = document.getElementById(id);
    if (!el || typeof Chart === "undefined") return null;
    opts = opts || {};
    const money = !!opts.money;
    config.options = Object.assign({
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: config.type !== "bar" || (config.data.datasets || []).length > 1,
                  labels: { boxWidth: 12, usePointStyle: true } },
        tooltip: { callbacks: { label: (ctx) => {
          const raw = ctx.parsed.y !== undefined && config.options?.indexAxis !== "y" ? ctx.parsed.y
            : (ctx.parsed.x !== undefined ? ctx.parsed.x : ctx.parsed);
          const val = money ? fmtBRL(raw) : (opts.percent ? fmtNum(raw) + "%" : fmtNum(raw));
          return `${ctx.dataset.label || ctx.label}: ${val}`;
        } } },
      },
    }, config.options || {});
    if (["line", "bar"].includes(config.type)) {
      const axis = config.options.indexAxis === "y" ? "x" : "y";
      config.options.scales = Object.assign({
        [axis]: { beginAtZero: true, grid: { color: "#eef2f7" },
                  ticks: { callback: (v) => (money ? fmtBRL(v) : (opts.percent ? fmtNum(v) + "%" : fmtNum(v))) } },
        [axis === "y" ? "x" : "y"]: { grid: { display: false } },
      }, config.options.scales || {});
    }
    (config.data.datasets || []).forEach((ds, i) => {
      const color = ds.color || PALETTE[i % PALETTE.length];
      if (config.type === "doughnut" || config.type === "pie") {
        ds.backgroundColor = ds.backgroundColor || PALETTE;
        ds.borderWidth = 0;
      } else {
        ds.borderColor = ds.borderColor || color;
        ds.backgroundColor = ds.backgroundColor || (config.type === "line" ? color + "22" : color);
        ds.borderWidth = ds.borderWidth || 2;
        if (config.type === "line") { ds.tension = 0.3; ds.pointRadius = ds.pointRadius ?? 2; }
        if (config.type === "bar") { ds.borderRadius = 5; ds.maxBarThickness = 38; }
      }
    });
    return new Chart(el, config);
  };

  document.addEventListener("click", function (e) {
    const toggle = e.target.closest("[data-sidebar-toggle]");
    if (toggle) { document.querySelector(".sidebar")?.classList.toggle("open"); return; }
    const sidebar = document.querySelector(".sidebar.open");
    if (sidebar && !e.target.closest(".sidebar")) sidebar.classList.remove("open");

    const confirmEl = e.target.closest("[data-confirm]");
    if (confirmEl && confirmEl.tagName !== "FORM" && !window.confirm(confirmEl.dataset.confirm)) { e.preventDefault(); e.stopPropagation(); }

    const add = e.target.closest("[data-formset-add]");
    if (add) {
      e.preventDefault();
      const prefix = add.dataset.formsetAdd;
      const total = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
      const template = document.getElementById(`${prefix}-empty`);
      const target = document.getElementById(`${prefix}-rows`);
      if (!total || !template || !target) return;
      const index = parseInt(total.value, 10);
      const html = template.innerHTML.replace(/__prefix__/g, index);
      target.insertAdjacentHTML("beforeend", html);
      total.value = index + 1;
    }
  });

  document.addEventListener("submit", function (e) {
    const form = e.target;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) e.preventDefault();
  });
})();
