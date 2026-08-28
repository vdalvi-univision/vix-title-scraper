(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const MAX_PAGES = 4;
  const MAX_COMPARE_RUNS = 2;
  const DEFAULT_PAGES = [
    { url_path: "/ondemandplus", web_link: "https://vix.com/es-mx/ondemandplus" },
    { url_path: "/ondemandpluswc", web_link: "https://vix.com/es-mx/ondemandpluswc" },
  ];
  const DEVICE_FALLBACK = [
    { id: "fire_tablet", label: "Fire Tablet", platform: "firetablet", device_type: "tablet" },
    { id: "android", label: "Android", platform: "android", device_type: "mobile" },
    { id: "android_tv", label: "Android TV", platform: "androidtv", device_type: "smarttv" },
    { id: "comcast_tv", label: "Comcast TV", platform: "comcasttv", device_type: "smarttv" },
    { id: "fire_tv", label: "Fire TV", platform: "firetv", device_type: "smarttv" },
    { id: "ios", label: "iOS", platform: "ios", device_type: "mobile" },
    { id: "lg_tv", label: "LG TV", platform: "lgtv", device_type: "smarttv" },
    { id: "roku", label: "Roku", platform: "roku", device_type: "smarttv" },
    { id: "samsung_galaxy", label: "Samsung Galaxy", platform: "samsung_galaxy", device_type: "mobile" },
    { id: "samsung_tv", label: "Samsung TV", platform: "samsungtv", device_type: "smarttv" },
    { id: "tigo_stb", label: "Tigo STB", platform: "web", device_type: "smarttv" },
    { id: "tvos", label: "tvOS", platform: "tvos", device_type: "smarttv" },
    { id: "vega", label: "Vega", platform: "web", device_type: "smarttv" },
    { id: "vidaa_tv", label: "Vidaa TV", platform: "vidaatv", device_type: "smarttv" },
    { id: "vizio_tv", label: "Vizio TV", platform: "viziotv", device_type: "smarttv" },
    { id: "web", label: "Web", platform: "web", device_type: "desktop" },
    { id: "web_tv", label: "Web TV", platform: "web", device_type: "smarttv" },
  ];
  const PLATFORM_OPTIONS = [
    "android",
    "androidtv",
    "firetv",
    "firetablet",
    "roku",
    "ios",
    "tvos",
    "web",
    "samsungtv",
    "lgtv",
    "viziotv",
    "vidaatv",
    "samsung_galaxy",
    "comcasttv",
  ];
  const DEVICE_TYPE_OPTIONS = ["mobile", "tablet", "smarttv", "desktop"];
  const PLATFORM_ALIASES = {
    lg: "lgtv",
    samsung: "samsung_galaxy",
    vizio: "viziotv",
    vidaa: "vidaatv",
    comcast: "comcasttv",
    tigo: "web",
    vega: "web",
    webtv: "web",
  };
  const DEVICE_TYPE_ALIASES = { tv: "smarttv", stb: "smarttv" };
  const DEVICE_FIELDS_KEY = "vixDeviceFields";
  const ICON_TRASH =
    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/></svg>';

  let selectedRunId = null;
  let expandedRunId = null;
  let historyRuns = [];
  let pollTimer = null;
  let pageRows = DEFAULT_PAGES.map(function (p) {
    return { url_path: p.url_path, web_link: p.web_link };
  });
  let selectedPages = [];
  let viewMode = "search";
  let platformOptions = PLATFORM_OPTIONS.slice();
  let deviceTypeOptions = DEVICE_TYPE_OPTIONS.slice();

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function setStatus(el, msg, kind) {
    el.className = "status-box" + (kind ? " " + kind : "");
    el.textContent = msg || "";
  }

  function suggestLink(path) {
    var p = (path || "").trim();
    if (!p) return "";
    if (!p.startsWith("/")) p = "/" + p;
    return "https://vix.com/es-mx" + p;
  }

  function gatherPages() {
    return pageRows
      .map(function (row) {
        var path = (row.url_path || "").trim();
        if (!path) return null;
        if (!path.startsWith("/")) path = "/" + path;
        return {
          url_path: path,
          web_link: (row.web_link || "").trim() || suggestLink(path),
        };
      })
      .filter(Boolean);
  }

  function firstEmptyPageIndex() {
    for (var i = 0; i < pageRows.length; i++) {
      if (!(pageRows[i].url_path || "").trim()) return i;
    }
    return -1;
  }

  function canonicalPlatform(value) {
    var v = String(value || "").trim();
    if (platformOptions.indexOf(v) !== -1) return v;
    if (PLATFORM_ALIASES[v]) return PLATFORM_ALIASES[v];
    return "";
  }

  function canonicalDeviceType(value) {
    var v = String(value || "").trim();
    if (deviceTypeOptions.indexOf(v) !== -1) return v;
    if (DEVICE_TYPE_ALIASES[v]) return DEVICE_TYPE_ALIASES[v];
    return "";
  }

  function fillSelect(selectEl, options, selected) {
    selectEl.innerHTML = options
      .map(function (opt) {
        var value = typeof opt === "string" ? opt : opt.value;
        var label = typeof opt === "string" ? opt : opt.label;
        return (
          '<option value="' +
          escapeHtml(value) +
          '"' +
          (value === selected ? " selected" : "") +
          ">" +
          escapeHtml(label) +
          "</option>"
        );
      })
      .join("");
  }

  function stripBearer(value) {
    return String(value || "")
      .trim()
      .replace(/^Bearer\s+/i, "");
  }

  function deviceIdFromPlatform(platform) {
    var plat = canonicalPlatform(platform) || String(platform || "").trim() || "web";
    var exact = DEVICE_FALLBACK.find(function (d) {
      return d.id === plat;
    });
    if (exact) return exact.id;
    return plat;
  }

  function persistDeviceFieldState() {
    try {
      var platform = (($("platformSelect") && $("platformSelect").value) || "").trim();
      var deviceType = (($("deviceTypeSelect") && $("deviceTypeSelect").value) || "").trim();
      localStorage.setItem(
        DEVICE_FIELDS_KEY,
        JSON.stringify({ platform: platform, device_type: deviceType })
      );
    } catch (err) {
      /* ignore */
    }
  }

  function loadStoredDeviceFields() {
    try {
      var raw = localStorage.getItem(DEVICE_FIELDS_KEY);
      if (!raw) return { platform: "web", device_type: "desktop" };
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") {
        return { platform: "web", device_type: "desktop" };
      }
      if (parsed.platform || parsed.device_type) {
        return {
          platform: canonicalPlatform(parsed.platform) || parsed.platform || "web",
          device_type: canonicalDeviceType(parsed.device_type) || parsed.device_type || "desktop",
        };
      }
      var first = null;
      Object.keys(parsed).some(function (key) {
        var block = parsed[key];
        if (block && typeof block === "object") {
          first = block;
          return true;
        }
        return false;
      });
      return {
        platform: canonicalPlatform(first && first.platform) || (first && first.platform) || "web",
        device_type:
          canonicalDeviceType(first && first.device_type) || (first && first.device_type) || "desktop",
      };
    } catch (err) {
      return { platform: "web", device_type: "desktop" };
    }
  }

  function renderDeviceControls(options) {
    if (options && options.platform_options && options.platform_options.length) {
      platformOptions = options.platform_options.slice();
    }
    if (options && options.device_type_options && options.device_type_options.length) {
      deviceTypeOptions = options.device_type_options.slice();
    }
    var stored = loadStoredDeviceFields();
    var platform = stored.platform || "web";
    var deviceType = stored.device_type || "desktop";
    if (platformOptions.indexOf(platform) === -1) platform = platformOptions[0] || "web";
    if (deviceTypeOptions.indexOf(deviceType) === -1) deviceType = deviceTypeOptions[0] || "desktop";
    fillSelect($("platformSelect"), platformOptions, platform);
    fillSelect($("deviceTypeSelect"), deviceTypeOptions, deviceType);
  }

  function gatherCreds() {
    var pages = gatherPages();
    var platform = (($("platformSelect") && $("platformSelect").value) || "").trim() || "web";
    var deviceType = (($("deviceTypeSelect") && $("deviceTypeSelect").value) || "").trim() || "desktop";
    var deviceId = deviceIdFromPlatform(platform);
    var deviceCreds = {};
    deviceCreds[deviceId] = { platform: platform, device_type: deviceType };
    return {
      auth_token: stripBearer(($("authToken") && $("authToken").value) || ""),
      x_vix_user_token: (($("userToken") && $("userToken").value) || "").trim(),
      installation_id: (($("installId") && $("installId").value) || "").trim(),
      platform: platform,
      device_type: deviceType,
      country: "MX",
      accept_language: "es-MX,es;q=0.9",
      pages: pages.map(function (p) {
        return p.url_path;
      }),
      page_details: pages,
      persist_local: !!$("persistLocal").checked,
      devices: [deviceId],
      device_creds: deviceCreds,
    };
  }

  function ensureAtLeastOnePage() {
    if (!pageRows.length) {
      pageRows.push({ url_path: "", web_link: "" });
    }
  }

  function canRemovePage(idx) {
    return idx > 0 && pageRows.length > 1;
  }

  function renderPageEditor() {
    ensureAtLeastOnePage();
    var host = $("pageEditor");
    host.innerHTML = pageRows
      .map(function (row, idx) {
        var removeControl = canRemovePage(idx)
          ? '<button type="button" class="btn-icon btn-remove" aria-label="Remove page">' +
            ICON_TRASH +
            "</button>"
          : '<span class="page-remove-slot" aria-hidden="true"></span>';
        return (
          '<div class="page-edit-row" data-idx="' +
          idx +
          '">' +
          '<div class="field">' +
          "<label>Path</label>" +
          '<input type="text" class="page-path" value="' +
          escapeHtml(row.url_path || "") +
          '" placeholder="/ondemandplus"' +
          (idx === 0 ? " required" : "") +
          "/>" +
          "</div>" +
          '<div class="field">' +
          "<label>URL</label>" +
          '<input type="url" class="page-link" value="' +
          escapeHtml(row.web_link || "") +
          '" placeholder="https://vix.com/es-mx/…"/>' +
          "</div>" +
          removeControl +
          "</div>"
        );
      })
      .join("");

    $("btnAddPage").disabled = pageRows.length >= MAX_PAGES;

    host.querySelectorAll(".page-edit-row").forEach(function (el) {
      var idx = Number(el.getAttribute("data-idx"));
      var pathInput = el.querySelector(".page-path");
      var linkInput = el.querySelector(".page-link");
      pathInput.addEventListener("input", function () {
        pageRows[idx].url_path = pathInput.value;
        if (!linkInput.dataset.touched) {
          pageRows[idx].web_link = suggestLink(pathInput.value);
          linkInput.value = pageRows[idx].web_link;
        }
      });
      linkInput.addEventListener("input", function () {
        linkInput.dataset.touched = "1";
        pageRows[idx].web_link = linkInput.value;
      });
      var removeBtn = el.querySelector(".btn-remove");
      if (removeBtn) {
        removeBtn.addEventListener("click", function () {
          if (!canRemovePage(idx)) return;
          pageRows.splice(idx, 1);
          ensureAtLeastOnePage();
          renderPageEditor();
        });
      }
    });
  }

  function runLabel(run) {
    return run.when || run.ran_at_local || run.run_id || "(unknown)";
  }

  function fillRunSelect(selectEl, runs, selected, allowEmpty) {
    var html = allowEmpty ? '<option value="">—</option>' : "";
    html += runs
      .map(function (run) {
        return (
          '<option value="' +
          escapeHtml(run.run_id) +
          '"' +
          (run.run_id === selected ? " selected" : "") +
          ">" +
          escapeHtml(runLabel(run)) +
          "</option>"
        );
      })
      .join("");
    selectEl.innerHTML = html || (allowEmpty ? '<option value="">—</option>' : "");
  }

  function unionPagesFromRuns(runIds) {
    var seen = [];
    runIds.forEach(function (rid) {
      var run = historyRuns.find(function (r) {
        return r.run_id === rid;
      });
      var pages = (run && run.pages) || [];
      pages.forEach(function (p) {
        if (p && seen.indexOf(p) === -1) seen.push(p);
      });
    });
    return seen;
  }

  function gatherSelectedRunIds() {
    var ids = [];
    ["runA", "runB"].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      var v = (el.value || "").trim();
      if (v && ids.indexOf(v) === -1) ids.push(v);
    });
    return ids.slice(0, MAX_COMPARE_RUNS);
  }

  function gatherSelectedPages() {
    var host = $("pageChecks");
    if (!host) return selectedPages.slice();
    var checked = [];
    host.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
      if (cb.checked) checked.push(cb.value);
    });
    return checked;
  }

  function renderPageChecks(available, labels) {
    var host = $("pageChecks");
    if (!available || !available.length) {
      host.innerHTML = '<span class="muted">No pages in the selected scrapes.</span>';
      return;
    }
    var keep = selectedPages.filter(function (p) {
      return available.indexOf(p) !== -1;
    });
    if (!keep.length) keep = available.slice();
    selectedPages = keep;
    host.innerHTML = available
      .map(function (page) {
        var label = (labels && labels[page]) || page;
        var checked = selectedPages.indexOf(page) !== -1;
        return (
          '<label class="check">' +
          '<input type="checkbox" value="' +
          escapeHtml(page) +
          '"' +
          (checked ? " checked" : "") +
          "/> " +
          escapeHtml(label) +
          "</label>"
        );
      })
      .join("");
    host.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
      cb.addEventListener("change", function () {
        selectedPages = gatherSelectedPages();
        if (viewMode === "layouts") loadLayouts();
      });
    });
  }

  function syncCompareControls(history) {
    historyRuns = (history && history.runs) || [];
    var preferred =
      selectedRunId ||
      (history && history.selected_run_id) ||
      (history && history.latest_run_id) ||
      (historyRuns[0] && historyRuns[0].run_id) ||
      "";

    var currentA = ($("runA").value || "").trim();
    var currentB = ($("runB").value || "").trim();
    if (!currentA || !historyRuns.some(function (r) { return r.run_id === currentA; })) {
      currentA = preferred;
    }
    if (currentB && !historyRuns.some(function (r) { return r.run_id === currentB; })) {
      currentB = "";
    }

    fillRunSelect($("runA"), historyRuns, currentA, false);
    fillRunSelect($("runB"), historyRuns, currentB, true);

    var labels = {};
    historyRuns.forEach(function (run) {
      (run.pages || []).forEach(function (p, i) {
        if (!labels[p] && run.page_labels && run.page_labels[i]) {
          labels[p] = run.page_labels[i];
        }
      });
    });
    var pages = unionPagesFromRuns(gatherSelectedRunIds());
    if (!pages.length && preferred) {
      pages = unionPagesFromRuns([preferred]);
    }
    renderPageChecks(pages, labels);
  }

  function onRunSelectChange() {
    var ids = gatherSelectedRunIds();
    if (ids[0]) selectedRunId = ids[0];
    var labels = {};
    historyRuns.forEach(function (run) {
      (run.pages || []).forEach(function (p, i) {
        if (!labels[p] && run.page_labels && run.page_labels[i]) {
          labels[p] = run.page_labels[i];
        }
      });
    });
    renderPageChecks(unionPagesFromRuns(ids), labels);
    if (viewMode === "layouts") loadLayouts();
  }

  function pageCsvs(run) {
    var listed = run.page_csvs || [];
    if (listed.length) return listed.slice(0, MAX_PAGES);
    return (run.files || [])
      .map(function (f) {
        return typeof f === "string" ? f : f.name;
      })
      .filter(function (name) {
        return /_titles\.csv$/i.test(name || "") && !/^combined_titles\.csv$/i.test(name || "");
      })
      .slice(0, MAX_PAGES);
  }

  function renderHistory(history) {
    var host = $("historyList");
    var runs = (history && history.runs) || [];
    syncCompareControls(history);
    if (!runs.length) {
      host.innerHTML = '<p class="empty">No scrapes yet.</p>';
      return;
    }
    host.innerHTML = runs
      .map(function (run) {
        var open = run.run_id === expandedRunId;
        var pages = (run.page_labels || run.pages || []).join(", ");
        var metaBits = [];
        if (run.device_label) metaBits.push(run.device_label);
        if (pages) metaBits.push(pages);
        var csvs = pageCsvs(run);
        var files = csvs
          .map(function (name) {
            return (
              '<a href="/api/runs/' +
              encodeURIComponent(run.run_id) +
              "/files/" +
              encodeURIComponent(name) +
              '" download>' +
              escapeHtml(name) +
              "</a>"
            );
          })
          .join("");
        return (
          '<div class="history-item' +
          (open ? " open" : "") +
          '" data-run="' +
          escapeHtml(run.run_id) +
          '">' +
          '<button type="button" class="btn-select" aria-expanded="' +
          (open ? "true" : "false") +
          '">' +
          '<span class="when">' +
          escapeHtml(runLabel(run)) +
          "</span>" +
          "</button>" +
          '<div class="history-details">' +
          (metaBits.length ? '<span class="meta">' + escapeHtml(metaBits.join(" · ")) + "</span>" : "") +
          (files ? '<div class="file-links">' + files + "</div>" : "") +
          '<button type="button" class="btn-icon btn-danger btn-delete" aria-label="Delete scrape">' +
          ICON_TRASH +
          "</button>" +
          "</div>" +
          "</div>"
        );
      })
      .join("");

    host.querySelectorAll(".btn-select").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var article = btn.closest(".history-item");
        var rid = article && article.getAttribute("data-run");
        if (!rid) return;
        var opening = expandedRunId !== rid;
        expandedRunId = opening ? rid : null;
        host.querySelectorAll(".history-item").forEach(function (item) {
          var isOpen = item.getAttribute("data-run") === expandedRunId;
          item.classList.toggle("open", isOpen);
          var selectBtn = item.querySelector(".btn-select");
          if (selectBtn) selectBtn.setAttribute("aria-expanded", isOpen ? "true" : "false");
        });
      });
    });
    host.querySelectorAll(".btn-delete").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var article = btn.closest(".history-item");
        var rid = article && article.getAttribute("data-run");
        if (rid) deleteRun(rid);
      });
    });
  }

  function resetProgress() {
    var wrap = $("progressWrap");
    var section = $("progressSection");
    var fill = $("progressFill");
    var label = $("progressLabel");
    var bar = $("progressBar");
    if (wrap) wrap.hidden = true;
    if (section) section.hidden = true;
    if (fill) fill.style.width = "0%";
    if (label) label.textContent = "";
    if (bar) bar.setAttribute("aria-valuenow", "0");
  }

  function updateProgress(status) {
    var wrap = $("progressWrap");
    var section = $("progressSection");
    var fill = $("progressFill");
    var label = $("progressLabel");
    var bar = $("progressBar");
    if (!status || status.status !== "running") {
      resetProgress();
      return;
    }
    if (section) section.hidden = false;
    if (wrap) wrap.hidden = false;
    var pct = Number(status.percent);
    if (isNaN(pct)) {
      var done = Number(status.row_index || status.modules_done || 0);
      var total = Number(status.rows_total || status.modules_total || 0);
      pct = total > 0 ? Math.min(99, Math.round((done / total) * 100)) : 0;
    }
    pct = Math.max(0, Math.min(99, Math.round(pct)));
    if (fill) fill.style.width = pct + "%";
    if (bar) bar.setAttribute("aria-valuenow", String(pct));
    if (label) {
      label.textContent = "";
      label.hidden = true;
    }
  }

  function rowClass(row) {
    if (row.is_also_at) return "also";
    if (!row.present) return "missing";
    if (row.compare_status === "moved") return "moved";
    if (row.compare_status === "same_row") return "same";
    return "";
  }

  function formatPosCell(row) {
    if (!row.present) {
      return '<span class="cell-missing">Not on this page</span>';
    }
    return escapeHtml(row.row_title || "(untitled row)");
  }

  function slotInRow(row) {
    if (!row || row.present === false) return "—";
    var raw = row.carousel_x;
    if (raw === "" || raw == null) raw = row.slot;
    if (typeof raw === "string") raw = raw.trim();
    if (raw === "" || raw == null) return "—";
    var n = Number(raw);
    if (!isNaN(n) && String(raw).trim() !== "") {
      if (n <= 0) return "1";
      return String(n);
    }
    return String(raw);
  }

  function placementKey(row) {
    if (!row) return "";
    return [
      row.page || "",
      row.run_id || "",
      row.row_title || "",
      row.carousel_y || "",
      row.carousel_x || row.slot || "",
      row.present === false ? "0" : "1",
    ].join("\t");
  }

  function flattenPlacementRows(rows) {
    var out = [];
    var seen = {};
    function push(row) {
      if (!row) return;
      var k = placementKey(row);
      if (k && seen[k]) return;
      if (k) seen[k] = true;
      out.push(row);
    }
    (rows || []).forEach(function (row) {
      push(row);
      if (row && row.is_also_at) return;
      (row.also_at || []).forEach(function (p) {
        push({
          page: row.page,
          page_label: row.page_label,
          run_id: row.run_id,
          ran_at_local: row.ran_at_local,
          device_label: row.device_label,
          present: true,
          is_also_at: true,
          row_title: p.row_title,
          carousel_y: p.carousel_y,
          carousel_x: p.carousel_x,
          slot: p.slot,
          compare_status: p.compare_status,
          also_at: [],
        });
      });
    });
    return out;
  }

  function formatScrapeWhen(runId, fallback) {
    var run = historyRuns.find(function (r) {
      return r.run_id === runId;
    });
    if (run && run.when) return run.when;
    var text = String(fallback || "").trim();
    var m = text.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}:\d{2}:\d{2})/);
    if (m) {
      var months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
      ];
      return m[3] + " " + months[Number(m[2]) - 1] + " " + m[1] + " " + m[4];
    }
    return text || runId || "(unknown)";
  }

  function scrapeColumns(data) {
    var cols = (data.matrix && data.matrix.columns) || [];
    if (cols.length) return cols;
    var runs = data.runs || [];
    if (runs.length) {
      return runs.map(function (r) {
        return {
          run_id: r.run_id,
          ran_at_local: r.ran_at_local || r.when || "",
          device_label: r.device_label || "",
        };
      });
    }
    return (data.run_ids || []).map(function (id) {
      return { run_id: id, ran_at_local: "" };
    });
  }

  function missingPlacement(page) {
    return {
      page: page,
      present: false,
      row_title: "",
      carousel_y: "",
      carousel_x: "",
      slot: "",
    };
  }

  function placementsFromList(page, placements) {
    if (!placements || !placements.length) return [missingPlacement(page)];
    return placements.map(function (p, i) {
      return {
        page: page,
        present: true,
        is_also_at: i > 0,
        row_title: p.row_title,
        carousel_y: p.carousel_y,
        carousel_x: p.carousel_x,
        slot: p.slot,
        compare_status: p.compare_status,
      };
    });
  }

  function placementsFor(group, data, runId, page) {
    var fromTable = [];
    (group.table_rows || []).forEach(function (row) {
      if ((row.page || "") !== page) return;
      if (runId && row.run_id && row.run_id !== runId) return;
      fromTable.push(row);
    });
    if (fromTable.length) return flattenPlacementRows(fromTable);

    var byPage = null;
    if (runId && group.by_run && group.by_run[runId]) {
      byPage = group.by_run[runId].by_page || null;
    }
    if (!byPage) byPage = group.by_page || null;
    if (byPage && Object.prototype.hasOwnProperty.call(byPage, page)) {
      return placementsFromList(page, byPage[page]);
    }

    var found = [];
    ((data.matrix && data.matrix.rows) || []).forEach(function (row) {
      var sameTitle =
        (group.content_id && row.content_id && group.content_id === row.content_id) ||
        (group.title && row.title && group.title === row.title);
      if (!sameTitle) return;
      if ((row.page || "") !== page) return;
      var cell = (row.cells && runId && row.cells[runId]) || {};
      found = found.concat(placementsFromList(page, cell.placements || []));
    });
    if (found.length) return found;
    return [missingPlacement(page)];
  }

  function renderPlacementTable(rows) {
    var body = (rows || [])
      .map(function (row) {
        return (
          '<tr class="' +
          rowClass(row) +
          '">' +
          "<td>" +
          (row.present ? escapeHtml(String(row.carousel_y || "—")) : "—") +
          "</td>" +
          "<td>" +
          formatPosCell(row) +
          "</td>" +
          "<td>" +
          escapeHtml(slotInRow(row)) +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
    return (
      '<div class="table-wrap">' +
      '<table class="placement-table">' +
      "<thead><tr><th>Row #</th><th>Row name</th><th>Position</th></tr></thead>" +
      "<tbody>" +
      body +
      "</tbody></table></div>"
    );
  }

  function renderNestedGroup(group, data) {
    var scrapes = scrapeColumns(data);
    if (!scrapes.length) scrapes = [{ run_id: "", ran_at_local: "" }];
    var pages = (data.page_order || []).slice();
    if (!pages.length) {
      var seen = {};
      (group.table_rows || []).forEach(function (r) {
        if (r.page && !seen[r.page]) {
          seen[r.page] = true;
          pages.push(r.page);
        }
      });
    }
    var labels = data.page_labels || data.available_page_labels || {};
    var scrapeHtml = scrapes
      .map(function (scrape) {
        var pageHtml = pages
          .map(function (page) {
            return (
              '<div class="page-col">' +
              '<h5 class="page-heading">' +
              escapeHtml(labels[page] || page) +
              "</h5>" +
              renderPlacementTable(placementsFor(group, data, scrape.run_id, page)) +
              "</div>"
            );
          })
          .join("");
        return (
          '<section class="scrape-col">' +
          '<h4 class="scrape-heading">' +
          escapeHtml(formatScrapeWhen(scrape.run_id, scrape.ran_at_local || scrape.when)) +
          "</h4>" +
          '<div class="scrape-pages">' +
          pageHtml +
          "</div></section>"
        );
      })
      .join("");
    return (
      '<article class="compare-result">' +
      '<h3 class="compare-title">' +
      escapeHtml(group.title || data.query || "(untitled)") +
      "</h3>" +
      '<div class="compare-scrapes scrape-n-' +
      scrapes.length +
      '">' +
      scrapeHtml +
      "</div></article>"
    );
  }

  function renderLayouts(data) {
    var host = $("compareOut");
    if (data.available_pages && data.available_pages.length) {
      renderPageChecks(data.available_pages, data.available_page_labels || data.page_labels || {});
    }
    var columns = data.pages || [];
    if (!columns.length) {
      host.innerHTML = '<p class="empty">No page layouts in the selected scrapes.</p>';
      return;
    }
    host.innerHTML =
      '<div class="layout-board">' +
      columns
        .map(function (col) {
          var scrapes = col.scrapes || [];
          var scrapeHtml = scrapes
            .map(function (scrape, idx) {
              var label = formatScrapeWhen(scrape.run_id, scrape.when);
              if (scrapes.length > 1) {
                label = (idx === 0 ? "Scrape A · " : "Scrape B · ") + label;
              }
              var rows = scrape.rows || [];
              var rowHtml = rows
                .map(function (rail) {
                  var count = rail.row_size;
                  if (count == null) count = (rail.titles || []).length;
                  var meta = rail.empty ? "empty" : count + (count === 1 ? " title" : " titles");
                  if (rail.is_hero) meta = "hero · " + meta;
                  var tiles = (rail.titles || [])
                    .map(function (tile) {
                      return (
                        '<li class="layout-tile' +
                        (tile.empty ? " empty" : "") +
                        '"><span class="layout-x">' +
                        escapeHtml(String(tile.carousel_x || "")) +
                        '</span><span class="layout-tile-title">' +
                        escapeHtml(tile.title || "(empty)") +
                        "</span></li>"
                      );
                    })
                    .join("");
                  return (
                    '<li class="layout-row"><details>' +
                    "<summary><span class=\"layout-y\">" +
                    escapeHtml(String(rail.carousel_y || "")) +
                    '</span><span class="layout-row-name">' +
                    escapeHtml(rail.row_title || "(untitled row)") +
                    '</span><span class="layout-row-meta">' +
                    escapeHtml(String(meta)) +
                    "</span></summary>" +
                    '<ol class="layout-tiles">' +
                    (tiles ||
                      '<li class="layout-tile empty"><span class="layout-x"></span><span class="layout-tile-title">(empty)</span></li>') +
                    "</ol></details></li>"
                  );
                })
                .join("");
              return (
                '<article class="layout-scrape">' +
                '<h4 class="layout-scrape-head">' +
                escapeHtml(label) +
                " · " +
                rows.length +
                " rows</h4>" +
                '<ol class="layout-rows">' +
                (rowHtml || '<li class="empty">No rows.</li>') +
                "</ol></article>"
              );
            })
            .join("");
          return (
            '<section class="layout-page">' +
            '<h3 class="layout-page-title">' +
            escapeHtml(col.label || col.page || "") +
            "</h3>" +
            scrapeHtml +
            "</section>"
          );
        })
        .join("") +
      "</div>";
  }

  function setViewMode(mode, opts) {
    var skipLoad = opts && opts.skipLoad;
    viewMode = mode === "layouts" ? "layouts" : "search";
    var searchBtn = $("viewSearch");
    var layoutBtn = $("viewLayouts");
    searchBtn.classList.toggle("is-active", viewMode === "search");
    layoutBtn.classList.toggle("is-active", viewMode === "layouts");
    searchBtn.setAttribute("aria-selected", viewMode === "search" ? "true" : "false");
    layoutBtn.setAttribute("aria-selected", viewMode === "layouts" ? "true" : "false");
    $("searchRow").hidden = viewMode !== "search";
    if (viewMode === "layouts") {
      $("compareHint").textContent =
        "Checked pages appear side by side. Expand a row to see titles by position.";
      if (!skipLoad) loadLayouts();
    } else {
      $("compareHint").textContent =
        "Pick scrapes and pages. Search a title, or view full page layouts side by side.";
    }
  }

  function renderCompare(data) {
    var host = $("compareOut");
    try {
      var groups = data.groups || [];

      if (data.available_pages && data.available_pages.length) {
        renderPageChecks(data.available_pages, data.available_page_labels || data.page_labels || {});
      }

      if (!groups.length && (data.table_rows || []).length) {
        groups = [{ title: data.query || "Results", table_rows: data.table_rows }];
      }

      if (!groups.length) {
        host.innerHTML =
          '<p class="empty">No matches for &ldquo;' +
          escapeHtml(data.query || "") +
          "&rdquo;.</p>";
        return;
      }

      host.innerHTML = groups
        .map(function (g) {
          return renderNestedGroup(g, data);
        })
        .join("");
    } catch (err) {
      host.innerHTML =
        '<p class="empty">Could not render compare for this title (incomplete placement data).</p>';
    }
  }

  function applyMeta(meta) {
    if (meta.history) renderHistory(meta.history);
    if (meta.run_id) selectedRunId = meta.run_id;
    updateProgress(meta.scrape_status);
  }

  async function loadMeta() {
    var url = "/api/meta";
    if (selectedRunId) url += "?run_id=" + encodeURIComponent(selectedRunId);
    var r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error("Could not load scrape status");
    var meta = await r.json();
    applyMeta(meta);
    return meta;
  }

  async function deleteRun(runId) {
    var label = runId;
    var run = historyRuns.find(function (r) {
      return r.run_id === runId;
    });
    if (run) label = runLabel(run);
    if (!window.confirm("Delete this scrape from history?\n\n" + label)) {
      return;
    }
    try {
      var r = await fetch("/api/history/" + encodeURIComponent(runId), {
        method: "DELETE",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      var data = await r.json();
      if (!r.ok || !data.ok) throw new Error(data.error || "Could not delete scrape");
      if (selectedRunId === runId) selectedRunId = data.run_id || data.latest_run_id || null;
      if (expandedRunId === runId) expandedRunId = null;
      ["runA", "runB"].forEach(function (id) {
        if ($(id).value === runId) $(id).value = id === "runA" ? selectedRunId || "" : "";
      });
      applyMeta(data);
      setStatus($("compareStatus"), data.message || "Scrape deleted.", "ok");
      if (viewMode === "layouts") loadLayouts();
      else {
        var q = ($("q").value || "").trim();
        if (q && gatherSelectedRunIds().length) runCompare(q);
        else if (!gatherSelectedRunIds().length) {
          renderCompare({ groups: [], summary: {} });
        }
      }
    } catch (err) {
      setStatus($("compareStatus"), err.message || String(err), "error");
    }
  }

  async function deleteAllHistory() {
    if (!window.confirm("Clear all successful scrape history? This cannot be undone.")) {
      return;
    }
    try {
      var r = await fetch("/api/history/clear", {
        method: "POST",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      var data = await r.json();
      if (!r.ok || !data.ok) throw new Error(data.error || "Could not delete history");
      selectedRunId = null;
      expandedRunId = null;
      selectedPages = [];
      applyMeta(data);
      renderCompare({ groups: [], summary: {} });
      setStatus($("compareStatus"), data.message || "All history deleted.", "ok");
    } catch (err) {
      setStatus($("compareStatus"), err.message || String(err), "error");
    }
  }

  async function loadLayouts() {
    setStatus($("compareStatus"), "", "");
    try {
      var runIds = gatherSelectedRunIds();
      var pages = gatherSelectedPages();
      if (!runIds.length) {
        throw new Error("Pick a scrape from History.");
      }
      if (!pages.length) {
        throw new Error("Select at least one page to include.");
      }
      var url =
        "/api/layouts?run_ids=" +
        encodeURIComponent(runIds.join(",")) +
        "&pages=" +
        encodeURIComponent(pages.join(","));
      var r = await fetch(url, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      var data = await r.json();
      if (!r.ok) throw new Error(data.error || "Could not load layouts");
      selectedRunId = runIds[0];
      renderLayouts(data);
      setStatus($("compareStatus"), "", "");
      var loc = new URL(window.location.href);
      loc.searchParams.set("view", "layouts");
      loc.searchParams.delete("q");
      if (runIds.length) loc.searchParams.set("run_ids", runIds.join(","));
      else loc.searchParams.delete("run_ids");
      if (pages.length) loc.searchParams.set("pages", pages.join(","));
      history.replaceState(null, "", loc.pathname + loc.search);
    } catch (err) {
      setStatus($("compareStatus"), err.message || String(err), "error");
      $("compareOut").innerHTML = '<p class="empty">' + escapeHtml(err.message || String(err)) + "</p>";
    }
  }

  async function runCompare(q) {
    var btn = $("btnCompare");
    btn.disabled = true;
    setStatus($("compareStatus"), "Searching…", "");
    try {
      var runIds = gatherSelectedRunIds();
      var pages = gatherSelectedPages();
      if (!runIds.length) {
        throw new Error("Pick a scrape from History.");
      }
      if (!pages.length) {
        throw new Error("Select at least one page to include.");
      }
      var url =
        "/api/compare?q=" +
        encodeURIComponent(q) +
        "&run_ids=" +
        encodeURIComponent(runIds.join(",")) +
        "&pages=" +
        encodeURIComponent(pages.join(","));
      var r = await fetch(url, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      var data = await r.json();
      if (!r.ok) throw new Error(data.error || "Search failed");
      selectedRunId = runIds[0];
      renderCompare(data);
      var n = (data.summary && data.summary.placements) || 0;
      var runsN = (data.run_ids || runIds).length;
      var msg =
        (n ? n + ' placement(s) for "' + q + '"' : 'No placements for "' + q + '"') +
        (runsN > 1 ? " · comparing " + runsN + " scrapes" : "");
      setStatus($("compareStatus"), msg, n ? "ok" : "");
      var loc = new URL(window.location.href);
      if (q) loc.searchParams.set("q", q);
      else loc.searchParams.delete("q");
      loc.searchParams.delete("view");
      if (runIds.length) loc.searchParams.set("run_ids", runIds.join(","));
      else loc.searchParams.delete("run_ids");
      if (pages.length) loc.searchParams.set("pages", pages.join(","));
      history.replaceState(null, "", loc.pathname + loc.search);
    } catch (err) {
      setStatus($("compareStatus"), err.message || String(err), "error");
    } finally {
      btn.disabled = false;
    }
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function pollScrapeStatus() {
    try {
      var r = await fetch("/api/scrape/status", { cache: "no-store" });
      var status = await r.json();
      updateProgress(status);
      if (status.status === "running") {
        setStatus($("scrapeStatus"), "", "");
      }

      if (status.status === "done" || status.status === "error") {
        stopPolling();
        resetProgress();
        $("btnScrape").disabled = false;
        if (status.status === "done") {
          selectedRunId = status.run_id || selectedRunId;
          selectedPages = [];
          var doneMsg =
            (status.result && status.result.message) || status.message || "Scrape complete";
          setStatus($("scrapeStatus"), doneMsg, "ok");
        } else {
          setStatus(
            $("scrapeStatus"),
            status.error ||
              (status.result && status.result.error) ||
              status.message ||
              "Scrape failed",
            "error"
          );
        }
        await loadMeta();
        if (viewMode === "layouts") loadLayouts();
      }
    } catch (err) {
      /* keep polling; transient */
    }
  }

  function focusFirstMissingCred(creds) {
    if (!creds.auth_token && $("authToken")) {
      $("authToken").focus();
      return;
    }
    if (!creds.x_vix_user_token && $("userToken")) {
      $("userToken").focus();
      return;
    }
    if (!creds.installation_id && $("installId")) $("installId").focus();
  }

  async function runScrape() {
    var btn = $("btnScrape");
    var creds = gatherCreds();
    if (!creds.auth_token || !creds.x_vix_user_token || !creds.installation_id) {
      setStatus(
        $("scrapeStatus"),
        "Authorization, User token, and Installation ID are required.",
        "error"
      );
      focusFirstMissingCred(creds);
      return;
    }
    var emptyIdx = firstEmptyPageIndex();
    if (emptyIdx >= 0) {
      setStatus($("scrapeStatus"), "Each page needs a path (for example /ondemandplus).", "error");
      var emptyInput = document.querySelector(
        '.page-edit-row[data-idx="' + emptyIdx + '"] .page-path'
      );
      if (emptyInput) emptyInput.focus();
      return;
    }
    if (!creds.pages.length) {
      setStatus($("scrapeStatus"), "Add at least one page path", "error");
      return;
    }
    if (!creds.platform || !creds.device_type) {
      setStatus($("scrapeStatus"), "Choose a platform and device type", "error");
      return;
    }
    if (creds.pages.length > MAX_PAGES) {
      setStatus(
        $("scrapeStatus"),
        "You can scrape at most " + MAX_PAGES + " pages at a time.",
        "error"
      );
      return;
    }
    persistDeviceFieldState();
    btn.disabled = true;
    setStatus($("scrapeStatus"), "", "");
    updateProgress({
      status: "running",
      percent: 0,
      row_index: 0,
      rows_total: 0,
      message: "Scraping…",
    });
    try {
      var r = await fetch("/api/scrape", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(creds),
      });
      var data = await r.json();
      if (!r.ok) {
        throw new Error(data.error || data.message || "Scrape failed");
      }
      stopPolling();
      pollTimer = setInterval(pollScrapeStatus, 800);
      pollScrapeStatus();
    } catch (err) {
      btn.disabled = false;
      resetProgress();
      setStatus($("scrapeStatus"), err.message || String(err), "error");
      await loadMeta();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (location.protocol === "file:") {
      $("fileWarn").hidden = false;
      return;
    }

    $("maxPagesLabel").textContent = String(MAX_PAGES);
    renderDeviceControls();
    renderPageEditor();

    $("btnAddPage").addEventListener("click", function () {
      if (pageRows.length >= MAX_PAGES) return;
      pageRows.push({ url_path: "", web_link: "" });
      renderPageEditor();
    });

    $("platformSelect").addEventListener("change", persistDeviceFieldState);
    $("deviceTypeSelect").addEventListener("change", persistDeviceFieldState);

    $("btnScrape").addEventListener("click", function (e) {
      e.preventDefault();
      runScrape();
    });

    $("btnDeleteAllHistory").addEventListener("click", function () {
      deleteAllHistory();
    });

    ["runA", "runB"].forEach(function (id) {
      $(id).addEventListener("change", onRunSelectChange);
    });

    $("viewSearch").addEventListener("click", function () {
      setViewMode("search", { skipLoad: true });
    });
    $("viewLayouts").addEventListener("click", function () {
      setViewMode("layouts");
    });

    $("compareForm").addEventListener("submit", function (e) {
      e.preventDefault();
      if (viewMode === "layouts") {
        return;
      }
      var q = ($("q").value || "").trim();
      if (!q) {
        setStatus($("compareStatus"), "Enter a title to search", "error");
        return;
      }
      runCompare(q);
    });

    loadMeta()
      .then(function (meta) {
        if (meta.platform_options || meta.device_type_options) {
          renderDeviceControls(meta);
        }
        if (meta.scrape_status && meta.scrape_status.status === "running") {
          $("btnScrape").disabled = true;
          pollTimer = setInterval(pollScrapeStatus, 800);
          pollScrapeStatus();
        }
        var params = new URLSearchParams(location.search);
        var q = (params.get("q") || "").trim();
        var runIdsParam = (params.get("run_ids") || "").trim();
        var pagesParam = (params.get("pages") || "").trim();
        if (runIdsParam) {
          var ids = runIdsParam.split(",").map(function (s) {
            return s.trim();
          }).filter(Boolean);
          if (ids[0]) {
            $("runA").value = ids[0];
            selectedRunId = ids[0];
          }
          if (ids[1]) $("runB").value = ids[1];
          onRunSelectChange();
        }
        if (pagesParam) {
          selectedPages = pagesParam.split(",").map(function (s) {
            return s.trim();
          }).filter(Boolean);
          onRunSelectChange();
        }
        var viewParam = (params.get("view") || "").trim();
        if (viewParam === "layouts") {
          setViewMode("layouts");
        } else if (q) {
          $("q").value = q;
          runCompare(q);
        }
      })
      .catch(function (err) {
        setStatus($("scrapeStatus"), err.message || String(err), "error");
      });
  });
})();
