#!/usr/bin/env node
/*
 *   node render.cjs stills 1.2,3.5 [outDir]     -> PNG stills
 *   node render.cjs frames <from> <to> [outDir] -> JPEG frames for t in [from,to) at 60 fps (parallel workers)
 *   node render.cjs coverage <from> <to> <step> -> key coverage per time
 */
const path = require('path'), fs = require('fs'), http = require('http');
let pw; try { pw = require('playwright'); } catch (e) { pw = require('/opt/node22/lib/node_modules/playwright'); }
const ROOT = path.join(__dirname, '..');
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.webp': 'image/webp', '.woff2': 'font/woff2', '.bin': 'application/octet-stream', '.png': 'image/png' };
function serve() {
  return new Promise(res => {
    const srv = http.createServer((req, rsp) => {
      const f = path.join(ROOT, decodeURIComponent(req.url.split('?')[0]));
      fs.readFile(f, (e, d) => { if (e) { rsp.writeHead(404); rsp.end(); return; } rsp.writeHead(200, { 'Content-Type': TYPES[path.extname(f)] || 'application/octet-stream' }); rsp.end(d); });
    }).listen(0, () => res(srv));
  });
}
async function openPage(browser, port) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.setDefaultTimeout(0);
  page.on('pageerror', e => console.error('PAGE ERROR', e.message));
  page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') console.error('console:', m.text().slice(0, 300)); });
  await page.goto(`http://localhost:${port}/sitevo-keys/index.html`);
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 400000 });
  return page;
}
(async () => {
  const [mode, a1, a2, a3] = process.argv.slice(2);
  const srv = await serve(); const port = srv.address().port;
  const browser = await pw.chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--disable-lcd-text', '--force-color-profile=srgb'] });
  if (mode === 'stills') {
    const out = a2 || path.join(__dirname, 'out', 'stills'); fs.mkdirSync(out, { recursive: true });
    const page = await openPage(browser, port);
    for (const ts of a1.split(',').map(Number)) {
      const t0 = Date.now(); await page.evaluate(t => window.renderAt(t), ts);
      await page.screenshot({ path: path.join(out, `t${ts.toFixed(2).padStart(5, '0')}.png`) });
      console.log(ts, ((Date.now() - t0) / 1000).toFixed(2) + 's');
    }
  } else if (mode === 'coverage') {
    const page = await openPage(browser, port);
    await page.evaluate(() => { window.SAMPLES = 2; });
    for (let t = +a1; t < +a2; t += +a3) { await page.evaluate(t => window.renderAt(t), t); console.log(t.toFixed(3), (await page.evaluate(() => window.coverage())).toFixed(3)); }
  } else if (mode === 'frames') {
    const FPS = Number(process.env.FPS || 60), from = +a1, to = +a2, out = a3 || path.join(__dirname, 'out', 'frames'); fs.mkdirSync(out, { recursive: true });
    const f0 = Math.round(from * FPS), f1 = Math.round(to * FPS); let next = f0, done = 0; const t0 = Date.now();
    const W = Number(process.env.WORKERS || 4);
    await Promise.all(Array.from({ length: W }, async () => {
      const page = await openPage(browser, port);
      if (process.env.SAMPLES) await page.evaluate(n => { window.SAMPLES = n; }, Number(process.env.SAMPLES));
      while (true) {
        const i = next++; if (i >= f1) break;
        const file = path.join(out, `f${String(i).padStart(5, '0')}.jpg`);
        if (fs.existsSync(file)) { done++; continue; }
        await page.evaluate(t => window.renderAt(t), i / FPS);
        await page.screenshot({ path: file, type: 'jpeg', quality: 95 });
        if (++done % 30 === 0) { const el = (Date.now() - t0) / 1000; console.log(`${done}/${f1 - f0}  ${(done / el).toFixed(2)} fps  eta ${((f1 - f0 - done) / (done / el)).toFixed(0)}s`); }
      }
    }));
  }
  await browser.close(); srv.close();
})().catch(e => { console.error(e); process.exit(1); });
