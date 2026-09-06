import { clarifyTurn } from '../server/src/clarify.js';
import assert from 'node:assert';

/**
 * M1 需求澄清模块测试
 * 验收标准（对应规划文档）：
 *   1. 故意只说"去成都玩"（缺天数）→ AI 必须追问而不是硬编
 *   2. 补齐必需信息后 → done=true，collected 正确累积
 */

let pass = 0, fail = 0;

async function run() {
  console.log('— M1 测试 · 场景1：信息不全应追问 —');
  try {
    const r1 = await clarifyTurn([], '我想去成都玩');
    assert.strictEqual(r1.done, false, '缺天数时 done 应为 false');
    assert.ok(r1.question, '应有追问文案');
    assert.ok(r1.collected.destination && r1.collected.destination.includes('成都'), '目的地应被识别为成都');
    console.log('  ✅ 通过：done=false，追问 =', r1.question);
    pass++;
  } catch (e) { console.error('  ❌ 失败：', e.message); fail++; }

  console.log('— M1 测试 · 场景2：补齐后应放行 —');
  try {
    const r2 = await clarifyTurn(
      [{ role: 'user', content: '我想去成都玩' }, { role: 'assistant', content: '请问玩几天？' }],
      '10月1日到3日共3天，2大1小，预算3000，带娃要悠闲，爱熊猫',
      { destination: '成都' }
    );
    assert.strictEqual(r2.done, true, '信息齐全时 done 应为 true');
    assert.strictEqual(r2.collected.days, 3, '天数应为 3');
    assert.ok(String(r2.collected.party).includes('1小'), '人数构成应含 1小');
    console.log('  ✅ 通过：done=true，collected =', JSON.stringify(r2.collected));
    pass++;
  } catch (e) { console.error('  ❌ 失败：', e.message); fail++; }

  console.log(`\nM1 结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail > 0 ? 1 : 0);
}

run();
