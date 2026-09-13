/**
 * Knox ECO 5000 Real-Time Solar Monitor - Frontend Controller
 * WebSockets, Dynamic SVG Flow Physics, and Chart.js Historical Visuals
 */

let ws = null;
let lastFrameEpoch = 0;
let currentRange = "1h";
let charts = {};

// -----------------------------------------------------------------------------
// Initialization
// -----------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initClock();
  initLifecycleListeners();
  initPollRateControls();
  initRangeControls();
  initCharts();
  connectWebSocket();
  loadHistory(currentRange);
  loadSummary();
  initMqttModal();
  initCloudModal();
  setInterval(loadSummary, 10000);
});

// -----------------------------------------------------------------------------
// Tab Lifecycle & Visibility Listeners (RAM Saver & Inactive Tab Handling)
// -----------------------------------------------------------------------------
function initLifecycleListeners() {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      console.log("Browser tab reactivated: syncing history and summary from server...");
      loadHistory(currentRange);
      loadSummary();
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        connectWebSocket();
      }
    }
  });

  window.addEventListener("focus", () => {
    const elapsed = (Date.now() - lastFrameEpoch) / 1000;
    if (elapsed > 8) {
      loadHistory(currentRange);
      loadSummary();
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        connectWebSocket();
      }
    }
  });
}

// -----------------------------------------------------------------------------
// Live Clock & Data Age
// -----------------------------------------------------------------------------
function initClock() {
  const clockEl = document.getElementById("live-clock");
  const ageEl = document.getElementById("data-age");

  setInterval(() => {
    const now = new Date();
    clockEl.textContent = now.toLocaleTimeString();

    if (lastFrameEpoch > 0) {
      const elapsed = ((Date.now() - lastFrameEpoch) / 1000).toFixed(1);
      ageEl.textContent = `Updated: ${elapsed}s ago`;
      if (elapsed > 10) {
        ageEl.style.color = "#f43f5e";
      } else {
        ageEl.style.color = "#94a3b8";
      }
    }
  }, 1000);
}

// -----------------------------------------------------------------------------
// Poll Rate Controls (1s, 2s, 5s)
// -----------------------------------------------------------------------------
function initPollRateControls() {
  const buttons = document.querySelectorAll(".rate-btn");
  buttons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const rate = parseFloat(btn.getAttribute("data-rate"));
      try {
        const resp = await fetch("/api/settings/poll_interval", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ interval: rate }),
        });
        if (resp.ok) {
          buttons.forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
        }
      } catch (err) {
        console.error("Failed to update poll rate:", err);
      }
    });
  });
}

// -----------------------------------------------------------------------------
// Historical Range Controls (1h, 6h, 24h, 7d)
// -----------------------------------------------------------------------------
function initRangeControls() {
  const buttons = document.querySelectorAll(".range-btn");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentRange = btn.getAttribute("data-range");
      loadHistory(currentRange);
    });
  });
}

