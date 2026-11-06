export interface VoicePlaybackRecord {
  listened: boolean
  positionSeconds: number
}

export interface VoicePlaybackSnapshot {
  records: Record<string, VoicePlaybackRecord>
  revision: number
}

export interface VoicePlaybackScope {
  authSessionId: string
  sessionId: string
}

export interface VoicePlaybackUpdate extends VoicePlaybackScope, VoicePlaybackRecord {
  messageId: string
  bubbleIndex: number
}

export interface VoicePlaybackRemoval extends VoicePlaybackScope {
  messageIds?: string[]
}

export interface VoicePlaybackChanged extends VoicePlaybackScope {
  snapshot: VoicePlaybackSnapshot
}

export function voicePlaybackKey(messageId: string, bubbleIndex: number): string {
  return `${messageId}:${bubbleIndex}`
}

export interface AudioUploadPayload {
  authSessionId: string
  dataUrl: string
  filename: string
  sessionId: string
}

export interface AudioUploadResult {
  url: string
  mime_type: string
  duration: number
}

export interface VoiceSampleUploadPayload {
  authSessionId: string
  dataUrl: string
  filename: string
}

export interface VoiceSampleUploadResult extends AudioUploadResult {
  sample_id: string
}

export interface MediaTtsPayload {
  context?: null | string
  /** 是否把合成结果按内容寻址落盘，供离线复用。 */
  persist: boolean
  text: string
  voice?: string
}

export interface AttachmentVideoUploadPayload {
  authSessionId: string
  path: string
  sessionId: string
}

export interface AttachmentVideoUploadResult {
  url: string
}
