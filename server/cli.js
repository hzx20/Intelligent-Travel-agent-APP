import readline from 'node:readline/promises';
import { stdin as input, stdout as output } from 'node:process';
import { clarifyTurn } from './src/clarify.js';
import { generateItinerary } from './src/itinerary.js';

/**
 * 命令行对话入口（v0.4 会聊天的助手）
 * 运行：node server/cli.js
 * 退出：输入 /exit
 */

const rl = readline.createInterface({ input, output });

function printItinerary(it, collected) {
  const lines = [];
  lines.push('');
  lines.push(`📦 ${it.title} — ${collected.days}天${collected.party ? ' · ' + collected.party : ''}${collected.budget ? ' · 预算¥' + collected.budget : ''}`);
  lines.push(`   ${it.summary}`);
  for (const day of it.days) {
    lines.push('');
    lines.push(`—— Day ${day.day} · ${day.theme || ''} ——`);
    for (const item of day.items) {
      const cost = item.cost_estimate !== undefined && item.cost_estimate !== null ? ` ¥${item.cost_estimate}` : '';
      const poi = item.poi ? ` [${item.poi}]` : '';
      lines.push(`  ${item.time} ${item.activity}${poi}${cost}`);
      if (item.transfer) lines.push(`        ↳ ${item.transfer}`);
      if (item.note) lines.push(`        💡 ${item.note}`);
    }
  }
  if (it.budget_summary) {
    const b = it.budget_summary;
    lines.push('');
    lines.push(`💰 预算：交通¥${b['交通'] ?? '-'} 住宿¥${b['住宿'] ?? '-'} 餐饮¥${b['餐饮'] ?? '-'} 门票¥${b['门票'] ?? '-'} = ¥${b['总计'] ?? '-'}`);
  }
  if (Array.isArray(it.assumptions) && it.assumptions.length) {
    lines.push(`📌 假设：${it.assumptions.join('；')}`);
  }
  lines.push('');
  console.log(lines.join('\n'));
}

async function main() {
  console.log('🧳 智能行程规划师 v0.4（命令行版）');
  console.log('   试试输入：我想去成都玩');
  console.log('   退出输入 /exit\n');

  const history = [];
  let collected = {};
  let clarified = false;

  while (true) {
    const rawInput = await rl.question('你 > ');
    if (rawInput === null) break; // 输入流结束（管道/Ctrl+D）
    const userInput = rawInput.trim();
    if (!userInput) continue;
    if (userInput === '/exit' || userInput === '退出') break;

    try {
      if (!clarified) {
        // M1 需求澄清阶段
        const turn = await clarifyTurn(history, userInput, collected);
        collected = turn.collected;
        history.push({ role: 'user', content: userInput });

        if (!turn.done) {
          console.log(`助手 > ${turn.question}`);
          history.push({ role: 'assistant', content: turn.question });
        } else {
          const brief = [
            collected.destination,
            collected.days + '天',
            collected.dates,
            collected.party,
            collected.budget ? '预算¥' + collected.budget : null,
            collected.pace,
            collected.preferences
          ].filter(Boolean).join(' · ');
          console.log(`助手 > 信息齐了：${brief}\n        正在生成行程，约需 20-40 秒…`);
          const it = await generateItinerary(collected);
          printItinerary(it, collected);
          clarified = true;
          history.push({ role: 'assistant', content: `已生成行程：${it.title}` });
          console.log('助手 > 行程如上。想调整就直接说（如"第二天太赶了"），我会重新生成；退出输入 /exit');
        }
      } else {
        // 已生成，后续输入 = 调整需求 → 重新生成
        console.log('助手 > 收到，按你的新要求重新生成，约需 20-40 秒…');
        collected = { ...collected, adjustments: userInput };
        const it = await generateItinerary(collected);
        printItinerary(it, collected);
      }
    } catch (err) {
      console.log(`助手 > 出了点小状况：${err.message}\n        你可以再说一次，或换个说法试试。`);
    }
  }
  console.log('再见！行程灵感随时来聊 👋');
  rl.close();
}

main().catch(err => { console.error('启动失败：', err.message); process.exit(1); });
