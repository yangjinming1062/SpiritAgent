import type { ChatAttachment, SessionMessage } from '@protocol'

import type { ChatMessageBody, ChatSessionKind, SessionSettings } from './chat-runtime-types'

export function normalizeChatSessionKind(raw: unknown): ChatSessionKind {
  return raw === 'special' || raw === 'standard' ? raw : 'standard'
}

// 服务端 settings 是开放字典，只保留客户端消费且类型相符的键。
export function toSessionSettings(raw: Record<string, unknown> = {}): SessionSettings {
  const settings: SessionSettings = {}

  if (typeof raw.temperature === 'number') {
    settings.temperature = raw.temperature
  }

  if (typeof raw.context_compression_threshold === 'number') {
    settings.context_compression_threshold = raw.context_compression_threshold
  }

  if (typeof raw.enable_context_compression === 'boolean') {
    settings.enable_context_compression = raw.enable_context_compression
  }

  if (typeof raw.reasoning_effort === 'string') {
    settings.reasoning_effort = raw.reasoning_effort
  }

  return settings
}

// 多模态正文和用户附件共用一次解析；附件只交给该消息的首个气泡。
export function extractMessageContent(m: SessionMessage): Pick<ChatMessageBody, 'text' | 'attachments'> {
  if (m.content_type === 'companion_reply' || typeof m.content !== 'string') {
    return { text: '' }
  }

  if (m.content_type !== 'multimodal_v1') {
    return { text: m.content }
  }

  let parsed: unknown

  try {
    parsed = JSON.parse(m.content)
  } catch {
    return { text: m.content }
  }

  if (!Array.isArray(parsed)) {
    return { text: m.content.trim() }
  }

  const parts: unknown[] = parsed
  const texts: string[] = []
  const attachments: ChatAttachment[] = []

  for (const part of parts) {
    if (!part || typeof part !== 'object' || !('type' in part)) {
      continue
    }

    if (part.type === 'input_text' && 'text' in part && typeof part.text === 'string') {
      texts.push(part.text)
    } else if (m.role === 'user') {
      if (part.type === 'input_image' && 'image_url' in part && typeof part.image_url === 'string' && part.image_url) {
        attachments.push({ type: 'image', url: part.image_url })
      } else if (
        part.type === 'input_video' &&
        'video_url' in part &&
        typeof part.video_url === 'string' &&
        part.video_url
      ) {
        attachments.push({ type: 'video', url: part.video_url })
      } else if (
        part.type === 'input_audio' &&
        'audio_url' in part &&
        typeof part.audio_url === 'string' &&
        part.audio_url
      ) {
        attachments.push({
          type: 'audio',
          url: part.audio_url,
          ...('duration' in part && typeof part.duration === 'number' ? { duration: part.duration } : {}),
          ...('transcript' in part && typeof part.transcript === 'string' ? { transcript: part.transcript } : {})
        })
      }
    }
  }

  return { text: texts.join('\n').trim(), attachments: attachments.length ? attachments : undefined }
}

export function isPositiveInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

export function splitUserText(text: string): string[] {
  return text
    .split(/\r?\n(?:[ \t]*\r?\n)+/)
    .map(part => part.trim())
    .filter(Boolean)
}
