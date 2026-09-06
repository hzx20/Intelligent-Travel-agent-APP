import { chat, extractJson } from './llm.js';

/**
 * M2 行程生成模块
 * 输入：结构化需求（M1 的 collected）
 * 输出：JSON 行程草稿 { title, summary, days[], budget_summary, assumptions }
 * 注意：POI 名称为 AI 建议的真实知名地点，M3 模块会用地图 API 逐一核实
 */

const SYSTEM_PROMPT = `你是资深旅行行程设计师。根据用户需求生成行程，输出纯 JSON（不要任何多余文字）：
{
  "title": "行程标题（10字内）",
  "summary": "一句话总览",
  "days": [
    {
      "day": 1,
      "theme": "当天主题（6字内）",
      "items": [
        {
          "time": "09:00",
          "activity": "做什么（一句话）",
          "poi": "具体地点名（真实存在的知名地点，供后续地图核实）",
          "type": "景点/餐饮/交通/酒店/自由活动",
          "transfer": "从上一地点怎么去（文字，第一站写 从酒店/车站出发）",
          "cost_estimate": 数字（本项预计花费，单位元，按整个出行队伍算）,
          "note": "一句话小贴士"
        }
      ]
    }
  ],
  "budget_summary": { "交通": 0, "住宿": 0, "餐饮": 0, "门票": 0, "总计": 0 },
  "assumptions": ["你做的关键假设，如：未说明出发城市，按当地出发计算"]
}

硬性约束：
1. days 数量必须等于需求天数，day 从 1 开始连续编号；
2. 每天 items 4-6 条，时间从早上到晚上合理排布，符合需求的节奏（悠闲=每天3-4个点，紧凑=5-6个点）；
3. 同一天的地点必须地理上顺路（同城市内），不要上午东郊下午西郊来回折腾；
4. cost_estimate 与 budget_summary 数额要能对上；未给预算时按中低标准估算并在 assumptions 说明；
5. poi 只写真实存在的知名地点名（如 宽窄巷子、成都大熊猫繁育基地），绝对不要编造；
6. 只输出 JSON。`;

export async function generateItinerary(collected) {
  const messages = [
    { role: 'system', content: SYSTEM_PROMPT },
    { role: 'user', content: `【用户需求】${JSON.stringify(collected)}\n请生成行程 JSON。` }
  ];
  const reply = await chat(messages, { temperature: 0.7, timeoutMs: 90000 });
  const it = extractJson(reply);

  // 结构校验：天数一致、每日有安排
  const expectDays = Number(collected.days) || 0;
  if (expectDays > 0 && Array.isArray(it.days) && it.days.length !== expectDays) {
    // 天数不符 → 让模型重排一次（自检纠偏，不抛给用户）
    const fix = await chat([
      { role: 'system', content: SYSTEM_PROMPT },
      { role: 'user', content: `【用户需求】${JSON.stringify(collected)}\n【你上次的输出有问题】天数应为 ${expectDays} 天，实际 ${it.days.length} 天。请严格按天数重新输出 JSON。` }
    ], { temperature: 0.4, timeoutMs: 90000 });
    const fixed = extractJson(fix);
    if (Array.isArray(fixed.days) && fixed.days.length === expectDays) return fixed;
    throw new Error(`行程天数校验失败：应为 ${expectDays} 天`);
  }
  if (!Array.isArray(it.days) || it.days.some(d => !Array.isArray(d.items) || d.items.length === 0)) {
    throw new Error('行程结构异常：某天没有安排');
  }
  return it;
}
