// Native QtWebChannel binding. Only media/signaling data crosses this channel.
import { XboxVoiceEngine, attachVoiceBridge, sanitizeError } from './xbox_voice_engine.js';
import { createNativeBridgeProxy } from './voice_transport.js';

new QWebChannel(qt.webChannelTransport, channel => {
  const host = channel.objects.voiceHost;
  const nonce = host.nonce;
  const emit = frame => host.receive(JSON.stringify({source:'xodus-voice', nonce, ...frame}));
  const proxy = createNativeBridgeProxy({nonce, transport:{postMessage: msg => host.receive(JSON.stringify(msg))}});
  const engine = new XboxVoiceEngine(proxy.api, state => {
    emit({event:'state',state:{connected:state.connected === true, muted:state.muted !== false,
      error:sanitizeError(state.error),mutedMembers:Array.isArray(state.mutedMembers)?state.mutedMembers:[],
      speakingMembers:Array.isArray(state.speakingMembers)?state.speakingMembers:[],
      speakingAvailable:state.speakingAvailable === true}});
  });
  host.reply.connect(raw => proxy.handleParentReply(raw));
  host.command.connect(raw => window.dispatchEvent(new MessageEvent('message', {data:raw})));
  const bridge = attachVoiceBridge(engine, {nonce, transport:{postMessage: raw => host.receive(raw)}});
  window.addEventListener('unload', () => {bridge.dispose(); proxy.teardown();}, {once:true});
});
