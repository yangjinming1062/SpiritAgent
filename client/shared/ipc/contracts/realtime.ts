export interface VoiceConnectionRequest {
  authSessionId: string
  sessionId: string
  callKey: string
}

export type VoiceConnectionEvent =
  | { kind: 'message'; callKey: string; data: string | Uint8Array }
  | { kind: 'closed'; callKey: string; failed: boolean }
  | { kind: 'busy'; busy: boolean }
