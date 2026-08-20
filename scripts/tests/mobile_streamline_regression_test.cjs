const fs = require("fs");
const path = require("path");

const PLAYWRIGHT = "C:/Users/wngar/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright";
const { chromium } = require(PLAYWRIGHT);
const BROWSER = process.env.STORYDRIVER_BROWSER_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";

const ROOT = "D:/StoryDriver";
const APP = "http://localhost:5173";
const API = "http://localhost:8001";
const OUT = path.join(ROOT, "backend/data/logs/screenshots");
const METRICS = path.join(ROOT, "backend/data/logs/MOBILE_STREAMLINE_LATEST.json");
const VIEWPORTS = [
  { name: "iphone_15_pro_max", width: 430, height: 932 },
  { name: "narrow_phone", width: 390, height: 844 },
  { name: "narrow_landscape", width: 844, height: 390 },
  { name: "desktop", width: 1440, height: 1000 },
];
const FORBIDDEN = [
  "Images paused",
  "Generate Image",
  "Regenerate Image",
  "Scene QA Checklist",
  "Ready for the next director note",
  "[inhales]",
  "[breathes deeply]",
  "[soft breath]",
  "[gasps]",
];

function requireCheck(condition, message) {
  if (!condition) throw new Error(message);
}

async function apiRequest(route, options = {}) {
  const response = await fetch(`${API}${route}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) throw new Error(`${options.method || "GET"} ${route}: HTTP ${response.status} ${await response.text()}`);
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

async function createFixture() {
  const story = await apiRequest("/sessions", {
    method: "POST",
    body: JSON.stringify({ title: "MOBILE STREAMLINE DISPOSABLE" }),
  });
  const response = await fetch(`${API}/sessions/${story.id}/generate-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
    body: JSON.stringify({
      mode: "continue",
      director_note: "Write a brief quiet moment in a modern apartment. Adult roommates Mara and June exchange a brass key at the kitchen table, then stop before either leaves the room.",
    }),
  });
  if (!response.ok) throw new Error(`fixture generation failed: HTTP ${response.status} ${await response.text()}`);
  const events = (await response.text()).split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  const sceneEvent = events.findLast((event) => event.type === "scene");
  requireCheck(sceneEvent?.scene?.id, "fixture generation returned no saved scene");
  return { sessionId: story.id, sceneId: sceneEvent.scene.id };
}

async function deleteFixture(sessionId) {
  const result = await apiRequest(`/sessions/${sessionId}?permanent=true`, { method: "DELETE" });
  const jobId = result?.job_id || result?.id;
  if (!jobId) return result;
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    const job = await apiRequest(`/sessions/delete-jobs/${jobId}`);
    if (["completed", "failed", "cancelled"].includes(job.status)) return job;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return { status: "timeout", job_id: jobId };
}

async function assertNoOverflow(page, label) {
  const result = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const offenders = [...document.querySelectorAll("body *")]
      .filter((element) => {
        const style = getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) return false;
        const rect = element.getBoundingClientRect();
        return rect.width > 1 && rect.height > 1 && (rect.left < -1 || rect.right > width + 1);
      })
      .slice(0, 8)
      .map((element) => ({ tag: element.tagName, className: String(element.className).slice(0, 120), rect: element.getBoundingClientRect().toJSON() }));
    return {
      documentOverflow: document.documentElement.scrollWidth - width,
      bodyOverflow: document.body.scrollWidth - width,
      offenders,
    };
  });
  requireCheck(result.documentOverflow <= 1 && result.bodyOverflow <= 1 && result.offenders.length === 0, `${label} horizontal overflow: ${JSON.stringify(result)}`);
  return result;
}

