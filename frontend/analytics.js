/**
 * Knox ECO 5000 Solar Monitor - Historical Analytics Controller
 * Handles daily/monthly energy rollups, stacked bar charts, 24h hourly profiles,
 * peak usage detection, tariff savings calculation, and CSV exports.
 */

let currentRange = "today";
let customStartDate = null;
let customEndDate = null;
let customStartTime = "00:00";
let customEndTime = "23:59";
let currentTariff = 65.0;
let selectedDate = null;
let charts = {};

function getLocalDateString(d = new Date()) {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function getLocalTimeString(d = new Date()) {
  const hours = String(d.getHours()).padStart(2, "0");
  const minutes = String(d.getMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}

document.addEventListener("DOMContentLoaded", () => {
  initTariff();
  initRangeControls();
  initCharts();
  loadAnalytics();
  initTariffModal();
  initCsvExport();
});

// -----------------------------------------------------------------------------
// Tariff Management
// -----------------------------------------------------------------------------
async function initTariff() {
  try {
    const res = await fetch("/api/analytics/tariff");
    if (res.ok) {
      const data = await res.json();
      currentTariff = parseFloat(data.rate_per_kwh) || 65.0;
      updateTariffDisplay();
    }
  } catch (err) {
    console.warn("Using default tariff rate:", err);
  }
}

function updateTariffDisplay() {
  const headerTariff = document.getElementById("header-tariff-val");
  const footerTariff = document.getElementById("footer-tariff-display");
  const inputTariff = document.getElementById("input-tariff-rate");

  const text = `${currentTariff.toFixed(1)} PKR / Unit`;
  if (headerTariff) headerTariff.textContent = `${currentTariff.toFixed(1)} PKR/kWh`;
  if (footerTariff) footerTariff.textContent = text;
  if (inputTariff) inputTariff.value = currentTariff.toFixed(1);
}

// -----------------------------------------------------------------------------
// Range & Date-Time Controls
// -----------------------------------------------------------------------------
function initRangeControls() {
  const buttons = document.querySelectorAll(".analytics-range-btn");
  const rangeLabel = document.getElementById("active-range-label");
  const startDateInput = document.getElementById("filter-start-date");
  const startTimeInput = document.getElementById("filter-start-time");
  const endDateInput = document.getElementById("filter-end-date");
  const endTimeInput = document.getElementById("filter-end-time");
  const applyBtn = document.getElementById("btn-apply-filter");
  const nowBtn = document.getElementById("btn-set-now");

  function setDatesForRange(range) {
    const now = new Date();
    const today = getLocalDateString(now);

    if (range === "today") {
      customStartDate = today;
      customStartTime = "00:00";
      customEndDate = today;
      customEndTime = "23:59";
      if (rangeLabel) rangeLabel.textContent = "Showing Today";
    } else if (range === "yesterday") {
      const yest = new Date(now);
      yest.setDate(yest.getDate() - 1);
      const yestStr = getLocalDateString(yest);
      customStartDate = yestStr;
      customStartTime = "00:00";
      customEndDate = yestStr;
      customEndTime = "23:59";
      if (rangeLabel) rangeLabel.textContent = "Showing Yesterday";
    } else if (range === "7d") {
      const d7 = new Date(now);
      d7.setDate(d7.getDate() - 6);
      customStartDate = getLocalDateString(d7);
      customStartTime = "00:00";
      customEndDate = today;
      customEndTime = "23:59";
      if (rangeLabel) rangeLabel.textContent = "Showing Last 7 Days";
    } else if (range === "this_month") {
      const mStart = new Date(now.getFullYear(), now.getMonth(), 1);
      customStartDate = getLocalDateString(mStart);
      customStartTime = "00:00";
      customEndDate = today;
      customEndTime = "23:59";
      if (rangeLabel) rangeLabel.textContent = "Showing This Month";
    } else if (range === "all") {
      customStartDate = "2026-01-01";
      customStartTime = "00:00";
      customEndDate = today;
      customEndTime = "23:59";
      if (rangeLabel) rangeLabel.textContent = "Showing All Time History";
    }

    if (startDateInput) startDateInput.value = customStartDate;
    if (startTimeInput) startTimeInput.value = customStartTime;
    if (endDateInput) endDateInput.value = customEndDate;
    if (endTimeInput) endTimeInput.value = customEndTime;
  }

  // Set default initial dates for "today"
  setDatesForRange("today");

  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const range = btn.getAttribute("data-range");
      buttons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");

      currentRange = range;
      setDatesForRange(range);
      loadAnalytics();
    });
  });

  if (nowBtn) {
    nowBtn.addEventListener("click", () => {
      const now = new Date();
      if (endDateInput) endDateInput.value = getLocalDateString(now);
      if (endTimeInput) endTimeInput.value = getLocalTimeString(now);
    });
  }

  // Format and sanitize 24-hour time inputs
  [startTimeInput, endTimeInput].forEach((input) => {
    if (!input) return;
    input.addEventListener("blur", () => {
      const isStart = input === startTimeInput;
      input.value = sanitize24hTime(input.value, isStart ? "00:00" : "23:59");
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (applyBtn) applyBtn.click();
      }
    });
  });

  if (applyBtn) {
    applyBtn.addEventListener("click", () => {
      const sDate = startDateInput ? startDateInput.value.trim() : "";
      const eDate = endDateInput ? endDateInput.value.trim() : "";
      const rawSTime = startTimeInput ? startTimeInput.value : "";
      const rawETime = endTimeInput ? endTimeInput.value : "";

      const sTime = sanitize24hTime(rawSTime, "00:00");
      const eTime = sanitize24hTime(rawETime, "23:59");

      if (startTimeInput) startTimeInput.value = sTime;
      if (endTimeInput) endTimeInput.value = eTime;

      if (!sDate || !eDate) return;

      buttons.forEach((b) => b.classList.remove("active"));
      currentRange = "custom";
      customStartDate = sDate;
      customStartTime = sTime;
      customEndDate = eDate;
      customEndTime = eTime;

      if (rangeLabel) {
        rangeLabel.textContent = `Showing ${sDate} ${sTime} to ${eDate} ${eTime}`;
      }
      loadAnalytics();
    });
  }
}

