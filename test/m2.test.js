import { generateItinerary } from '../server/src/itinerary.js';
import assert from 'node:assert';

/**
 * M2 行程生成模块测试
 * 验收标准（对应规划文档）：多组标准需求，行程天数与要求一致、每日安排非空、预算汇总存在
 */

const CASES = [
  { name: '成都 3 天亲子悠闲', req: { destination: '成都', days: 3, dates: '10月1日-3日', party: '2大1小', budget: 3000, pace: '悠闲', preferences: '带5岁小孩' } },
  { name: '杭州 2 天周末', req: { destination: '杭州', days: 2, party: '2人', budget: 1500, pace: '中等' } },
  { name: '北京 1 天紧凑', req: { destination: '北京', days: 1, party: '1人', pace: '紧凑' } }
];

let pass = 0, fail = 0;

async function run() {
  for (const c of CASES) {
    console.log(`— M2 测试 · ${c.name} —`);
    try {
      const it = await generateItinerary(c.req);
      assert.ok(it.title, '应有标题');
      assert.ok(Array.isArray(it.days) && it.days.length === c.req.days,
        `天数应为 ${c.req.days}，实际 ${it.days?.length}`);
      it.days.forEach((d, i) => {
        assert.ok(Array.isArray(d.items) && d.items.length >= 3, `Day${i + 1} 安排应不少于 3 条`);
        d.items.forEach(item => {
          assert.ok(item.poi, `Day${i+1} ${item.time} 应有 poi 字段`);
          assert.ok(item.time, `Day${i+1} 应有时间`);
        });
      });
      assert.ok(it.budget_summary && typeof it.budget_summary['总计'] !== 'undefined', '应有预算汇总');
      console.log(`  ✅ 通过：《${it.title}》${it.days.length}天，每天 ${it.days.map(d => d.items.length).join('/')} 条安排，预算 ¥${it.budget_summary['总计']}`);
      pass++;
    } catch (e) { console.error('  ❌ 失败：', e.message.slice(0, 200)); fail++; }
  }
  console.log(`\nM2 结果：${pass}/${CASES.length} 通过`);
  process.exit(fail > 0 ? 1 : 0);
}

run();
