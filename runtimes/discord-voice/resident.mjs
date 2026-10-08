import { EventEmitter } from 'node:events';
import { pathToFileURL } from 'node:url';
import { Client, Events, GatewayIntentBits, PermissionFlagsBits, ChannelType } from 'discord.js';
import { runLiveVoice } from './live.mjs';
import { loadSttConfig } from './stt-provider.mjs';
import { loadCloudflareConversationConfig } from './cloudflare-conversation.mjs';
import { createLiveVoicevoxTTS } from './live-voicevox.mjs';

export const RESIDENT_COMMANDS = Object.freeze([
  {name:'join',description:'DoCiAIを現在のVCに招待し、「同志」で呼びかけます',defaultMemberPermissions:PermissionFlagsBits.ManageGuild,contexts:[0]},
  {name:'leave',description:'DoCiAIをVCから退出させ、一時的な話題コンテキストを消します',defaultMemberPermissions:PermissionFlagsBits.ManageGuild,contexts:[0]},
]);
const emitDefault = record => console.log(JSON.stringify(record));

export class ResidentController {
  #sessions=new Map(); #busy=new Set(); #start; #emit; #closed=false;
  constructor({startSession,emit=emitDefault}={}) { this.#start=startSession; this.#emit=emit; }
  async handle(interaction) {
    if(!interaction.isChatInputCommand?.() || !['join','leave'].includes(interaction.commandName)) return;
    const guild=interaction.guild;
    if(!guild || !interaction.memberPermissions?.has(PermissionFlagsBits.ManageGuild)) {
      await interaction.reply({content:'この操作はサーバー管理権限のある人が、サーバー内で実行してください。',flags:64}); return;
    }
    if(this.#closed || this.#busy.has(guild.id)) {
      await interaction.reply({content:'接続状態を変更中です。少し待ってから再実行してください。',flags:64}); return;
    }
    this.#busy.add(guild.id);
    try {
      await interaction.deferReply();
      const existing=this.#sessions.get(guild.id);
      if(interaction.commandName==='leave') {
        existing?.stop();
        if(existing) await existing.done;
        await interaction.editReply('退出しました。このVCの一時的な話題コンテキストを破棄しました。'); return;
      }
      const member=await guild.members.fetch(interaction.user.id);
      if(this.#closed) return;
      const channel=member.voice?.channel;
      if(!channel || channel.type!==ChannelType.GuildVoice) {
        await interaction.editReply('先に参加したいボイスチャンネルへ入り、/join を実行してください。'); return;
      }
      if(existing) {
        await interaction.editReply(existing.channelId===channel.id ? 'このVCに常駐中です。「同志」を含む発話で呼びかけてください。' : '別のVCに常駐中です。先に /leave で退出させてください。'); return;
      }
      if(this.#sessions.size>=8) { await interaction.editReply('接続上限に達しています。'); return; }
      // Announce the background-context policy before subscribing to anybody.
      await interaction.editReply('このVCに参加します。Bot以外の参加者の発話を文字起こしし、直近30分の話題を一時的に保持します。「同志」を含む発話にだけ返答します。/leave で退出し、一時文脈を破棄します。');
      if(this.#closed) return;
      if(this.#sessions.size>=8) { await interaction.editReply('接続上限に達しています。'); return; }
      const session=this.#start({guild,channel});
      session.channelId=channel.id;
      this.#sessions.set(guild.id,session);
      const reportFailure=async()=>{
        this.#emit({event:'resident_session_failed'});
        try { await interaction.editReply('VC接続が終了しました。接続権限とサービス設定を確認してから /join を再実行してください。'); } catch {}
      };
      void session.done.then(code=>{ if(code!==undefined && code!==0) return reportFailure(); },reportFailure).finally(()=>{
        if(this.#sessions.get(guild.id)===session) this.#sessions.delete(guild.id);
      });
    } catch {
      this.#emit({event:'resident_command_failed'});
      try { if(interaction.deferred || interaction.replied) await interaction.editReply('VC操作に失敗しました。接続権限と設定を確認してください。'); } catch {}
    } finally { this.#busy.delete(guild.id); }
  }
  moved(guildId,channelId) {
    const session=this.#sessions.get(guildId);
    if(session && channelId && session.channelId!==channelId) session.stop();
  }
  async close() {
    this.#closed=true;
    const all=[...this.#sessions.values()];
    for(const s of all) s.stop();
    await Promise.allSettled(all.map(s=>s.done));
    this.#sessions.clear();
  }
}

export function startResidentSession(client,env,{guild,channel},emit=emitDefault,observers={}) {
  const signalTarget=new EventEmitter();
  const facade={
    user:client.user,guilds:client.guilds,
    login:async()=>{},destroy(){},
    on:client.on.bind(client),off:client.off.bind(client),
  };
  const sessionEnv={...env,
    DOCICH_DISCORD_VOICE_STT_PROVIDER:env.DOCICH_DISCORD_VOICE_STT_PROVIDER ?? 'local',
    DOCICH_DISCORD_VOICE_ENABLED:'1', DOCICH_DISCORD_VOICE_WAKE_ENABLED:'1',
    DOCICH_DISCORD_VOICE_GUILD_ID:guild.id,DOCICH_DISCORD_VOICE_CHANNEL_ID:channel.id,
    DOCICH_DISCORD_VOICE_RECEIVE_ENABLED:'1',DOCICH_DISCORD_VOICE_CONVERSATION_ENABLED:'1',DOCICH_DISCORD_VOICE_TTS_ENABLED:'1',
    DOCICH_DISCORD_VOICE_TRANSCRIPT_DEBUG:'0',DOCICH_DISCORD_VOICE_REPLY_DEBUG:'0',DOCICH_DISCORD_VOICE_TEST_TONE:'0',
  };
  return {
    stop:()=>signalTarget.emit('SIGINT'),
    done:runLiveVoice(sessionEnv,{createClient:()=>facade,waitClientReady:async()=>{},signalTarget,emit,onTranscriptText:observers.onTranscriptText,onSynthesisText:observers.onSynthesisText,getGameState:observers.getGameState,emitError:()=>emit({event:'resident_session_failed'})}),
  };
}

export async function runResident(env=process.env,{register=false,onTranscriptText,onSynthesisText,getGameState}={}) {
  env={...env,DOCICH_DISCORD_VOICE_STT_PROVIDER:env.DOCICH_DISCORD_VOICE_STT_PROVIDER ?? 'local'};
  if(typeof env.DOCICH_DISCORD_TOKEN!=='string' || env.DOCICH_DISCORD_TOKEN.length<20 || /\s/.test(env.DOCICH_DISCORD_TOKEN)) throw Error('invalid_resident_config');
  if(!register) {
    if(env.DOCICH_DISCORD_VOICE_RESIDENT_ENABLED!=='1') throw Error('resident_disabled');
    loadSttConfig(env);
    loadCloudflareConversationConfig(env);
    createLiveVoicevoxTTS(env,{allowLoopback:env.DOCICH_DISCORD_VOICE_VOICEVOX_ALLOW_LOOPBACK==='1'});
  }
  const client=new Client({intents:[GatewayIntentBits.Guilds,GatewayIntentBits.GuildVoiceStates]});
  const controller=new ResidentController({startSession:target=>startResidentSession(client,env,target,emitDefault,{onTranscriptText,onSynthesisText,getGameState})});
  let stopping=false; let resolveStop;
  const stopped=new Promise(r=>{resolveStop=r;});
  let readyTimer;
  const deadline=new Promise((_,reject)=>{readyTimer=setTimeout(()=>reject(Error("resident_ready_timeout")),30000);});
  const stop=()=>{stopping=true;resolveStop();};
  const onInteraction=i=>{void controller.handle(i).catch(()=>emitDefault({event:'resident_command_failed'}));};
  process.once('SIGINT',stop); process.once('SIGTERM',stop);
  client.on(Events.Error,()=>emitDefault({event:'resident_gateway_error'}));
  client.on(Events.VoiceStateUpdate,(_old,next)=>{
    if(next.id===client.user?.id) controller.moved(next.guild.id,next.channelId);
  });
  try {
    const ready=new Promise(resolve=>client.once(Events.ClientReady,resolve));
    await Promise.race([client.login(env.DOCICH_DISCORD_TOKEN),stopped,deadline]);
    if(stopping) return;
    await Promise.race([ready,stopped,deadline]);
    if(stopping) return;
    clearTimeout(readyTimer);
    const commands=await client.application.commands.fetch();
    for(const desired of RESIDENT_COMMANDS) {
      if(stopping) return;
      const found=commands.find(c=>c.name===desired.name);
      if(found && found.description!==desired.description) throw Error('command_name_conflict');
      if(!found) {
        if(!register) throw Error('resident_commands_missing');
        await client.application.commands.create(desired);
      }
    }
    if(stopping) return;
    if(register) { emitDefault({event:'resident_commands_registered'}); return; }
    client.on(Events.InteractionCreate,onInteraction);
    emitDefault({event:'resident_ready'});
    await stopped;
  } finally {
    clearTimeout(readyTimer);
    client.off(Events.InteractionCreate,onInteraction);
    process.off('SIGINT',stop); process.off('SIGTERM',stop);
    await controller.close(); client.destroy();
  }
}

if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  const arg=process.argv.slice(2);
  try {
    if(arg.length!==1 || !['--resident','--register-commands'].includes(arg[0])) throw Error('invalid_mode');
    await runResident(process.env,{register:arg[0]==='--register-commands'});
  } catch { emitDefault({event:'resident_start_failed'}); process.exitCode=2; }
}
