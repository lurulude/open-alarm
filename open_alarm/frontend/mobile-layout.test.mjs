import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const styles = await readFile(new URL("./src/styles.css", import.meta.url), "utf8");
const controls = await readFile(new URL("./src/controls.css", import.meta.url), "utf8");

assert.match(styles, /body\{[^}]*min-width:0/);
assert.doesNotMatch(styles, /body\{[^}]*min-width:760px/);
assert.match(styles, /@media\(max-width:700px\)/);
assert.match(styles, /\.engineering-shell\{display:flex;flex-direction:column/);
assert.match(styles, /\.engineering-grid-wrap\{width:100%;max-width:100%;overflow:auto/);
assert.match(styles, /\.nav-tabs\{width:100%;height:44px/);
assert.match(styles, /\.admin-grid-wrap\{width:100%;max-width:100%;overflow:auto/);
assert.match(controls, /@media\(max-width:700px\)/);
assert.match(controls, /\.alarm-filter-strip\{display:grid;grid-template-columns:/);
assert.match(controls, /\.alarm-control-strip\{min-width:0;align-items:stretch;flex-direction:column/);