function sanitize24hTime(str, fallback = "00:00") {
  if (!str) return fallback;
  str = String(str).trim().toLowerCase();
  const isPm = str.includes("pm");
  const isAm = str.includes("am");
  str = str.replace(/[^\d:]/g, "");

  let hours = 0;
  let mins = 0;

  if (str.includes(":")) {
    const parts = str.split(":");
    hours = parseInt(parts[0], 10) || 0;
    mins = parseInt(parts[1], 10) || 0;
  } else if (str.length === 3 || str.length === 4) {
    hours = parseInt(str.slice(0, -2), 10) || 0;
    mins = parseInt(str.slice(-2), 10) || 0;
  } else if (str.length > 0) {
    hours = parseInt(str, 10) || 0;
    mins = 0;
  } else {
    return fallback;
  }

  if (isPm && hours < 12) hours += 12;
  if (isAm && hours === 12) hours = 0;

  hours = Math.max(0, Math.min(23, hours));
  mins = Math.max(0, Math.min(59, mins));

  return `${String(hours).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
}

// -----------------------------------------------------------------------------
// Chart.js Initializations
// -----------------------------------------------------------------------------
function initCharts() {
  const commonOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        labels: {
          color: "#cbd5e1",
          font: { family: "'Outfit', sans-serif", size: 12 },
          usePointStyle: true,
          boxWidth: 10,
        },
      },
      tooltip: {
        backgroundColor: "rgba(15, 23, 42, 0.95)",
        borderColor: "rgba(255, 255, 255, 0.15)",
        borderWidth: 1,
        titleFont: { family: "'JetBrains Mono', monospace" },
        bodyFont: { family: "'JetBrains Mono', monospace" },
      },
    },
    scales: {
      x: {
        grid: { color: "rgba(255, 255, 255, 0.04)" },
        ticks: { color: "#94a3b8", font: { family: "'JetBrains Mono', monospace", size: 11 } },
      },
      y: {
        beginAtZero: true,
        min: 0,
        grid: { color: "rgba(255, 255, 255, 0.06)" },
        ticks: { color: "#94a3b8", font: { family: "'JetBrains Mono', monospace", size: 11 } },
      },
    },
  };

  // Chart 0: Filtered Time Power Flow Timeline (Solar, Load, Utility Grid, Battery Charge & Discharge)
  const canvasFiltered = document.getElementById("chart-filtered-power");
  if (canvasFiltered) {
    const ctxFiltered = canvasFiltered.getContext("2d");
    charts.filteredPower = new Chart(ctxFiltered, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Solar PV (W)",
            data: [],
            borderColor: "#f59e0b",
            backgroundColor: "rgba(245, 158, 11, 0.12)",
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            fill: true,
            tension: 0.25,
          },
          {
            label: "Home Load (W)",
            data: [],
            borderColor: "#f43f5e",
            backgroundColor: "rgba(244, 63, 94, 0.05)",
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            fill: false,
            tension: 0.25,
          },
          {
            label: "Utility Grid (W)",
            data: [],
            borderColor: "#818cf8",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            pointRadius: 0,
            pointHoverRadius: 4,
            borderDash: [4, 4],
            tension: 0.25,
          },
          {
            label: "Battery Charge (W)",
            data: [],
            borderColor: "#10b981",
            backgroundColor: "rgba(16, 185, 129, 0.08)",
            borderWidth: 1.8,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.25,
          },
          {
            label: "Battery Discharge (W)",
            data: [],
            borderColor: "#c084fc",
            backgroundColor: "rgba(192, 132, 252, 0.08)",
            borderWidth: 1.8,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.25,
          },
        ],
      },
      options: {
        ...commonOptions,
        interaction: {
          mode: "index",
          intersect: false,
        },
        plugins: {
          ...commonOptions.plugins,
          tooltip: {
            ...commonOptions.plugins.tooltip,
            callbacks: {
              label: function (context) {
                const label = context.dataset.label || "";
                const val = context.parsed.y != null ? Math.round(context.parsed.y) : 0;
                return ` ${label}: ${val} W`;
              },
            },
          },
        },
        scales: {
          ...commonOptions.scales,
          y: {
            ...commonOptions.scales.y,
            beginAtZero: true,
            min: 0,
            title: { display: true, text: "Power (Watts)", color: "#94a3b8" },
          },
        },
      },
    });
  }

  // Chart 1: Daily Energy Mix Stacked Bar Chart
  const ctxDaily = document.getElementById("chart-daily-mix").getContext("2d");
  charts.dailyMix = new Chart(ctxDaily, {
    type: "bar",
    data: {
      labels: [],
      datasets: [
        {
          label: "Solar Generation (kWh)",
          data: [],
          backgroundColor: "rgba(245, 158, 11, 0.75)",
          borderColor: "#f59e0b",
          borderWidth: 1,
          borderRadius: 4,
          stack: "generation",
        },
        {
          label: "Grid Imported (kWh)",
          data: [],
          backgroundColor: "rgba(99, 102, 241, 0.75)",
          borderColor: "#818cf8",
          borderWidth: 1,
          borderRadius: 4,
          stack: "consumption",
        },
        {
          label: "Battery Discharged (kWh)",
          data: [],
          backgroundColor: "rgba(244, 63, 94, 0.75)",
          borderColor: "#fb7185",
          borderWidth: 1,
          borderRadius: 4,
          stack: "consumption",
        },
      ],
    },
    options: {
      ...commonOptions,
      scales: {
        ...commonOptions.scales,
        y: {
          ...commonOptions.scales.y,
          title: { display: true, text: "Energy (kWh)", color: "#94a3b8" },
        },
      },
    },
  });

  // Chart 2: 24-Hour Hourly Profile Line Chart
  const ctxHourly = document.getElementById("chart-hourly-profile").getContext("2d");
  charts.hourly = new Chart(ctxHourly, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Solar PV (W)",
          data: [],
          borderColor: "#f59e0b",
          backgroundColor: "rgba(245, 158, 11, 0.15)",
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.3,
          yAxisID: "y",
        },
        {
          label: "Home Load (W)",
          data: [],
          borderColor: "#fb7185",
          backgroundColor: "transparent",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          yAxisID: "y",
        },
        {
          label: "Utility Grid (W)",
          data: [],
          borderColor: "#818cf8",
          backgroundColor: "transparent",
          borderWidth: 1.5,
          pointRadius: 0,
          borderDash: [4, 4],
          tension: 0.3,
          yAxisID: "y",
        },
        {
          label: "Battery SOC (%)",
          data: [],
          borderColor: "#34d399",
          backgroundColor: "transparent",
          borderWidth: 1.5,
          pointRadius: 0,
          yAxisID: "y1",
          tension: 0.3,
        },
      ],
    },
    options: {
      ...commonOptions,
      scales: {
        ...commonOptions.scales,
        y: {
          ...commonOptions.scales.y,
          beginAtZero: true,
          min: 0,
          title: { display: true, text: "Power (Watts)", color: "#94a3b8" },
        },
        y1: {
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#34d399" },
          title: { display: true, text: "SOC (%)", color: "#34d399" },
          min: 0,
          max: 100,
        },
      },
    },
  });
}

// -----------------------------------------------------------------------------
// Load Daily Analytics & Populate UI
// -----------------------------------------------------------------------------
async function loadAnalytics() {
  try {
    let url = `/api/analytics/daily?range=${currentRange}&tariff=${currentTariff}`;
    if (customStartDate && customEndDate) {
      url += `&start_date=${encodeURIComponent(customStartDate)}&end_date=${encodeURIComponent(customEndDate)}`;
    }
    if (customStartTime && customEndTime) {
      url += `&start_time=${encodeURIComponent(customStartTime)}&end_time=${encodeURIComponent(customEndTime)}`;
    }

    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    updateKpiCards(data.totals, data.daily);
    updateDailyMixChart(data.daily);
    populateTable(data.daily);

    // Update the Filtered Power Flow Timeline graph for the active filter
    loadFilteredPowerGraph();

    // Pick date for 24h hourly profile: latest active date or customStartDate or today
    if (data.daily && data.daily.length > 0) {
      const defaultDate = data.daily[0].date;
      loadHourlyProfile(defaultDate);
    } else if (customStartDate) {
      loadHourlyProfile(customStartDate);
    } else {
      loadHourlyProfile(getLocalDateString());
    }
  } catch (err) {
    console.error("Failed to load analytics data:", err);
  }
}

// -----------------------------------------------------------------------------
// Update Hero KPI Cards
// -----------------------------------------------------------------------------
function updateKpiCards(totals, daily) {
  if (!totals) return;

  // 1. Solar Generation
  document.getElementById("kpi-solar-kwh").innerHTML = `${totals.solar_kwh.toFixed(2)} <span class="kpi-unit">kWh</span>`;
  document.getElementById("kpi-solar-peak").textContent = `${Math.round(totals.peak_pv_w)} W`;

  // Find peak time for the highest peak day
  let peakPvTime = "--";
  let peakLoadTime = "--";
  if (daily && daily.length > 0) {
    const highestSolarDay = daily.reduce((prev, curr) => (curr.peak_pv_w > prev.peak_pv_w ? curr : prev), daily[0]);
    if (highestSolarDay && highestSolarDay.peak_pv_w > 0) {
      peakPvTime = daily.length === 1 ? highestSolarDay.peak_pv_time : `${highestSolarDay.peak_pv_time} (${highestSolarDay.date})`;
    }
    const highestLoadDay = daily.reduce((prev, curr) => (curr.peak_load_w > prev.peak_load_w ? curr : prev), daily[0]);
    if (highestLoadDay && highestLoadDay.peak_load_w > 0) {
      peakLoadTime = daily.length === 1 ? highestLoadDay.peak_load_time : `${highestLoadDay.peak_load_time} (${highestLoadDay.date})`;
    }
  }
  document.getElementById("kpi-solar-peak-time").textContent = peakPvTime;

  // 2. Utility Grid
  document.getElementById("kpi-grid-kwh").innerHTML = `${totals.grid_kwh.toFixed(2)} <span class="kpi-unit">kWh</span>`;
  document.getElementById("kpi-grid-peak").textContent = `${Math.round(totals.peak_grid_w)} W`;
  document.getElementById("kpi-grid-cost").textContent = `Rs ${totals.grid_cost_pkr.toFixed(2)}`;

  // 3. Home Load
  document.getElementById("kpi-load-kwh").innerHTML = `${totals.load_kwh.toFixed(2)} <span class="kpi-unit">kWh</span>`;
  document.getElementById("kpi-load-peak").textContent = `${Math.round(totals.peak_load_w)} W`;
  document.getElementById("kpi-load-peak-time").textContent = peakLoadTime;

  // 4. Autarky & Savings
  document.getElementById("kpi-savings-pkr").innerHTML = `Rs ${totals.savings_pkr.toFixed(2)} <span class="kpi-unit">PKR</span>`;
  document.getElementById("kpi-autarky-pct").textContent = `${totals.autarky_pct.toFixed(1)}%`;
  document.getElementById("kpi-active-days").textContent = `${totals.active_days} Day${totals.active_days === 1 ? "" : "s"}`;
}

// -----------------------------------------------------------------------------
// Update Daily Stacked Bar Chart
// -----------------------------------------------------------------------------
function updateDailyMixChart(daily) {
  if (!charts.dailyMix) return;

  // Reverse so older dates are on left, newer on right
  const sorted = [...daily].reverse();

  const labels = sorted.map((d) => d.date);
  const solarSeries = sorted.map((d) => d.pv_kwh);
  const gridSeries = sorted.map((d) => d.grid_kwh);
  const batDischargeSeries = sorted.map((d) => d.bat_discharge_kwh);

  charts.dailyMix.data.labels = labels;
  charts.dailyMix.data.datasets[0].data = solarSeries;
  charts.dailyMix.data.datasets[1].data = gridSeries;
  charts.dailyMix.data.datasets[2].data = batDischargeSeries;
  charts.dailyMix.update();
}

// -----------------------------------------------------------------------------
// Populate Historical Table
// -----------------------------------------------------------------------------
function populateTable(daily) {
  const tbody = document.getElementById("table-body");
  if (!tbody) return;

  if (!daily || daily.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" class="table-loading">No telemetry snapshots found for this period.</td></tr>`;
    return;
  }

  tbody.innerHTML = daily
    .map((d) => {
      let badgeClass = "badge-green";
      if (d.autarky_pct < 60) badgeClass = "badge-red";
      else if (d.autarky_pct < 90) badgeClass = "badge-yellow";

      return `
        <tr>
          <td class="table-date-cell">${d.date}</td>
          <td class="table-solar-cell">${d.pv_kwh.toFixed(2)}</td>
          <td class="table-grid-cell">${d.grid_kwh.toFixed(2)}</td>
          <td class="table-load-cell">${d.load_kwh.toFixed(2)}</td>
          <td>
            <span class="table-peak-val">${Math.round(d.peak_pv_w)} W</span>
            <span class="table-peak-time">${d.peak_pv_time}</span>
          </td>
          <td>
            <span class="table-peak-val">${Math.round(d.peak_load_w)} W</span>
            <span class="table-peak-time">${d.peak_load_time}</span>
          </td>
          <td>
            <span class="table-peak-val">${Math.round(d.peak_grid_w)} W</span>
            <span class="table-peak-time">${d.peak_grid_time}</span>
          </td>
          <td><span class="autarky-badge ${badgeClass}">${d.autarky_pct.toFixed(0)}%</span></td>
          <td class="table-savings-cell">Rs ${d.savings_pkr.toFixed(1)}</td>
          <td>
            <button class="btn-inspect-day" onclick="inspectDay('${d.date}')">
              Inspect 24h
            </button>
          </td>
        </tr>
      `;
    })
    .join("");
}

