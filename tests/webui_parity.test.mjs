import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source=readFileSync(new URL('../webui/app.js',import.meta.url),'utf8');
function setup(){
  const nodes=new Map();
  const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',checked:false,textContent:'',disabled:false});return nodes.get(id);};
  const context=vm.createContext({$,naturalDraft:{enabled:true,personas:{},group_settings:{room:{name:'draft',owner_wxid:'saved',owner_name:'Saved owner'}}},naturalRoom:'room',naturalMembers:[],naturalDirty:true,config:{groups:['room']},escape:s=>String(s??'').replaceAll('<','&lt;'),serviceAvailable:true,naturalMembersLoading:false,renderNaturalSettings(){},capturePersona(){},state:{connected:false}});
  for(const name of ['naturalGroupDefaults','captureNaturalGroup','renderNaturalOwner','loadNaturalConfig']){
    const start=source.indexOf('function '+name+'('),end=source.indexOf('\n}',start)+2;
    vm.runInContext(source.slice(start,end),context);
  }
  $('#natural-owner').value='saved';$('#natural-room-name').value='Unsaved name';$('#natural-persona').value='neutral';$('#natural-cooldown').value='42';$('#natural-hourly').value='8';$('#natural-reply-mode').value='text';
  return {context,$};
}
test('member refresh retains saved owner absent from snapshot',()=>{
  const {context,$}=setup();context.renderNaturalOwner();
  assert.equal($('#natural-owner').value,'saved');
  assert.match($('#natural-owner').innerHTML,/Saved owner/);
  context.captureNaturalGroup();
  assert.equal(context.naturalDraft.group_settings.room.owner_name,'Saved owner');
});
test('config refresh preserves unsaved group edits',()=>{
  const {context}=setup();context.loadNaturalConfig();
  assert.equal(context.naturalDraft.group_settings.room.name,'Unsaved name');
  assert.equal(context.naturalDraft.group_settings.room.cooldown_seconds,42);
  assert.equal(context.naturalDirty,true);
});
test('explicit unset clears both owner fields',()=>{
  const {context,$}=setup();$('#natural-owner').value='';context.captureNaturalGroup();
  assert.equal(context.naturalDraft.group_settings.room.owner_wxid,'');
  assert.equal(context.naturalDraft.group_settings.room.owner_name,'');
});
