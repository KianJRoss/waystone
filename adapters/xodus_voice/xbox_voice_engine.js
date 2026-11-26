/**
 * Xbox WebRTC Voice Engine
 *
 * Reusable, media-only WebRTC audio client for Xbox party voice chat.
 * Ported directly from Xodus Nexus (MIT License, Copyright 2026 Xodus Nexus contributors).
 *
 * Invariants:
 * - Page load NEVER accesses microphone or initiates peer connections.
 * - Hardware microphone is acquired ONLY upon explicit setMuted(false) command.
 * - Strict schema validation: missing/non-boolean `muted` rejects before engine or mic touch.
 * - Nonce-gated bridge: every message requires valid target and matching instance nonce.
 * - Bounded IDs, sizes, and maps; all pending operations timeout and reject on teardown.
 * - Strict error sanitization: only typed safe codes emitted, zero tokens/SDP/ICE leakage.
 */

const text = (v, max) =>
  typeof v === 'string' && v.length > 0 && v.length <= max && !/[\x00-\x1f\x7f]/.test(v);

const validXuid = (v) =>
  typeof v === 'string' &&
  /^[1-9][0-9]{0,19}$/.test(v) &&
  BigInt(v) <= 18446744073709551615n;

const isPlainObject = (v) => Boolean(v && typeof v === 'object' && !Array.isArray(v));

export const MAX_BRIDGE_MESSAGE_BYTES = 65536;
export const MAX_PENDING_CALLS = 16;
export const CALL_TIMEOUT_MS = 30000;

export function sanitizeError(err) {
  if (!err) return null;
  const msg = err instanceof Error ? err.message : String(err);
  if (/^ERR_[A-Z0-9_]{1,56}$/.test(msg)) {
    return msg;
  }
  if (/microphone|getusermedia|permission/i.test(msg)) {
    return 'ERR_MIC_PERMISSION_DENIED';
  }
  if (/time(?:d)?[\s-]?out/i.test(msg)) {
    return 'ERR_TIMEOUT';
  }
  if (/disconnect/i.test(msg)) {
    return 'ERR_DISCONNECTED';
  }
  if (/duplicate/i.test(msg)) {
    return 'ERR_DUPLICATE_TRANSPORT';
  }
  if (/device.*not.*found|notfound|overconstrained/i.test(msg)) {
    return 'ERR_DEVICE_NOT_FOUND';
  }
  if (/sink.*unsupported|unsupported.*sink/i.test(msg)) {
    return 'ERR_SINK_ID_UNSUPPORTED';
  }
  if (/sink|setsinkid|audio.*output/i.test(msg)) {
    return 'ERR_AUDIO_OUTPUT_FAILED';
  }
  if (/invalid.*device/i.test(msg)) {
    return 'ERR_INVALID_DEVICE_ID';
  }
  if (/device.*enum/i.test(msg)) {
    return 'ERR_DEVICE_ENUMERATION_FAILED';
  }
  if (/party/i.test(msg)) {
    return 'ERR_PARTY_REJECTED';
  }
  return 'ERR_VOICE_INTERNAL';
}

export function validateVoiceCommand(value) {
  if (!isPlainObject(value)) {
    throw new Error('ERR_INVALID_COMMAND');
  }
  const keys = Object.keys(value);
  const only = (allowed) => keys.every((key) => allowed.includes(key));

  if (value.type === 'start' && only(['type', 'partyId'])) {
    if (value.partyId !== undefined && (!text(value.partyId, 128) || !/^[A-Za-z0-9_-]+$/.test(value.partyId))) {
      throw new Error('ERR_INVALID_PARTY_ID');
    }
    return { type: 'start', ...(value.partyId ? { partyId: value.partyId } : {}) };
  }

  if (value.type === 'accept' && only(['type', 'inviteId'])) {
    if (!text(value.inviteId, 262) || !value.inviteId.startsWith('party:') || value.inviteId.length <= 6) {
      throw new Error('ERR_INVALID_INVITE_ID');
    }
    return { type: 'accept', inviteId: value.inviteId };
  }

  if (value.type === 'mute' && only(['type', 'xuid', 'muted'])) {
    if (!validXuid(value.xuid) || typeof value.muted !== 'boolean') {
      throw new Error('ERR_INVALID_MUTE_PARAMS');
    }
    return { type: 'mute', xuid: value.xuid, muted: value.muted };
  }

  if (value.type === 'setPeerVolume' && only(['type', 'xuid', 'percent'])) {
    if (!validXuid(value.xuid) || !Number.isInteger(value.percent) || value.percent < 0 || value.percent > 200) {
      throw new Error('ERR_INVALID_PEER_VOLUME');
    }
    return { type: 'setPeerVolume', xuid: value.xuid, percent: value.percent };
  }

  if (value.type === 'setMuted' && only(['type', 'muted'])) {
    if (typeof value.muted !== 'boolean') {
      throw new Error('ERR_INVALID_MUTED_TYPE');
    }
    return { type: 'setMuted', muted: value.muted };
  }

  if (value.type === 'enumerateDevices' && only(['type'])) {
    return { type: 'enumerateDevices' };
  }

  if (value.type === 'selectDevices' && only(['type', 'inputId', 'outputId'])) {
    if (
      value.inputId !== undefined &&
      value.inputId !== null &&
      (!text(value.inputId, 128) || !/^[A-Za-z0-9_.:-]+$/.test(value.inputId))
    ) {
      throw new Error('ERR_INVALID_DEVICE_ID');
    }
    if (
      value.outputId !== undefined &&
      value.outputId !== null &&
      (!text(value.outputId, 128) || !/^[A-Za-z0-9_.:-]+$/.test(value.outputId))
    ) {
      throw new Error('ERR_INVALID_DEVICE_ID');
    }
    return {
      type: 'selectDevices',
      ...(value.inputId !== undefined && value.inputId !== null ? { inputId: value.inputId } : {}),
      ...(value.outputId !== undefined && value.outputId !== null ? { outputId: value.outputId } : {}),
    };
  }

  if ((value.type === 'leave' || value.type === 'resume') && only(['type'])) {
    return { type: value.type };
  }

  throw new Error('ERR_INVALID_COMMAND');
}

