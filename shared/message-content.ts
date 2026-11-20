import { isRecord } from './is-record'
import type { ChatAttachment } from './protocol'

type MessagePart = { type: 'text'; text: string } | ChatAttachment

/** 按持久化顺序读取多模态正文。格式损坏时由视图保留原文，未知条目单独跳过。 */
export function multimodalParts(content: string): MessagePart[] | null {
  let decoded: unknown
  try {
    decoded = JSON.parse(content)
  } catch {
    return null
  }
  if (!Array.isArray(decoded)) {
    return null
  }
  const parts: MessagePart[] = []
  for (const part of decoded) {
    if (!isRecord(part)) {
      continue
    }
    if (part.type === 'input_text' && typeof part.text === 'string') {
      parts.push({ type: 'text', text: part.text })
    } else if (part.type === 'input_image' && typeof part.image_url === 'string' && part.image_url) {
      parts.push({ type: 'image', url: part.image_url })
    } else if (part.type === 'input_video' && typeof part.video_url === 'string' && part.video_url) {
      parts.push({ type: 'video', url: part.video_url })
    } else if (part.type === 'input_audio' && typeof part.audio_url === 'string' && part.audio_url) {
      parts.push({
        type: 'audio',
        url: part.audio_url,
        ...(typeof part.duration === 'number' ? { duration: part.duration } : {}),
        ...(typeof part.transcript === 'string' ? { transcript: part.transcript } : {})
      })
    }
  }
  return parts
}