// -----------------------------------------------------------------------------
// WebSocket Client
// -----------------------------------------------------------------------------
function connectWebSocket() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${location.host}/ws/live`;

  console.log("Connecting WebSocket to", wsUrl);
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log("WebSocket connected");
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      updateDashboard(data);
    } catch (err) {
      console.error("Error parsing WS frame:", err);
    }
  };

  ws.onclose = () => {
    console.log("WebSocket disconnected, retrying in 3s...");
    updateConnectionStatus(false);
    setTimeout(connectWebSocket, 3000);
  };

  ws.onerror = (err) => {
    console.error("WebSocket error:", err);
    ws.close();
  };
}

// -----------------------------------------------------------------------------
// Dashboard State & DOM Update
// -----------------------------------------------------------------------------
function updateDashboard(data) {
  lastFrameEpoch = Date.now();
  updateConnectionStatus(data.collector_connected, data.collector_ip);

  if (!data.collector_connected) {
    return;
  }

  // Operating Mode
  const modeBadge = document.getElementById("operating-mode-badge");
  const modeName = document.getElementById("mode-name");
  if (data.mode_name) {
    modeName.textContent = data.mode_name.toUpperCase();
    if (data.mode_code === "B") {
      modeBadge.style.background = "rgba(16, 185, 129, 0.2)";
      modeBadge.style.borderColor = "rgba(16, 185, 129, 0.5)";
      modeBadge.style.color = "#6ee7b7";
    } else {
      modeBadge.style.background = "rgba(99, 102, 241, 0.15)";
      modeBadge.style.borderColor = "rgba(99, 102, 241, 0.4)";
      modeBadge.style.color = "#a5b4fc";
    }
  }

  // Solar Metrics
  const pvPower = data.pv_power || 0;
  const pvV = data.pv_voltage || 0;
  const pvI = data.pv_current || 0;
  document.getElementById("flow-val-pv").innerHTML = `${Math.round(pvPower)} <span class="unit">W</span>`;
  document.getElementById("flow-sub-pv").textContent = `${pvV.toFixed(1)} V • ${pvI.toFixed(1)} A`;
  document.getElementById("card-pv-w").textContent = Math.round(pvPower);
  document.getElementById("card-pv-v").textContent = `${pvV.toFixed(1)} V`;
  document.getElementById("card-pv-i").textContent = `${pvI.toFixed(1)} A`;

  const pvTag = document.getElementById("pv-status-tag");
  if (pvPower > 20) {
    pvTag.textContent = "Producing";
    pvTag.className = "card-tag tag-solar";
  } else {
    pvTag.textContent = "Inactive (Night)";
    pvTag.className = "card-tag";
  }

  // Home Load Metrics
  const loadW = data.output_active_power || 0;
  const loadVa = data.output_apparent_power || 0;
  const loadPct = data.load_percent || 0;
  const outV = data.output_voltage || 0;
  const outFreq = data.output_frequency || 0;
  document.getElementById("flow-val-load").innerHTML = `${Math.round(loadW)} <span class="unit">W</span>`;
  document.getElementById("flow-sub-load").textContent = `${loadVa} VA • ${loadPct}%`;
  document.getElementById("inverter-load-pct").textContent = `${loadPct}%`;
  document.getElementById("card-load-w").textContent = Math.round(loadW);
  document.getElementById("card-load-va").textContent = `${loadVa} VA`;
  document.getElementById("card-load-pct").textContent = `${loadPct} %`;
  document.getElementById("card-load-ac").textContent = `${outV.toFixed(1)} V @ ${outFreq.toFixed(0)} Hz`;
  document.getElementById("card-load-bar").style.width = `${Math.min(100, loadPct)}%`;

  // Battery Metrics
  const batSoc = data.battery_soc || 0;
  const batV = data.battery_voltage || 0;
  const batChargeA = data.battery_charge_current || 0;
  const batDischargeA = data.battery_discharge_current || 0;
  const batNetW = data.battery_net_power || 0;
  const batStatus = data.battery_status || "Idle";

  document.getElementById("flow-val-bat").innerHTML = `${batSoc} <span class="unit">%</span>`;
  document.getElementById("flow-sub-bat").textContent = `${batV.toFixed(2)} V • ${batNetW > 0 ? "+" : ""}${batNetW.toFixed(0)} W`;
  document.getElementById("card-bat-soc").textContent = batSoc;
  document.getElementById("card-bat-v").textContent = `${batV.toFixed(2)} V`;
  document.getElementById("card-bat-charge-a").textContent = `${batChargeA} A`;
  document.getElementById("card-bat-discharge-a").textContent = `${batDischargeA} A`;
  document.getElementById("card-bat-net-w").textContent = `${batNetW > 0 ? "+" : ""}${batNetW.toFixed(1)} W`;
  document.getElementById("card-bat-bar").style.width = `${Math.min(100, batSoc)}%`;

  const batTag = document.getElementById("card-bat-tag");
  batTag.textContent = batStatus;
  if (batStatus === "Charging") {
    batTag.className = "card-tag tag-battery";
    document.getElementById("card-bat-net-w").style.color = "#10b981";
  } else if (batStatus === "Discharging") {
    batTag.className = "card-tag tag-load";
    document.getElementById("card-bat-net-w").style.color = "#f43f5e";
  } else {
    batTag.className = "card-tag";
    document.getElementById("card-bat-net-w").style.color = "#94a3b8";
  }

  // WAPDA / Grid Metrics
  const gridV = data.grid_voltage || 0;
  const gridFreq = data.grid_frequency || 0;
  const gridW = data.grid_power_w || 0;
  const gridAvailable = data.grid_available;

  document.getElementById("flow-val-grid").innerHTML = `${Math.round(gridW)} <span class="unit">W</span>`;
  document.getElementById("flow-sub-grid").textContent = `${gridV.toFixed(1)} V • ${gridFreq.toFixed(1)} Hz`;
  document.getElementById("card-grid-v").textContent = gridV.toFixed(1);
  document.getElementById("card-grid-freq").textContent = `${gridFreq.toFixed(1)} Hz`;
  document.getElementById("card-grid-w").textContent = `${Math.round(gridW)} W`;
  document.getElementById("card-grid-mode").textContent = data.mode_code === "L" ? "Passthrough" : "Off-grid";

  const gridTag = document.getElementById("card-grid-tag");
  if (gridAvailable) {
    gridTag.textContent = "Grid Online";
    gridTag.className = "card-tag tag-grid";
  } else {
    gridTag.textContent = "Power Outage";
    gridTag.className = "card-tag tag-load";
  }

  // Inverter Hardware State
  const temp = data.inverter_temperature || 0;
  const busV = data.bus_voltage || 0;
  document.getElementById("inverter-temp-badge").textContent = `${temp}°C`;
  document.getElementById("inverter-bus-badge").textContent = `${busV}V Bus`;
  document.getElementById("card-temp").textContent = `${temp} °C`;

  // Update SVG Flow Physics
  updateFlowPipes({
    pvPower,
    gridW,
    gridAvailable,
    loadW,
    batChargeA,
    batDischargeA,
  });

  // Append point to active charts if current range is 1h
  if (currentRange === "1h" && charts.power) {
    appendLivePointToCharts(data);
  }
}

// -----------------------------------------------------------------------------
// SVG Power Flow Physics
// -----------------------------------------------------------------------------
function updateFlowPipes({ pvPower, gridW, gridAvailable, loadW, batChargeA, batDischargeA }) {
  const pipeSolar = document.getElementById("flow-solar");
  const pipeGrid = document.getElementById("flow-grid");
  const pipeLoad = document.getElementById("flow-load");
  const pipeBat = document.getElementById("flow-battery");

  // Solar Pipe
  if (pvPower > 25) {
    pipeSolar.classList.remove("flow-paused");
    const speed = Math.max(0.6, 2.5 - (pvPower / 2500)).toFixed(2);
    pipeSolar.style.animationDuration = `${speed}s`;
  } else {
    pipeSolar.classList.add("flow-paused");
  }

  // Grid Pipe
  if (gridAvailable && gridW > 25) {
    pipeGrid.classList.remove("flow-paused");
    const speed = Math.max(0.6, 2.5 - (gridW / 2500)).toFixed(2);
    pipeGrid.style.animationDuration = `${speed}s`;
  } else {
    pipeGrid.classList.add("flow-paused");
  }

  // Load Pipe
  if (loadW > 25) {
    pipeLoad.classList.remove("flow-paused");
    const speed = Math.max(0.6, 2.5 - (loadW / 3000)).toFixed(2);
    pipeLoad.style.animationDuration = `${speed}s`;
  } else {
    pipeLoad.classList.add("flow-paused");
  }

  // Battery Pipe (Directional!)
  if (batChargeA > 1) {
    // Charging: flows down into battery
    pipeBat.classList.remove("flow-paused");
    pipeBat.classList.remove("discharging");
    pipeBat.classList.add("charging");
    pipeBat.style.animationDuration = "1.5s";
  } else if (batDischargeA > 1) {
    // Discharging: flows up into inverter
    pipeBat.classList.remove("flow-paused");
    pipeBat.classList.remove("charging");
    pipeBat.classList.add("discharging");
    pipeBat.style.animationDuration = "1.5s";
  } else {
    pipeBat.classList.add("flow-paused");
  }
}

// -----------------------------------------------------------------------------
// Connection Status Pill
// -----------------------------------------------------------------------------
function updateConnectionStatus(connected, ip = "") {
  const dot = document.getElementById("conn-dot");
  const text = document.getElementById("conn-text");
  const pill = document.getElementById("conn-pill");
  const footerIp = document.getElementById("footer-conn-ip");

  if (connected) {
    dot.className = "status-dot pulse-active";
    text.textContent = `Local Link Active (${ip || "Connected"})`;
    pill.style.borderColor = "rgba(16, 185, 129, 0.4)";
    footerIp.textContent = `${ip || "--"} : 8899 (Active)`;
  } else {
    dot.className = "status-dot pulse-error";
    text.textContent = "Connecting to Inverter...";
    pill.style.borderColor = "rgba(244, 63, 94, 0.4)";
    footerIp.textContent = "Searching for dongle...";
  }
}

// -----------------------------------------------------------------------------
// Today's Energy Summary (kWh)
// -----------------------------------------------------------------------------
async function loadSummary() {
  try {
    const res = await fetch("/api/summary");
    if (!res.ok) return;
    const summary = await res.json();

    document.getElementById("card-pv-kwh").textContent = `${summary.today_pv_energy_kwh || 0.0} kWh`;
    const pvPeakTime = summary.peak_pv_time && summary.peak_pv_time !== "--" ? ` (${summary.peak_pv_time})` : "";
    document.getElementById("card-pv-peak").textContent = `${Math.round(summary.peak_pv_w || 0)} W${pvPeakTime}`;
    document.getElementById("card-load-kwh").textContent = `${summary.today_load_energy_kwh || 0.0} kWh`;
    const loadPeakTime = summary.peak_load_time && summary.peak_load_time !== "--" ? ` (${summary.peak_load_time})` : "";
    document.getElementById("card-load-peak").textContent = `${Math.round(summary.peak_load_w || 0)} W${loadPeakTime}`;
    document.getElementById("card-bat-charged-kwh").textContent = `${summary.today_battery_charge_kwh || 0.0} kWh`;
    document.getElementById("card-grid-kwh").textContent = `${summary.today_grid_energy_kwh || 0.0} kWh`;
    document.getElementById("footer-sample-count").textContent = `${summary.sample_count || 0} snapshots logged today`;
  } catch (err) {
    console.debug("Failed to fetch summary:", err);
  }
}

// -----------------------------------------------------------------------------
// Chart.js Setup and Historic Data
// -----------------------------------------------------------------------------
const commonChartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  interaction: {
    mode: "index",
    intersect: false,
  },
  plugins: {
    legend: {
      labels: {
        color: "#cbd5e1",
        font: { family: "'Outfit', sans-serif", size: 12 },
        boxWidth: 12,
        usePointStyle: true,
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
      ticks: {
        color: "#64748b",
        font: { family: "'JetBrains Mono', monospace", size: 10 },
        maxTicksLimit: 8,
      },
    },
    y: {
      beginAtZero: true,
      min: 0,
      grid: { color: "rgba(255, 255, 255, 0.06)" },
      ticks: {
        color: "#94a3b8",
        font: { family: "'JetBrains Mono', monospace", size: 11 },
      },
    },
  },
};

function initCharts() {
  // Chart 1: Power (Solar vs Load vs Grid)
  const ctxPower = document.getElementById("chart-power").getContext("2d");
  charts.power = new Chart(ctxPower, {
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
        },
        {
          label: "Home Load (W)",
          data: [],
          borderColor: "#f43f5e",
          backgroundColor: "transparent",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
        },
        {
          label: "Utility Grid (W)",
          data: [],
          borderColor: "#6366f1",
          backgroundColor: "transparent",
          borderWidth: 1.5,
          pointRadius: 0,
          borderDash: [4, 4],
          tension: 0.3,
        },
      ],
    },
    options: {
      ...commonChartOptions,
      scales: {
        ...commonChartOptions.scales,
        y: {
          ...commonChartOptions.scales.y,
          beginAtZero: true,
          min: 0,
          suggestedMax: 2000,
          title: { display: true, text: "Power (W)", color: "#f59e0b" },
        },
      },
    },
  });

  // Chart 2: Battery Current
  const ctxBatCurr = document.getElementById("chart-battery-current").getContext("2d");
  charts.batteryCurrent = new Chart(ctxBatCurr, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Charge Current (A)",
          data: [],
          borderColor: "#10b981",
          backgroundColor: "rgba(16, 185, 129, 0.12)",
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.3,
        },
        {
          label: "Discharge Current (A)",
          data: [],
          borderColor: "#ef4444",
          backgroundColor: "rgba(239, 68, 68, 0.12)",
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.3,
        },
      ],
    },
    options: {
      ...commonChartOptions,
      scales: {
        ...commonChartOptions.scales,
        y: {
          ...commonChartOptions.scales.y,
          beginAtZero: true,
          min: 0,
          suggestedMax: 30,
          title: { display: true, text: "Current (A)", color: "#10b981" },
        },
      },
    },
  });

  // Chart 3: Battery Voltage & SOC
  const ctxBatVolt = document.getElementById("chart-battery-voltage").getContext("2d");
  charts.batteryVoltage = new Chart(ctxBatVolt, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Battery Voltage (V)",
          data: [],
          borderColor: "#06b6d4",
          backgroundColor: "transparent",
          borderWidth: 2,
          pointRadius: 0,
          yAxisID: "y",
          tension: 0.3,
        },
        {
          label: "State of Charge (%)",
          data: [],
          borderColor: "#fbbf24",
          backgroundColor: "transparent",
          borderWidth: 1.5,
          pointRadius: 0,
          yAxisID: "y1",
          tension: 0.3,
        },
      ],
    },
    options: {
      ...commonChartOptions,
      scales: {
        ...commonChartOptions.scales,
        y: {
          ...commonChartOptions.scales.y,
          beginAtZero: true,
          min: 0,
          suggestedMax: 32,
          title: { display: true, text: "Battery Voltage (V)", color: "#06b6d4" },
        },
        y1: {
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#fbbf24" },
          title: { display: true, text: "SOC (%)", color: "#fbbf24" },
          min: 0,
          max: 100,
        },
      },
    },
  });

  // Chart 4: Grid Voltage & Inverter Temp
  const ctxGridTemp = document.getElementById("chart-grid-temp").getContext("2d");
  charts.gridTemp = new Chart(ctxGridTemp, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Grid Voltage (V)",
          data: [],
          borderColor: "#818cf8",
          backgroundColor: "transparent",
          borderWidth: 2,
          pointRadius: 0,
          yAxisID: "y",
          tension: 0.3,
        },
        {
          label: "Heat Sink Temp (°C)",
          data: [],
          borderColor: "#f97316",
          backgroundColor: "transparent",
          borderWidth: 1.5,
          pointRadius: 0,
          yAxisID: "y1",
          tension: 0.3,
        },
      ],
    },
    options: {
      ...commonChartOptions,
      scales: {
        ...commonChartOptions.scales,
        y: {
          ...commonChartOptions.scales.y,
          beginAtZero: true,
          min: 0,
          suggestedMax: 260,
          title: { display: true, text: "Grid Voltage (V)", color: "#818cf8" },
        },
        y1: {
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#f97316" },
          title: { display: true, text: "Heat Sink Temp (°C)", color: "#f97316" },
          beginAtZero: true,
          min: 0,
          suggestedMax: 70,
        },
      },
    },
  });
}

// -----------------------------------------------------------------------------
// Load Historical Records from API
// -----------------------------------------------------------------------------
async function loadHistory(range) {
  try {
    const res = await fetch(`/api/history?range=${range}`);
    if (!res.ok) return;
    const points = await res.json();

    const labels = points.map((p) => {
      const d = new Date(p.time);
      return range === "7d" || range === "30d"
        ? `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`
        : `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
    });

    // Chart 1: Power
    charts.power.data.labels = labels;
    charts.power.data.datasets[0].data = points.map((p) => p.pv_power);
    charts.power.data.datasets[1].data = points.map((p) => p.load_w);
    charts.power.data.datasets[2].data = points.map((p) => p.grid_w);
    charts.power.update();

    // Chart 2: Battery Current
    charts.batteryCurrent.data.labels = labels;
    charts.batteryCurrent.data.datasets[0].data = points.map((p) => p.bat_charge_a);
    charts.batteryCurrent.data.datasets[1].data = points.map((p) => p.bat_discharge_a);
    charts.batteryCurrent.update();

    // Chart 3: Battery Voltage & SOC
    charts.batteryVoltage.data.labels = labels;
    charts.batteryVoltage.data.datasets[0].data = points.map((p) => p.bat_v);
    charts.batteryVoltage.data.datasets[1].data = points.map((p) => p.bat_soc);
    charts.batteryVoltage.update();

    // Chart 4: Grid & Temp
    charts.gridTemp.data.labels = labels;
    charts.gridTemp.data.datasets[0].data = points.map((p) => p.grid_v);
    charts.gridTemp.data.datasets[1].data = points.map((p) => p.inverter_temp);
    charts.gridTemp.update();
  } catch (err) {
    console.error("Error loading history:", err);
  }
}