async function inspectPwa(page) {
  const manifestHref = await page.locator('link[rel="manifest"]').getAttribute("href");
  requireCheck(manifestHref, "PWA manifest link is missing");
  const manifestResponse = await page.request.get(new URL(manifestHref, APP).href);
  requireCheck(manifestResponse.ok(), `PWA manifest failed: ${manifestResponse.status()}`);
  const manifest = await manifestResponse.json();
  requireCheck(Array.isArray(manifest.icons) && manifest.icons.length > 0, "PWA manifest has no icons");
  for (const icon of manifest.icons) {
    const response = await page.request.get(new URL(icon.src, APP).href);
    requireCheck(response.ok(), `PWA icon failed: ${icon.src} HTTP ${response.status()}`);
  }
  return { manifest: manifestHref, iconCount: manifest.icons.length };
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const result = { startedAt: new Date().toISOString(), viewports: [], errors: [], screenshots: [], fixture: null };
  let fixture = null;
  let browser = null;
  try {
    fixture = await createFixture();
    result.fixture = fixture;
    browser = await chromium.launch({
      headless: true,
      executablePath: BROWSER,
      args: ["--autoplay-policy=no-user-gesture-required"],
    });
    for (const viewport of VIEWPORTS) {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, reducedMotion: "reduce" });
      const page = await context.newPage();
      const ttsDebug = [];
      page.on("console", (message) => {
        const value = message.text();
        if (value.includes("[StoryDriver TTS]")) ttsDebug.push(value);
      });
      await page.goto(APP, { waitUntil: "domcontentloaded", timeout: 60000 });
      await page.getByRole("textbox").first().waitFor({ state: "visible", timeout: 30000 });
      await page.waitForTimeout(1200);
      const bodyText = await page.locator("body").innerText();
      for (const text of FORBIDDEN) requireCheck(!bodyText.includes(text), `${viewport.name} exposed retired text: ${text}`);

      const textarea = page.locator("textarea").first();
      const fontSize = Number.parseFloat(await textarea.evaluate((element) => getComputedStyle(element).fontSize));
      if (viewport.width < 768) requireCheck(fontSize >= 16, `${viewport.name} composer font ${fontSize}px can trigger input zoom`);
      const touchTargets = await page.locator('button[aria-label="Open stories"], button[aria-label="Settings"], button[aria-label="Send direction"]').evaluateAll((buttons) => buttons
        .map((button) => {
          const rect = button.getBoundingClientRect();
          return { label: button.getAttribute("aria-label"), width: rect.width, height: rect.height };
        })
        .filter((target) => target.width > 0 && target.height > 0));
      requireCheck(touchTargets.every((target) => target.width >= 40 && target.height >= 40), `${viewport.name} undersized primary touch target: ${JSON.stringify(touchTargets)}`);

      if (viewport.width < 1024) {
        await page.getByRole("button", { name: "Open stories" }).click();
        await page.getByRole("button", { name: "Close sidebar" }).waitFor({ state: "visible" });
        await page.waitForFunction(() => {
          const drawer = document.querySelector(".sd-sidebar-mobile");
          if (!drawer) return false;
          const rect = drawer.getBoundingClientRect();
          return rect.left >= -1 && rect.right <= document.documentElement.clientWidth + 1;
        });
        await assertNoOverflow(page, `${viewport.name} sidebar`);
        await page.mouse.click(viewport.width - 8, Math.floor(viewport.height / 2));
        await page.getByRole("button", { name: "Close sidebar" }).waitFor({ state: "hidden" });
      }

      await page.getByRole("button", { name: "Settings", exact: true }).click();
      const settingsDrawer = page.locator('aside[aria-label="StoryDriver settings"]');
      await settingsDrawer.waitFor({ state: "visible" });
      const breathingControl = page.getByRole("group", { name: "Breathing" });
      await breathingControl.scrollIntoViewIfNeeded();
      await breathingControl.waitFor({ state: "visible" });
      const settingsText = await settingsDrawer.innerText();
      for (const text of FORBIDDEN) requireCheck(!settingsText.includes(text), `${viewport.name} settings exposed retired text: ${text}`);
      for (const mode of ["Off", "Natural", "Cinematic"]) {
        requireCheck(
          await breathingControl.getByRole("button", { name: mode, exact: true }).isVisible(),
          `${viewport.name} ${mode} breathing control is not visible`,
        );
      }
      await assertNoOverflow(page, `${viewport.name} settings`);
      await page.getByRole("button", { name: "Close settings" }).click();
      await settingsDrawer.waitFor({ state: "hidden" });

      if (viewport.name === "iphone_15_pro_max") {
        const pwa = await inspectPwa(page);
        result.pwa = pwa;
        await page.getByRole("button", { name: "Narrate" }).first().click();
        await page.getByRole("button", { name: "Back 10 seconds" }).waitFor({ state: "visible", timeout: 180000 });
        await page.waitForFunction(() => {
          const scrubber = document.querySelector('input[aria-label="Narration position"]');
          return scrubber && !scrubber.disabled && Number(scrubber.max) > Number(scrubber.min);
        }, null, { timeout: 180000 });
        await page.getByRole("button", { name: "Back 10 seconds" }).click();
        await page.getByRole("button", { name: "Forward 10 seconds" }).click();
        await page.waitForTimeout(750);
        await page.getByRole("button", { name: "Narration options" }).click();
        await assertNoOverflow(page, `${viewport.name} narration`);
        const cursorBeforeReload = await page.evaluate((sessionId) => {
          const key = Object.keys(localStorage).find((candidate) =>
            candidate.startsWith(`storydriver_tts_cursor::${sessionId}:`));
          return key ? { key, value: JSON.parse(localStorage.getItem(key)) } : null;
        }, fixture.sessionId);
        requireCheck(cursorBeforeReload?.value?.globalNarrationTime > 0, "Narration cursor was not persisted before reload");

        await page.evaluate(() => localStorage.setItem("storydriver_tts_debug", "1"));
        await page.reload({ waitUntil: "domcontentloaded" });
        await page.getByRole("textbox").first().waitFor({ state: "visible", timeout: 30000 });
        const cursorAfterReload = await page.evaluate((key) => JSON.parse(localStorage.getItem(key) || "null"), cursorBeforeReload.key);
        requireCheck(cursorAfterReload?.globalNarrationTime >= cursorBeforeReload.value.globalNarrationTime - 1,
          "Narration cursor did not survive page reload");
        await page.getByRole("button", { name: "Narrate" }).first().click();
        await page.getByRole("button", { name: "Back 10 seconds" }).waitFor({ state: "visible", timeout: 180000 });
        const resumeSamples = [];
        for (let sampleIndex = 0; sampleIndex < 16; sampleIndex += 1) {
          await page.waitForTimeout(250);
          resumeSamples.push(Number(await page.locator('input[aria-label="Narration position"]').inputValue()));
        }
        const resumedAt = resumeSamples.at(-1);
        const stableResume = resumeSamples.slice(-4).every(
          (value) => value >= cursorBeforeReload.value.globalNarrationTime - 2,
        );
        requireCheck(stableResume,
          `Narration resumed too far behind saved cursor: ${JSON.stringify({ saved: cursorBeforeReload.value, resumeSamples, ttsDebug })}`);
        await page.evaluate(() => localStorage.removeItem("storydriver_tts_debug"));
        result.narration = {
          rewindVisible: true,
          forwardVisible: true,
          generatedSeekEnabled: true,
          optionsExpandable: true,
          cursorPersistedOnReload: true,
          savedCursorSeconds: cursorBeforeReload.value.globalNarrationTime,
          resumedAtSeconds: resumedAt,
        };
        await page.getByRole("button", { name: "Stop narration" }).click();
      }

      const overflow = await assertNoOverflow(page, viewport.name);
      const screenshot = path.join(OUT, `qwen_streamline_${viewport.name}.png`);
      await page.screenshot({ path: screenshot, fullPage: false });
      result.screenshots.push(screenshot);
      result.viewports.push({ ...viewport, composerFontPx: fontSize, touchTargets, overflow, passed: true });
      await context.close();
    }
  } catch (error) {
    result.errors.push(`${error.name}: ${error.message}`);
  } finally {
    if (browser) await browser.close();
    if (fixture?.sessionId) result.deletion = await deleteFixture(fixture.sessionId);
    result.finishedAt = new Date().toISOString();
    result.passed = result.errors.length === 0 && result.viewports.length === VIEWPORTS.length && result.deletion?.status === "completed";
    fs.writeFileSync(METRICS, JSON.stringify(result, null, 2));
  }
  console.log(result.passed ? "PASS: mobile/desktop layouts, drawers, PWA assets, and generated-audio narration controls." : "FAIL");
  for (const error of result.errors) console.log(`  ${error}`);
  console.log(`Metrics: ${METRICS}`);
  process.exitCode = result.passed ? 0 : 1;
}

main();