// -----------------------------------------------------------------------------
// Load Filtered Power Flow Timeline (Solar, Load, Utility Grid, Battery Charge & Discharge)
// -----------------------------------------------------------------------------
async function loadFilteredPowerGraph(overrideParams = null) {
  const subtitleEl = document.getElementById("filtered-power-subtitle");
  const badgeEl = document.getElementById("filtered-power-badge");
  const chipSolar = document.getElementById("chip-peak-solar");
  const chipLoad = document.getElementById("chip-peak-load");
  const chipGrid = document.getElementById("chip-peak-grid");
  const chipCharge = document.getElementById("chip-peak-charge");
  const chipDischarge = document.getElementById("chip-peak-discharge");

  let range = currentRange;
  let sDate = customStartDate;
  let eDate = customEndDate;
  let sTime = customStartTime || "00:00";
  let eTime = customEndTime || "23:59";

  if (overrideParams) {
    if (overrideParams.range) range = overrideParams.range;
    if (overrideParams.start_date) sDate = overrideParams.start_date;
    if (overrideParams.end_date) eDate = overrideParams.end_date;
    if (overrideParams.start_time) sTime = overrideParams.start_time;
    if (overrideParams.end_time) eTime = overrideParams.end_time;
  }

  let url = `/api/analytics/power_timeseries?range=${range}`;
  if (sDate && eDate) {
    url += `&start_date=${encodeURIComponent(sDate)}&end_date=${encodeURIComponent(eDate)}`;
  }
  if (sTime && eTime) {
    url += `&start_time=${encodeURIComponent(sTime)}&end_time=${encodeURIComponent(eTime)}`;
  }

  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (badgeEl) {
      if (range === "today") {
        badgeEl.textContent = `Today (${sDate || getLocalDateString()})`;
      } else if (range === "yesterday") {
        badgeEl.textContent = `Yesterday (${sDate || ""})`;
      } else if (range === "7d") {
        badgeEl.textContent = `Last 7 Days`;
      } else if (range === "this_month") {
        badgeEl.textContent = `This Month`;
      } else if (range === "all") {
        badgeEl.textContent = `All Time`;
      } else {
        badgeEl.textContent = `${sDate} ${sTime} → ${eDate} ${eTime}`;
      }
    }

    if (subtitleEl) {
      if (data.point_count > 0) {
        subtitleEl.textContent = `Showing ${data.point_count} interval snapshots (${data.bucket_seconds}s cadence) — Solar PV, Home Load, Utility Grid, Battery Charge & Discharge`;
      } else {
        subtitleEl.textContent = `No power snapshots recorded for this time window.`;
      }
    }

    if (data.summary) {
      if (chipSolar) chipSolar.textContent = `Peak Solar: ${Math.round(data.summary.peak_solar_w)} W`;
      if (chipLoad) chipLoad.textContent = `Peak Load: ${Math.round(data.summary.peak_load_w)} W`;
      if (chipGrid) chipGrid.textContent = `Peak Grid: ${Math.round(data.summary.peak_wapda_w)} W`;
      if (chipCharge) chipCharge.textContent = `Peak Charge: ${Math.round(data.summary.peak_bat_charge_w)} W`;
      if (chipDischarge) chipDischarge.textContent = `Peak Discharge: ${Math.round(data.summary.peak_bat_discharge_w)} W`;
    }

    if (charts.filteredPower) {
      charts.filteredPower.data.labels = data.labels || [];
      charts.filteredPower.data.datasets[0].data = data.solar_w || [];
      charts.filteredPower.data.datasets[1].data = data.load_w || [];
      charts.filteredPower.data.datasets[2].data = data.wapda_w || [];
      charts.filteredPower.data.datasets[3].data = data.bat_charge_w || [];
      charts.filteredPower.data.datasets[4].data = data.bat_discharge_w || [];
      charts.filteredPower.update();
    }
  } catch (err) {
    console.error("Failed to load filtered power timeseries:", err);
  }
}