// -----------------------------------------------------------------------------
// Live Point Stream to Chart
// -----------------------------------------------------------------------------
function appendLivePointToCharts(data) {
  const now = new Date();
  const timeLabel = `${now.getHours()}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`;
  const maxPoints = 300;

  function pushAndTrim(chart, datasetIdx, val) {
    chart.data.datasets[datasetIdx].data.push(val);
    if (chart.data.datasets[datasetIdx].data.length > maxPoints) {
      chart.data.datasets[datasetIdx].data.shift();
    }
  }

  // Push label
  charts.power.data.labels.push(timeLabel);
  if (charts.power.data.labels.length > maxPoints) charts.power.data.labels.shift();

  pushAndTrim(charts.power, 0, data.pv_power || 0);
  pushAndTrim(charts.power, 1, data.output_active_power || 0);
  pushAndTrim(charts.power, 2, data.grid_power_w || 0);
  charts.power.update("none");

  // Battery current
  charts.batteryCurrent.data.labels.push(timeLabel);
  if (charts.batteryCurrent.data.labels.length > maxPoints) charts.batteryCurrent.data.labels.shift();
  pushAndTrim(charts.batteryCurrent, 0, data.battery_charge_current || 0);
  pushAndTrim(charts.batteryCurrent, 1, data.battery_discharge_current || 0);
  charts.batteryCurrent.update("none");

  // Battery voltage & SOC
  charts.batteryVoltage.data.labels.push(timeLabel);
  if (charts.batteryVoltage.data.labels.length > maxPoints) charts.batteryVoltage.data.labels.shift();
  pushAndTrim(charts.batteryVoltage, 0, data.battery_voltage || 0);
  pushAndTrim(charts.batteryVoltage, 1, data.battery_soc || 0);
  charts.batteryVoltage.update("none");

  // Grid & Temp
  charts.gridTemp.data.labels.push(timeLabel);
  if (charts.gridTemp.data.labels.length > maxPoints) charts.gridTemp.data.labels.shift();
  pushAndTrim(charts.gridTemp, 0, data.grid_voltage || 0);
  pushAndTrim(charts.gridTemp, 1, data.inverter_temperature || 0);
  charts.gridTemp.update("none");
}

