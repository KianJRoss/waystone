/**
 * Offline Fixture Tests for XboxVoiceEngine & VoiceBridge
 *
 * Runs with Node.js built-in test runner: `node --test adapters/xodus_voice/test_voice_engine.mjs`
 * Zero dependencies, sub-second execution, zero real mic/network/party calls.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  XboxVoiceEngine,
  attachVoiceBridge,
  validateVoiceCommand,
  sanitizeError,
  MAX_BRIDGE_MESSAGE_BYTES,
  MAX_PENDING_CALLS,
} from './xbox_voice_engine.js';

const VALID_NONCE = 'test_nonce_1234567890abcdef_1234';

const createTrack = () => {
  let stopped = false;
  let enabled = true;
  return {
    kind: 'audio',
    get enabled() {
      return enabled;
    },
    set enabled(v) {
      enabled = Boolean(v);
    },
    get isStopped() {
      return stopped;
    },
    stop() {
      stopped = true;
    },
  };
};

class MockChannel {
  constructor() {
    this.readyState = 'connecting';
    this.onopen = undefined;
    this.onclose = undefined;
    this.onmessage = undefined;
    this.sent = [];
  }

  send(data) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 'closed';
    this.onclose?.();
  }

  open() {
    this.readyState = 'open';
    this.onopen?.();
  }

  message(type, content) {
    this.onmessage?.({
      data: JSON.stringify({
        type,
        channelVersion: 1,
        contentMessage: JSON.stringify(content),
      }),
    });
  }
}

class MockPeer {
  static instances = [];

  constructor() {
    MockPeer.instances.push(this);
    this.connectionState = 'new';
    this.channel = new MockChannel();
    this.transceivers = [];
    this.localDescription = { sdp: 'v=0\r\no=mock 0 0 IN IP4 127.0.0.1\r\ns=-\r\n' };
    this.remoteDescription = undefined;
    this.candidates = [];
    this.onconnectionstatechange = undefined;
    this.ontrack = undefined;
  }

  createDataChannel() {
    return this.channel;
  }

  addTransceiver(kind, { direction }) {
    const t = {
      mid: String(this.transceivers.length),
      direction,
      receiver: { track: createTrack() },
      sender: {
        track: null,
        replaceTrack: async (track) => {
          t.sender.track = track;
        },
      },
      setCodecPreferences: () => {},
    };
    this.transceivers.push(t);
    return t;
  }

  getTransceivers() {
    return this.transceivers;
  }

  async createOffer() {
    return { type: 'offer', sdp: this.localDescription.sdp };
  }

  async setLocalDescription(desc) {
    this.localDescription = desc;
  }

  async setRemoteDescription(desc) {
    this.remoteDescription = desc;
  }

  async addIceCandidate(cand) {
    this.candidates.push(cand);
  }

  close() {
    this.connectionState = 'closed';
    this.onconnectionstatechange?.();
  }

  connect() {
    this.connectionState = 'connected';
    this.onconnectionstatechange?.();
  }
}

class MockAudioContext {
  constructor() { this.currentTime = 0; }
  createMediaStreamSource(stream) { return { stream, connect() {}, disconnect() {} }; }
  createGain() { return { gain: { value: 1, setTargetAtTime(value) { this.value = value; } }, connect() {}, disconnect() {} }; }
  createAnalyser() { return { fftSize: 0, smoothingTimeConstant: 0, getByteTimeDomainData(data) { data.fill(128); } }; }
  createMediaStreamDestination() {
    const t = createTrack();
    return {
      stream: {
        getAudioTracks: () => [t],
        getTracks: () => [t],
      },
    };
  }
  async close() {}
  async resume() {}
}

let micTrack = createTrack();
let captureCallCount = 0;
let lastCaptureOptions = null;
let captureOverride = null;
let mockDevices = [
  { deviceId: 'default', kind: 'audioinput', label: 'Default Microphone' },
  { deviceId: 'mic-usb-1', kind: 'audioinput', label: 'USB Headset Microphone' },
  { deviceId: 'default', kind: 'audiooutput', label: 'Default Audio Output' },
  { deviceId: 'speaker-usb-1', kind: 'audiooutput', label: 'USB Headset Audio' },
];
let deviceChangeListeners = [];
let enumerateCallCount = 0;

class MockAudioElement {
  constructor() {
    this.srcObject = null;
    this.autoplay = false;
    this.sinkId = '';
    this.playCount = 0;
    this.paused = false;
  }
  async play() {
    this.playCount++;
  }
  pause() {
    this.paused = true;
  }
  async setSinkId(id) {
    this.sinkId = id;
  }
}

function setupGlobals() {
  MockPeer.instances = [];
  micTrack = createTrack();
  captureCallCount = 0;
  lastCaptureOptions = null;
  captureOverride = null;
  enumerateCallCount = 0;
  deviceChangeListeners = [];
  mockDevices = [
    { deviceId: 'default', kind: 'audioinput', label: 'Default Microphone' },
    { deviceId: 'mic-usb-1', kind: 'audioinput', label: 'USB Headset Microphone' },
    { deviceId: 'default', kind: 'audiooutput', label: 'Default Audio Output' },
    { deviceId: 'speaker-usb-1', kind: 'audiooutput', label: 'USB Headset Audio' },
  ];

  globalThis.RTCPeerConnection = MockPeer;
  globalThis.RTCRtpSender = {
    getCapabilities: () => ({ codecs: [{ mimeType: 'audio/opus' }] }),
  };
  globalThis.AudioContext = MockAudioContext;
  globalThis.MediaStream = class {
    constructor(tracks = []) {
      this.tracks = tracks;
    }
    getAudioTracks() {
      return this.tracks;
    }
    getTracks() {
      return this.tracks;
    }
  };

  globalThis.HTMLMediaElement = MockAudioElement;
  globalThis.document = {
    createElement: (tag) => {
      if (tag === 'audio') return new MockAudioElement();
      return {};
    },
  };

  Object.defineProperty(globalThis, 'navigator', {
    value: {
      mediaDevices: {
        getUserMedia: async (options) => {
          captureCallCount++;
          lastCaptureOptions = options;
          if (captureOverride) {
            return captureOverride(options);
          }
          const capturedTrack = micTrack;
          return {
            getAudioTracks: () => [capturedTrack],
            getTracks: () => [capturedTrack],
          };
        },
        enumerateDevices: async () => {
          enumerateCallCount++;
          return [...mockDevices];
        },
        addEventListener: (event, handler) => {
          if (event === 'devicechange') {
            deviceChangeListeners.push(handler);
          }
        },
        removeEventListener: (event, handler) => {
          if (event === 'devicechange') {
            deviceChangeListeners = deviceChangeListeners.filter((h) => h !== handler);
          }
        },
      },
    },
    configurable: true,
    writable: true,
  });
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function fixture() {
  const calls = {
    startXboxParty: [],
    confirmXboxParty: [],
    leaveXboxParty: [],
    xboxVoiceAllowed: [],
    allowXboxMicrophone: [],
  };

  const api = {
    startXboxParty: async (offer, partyId) => {
      calls.startXboxParty.push({ offer, partyId });
      return {
        id: partyId || 'party-12345',
        answer: {
          sdpType: 'answer',
          sdp: 'v=0\r\no=server 0 0 IN IP4 127.0.0.1\r\ns=-\r\n',
          iceCandidates: [],
        },
      };
    },
    acceptXboxPartyInvite: async (offer, inviteId) => {
      return {
        id: 'party-invite',
        answer: { sdpType: 'answer', sdp: 'v=0\r\n', iceCandidates: [] },
      };
    },
    confirmXboxParty: async (partyId) => {
      calls.confirmXboxParty.push(partyId);
    },
    leaveXboxParty: async () => {
      calls.leaveXboxParty.push(true);
    },
    xboxVoiceAllowed: async (xuid) => {
      calls.xboxVoiceAllowed.push(xuid);
      return false;
    },
    allowXboxMicrophone: async () => {
      calls.allowXboxMicrophone.push(true);
    },
  };

  const stateChanges = [];
  const engine = new XboxVoiceEngine(api, (state) => {
    stateChanges.push({ ...state });
  });

  return { engine, api, calls, stateChanges };
}

async function connectedFixture() {
  const f = fixture();
  const startPromise = f.engine.start();
  await Promise.resolve();

  const pc = MockPeer.instances[0];
  assert.ok(pc, 'PeerConnection instance must be created');
  pc.connect();
  pc.channel.open();
  pc.channel.message('RelayConnectionState', { state: 'Connected' });

  await startPromise;
  for (let i = 0; i < 10; i++) {
    if (f.calls.confirmXboxParty.length > 0) break;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.equal(f.calls.confirmXboxParty.length, 1);
  return { ...f, pc };
}

test('1. starts without microphone access and requires ICE, control and relay before claiming connection', async () => {
  setupGlobals();
  const { engine, api, calls, stateChanges } = fixture();

  const startPromise = engine.start('test-party-1');
  const pc = MockPeer.instances[0];
  assert.ok(pc);

  // Microphones must NEVER be accessed on startup
  assert.equal(captureCallCount, 0);

  // All 15 recvonly transceivers start disabled
  const recvOnly = pc.transceivers.filter((t) => t.direction === 'recvonly');
  assert.equal(recvOnly.length, 15);
  assert.ok(recvOnly.every((t) => !t.receiver.track.enabled));

  // Connect ICE and channel, but party must not confirm without relay
  pc.connect();
  pc.channel.open();
  assert.equal(calls.confirmXboxParty.length, 0);

  // Send relay connected
  pc.channel.message('RelayConnectionState', { state: 'Connected' });
  await startPromise;

  for (let i = 0; i < 10; i++) {
    if (calls.confirmXboxParty.length > 0) break;
    await new Promise((r) => setTimeout(r, 5));
  }
  assert.equal(calls.confirmXboxParty.length, 1);
  assert.equal(calls.confirmXboxParty[0], 'test-party-1');
  assert.equal(engine.getState().connected, true);
  assert.equal(engine.getState().muted, true);

  engine.closeMedia();
});

test('2. keeps denied peers silent and preserves local mute when Xbox later allows communication', async () => {
  setupGlobals();
  const { engine, api, calls, pc } = await connectedFixture();

  pc.channel.message('PeerAdded', { xuid: '2533274791384321', transportSinkId: '0' });
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(calls.xboxVoiceAllowed.length, 1);
  assert.equal(calls.xboxVoiceAllowed[0], '2533274791384321');
  assert.equal(pc.transceivers[0].receiver.track.enabled, false);

  engine.mutePeer('2533274791384321', true);
  api.xboxVoiceAllowed = async () => true;
  pc.channel.message('PrivacyChanged', {});
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(pc.transceivers[0].receiver.track.enabled, false);

  engine.mutePeer('2533274791384321', false);
  assert.equal(pc.transceivers[0].receiver.track.enabled, true);

  engine.closeMedia();
});

test('3. opens microphone only on explicit unmute and stops hardware capture on mute or disposal', async () => {
  setupGlobals();
  const { engine, calls } = await connectedFixture();

  assert.equal(captureCallCount, 0);
  assert.equal(micTrack.isStopped, false);

  await engine.setMuted(false);
  assert.equal(calls.allowXboxMicrophone.length, 1);
  assert.equal(captureCallCount, 1);
  assert.equal(lastCaptureOptions.video, false);
  assert.equal(engine.getState().muted, false);

  await engine.setMuted(true);
  assert.equal(micTrack.isStopped, true);
  assert.equal(engine.getState().muted, true);

  micTrack = createTrack();
  await engine.setMuted(false);
  assert.equal(micTrack.isStopped, false);

  engine.closeMedia();
  assert.equal(micTrack.isStopped, true);
});

test('4. does not open microphone after teardown while permission request is pending', async () => {
  setupGlobals();
  const f = await connectedFixture();
  const grant = deferred();
  f.api.allowXboxMicrophone = () => grant.promise;

  const pending = f.engine.setMuted(false);
  f.engine.closeMedia();
  grant.resolve();

  await assert.rejects(pending, /ERR_MIC_DISCONNECTED/);
  assert.equal(captureCallCount, 0);
});

test('5. stops a late microphone stream instead of attaching it to a replacement session', async () => {
  setupGlobals();
  const f = await connectedFixture();
  const streamPromise = deferred();
  captureOverride = () => streamPromise.promise;

  const pending = f.engine.setMuted(false);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(captureCallCount, 1);

  f.engine.closeMedia();

  const lateTrack = createTrack();
  streamPromise.resolve({
    getAudioTracks: () => [lateTrack],
    getTracks: () => [lateTrack],
  });

  await assert.rejects(pending, /ERR_MIC_DISCONNECTED/);
  assert.equal(lateTrack.isStopped, true);
});

test('6. does not create server party when closed during initial local audio preparation', async () => {
  setupGlobals();
  const prepared = deferred();
  const CustomPeer = class extends MockPeer {
    addTransceiver(kind, options) {
      const val = super.addTransceiver(kind, options);
      if (options.direction === 'sendonly') {
        val.sender.replaceTrack = async () => prepared.promise;
      }
      return val;
    }
  };
  globalThis.RTCPeerConnection = CustomPeer;

  const f = fixture();
  const pending = f.engine.start('party-aborted');
  f.engine.closeMedia();
  prepared.resolve();

  await pending;
  assert.equal(f.calls.startXboxParty.length, 0);
  assert.equal(captureCallCount, 0);
});

test('7. cannot undo newer voice-policy denial with an older successful response', async () => {
  setupGlobals();
  const f = await connectedFixture();
  const first = deferred();
  const second = deferred();

  let callNum = 0;
  f.api.xboxVoiceAllowed = async () => {
    callNum++;
    if (callNum === 1) return first.promise;
    return second.promise;
  };

  f.pc.channel.message('PeerAdded', { xuid: '2533274791384321', transportSinkId: '0' });
  f.pc.channel.message('PrivacyChanged', {});

  second.resolve(false);
  await Promise.resolve();
  await Promise.resolve();

  first.resolve(true);
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(f.pc.transceivers[0].receiver.track.enabled, false);
  f.engine.closeMedia();
});

test('8. silences previous transport before remapping peer and rejects shared transport IDs', async () => {
  setupGlobals();
  const f = await connectedFixture();
  f.api.xboxVoiceAllowed = async () => true;

  f.pc.channel.message('PeerAdded', { xuid: '2533274791384321', transportSinkId: '0' });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(f.pc.transceivers[0].receiver.track.enabled, true);

  f.pc.channel.message('PeerAdded', { xuid: '2533274791384321', transportSinkId: '1' });
  assert.equal(f.pc.transceivers[0].receiver.track.enabled, false);

  f.pc.channel.message('PeerAdded', { xuid: '2533274791384399', transportSinkId: '1' });
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(f.calls.leaveXboxParty.length, 1);
  assert.equal(f.pc.connectionState, 'closed');
  f.engine.closeMedia();
});

test('9. stops tracks delivered late by a disposed peer without creating playback elements', async () => {
  setupGlobals();
  const f = await connectedFixture();
  f.engine.closeMedia();

  const lateTrack = createTrack();
  f.pc.ontrack?.({ track: lateTrack, transceiver: { mid: '0' } });

  assert.equal(lateTrack.enabled, false);
  assert.equal(lateTrack.isStopped, true);
  assert.equal(captureCallCount, 0);
});

test('10. reports a failed start even when leaving the failed allocation succeeds', async () => {
  setupGlobals();
  const f = fixture();
  f.api.startXboxParty = async () => {
    throw new Error('Xbox rejected this party allocation.');
  };

  await assert.rejects(f.engine.start(), /Xbox rejected this party allocation/);
  assert.equal(f.calls.leaveXboxParty.length, 1);
  assert.equal(captureCallCount, 0);
  f.engine.closeMedia();
});

test('11. schema validator rejects non-boolean or missing muted property without engine invocation', () => {
  // Coercion vulnerability check: missing muted must throw ERR_INVALID_MUTED_TYPE
  assert.throws(() => validateVoiceCommand({ type: 'setMuted' }), /ERR_INVALID_MUTED_TYPE/);
  assert.throws(() => validateVoiceCommand({ type: 'setMuted', muted: 'false' }), /ERR_INVALID_MUTED_TYPE/);
  assert.throws(() => validateVoiceCommand({ type: 'setMuted', muted: null }), /ERR_INVALID_MUTED_TYPE/);
  assert.throws(() => validateVoiceCommand({ type: 'setMuted', muted: 0 }), /ERR_INVALID_MUTED_TYPE/);

  // Valid commands
  assert.deepEqual(validateVoiceCommand({ type: 'setMuted', muted: true }), { type: 'setMuted', muted: true });
  assert.deepEqual(validateVoiceCommand({ type: 'setMuted', muted: false }), { type: 'setMuted', muted: false });
  assert.deepEqual(validateVoiceCommand({ type: 'start' }), { type: 'start' });
  assert.deepEqual(validateVoiceCommand({ type: 'start', partyId: 'abc-123' }), { type: 'start', partyId: 'abc-123' });
  assert.deepEqual(validateVoiceCommand({ type: 'accept', inviteId: 'party:12345678' }), { type: 'accept', inviteId: 'party:12345678' });
  assert.deepEqual(validateVoiceCommand({ type: 'mute', xuid: '2533274791384321', muted: true }), { type: 'mute', xuid: '2533274791384321', muted: true });
  assert.deepEqual(validateVoiceCommand({ type: 'setPeerVolume', xuid: '2533274791384321', percent: 175 }), { type: 'setPeerVolume', xuid: '2533274791384321', percent: 175 });
  assert.deepEqual(validateVoiceCommand({ type: 'leave' }), { type: 'leave' });

  // Invalid commands
  assert.throws(() => validateVoiceCommand(null), /ERR_INVALID_COMMAND/);
  assert.throws(() => validateVoiceCommand('start'), /ERR_INVALID_COMMAND/);
  assert.throws(() => validateVoiceCommand({ type: 'unknown_cmd' }), /ERR_INVALID_COMMAND/);
  assert.throws(() => validateVoiceCommand({ type: 'accept', inviteId: 'invalid' }), /ERR_INVALID_INVITE_ID/);
  assert.throws(() => validateVoiceCommand({ type: 'mute', xuid: 'bad_xuid', muted: true }), /ERR_INVALID_MUTE_PARAMS/);
  for (const percent of [-1, 201, 1.5, '100']) assert.throws(() => validateVoiceCommand({ type: 'setPeerVolume', xuid: '1', percent }), /ERR_INVALID_PEER_VOLUME/);
  assert.throws(() => validateVoiceCommand({ type: 'setPeerVolume', xuid: 'bad', percent: 100 }), /ERR_INVALID_PEER_VOLUME/);
});

test('11a. per-peer gain accepts 0-200 percent and applies to mapped remote audio only', async () => {
  setupGlobals();
  const { api } = fixture();
  const engine = new XboxVoiceEngine(api);
  await engine.start('gain-fixture');
  engine.peers.set('2533274791384321', { mid: 'm-peer-gain', allowed: true, muted: false, privacyVersion: 0 });
  const track = createTrack();
  MockPeer.instances[0].ontrack({ track, transceiver: { mid: 'm-peer-gain' } });
  const nodes = engine.peerAudio.get('m-peer-gain');
  assert.ok(nodes);
  engine.setPeerVolume('2533274791384321', 175);
  assert.equal(nodes.gain.gain.value, 1.75);
  engine.setPeerVolume('2533274791384321', 0);
  assert.equal(nodes.gain.gain.value, 0);
  assert.throws(() => engine.setPeerVolume('2533274791384321', 201), /ERR_INVALID_PEER_VOLUME/);
  engine.closeMedia();
  assert.equal(engine.peerAudio.size, 0);
});

test('12. attachVoiceBridge enforces per-instance nonce and rejects messages without matching nonce', () => {
  setupGlobals();
  const f = fixture();
  const posted = [];
  const mockTransport = {
    postMessage: (p) => posted.push(JSON.parse(p)),
  };

  // Rejects attachment without valid nonce
  assert.throws(() => attachVoiceBridge(f.engine, { transport: mockTransport, nonce: '' }), /ERR_INVALID_BRIDGE_NONCE/);
  assert.throws(() => attachVoiceBridge(f.engine, { transport: mockTransport, nonce: 'short' }), /ERR_INVALID_BRIDGE_NONCE/);

  const bridge = attachVoiceBridge(f.engine, { transport: mockTransport, nonce: VALID_NONCE });
  assert.ok(posted.some((p) => p.event === 'ready' && p.nonce === VALID_NONCE));

  bridge.dispose();
});

test('13. attachVoiceBridge rejects malformed setMuted command without invoking engine or mic', async () => {
  setupGlobals();
  const f = fixture();
  const posted = [];
  const mockTransport = {
    postMessage: (p) => posted.push(JSON.parse(p)),
  };

  const listeners = [];
  const fakeWindow = {
    addEventListener: (type, fn) => listeners.push(fn),
    removeEventListener: () => {},
  };
  globalThis.window = fakeWindow;

  const bridge = attachVoiceBridge(f.engine, { transport: mockTransport, nonce: VALID_NONCE });

  // Deliver malformed command missing `muted`
  for (const listener of listeners) {
    listener({
      data: {
        target: 'xodus-voice',
        nonce: VALID_NONCE,
        type: 'command',
        commandId: 'cmd_1',
        command: { type: 'setMuted' }, // MISSING MUTED
      },
    });
  }

  await Promise.resolve();
  await Promise.resolve();

  // Must complete with error and NEVER touch mic
  const completion = posted.find((p) => p.event === 'commandComplete' && p.commandId === 'cmd_1');
  assert.ok(completion, 'Command complete must be posted');
  assert.equal(completion.error, 'ERR_INVALID_MUTED_TYPE');
  assert.equal(captureCallCount, 0);

  // Deliver command with wrong nonce -> ignored completely
  const prevCount = posted.length;
  for (const listener of listeners) {
    listener({
      data: {
        target: 'xodus-voice',
        nonce: 'wrong_nonce_1234567890abcdef',
        type: 'command',
        commandId: 'cmd_2',
        command: { type: 'leave' },
      },
    });
  }
  assert.equal(posted.length, prevCount); // completely ignored

  // Deliver oversized command (> 64 KiB) -> ignored completely
  const hugePayload = JSON.stringify({
    target: 'xodus-voice',
    nonce: VALID_NONCE,
    type: 'command',
    commandId: 'cmd_3',
    command: { type: 'start', partyId: 'x'.repeat(70000) },
  });
  assert.ok(hugePayload.length > MAX_BRIDGE_MESSAGE_BYTES);
  for (const listener of listeners) {
    listener({ data: hugePayload });
  }
  assert.equal(posted.length, prevCount);

  bridge.dispose();
});

test('14. sanitizeError strips stack traces, SDP, and ICE details to safe uppercase codes', () => {
  assert.equal(sanitizeError(new Error('navigator.mediaDevices.getUserMedia permission denied')), 'ERR_MIC_PERMISSION_DENIED');
  assert.equal(sanitizeError(new Error('Connection timed out waiting for relay')), 'ERR_TIMEOUT');
  assert.equal(sanitizeError(new Error('Duplicate audio transport sink ID')), 'ERR_DUPLICATE_TRANSPORT');
  assert.equal(sanitizeError(new Error('v=0\r\no=server 12345 IN IP4 10.0.0.1\r\na=candidate:1 1 UDP 2130706431 10.0.0.1 50000')), 'ERR_VOICE_INTERNAL');
  assert.equal(sanitizeError('ERR_CUSTOM_CODE'), 'ERR_CUSTOM_CODE');
  assert.equal(sanitizeError(null), null);
});

test('15. generateSessionNonce strictly requires getRandomValues and fails closed on crypto absence', async () => {
  const { generateSessionNonce } = await import('./voice_transport.js');

  // Successful generation with crypto
  const nonce = generateSessionNonce(globalThis.crypto);
  assert.ok(typeof nonce === 'string' && nonce.length >= 16);
  assert.match(nonce, /^[A-Za-z0-9_-]+$/);

  // Fails closed on null / undefined / missing getRandomValues
  assert.throws(() => generateSessionNonce(null), /ERR_CRYPTO_UNAVAILABLE/);
  assert.throws(() => generateSessionNonce({}), /ERR_CRYPTO_UNAVAILABLE/);
  assert.throws(() => generateSessionNonce({ getRandomValues: 'not_a_func' }), /ERR_CRYPTO_UNAVAILABLE/);
});

test('16. createNativeBridgeProxy enforces capacity limit of 16 and rejects 17th call immediately', async () => {
  const { createNativeBridgeProxy } = await import('./voice_transport.js');
  const posted = [];
  const mockTransport = { postMessage: (msg) => posted.push(msg) };

  const proxy = createNativeBridgeProxy({
    transport: mockTransport,
    nonce: VALID_NONCE,
    maxCapacity: 16,
  });

  const promises = [];
  for (let i = 0; i < 16; i++) {
    promises.push(proxy.invokeNative('testMethod', i));
  }
  assert.equal(proxy.getPendingCount(), 16);
  assert.equal(posted.length, 16);

  // 17th call must reject immediately without posting
  await assert.rejects(proxy.invokeNative('overflowMethod'), /ERR_CALL_CAPACITY_EXCEEDED/);
  assert.equal(posted.length, 16);

  // Clean up
  proxy.teardown();
  await Promise.allSettled(promises);
});

test('17. createNativeBridgeProxy resolves parent reply and rejects matching error', async () => {
  const { createNativeBridgeProxy } = await import('./voice_transport.js');
  const posted = [];
  const mockTransport = { postMessage: (msg) => posted.push(msg) };

  const proxy = createNativeBridgeProxy({
    transport: mockTransport,
    nonce: VALID_NONCE,
  });

  const p1 = proxy.invokeNative('startXboxParty', 'offer-data');
  const callId = posted[0].callId;

  // Deliver matching parent reply
  const handled = proxy.handleParentReply({
    source: 'xodus-voice-parent',
    nonce: VALID_NONCE,
    callId,
    result: { id: 'party-ok' },
  });
  assert.equal(handled, true);
  const result = await p1;
  assert.deepEqual(result, { id: 'party-ok' });
  assert.equal(proxy.getPendingCount(), 0);

  // Error case
  const p2 = proxy.invokeNative('leaveXboxParty');
  const callId2 = posted[1].callId;
  proxy.handleParentReply({
    source: 'xodus-voice-parent',
    nonce: VALID_NONCE,
    callId: callId2,
    error: 'party disconnected by host',
  });
  await assert.rejects(p2, /ERR_DISCONNECTED/);

  proxy.teardown();
});

test('18. createNativeBridgeProxy ignores mismatched nonce, wrong source, or unknown callIds', async () => {
  const { createNativeBridgeProxy } = await import('./voice_transport.js');
  const posted = [];
  const mockTransport = { postMessage: (msg) => posted.push(msg) };

  const proxy = createNativeBridgeProxy({
    transport: mockTransport,
    nonce: VALID_NONCE,
  });

  const p = proxy.invokeNative('confirmXboxParty', 'party-1');
  const callId = posted[0].callId;

  // Wrong source -> ignored
  assert.equal(proxy.handleParentReply({ source: 'wrong-source', nonce: VALID_NONCE, callId, result: true }), false);
  // Wrong nonce -> ignored
  assert.equal(proxy.handleParentReply({ source: 'xodus-voice-parent', nonce: 'wrong_nonce_abc', callId, result: true }), false);
  // Unknown callId -> ignored
  assert.equal(proxy.handleParentReply({ source: 'xodus-voice-parent', nonce: VALID_NONCE, callId: 'unknown_call', result: true }), false);

  assert.equal(proxy.getPendingCount(), 1);
  proxy.teardown();
  await assert.rejects(p, /ERR_DISPOSED/);
});

test('19. createNativeBridgeProxy evicts and rejects calls on timeout', async () => {
  const { createNativeBridgeProxy } = await import('./voice_transport.js');
  const mockTransport = { postMessage: () => {} };

  const proxy = createNativeBridgeProxy({
    transport: mockTransport,
    nonce: VALID_NONCE,
    timeoutMs: 20, // 20ms timeout for test
  });

  const p = proxy.invokeNative('slowMethod');
  assert.equal(proxy.getPendingCount(), 1);

  await assert.rejects(p, /ERR_CALL_TIMEOUT/);
  assert.equal(proxy.getPendingCount(), 0);

  proxy.teardown();
});

test('20. createNativeBridgeProxy teardown rejects all pending calls with ERR_DISPOSED', async () => {
  const { createNativeBridgeProxy } = await import('./voice_transport.js');
  const mockTransport = { postMessage: () => {} };

  const proxy = createNativeBridgeProxy({
    transport: mockTransport,
    nonce: VALID_NONCE,
    timeoutMs: 5000,
  });

  const p1 = proxy.invokeNative('call1');
  const p2 = proxy.invokeNative('call2');
  assert.equal(proxy.getPendingCount(), 2);

  proxy.teardown();
  assert.equal(proxy.getPendingCount(), 0);
  assert.equal(proxy.isDisposed(), true);

  await assert.rejects(p1, /ERR_DISPOSED/);
  await assert.rejects(p2, /ERR_DISPOSED/);

  // New call after disposal rejects immediately
  await assert.rejects(proxy.invokeNative('call3'), /ERR_DISPOSED/);
});


test('21. transport failures release capacity and sanitize synchronous errors', async () => {
  const { createNativeBridgeProxy } = await import('./voice_transport.js');
  for (const transport of [null, { postMessage() { throw new Error('secret SDP exception'); } }]) {
    const proxy = createNativeBridgeProxy({transport, nonce: VALID_NONCE});
    await assert.rejects(proxy.invokeNative('leaveXboxParty'), /^Error: ERR_TRANSPORT_UNAVAILABLE$/);
    assert.equal(proxy.getPendingCount(), 0);
    proxy.teardown();
  }
});

test('22. malformed and oversized replies cannot settle a pending native call', async () => {
  const { createNativeBridgeProxy } = await import('./voice_transport.js');
  const posted = [];
  const proxy = createNativeBridgeProxy({transport: {postMessage: m => posted.push(m)}, nonce: VALID_NONCE});
  const pending = proxy.invokeNative('confirmXboxParty', 'party');
  const base = {source: 'xodus-voice-parent', nonce: VALID_NONCE, callId: posted[0].callId};
  for (const reply of [base, {...base, result: true, error: 'ERR_INTERNAL'},
    {...base, error: false}, {...base, error: ''}, {...base, result: 'é'.repeat(132000)}]) {
    assert.equal(proxy.handleParentReply(reply), false);
    assert.equal(proxy.getPendingCount(), 1);
  }
  assert.equal(proxy.handleParentReply({...base, result: true}), true);
  assert.equal(await pending, true);
  proxy.teardown();
});

test('23. ERR-prefixed raw text is never treated as a typed safe code', async () => {
  const {sanitizeError} = await import('./xbox_voice_engine.js');
  assert.equal(sanitizeError('ERR_TRANSPORT_UNAVAILABLE'), 'ERR_TRANSPORT_UNAVAILABLE');
  assert.equal(sanitizeError('ERR_secret token value'), 'ERR_VOICE_INTERNAL');
  assert.equal(sanitizeError('ERR_ICE candidate:secret'), 'ERR_VOICE_INTERNAL');
});

test('24. validateVoiceCommand validates enumerateDevices and selectDevices schemas', async () => {
  assert.deepEqual(validateVoiceCommand({ type: 'enumerateDevices' }), { type: 'enumerateDevices' });
  assert.deepEqual(
    validateVoiceCommand({ type: 'selectDevices', inputId: 'mic-1', outputId: 'spk-1' }),
    { type: 'selectDevices', inputId: 'mic-1', outputId: 'spk-1' }
  );
  assert.deepEqual(
    validateVoiceCommand({ type: 'selectDevices', inputId: null, outputId: null }),
    { type: 'selectDevices' }
  );

  // Extra disallowed keys
  assert.throws(() => validateVoiceCommand({ type: 'enumerateDevices', extra: true }), /ERR_INVALID_COMMAND/);
  // Invalid device IDs
  assert.throws(() => validateVoiceCommand({ type: 'selectDevices', inputId: 'bad id with spaces' }), /ERR_INVALID_DEVICE_ID/);
  assert.throws(() => validateVoiceCommand({ type: 'selectDevices', outputId: 'a'.repeat(200) }), /ERR_INVALID_DEVICE_ID/);
});

test('25. enumerateDevices extracts inputs/outputs and never calls getUserMedia', async () => {
  setupGlobals();
  const { api } = fixture();
  const engine = new XboxVoiceEngine(api);

  const res = await engine.enumerateDevices();
  assert.equal(captureCallCount, 0, 'Must not call getUserMedia during device enumeration');
  assert.equal(res.inputs.length, 2);
  assert.equal(res.outputs.length, 2);
  assert.equal(res.inputs[0].id, 'default');
  assert.equal(res.inputs[1].id, 'mic-usb-1');
  assert.equal(res.outputs[1].id, 'speaker-usb-1');
  assert.equal(res.sinkIdSupported, true);
  assert.equal(res.missingSelectedInput, false);

  engine.closeMedia();
});

test('26. selectDevices sets output sinkId on current and future audio elements and rejects unsupported sink', async () => {
  setupGlobals();
  const { api } = fixture();
  const engine = new XboxVoiceEngine(api);

  // Unsupported sinkId
  engine.sinkIdSupported = false;
  await assert.rejects(
    engine.selectDevices({ outputId: 'speaker-usb-1' }),
    /ERR_SINK_ID_UNSUPPORTED/
  );

  // Supported sinkId
  engine.sinkIdSupported = true;
  await engine.start('test-party');
  assert.equal(engine.audio.size, 0);

  // Connect mock peer track to create an audio element
  const pc = MockPeer.instances[0];
  const mockTrack = createTrack();
  pc.ontrack({ track: mockTrack, transceiver: { mid: 'm1' } });
  assert.equal(engine.audio.size, 1);
  const audio1 = engine.audio.get('m1');
  assert.equal(audio1.sinkId, '');

  // Select output device
  await engine.selectDevices({ outputId: 'speaker-usb-1' });
  assert.equal(audio1.sinkId, 'speaker-usb-1');

  // Future audio element gets the chosen sinkId
  const mockTrack2 = createTrack();
  pc.ontrack({ track: mockTrack2, transceiver: { mid: 'm2' } });
  const audio2 = engine.audio.get('m2');
  assert.equal(audio2.sinkId, 'speaker-usb-1');

  engine.closeMedia();
});

test('27. selectDevices switches live microphone stream with exact constraint when unmuted', async () => {
  setupGlobals();
  const f = await connectedFixture();
  const { engine } = f;

  // Unmute to capture mic
  await engine.setMuted(false);
  assert.equal(captureCallCount, 1);
  assert.deepEqual(lastCaptureOptions.audio.deviceId, undefined);
  const track1 = micTrack;

  // Change input device while active
  const track2 = createTrack();
  micTrack = track2;
  await engine.selectDevices({ inputId: 'mic-usb-1' });
  assert.equal(captureCallCount, 2);
  assert.deepEqual(lastCaptureOptions.audio.deviceId, { exact: 'mic-usb-1' });
  assert.equal(track1.isStopped, true, 'Old track must be stopped after switch');
  assert.equal(track2.isStopped, false, 'New track must remain active');

  engine.closeMedia();
});

test('28. selectDevices does not open microphone when muted', async () => {
  setupGlobals();
  const { api } = fixture();
  const engine = new XboxVoiceEngine(api);

  await engine.start('party-muted');
  assert.equal(engine.state.muted, true);

  await engine.selectDevices({ inputId: 'mic-usb-1' });
  assert.equal(captureCallCount, 0, 'Selecting input while muted must NEVER call getUserMedia');
  assert.equal(engine.selectedInputId, 'mic-usb-1');

  engine.closeMedia();
});

test('29. hotplug devicechange detects missing selected device and silences active mic', async () => {
  setupGlobals();
  const f = await connectedFixture();
  const { engine } = f;

  let notifiedDevices = null;
  engine.onDevicesChanged = (d) => {
    notifiedDevices = d;
  };

  await engine.enumerateDevices();
  assert.equal(deviceChangeListeners.length, 1);

  // Select input and unmute
  await engine.selectDevices({ inputId: 'mic-usb-1' });
  await engine.setMuted(false);
  assert.equal(engine.state.muted, false);

  // Simulate USB headset unplugged
  mockDevices = [
    { deviceId: 'default', kind: 'audioinput', label: 'Default Microphone' },
    { deviceId: 'default', kind: 'audiooutput', label: 'Default Audio Output' },
  ];

  // Trigger devicechange
  await deviceChangeListeners[0]();
  assert.equal(engine.state.muted, true);
  assert.equal(engine.state.error, 'ERR_DEVICE_NOT_FOUND');
  assert.equal(notifiedDevices?.missingSelectedInput, true);

  engine.closeMedia();
  assert.equal(deviceChangeListeners.length, 0);
});

test('30. attachVoiceBridge dispatches enumerateDevices and selectDevices commands', async () => {
  setupGlobals();
  const f = fixture();
  const posted = [];
  const mockTransport = {
    postMessage: (payload) => posted.push(JSON.parse(payload)),
  };

  const listeners = [];
  const fakeWindow = {
    addEventListener: (type, fn) => listeners.push(fn),
    removeEventListener: () => {},
  };
  globalThis.window = fakeWindow;

  const bridge = attachVoiceBridge(f.engine, {
    transport: mockTransport,
    nonce: VALID_NONCE,
  });

  // 1. Deliver enumerateDevices command
  for (const listener of listeners) {
    listener({
      data: {
        target: 'xodus-voice',
        nonce: VALID_NONCE,
        type: 'command',
        commandId: 'cmd_enum',
        command: { type: 'enumerateDevices' },
      },
    });
  }

  await new Promise((r) => setTimeout(r, 10));

  const devEvent = posted.find((p) => p.event === 'devices');
  assert.ok(devEvent, 'Must emit devices event');
  assert.equal(devEvent.nonce, VALID_NONCE);
  assert.equal(devEvent.inputs.length, 2);

  // 2. Deliver selectDevices command
  for (const listener of listeners) {
    listener({
      data: {
        target: 'xodus-voice',
        nonce: VALID_NONCE,
        type: 'command',
        commandId: 'cmd_select',
        command: { type: 'selectDevices', inputId: 'mic-usb-1', outputId: 'speaker-usb-1' },
      },
    });
  }

  await new Promise((r) => setTimeout(r, 10));

  const selEvent = posted.find((p) => p.event === 'devicesSelected');
  assert.ok(selEvent, 'Must emit devicesSelected event');
  assert.equal(selEvent.inputId, 'mic-usb-1');
  assert.equal(selEvent.outputId, 'speaker-usb-1');

  bridge.dispose();
});

test('31. selectDevices throws ERR_AUDIO_OUTPUT_FAILED and does not set selectedOutputId when setSinkId rejects', async () => {
  setupGlobals();
  const { api } = fixture();
  const engine = new XboxVoiceEngine(api);

  await engine.start('test-party-fail-sink');
  const pc = MockPeer.instances[0];
  const mockTrack = createTrack();
  pc.ontrack({ track: mockTrack, transceiver: { mid: 'm-failing' } });

  const audio = engine.audio.get('m-failing');
  assert.ok(audio);
  audio.setSinkId = async () => {
    throw new Error('Device unavailable');
  };

  await assert.rejects(
    engine.selectDevices({ outputId: 'speaker-usb-1' }),
    /ERR_AUDIO_OUTPUT_FAILED/
  );

  // Must NOT acknowledge failed routing as selected
  assert.equal(engine.selectedOutputId, null);

  engine.closeMedia();
});

test('32. future peer sink failure pauses/blocks that peer and emits safe ERR_AUDIO_OUTPUT_FAILED', async () => {
  setupGlobals();
  const { api } = fixture();
  const engine = new XboxVoiceEngine(api);

  await engine.start('test-party-future-sink');
  // Successfully select output device while no peers are active yet
  await engine.selectDevices({ outputId: 'speaker-usb-1' });
  assert.equal(engine.selectedOutputId, 'speaker-usb-1');

  // Override audio element factory so future audio element rejects setSinkId
  let createdAudio = null;
  globalThis.document.createElement = (tag) => {
    if (tag === 'audio') {
      createdAudio = new MockAudioElement();
      createdAudio.setSinkId = async () => {
        throw new Error('Audio output device disconnected');
      };
      return createdAudio;
    }
    return {};
  };

  const pc = MockPeer.instances[0];
  const mockTrack = createTrack();
  pc.ontrack({ track: mockTrack, transceiver: { mid: 'm-future-fail' } });

  // Yield microtasks for setSinkId rejection handling
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(engine.state.error, 'ERR_AUDIO_OUTPUT_FAILED');
  assert.equal(createdAudio.srcObject, null, 'Failed peer audio element must be cleared');

  engine.closeMedia();
});

test('33. delayed sink success/reject proves play count stays zero before resolve and on reject', async () => {
  setupGlobals();
  const { api } = fixture();
  const engine = new XboxVoiceEngine(api);

  await engine.start('test-party-delayed-sink');
  await engine.selectDevices({ outputId: 'speaker-usb-1' });

  // 1. Delayed sink success
  const sinkDeferred1 = deferred();
  let audio1 = null;
  globalThis.document.createElement = (tag) => {
    if (tag === 'audio') {
      audio1 = new MockAudioElement();
      audio1.setSinkId = () => sinkDeferred1.promise;
      return audio1;
    }
    return {};
  };

  const pc = MockPeer.instances[0];
  pc.ontrack({ track: createTrack(), transceiver: { mid: 'm-delayed-ok' } });

  await Promise.resolve();
  assert.equal(audio1.playCount, 0, 'Play count must remain zero while setSinkId is pending');

  sinkDeferred1.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(audio1.playCount, 1, 'Play count must increment only AFTER setSinkId resolves');

  // 2. Delayed sink reject
  const sinkDeferred2 = deferred();
  let audio2 = null;
  globalThis.document.createElement = (tag) => {
    if (tag === 'audio') {
      audio2 = new MockAudioElement();
      audio2.setSinkId = () => sinkDeferred2.promise;
      return audio2;
    }
    return {};
  };

  pc.ontrack({ track: createTrack(), transceiver: { mid: 'm-delayed-fail' } });
  await Promise.resolve();
  assert.equal(audio2.playCount, 0, 'Play count must remain zero while setSinkId is pending');

  sinkDeferred2.reject(new Error('Hardware audio output vanished'));
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(audio2.playCount, 0, 'Play count must remain ZERO when setSinkId rejects');
  assert.equal(audio2.srcObject, null, 'Audio track must be cleared on reject');
  assert.equal(engine.state.error, 'ERR_AUDIO_OUTPUT_FAILED');

  engine.closeMedia();
});

test('34. selectDevices across multiple peers fails closed and pauses all peers if one fails', async () => {
  setupGlobals();
  const { api } = fixture();
  const engine = new XboxVoiceEngine(api);

  await engine.start('test-party-multi-peer-fail');
  const pc = MockPeer.instances[0];

  // Add peer 1 (normal)
  pc.ontrack({ track: createTrack(), transceiver: { mid: 'm-peer-1' } });
  const a1 = engine.audio.get('m-peer-1');

  // Add peer 2 (fails setSinkId)
  pc.ontrack({ track: createTrack(), transceiver: { mid: 'm-peer-2' } });
  const a2 = engine.audio.get('m-peer-2');
  a2.setSinkId = async () => {
    throw new Error('Device busy');
  };

  await assert.rejects(
    engine.selectDevices({ outputId: 'speaker-usb-1' }),
    /ERR_AUDIO_OUTPUT_FAILED/
  );

  // All peers must be paused and fail-closed
  assert.equal(a1.paused, true);
  assert.equal(a1.srcObject, null);
  assert.equal(a2.paused, true);
  assert.equal(a2.srcObject, null);
  assert.equal(engine.selectedOutputId, null, 'selectedOutputId must not be acknowledged');
  assert.equal(engine.state.error, 'ERR_AUDIO_OUTPUT_FAILED');

  engine.closeMedia();
});

test('35. speaking state: mutePeer/peer remove/complete/failed/leave/closeMedia clear and update', async () => {
  setupGlobals();
  const { api } = fixture();
  const engine = new XboxVoiceEngine(api);
  let last;
  engine.changed = (s) => { last = s; };

  engine.state.connected = true;
  engine.context = { close: async () => {} };
  engine._speaking.set('2', { active: true, level: 0.1, lastActiveAt: Date.now() });
  engine._speaking.set('1', { active: true, level: 0.1, lastActiveAt: Date.now() });
  engine.peers.set('1', { mid: 'm1', allowed: true, muted: false, privacyVersion: 0 });
  engine.peers.set('2', { mid: 'm2', allowed: true, muted: false, privacyVersion: 0 });
  engine._setSpeakingState(true, engine.getSpeakingMembers());
  assert.deepEqual(engine.state.speakingMembers, ['1', '2']);
  assert.equal(engine.state.speakingAvailable, true);

  // muting a peer clears only that peer's speaking state
  engine.mutePeer('1', true);
  assert.deepEqual(engine.state.speakingMembers, ['2']);
  assert.equal(engine.state.speakingAvailable, true);
  assert.equal(last.speakingMembers.includes('1'), false);

  // peer remove clears speaking for removed peer
  engine._speaking.set('2', { active: true, level: 0.1, lastActiveAt: Date.now() });
  engine._clearSpeaking(['2']);
  engine._setSpeakingState(true, engine.getSpeakingMembers());
  assert.deepEqual(engine.state.speakingMembers, []);

  // complete marks available with empty members
  engine.state.connected = false;
  engine.partyId = 'p1';
  engine.relayConnected = true;
  engine.confirming = false;
  engine.pc = { connectionState: 'connected' };
  engine.channel = { readyState: 'open', send(){} };
  await engine.complete(engine.generation);
  assert.equal(engine.state.connected, true);
  assert.deepEqual(engine.state.speakingMembers, []);
  assert.equal(engine.state.speakingAvailable, true);

  // failed clears speaking state
  engine._speaking.set('3', { active: true, level: 0.2, lastActiveAt: Date.now() });
  await engine.failed(engine.generation, 'ERR_TEST');
  assert.deepEqual(engine.state.speakingMembers, []);
  assert.equal(engine.state.speakingAvailable, false);

  // leave clears speaking state
  engine.state.connected = true;
  engine.state.speakingAvailable = true;
  engine.state.speakingMembers = ['4'];
  engine._speaking.set('4', { active: true, level: 0.2, lastActiveAt: Date.now() });
  engine.partyId = 'p2';
  await engine.leave();
  assert.deepEqual(engine.state.speakingMembers, []);
  assert.equal(engine.state.speakingAvailable, false);

  // closeMedia clears speaking state
  engine.state.speakingAvailable = true;
  engine.state.speakingMembers = ['5'];
  engine._speaking.set('5', { active: true, level: 0.2, lastActiveAt: Date.now() });
  engine.closeMedia();
  assert.deepEqual(engine.state.speakingMembers, []);
  assert.equal(engine.state.speakingAvailable, false);
});