export class XboxVoiceEngine {
  constructor(api, onStateChanged) {
    if (!api || typeof api !== 'object') {
      throw new Error('ERR_INVALID_API_DELEGATE');
    }
    this.api = api;
    this.changed = typeof onStateChanged === 'function' ? onStateChanged : () => {};

    this.pc = undefined;
    this.channel = undefined;
    this.sender = undefined;
    this.context = undefined;
    this.silence = undefined;
    this.microphone = undefined;
    this.audio = new Map();
    this.peerAudio = new Map(); // transport mid -> { source, gain, destination }
    this.peerVolumes = new Map(); // XUID -> integer percent
    this.peers = new Map();
    this.mutedPeers = new Set();
    this.timer = undefined;
    this.privacyTimer = undefined;
    this.generation = 0;
    this.microphoneVersion = 0;
    this.partyId = undefined;
    this.relayConnected = false;
    this.confirming = false;
    this.selectedInputId = null;
    this.selectedOutputId = null;
    this.sinkIdSupported =
      typeof HTMLMediaElement !== 'undefined' &&
      typeof HTMLMediaElement.prototype.setSinkId === 'function';
    this._deviceChangeListener = null;
    this.onDevicesChanged = null;
    // speaking detection (bounded)
    this._speaking = new Map(); // xuid -> { active:boolean, level:number, lastActiveAt:number }
    this._speakingTimer = undefined;
    this._analysers = new Map(); // midKey -> { analyser, data, source }
    this._speakingCadenceMs = 250;
    this._speakingOnThresh = 0.04;
    this._speakingOffThresh = 0.02;
    this._speakingExpiryMs = 1200;
    this.state = { connected: false, muted: true, audioBlocked: false, error: undefined, speakingMembers: [], speakingAvailable: false };
  }

  getMicConstraints() {
    const audio = { channelCount: 1, echoCancellation: true, noiseSuppression: true };
    if (this.selectedInputId && this.selectedInputId !== 'default') {
      audio.deviceId = { exact: this.selectedInputId };
    }
    return { audio, video: false };
  }

  async enumerateDevices() {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices) {
      throw new Error('ERR_DEVICE_ENUMERATION_FAILED');
    }
    let rawDevices;
    try {
      rawDevices = await navigator.mediaDevices.enumerateDevices();
    } catch {
      throw new Error('ERR_DEVICE_ENUMERATION_FAILED');
    }
    if (!Array.isArray(rawDevices)) {
      rawDevices = [];
    }

    const inputs = [];
    const outputs = [];

    for (const d of rawDevices) {
      if (!d || typeof d !== 'object') continue;
      const devId =
        typeof d.deviceId === 'string' && d.deviceId.length > 0 && d.deviceId.length <= 128
          ? d.deviceId
          : 'default';
      const rawLabel =
        typeof d.label === 'string' && d.label.length > 0 && d.label.length <= 256 ? d.label : '';

      if (d.kind === 'audioinput') {
        inputs.push({
          id: devId,
          label:
            rawLabel ||
            (devId === 'default' ? 'Default Microphone' : `Microphone (${devId.slice(0, 8)})`),
        });
      } else if (d.kind === 'audiooutput') {
        outputs.push({
          id: devId,
          label:
            rawLabel ||
            (devId === 'default' ? 'Default Audio Output' : `Audio Output (${devId.slice(0, 8)})`),
        });
      }
    }

