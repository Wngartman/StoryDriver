const PLAYWRIGHT = "C:/Users/wngar/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright";
const { chromium } = require(PLAYWRIGHT);

const BROWSER = process.env.STORYDRIVER_BROWSER_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const port = Number(process.argv[2]);
const timeoutMs = Number(process.argv[3] || 30 * 60 * 1000);

if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error("Usage: node qwen_continuous_browser_driver.cjs <port> [timeout-ms]");
}

const baseUrl = `http://127.0.0.1:${port}`;

async function waitForServer() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/manifest`, { cache: "no-store" });
      if (response.ok) return;
    } catch {
      // The local acceptance server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Continuous playback server did not start on port ${port}.`);
}

async function main() {
  await waitForServer();
  const browser = await chromium.launch({
    executablePath: BROWSER,
    headless: true,
    args: [
      "--autoplay-policy=no-user-gesture-required",
      "--disable-background-timer-throttling",
      "--disable-backgrounding-occluded-windows",
      "--disable-renderer-backgrounding",
    ],
  });
  try {
    const page = await browser.newPage();
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    const resultPromise = new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error(`Continuous playback result timed out on port ${port}.`)),
        timeoutMs,
      );
      page.on("request", (request) => {
        if (request.method() !== "POST" || request.url() !== `${baseUrl}/result`) return;
        try {
          const result = JSON.parse(request.postData() || "{}");
          clearTimeout(timer);
          resolve(result);
        } catch (error) {
          clearTimeout(timer);
          reject(error);
        }
      });
    });
    await page.getByRole("button", { name: "Start real-time test", exact: true }).click();
    const result = await resultPromise;
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    if (result.status !== "complete") process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error?.stack || String(error));
  process.exitCode = 1;
});
