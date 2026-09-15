const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.tools/release-test-data');
(async () => {
  const context = await chromium.launchPersistentContext(path.join(root, '.tools/release-browser'), {
    executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, viewport: { width: 1440, height: 1000 },
    args: ['--autoplay-policy=no-user-gesture-required', '--disable-background-networking'],
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto('http://127.0.0.1:8001', { waitUntil: 'networkidle' });
    await page.getByLabel('Settings', { exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'StoryDriver settings' });
    await dialog.waitFor();
    const original = await (await page.request.get('http://127.0.0.1:8001/settings/model')).json();
    try {
      await dialog.getByText('System Prompt', { exact: true }).click();
      const prompt = 'Synthetic system prompt persistence check. '.repeat(800);
      await dialog.locator('textarea:visible').first().fill(prompt);
      await page.getByRole('button', { name: 'Close settings', exact: true }).click();
      const saved = await (await page.request.get('http://127.0.0.1:8001/settings/model')).json();
      if (saved.system_prompt !== prompt) throw Error('Immediate-close prompt save lost text');
    } finally {
      await page.request.put('http://127.0.0.1:8001/settings/model', { data: original });
    }
    await page.getByLabel('Settings', { exact: true }).click();
    for (const category of ['Writing', 'Narration', 'Appearance', 'App']) {
      await dialog.getByRole('navigation', { name: 'Settings categories' }).getByRole('button', { name: category, exact: true }).click();
      await page.waitForTimeout(500);
      await page.screenshot({ path: path.join(output, `desktop-${category.toLowerCase()}.png`) });
    }
    await page.getByRole('button', { name: 'Close settings', exact: true }).click();
    await page.screenshot({ path: path.join(output, 'desktop-workspace.png') });
    await page.setViewportSize({ width: 430, height: 932 });
    await page.screenshot({ path: path.join(output, 'mobile-workspace.png') });
    await page.getByLabel('Settings', { exact: true }).click();
    for (const category of ['Writing', 'Narration', 'Appearance', 'App']) {
      await dialog.getByRole('navigation', { name: 'Settings categories' }).getByRole('button', { name: category, exact: true }).click();
      await page.waitForTimeout(300);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      if (overflow) throw new Error(`Mobile horizontal overflow: ${category}`);
      await page.screenshot({ path: path.join(output, `mobile-${category.toLowerCase()}.png`) });
    }
    if (errors.length) throw new Error(errors.join('\n'));
    fs.writeFileSync(path.join(output, 'ui-evidence.json'), JSON.stringify({ desktop: '1440x1000', mobile: '430x932', categories: 4, pageErrors: errors, overflow: false }, null, 2));
    console.log('PASS: desktop/mobile settings, no page errors or horizontal overflow.');
  } finally { await context.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
