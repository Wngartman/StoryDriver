const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const out = path.join(root, '.tools/release-test-data');
const continuous = process.argv.includes('--continuous');
(async () => {
  const context = await chromium.launchPersistentContext(path.join(root, continuous ? '.tools/continuous-browser-v2' : '.tools/playback-browser'), {
    executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true, viewport: { width: 1440, height: 1000 },
    args: ['--autoplay-policy=no-user-gesture-required', '--disable-background-networking'],
  });
  await context.addInitScript(() => {
    const OriginalAudio = window.Audio;
    window.__audios = [];
    window.__audioEvents = [];
    window.Audio = function(...args) {
      const audio = new OriginalAudio(...args);
      window.__audios.push(audio);
      for (const type of ['playing', 'ended', 'error']) audio.addEventListener(type, () => {
        window.__audioEvents.push({ type, at: performance.now(), src: audio.currentSrc, time: audio.currentTime, duration: audio.duration });
      });
      return audio;
    };
    window.Audio.prototype = OriginalAudio.prototype;
  });
  const page = await context.newPage();
  const errors = [];
  const chunks = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', async response => {
    if (response.url().endsWith('/tts/synthesize') && response.ok()) {
      try { chunks.push(await response.json()); } catch {}
    }
  });
  try {
    await page.goto('http://127.0.0.1:8001', { waitUntil: 'networkidle' });
    if (continuous) await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith('storydriver_tts_cursor')).forEach(key => localStorage.removeItem(key)));
    await page.getByText('RELEASE TEST - narration', { exact: true }).first().click();
    const started = Date.now();
    await page.getByRole('button', { name: 'Narrate', exact: true }).first().click();
    await page.waitForFunction(() => window.__audios.some(a => !a.paused && a.currentTime > 0.2), { timeout: 60000 });
    const firstAudioSeconds = (Date.now() - started) / 1000;
    await page.waitForTimeout(3000);
    await page.getByRole('button', { name: 'Pause narration', exact: true }).click();
    const paused = await page.evaluate(() => window.__audios.every(a => a.paused));
    if (!paused) throw Error('Pause did not pause audio');
    const cursor = await page.evaluate(() => Object.entries(localStorage).filter(([k]) => k.startsWith('storydriver_tts_cursor')).map(([,v]) => JSON.parse(v)).sort((a,b) => b.updatedAtMs-a.updatedAtMs)[0]);
    if (!cursor || cursor.globalNarrationTime < 1) throw Error('Playback cursor not persisted');
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Narrate', exact: true }).first().click();
    await page.waitForFunction(() => window.__audios.some(a => !a.paused && a.currentTime > 0.2), { timeout: 60000 });
    const resumed = await page.evaluate(() => window.__audios.find(a => !a.paused)?.currentTime);
    if (resumed < 1) throw Error('Reload resume restarted from zero');
    await page.getByRole('button', { name: 'Forward 10 seconds', exact: true }).click();
    await page.waitForTimeout(1000);
    await page.getByRole('button', { name: 'Back 10 seconds', exact: true }).click();
    await page.setViewportSize({ width: 430, height: 932 });
    await page.screenshot({ path: path.join(out, 'mobile-player.png') });
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw Error('Player overflow');
    if (continuous) {
      await page.waitForFunction(count => window.__audioEvents.filter(event => event.type === 'ended').length >= count
        && window.__audios.every(audio => audio.paused)
        && !Object.keys(localStorage).some(key => key.startsWith('storydriver_tts_cursor')), cursor.chunkCount, { timeout: 900000 });
    } else for (let index = 0; index < 24; index++) {
      await page.waitForFunction(() => window.__audios.some(a => !a.paused && Number.isFinite(a.duration) && a.duration > 1), { timeout: 60000 });
      await page.waitForTimeout(1600);
      const before = await page.evaluate(() => window.__audioEvents.filter(e => e.type === 'ended').length);
      await page.evaluate(() => { const a = window.__audios.find(a => !a.paused && Number.isFinite(a.duration) && a.duration > 1); if (a) a.currentTime = Math.max(0, a.duration - 0.15); });
      await page.waitForFunction(count => window.__audioEvents.filter(e => e.type === 'ended').length > count, before, { timeout: 60000 });
    }
    if (!continuous) await page.getByRole('button', { name: 'Pause narration', exact: true }).click();
    const events = await page.evaluate(() => window.__audioEvents);
    const gaps = [];
    for (let i = 0; i < events.length; i++) if (events[i].type === 'ended') {
      const next = events.slice(i+1).find(e => e.type === 'playing');
      if (next) gaps.push((next.at - events[i].at) / 1000);
    }
    if (errors.length || events.some(e => e.type === 'error')) throw Error(JSON.stringify({errors, audioErrors: events.filter(e => e.type === 'error')}));
    fs.writeFileSync(path.join(out, continuous ? 'continuous-playback-evidence.json' : 'playback-evidence.json'), JSON.stringify({ firstAudioSeconds, pause: true, reloadResumeSeconds: resumed, cursor, transitions: gaps.length, gaps, chunks, errors, acceleratedBoundaryTest: !continuous, wallSeconds: (Date.now()-started)/1000 }, null, 2));
    console.log(JSON.stringify({ firstAudioSeconds, reloadResumeSeconds: resumed, transitions: gaps.length, maxGap: Math.max(...gaps), cachedResponses: chunks.filter(c => c.cached || c.cache_hit).length }));
  } finally { await context.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
