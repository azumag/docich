import { PCM } from './runtime.mjs';
import { pcmToWav } from './cloudflare-stt.mjs';
export class LocalSttError extends Error {
  constructor(code) { super(code); this.code=code; this.name='LocalSttError'; }
}
export function loadLocalSttConfig(env=process.env) {
  try {
    const url=new URL(env.DOCICH_DISCORD_VOICE_LOCAL_STT_URL ?? 'http://127.0.0.1:8765/transcribe');
    if(url.protocol!=='http:' || !['127.0.0.1','[::1]'].includes(url.hostname) || url.username || url.password || url.pathname!=='/transcribe' || url.search || url.hash) throw Error();
    return Object.freeze({url:url.toString()});
  } catch { throw new LocalSttError('invalid_local_stt_config'); }
}
export class LocalWhisperSTT {
  #config; #request;
  constructor({env=process.env,request=fetch}={}) {
    this.#config=loadLocalSttConfig(env);
    if(typeof request!=='function') throw new LocalSttError('invalid_local_stt_config');
    this.#request=request;
  }
  async transcribe(pcm,{format=PCM,signal}={}) {
    if(!(signal instanceof AbortSignal)) throw new LocalSttError('invalid_stt_context');
    let wav; let bytes;
    try {
      signal.throwIfAborted(); wav=pcmToWav(pcm,format);
      const response=await this.#request(this.#config.url,{method:'POST',headers:{'Content-Type':'audio/wav'},body:wav,signal,redirect:'error'});
      if(!response.ok || !response.body) throw Error();
      const reader=response.body.getReader(); const chunks=[]; let total=0;
      try {
        while(true) {
          signal.throwIfAborted(); const {done,value}=await reader.read(); if(done) break;
          total+=value.byteLength;
          if(total>16384) { await reader.cancel(); throw Error(); }
          chunks.push(value);
        }
        bytes=new Uint8Array(total);let offset=0;
        for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;chunk.fill(0);}
      } finally {reader.releaseLock();}
      signal.throwIfAborted();
      const result=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
      if(typeof result.text!=='string' || result.text.length>2000) throw Error();
      return result.text.trim();
    } catch { throw new LocalSttError(signal.aborted?'stt_cancelled':'local_stt_failed'); }
    finally {wav?.fill(0);bytes?.fill(0);}
  }
}
