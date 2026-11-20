import type { SessionMessage } from '@protocol'

import { multimodalParts } from '../../../../shared/message-content'

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

  const parts = multimodalParts(m.content)

  if (!parts) {
    return { text: m.content }
  }

  const text = parts
    .filter(part => part.type === 'text')
    .map(part => part.text)
    .join('\n')
    .trim()

  const attachments = m.role === 'user' ? parts.filter(part => part.type !== 'text') : []

  return { text, attachments: attachments.length ? attachments : undefined }
}

export function splitUserText(text: string): string[] {
  return text
    .split(/\r?\n(?:[ \t]*\r?\n)+/)
    .map(part => part.trim())
    .filter(Boolean)
}
