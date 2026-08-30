import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const playwrightModule = process.env.ORI_PLAYWRIGHT_MODULE ?? "playwright";
const { chromium } = await import(playwrightModule);

const args = process.argv.slice(2);
const valueAfter = (flag) => {
  const index = args.indexOf(flag);
  return index >= 0 ? args[index + 1] : undefined;
};
const outputDir = path.resolve(
  valueAfter("--output-dir") ?? "docs/assets/offensive-ai-con/offline-demo",
);
const chromePath = process.env.ORI_CHROME_EXECUTABLE;
if (!chromePath) throw new Error("ORI_CHROME_EXECUTABLE is required");

const staticDir = path.join(outputDir, "static");
await fs.rm(staticDir, { recursive: true, force: true });
await fs.mkdir(staticDir, { recursive: true });

const browser = await chromium.launch({ headless: true, executablePath: chromePath });
const sourceUrl = pathToFileURL(path.join(outputDir, "index.html")).href;
const artifacts = {};
const layouts = {};
for (let beat = 1; beat <= 6; beat += 1) {
  // Avoid browser history/scroll restoration clipping an individual fallback.
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.goto(`${sourceUrl}?beat=${beat}`, { waitUntil: "load" });
  await page.evaluate(() => window.scrollTo(0, 0));
  const layout = await page.evaluate(() => {
    const bounds = (selector) => {
      const rect = document.querySelector(selector)?.getBoundingClientRect();
      return rect ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null;
    };
    return {
      scroll: { x: window.scrollX, y: window.scrollY },
      beat: bounds(".beat.active"),
      eyebrow: bounds(".beat.active .eyebrow"),
      title: bounds(".beat.active .title"),
      footer: bounds(".beat.active .footer"),
    };
  });
  if (
    layout.scroll.x !== 0 ||
    layout.scroll.y !== 0 ||
    layout.beat?.x !== 0 ||
    layout.beat?.y !== 0 ||
    (beat < 6 && (layout.eyebrow?.y ?? -1) < 25) ||
    (layout.footer?.y ?? 721) + (layout.footer?.height ?? 0) > 705
  ) {
    throw new Error(`unsafe fallback layout for beat ${beat}: ${JSON.stringify(layout)}`);
  }
  layouts[String(beat)] = layout;
  const target = path.join(staticDir, `beat-${String(beat).padStart(2, "0")}.png`);
  await page.screenshot({ path: target, fullPage: false });
  const bytes = await fs.readFile(target);
  artifacts[path.relative(outputDir, target)] = crypto.createHash("sha256").update(bytes).digest("hex");
  await page.close();
}
await browser.close();

const manifest = {
  schema_version: "ori-offensive-ai-con-offline-demo-render-v1",
  viewport: { width: 1280, height: 720 },
  beat_count: 6,
  artifacts,
  layouts,
};
await fs.writeFile(
  path.join(outputDir, "render-manifest.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
  "utf8",
);
