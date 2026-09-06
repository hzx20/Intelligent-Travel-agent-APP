import { ZHIPU_API_KEY } from './config.js';

const ENDPOINT = 'https://open.bigmodel.cn/api/paas/v4/chat/completions';

/**
 * 调用智谱 GLM 对话接口
 * @param {Array<{role:string, content:string}>} messages
 * @param {{temperature?: number, timeoutMs?: number}} opts
 * @returns {Promise<string>} AI 回复文本
 */
export async function chat(messages, { temperature = 0.5, timeoutMs = 60000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(ENDPOINT, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${ZHIPU_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ model: 'glm-4-flash', messages, temperature }),
      signal: controller.signal
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`智谱API错误 ${res.status}: ${body.slice(0, 200)}`);
    }
    const data = await res.json();
    const content = data?.choices?.[0]?.message?.content;
    if (!content) throw new Error('智谱API返回异常：无 content 字段');
    return content;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 从 AI 回复中容错提取 JSON 对象（剥掉 ```json 包裹、截取首尾大括号）
 */
export function extractJson(text) {
  let t = String(text).trim();
  const fence = t.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) t = fence[1].trim();
  const start = t.indexOf('{');
  const end = t.lastIndexOf('}');
  if (start === -1 || end === -1 || end <= start) {
    throw new Error('AI 未返回合法 JSON：' + t.slice(0, 120));
  }
  return JSON.parse(t.slice(start, end + 1));
}
