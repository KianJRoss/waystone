/**
 * Sandboxed Native Bridge Transport for Xbox Voice Media Component
 *
 * Provides nonce-gated, capacity-bounded native IPC call management
 * for communication between the media webview and parent host.
 */
import { sanitizeError } from './xbox_voice_engine.js';

export const MAX_NATIVE_REPLY_BYTES = 262144; // bounded SDP/ICE, not command frames
export const MAX_PENDING_CALLS = 16;
export const CALL_TIMEOUT_MS = 30000;

export function generateSessionNonce(cryptoObj = (typeof crypto !== 'undefined' ? crypto : null)) {
  if (!cryptoObj || typeof cryptoObj.getRandomValues !== 'function') {
    throw new Error('ERR_CRYPTO_UNAVAILABLE');
  }
  const bytes = new Uint8Array(24);
  cryptoObj.getRandomValues(bytes);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  const encoded = (typeof btoa === 'function' ? btoa(binary) : Buffer.from(bytes).toString('base64'))
    .replace(/[^a-zA-Z0-9_-]/g, '')
    .slice(0, 32);
  if (encoded.length < 16) {
    throw new Error('ERR_CRYPTO_UNAVAILABLE');
  }
  return encoded;
}

export function createNativeBridgeProxy(options = {}) {
  const {
    transport = (typeof window !== 'undefined' ? window.parent : null),
    nonce,
    timeoutMs = CALL_TIMEOUT_MS,
    maxCapacity = MAX_PENDING_CALLS,
  } = options;

  if (!nonce || typeof nonce !== 'string' || !/^[A-Za-z0-9_-]{16,64}$/.test(nonce)) {
    throw new Error('ERR_INVALID_BRIDGE_NONCE');
  }

  let callSeq = 0;
  const pendingCalls = new Map();
  let disposed = false;

  function invokeNative(method, ...args) {
    if (disposed) {
      return Promise.reject(new Error('ERR_DISPOSED'));
    }
    if (pendingCalls.size >= maxCapacity) {
      return Promise.reject(new Error('ERR_CALL_CAPACITY_EXCEEDED'));
    }

    return new Promise((resolve, reject) => {
      const id = `call_${++callSeq}_${Date.now()}`;
      const timer = setTimeout(() => {
        if (pendingCalls.has(id)) {
          pendingCalls.delete(id);
          reject(new Error('ERR_CALL_TIMEOUT'));
        }
      }, timeoutMs);

      pendingCalls.set(id, { resolve, reject, timer });

      try {
        if (!transport || typeof transport.postMessage !== 'function') {
          throw new Error('ERR_TRANSPORT_UNAVAILABLE');
        }
        transport.postMessage(
          {
            source: 'xodus-voice',
            nonce,
            type: 'nativeCall',
            callId: id,
            method,
            args,
          },
          '*'
        );
      } catch {
        pendingCalls.delete(id);
        clearTimeout(timer);
        reject(new Error('ERR_TRANSPORT_UNAVAILABLE'));
      }
    });
  }

  function handleParentReply(raw) {
    if (disposed) return false;
    let data = raw;
    try {
      const encoded = typeof data === 'string' ? data : JSON.stringify(data);
      if (typeof encoded !== 'string' || new TextEncoder().encode(encoded).byteLength > MAX_NATIVE_REPLY_BYTES) return false;
    } catch {
      return false;
    }
    if (typeof data === 'string') {
      try {
        data = JSON.parse(data);
      } catch {
        return false;
      }
    }
    if (
      !data ||
      typeof data !== 'object' ||
      data.source !== 'xodus-voice-parent' ||
      data.nonce !== nonce ||
      typeof data.callId !== 'string' ||
      (Object.hasOwn(data, 'result') === Object.hasOwn(data, 'error')) ||
      (Object.hasOwn(data, 'error') && (typeof data.error !== 'string' || !data.error)) ||
      !pendingCalls.has(data.callId)
    ) {
      return false;
    }

    const entry = pendingCalls.get(data.callId);
    pendingCalls.delete(data.callId);
    clearTimeout(entry.timer);

    if (data.error) {
      entry.reject(new Error(sanitizeError(data.error)));
    } else {
      entry.resolve(data.result);
    }
    return true;
  }

  function teardown() {
    if (disposed) return;
    disposed = true;
    for (const [id, entry] of pendingCalls.entries()) {
      clearTimeout(entry.timer);
      entry.reject(new Error('ERR_DISPOSED'));
    }
    pendingCalls.clear();
  }

  return {
    nonce,
    invokeNative,
    handleParentReply,
    teardown,
    getPendingCount: () => pendingCalls.size,
    isDisposed: () => disposed,
    api: {
      startXboxParty: (offer, partyId) => invokeNative('startXboxParty', offer, partyId),
      acceptXboxPartyInvite: (offer, id) => invokeNative('acceptXboxPartyInvite', offer, id),
      confirmXboxParty: (id) => invokeNative('confirmXboxParty', id),
      leaveXboxParty: () => invokeNative('leaveXboxParty'),
      xboxVoiceAllowed: (xuid) => invokeNative('xboxVoiceAllowed', xuid),
      allowXboxMicrophone: () => invokeNative('allowXboxMicrophone'),
    },
  };
}
