import { chatDisplayText } from '@/modules/conversation'
import { getStrings } from '@/shared/strings'
import type { ChatMediaItem, CompanionBubble } from '@protocol'

// 待读文案规则：正文预览优先，无正文但带媒体时给就绪兜底；preview 另供精灵提示（媒体-only 不提示）。
export function pendingReplyText(
  text?: string,
  bubbles?: CompanionBubble[],
  media?: ChatMediaItem[]
): { preview: string; pending: string } {
  const preview = chatDisplayText(
    bubbles?.findLast(bubble => 'text' in bubble)?.text ??
      bubbles?.findLast(bubble => bubble.type === 'link')?.title ??
      text ??
      ''
  )

  const hasMedia = media?.length || bubbles?.some(bubble => bubble.type === 'image' || bubble.type === 'video')

  const pending =
    preview ||
    (hasMedia
      ? media?.some(item => item.type === 'video') || bubbles?.some(bubble => bubble.type === 'video')
        ? getStrings().notifications.system.videoReady
        : getStrings().notifications.system.imageReady
      : '')

  return { preview, pending }
}