// -----------------------------------------------------------------------------
// Load 24-Hour Profile for Selected Day
// -----------------------------------------------------------------------------
async function loadHourlyProfile(dayStr) {
  selectedDate = dayStr;
  const titleEl = document.getElementById("profile-chart-title");
  const badgeEl = document.getElementById("profile-date-badge");

  if (titleEl) titleEl.textContent = `24-Hour Energy Profile (${dayStr})`;
  if (badgeEl) badgeEl.textContent = dayStr;

  try {
    const res = await fetch(`/api/analytics/day?date=${dayStr}`);
    if (!res.ok) return;
    const data = await res.json();

    if (charts.hourly) {
      charts.hourly.data.labels = data.labels;
      charts.hourly.data.datasets[0].data = data.pv_series;
      charts.hourly.data.datasets[1].data = data.load_series;
      charts.hourly.data.datasets[2].data = data.grid_series;
      charts.hourly.data.datasets[3].data = data.soc_series;
      charts.hourly.update();
    }
  } catch (err) {
    console.error("Failed to load day hourly profile:", err);
  }
}

function inspectDay(dayStr) {
  selectedDate = dayStr;
  loadHourlyProfile(dayStr);
  loadFilteredPowerGraph({
    range: "custom",
    start_date: dayStr,
    end_date: dayStr,
    start_time: "00:00",
    end_time: "23:59",
  });
}

