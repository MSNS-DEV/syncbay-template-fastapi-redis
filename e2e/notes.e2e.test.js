// End-to-end tests against the running compose stack (docker compose up -d --wait).
// Run: node --test e2e/   (BASE_URL defaults to http://localhost:15100)
"use strict";

const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const { chromium } = require("playwright");

const BASE_URL = process.env.BASE_URL || "http://localhost:15100";
const PROJECT = process.env.COMPOSE_PROJECT || "tpl-fastapi-redis";
const ROOT = path.resolve(__dirname, "..");
const SHOTS = path.join(ROOT, "docs", "screenshots");
const VIEWPORT = { width: 1280, height: 800 };
const PASSWORD = "e2e-pass-123";

let browser;

function resetRateLimits() {
  // Rate-limit windows last a minute; clear them so back-to-back runs start fresh.
  const script = "for k in $(redis-cli --scan --pattern 'rl:*'); do redis-cli del \"$k\" >/dev/null; done";
  execFileSync("docker", ["compose", "-p", PROJECT, "exec", "-T", "redis", "sh", "-c", script], {
    cwd: ROOT,
    stdio: "inherit",
  });
}

async function newPage(colorScheme = "light") {
  const context = await browser.newContext({ viewport: VIEWPORT, colorScheme, baseURL: BASE_URL });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (err) => errors.push(err));
  page.on("console", (msg) => {
    if (msg.type() === "error" && !/status of 4\d\d/.test(msg.text())) errors.push(msg.text());
  });
  page.jsErrors = errors;
  return page;
}

async function open(page) {
  await page.goto("/");
  await page.waitForSelector("body[data-ready=true]");
}

