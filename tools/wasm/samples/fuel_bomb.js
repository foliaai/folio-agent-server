// 恶意样本：fuel 炸弹（死循环）
export function run(input) {
  let x = 0;
  while (true) {
    x = x + 1;
  }
  return "never";
}