// -----------------------------------------------------------------------------
// Tariff Configuration Modal
// -----------------------------------------------------------------------------
function initTariffModal() {
  const modal = document.getElementById("tariff-modal");
  const openBtn = document.getElementById("btn-edit-tariff");
  const closeBtn = document.getElementById("btn-close-tariff");
  const saveBtn = document.getElementById("btn-save-tariff");
  const inputTariff = document.getElementById("input-tariff-rate");

  if (openBtn) {
    openBtn.addEventListener("click", () => {
      inputTariff.value = currentTariff.toFixed(1);
      modal.classList.remove("hidden");
    });
  }

  if (closeBtn) {
    closeBtn.addEventListener("click", () => modal.classList.add("hidden"));
  }

  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.classList.add("hidden");
    });
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const val = parseFloat(inputTariff.value);
      if (isNaN(val) || val <= 0) return;

      currentTariff = val;
      updateTariffDisplay();
      modal.classList.add("hidden");

      try {
        await fetch("/api/analytics/tariff", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rate_per_kwh: currentTariff, currency: "PKR" }),
        });
      } catch (err) {
        console.error("Failed to save tariff remotely:", err);
      }

      // Reload analytics with new tariff
      loadAnalytics();
    });
  }
}

// -----------------------------------------------------------------------------
// CSV Export
// -----------------------------------------------------------------------------
function initCsvExport() {
  const exportBtn = document.getElementById("btn-export-csv");
  if (!exportBtn) return;

  exportBtn.addEventListener("click", () => {
    let url = "/api/analytics/export_csv";
    const params = [];
    if (customStartDate && customEndDate) {
      params.push(`start_date=${encodeURIComponent(customStartDate)}`);
      params.push(`end_date=${encodeURIComponent(customEndDate)}`);
    }
    if (customStartTime && customEndTime) {
      params.push(`start_time=${encodeURIComponent(customStartTime)}`);
      params.push(`end_time=${encodeURIComponent(customEndTime)}`);
    }
    if (params.length > 0) {
      url += `?${params.join("&")}`;
    }
    window.location.href = url;
  });
}
