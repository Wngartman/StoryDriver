const fs = require("fs");
const path = require("path");

const PLAYWRIGHT = "C:/Users/wngar/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright";
const { chromium } = require(PLAYWRIGHT);

const ROOT = "D:/StoryDriver";
const APP = "http://localhost:5173";
const API = "http://localhost:8001";
const BROWSER = process.env.STORYDRIVER_BROWSER_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const EVIDENCE = path.join(ROOT, "backend/data/logs/mobile_title_narration_evidence.json");
const VIEWPORTS = [
  { name: "iphone_15_pro_max", width: 430, height: 932 },
  { name: "narrow_phone", width: 390, height: 844 },
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
  const body = await response.text();
  return body ? JSON.parse(body) : null;
}

async function createFixture() {
  const story = await apiRequest("/sessions", {
    method: "POST",
    body: JSON.stringify({ title: "Untitled Story" }),
  });
  const response = await fetch(`${API}/sessions/${story.id}/generate-stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
    body: JSON.stringify({
      mode: "continue",
      director_note: "Write a brief synthetic opening. Adult cartographer Nessa Vale sorts three sealed maps at a kitchen table. Cover only two minutes. No visitor, no time skip, and do not open the maps.",
    }),
  });
  if (!response.ok) throw new Error(`Fixture generation failed: HTTP ${response.status} ${await response.text()}`);
  const events = (await response.text()).split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  const sceneEvent = events.findLast((event) => event.type === "scene");
  requireCheck(sceneEvent?.scene?.id, "Fixture generation returned no saved scene");
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    const session = await apiRequest(`/sessions/${story.id}`);
    const title = String(session?.title || "").trim();
    if (title && title !== "Untitled Story") {
      return { sessionId: story.id, sceneId: sceneEvent.scene.id, title };
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error(`Fixture ${story.id} did not receive an automatic title`);
}

async function deleteFixture(sessionId) {
  const result = await apiRequest(`/sessions/${sessionId}?permanent=true`, { method: "DELETE" });
  const jobId = result?.job_id || result?.id;
  if (!jobId) return result;
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    const job = await apiRequest(`/sessions/delete-jobs/${jobId}`);
    if (["completed", "failed", "cancelled"].includes(job.status)) return job;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return { status: "timeout", job_id: jobId };
}

async function inspectOverflow(page) {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const offenders = [...document.querySelectorAll("body *")]
      .filter((element) => {
        const style = getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden") return false;
        if (element.closest('.sd-workspace-background[aria-hidden="true"]')) return false;
        const rect = element.getBoundingClientRect();
        return rect.width > 1 && rect.height > 1 && (rect.left < -1 || rect.right > width + 1);
      })
      .slice(0, 8)
      .map((element) => ({
        tag: element.tagName,
        className: String(element.className).slice(0, 120),
        left: Math.round(element.getBoundingClientRect().left),
        right: Math.round(element.getBoundingClientRect().right),
      }));
    return {
      documentOverflow: document.documentElement.scrollWidth - width,
      bodyOverflow: document.body.scrollWidth - width,
      offenders,
    };
  });
}

async function main() {
  const ttsSettings = await fetch(`${API}/settings/tts`).then((response) => response.json());
  const customVoiceLibrary = await fetch(`${API}/tts/custom-voices`).then((response) => response.json());
  const selectedVoiceId = String(ttsSettings.tts_voice || "").replace(/^custom:/, "");
  const selectedVoice = (customVoiceLibrary.voices || []).find((voice) => voice.id === selectedVoiceId);
  requireCheck(ttsSettings.tts_provider === "high_quality_local", "Qwen must be selected for this regression");
  requireCheck(selectedVoice?.display_name, "The selected custom Qwen voice was not found");
  const expectedVoiceName = selectedVoice.display_name;
  const result = {
    status: "running",
    startedAt: new Date().toISOString(),
    selectedVoiceName: expectedVoiceName,
    viewports: [],
  };
  let fixture = null;
  let browser;
  try {
    fixture = await createFixture();
    result.fixtureSessionId = fixture.sessionId;
    browser = await chromium.launch({ headless: true, executablePath: BROWSER });
    for (const viewport of VIEWPORTS) {
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } });
      const page = await context.newPage();
      await page.goto(APP, { waitUntil: "domcontentloaded", timeout: 60000 });
      await page.getByRole("textbox").first().waitFor({ state: "visible", timeout: 30000 });
      await page.waitForTimeout(1000);

      await page.getByRole("button", { name: "Open stories" }).click();
      const fixtureButton = page.locator(".sd-sidebar-mobile button").filter({ hasText: fixture.title });
      await fixtureButton.first().waitFor({ state: "visible", timeout: 30000 });
      requireCheck(await fixtureButton.count() === 1, `Expected one sidebar entry for ${fixture.title}`);
      await fixtureButton.click();
      await page.getByRole("button", { name: "Close sidebar" }).waitFor({ state: "hidden" });
      await page.waitForFunction((expectedTitle) => {
        const titleButton = document.querySelector('button[aria-label="Current story title"]');
        const visibleTitle = titleButton?.textContent?.trim() || "";
        return visibleTitle === expectedTitle && document.title.startsWith(`${expectedTitle} |`);
      }, fixture.title, { timeout: 30000 });
      const liveTitle = await page.getByRole("button", { name: "Current story title" }).innerText();

      const initial = await inspectOverflow(page);
      requireCheck(initial.documentOverflow <= 1 && initial.bodyOverflow <= 1 && initial.offenders.length === 0,
        `${viewport.name} initial horizontal overflow: ${JSON.stringify(initial)}`);

      await page.getByRole("button", { name: "Open stories" }).click();
      await page.getByRole("button", { name: "Close sidebar" }).waitFor({ state: "visible" });
      await page.waitForFunction(() => {
        const drawer = document.querySelector(".sd-sidebar-mobile");
        if (!drawer) return false;
        const rect = drawer.getBoundingClientRect();
        return rect.left >= -1 && rect.right <= document.documentElement.clientWidth + 1;
      });
      const sidebar = await inspectOverflow(page);
      requireCheck(sidebar.documentOverflow <= 1 && sidebar.bodyOverflow <= 1 && sidebar.offenders.length === 0,
        `${viewport.name} sidebar horizontal overflow: ${JSON.stringify(sidebar)}`);

      const bodyText = await page.locator("body").innerText();
      requireCheck(!bodyText.includes("High Quality") && !bodyText.includes("High quality local"),
        `${viewport.name} exposed a generic TTS provider label`);

      await page.mouse.click(viewport.width - 8, Math.floor(viewport.height / 2));
      await page.getByRole("button", { name: "Close sidebar" }).waitFor({ state: "hidden" });
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      const settings = page.locator('aside[aria-label="StoryDriver settings"]');
      await settings.waitFor({ state: "visible" });
      const settingsText = await settings.innerText();
      requireCheck(settingsText.includes("Qwen3-TTS 0.6B"), `${viewport.name} settings did not expose the Qwen product name`);
      requireCheck(!settingsText.includes("High Quality") && !settingsText.includes("High quality local"),
        `${viewport.name} settings exposed a generic TTS provider label`);
      const settingsOverflow = await inspectOverflow(page);
      requireCheck(settingsOverflow.documentOverflow <= 1 && settingsOverflow.bodyOverflow <= 1 && settingsOverflow.offenders.length === 0,
        `${viewport.name} settings horizontal overflow: ${JSON.stringify(settingsOverflow)}`);
      await page.getByRole("button", { name: "Close settings" }).click();
      await settings.waitFor({ state: "hidden" });

      let effectiveNarration = null;
      if (viewport.name === "iphone_15_pro_max") {
        const narrate = page.getByRole("button", { name: "Narrate", exact: true });
        requireCheck(await narrate.count() === 1, "Expected exactly one Narrate button");
        await narrate.click();
        await page.waitForFunction(() => {
          const labels = [...document.querySelectorAll('[title^="Effective narration:"]')]
            .map((element) => element.getAttribute("title") || "");
          return labels.some((label) => label.includes("Qwen3-TTS 0.6B Base"));
        }, null, { timeout: 60000 });
        effectiveNarration = await page.locator('[title^="Effective narration:"]').getAttribute("title");
        requireCheck(effectiveNarration.includes(expectedVoiceName),
          `Effective narration label did not use selected voice ${expectedVoiceName}: ${effectiveNarration}`);
        await page.getByRole("button", { name: "Stop narration" }).click();
      }

      result.viewports.push({ ...viewport, liveTitle, initial, sidebar, settingsOverflow, qwenLabelVisible: true, effectiveNarration });
      await context.close();
    }
    result.status = "passed";
    result.completedAt = new Date().toISOString();
    fs.writeFileSync(EVIDENCE, `${JSON.stringify(result, null, 2)}\n`);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } catch (error) {
    result.status = "failed";
    result.error = error.stack || String(error);
    fs.writeFileSync(EVIDENCE, `${JSON.stringify(result, null, 2)}\n`);
    throw error;
  } finally {
    if (browser) await browser.close();
    if (fixture?.sessionId) {
      result.fixtureCleanup = await deleteFixture(fixture.sessionId).catch((error) => ({ status: "failed", error: String(error) }));
      fs.writeFileSync(EVIDENCE, `${JSON.stringify(result, null, 2)}\n`);
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
