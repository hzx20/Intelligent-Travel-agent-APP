<script setup>
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {api} from '../api'
import {useAuth} from '../store/auth'
import {createReadResource} from '../store/readResource'
import {discoveryQuery,discoveryRoute,discoveryPath,validPage} from '../utils/discoveryData'
import {pageList} from '../utils/pagination'

const kind='guides',route=useRoute(),router=useRouter(),{user,openLogin}=useAuth()
const keyword=ref(''),city=ref(''),mode=ref('new')
const resource=createReadResource({read:q=>api.get(discoveryPath(q,kind)),validate:(r,q)=>validPage(r,q,kind),initial:{items:[],total:0,page:1},owner:()=>user.value?.id||0})
const source=resource.state,items=computed(()=>source.data.items),total=computed(()=>source.data.total),page=computed(()=>source.data.page),pages=computed(()=>Math.max(1,Math.ceil(total.value/10))),pageItems=computed(()=>pageList(page.value,pages.value))
const CITIES=computed(()=>[...new Set(['','成都','杭州','西安','北京','三亚',city.value])])
const applied=computed(()=>source.applied||discoveryQuery({},kind))
watch(()=>[route.query,user.value?.id||0],([query,uid],previous)=>{
  if(previous && uid!==previous[1])resource.reset()
  const q=discoveryQuery(query,kind);keyword.value=q.keyword;city.value=q.city;mode.value=q.sort
  resource.load(q)
},{immediate:true,deep:true,flush:'sync'})
onBeforeUnmount(()=>resource.dispose())
async function commit(q){const before=route.fullPath;await router.push({query:discoveryRoute(q,kind)});if(before===route.fullPath)resource.load(q)}
function search(){return commit({page:1,keyword:keyword.value.trim(),city:city.value,sort:mode.value})}
function goPage(n){if(n<1||n>pages.value||n===page.value)return;return commit({...applied.value,page:n})}
function writeGuide(){if(!user.value){openLogin('login');return}router.push('/guides/new')}
</script>

<template>
  <div class="wrap">
    <div class="filters">
      <input
        v-model="keyword" aria-label="搜索攻略" maxlength="200" class="finput"
        placeholder="🔍 多个关键词空格分隔，如：成都 亲子（标题/正文/关联景点）"
        @keyup.enter="search"
      />
      <select v-model="city" aria-label="城市筛选" class="fsel" @change="search">
        <option v-for="c in CITIES" :key="c" :value="c">{{ c || '全部城市' }}</option>
      </select>
      <select v-model="mode" aria-label="攻略排序" class="fsel" @change="search">
        <option value="new">最新发布</option>
        <option value="hot">最多浏览</option>
      </select>
      <button class="btn-search" @click="search">搜索</button>
    </div>

    <p v-if="source.loaded" class="applied-query">当前结果：{{ applied.city || '全部城市' }} · {{ applied.sort === 'hot' ? '最多浏览' : '最新发布' }}<template v-if="applied.keyword"> · {{ applied.keyword }}</template></p>
    <div class="bar">
      <b>全部攻略 <span v-if="source.loaded" class="tag">{{ total }} 篇</span></b>
      <button class="btn-write" @click="writeGuide">✍️ 写攻略{{ user ? '' : '（登录后开放）' }}</button>
    </div>

    <p v-if="source.loading" class="loading" role="status">正在更新结果…</p>
    <p v-if="source.error" class="empty list-error" role="alert">暂时无法更新：{{ source.error }} <span v-if="source.loaded">仍显示上次已加载的结果。</span><button :disabled="source.loading" @click="resource.retry">重试本次查询</button></p>
    <p v-if="source.loaded && !source.loading && !source.error && !items.length" class="empty">{{ total && page > pages ? '当前页已超出结果范围，请返回第一页。' : '没有符合条件的内容，试试其他关键词或城市。' }} <button v-if="total && page > pages" @click="goPage(1)">返回第一页</button></p>

    <div v-if="items.length" class="aflow">
      <router-link v-for="g in items" :key="g.id" class="acard" :to="`/guides/${g.id}`">
        <div
          class="cover"
          :style="g.cover_image ? { backgroundImage: `url(${g.cover_image})` } : {}"
        >
          <span v-if="!g.cover_image" class="ph-txt">路线与旅行灵感</span>
        </div>
        <div class="ai">
          <b>{{ g.title }}</b>
          <small>
            {{ g.author }} · {{ g.created_at.slice(0, 10) }}
            · 👁 {{ g.views }} · 📍 关联 {{ g.spot_count }} 个
          </small>
          <p class="desc">{{ g.summary }}</p>
        </div>
      </router-link>
    </div>

    <nav v-if="pages > 1" class="pager" aria-label="攻略结果分页">
      <button class="pg" :disabled="page === 1" @click="goPage(Math.min(page - 1,pages))">上一页</button>
      <template v-for="(p, i) in pageItems" :key="i">
        <span v-if="p === '...'" class="pg ellipsis">…</span>
        <button v-else class="pg" :class="{ on: p === page }" :aria-current="(p === page) ? 'page' : undefined" @click="goPage(p)">{{ p }}</button>
      </template>
      <button class="pg" :disabled="page === pages" @click="goPage(page + 1)">下一页</button>
    </nav>
  </div>
