const OPEN_ALARM_COUNT_ENTITY = "sensor.open_alarm_unacknowledged";
const OPEN_ALARM_ATTENTION_ENTITY = "binary_sensor.open_alarm_attention";
const OPEN_ALARM_INDICATOR_ID = "open-alarm-corner-indicator";
const STARTUP_GRACE_MS = 5000;
const POLL_INTERVAL_MS = 1000;
const MODULE_LOADED_AT = Date.now();

let latestHass = null;

function homeAssistant() {
  const root = document.querySelector("home-assistant");
  return root?.hass ?? root?.shadowRoot?.querySelector("home-assistant-main")?.hass ?? null;
}

function isOpenAlarmPanel(panel) {
  if (!panel || panel.component_name !== "app") return false;
  const addon = String(panel.config?.addon ?? "");
  const title = String(panel.title ?? "");
  return addon === "open_alarm" || addon.endsWith("_open_alarm") || title === "Open Alarm";
}

function resolveOpenAlarmPanelPath(hass) {
  const panels = hass?.panels;
  if (!panels || typeof panels !== "object") return null;

  for (const panel of Object.values(panels)) {
    if (isOpenAlarmPanel(panel) && typeof panel.url_path === "string" && panel.url_path) {
      return `/${panel.url_path}`;
    }
  }
  return null;
}

function finnish(hass) {
  const language = hass?.locale?.language ?? hass?.language ?? "";
  return String(language).toLowerCase().startsWith("fi");
}

function translatedTitle(hass, mode, count = 0) {
  const fi = finnish(hass);
  if (mode === "missing") {
    return fi
      ? "Open Alarm -tilaa ei löydy. Tarkista App ja frontend-moduuli."
      : "Open Alarm state is unavailable. Check the App and frontend module.";
  }
  if (mode === "unavailable") {
    return fi ? "Open Alarm ei saatavilla" : "Open Alarm unavailable";
  }
  return fi
    ? `Kuittaamattomia hälytyksiä: ${count}`
    : `Unacknowledged alarms: ${count}`;
}

function indicatorModel(hass, showMissing = false) {
  if (!hass) {
    return showMissing
      ? { visible: true, mode: "missing", text: "⚠ ?", count: 0 }
      : { visible: false, mode: "loading", text: "", count: 0 };
  }

  const countState = hass.states?.[OPEN_ALARM_COUNT_ENTITY];
  const attentionState = hass.states?.[OPEN_ALARM_ATTENTION_ENTITY];

  if (!countState && !attentionState) {
    return showMissing
      ? { visible: true, mode: "missing", text: "⚠ ?", count: 0 }
      : { visible: false, mode: "loading", text: "", count: 0 };
  }

  const unavailable =
    countState?.state === "unavailable" || attentionState?.state === "unavailable";
  if (unavailable) {
    return { visible: true, mode: "unavailable", text: "⚠ ?", count: 0 };
  }

  const count = Number.parseInt(countState?.state ?? "0", 10);
  const normalizedCount = Number.isFinite(count) && count > 0 ? count : 0;
  const needsAttention = attentionState?.state === "on" || normalizedCount > 0;
  if (!needsAttention) {
    return { visible: false, mode: "normal", text: "", count: 0 };
  }

  return {
    visible: true,
    mode: "alarm",
    text: normalizedCount > 0 ? `⚠ ${normalizedCount}` : "⚠",
    count: normalizedCount || 1,
  };
}

function indicator() {
  let button = document.getElementById(OPEN_ALARM_INDICATOR_ID);
  if (button) return button;

  button = document.createElement("button");
  button.id = OPEN_ALARM_INDICATOR_ID;
  button.type = "button";
  button.style.cssText = `
    position: fixed;
    top: calc(env(safe-area-inset-top, 0px) + 10px);
    right: calc(env(safe-area-inset-right, 0px) + 10px);
    z-index: 2147483647;
    display: none;
    align-items: center;
    justify-content: center;
    min-width: 46px;
    height: 38px;
    padding: 0 12px;
    border: 0;
    border-radius: 19px;
    color: white;
    font: 700 16px/1 sans-serif;
    cursor: pointer;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
  `;
  button.addEventListener("click", () => {
    const path = resolveOpenAlarmPanelPath(latestHass ?? homeAssistant());
    if (path) window.location.assign(path);
  });
  document.body.appendChild(button);
  return button;
}

function updateIndicator() {
  const hass = homeAssistant();
  if (hass) latestHass = hass;

  const button = indicator();
  const showMissing = Date.now() - MODULE_LOADED_AT >= STARTUP_GRACE_MS;
  const model = indicatorModel(hass, showMissing);

  if (!model.visible) {
    button.style.display = "none";
    return;
  }

  button.textContent = model.text;
  button.title = translatedTitle(hass, model.mode, model.count);
  button.setAttribute("aria-label", button.title);
  button.style.background =
    model.mode === "alarm"
      ? "var(--error-color, #db4437)"
      : "var(--warning-color, #f59e0b)";
  button.style.display = "inline-flex";
}

function startIndicator() {
  updateIndicator();
  window.setInterval(updateIndicator, POLL_INTERVAL_MS);
  window.addEventListener("location-changed", updateIndicator);
  window.addEventListener("pageshow", updateIndicator);
}

// Kept deliberately tiny so CI can exercise the behavior without a browser framework.
globalThis.__openAlarmIndicatorTest = {
  indicatorModel,
  resolveOpenAlarmPanelPath,
};

if (typeof window !== "undefined" && typeof document !== "undefined") {
  if (window.customElements?.whenDefined) {
    window.customElements.whenDefined("home-assistant").then(startIndicator);
  } else {
    startIndicator();
  }
}