function uniqueEmail() {
  return `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function registerViaUi(page, email) {
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByLabel("Display name").fill("E2E Tester");
  await page.locator("#register-email").fill(email);
  await page.locator("#register-password").fill(PASSWORD);
  await page.locator("#register-form").getByRole("button", { name: "Create account" }).click();
  await page.getByRole("heading", { level: 1, name: "Notes" }).waitFor();
}

before(async () => {
  resetRateLimits();
  browser = await chromium.launch();
});

after(async () => {
  await browser?.close();
});

test("health endpoint reports db and redis ok", async () => {
  const res = await fetch(`${BASE_URL}/health`);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { status: "ok", checks: { db: "ok", redis: "ok" } });
  assert.equal(res.headers.get("cache-control"), "no-store");
});

test("landing page renders in light and dark mode", async () => {
  for (const scheme of ["light", "dark"]) {
    const page = await newPage(scheme);
    await open(page);
    await page.getByRole("heading", { level: 1, name: /Your notes/ }).waitFor();
    assert.equal(await page.locator("#brand-name").textContent(), "Syncbay");
    await page.screenshot({ path: path.join(SHOTS, `home-${scheme}.png`) });
    assert.deepEqual(page.jsErrors, []);
    await page.context().close();
  }
});

test("register, create, search, edit and delete a note", async () => {
  const page = await newPage("light");
  await open(page);
  await registerViaUi(page, uniqueEmail());

  // SEED_DEMO_DATA=true gives every new account three welcome notes.
  await assert.doesNotReject(page.locator(".note-item").nth(2).waitFor());
  assert.equal(await page.locator(".note-item").count(), 3);

  // Create
  await page.getByRole("button", { name: "New note" }).click();
  await page.getByLabel("Title").fill("Release checklist");
  await page.getByLabel(/^Body/).fill("## Steps\n\n- run **migrations**\n- deploy on Friday");
  await page.getByRole("textbox", { name: /^Tags/ }).fill("ops, release");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Note created." }).waitFor();
  await page.locator("#view-title", { hasText: "Release checklist" }).waitFor();
  assert.equal(await page.locator("#view-body strong").textContent(), "migrations");
  assert.equal(await page.locator(".note-item").count(), 4);

  // Search (full text + substring) and tag filter
  await page.getByLabel("Search").fill("migrations");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll(".note-item").length === 1);
  assert.match(await page.locator(".note-item").first().textContent(), /Release checklist/);
  await page.locator(".note-item").first().click();
  await page.screenshot({ path: path.join(SHOTS, "notes-search.png") });

  await page.getByLabel("Search").fill("");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll(".note-item").length === 4);
  await page.getByLabel("Tag", { exact: true }).selectOption("release");
  await page.waitForFunction(() => document.querySelectorAll(".note-item").length === 1);
  await page.getByLabel("Tag", { exact: true }).selectOption("");
  await page.waitForFunction(() => document.querySelectorAll(".note-item").length === 4);

  // Edit: rename and pin
  await page.locator(".note-item", { hasText: "Release checklist" }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Title").fill("Release checklist v2");
  await page.getByLabel("Pin to top").check();
  await page.screenshot({ path: path.join(SHOTS, "note-editor.png") });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Note saved." }).waitFor();
  await page.locator("#view-title", { hasText: "Release checklist v2" }).waitFor();
  const first = page.locator(".note-item").first();
  assert.match(await first.textContent(), /Release checklist v2.*Pinned|Pinned.*Release checklist v2/s);

  // The session survives a reload (refresh-token cookie + CSRF header).
  await page.reload();
  await page.waitForSelector("body[data-ready=true]");
  await page.locator(".note-item", { hasText: "Release checklist v2" }).waitFor();

  // Delete
  await page.locator(".note-item", { hasText: "Release checklist v2" }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Note deleted." }).waitFor();
  await page.waitForFunction(() => document.querySelectorAll(".note-item").length === 3);
  assert.equal(await page.locator(".note-item", { hasText: "Release checklist" }).count(), 0);

  // Log out returns to the landing page and the refresh token is revoked.
  await page.getByRole("button", { name: "Log out" }).click();
  await page.getByRole("heading", { level: 1, name: /Your notes/ }).waitFor();
  await page.reload();
  await page.waitForSelector("body[data-ready=true]");
  assert.equal(await page.locator("#auth-view").isVisible(), true);

  assert.deepEqual(page.jsErrors, []);
  await page.context().close();
});

test("validation errors are shown to the user", async () => {
  const page = await newPage("light");
  await open(page);
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.locator("#register-email").fill(uniqueEmail());
  await page.locator("#register-password").fill("short");
  await page.locator("#register-form").getByRole("button", { name: "Create account" }).click();
  const error = page.locator("#register-error");
  await error.filter({ hasText: /password/i }).waitFor();
  assert.match(await error.textContent(), /at least 8 characters/);

  // Server-side validation of a note: an invalid tag is rejected with 422.
  await page.locator("#register-password").fill(PASSWORD);
  await page.locator("#register-form").getByRole("button", { name: "Create account" }).click();
  await page.getByRole("button", { name: "New note" }).click();
  await page.getByLabel("Title").fill("Bad tags");
  await page.getByRole("textbox", { name: /^Tags/ }).fill("not valid!");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.locator("#note-error").filter({ hasText: /invalid tag/ }).waitFor();
  await page.context().close();
});

test("dark mode follows the system and the toggle persists", async () => {
  const dark = await newPage("dark");
  await open(dark);
  const bgDark = await dark.evaluate(() => getComputedStyle(document.body).backgroundColor);
  assert.equal(bgDark, "rgb(11, 17, 32)");
  assert.equal(await dark.locator("#theme-toggle").getAttribute("aria-pressed"), "true");
  await dark.context().close();

  const page = await newPage("light");
  await open(page);
  const bgLight = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  assert.equal(bgLight, "rgb(246, 248, 251)");
  await page.getByRole("button", { name: "Dark mode" }).click();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), "rgb(11, 17, 32)");
  await page.reload();
  await page.waitForSelector("body[data-ready=true]");
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  assert.equal(await page.locator("#theme-toggle").getAttribute("aria-pressed"), "true");
  await page.context().close();
});

test("auth endpoints return 429 with Retry-After after 10 attempts per minute", async () => {
  resetRateLimits();
  const page = await newPage("light");
  await open(page);
  const statuses = [];
  let last;
  for (let i = 0; i < 11; i++) {
    last = await page.request.post("/api/auth/login", {
      data: { email: "nobody@example.com", password: "wrong-pass-1" },
    });
    statuses.push(last.status());
  }
  assert.deepEqual(statuses, [...Array(10).fill(401), 429]);
  const retryAfter = Number(last.headers()["retry-after"]);
  assert.ok(retryAfter >= 1 && retryAfter <= 60, `Retry-After ${retryAfter}`);

  // The UI explains the limit.
  await page.locator("#login-email").fill("nobody@example.com");
  await page.locator("#login-password").fill("wrong-pass-1");
  await page.locator("#login-form").getByRole("button", { name: "Log in" }).click();
  await page.locator("#login-error").filter({ hasText: /Too many attempts/ }).waitFor();
  await page.context().close();
  resetRateLimits();
});
