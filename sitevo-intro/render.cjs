#!/usr/bin/env node
/*
 * Frame-accurate renderer for the Sitevo intro.
 *   node render.cjs stills 1.2,3.5,9.4 [outDir]   -> PNG stills at given times
 *   node render.cjs frames [fps] [outDir]          -> every frame as JPEG (parallel workers)
 */
const path = require('path');
const fs = require('fs');
let pw;
try { pw = require('playwright'); } catch (e) { pw = require('/opt/node22/lib/node_modules/playwright'); }

const DUR = 30;
const W = 1920, H = 1080;
const page_url = 'file://' + path.join(__dirname, 'index.html') + '?render=1';
const exe = fs.existsSync('/opt/pw-browsers/chromium') ? undefined : undefined;

async function openPage(browser) {
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on('pageerror', e => console.error('PAGE ERROR', e.message));
  page.on('console', m => { if (m.type() === 'error') console.error('console:', m.text()); });
  await page.goto(page_url);
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 30000 });
  return page;
}

async function main() {
  const [mode = 'stills', arg = '', outArg] = process.argv.slice(2);
  const browser = await pw.chromium.launch({ args: ['--disable-lcd-text', '--font-render-hinting=none', '--force-color-profile=srgb'] });
  if (mode === 'stills') {
    const out = outArg || path.join(__dirname, 'out', 'stills');
    fs.mkdirSync(out, { recursive: true });
    const page = await openPage(browser);
    for (const ts of arg.split(',').map(Number)) {
      await page.evaluate(t => window.renderAt(t), ts);
      await page.screenshot({ path: path.join(out, `t${ts.toFixed(2).padStart(5, '0')}.png`) });
    }
    console.log('stills ->', out);
  } else if (mode === 'frames') {
    const fps = Number(arg || 60);
    const out = outArg || path.join(__dirname, 'out', 'frames');
    fs.mkdirSync(out, { recursive: true });
    const total = Math.round(DUR * fps);
    const workers = Number(process.env.WORKERS || 4);
    let next = 0, done = 0;
    const t0 = Date.now();
    await Promise.all(Array.from({ length: workers }, async () => {
      const page = await openPage(browser);
      while (true) {
        const i = next++;
        if (i >= total) break;
        const t = i / fps;
        await page.evaluate(tt => window.renderAt(tt), t);
        await page.screenshot({ path: path.join(out, `f${String(i).padStart(5, '0')}.jpg`), type: 'jpeg', quality: 95 });
        done++;
        if (done % 60 === 0) {
          const el = (Date.now() - t0) / 1000;
          console.log(`${done}/${total} frames  ${(done / el).toFixed(1)} fps  eta ${((total - done) / (done / el)).toFixed(0)}s`);
        }
      }
    }));
    console.log('frames ->', out);
  }
  await browser.close();
}
main().catch(e => { console.error(e); process.exit(1); });