</template>

<style scoped>
.list-error{padding:16px;text-align:left}.list-error button,.empty button{min-height:44px;border:1px solid var(--green-border);border-radius:8px;background:#fff;color:var(--green);padding:8px 12px;margin-left:8px}.applied-query{font-size:12px;color:#73847b;line-height:1.7;margin:8px 0 12px;overflow-wrap:anywhere}.filters{flex-wrap:wrap}.finput{min-width:0;flex-basis:200px}.fsel,.btn-search,.btn-write,.pager .pg{min-height:44px}.pager{flex-wrap:wrap}.acard:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #548b73;outline-offset:3px}.ai{min-width:0;overflow-wrap:anywhere}

.filters { display: flex; gap: 10px; margin-bottom: 12px; }
.finput {
  flex: 1; border: 1px solid var(--line); border-radius: 10px;
  padding: 9px 12px; font-size: 13.5px; background: #fff; font-family: inherit;
}
.finput:focus { outline: none; border-color: var(--green); }
.fsel {
  border: 1px solid var(--line); border-radius: 10px; padding: 9px 10px;
  font-size: 13px; background: #fff; font-family: inherit; color: #555;
}
.btn-search, .btn-write {
  border: none; border-radius: 10px; padding: 9px 16px;
  font-size: 13.5px; background: var(--green); color: #fff; white-space: nowrap;
}
.bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 0 12px; font-size: 15px;
}
.bar .tag {
  font-size: 12px; font-weight: 400; color: var(--green);
  background: var(--green-soft); border: 1px solid var(--green-border);
  padding: 1px 8px; border-radius: 20px; margin-left: 4px;
}
.aflow { display: flex; flex-direction: column; gap: 12px; }
.acard {
  display: flex; gap: 14px; background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); overflow: hidden; cursor: pointer;
  transition: transform .15s, box-shadow .15s;
}
.acard:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(26,110,80,.12); }
.cover {
  width: 200px; min-height: 118px; flex-shrink: 0;
  background: linear-gradient(135deg, #2a8a67, #1a6e50);
  background-size: cover; background-position: center;
  display: flex; align-items: center; justify-content: center;
}
.ph-txt { color: rgba(255,255,255,.9); font-size: 12px; }
.ai { padding: 12px 14px 12px 0; display: flex; flex-direction: column; gap: 5px; }
.ai b { font-size: 15px; line-height: 1.4; }
.ai small { color: var(--text-sub); font-size: 12px; }
.desc {
  color: #666; font-size: 12.5px; line-height: 1.6;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
@media (max-width: 640px) {
  .filters { flex-wrap: wrap; }
  .finput { flex: 1 1 100%; }
  .acard { flex-direction: column; }
  .cover { width: 100%; height: 150px; min-height: 0; }
  .ai { padding: 10px 12px 12px; }
}
</style>
