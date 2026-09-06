import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// 读取项目根目录的 .env（本文件位于 server/src/，根目录在上两级）
const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, '../../.env');

function loadEnv(path) {
  try {
    const lines = readFileSync(path, 'utf8').split(/\r?\n/);
    const out = {};
    for (const line of lines) {
      const t = line.trim();
      if (!t || t.startsWith('#')) continue;
      const i = t.indexOf('=');
      if (i > 0) out[t.slice(0, i).trim()] = t.slice(i + 1).trim();
    }
    return out;
  } catch {
    return {}; // .env 不存在时不报错，交由环境变量兜底
  }
}

const fileEnv = loadEnv(envPath);
// 真实环境变量优先于文件
const env = { ...fileEnv, ...process.env };

export const ZHIPU_API_KEY = env.ZHIPU_API_KEY;
export const AMAP_API_KEY = env.AMAP_API_KEY;

if (!ZHIPU_API_KEY) {
  throw new Error('缺少 ZHIPU_API_KEY：请确认项目根目录 .env 文件存在且包含该钥匙');
}
