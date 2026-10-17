/** Write the Agent Atlas mock scenarios. Run: node multica-board/web/fixtures/write-atlas.js */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { scenarioFiles } from "./atlas-data.js";

const root = dirname(fileURLToPath(import.meta.url));
const names = ["atlas", "atlas-empty", "atlas-unfilled", "atlas-quiet", "atlas-gap"];

for (const name of names) {
  const dir = join(root, name);
  mkdirSync(dir, { recursive: true });
  const files = scenarioFiles(name);
  for (const [file, body] of Object.entries(files)) {
    writeFileSync(join(dir, `${file}.json`), `${JSON.stringify(body, null, 1)}\n`);
  }
  console.log(name);
}
