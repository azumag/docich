import { CloudflareWhisperSTT, loadCloudflareSttConfig } from './cloudflare-stt.mjs';
import { LocalWhisperSTT, loadLocalSttConfig } from './local-stt.mjs';
export function loadSttConfig(env=process.env) {
  const provider=env.DOCICH_DISCORD_VOICE_STT_PROVIDER ?? 'cloudflare';
  if(provider==='local') return {provider,...loadLocalSttConfig(env)};
  if(provider==='cloudflare') return {provider,...loadCloudflareSttConfig(env)};
  throw Error('invalid_stt_provider');
}
export function createStt(env=process.env) {
  const {provider}=loadSttConfig(env);
  return provider==='local'?new LocalWhisperSTT({env}):new CloudflareWhisperSTT({env});
}
