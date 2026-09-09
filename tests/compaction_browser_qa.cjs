// Optional local-only browser QA; uses an ephemeral headless browser profile.
// Supply Playwright through NODE_PATH; never downloads a browser or assets.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const out = path.resolve(__dirname, '../build/compaction-qa');
  fs.mkdirSync(out, { recursive: true });
  const options = { headless: true };
  if (process.env.MESHCOMPACT_CHROME) options.executablePath = process.env.MESHCOMPACT_CHROME;
  const browser = await chromium.launch(options);
  try {
    const page = await browser.newPage();
    const errors = [];
    const foreign = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://127.0.0.1:8766') {
        foreign.push(url.href);
        return route.abort();
      }
      return route.continue();
    });
    const checks = [];
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.goto('http://127.0.0.1:8766/compaction.html', { waitUntil: 'networkidle' });
      assert.equal(await page.title(), '先筛选，再搬运 — MeshCompact 实验笔记');
      if (width === 390) {
        await page.screenshot({ path: path.join(out, 'mobile-first-screen.png') });
        await page.locator('.direction-sheet').screenshot({ path: path.join(out, 'mobile-flow.png') });
      }
      const sizing = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
        touch: [...document.querySelectorAll('nav a, summary, .compact-next')].map(item => ({
          text: item.textContent.trim(), height: item.getBoundingClientRect().height,
        })),
      }));
      assert(sizing.scroll <= sizing.viewport, `horizontal overflow at ${width}`);
      assert(sizing.touch.every(item => item.height >= 44), `touch target shorter than 44px at ${width}`);
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => document.activeElement.className), 'skip-link');
      const focus = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
      assert.notEqual(focus, 'none');
      await page.keyboard.press('Enter');
      assert.equal(new URL(page.url()).hash, '#main-content');
      await page.getByText('为什么不用把数据往右搬？', { exact: true }).click();
      assert(await page.locator('details').first().evaluate(item => item.open));
      await page.screenshot({ path: path.join(out, `chapter-${width}.png`), fullPage: true });
      checks.push({ width, noOverflow: true, touchTargets: true, skipLink: true, details: true });
    }
    assert.deepEqual(foreign, [], 'external resource request');
    assert.deepEqual(errors, [], 'browser console/page errors');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('http://127.0.0.1:8766/', { waitUntil: 'networkidle' });
    await page.getByRole('link', { name: '第二个算法：稳定筛选 →', exact: true }).click();
    assert(new URL(page.url()).pathname.endsWith('/compaction.html'));
    const record = { execution: 'headless browser, isolated profile, local teaching page only',
      checks, externalRequests: foreign, errors, navigation: true };
    fs.writeFileSync(path.join(out, 'qa.json'), JSON.stringify(record, null, 2));
    console.log(JSON.stringify(record));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
