import test from 'node:test'
import assert from 'node:assert/strict'
import {mergeUserInfo} from '../src/store/profileInfo.js'
test('迟到身份核对不回退已保存昵称和头像，其他身份字段仍更新',()=>{
  const current={id:1,nickname:'新昵称',avatar:'new',profile_version:2,is_admin:false}
  const merged=mergeUserInfo(current,{id:1,nickname:'旧昵称',avatar:'old',profile_version:1,is_admin:true})
  assert.equal(merged.nickname,'新昵称');assert.equal(merged.avatar,'new');assert.equal(merged.profile_version,2);assert.equal(merged.is_admin,true)
  assert.equal(current.is_admin,false)
})
test('更新版本正常采用，无版本旧响应保留已知资料',()=>{
  const current={id:1,nickname:'原昵称',profile_version:2,avatar:'a'}
  assert.equal(mergeUserInfo(current,{id:1,nickname:'新昵称',profile_version:3,avatar:'b'}).nickname,'新昵称')
  assert.equal(mergeUserInfo(current,{id:1,nickname:'未带版本',avatar:'c'}).nickname,'原昵称')
})
test('切换账号不携带前一账号的资料或版本',()=>{
  assert.deepEqual(mergeUserInfo({id:1,nickname:'私有旧资料',profile_version:5},{id:2,nickname:'新账号',profile_version:1}),{id:2,nickname:'新账号',profile_version:1})
})
