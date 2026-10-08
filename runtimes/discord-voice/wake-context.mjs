// Session-local context is separate from acknowledged conversational memory.
const MAX_AGE_MS = 30 * 60_000;
const MAX_ENTRIES = 256;
const MAX_CHARS = 64_000;
export class WakeContext {
  #entries = [];
  #now;
  #expiry=null;
  constructor({now = Date.now} = {}) { this.#now = now; }
  #prune() {
    clearTimeout(this.#expiry);
    const cutoff = this.#now() - MAX_AGE_MS;
    this.#entries = this.#entries.filter(e => e.at > cutoff);
    while (this.#entries.length > MAX_ENTRIES || this.#entries.reduce((n,e)=>n+e.text.length,0)>MAX_CHARS) this.#entries.shift();
    if(this.#entries.length) {
      this.#expiry=setTimeout(()=>this.#prune(), Math.max(1,this.#entries[0].at+MAX_AGE_MS-this.#now()));
      this.#expiry.unref?.();
    }
  }
  observe(userId, text) {
    if (typeof userId !== 'string' || !/^[1-9][0-9]{0,19}$/.test(userId) || typeof text !== 'string' || !text.trim() || text.length > 2000) return null;
    this.#prune();
    const prior = this.recent();
    const normalized = text.trim();
    this.#entries.push({userId, text:normalized, at:this.#now()});
    this.#prune();
    // Accept separated STT homophones at any position, including a trailing call.
    // Separators exclude どうして/どうしよう/友達同士, while preserving
    // the existing handling of a leading quoted call.
    const spelling = normalized.normalize('NFKC');
    const wake = spelling.includes('同志')
      || /^[「『(]*(?:同士|どうし|ドウシ)(?:[\s、,。.!！?？:：」』)]|$)/u.test(spelling)
      || /(?:^|[\s、,。.!！?？:：])(?:同士|どうし|ドウシ)(?=[\s、,。.!！?？:：]|$)/u.test(spelling);
    return wake ? {transcript:normalized, userId, recentContext:prior} : null;
  }
  recent() {
    this.#prune();
    const selected=[];
    let chars=0;
    for (let i=this.#entries.length-1; i>=0 && selected.length<12; i--) {
      const e=this.#entries[i];
      if (chars+e.text.length>2000) break;
      selected.unshift({userId:e.userId,text:e.text}); chars+=e.text.length;
    }
    return selected;
  }
  forget(userId) { this.#entries=this.#entries.filter(e=>e.userId!==userId); }
  clear() { clearTimeout(this.#expiry); this.#entries=[]; }
}

// Transcription/context continue while a reply plays. A complete reply lifecycle
// (including its post-playback acknowledgement) finishes before the next starts.
const MAX_PENDING_REPLIES = 8;
export class WakeSession {
  #context; #reply; #emit; #allow; #active=null; #pending=[]; #stopped=false;
  constructor({reply, emit=()=>{}, now, allowReply=()=>true}={}) {
    if(typeof reply!=='function' || typeof allowReply!=='function') throw new TypeError('invalid_wake_session');
    this.#reply=reply; this.#emit=emit; this.#allow=allowReply; this.#context=new WakeContext({now});
  }
  #event(event) { try { this.#emit({event}); } catch {} }
  observe(text,{userId,signal}={}) {
    if(this.#stopped || signal?.aborted) return;
    const request=this.#context.observe(userId,text);
    if(!request) { this.#event('voice_context_updated'); return; }
    this.#event('voice_wake_detected');
    if(this.#pending.length>=MAX_PENDING_REPLIES) { this.#event('voice_reply_queue_full'); return; }
    this.#pending.push(request);
    if(this.#active) this.#event('voice_reply_queued');
    this.#drain();
  }
  #drain() {
    if(this.#stopped || this.#active) return;
    let request;
    while(this.#pending.length) {
      const candidate=this.#pending.shift();
      let allowed=false;
      try { allowed=this.#allow(candidate.userId)===true; } catch {}
      if(allowed) { request=candidate; break; }
      this.#event('voice_reply_skipped');
    }
    if(!request) return;
    const state={controller:new AbortController()}; this.#active=state;
    void Promise.resolve().then(()=>{
      if(this.#stopped || state.controller.signal.aborted) return;
      return this.#reply(request.transcript,{userId:request.userId,recentContext:request.recentContext,signal:state.controller.signal});
    }).catch(()=>this.#event('voice_wake_reply_failed')).finally(()=>{
      if(this.#active===state) this.#active=null;
      state.controller.abort(); this.#drain();
    });
  }
  stop() { this.#stopped=true; this.#pending=[]; this.#active?.controller.abort(); this.#context.clear(); }
}
