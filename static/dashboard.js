(() => {
  "use strict";

  const root = document.getElementById("dashboard");
  if (!root) return;

  const config = window.DASHBOARD_CONFIG || {};
  const timezone = config.timezone || root.dataset.timezone || "UTC";
  const calendarPanel = document.getElementById("calendar-panel");
  const weatherPanel = document.getElementById("weather-panel");
  const offlineIndicator = document.getElementById("offline-indicator");
  const clockElement = document.getElementById("clock");
  const dateElement = document.getElementById("date-label");
  const backgroundElement = document.querySelector(".background-image");
  const iconPath = (key) => `static/icons/${encodeURIComponent(key)}.svg`;

  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };

  const paletteFor = (data, name) => (data.palette && data.palette[name]) || {
    bg: "#455A64",
    fg: "#FFFFFF",
    tentative_bg: "#CFD8DC",
    tentative_fg: "#1F292D",
  };

  function fitRenderedWeeks() {
    const weeks = [...calendarPanel.querySelectorAll(".week-grid")];
    if (!weeks.length) return;

    weeks.forEach((week) => week.classList.remove("is-hidden"));
    const styles = getComputedStyle(calendarPanel);
    const availableHeight = calendarPanel.clientHeight
      - parseFloat(styles.paddingTop)
      - parseFloat(styles.paddingBottom)
      - (calendarPanel.querySelector(".weekday-row")?.getBoundingClientRect().height || 0);
    let usedHeight = 0;
    let visibleCount = 0;

    for (const week of weeks) {
      const actualHeight = week.getBoundingClientRect().height;
      if (visibleCount > 0 && usedHeight + actualHeight > availableHeight) break;
      usedHeight += actualHeight;
      visibleCount += 1;
    }

    weeks.slice(visibleCount).forEach((week) => week.classList.add("is-hidden"));
  }

  function updateClock() {
    const now = new Date();
    const timeOptions = {
      timeZone: timezone,
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    };
    clockElement.textContent = new Intl.DateTimeFormat(undefined, timeOptions).format(now);
    dateElement.textContent = new Intl.DateTimeFormat(undefined, {
      timeZone: timezone,
      weekday: "long",
      month: "long",
      day: "numeric",
      year: "numeric",
    }).format(now);
    applyNightTheme(window.currentDashboardData);
  }

  function applyNightTheme(data) {
    const theme = data && data.theme;
    if (!theme || !theme.night_enabled) {
      document.body.classList.remove("night-theme");
      return;
    }
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date());
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    const current = Number(values.hour) * 60 + Number(values.minute);
    const toMinutes = (value) => {
      const [hour, minute] = String(value || "00:00").split(":").map(Number);
      return hour * 60 + minute;
    };
    const start = toMinutes(theme.night_start);
    const end = toMinutes(theme.night_end);
    const night = start > end
      ? current >= start || current < end
      : current >= start && current < end;
    document.body.classList.toggle("night-theme", night);
  }

  function renderWeather(data) {
    weatherPanel.replaceChildren();
    const weather = data.weather;
    if (!weather) {
      weatherPanel.append(element("div", "weather-empty", "Weather unavailable"));
      return;
    }

    const currentRow = element("div", "weather-current");
    const icon = element("img", "weather-icon current-icon");
    icon.src = iconPath(weather.current.icon_key);
    icon.alt = "";
    const reading = element("div", "current-reading");
    const temperature = element("span", "current-temperature", `${Math.round(weather.current.temp)}°`);
    const condition = element("span", "current-condition", weather.current.condition);
    reading.append(temperature, condition);
    const summary = element("div", "today-summary");
    const hi = weather.today.hi === null ? "--" : weather.today.hi;
    const lo = weather.today.lo === null ? "--" : weather.today.lo;
    const precip = weather.today.precip_chance === null ? "--" : weather.today.precip_chance;
    summary.append(
      element("span", "", `Today ${hi}° / ${lo}°`),
      element("span", "", `${precip}% precip`),
    );
    currentRow.append(icon, reading);
    weatherPanel.append(currentRow, summary);

  }

  function renderCalendar(data) {
    calendarPanel.querySelectorAll(".week-grid, .calendar-empty").forEach((node) => node.remove());
    const calendar = data.calendar;
    if (!calendar || !Array.isArray(calendar.weeks) || calendar.weeks.length === 0) {
      calendarPanel.prepend(element("div", "calendar-empty", "No calendar events"));
      return;
    }

    const staleSources = new Map(
      ((data.status && data.status.sources) || [])
        .filter((source) => source.kind === "calendar" && source.state !== "ok")
        .map((source) => [String(source.id), String(source.name)]),
    );
    const statusPanel = document.getElementById("calendar-status");
    statusPanel.replaceChildren();
    staleSources.forEach((sourceName, sourceId) => {
      const chip = element("span", "stale-source");
      chip.dataset.source = sourceId;
      chip.title = `${sourceName} data may be stale`;
      chip.append(element("span", "stale-dot"));
      chip.append(document.createTextNode(sourceName));
      statusPanel.append(chip);
    });

    const forecasts = new Map(
      ((data.weather && data.weather.daily) || []).map((item) => [String(item.date), item]),
    );
    calendar.weeks.forEach((week) => {
      const weekGrid = element("section", "week-grid");
      weekGrid.style.setProperty("--lanes", Number(week.lanes) || 0);
      weekGrid.setAttribute("aria-label", `Week beginning ${week.start}`);

      const backgrounds = element("div", "day-backgrounds");
      backgrounds.setAttribute("aria-hidden", "true");
      week.days.forEach((day, index) => {
        const background = element("div", "day-background");
        background.style.gridColumn = String(index + 1);
        background.style.gridRow = "1 / -1";
        backgrounds.append(background);
      });
      weekGrid.append(backgrounds);

      week.days.forEach((day, index) => {
        const dateCell = element("div", `date-cell${day.is_today ? " is-today" : ""}`);
        dateCell.style.gridColumn = String(index + 1);
        dateCell.style.gridRow = "1";
        dateCell.append(element("span", "date-number", String(day.date).slice(-2)));
        if (day.is_first_of_month) {
          const month = new Intl.DateTimeFormat("en-US", {
            month: "short",
            timeZone: "UTC",
          }).format(new Date(`${String(day.date)}T12:00:00Z`));
          dateCell.append(element("span", "month-marker", month));
        }
        const forecast = forecasts.get(String(day.date));
        if (forecast && !day.is_today) {
          const forecastElement = element("span", "day-forecast");
          forecastElement.setAttribute(
            "aria-label",
            `Forecast ${forecast.hi === null ? "unknown" : forecast.hi} high, ${forecast.lo === null ? "unknown" : forecast.lo} low`,
          );
          const forecastIcon = element("img");
          forecastIcon.src = iconPath(forecast.icon_key);
          forecastIcon.alt = "";
          const values = element("span", "day-forecast-values");
          values.append(
            element(
              "span",
              "",
              `${forecast.hi === null ? "--" : forecast.hi}°/${forecast.lo === null ? "--" : forecast.lo}°`,
            ),
            element(
              "span",
              "",
              `${forecast.precip_chance === null ? "--" : forecast.precip_chance}% precip`,
            ),
          );
          forecastElement.append(forecastIcon, values);
          dateCell.append(forecastElement);
        }
        weekGrid.append(dateCell);
      });

      (week.bars || []).forEach((bar) => {
        const palette = paletteFor(data, bar.calendar);
        const barElement = element(
          "div",
          `event-bar status-${bar.status}${bar.continues_left ? " continues-left" : ""}${bar.continues_right ? " continues-right" : ""}`,
        );
        barElement.dataset.calendar = bar.calendar;
        barElement.style.setProperty("--bar-bg", palette.bg);
        barElement.style.setProperty("--bar-fg", palette.fg);
        barElement.style.gridColumn = `${bar.col_start + 1} / span ${bar.col_span}`;
        barElement.style.gridRow = String(bar.lane + 2);
        barElement.append(element("span", "bar-title", bar.title));
        weekGrid.append(barElement);
      });

      week.days.forEach((day, index) => {
        const cell = element("div", "chip-cell");
        cell.style.gridColumn = String(index + 1);
        cell.style.gridRow = String(week.lanes + 2);
        (day.chips || []).forEach((chip) => {
          const palette = paletteFor(data, chip.calendar);
          const tentative = chip.status === "tentative";
          const chipElement = element("div", `event-chip status-${chip.status}`);
          chipElement.dataset.calendar = chip.calendar;
          chipElement.style.setProperty("--chip-bg", tentative ? palette.tentative_bg : palette.bg);
          chipElement.style.setProperty("--chip-fg", tentative ? palette.tentative_fg : palette.fg);
          chipElement.style.setProperty("--tentative-border", palette.bg);
          if (chip.time_label) chipElement.append(element("span", "chip-time", chip.time_label));
          chipElement.append(element("span", "chip-title", chip.title));
          if (chip.location) chipElement.append(element("span", "chip-location", chip.location));
          cell.append(chipElement);
        });
        if (day.more_count) cell.append(element("div", "more-count", `+${day.more_count} more`));
        weekGrid.append(cell);
      });
      calendarPanel.insertBefore(weekGrid, statusPanel);
    });
    fitRenderedWeeks();
  }

  function updateDashboard(data) {
    if (!data || typeof data !== "object") return;
    window.currentDashboardData = data;
    renderWeather(data);
    renderCalendar(data);
    offlineIndicator.classList.toggle("is-hidden", !(data.status && data.status.offline));
    applyNightTheme(data);
  }

  async function pollData() {
    try {
      const response = await fetch(`data.json?ts=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`data.json returned ${response.status}`);
      updateDashboard(await response.json());
    } catch (_error) {
      offlineIndicator.classList.remove("is-hidden");
    }
  }

  function rotateBackground() {
    const backgrounds = config.backgrounds || [];
    if (!backgroundElement || backgrounds.length === 0) return;
    let index = 0;
    const change = () => {
      backgroundElement.style.backgroundImage = `url("${backgrounds[index]}")`;
      index = (index + 1) % backgrounds.length;
    };
    change();
    window.setInterval(change, Math.max(1, Number(config.backgroundChangeMinutes) || 60) * 60000);
  }

  function scheduleClock() {
    updateClock();
    window.setInterval(updateClock, 1000);
  }

  scheduleClock();
  rotateBackground();
  pollData();
  window.setInterval(pollData, 60000);
  window.addEventListener("resize", fitRenderedWeeks);
  if ("ResizeObserver" in window) {
    new ResizeObserver(fitRenderedWeeks).observe(calendarPanel);
  }
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(fitRenderedWeeks);
  }
})();
