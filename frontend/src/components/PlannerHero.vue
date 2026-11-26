<script setup>
/** 首页「规划发令台」。
 *  这里只负责收集需求，不发任何请求——提交后带着参数跳到 /plan，
 *  由 PlanView 读 query 自动发起规划，用户不用把刚说的话再打一遍。
 *  参数：d=城市 / days=天数（仅当用户没自己打字时才带）/ q=原话 / auto=1 */
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { useRouter, onBeforeRouteLeave } from 'vue-router'
import RegionPicker from './regionpicker.vue'
import {useAuth} from '../store/auth'
import {registerAuthChangeGuard} from '../store/authChangeGuard'

const router = useRouter()
const {user}=useAuth()

/** 全站景点数由首页读取 /api/spots/regions 的 total 后传进来。
 *  不在文案里写死数字：写死过一次 530，实际库里是 500，改完数据就变成了假话。 */
defineProps({ totalSpots: { type: Number, default: 0 } })

const text = ref('')      // 用户输入的需求原话
const city = ref('')      // 条件行选中的城市（纯城市名，与后端地图核实口径一致）
const days = ref(3)       // 条件行的天数
const inputEl = ref(null)
const regionDraft=ref(''),submitting=ref(false)
const hasDraft=computed(()=>Boolean(text.value.trim()||city.value||regionDraft.value.trim()||days.value!==3))
const leave=()=>!hasDraft.value||window.confirm('还有未发送的规划需求，离开或切换账号会丢失本页输入，确定继续吗？')
const removeGuard=registerAuthChangeGuard(leave)
onBeforeRouteLeave(()=>submitting.value||leave())
const beforeUnload=e=>{if(hasDraft.value){e.preventDefault();e.returnValue=''}}
window.addEventListener('beforeunload',beforeUnload)
watch(()=>user.value?.id||0,()=>{text.value='';city.value='';regionDraft.value='';days.value=3},{flush:'sync'})
onBeforeUnmount(()=>{removeGuard();window.removeEventListener('beforeunload',beforeUnload)})

const EXAMPLES = [
  { label: '成都 3 天亲子', q: '成都 3 天，带 5 岁孩子，节奏想轻松一点' },
  { label: '杭州 2 天安静', q: '杭州 2 天，喜欢安静的地方，不想赶路' },
  { label: '西安 4 天历史', q: '西安 4 天，主要看历史古迹，节奏适中' },
]

/** 只点了条件、没打字时，用条件拼一句话，主按钮不至于灰在那儿点不动 */
const composed = computed(() => {
  const t = text.value.trim()
  if (t) return t
  const destination=city.value||regionDraft.value.trim()
  return destination ? `去${destination}玩 ${days.value} 天` : ''
})

function useExample(e) {
  if((text.value.trim()||city.value||regionDraft.value.trim())&&text.value!==e.q&&!window.confirm('替换会覆盖当前规划需求，确定使用这条灵感吗？'))return
  text.value = e.q
  city.value='';regionDraft.value=''
  inputEl.value?.focus()
}

async function submit() {
  if(submitting.value)return
  const q = composed.value
  // 空着点按钮不装死：把光标送回输入框，让人知道该从这儿开始
  if (!q) { inputEl.value?.focus(); return }
  const query = { q, auto: '1' }
  if (city.value) query.d = city.value
  // 用户自己打了字，就以文字为准，不再塞天数参数——
  // 否则会出现"文字说 2 天、参数说 3 天"的打架，AI 收到两份互相矛盾的需求
  if (!text.value.trim()) query.days = String(days.value)
  submitting.value=true
  try{await router.push({ path: '/plan', query })}finally{submitting.value=false}
}

/** 首页城市卡点进来：把城市和一句话预填好，让用户改完直接出发 */
function prefill(c) {
  if((text.value.trim()||city.value||regionDraft.value.trim())&&!window.confirm('替换会覆盖当前规划需求，确定使用这条灵感吗？'))return false
  city.value = c
  text.value = '';regionDraft.value=''
  inputEl.value?.focus()
}
defineExpose({ prefill })
</script>

