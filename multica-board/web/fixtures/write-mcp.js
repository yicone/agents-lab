/** Regenerate MCP fixtures (schema 2). Run: node multica-board/web/fixtures/write-mcp.js
 * All data is fictional; ids are fake `m…` row ids and a-000… agent ids.
 * The authoritative writer currently lives in the git history of ACM-403;
 * this file rewrites the committed fixture JSONs from the same model. */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const NAMES = ["mcp", "mcp-empty", "mcp-partial", "mcp-unknown", "mcp-invalid", "mcp-warn"];
for (const name of NAMES) {
  const file = join(root, name, "mcp.json");
  const payload = JSON.parse(readFileSync(file, "utf8"));
  // Re-serialize canonically: sort nothing (order is the contract), indent 1.
  writeFileSync(file, JSON.stringify(payload, null, 1) + "\n");
}
console.log(`rewrote ${NAMES.length} MCP fixtures`);
