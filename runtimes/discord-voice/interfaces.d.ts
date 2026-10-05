/** Offline-tested boundaries only; no built-in network transport or credentials. */
export interface Scope { readonly guildId: string; readonly channelId: string; readonly userId: string; }
export interface Context { readonly signal: AbortSignal; }
export interface PCMFormat { readonly sampleRate: 48000; readonly channels: 1; readonly frameMs: 20; readonly samples: 960; }
export interface FakeTransport {
  readonly kind: 'fake';
  connect(channel: Pick<Scope, 'guildId' | 'channelId'>, context: Context): Promise<{ botUserId: string }>;
  play(pcm: Int16Array, context: Context & { format: PCMFormat }): Promise<void>;
  stop(): void;
  disconnect(): void;
}
export interface FakeSTT {
  readonly kind: 'fake';
  transcribe(pcm: Int16Array, context: Context & { format: PCMFormat; scope: Scope }): Promise<string>;
}
export interface FakeConversation {
  readonly kind: 'fake';
  reply(turn: { scope: Scope; turnId: string; transcript: string }, context: Context): Promise<string>;
}
export interface FakeTTS {
  readonly kind: 'fake';
  synthesize(reply: string, context: Context & { format: PCMFormat; scope: Scope }): Promise<Int16Array>;
}
/** Standalone injectable boundary; NOT accepted by fake-only VoiceRuntime. */
export interface VoicevoxHttpRequest {
  readonly url: string;
  readonly method: 'POST';
  readonly headers: Readonly<{ 'Content-Type': 'application/json' }>;
  readonly body: string;
  readonly signal: AbortSignal;
  /** Transport must enforce this while reading, before unbounded allocation. */
  readonly maxBytes: number;
}
export interface InjectedVoicevoxAdapter {
  readonly kind: 'voicevox-injected';
  synthesize(reply: string, context: Context & { format: PCMFormat; scope: Scope }): Promise<Int16Array>;
}
/** Returned bytes transfer ownership to adapter, including after cancellation. */
export type VoicevoxRequest = (request: VoicevoxHttpRequest) => Promise<{ status: number; body: Uint8Array }>;
export interface Activation extends Scope {
  readonly session: number;
  readonly turnId: string;
  readonly receive?: boolean;
  readonly respond?: boolean;
}
export interface DecodedFrame extends Scope {
  readonly session: number;
  readonly isBot: boolean;
  readonly sequence: number;
  readonly samples: Int16Array;
}