<template>
  <div class="hero-card">
    <p class="eyebrow">AI 智能旅行规划</p>
    <h1>说一句话，帮你把行程排好</h1>
    <p class="sub">
      从目的地、天数和偏好开始<template v-if="totalSpots">，当前景点库收录 {{ totalSpots }} 个地点</template>。
      生成后结合地图核对地点与路线，再决定每天的安排。
    </p>

    <!-- 主输入：一整句话描述需求 -->
    <div class="ask-row">
      <input
        ref="inputEl"
        v-model="text"
        aria-label="规划需求"
        :disabled="submitting"
        class="ask-input"
        maxlength="200"
        placeholder="例如：成都 3 天，带 5 岁孩子"
        @keyup.enter="submit"
      />
      <button class="ask-btn" :disabled="submitting" @click="submit">
        生成我的路线<span class="arr">→</span>
      </button>
    </div>

    <!-- 条件行：不想打字的人也能两下点完 -->
    <div class="cond-row" :inert="submitting || undefined">
      <div class="city-wrap">
        <RegionPicker
          :key="user?.id || 0"
          :initial="city"
          placement="down"
          placeholder="目的地，如：成都"
          @select="city = $event.city || ''"
          @draft="regionDraft = $event"
        />
      </div>
      <label class="day-pick">
        <span>玩</span>
        <select v-model.number="days" aria-label="游玩天数">
          <option v-for="n in 10" :key="n" :value="n">{{ n }}</option>
        </select>
        <span>天</span>
      </label>
      <div class="examples">
        <span class="ex-label">试试</span>
        <button v-for="e in EXAMPLES" :key="e.label" class="ex-chip" @click="useExample(e)">
          {{ e.label }}
        </button>
      </div>
    </div>

    <!-- 能力标签：一句话说清这东西和普通搜索框的区别 -->
    <div class="caps">
      <span>地点信息核对</span><i />
      <span>在途耗时估算</span><i />
      <span>出行难度评估</span><i />
      <span>住宿建议</span><i />
      <span>天气参考</span>
    </div>
  </div>
</template>

<style scoped>
.ex-chip{min-height:44px}.ask-input:focus-visible,.ask-btn:focus-visible,.ex-chip:focus-visible{outline:3px solid #548b73;outline-offset:3px}
.hero-card {
  background: #fff;
  border: 1px solid var(--green-border);
  border-radius: 22px;
  padding: 38px 36px 30px;
  box-shadow: 0 14px 40px rgba(26, 110, 80, .09);
}

.eyebrow {
  font-size: 12px;
  letter-spacing: 2px;
  color: var(--green);
  margin-bottom: 12px;
}
h1 {
  font-size: 31px;
  line-height: 1.3;
  font-weight: 700;
  letter-spacing: -.5px;
  color: #1d3a30;
}
.sub {
  margin-top: 12px;
  font-size: 13.5px;
  line-height: 1.8;
  color: var(--text-sub);
  max-width: 620px;
}

/* 主输入行 */
.ask-row { display: flex; gap: 10px; margin-top: 24px; }
.ask-input {
  flex: 1; min-width: 0; height: 54px;
  border: 1.5px solid #cfd8d2; border-radius: 14px;
  padding: 0 18px; font-size: 15px; color: var(--text);
  background: #fff;
}
.ask-input::placeholder { color: #a8b0ab; }
.ask-input:focus { outline: none; border-color: var(--green); box-shadow: 0 0 0 3px rgba(26, 110, 80, .1); }
.ask-btn {
  flex-shrink: 0; height: 54px; padding: 0 26px;
  border: none; border-radius: 14px;
  background: var(--green); color: #fff;
  font-size: 15px; font-weight: 600; white-space: nowrap;
  transition: background .16s, transform .16s;
}
.ask-btn:hover { background: #14563e; transform: translateY(-1px); }
.ask-btn .arr { margin-left: 6px; }

/* 条件行 */
.cond-row {
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
  margin-top: 14px;
  --rp-h: 44px;
  --rp-fs: 13px;
}
.city-wrap { width: 200px; flex-shrink: 0; display: flex; }
.day-pick {
  display: flex; align-items: center; gap: 6px;
  height: 44px; padding: 0 12px; flex-shrink: 0;
  background: #fff; border: 1.5px solid #ccc; border-radius: 10px;
  font-size: 13px; color: var(--text-sub);
}
.day-pick:focus-within { border-color: var(--green); }
.day-pick select {
  border: none; outline: none; background: transparent;
  font-size: 13px; font-weight: 700; color: var(--green);
  font-family: inherit; cursor: pointer;
}
.examples { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.ex-label { font-size: 12px; color: #a8b0ab; }
.ex-chip {
  border: 1px solid var(--green-border); background: var(--green-soft);
  color: var(--green); border-radius: 20px; padding: 5px 12px;
  font-size: 12px; cursor: pointer; transition: background .15s;
}
.ex-chip:hover { background: #d6ecdf; }

/* 能力标签 */
.caps {
  display: flex; flex-wrap: wrap; align-items: center; gap: 9px;
  margin-top: 20px; font-size: 12px; color: #8b968f;
}
.caps i { width: 3px; height: 3px; border-radius: 50%; background: #cbd6cf; }

@media (max-width: 640px) {
  .hero-card { padding: 24px 20px 22px; border-radius: 16px; }
  h1 { font-size: 23px; }
  .sub { font-size: 12.5px; line-height: 1.75; }
  .ask-row { flex-direction: column; gap: 8px; }
  .ask-input, .ask-btn { height: 48px; }
  .ask-btn { width: 100%; }
  .cond-row { gap: 8px; }
  .city-wrap { width: 100%; }
  .examples { width: 100%; }
  .caps { gap: 7px; font-size: 11.5px; }
}
</style>
