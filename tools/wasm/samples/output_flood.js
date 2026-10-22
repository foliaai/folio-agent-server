// 恶意样本：输出洪水（返回 2MB 字符串，超出 1MB 输出上限）
export function run(input) {
  return "x".repeat(2 * 1024 * 1024);
}