// -----------------------------------------------------------------------------
// Home Assistant & MQTT Modal Controller
// -----------------------------------------------------------------------------
function initMqttModal() {
  const modal = document.getElementById("mqtt-modal");
  const openBtn = document.getElementById("btn-open-mqtt");
  const closeBtn = document.getElementById("btn-close-mqtt");
  const cancelBtn = document.getElementById("btn-cancel-mqtt");
  const saveBtn = document.getElementById("btn-save-mqtt");

  const enabledInput = document.getElementById("mqtt-enabled");
  const brokerInput = document.getElementById("mqtt-broker");
  const portInput = document.getElementById("mqtt-port");
  const usernameInput = document.getElementById("mqtt-username");
  const passwordInput = document.getElementById("mqtt-password");
  const topicInput = document.getElementById("mqtt-topic");

  const statusBadge = document.getElementById("modal-mqtt-status");
  const headerDot = document.getElementById("header-mqtt-dot");

  async function refreshMqttStatus() {
    try {
      const res = await fetch("/api/mqtt/status");
      if (!res.ok) return;
      const status = await res.json();

      enabledInput.checked = Boolean(status.enabled);
      brokerInput.value = status.broker || "";
      portInput.value = status.port || 1883;
      usernameInput.value = status.username || "";
      topicInput.value = status.topic_prefix || "solar/knox";

      if (status.connected) {
        statusBadge.textContent = "CONNECTED";
        statusBadge.style.color = "#10b981";
        headerDot.className = "mqtt-status-dot connected";
      } else if (status.enabled) {
        statusBadge.textContent = "CONNECTING...";
        statusBadge.style.color = "#f59e0b";
        headerDot.className = "mqtt-status-dot";
      } else {
        statusBadge.textContent = "DISABLED";
        statusBadge.style.color = "#94a3b8";
        headerDot.className = "mqtt-status-dot";
      }
    } catch (err) {
      console.debug("Failed to fetch MQTT status:", err);
    }
  }

  openBtn.addEventListener("click", () => {
    modal.style.display = "flex";
    refreshMqttStatus();
  });

  function closeModal() {
    modal.style.display = "none";
  }

  closeBtn.addEventListener("click", closeModal);
  cancelBtn.addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = "Connecting...";

    const payload = {
      enabled: enabledInput.checked,
      broker: brokerInput.value.trim(),
      port: parseInt(portInput.value, 10) || 1883,
      username: usernameInput.value.trim(),
      password: passwordInput.value,
      topic_prefix: topicInput.value.trim() || "solar/knox",
    };

    try {
      const res = await fetch("/api/mqtt/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        await refreshMqttStatus();
        closeModal();
      } else {
        alert("Failed to save MQTT configuration");
      }
    } catch (err) {
      alert(`MQTT error: ${err.message}`);
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save & Connect";
    }
  });

  // Check initial MQTT status on load
  refreshMqttStatus();
  setInterval(refreshMqttStatus, 15000);
}

