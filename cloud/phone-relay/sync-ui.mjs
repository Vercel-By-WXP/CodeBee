#!/usr/bin/env node
// Mirror the desktop workbench's static files into the Worker's asset
// directory so phones fetch them from the Cloudflare edge instead of the
// cross-border relay. `npm run deploy` runs this first; commit the result.
// index.html included: the workbench shell is the single largest first-paint
// cost left on the relay; version drift with the answering computer is the
// same accepted risk as app.js, bounded by the short cache TTL.
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const project = dirname(fileURLToPath(import.meta.url)); // cloud/phone-relay
const ui = join(project, "..", "..", "app", "ui");
const out = join(project, "public", "ui");

const top = readdirSync(ui).filter(f => /\.(?:js|css|html|json|txt)$/i.test(f));

// Collect asset/icons references from every file that will be served plus the
// index that links them, so a new reference fails the build loudly, not on a
// phone with a broken image.
const refs = new Set();
for (const name of [...top, "index.html"]) {
  const text = readFileSync(join(ui, name), "utf8");
  for (const m of text.matchAll(/(?:assets|icons)\/[\w.-]+\.(?:png|jpe?g|svg|webp|ico|woff2?)/g)) {
    refs.add(m[0]);
  }
}

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

let files = 0;
let bytes = 0;
for (const name of [...top, ...refs].sort()) {
  const source = join(ui, name);
  if (!existsSync(source)) throw new Error(`referenced UI file is missing: ${name}`);
  const target = join(out, name);
  mkdirSync(dirname(target), { recursive: true });
  copyFileSync(source, target);
  bytes += statSync(source).size;
  files += 1;
}
console.log(`sync-ui: ${files} files, ${(bytes / 1024 / 1024).toFixed(2)} MiB -> public/ui`);
