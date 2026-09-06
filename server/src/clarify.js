import { chat, extractJson } from './llm.js';

/**
 * M1 需求澄清模块
 * 输入：对话历史 + 用户本轮输入
 * 输出：{ collected, missing, question, done }
 *  - collected: 已收集的需求字段（累积合并）
 *  - missing: 还缺的必需字段
 *  - question: 缺信息时的自然语言追问（齐全时为 null）
 *  - done: 必需信息是否齐全
 */

const REQUIRED = ['destination', 'days'];
const OPTIONAL = ['dates', 'party', 'budget', 'pace', 'preferences'];

const SYSTEM_PROMPT = `你是旅行规划助手的需求收集器。根据【已收集信息】和【用户本轮输入】，合并整理出最新需求，输出纯 JSON（不要任何多余文字）：
{
  "collected": {
    "destination": "目的地城市，如 成都（不确定则省略该字段）",
    "days": 数字天数（不确定则省略）,
    "dates": "具体日期，如 10月1日-3日（没说则省略）",
    "party": "人数构成，如 2大1小（没说则省略）",
    "budget": "预算数字（元），没说则省略",
    "pace": "节奏：悠闲/紧凑/中等（没说则省略）",
    "preferences": "特殊偏好，如 带5岁小孩/爱美食/亲子（没说则省略）"
  },
  "missing": ["还缺的必需字段名，只能从 destination 和 days 里选"],
  "question": "若 missing 非空，给一句自然的中文追问，一次最多问两件事，语气友好；若齐全则为 null"
}

规则：
1. 用户本轮输入可能补充、修正之前的信息，以最新说法为准；
2. 不得编造用户没说过的信息；
3. 只输出 JSON。`;

export async function clarifyTurn(history, userInput, prevCollected = {}) {
  const messages = [
    { role: 'system', content: SYSTEM_PROMPT },
    ...history,
    { role: 'user', content: `【已收集信息】${JSON.stringify(prevCollected)}\n【用户本轮输入】${userInput}` }
  ];
  const reply = await chat(messages, { temperature: 0.2 });
  const parsed = extractJson(reply);

  const collected = { ...prevCollected, ...(parsed.collected || {}) };
  const missing = (Array.isArray(parsed.missing) ? parsed.missing : [])
    .filter(k => REQUIRED.includes(k) && (collected[k] === undefined || collected[k] === null || collected[k] === ''));
  const done = missing.length === 0;

  return {
    collected,
    missing,
    question: done ? null : (parsed.question || `还差一点信息：请告诉我${missing.join('和')}？`),
    done
  };
}

export const CLARIFY_OPTIONAL_FIELDS = OPTIONAL;
