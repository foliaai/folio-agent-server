/**
 * folio sandbox 编译链：用户 JS/TS → wasm component（wasi 0.2）
 *
 * 用法：npx tsx build.ts <input.ts|input.js> <output.wasm>
 * 约定：入口文件导出 `export function run(input: string): string`；
 *       宿主 API（httpFetch/readFile）由 runner.wit 的 folio:sandbox/host 提供。
 */

import { componentize } from "@bytecodealliance/componentize-js";
import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const [, , srcPath, outPath] = process.argv;
if (!srcPath || !outPath) {
  console.error("usage: npx tsx build.ts <input.js|input.ts> <output.wasm>");
  process.exit(2);
}

const { component, imports } = await componentize({
  sourcePath: resolve(srcPath),
  witPath: fileURLToPath(new URL("./runner.wit", import.meta.url)),
  worldName: "runner",
});

await writeFile(outPath, component);
console.error(`built ${outPath} (${component.byteLength} bytes) | guest imports: ${JSON.stringify(imports)}`);
