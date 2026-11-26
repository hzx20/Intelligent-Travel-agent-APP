import test from 'node:test'
import assert from 'node:assert/strict'
import {newCommentAttempt,saveCommentAttempt,readCommentAttempt,clearCommentAttempt} from '../src/store/commentAttempt.js'
async function storage(run){const previous=globalThis.sessionStorage,data=new Map();globalThis.sessionStorage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};try{await run(data)}finally{globalThis.sessionStorage=previous}}
test('评论已提交快照按账号和景点隔离，恢复同一标识',async()=>storage(()=>{
  const attempt=newCommentAttempt({content:'评论正文',rating:4});saveCommentAttempt(1,2,attempt)
  const restored=readCommentAttempt(1,2);assert.equal(restored.request_id,attempt.request_id);assert(Object.isFrozen(restored.payload))
  assert.equal(readCommentAttempt(2,2),null);assert.equal(readCommentAttempt(1,3),null)
  clearCommentAttempt(1,2,'other');assert(readCommentAttempt(1,2));clearCommentAttempt(1,2,attempt.request_id);assert.equal(readCommentAttempt(1,2),null)
}))
test('损坏或错账号/景点的评论拒绝恢复，明确放弃才清除',async()=>storage(data=>{
  saveCommentAttempt(1,2,newCommentAttempt({content:'正文',rating:null}));data.set('travel.comment-creation.v1.2.2',data.get('travel.comment-creation.v1.1.2'))
  assert.throws(()=>readCommentAttempt(2,2),/不一致/);data.set('travel.comment-creation.v1.1.2','broken');assert.throws(()=>readCommentAttempt(1,2),/无法恢复/)
  clearCommentAttempt(1,2);assert.equal(readCommentAttempt(1,2),null);assert.throws(()=>newCommentAttempt({content:'x',rating:6}),/无法恢复/)
}))
test('无法暂存评论时明确报错，不假装已保留',async()=>storage(()=>{sessionStorage.setItem=()=>{throw Error('quota')};assert.throws(()=>saveCommentAttempt(1,2,newCommentAttempt({content:'正文',rating:null})),/无法保留/);assert.equal(readCommentAttempt(1,2),null)}))