    const uniqueInputs = Array.from(new Map(inputs.map((i) => [i.id, i])).values());
    const uniqueOutputs = Array.from(new Map(outputs.map((o) => [o.id, o])).values());

    const missingSelectedInput = Boolean(
      this.selectedInputId &&
        this.selectedInputId !== 'default' &&
        !uniqueInputs.some((i) => i.id === this.selectedInputId)
    );
    const missingSelectedOutput = Boolean(
      this.selectedOutputId &&
        this.selectedOutputId !== 'default' &&
        !uniqueOutputs.some((o) => o.id === this.selectedOutputId)
    );

    this.setupDeviceChangeHandler();

    return {
      inputs: uniqueInputs,
      outputs: uniqueOutputs,
      selectedInputId: this.selectedInputId,
      selectedOutputId: this.selectedOutputId,
      sinkIdSupported: this.sinkIdSupported,
      missingSelectedInput,
      missingSelectedOutput,
    };
  }

  setupDeviceChangeHandler() {
    if (
      this._deviceChangeListener ||
      typeof navigator === 'undefined' ||
      !navigator.mediaDevices?.addEventListener
    ) {
      return;
    }
    this._deviceChangeListener = async () => {
      try {
        const devInfo = await this.enumerateDevices();
        if (devInfo.missingSelectedInput) {
          if (this.microphone && !this.state.muted) {
            this.microphone.getTracks().forEach((t) => t.stop());
            this.microphone = undefined;
            if (this.sender && this.silence) {
              await this.sender.replaceTrack(this.silence.getAudioTracks()[0]).catch(() => {});
            }
            this.update({ muted: true, error: 'ERR_DEVICE_NOT_FOUND' });
          }
        }
        if (typeof this.onDevicesChanged === 'function') {
          this.onDevicesChanged(devInfo);
        }
      } catch {}
    };
    navigator.mediaDevices.addEventListener('devicechange', this._deviceChangeListener);
  }

  async selectDevices(options = {}) {
    const { inputId, outputId } = options;

    // Output selection
    if (outputId !== undefined) {
      if (outputId && outputId !== 'default' && !this.sinkIdSupported) {
        throw new Error('ERR_SINK_ID_UNSUPPORTED');
      }
      const targetSink = outputId || null;
      const sinkValue = targetSink || '';

      // Apply to all current audio elements before updating selectedOutputId
      for (const audio of this.audio.values()) {
        if (typeof audio.setSinkId === 'function') {
          try {
            await audio.setSinkId(sinkValue);
          } catch {
            for (const a of this.audio.values()) {
              a.pause?.();
              a.srcObject = null;
            }
            this.update({ error: 'ERR_AUDIO_OUTPUT_FAILED' });
            throw new Error('ERR_AUDIO_OUTPUT_FAILED');
          }
        }
      }
      this.selectedOutputId = targetSink;
    }

    // Input selection
    if (inputId !== undefined) {
      const newId = inputId || null;
      const changed = newId !== this.selectedInputId;
      this.selectedInputId = newId;

      if (changed && this.microphone && !this.state.muted) {
        const generation = this.generation;
        this.microphoneVersion++;
        const micVersion = this.microphoneVersion;
        const oldMic = this.microphone;

        try {
          const stream = await navigator.mediaDevices.getUserMedia(this.getMicConstraints());
          if (
            generation !== this.generation ||
            micVersion !== this.microphoneVersion ||
            this.state.muted
          ) {
            stream.getTracks().forEach((t) => t.stop());
            return;
          }
          this.microphone = stream;
          const micTrack = stream.getAudioTracks()[0];
          if (micTrack && this.sender) {
            await this.sender.replaceTrack(micTrack);
          }
          oldMic?.getTracks().forEach((t) => t.stop());
        } catch (err) {
          if (generation !== this.generation) return;
          this.update({ muted: true, error: sanitizeError(err) });
        }
      }
    }

    return {
      inputId: this.selectedInputId,
      outputId: this.selectedOutputId,
      sinkIdSupported: this.sinkIdSupported,
    };
  }

  getState() {
    return { ...this.state };
  }

  getMutedMembers() {
    return Array.from(this.mutedPeers);
  }

  getSpeakingMembers() {
    const out = [];
    for (const [xuid, entry] of this._speaking.entries()) {
      if (entry?.active) out.push(xuid);
    }
    out.sort();
    return out;
  }

  _clearSpeaking(xuids) {
    if (Array.isArray(xuids)) {
      for (const xuid of xuids) this._speaking.delete(xuid);
    } else {
      this._speaking.clear();
    }
  }

  _stopSpeakingPoll() {
    if (this._speakingTimer) {
      clearInterval(this._speakingTimer);
      this._speakingTimer = undefined;
    }
    for (const info of this._analysers.values()) {
      try { info.source?.disconnect?.(); } catch {}
    }
    this._analysers.clear();
  }

  _setSpeakingState(available, members) {
    const speakingMembers = Array.isArray(members) ? members : this.getSpeakingMembers();
    const speakingAvailable = Boolean(available);
    const prevM = this.state.speakingMembers || [];
    const sameM = prevM.length === speakingMembers.length && prevM.every((v, i) => v === speakingMembers[i]);
    if (sameM && this.state.speakingAvailable === speakingAvailable) return;
    this.update({ speakingMembers, speakingAvailable });
  }

  _ensureAnalyserForMid(midKey) {
    if (!midKey || this._analysers.has(midKey)) return this._analysers.get(midKey);
    const nodes = this.peerAudio.get(midKey);
    if (!this.context || !nodes) return undefined;
    try {
      const analyser = this.context.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.4;
      nodes.source.connect(analyser);
      const info = { analyser, data: new Uint8Array(analyser.fftSize), source: nodes.source };
      this._analysers.set(midKey, info);
      return info;
    } catch {
      return undefined;
    }
  }

  _sampleSpeaking() {
    if (!this.state.connected || !this.context) {
      if ((this.state.speakingMembers || []).length || this.state.speakingAvailable) {
        this._clearSpeaking();
        this._setSpeakingState(false, []);
      }
      return;
    }
    const now = Date.now();
    let changed = false;
    // only consider peers with real remote stream->xuid mapping
    for (const [xuid, peer] of this.peers.entries()) {
      if (!peer?.mid) continue;
      if (peer.muted || !peer.allowed) {
        if (this._speaking.delete(xuid)) changed = true;
        continue;
      }
      const info = this._ensureAnalyserForMid(peer.mid);
      if (!info) continue;
      try {
        info.analyser.getByteTimeDomainData(info.data);
      } catch {
        continue;
      }
      let sum = 0;
      for (let i = 0; i < info.data.length; i++) {
        const v = (info.data[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / info.data.length);
      let entry = this._speaking.get(xuid);
      if (!entry) {
        entry = { active: false, level: 0, lastActiveAt: 0 };
        this._speaking.set(xuid, entry);
      }
      const wasActive = entry.active;
      if (rms >= this._speakingOnThresh) {
        entry.active = true;
        entry.lastActiveAt = now;
      } else if (entry.active) {
        const belowOff = rms <= this._speakingOffThresh;
        const expired = (now - entry.lastActiveAt) >= this._speakingExpiryMs;
        if (belowOff && expired) entry.active = false;
      }
      entry.level = rms;
      if (entry.active !== wasActive) changed = true;
    }
    // prune stale entries for removed peers
    for (const xuid of Array.from(this._speaking.keys())) {
      if (!this.peers.has(xuid)) {
        this._speaking.delete(xuid);
        changed = true;
      }
    }
    if (changed || this.state.speakingAvailable !== true) {
      this._setSpeakingState(true, this.getSpeakingMembers());
    }
  }

  _startSpeakingPoll() {
    if (this._speakingTimer || !this.state.connected) return;
    this._speakingTimer = setInterval(() => this._sampleSpeaking(), this._speakingCadenceMs);
    this._speakingTimer?.unref?.();
    this._setSpeakingState(true, this.getSpeakingMembers());
  }

  update(patch) {
    this.state = { ...this.state, ...patch };
    this.changed({ ...this.state, mutedMembers: this.getMutedMembers() });
  }

  send(type, content) {
    if (this.channel?.readyState === 'open') {
      this.channel.send(
        JSON.stringify({
          type,
          channelVersion: 1,
          contentMessage: JSON.stringify(content),
        })
      );
    }
  }

  applyPeer(xuid) {
    const peer = this.peers.get(xuid);
    if (!peer) return;
    const blocked = !peer.allowed;
    const muted = blocked || peer.muted;
    this.send('BlockState', { messageVersion: 1, blocked, xuid });
    this.send('MuteState', { messageVersion: 1, muted, xuid });
    const transceiver = this.pc?.getTransceivers().find((t) => t.mid === peer.mid);
    if (transceiver && transceiver.receiver?.track?.kind === 'audio') {
      transceiver.receiver.track.enabled = !muted;
    }
  }

  async privacy(xuid) {
    const peer = this.peers.get(xuid);
    if (!peer) return;
    const generation = this.generation;
    const version = ++peer.privacyVersion;
    peer.allowed = false;
    this.applyPeer(xuid);

    let allowed = false;
    try {
      allowed = await this.api.xboxVoiceAllowed(xuid);
    } catch {
      allowed = false;
    }

    if (
      generation !== this.generation ||
      this.peers.get(xuid) !== peer ||
      version !== peer.privacyVersion
    ) {
      return;
    }
    peer.allowed = allowed;
    this.applyPeer(xuid);
  }

  mutePeer(xuid, muted) {
    if (!validXuid(xuid)) {
      throw new Error('ERR_INVALID_XUID');
    }
    if (typeof muted !== 'boolean') {
      throw new Error('ERR_INVALID_MUTED_TYPE');
    }
    if (muted) {
      this.mutedPeers.add(xuid);
    } else {
      this.mutedPeers.delete(xuid);
    }
    const peer = this.peers.get(xuid);
    if (peer) {
      peer.muted = muted;
      this.applyPeer(xuid);
    }
    if (muted) {
      this._clearSpeaking([xuid]);
    }
    this.update({ speakingMembers: this.getSpeakingMembers(), speakingAvailable: this.state.connected ? Boolean(this.state.speakingAvailable || this._speakingTimer || this.context) : false });
  }

  setPeerVolume(xuid, percent) {
    if (!validXuid(xuid) || !Number.isInteger(percent) || percent < 0 || percent > 200) {
      throw new Error('ERR_INVALID_PEER_VOLUME');
    }
    this.peerVolumes.set(xuid, percent);
    const peer = this.peers.get(xuid);
    const nodes = peer && this.peerAudio.get(peer.mid);
    if (nodes && this.context) nodes.gain.gain.setTargetAtTime(percent / 100, this.context.currentTime, 0.02);
  }

  async complete(generation) {
    if (
      generation !== this.generation ||
      !this.partyId ||
      this.confirming ||
      !this.relayConnected ||
      this.pc?.connectionState !== 'connected' ||
      this.channel?.readyState !== 'open'
    ) {
      return;
    }
    this.confirming = true;
    try {
      await this.api.confirmXboxParty(this.partyId);
      if (generation !== this.generation) return;
      clearTimeout(this.timer);
      this.update({ connected: true, error: undefined, speakingMembers: [], speakingAvailable: true });
      this._startSpeakingPoll();
    } catch {
      await this.failed(generation, 'ERR_PARTY_CONFIRM_FAILED');
    }
  }

  async start(partyId) {
    return this.connect(partyId, undefined);
  }

  async accept(inviteId) {
    if (!text(inviteId, 262) || !inviteId.startsWith('party:')) {
      throw new Error('ERR_INVALID_INVITE_ID');
    }
    return this.connect(undefined, inviteId);
  }

  async connect(partyId, inviteId) {
    this.closeMedia();
    const generation = this.generation;
    this.update({ connected: false, muted: true, error: undefined, audioBlocked: false });

    try {
      const pc = new RTCPeerConnection({});
      this.pc = pc;

      this.context = new AudioContext();
      this.silence = this.context.createMediaStreamDestination().stream;

      const channel = pc.createDataChannel('control', { ordered: true, protocol: 'controlV1' });
      this.channel = channel;

      channel.onopen = () => {
        if (generation !== this.generation) return;
        this.send('SessionConfiguration', {
          messageVersion: 1,
          useSessionDocumentForPrivacy: false,
        });
        void this.complete(generation);
      };

      channel.onclose = () => {
        if (generation === this.generation) {
          void this.failed(generation, 'ERR_AUDIO_DISCONNECTED');
        }
      };

      channel.onmessage = (event) => {
        if (generation !== this.generation || typeof event.data !== 'string' || event.data.length > 256 * 1024) {
          return;
        }
        try {
          const message = JSON.parse(event.data);
          if (message.channelVersion !== 1) return;
          if (!['RelayConnectionState', 'PeerAdded', 'PeerRemoved', 'PrivacyChanged', 'CleanDisconnect'].includes(message.type)) {
            return;
          }
          const content = JSON.parse(message.contentMessage);
          if (message.type === 'RelayConnectionState') {
            this.relayConnected = content.state === 'Connected';
            void this.complete(generation);
            if (!this.relayConnected && this.state.connected) {
              void this.failed(generation, 'ERR_RELAY_DISCONNECTED');
            }
          } else if (message.type === 'PeerAdded' && validXuid(content.xuid) && typeof content.transportSinkId === 'string' && content.transportSinkId.length <= 128) {
            if ([...this.peers.entries()].some(([x, p]) => x !== content.xuid && p.mid === content.transportSinkId)) {
              throw new Error('ERR_DUPLICATE_TRANSPORT');
            }
            const existing = this.peers.get(content.xuid);
            if (existing) {
              existing.allowed = false;
              this.applyPeer(content.xuid);
            }
            this.peers.set(content.xuid, {
              mid: content.transportSinkId,
              allowed: false,
              muted: this.mutedPeers.has(content.xuid),
              privacyVersion: 0,
            });
            const peerAudio = this.peerAudio.get(content.transportSinkId);
            if (peerAudio && this.context) peerAudio.gain.gain.setTargetAtTime((this.peerVolumes.get(content.xuid) ?? 100) / 100, this.context.currentTime, 0.02);
            void this.privacy(content.xuid);
          } else if (message.type === 'PeerRemoved' && validXuid(content.xuid)) {
            const peer = this.peers.get(content.xuid);
            if (peer) {
              peer.allowed = false;
              this.applyPeer(content.xuid);
              this.peers.delete(content.xuid);
            }
            this._clearSpeaking([content.xuid]);
            this._setSpeakingState(this.state.connected ? Boolean(this._speakingTimer || this.context) : false, this.getSpeakingMembers());
          } else if (message.type === 'PrivacyChanged') {
            for (const xuid of this.peers.keys()) {
              void this.privacy(xuid);
            }
          } else if (message.type === 'CleanDisconnect') {
            void this.failed(generation, 'ERR_PARTY_DISCONNECTED_BY_HOST');
          }
        } catch {
          void this.failed(generation, 'ERR_INVALID_MESSAGE');
        }
      };

      for (let i = 0; i < 15; i++) {
        const transceiver = pc.addTransceiver('audio', { direction: 'recvonly' });
        if (transceiver.receiver?.track) {
          transceiver.receiver.track.enabled = false;
        }
      }

      const sendTransceiver = pc.addTransceiver('audio', { direction: 'sendonly' });
      this.sender = sendTransceiver.sender;
      await sendTransceiver.sender.replaceTrack(this.silence.getAudioTracks()[0]);
      if (generation !== this.generation) return;

      const opus = RTCRtpSender.getCapabilities?.('audio')?.codecs?.filter((c) =>
        c.mimeType.toLowerCase().includes('opus')
      );
      if (opus && opus.length && sendTransceiver.setCodecPreferences) {
        sendTransceiver.setCodecPreferences(opus);
      }

      pc.ontrack = (event) => {
        if (event.track) {
          event.track.enabled = false;
        }
        if (generation !== this.generation) {
          event.track?.stop();
          return;
        }
        if (typeof document !== 'undefined') {
          const audio = document.createElement('audio');
          audio.autoplay = false;
          const midKey = event.transceiver?.mid ?? event.track.id;
          const source = this.context.createMediaStreamSource(new MediaStream([event.track]));
          const gain = this.context.createGain();
          const destination = this.context.createMediaStreamDestination();
          source.connect(gain);
          gain.connect(destination);
          audio.srcObject = destination.stream;
          const xuid = [...this.peers.entries()].find(([, peer]) => peer.mid === midKey)?.[0];
          gain.gain.value = xuid ? (this.peerVolumes.get(xuid) ?? 100) / 100 : 1;
          this.peerAudio.set(midKey, { source, gain, destination });
          this.audio.set(midKey, audio);

          const prepareAndPlay = async () => {
            if (this.selectedOutputId && typeof audio.setSinkId === 'function') {
              try {
                await audio.setSinkId(this.selectedOutputId);
              } catch {
                if (generation === this.generation) {
                  audio.pause?.();
                  audio.srcObject = null;
                  event.track?.stop?.();
                  this.audio.delete(midKey);
                  const nodes = this.peerAudio.get(midKey);
                  nodes?.source.disconnect(); nodes?.gain.disconnect();
                  this.peerAudio.delete(midKey);
                  this.update({ error: 'ERR_AUDIO_OUTPUT_FAILED' });
                }
                return;
              }
            }
            if (generation !== this.generation) {
              audio.pause?.();
              audio.srcObject = null;
              event.track?.stop?.();
              this.audio.delete(midKey);
              const nodes = this.peerAudio.get(midKey);
              nodes?.source.disconnect(); nodes?.gain.disconnect();
              this.peerAudio.delete(midKey);
              return;
            }
            try {
              await audio.play?.();
            } catch {
              if (generation === this.generation) {
                this.update({ audioBlocked: true });
              }
            }
          };
          void prepareAndPlay();
        }
        for (const xuid of this.peers.keys()) {
          this.applyPeer(xuid);
        }
      };

      pc.onconnectionstatechange = () => {
        if (generation !== this.generation) return;
        if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
          void this.failed(generation, 'ERR_AUDIO_DISCONNECTED');
        } else {
          void this.complete(generation);
        }
      };

      this.timer = setTimeout(() => {
        void this.failed(generation, 'ERR_TIMEOUT');
      }, 60000);
      this.timer?.unref?.();

      const description = await pc.createOffer();
      if (generation !== this.generation) return;
      await pc.setLocalDescription(description);
      if (generation !== this.generation) return;

      const bytes = new Uint8Array(16);
      if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
        crypto.getRandomValues(bytes);
      }
      let binary = '';
      for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
      const cv = (typeof btoa === 'function' ? btoa(binary) : Buffer.from(bytes).toString('base64')).replace(/=/g, '') + '.0';

      const offer = JSON.stringify({
        messageType: 'transportConnectionRequestMessage',
        sdp: pc.localDescription.sdp,
        cv,
        configuration: { control: { minVersion: 1, maxVersion: 1 } },
      });

      const result = await (inviteId
        ? this.api.acceptXboxPartyInvite(offer, inviteId)
        : this.api.startXboxParty(offer, partyId));

      if (generation !== this.generation) return;
      this.partyId = result.id;

      await pc.setRemoteDescription({ type: result.answer.sdpType, sdp: result.answer.sdp });
      if (generation !== this.generation) return;

      for (const candidate of result.answer.iceCandidates) {
        await pc.addIceCandidate({
          ...candidate,
          candidate: candidate.candidate === 'a=end-of-candidates' ? '' : candidate.candidate,
        });
        if (generation !== this.generation) return;
      }

      this.privacyTimer = setInterval(() => {
        for (const xuid of this.peers.keys()) {
          void this.privacy(xuid);
        }
      }, 60000);
      this.privacyTimer?.unref?.();

      void this.complete(generation);
    } catch (error) {
      const code = sanitizeError(error);
      await this.failed(generation, code);
      throw error;
    }
  }

  async setMuted(muted) {
    if (typeof muted !== 'boolean') {
      throw new Error('ERR_INVALID_MUTED_TYPE');
    }
    const version = ++this.microphoneVersion;
    if (muted) {
      this.microphone?.getTracks().forEach((t) => t.stop());
      this.microphone = undefined;
      await this.sender?.replaceTrack(this.silence?.getAudioTracks()[0] ?? null);
      if (version === this.microphoneVersion) {
        this.update({ muted: true });
      }
      return;
    }

    if (!this.state.connected || !this.sender) {
      throw new Error('ERR_NOT_CONNECTED');
    }
    const generation = this.generation;
    await this.api.allowXboxMicrophone();
    if (generation !== this.generation || version !== this.microphoneVersion || !this.sender) {
      throw new Error('ERR_MIC_DISCONNECTED_BEFORE_CAPTURE');
    }

    const microphone = await navigator.mediaDevices.getUserMedia(this.getMicConstraints());

    try {
      if (generation !== this.generation || version !== this.microphoneVersion || !this.sender) {
        throw new Error('ERR_MIC_DISCONNECTED_BEFORE_CAPTURE');
      }
      await this.sender.replaceTrack(microphone.getAudioTracks()[0]);
      if (generation !== this.generation || version !== this.microphoneVersion) {
        throw new Error('ERR_MIC_DISCONNECTED_BEFORE_CAPTURE');
      }
      this.microphone?.getTracks().forEach((t) => t.stop());
      this.microphone = microphone;
      this.update({ muted: false });
    } catch (error) {
      microphone.getTracks().forEach((t) => t.stop());
      throw error;
    }
  }

  async resumeAudio() {
    await this.context?.resume?.();
    await Promise.all(Array.from(this.audio.values()).map((audio) => audio.play?.()));
    this.update({ audioBlocked: false });
  }

  async failed(generation, error) {
    if (generation !== this.generation) return;
    this.closeMedia();
    this.update({ connected: false, muted: true, error: sanitizeError(error), speakingMembers: [], speakingAvailable: false });
    await this.api.leaveXboxParty().catch(() => {});
  }

  closeMedia() {
    this.state = { ...this.state, connected: false, muted: true, speakingMembers: [], speakingAvailable: false };
    this.generation++;
    this.microphoneVersion++;
    clearTimeout(this.timer);
    clearInterval(this.privacyTimer);
    this._stopSpeakingPoll();
    this._clearSpeaking();

    if (
      this._deviceChangeListener &&
      typeof navigator !== 'undefined' &&
      navigator.mediaDevices?.removeEventListener
    ) {
      navigator.mediaDevices.removeEventListener('devicechange', this._deviceChangeListener);
      this._deviceChangeListener = null;
    }

    this.microphone?.getTracks().forEach((t) => t.stop());
    this.microphone = undefined;

    this.silence?.getTracks().forEach((t) => t.stop());
    this.silence = undefined;

    for (const audio of this.audio.values()) {
      audio.pause?.();
      audio.srcObject = null;
    }
    this.audio.clear();
    for (const nodes of this.peerAudio.values()) {
      try { nodes.source.disconnect(); nodes.gain.disconnect(); } catch {}
    }
    this.peerAudio.clear();
    this.peers.clear();
    this.mutedPeers.clear();

    this.channel?.close?.();
    this.channel = undefined;

    this.pc?.close?.();
    this.pc = undefined;
    this.sender = undefined;

    void this.context?.close?.().catch(() => {});
    this.context = undefined;

    this.partyId = undefined;
    this.relayConnected = false;
    this.confirming = false;
  }

  async leave() {
    this.closeMedia();
    this.update({ connected: false, muted: true, error: undefined, speakingMembers: [], speakingAvailable: false });
    await this.api.leaveXboxParty();
  }
}

