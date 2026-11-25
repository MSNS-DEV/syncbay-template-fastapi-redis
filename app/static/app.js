// Notes web UI: vanilla JS, no build step. Talks to the JSON API under /api.
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var state = {
    accessToken: null,
    user: null,
    notes: [],
    nextCursor: null,
    selectedId: null,
    editingId: null,
    query: "",
    tag: "",
  };

  // ---------- helpers ----------
  function setStatus(message, kind) {
    var el = $("status");
    el.textContent = message || "";
    el.dataset.kind = kind || "info";
  }

  function readCookie(name) {
    var match = document.cookie.split("; ").find(function (c) { return c.indexOf(name + "=") === 0; });
    return match ? decodeURIComponent(match.slice(name.length + 1)) : "";
  }

  function describeError(res, data) {
    if (res.status === 429) {
      var wait = res.headers.get("Retry-After");
      return "Too many attempts. Try again in " + (wait || "a few") + " seconds.";
    }
    if (data && Array.isArray(data.detail)) {
      return data.detail.map(function (d) {
        var field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : "";
        var msg = String(d.msg || "Invalid value").replace(/^Value error, /, "");
        return (field && field !== "body" ? field + ": " : "") + msg;
      }).join(" ");
    }
    if (data && typeof data.detail === "string") return data.detail;
    return "Request failed (" + res.status + ").";
  }

  async function api(path, options, retried) {
    var opts = Object.assign({ method: "GET", headers: {} }, options || {});
    opts.headers = Object.assign({ Accept: "application/json" }, opts.headers);
    if (opts.json !== undefined) {
      opts.body = JSON.stringify(opts.json);
      opts.headers["Content-Type"] = "application/json";
      delete opts.json;
    }
    if (state.accessToken && !opts.noAuth) opts.headers.Authorization = "Bearer " + state.accessToken;
    opts.credentials = "same-origin";
    var res = await fetch(path, opts);
    if (res.status === 401 && !opts.noAuth && !retried && state.accessToken) {
      if (await refreshSession()) return api(path, options, true);
      signedOut("Your session expired. Please log in again.");
    }
    var data = null;
    var text = res.status === 204 ? "" : await res.text();
    if (text) {
      try { data = JSON.parse(text); } catch (e) { data = null; }
    }
    return { res: res, data: data };
  }

  async function refreshSession() {
    if (!readCookie("csrf_token")) return false;
    var r = await fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": readCookie("csrf_token"), Accept: "application/json" },
    });
    if (!r.ok) return false;
    var data = await r.json();
    state.accessToken = data.access_token;
    state.user = data.user;
    return true;
  }

  // ---------- branding & theme ----------
  function contrastText(hex) {
    var h = hex.replace("#", "");
    if (h.length === 3) h = h.split("").map(function (c) { return c + c; }).join("");
    var rgb = [0, 2, 4].map(function (i) {
      var v = parseInt(h.slice(i, i + 2), 16) / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    var lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
    // Pick whichever of near-black / white contrasts more with the brand colour.
    return (lum + 0.05) / 0.05 > 1.05 / (lum + 0.05) ? "#0b1220" : "#ffffff";
  }

  async function loadBranding() {
    try {
      var res = await fetch("/config.json");
      var cfg = await res.json();
      var root = document.documentElement.style;
      root.setProperty("--brand-primary", cfg.brandPrimary);
      root.setProperty("--brand-on-primary", contrastText(cfg.brandPrimary));
      root.setProperty("--brand-name", JSON.stringify(cfg.brandName));
      $("brand-name").textContent = cfg.brandName;
      document.title = cfg.brandName + " Notes";
      if (cfg.brandLogoUrl) {
        var logo = $("brand-logo");
        logo.src = cfg.brandLogoUrl;
        logo.hidden = false;
        $("brand-mark").hidden = true;
      }
    } catch (e) {
      /* keep the defaults from styles.css */
    }
  }

  function currentTheme() {
    var set = document.documentElement.getAttribute("data-theme");
    if (set) return set;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function syncThemeButton() {
    var dark = currentTheme() === "dark";
    $("theme-toggle").setAttribute("aria-pressed", String(dark));
    $("theme-label").textContent = dark ? "Light mode" : "Dark mode";
  }

  function toggleTheme() {
    var next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) { /* ignore */ }
    syncThemeButton();
  }

  // ---------- auth ----------
  function selectTab(which) {
    var login = which === "login";
    $("tab-login").setAttribute("aria-selected", String(login));
    $("tab-register").setAttribute("aria-selected", String(!login));
    $("tab-login").tabIndex = login ? 0 : -1;
    $("tab-register").tabIndex = login ? -1 : 0;
    $("login-form").hidden = !login;
    $("register-form").hidden = login;
  }

  function onTabKey(event) {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      var toRegister = event.target.id === "tab-login";
      selectTab(toRegister ? "register" : "login");
      $(toRegister ? "tab-register" : "tab-login").focus();
    }
  }

  async function submitAuth(event, kind) {
    event.preventDefault();
    var form = event.target;
    var errorEl = $(kind + "-error");
    errorEl.textContent = "";
    var payload = {
      email: form.email.value.trim(),
      password: form.password.value,
    };
    if (kind === "register") payload.display_name = form.display_name.value.trim();
    if (!payload.email || !payload.password) {
      errorEl.textContent = "Enter your email and password.";
      return;
    }
    var button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      var out = await api("/api/auth/" + kind, { method: "POST", json: payload, noAuth: true });
      if (!out.res.ok) {
        errorEl.textContent = describeError(out.res, out.data);
        return;
      }
      state.accessToken = out.data.access_token;
      state.user = out.data.user;
      form.reset();
      await signedIn(kind === "register" ? "Account created. Welcome!" : "Logged in.");
    } catch (e) {
      errorEl.textContent = "Network error, please retry.";
    } finally {
      button.disabled = false;
    }
  }

  async function logout() {
    await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": readCookie("csrf_token") },
    }).catch(function () { /* already signed out */ });
    signedOut("Logged out.");
  }

  function signedOut(message) {
    state.accessToken = null;
    state.user = null;
    state.notes = [];
    $("notes-view").hidden = true;
    $("auth-view").hidden = false;
    $("logout").hidden = true;
    $("user-chip").hidden = true;
    setStatus(message || "");
  }

  async function signedIn(message) {
    $("auth-view").hidden = true;
    $("notes-view").hidden = false;
    $("logout").hidden = false;
    var chip = $("user-chip");
    chip.textContent = state.user.display_name || state.user.email;
    chip.hidden = false;
    setStatus(message || "");
    closeEditor();
    await Promise.all([loadNotes(false), loadTags()]);
  }

  // ---------- notes ----------
  async function loadTags() {
    var out = await api("/api/tags");
    if (!out.res.ok) return;
    var select = $("tag-filter");
    var current = select.value;
    select.textContent = "";
    var all = document.createElement("option");
    all.value = "";
    all.textContent = "All tags";
    select.appendChild(all);
    out.data.forEach(function (t) {
      var opt = document.createElement("option");
      opt.value = t.tag;
      opt.textContent = t.tag + " (" + t.count + ")";
      select.appendChild(opt);
    });
    select.value = out.data.some(function (t) { return t.tag === current; }) ? current : "";
  }

  async function loadNotes(append) {
    var params = new URLSearchParams();
    if (state.query) params.set("q", state.query);
    if (state.tag) params.set("tag", state.tag);
    if (append && state.nextCursor) params.set("cursor", state.nextCursor);
    params.set("limit", "20");
    var out = await api("/api/notes?" + params.toString());
    if (!out.res.ok) {
      setStatus(describeError(out.res, out.data), "error");
      return;
    }
    state.notes = append ? state.notes.concat(out.data.items) : out.data.items;
    state.nextCursor = out.data.next_cursor;
    $("cache-note").textContent = "List served from " +
      (out.res.headers.get("X-Cache") === "HIT" ? "Redis cache" : "PostgreSQL") + ".";
    renderList();
  }

  function formatDate(iso) {
    return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }

  function excerpt(body) {
    var plain = body.replace(/[#*_`>\[\]()-]/g, " ").replace(/\s+/g, " ").trim();
    return plain.length > 120 ? plain.slice(0, 117) + "…" : plain;
  }

  function renderList() {
    var list = $("note-list");
    list.textContent = "";
    state.notes.forEach(function (note) {
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "note-item" + (note.id === state.selectedId ? " is-selected" : "");
      btn.dataset.id = note.id;
      btn.setAttribute("aria-current", note.id === state.selectedId ? "true" : "false");
      var title = document.createElement("span");
      title.className = "note-item-title";
      title.textContent = note.title;
      if (note.pinned) {
        var pin = document.createElement("span");
        pin.className = "pin";
        pin.textContent = "Pinned";
        title.appendChild(pin);
      }
      var ex = document.createElement("span");
      ex.className = "note-item-excerpt";
      ex.textContent = excerpt(note.body) || "No content";
      var meta = document.createElement("span");
      meta.className = "note-item-meta";
      meta.textContent = formatDate(note.updated_at) + (note.tags.length ? " · " + note.tags.map(function (t) { return "#" + t; }).join(" ") : "");
      btn.append(title, ex, meta);
      btn.addEventListener("click", function () { showNote(note.id); });
      li.appendChild(btn);
      list.appendChild(li);
    });
    $("empty").hidden = state.notes.length > 0;
    $("load-more").hidden = !state.nextCursor;
  }

  function findNote(id) {
    return state.notes.find(function (n) { return n.id === id; });
  }

  function showNote(id) {
    var note = findNote(id);
    if (!note) return;
    state.selectedId = id;
    $("note-form").hidden = true;
    $("no-selection").hidden = true;
    var view = $("note-view");
    view.hidden = false;
    $("view-title").textContent = note.title;
    $("view-meta").textContent = (note.pinned ? "Pinned · " : "") + "Updated " + formatDate(note.updated_at);
    var tags = $("view-tags");
    tags.textContent = "";
    note.tags.forEach(function (t) {
      var li = document.createElement("li");
      li.textContent = "#" + t;
      tags.appendChild(li);
    });
    $("view-body").innerHTML = window.Markdown.render(note.body);
    renderList();
  }

  function openEditor(note) {
    state.editingId = note ? note.id : null;
    $("note-view").hidden = true;
    $("no-selection").hidden = true;
    var form = $("note-form");
    form.hidden = false;
    $("editor-title").textContent = note ? "Edit note" : "New note";
    $("note-title").value = note ? note.title : "";
    $("note-body").value = note ? note.body : "";
    $("note-tags").value = note ? note.tags.join(", ") : "";
    $("note-pinned").checked = note ? note.pinned : false;
    $("delete-note").hidden = !note;
    $("note-error").textContent = "";
    $("note-title").focus();
  }

  function closeEditor() {
    state.editingId = null;
    $("note-form").hidden = true;
    var sel = state.selectedId && findNote(state.selectedId);
    if (sel) {
      showNote(sel.id);
    } else {
      $("note-view").hidden = true;
      $("no-selection").hidden = false;
    }
  }

  async function saveNote(event) {
    event.preventDefault();
    var form = event.target;
    var errorEl = $("note-error");
    errorEl.textContent = "";
    var payload = {
      title: $("note-title").value.trim(),
      body: $("note-body").value,
      tags: $("note-tags").value.split(",").map(function (t) { return t.trim(); }).filter(Boolean),
      pinned: $("note-pinned").checked,
    };
    if (!payload.title) {
      errorEl.textContent = "Title is required.";
      $("note-title").focus();
      return;
    }
    var editing = state.editingId;
    var out = await api(editing ? "/api/notes/" + editing : "/api/notes", {
      method: editing ? "PATCH" : "POST",
      json: payload,
    });
    if (!out.res.ok) {
      errorEl.textContent = describeError(out.res, out.data);
      return;
    }
    state.selectedId = out.data.id;
    state.editingId = null;
    setStatus(editing ? "Note saved." : "Note created.", "success");
    await Promise.all([loadNotes(false), loadTags()]);
    if (!findNote(out.data.id)) state.notes.unshift(out.data);
    showNote(out.data.id);
    $("note-form").hidden = true;
  }

  async function deleteNote() {
    var id = state.editingId;
    var note = id && findNote(id);
    if (!note) return;
    if (!window.confirm("Delete “" + note.title + "”? This cannot be undone.")) return;
    var out = await api("/api/notes/" + id, { method: "DELETE" });
    if (!out.res.ok) {
      $("note-error").textContent = describeError(out.res, out.data);
      return;
    }
    state.selectedId = null;
    state.editingId = null;
    setStatus("Note deleted.", "success");
    await Promise.all([loadNotes(false), loadTags()]);
    closeEditor();
  }

  // ---------- wiring ----------
  async function boot() {
    syncThemeButton();
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", syncThemeButton);
    $("theme-toggle").addEventListener("click", toggleTheme);
    $("tab-login").addEventListener("click", function () { selectTab("login"); });
    $("tab-register").addEventListener("click", function () { selectTab("register"); });
    $("tab-login").addEventListener("keydown", onTabKey);
    $("tab-register").addEventListener("keydown", onTabKey);
    $("login-form").addEventListener("submit", function (e) { submitAuth(e, "login"); });
    $("register-form").addEventListener("submit", function (e) { submitAuth(e, "register"); });
    $("logout").addEventListener("click", logout);
    $("new-note").addEventListener("click", function () { openEditor(null); });
    $("cancel-note").addEventListener("click", closeEditor);
    $("note-form").addEventListener("submit", saveNote);
    $("delete-note").addEventListener("click", deleteNote);
    $("edit-note").addEventListener("click", function () { openEditor(findNote(state.selectedId)); });
    $("load-more").addEventListener("click", function () { loadNotes(true); });
    $("search-form").addEventListener("submit", function (e) {
      e.preventDefault();
      state.query = $("search").value.trim();
      state.tag = $("tag-filter").value;
      loadNotes(false);
    });
    $("tag-filter").addEventListener("change", function () {
      state.tag = $("tag-filter").value;
      loadNotes(false);
    });

    await loadBranding();
    var restored = false;
    try { restored = await refreshSession(); } catch (e) { restored = false; }
    if (restored) {
      await signedIn("");
    } else {
      signedOut("");
    }
    document.body.dataset.ready = "true";
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
