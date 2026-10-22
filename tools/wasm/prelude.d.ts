/**
 * folio sandbox 用户工具 TS 类型声明（编译时可见的宿主 API）
 *
 * 用户工具约定：导出 `run(input: string): string`
 *   - input：调用方传入的标准输入（任意字符串）
 *   - 返回值：工具输出
 *
 * 网络唯一出口是 httpFetch（经平台 allowlist 检查），文件经 readFile（内存 FS）。
 */

export interface FetchResponse {
  status: number;
  body: string;
}

export interface FetchError {
  error: string;
}

export declare function httpFetch(
  url: string,
  body?: string,
  contentType?: string,
): Promise<FetchResponse & Partial<FetchError>>;

export declare function readFile(name: string): Promise<string>;

export declare function run(input: string): string;
