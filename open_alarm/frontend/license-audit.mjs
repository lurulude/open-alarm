import fs from "node:fs";
import path from "node:path";

const ALLOWED = new Set([
  "0BSD",
  "Apache-2.0",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "CC0-1.0",
  "ISC",
  "MIT",
  "MIT-0",
  "MPL-2.0",
  "PSF-2.0",
  "Python-2.0",
  "Unlicense",
  "Zlib",
]);
const OPERATORS = new Set(["AND", "OR", "WITH"]);
const TOKEN_RE = /[A-Za-z0-9][A-Za-z0-9.+-]*/g;

function validateLicense(expression) {
  if (typeof expression !== "string" || !expression.trim()) {
    return { ok: false, detail: "missing license metadata" };
  }
  const normalized = expression.trim();
  if (/UNLICENSED|SEE LICENSE IN/i.test(normalized)) {
    return { ok: false, detail: normalized };
  }
  const tokens = (normalized.match(TOKEN_RE) ?? []).filter((token) => !OPERATORS.has(token));
  if (tokens.length === 0) return { ok: false, detail: normalized };
  const unknown = [...new Set(tokens.filter((token) => !ALLOWED.has(token)))].sort();
  return unknown.length === 0
    ? { ok: true, detail: normalized }
    : { ok: false, detail: `${normalized} (unreviewed: ${unknown.join(", ")})` };
}

function packageDirectories(nodeModules) {
  const result = [];
  for (const entry of fs.readdirSync(nodeModules, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name === ".bin") continue;
    const first = path.join(nodeModules, entry.name);
    if (entry.name.startsWith("@")) {
      for (const scoped of fs.readdirSync(first, { withFileTypes: true })) {
        if (scoped.isDirectory()) result.push(path.join(first, scoped.name));
      }
    } else {
      result.push(first);
    }
  }
  return result;
}

function collect(nodeModules, seen, rows) {
  if (!fs.existsSync(nodeModules)) return;
  for (const packageDir of packageDirectories(nodeModules)) {
    const packageJson = path.join(packageDir, "package.json");
    if (!fs.existsSync(packageJson)) continue;
    const pkg = JSON.parse(fs.readFileSync(packageJson, "utf8"));
    const key = `${pkg.name ?? path.basename(packageDir)}@${pkg.version ?? "unknown"}`;
    if (!seen.has(key)) {
      seen.add(key);
      const check = validateLicense(pkg.license);
      rows.push({
        name: pkg.name ?? path.basename(packageDir),
        version: pkg.version ?? "unknown",
        license: pkg.license ?? "UNKNOWN",
        approved: check.ok,
        detail: check.detail,
      });
    }
    collect(path.join(packageDir, "node_modules"), seen, rows);
  }
}

const nodeModules = path.resolve(process.argv[2] ?? "node_modules");
const reportPath = process.argv[3] ? path.resolve(process.argv[3]) : null;
const rows = [];
collect(nodeModules, new Set(), rows);
rows.sort((a, b) => `${a.name}@${a.version}`.localeCompare(`${b.name}@${b.version}`));

for (const row of rows) console.log(`${row.name}@${row.version}: ${row.license}`);
if (reportPath) {
  fs.mkdirSync(path.dirname(reportPath), { recursive: true });
  fs.writeFileSync(reportPath, `${JSON.stringify(rows, null, 2)}\n`);
}

const failures = rows.filter((row) => !row.approved);
if (failures.length > 0) {
  console.error("\nUnapproved or unknown npm dependency licenses:");
  for (const row of failures) console.error(`- ${row.name}@${row.version}: ${row.detail}`);
  process.exitCode = 1;
} else {
  console.log(`Audited ${rows.length} installed npm packages; all licenses are approved.`);
}
