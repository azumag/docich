import assert from 'node:assert/strict';
import test from 'node:test';
import { ChannelType } from 'discord.js';
import { ResidentController } from './resident.mjs';
function fixture({id='10',channelId='20',allowed=true}={}) {
  const replies=[];
  const channel=channelId ? {id:channelId,type:ChannelType.GuildVoice} : null;
  const guild={id,members:{fetch:async()=>({voice:{channel}})}};
  const interaction={guild,user:{id:'30'},commandName:'join',memberPermissions:{has:()=>allowed},isChatInputCommand:()=>true,
    async deferReply(){this.deferred=true;},async reply(v){replies.push(v);},async editReply(v){replies.push(v);}};
  return {interaction,replies,channel,guild};
}
test('join chooses the invoking member VC without configured IDs, remains resident, and leave closes it',async()=>{
  let resolve; let stopped=0; const starts=[];
  const session={done:new Promise(r=>{resolve=r;}),stop(){stopped++;resolve();}};
  const c=new ResidentController({startSession:target=>{starts.push(target);return session;},emit:()=>{}});
  const f=fixture(); await c.handle(f.interaction);
  assert.equal(starts.length,1); assert.equal(starts[0].channel,f.channel); assert.equal(stopped,0);
  await c.handle(f.interaction); assert.equal(starts.length,1);
  const other=fixture({channelId:'21'}); await c.handle(other.interaction); assert.equal(starts.length,1);
  f.interaction.commandName='leave'; await c.handle(f.interaction); assert.equal(stopped,1);
  await c.close();
});
test('unprivileged and no-VC requests do not start capture',async()=>{
  let calls=0; const c=new ResidentController({startSession:()=>{calls++;},emit:()=>{}});
  await c.handle(fixture({allowed:false}).interaction);
  await c.handle(fixture({channelId:null}).interaction);
  assert.equal(calls,0); await c.close();
});
test('shutdown during member resolution prevents a late join',async()=>{
  let release; const gate=new Promise(r=>{release=r;}); let calls=0;
  const f=fixture(); f.guild.members.fetch=async()=>{await gate;return {voice:{channel:f.channel}};};
  const c=new ResidentController({startSession:()=>{calls++;},emit:()=>{}});
  const pending=c.handle(f.interaction); await new Promise(r=>setImmediate(r));
  await c.close(); release(); await pending; assert.equal(calls,0);
});

test('failed connection informs the command user and allows retry',async()=>{
  const f=fixture(); let starts=0;
  const c=new ResidentController({startSession:()=>{starts++;return {done:Promise.resolve(2),stop(){}};},emit:()=>{}});
  await c.handle(f.interaction); await new Promise(r=>setImmediate(r));
  assert.ok(f.replies.some(v=>typeof v==='string' && v.includes('VC接続が終了')));
  await c.handle(f.interaction); assert.equal(starts,2); await c.close();
});

test('simultaneous guild requests do not exceed eight resident connections',async()=>{
  let starts=0; const c=new ResidentController({startSession:()=>{starts++;let resolve;return {done:new Promise(r=>{resolve=r;}),stop(){resolve(0);}};},emit:()=>{}});
  await Promise.all(Array.from({length:16},(_,i)=>c.handle(fixture({id:String(100+i)}).interaction)));
  assert.equal(starts,8); await c.close();
});
