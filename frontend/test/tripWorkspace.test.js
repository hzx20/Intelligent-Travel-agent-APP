import test from 'node:test'
import assert from 'node:assert/strict'
import {validWorkspaceTrip,workspaceView,workspaceTabs} from '../src/utils/tripWorkspace.js'
test('工作区视图和日期解析遵循当前旅行权限',()=>{
  const t={id:1,version:1,title:'旅行',days:[{id:1,day_number:1,items:[]},{id:2,day_number:2,items:[]}],places:[]}
  assert(validWorkspaceTrip(t,1));assert(!validWorkspaceTrip(t,2));assert(!validWorkspaceTrip({...t,days:[]},1))
  assert.equal(workspaceView({guide:'2'},t,false).panel,'assistant');assert.equal(workspaceView({guide:'2',panel:'itinerary'},t,false).panel,'itinerary')
  assert.equal(workspaceView({panel:'sharing'},t,true).panel,'itinerary');assert.equal(workspaceView({day:'2'},t,false).day.id,2);assert.equal(workspaceView({day:['2']},t,false).day,null)
  assert.equal(workspaceTabs(true).length,5);assert.equal(workspaceTabs(false).length,6)
})