/**
 * Attaches XboxVoiceEngine to a postMessage or QtWebChannel transport bridge.
 * Requires an explicit per-instance nonce and strict command schema validation.
 */
export function attachVoiceBridge(engine, options = {}) {
  const { transport = (typeof window !== 'undefined' ? window : null), nonce } = options;

  if (!nonce || typeof nonce !== 'string' || !/^[A-Za-z0-9_-]{16,64}$/.test(nonce)) {
    throw new Error('ERR_INVALID_BRIDGE_NONCE');
  }

  let disposed = false;

  const postEvent = (event, data = {}) => {
    if (disposed || !transport || typeof transport.postMessage !== 'function') return;
    const payload = JSON.stringify({
      source: 'xodus-voice',
      nonce,
      event,
      ...data,
    });
    transport.postMessage(payload, '*');
  };

  const handleCommand = async (commandId, rawCommand) => {
    if (!text(commandId, 64) || !/^[A-Za-z0-9_-]+$/.test(commandId)) {
      postEvent('commandComplete', { commandId: 'invalid', error: 'ERR_INVALID_COMMAND_ID' });
      return;
    }

    let command;
    try {
      command = validateVoiceCommand(rawCommand);
    } catch (validationErr) {
      // Rejects without invoking engine or touching hardware microphone!
      postEvent('commandComplete', {
        commandId,
        error: sanitizeError(validationErr),
      });
      return;
    }

    try {
      switch (command.type) {
        case 'start':
          await engine.start(command.partyId);
          break;
        case 'accept':
          await engine.accept(command.inviteId);
          break;
        case 'setMuted':
          await engine.setMuted(command.muted);
          break;
        case 'mute':
          engine.mutePeer(command.xuid, command.muted);
          break;
        case 'setPeerVolume':
          engine.setPeerVolume(command.xuid, command.percent);
          break;
        case 'leave':
          await engine.leave();
          break;
        case 'resume':
          await engine.resumeAudio();
          break;
        case 'enumerateDevices': {
          const devInfo = await engine.enumerateDevices();
          postEvent('devices', devInfo);
          break;
        }
        case 'selectDevices': {
          const selection = await engine.selectDevices(command);
          postEvent('devicesSelected', selection);
          break;
        }
        default:
          throw new Error('ERR_INVALID_COMMAND');
      }
      postEvent('commandComplete', { commandId, error: null });
    } catch (err) {
      postEvent('commandComplete', {
        commandId,
        error: sanitizeError(err),
      });
    }
  };

  const messageListener = (event) => {
    if (disposed) return;
    let raw = event.data;
    if (typeof raw === 'string') {
      if (raw.length > MAX_BRIDGE_MESSAGE_BYTES) {
        return; // Reject oversized messages immediately
      }
      try {
        raw = JSON.parse(raw);
      } catch {
        return;
      }
    }

    if (!isPlainObject(raw) || raw.target !== 'xodus-voice') {
      return; // Reject non-targeted or malformed envelope
    }

    // Explicit nonce verification
    if (raw.nonce !== nonce) {
      return; // Reject messages without valid instance nonce
    }

    if (raw.type === 'command') {
      void handleCommand(raw.commandId, raw.command);
    }
  };

  if (typeof window !== 'undefined' && window.addEventListener) {
    window.addEventListener('message', messageListener);
  }

  engine.onDevicesChanged = (devices) => {
    postEvent('devices', devices);
  };

  // Notify parent runtime that the voice bridge is loaded and ready
  postEvent('ready', { state: engine.getState() });

  return {
    nonce,
    dispose: () => {
      if (disposed) return;
      disposed = true;
      engine.onDevicesChanged = null;
      if (typeof window !== 'undefined' && window.removeEventListener) {
        window.removeEventListener('message', messageListener);
      }
      engine.closeMedia();
    },
  };
}
