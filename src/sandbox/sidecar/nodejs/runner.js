#!/usr/bin/env node
/**
 * folio sandbox Pyodide runner（孙进程 C）
 * 由 src/sandbox/sidecar/engines/pyodide_engine.py 每请求 spawn 一次。
 *
 * 协议（行分隔 JSON，stdin 进 / stdout 出，stderr 仅诊断）：
 *   ← {"type":"ready"}                          Pyodide 加载完成
 *   → {"type":"run", code, files[], outputLimitBytes}
 *   ← {"type":"net", seq, url, body, contentType}    用户代码 folio_fetch 转发
 *   → {"type":"net-result", seq, ok, status, body, error}
 *   ← {"type":"done", ok, stdout, stderr, resultCode, error}
 *
 * 安全硬化（在用户代码执行前完成）：
 *   - globalThis.fetch / WebSocket / XMLHttpRequest 置为抛错函数（唯一网络出口是 folio_fetch）
 *   - delete globalThis.process（防 env 泄漏与 process.exit；本进程自留引用）
 *   - sys.modules["pyodide.http"] 替换为抛错 stub
 */

import readline from "node:readline";
import { loadPyodide } from "pyodide";

// 顶层引用先留好（delete globalThis.process 之后自己仍可用）
const proc = globalThis.process;

const send = (obj) => proc.stdout.write(JSON.stringify(obj) + "\n");

// ---------- 输出截断缓冲 ----------
let outLimitBytes = 1024 * 1024;
let outChunks = [];
let outBytes = 0;
let outTruncated = false;
function appendOut(s) {
  if (outBytes >= outLimitBytes) {
    outTruncated = true;
    return;
  }
  outBytes += s.length;
  outChunks.push(s);
  if (outBytes >= outLimitBytes) outTruncated = true;
}
let errChunks = [];
let errBytes = 0;
function appendErr(s) {
  if (errBytes < 64 * 1024) {
    errBytes += s.length;
    errChunks.push(s);
  }
}
function stdoutText() {
  return outChunks.join("") + (outTruncated ? "\n[folio-sandbox: output truncated]" : "");
}

// ---------- 加载 Pyodide ----------
const pyodide = await loadPyodide({
  stdout: (s) => appendOut(s + "\n"),
  stderr: (s) => appendErr(s + "\n"),
});
send({ type: "ready" });

// ---------- 安全硬化 ----------
globalThis.fetch = () => {
  throw new Error("direct fetch is disabled in folio sandbox; use folio_fetch()");
};
if (globalThis.WebSocket) {
  globalThis.WebSocket = () => {
    throw new Error("WebSocket is disabled in folio sandbox; use folio_fetch()");
  };
}
if (globalThis.XMLHttpRequest) {
  globalThis.XMLHttpRequest = class {
    open() {
      throw new Error("XMLHttpRequest is disabled in folio sandbox");
    }
  };
}
delete globalThis.process;

// 禁 pyodide.http（pyfetch 走浏览器 fetch，绕过 allowlist）
await pyodide.runPythonAsync(`
import sys, types
_m = types.ModuleType("pyodide.http")
async def _denied(*a, **k):
    raise RuntimeError("pyodide.http is disabled in folio sandbox; use folio_fetch()")
_m.pyfetch = _denied
_m.fetch = _denied
sys.modules["pyodide.http"] = _m
`);

// ---------- folio_fetch：唯一网络出口（转发回 sidecar 执行 allowlist 检查） ----------
let netSeq = 0;
const pendingNets = new Map();
async function folioFetch(url, body, contentType) {
  const seq = ++netSeq;
  send({ type: "net", seq, url, body: body ?? null, contentType: contentType ?? null });
  return await new Promise((resolve) => pendingNets.set(seq, resolve));
}
// 注意：注入必须走 globalThis（`from js import folio_fetch` 反映 JS 全局），
// pyodide.globals.set 写的是 Python __main__ 命名空间，js 模块看不到
globalThis.folio_fetch = folioFetch;

// ---------- 主循环 ----------
const rl = readline.createInterface({ input: proc.stdin });

rl.on("line", async (line) => {
  line = line.trim();
  if (!line) return;
  let frame;
  try {
    frame = JSON.parse(line);
  } catch {
    return;
  }

  if (frame.type === "net-result") {
    const resolve = pendingNets.get(frame.seq);
    if (resolve) {
      pendingNets.delete(frame.seq);
      resolve(frame);
    }
    return;
  }

  if (frame.type === "run") {
      // done 帧可能很大（stdout 上限 1MiB+）：必须等 write flush 到 OS 后再 exit，
      // 否则进程退出会截断未写完的帧（pipe 异步写）
      const finish = (payload) => {
        proc.stdout.write(JSON.stringify(payload) + "\n", () => proc.exit(0));
      };

      try {
        if (frame.outputLimitBytes) outLimitBytes = frame.outputLimitBytes;

        // 输入文件写入内存 FS（用户代码可 open()/相对路径访问）
        pyodide.FS.mkdirTree("/home/pydide");
        for (const f of frame.files ?? []) {
          const name = String(f.name).replace(/[\\/]/g, "_");
          pyodide.FS.writeFile(`/home/pydide/${name}`, Buffer.from(f.content_b64, "base64"));
        }
        pyodide.FS.chdir("/home/pydide");

        await pyodide.runPythonAsync(frame.code);

        // 约定：结果放 `result` 变量（str 原样，其他 JSON 序列化，None → null）
        // 注意：最后必须是表达式语句（函数调用）——Pyodide 只返回 Expr 语句的值
        const rc = await pyodide.runPythonAsync(`
def _folio_read_result():
    import json as _json
    _r = globals().get("result")
    if _r is None:
        return None
    if isinstance(_r, str):
        return _r
    return _json.dumps(_r, ensure_ascii=False, default=str)

_folio_read_result()
`);

        finish({
          type: "done",
          ok: true,
          stdout: stdoutText(),
          stderr: errChunks.join(""),
          resultCode: rc ?? null,
          error: null,
        });
      } catch (e) {
        finish({
          type: "done",
          ok: false,
          stdout: stdoutText(),
          stderr: (errChunks.join("") + "\n" + (e?.message ?? String(e))).trim(),
          resultCode: null,
          error: `${e?.constructor?.name ?? "PythonError"}: ${e?.message ?? String(e)}`,
        });
      }
  }
});
