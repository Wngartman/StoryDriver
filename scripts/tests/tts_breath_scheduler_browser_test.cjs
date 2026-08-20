const { spawn } = require("child_process");

const PLAYWRIGHT = "C:/Users/wngar/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright";
const { chromium } = require(PLAYWRIGHT);
const BROWSER = process.env.STORYDRIVER_BROWSER_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const DEV_URL = "http://127.0.0.1:5178";

function requireCheck(condition, message) {
  if (!condition) throw new Error(message);
}

function breathDataUrl(duration = 0.72, rate = 24000) {
  const frames = Math.floor(duration * rate);
  const buffer = Buffer.alloc(44 + frames * 2);
  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + frames * 2, 4);
  buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(rate, 24);
  buffer.writeUInt32LE(rate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(frames * 2, 40);
  let state = 0x12345678;
  for (let index = 0; index < frames; index += 1) {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    const noise = ((state / 0xffffffff) * 2) - 1;
    const envelope = Math.max(0, Math.min(1, index / (rate * 0.08), (frames - index) / (rate * 0.16)));
    buffer.writeInt16LE(Math.round(noise * envelope * 2800), 44 + index * 2);
  }
  return `data:audio/wav;base64,${buffer.toString("base64")}`;
}

async function waitForServer(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // Vite is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("Temporary Vite test server did not start.");
}

async function main() {
  const vite = spawn(
    process.execPath,
    ["D:/StoryDriver/frontend/node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "5178", "--strictPort"],
    { cwd: "D:/StoryDriver/frontend", windowsHide: true, stdio: "ignore" },
  );
  let browser;
  try {
    await waitForServer(DEV_URL);
    browser = await chromium.launch({
      executablePath: BROWSER,
      headless: true,
      args: ["--autoplay-policy=no-user-gesture-required"],
    });
    const page = await browser.newPage({ viewport: { width: 430, height: 932 } });
    await page.goto(DEV_URL, { waitUntil: "domcontentloaded" });
    const result = await page.evaluate(async (url) => {
      const { ttsBreathScheduler } = await import("/src/services/ttsBreathScheduler.js");
      const { ttsController } = await import("/src/services/ttsController.js");
      const primed = await ttsBreathScheduler.prime();
      const buffer = await ttsBreathScheduler.preload(url);
      const progress = [];
      const completion = ttsBreathScheduler.play({
        url,
        chunkIndex: 3,
        phase: "breath_before",
        event: "shaky_inhale",
        onProgress: (snapshot) => progress.push(snapshot?.elapsed || 0),
      });
      const deadline = performance.now() + 2500;
      while ((ttsBreathScheduler.snapshot()?.elapsed || 0) < 0.08 && performance.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
      const beforePause = ttsBreathScheduler.snapshot();
      const paused = ttsBreathScheduler.pause();
      const pausedAt = ttsBreathScheduler.snapshot();
      await new Promise((resolve) => setTimeout(resolve, 160));
      const stillPaused = ttsBreathScheduler.snapshot();
      const resumed = await ttsBreathScheduler.resume();
      const completed = await completion;
      const cursorText = "Cursor compatibility check.";
      let hash = 2166136261;
      for (let index = 0; index < cursorText.length; index += 1) {
        hash ^= cursorText.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      const textHash = `ui-${(hash >>> 0).toString(16)}-${cursorText.length}`;
      const cursorKey = [
        "storydriver_tts_cursor:",
        "cursor-session",
        "cursor-scene",
        "cursor-version",
        "default",
        "0.95",
        "natural",
        textHash,
      ].join(":");
      localStorage.setItem(cursorKey, JSON.stringify({
        sessionId: "cursor-session",
        sceneId: "cursor-scene",
        versionId: "cursor-version",
        provider: "kokoro",
        voice: "default",
        speed: 0.95,
        breathingMode: "natural",
        textHash,
        chunkIndex: 2,
        chunkCurrentTime: 1.25,
        globalNarrationTime: 14.5,
        updatedAtMs: Date.now(),
        completed: false,
        stopped: false,
      }));
      const compatibleCursor = ttsController.getSavedCursor({
        text: cursorText,
        provider: "kokoro",
        speed: 0.75,
        voice: "af_aoede",
        voiceProfileId: "natural_female_narrator",
        breathingMode: "cinematic",
        sessionId: "cursor-session",
        sceneId: "cursor-scene",
        versionId: "cursor-version",
      });
      localStorage.removeItem(cursorKey);
      return {
        primed,
        decodedDuration: buffer?.duration || 0,
        beforePause,
        paused,
        pausedAt,
        stillPaused,
        resumed,
        completed,
        activeAfter: ttsBreathScheduler.snapshot(),
        progressSamples: progress.length,
        maxProgress: Math.max(...progress),
        compatibleCursor,
      };
    }, breathDataUrl());
    requireCheck(result.primed, "Web Audio context did not prime.");
    requireCheck(result.decodedDuration > 0.65 && result.decodedDuration < 0.8, "Breath did not decode to the expected duration.");
    requireCheck(result.beforePause?.phase === "breath_before", "Breath phase was not exposed to cursor progress.");
    requireCheck(result.beforePause?.elapsed > 0.07, "Breath progress did not advance before pause.");
    requireCheck(result.paused && result.pausedAt?.paused, "Breath pause did not hold the performance element.");
    requireCheck(Math.abs(result.stillPaused.elapsed - result.pausedAt.elapsed) < 0.03, "Paused breath continued advancing.");
    requireCheck(result.resumed && result.completed, "Breath did not resume and complete.");
    requireCheck(result.activeAfter === null, "Completed breath remained active.");
    requireCheck(result.progressSamples >= 4 && result.maxProgress >= result.decodedDuration - 0.03, "Breath progress did not cover the decoded duration.");
    requireCheck(result.compatibleCursor?.globalNarrationTime === 14.5, "Cursor did not survive voice, speed, and breathing-mode identity changes.");
    console.log(JSON.stringify({ status: "pass", ...result }, null, 2));
  } finally {
    if (browser) await browser.close();
    vite.kill();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});