// -----------------------------------------------------------------------------
// Cloud Server & Offline Gap Recovery Modal Controller
// -----------------------------------------------------------------------------
function initCloudModal() {
  const modal = document.getElementById("cloud-modal");
  const openBtn = document.getElementById("btn-open-cloud");
  const closeBtn = document.getElementById("btn-close-cloud");
  const triggerSyncBtn = document.getElementById("btn-trigger-sync");
  const saveBtn = document.getElementById("btn-save-cloud");

  const enabledInput = document.getElementById("cloud-enabled");
  const urlInput = document.getElementById("cloud-url");
  const tokenInput = document.getElementById("cloud-token");
  const authInput = document.getElementById("cloud-auth");

  const statusBadge = document.getElementById("cloud-sync-status");
  const lastGtsEl = document.getElementById("cloud-last-gts");
  const lastSyncEl = document.getElementById("cloud-last-sync");
  const headerDot = document.getElementById("header-cloud-dot");

  async function refreshCloudStatus() {
    try {
      const res = await fetch("/api/cloud/status");
      if (!res.ok) return;
      const data = await res.json();

      enabledInput.checked = Boolean(data.enabled);
      urlInput.value = data.url || "";

      if (data.last_sync_status === "up_to_date" || data.last_sync_status === "synced_gap") {
        statusBadge.textContent = data.last_sync_status.toUpperCase().replace("_", " ");
        statusBadge.style.color = "#10b981";
        if (headerDot) {
          headerDot.style.background = "#10b981";
          headerDot.style.boxShadow = "0 0 6px #10b981";
        }
      } else {
        statusBadge.textContent = (data.last_sync_status || "IDLE").toUpperCase();
        statusBadge.style.color = "#38bdf8";
        if (headerDot) {
          headerDot.style.background = "#38bdf8";
          headerDot.style.boxShadow = "0 0 6px #38bdf8";
        }
      }

      if (data.cloud_last_gts) {
        lastGtsEl.textContent = new Date(data.cloud_last_gts).toLocaleString();
      }
      if (data.last_sync_timestamp) {
        lastSyncEl.textContent = new Date(data.last_sync_timestamp).toLocaleTimeString();
      }
    } catch (err) {
      console.debug("Failed to fetch cloud status:", err);
    }
  }

  if (openBtn) {
    openBtn.addEventListener("click", () => {
      modal.style.display = "flex";
      refreshCloudStatus();
    });
  }

  function closeModal() {
    modal.style.display = "none";
  }

  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal();
    });
  }

  if (triggerSyncBtn) {
    triggerSyncBtn.addEventListener("click", async () => {
      triggerSyncBtn.disabled = true;
      triggerSyncBtn.textContent = "Syncing...";
      try {
        const res = await fetch("/api/cloud/sync", { method: "POST" });
        const data = await res.json();
        if (data.status === "ok") {
          await refreshCloudStatus();
          loadSummary();
          loadHistory(currentRange);
          alert(`Cloud sync complete! Backfilled ${data.backfilled} snapshots (gap: ${data.gap_seconds}s)`);
        } else {
          alert(`Sync response: ${data.message || data.status}`);
        }
      } catch (err) {
        alert(`Sync failed: ${err.message}`);
      } finally {
        triggerSyncBtn.disabled = false;
        triggerSyncBtn.textContent = "🔄 Sync Now";
      }
    });
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving...";

      const payload = {
        enabled: enabledInput.checked,
        url: urlInput.value.trim(),
        token: tokenInput.value.trim(),
        auth: authInput.value.trim(),
      };

      try {
        const res = await fetch("/api/cloud/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          await refreshCloudStatus();
          closeModal();
        } else {
          alert("Failed to save cloud config");
        }
      } catch (err) {
        alert(`Error saving cloud config: ${err.message}`);
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = "Save Settings";
      }
    });
  }

  refreshCloudStatus();
  setInterval(refreshCloudStatus, 30000);
}


