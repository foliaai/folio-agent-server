// folio 沙箱最小 JS 工具样例：回显输入
// 编译：cd tools/wasm && npx tsx build.ts samples/echo.js /tmp/echo.wasm
export function run(input) {
  return "echo: " + input;
}
