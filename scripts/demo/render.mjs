#!/usr/bin/env node
// Render an asciicast (.cast) to a PNG screenshot or an animated GIF/MP4.
//
// Replays the recording in xterm.js inside headless Chromium (Playwright) and
// captures frames. Needs `npm install` here (xterm.js, playwright) and ffmpeg.
//
//   node scripts/demo/render.mjs pipeline.cast pipeline.gif
//   node scripts/demo/render.mjs pipeline.cast select.png --at 9.5   (time after idle compression)
//   node scripts/demo/render.mjs tracker.cast tracker.png            (last frame)

import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, extname } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const HERE = dirname(fileURLToPath(import.meta.url));
const XTERM = join(HERE, "node_modules", "@xterm", "xterm");

const THEME = {
  background: "#1d2127", foreground: "#d7dae0", cursor: "#1d2127",
  black: "#1d2127", red: "#e06c6c", green: "#7fbf8f", yellow: "#e0b45c",
  blue: "#6f9fd0", magenta: "#c49bd6", cyan: "#6fb8c4", white: "#d7dae0",
  brightBlack: "#7a818c", brightRed: "#ef8a8a", brightGreen: "#9cd3a9",
  brightYellow: "#ecc77f", brightBlue: "#8fb6df", brightMagenta: "#d5b3e3",
  brightCyan: "#8fcbd5", brightWhite: "#f2f3f5",
};

function args() {
  const [cast, out, ...rest] = process.argv.slice(2);
  const opt = { cast, out, at: null, fps: 8, idle: 1.5, hold: 3 };
  for (let i = 0; i < rest.length; i += 2) opt[rest[i].replace(/^--/, "")] = Number(rest[i + 1]);
  return opt;
}

function loadCast(path, idle) {
  const lines = readFileSync(path, "utf8").split("\n").filter(Boolean);
  const header = JSON.parse(lines[0]);
  let shift = 0, prev = 0;
  const events = [];
  for (const line of lines.slice(1)) {
    const [t, kind, data] = JSON.parse(line);
    if (kind !== "o") continue;
    if (t - prev > idle) shift += t - prev - idle;   // cap long pauses
    prev = t;
    events.push([t - shift, data]);
  }
  return { header, events };
}

const o = args();
const { header, events } = loadCast(o.cast, o.idle);
const html = `<!doctype html><html><head><meta charset="utf-8"><style>
${readFileSync(join(XTERM, "css", "xterm.css"), "utf8")}
body{margin:0;background:${THEME.background}}
#frame{display:inline-block;padding:18px 22px;background:${THEME.background}}
</style></head><body><div id="frame"><div id="t"></div></div>
<script>${readFileSync(join(XTERM, "lib", "xterm.js"), "utf8")}</script>
<script>
const term = new Terminal({cols:${header.width}, rows:${header.height},
  fontFamily:"JetBrains Mono, DejaVu Sans Mono, monospace", fontSize:15, lineHeight:1.15,
  theme:${JSON.stringify(THEME)}, cursorBlink:false});
term.open(document.getElementById("t"));
window.feed = (s) => new Promise(r => term.write(s, r));
</script></body></html>`;

const png = extname(o.out) === ".png";
const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: png ? 2 : 1 });
await page.setContent(html);
await page.waitForTimeout(300);
const frame = page.locator("#frame");

if (png) {
  const limit = o.at ?? Infinity;
  await page.evaluate((s) => feed(s), events.filter(([t]) => t <= limit).map(([, d]) => d).join(""));
  await page.waitForTimeout(200);
  await frame.screenshot({ path: o.out });
} else {
  const tmp = mkdtempSync(join(tmpdir(), "cast-frames-"));
  const end = (events.at(-1)?.[0] ?? 0) + o.hold;
  let i = 0, n = 0;
  for (let t = 0; t <= end; t += 1 / o.fps) {
    let chunk = "";
    while (i < events.length && events[i][0] <= t) chunk += events[i++][1];
    if (chunk) await page.evaluate((s) => feed(s), chunk);
    await frame.screenshot({ path: join(tmp, `f${String(n++).padStart(5, "0")}.png`) });
  }
  const src = ["-framerate", String(o.fps), "-i", join(tmp, "f%05d.png")];
  const vf = extname(o.out) === ".gif"
    ? ["-vf", "split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=none"]
    : ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "28"];
  execFileSync("ffmpeg", ["-loglevel", "error", "-y", ...src, ...vf, o.out]);
  rmSync(tmp, { recursive: true });
}
await browser.close();
console.log(`wrote ${o.out}`);
