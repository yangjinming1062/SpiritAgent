import { isSpriteOverlayVisible } from '@/app/workflows/proactive-delivery'
import { openSessionSurface } from '@/app/workflows/session-delivery'
import { $effectiveTier, $screenLocked } from '@/modules/character'
import {
  chatDisplayText,
  findConversationRuntime,
  rememberPendingMessage,
  setCompanionSessionId,
  showMediaHint
} from '@/modules/conversation'
import { type GatewayEvent } from '@/shared/lib/gateway-protocol'
import { $chatVisible } from '@/shared/store/chat-visibility'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { ChatMediaItem, CompanionBubble } from '@protocol'

import { decodePayload } from '../gateway-event-util'

// 主动消息与通知投递：companion.message / system.notification / 视频任务完成。提醒是否出现由打扰档位、锁屏与聊天可见性共同裁决；精灵旁提示另需精灵实际可见，不可见时由未读承接。

export function handleDeliveryEvent(event: GatewayEvent): void {
  switch (event.type) {
    case 'companion.message': {
      const payload = decodePayload<{
        bubbles?: CompanionBubble[]
        session_id?: string
        message_id?: number
      }>(event.payload)

      const bubbles = Array.isArray(payload.bubbles) ? payload.bubbles : undefined

      if (
        !bubbles ||
        typeof payload.session_id !== 'string' ||
        !payload.session_id ||
        typeof payload.message_id !== 'number' ||
        !Number.isSafeInteger(payload.message_id) ||
        payload.message_id <= 0
      ) {
        break
      }

      const text = bubbles.findLast(b => 'text' in b)?.text ?? bubbles.findLast(b => b.type === 'link')?.title ?? ''

      const displayText = chatDisplayText(text)
      const hasMedia = bubbles.some(bubble => bubble.type === 'image' || bubble.type === 'video')

      const pendingText =
        displayText ||
        (hasMedia
          ? bubbles.some(bubble => bubble.type === 'video')
            ? getStrings().notifications.system.videoReady
            : getStrings().notifications.system.imageReady
          : '')

      // 此事件由服务端写入唯一陪伴主会话；会话列表尚未加载时也能确定提醒归属。
      setCompanionSessionId(payload.session_id)

      if (pendingText) {
        rememberPendingMessage(payload.session_id, pendingText)
      }

      findConversationRuntime(payload.session_id)?.finalizeCompanionReply(bubbles, payload.message_id, undefined, true)

      if (displayText && $effectiveTier.get() !== 'still' && isSpriteOverlayVisible()) {
        showMediaHint(displayText, payload.session_id)
      }

      break
    }

    case 'system.notification': {
      const p = decodePayload<{ kind?: string; title?: string; message?: string; session_id?: string }>(event.payload)
      const message = p.message?.trim()

      if (message) {
        const sessionId = p.session_id
        const sys = getStrings().notifications.system
        notify({
          kind: p.kind === 'error' || p.kind === 'warning' || p.kind === 'success' ? p.kind : 'info',
          title: p.title || sys.scheduledTask,
          message,
          durationMs: p.kind === 'error' ? 0 : undefined,
          action: sessionId
            ? {
                label: sys.view,
                onClick: () => {
                  openSessionSurface(sessionId)
                }
              }
            : undefined
        })
      }

      break
    }

    case 'video_gen.completed': {
      // 后台视频任务完成（WSEvent outbox 路径，信封不带 session_id，载荷自带）。
      const p = decodePayload<{
        session_id?: string
        media?: ChatMediaItem[]
      }>(event.payload)

      const sessionId = p.session_id

      const media = Array.isArray(p.media) ? p.media : []

      if (!media.length) {
        break
      }

      const sys = getStrings().notifications.system
      const message = sys.videoReady

      if (sessionId) {
        rememberPendingMessage(sessionId, message)
      }

      const runtime = sessionId ? findConversationRuntime(sessionId) : undefined

      if (runtime) {
        runtime.pushMediaMessage(media)
      } else if (!$screenLocked.get()) {
        // 正在看别的会话或轻语时用通知承载跳转；对话界面收起且精灵可见时用精灵气泡提示。
        if ($chatVisible.get() && sessionId) {
          notify({
            kind: 'success',
            message,
            action: {
              label: sys.view,
              onClick: () => {
                openSessionSurface(sessionId)
              }
            }
          })
        } else if (isSpriteOverlayVisible()) {
          showMediaHint(message, sessionId)
        }
      }

      break
    }

    case 'video_gen.failed': {
      const p = decodePayload<{
        error?: string
        session_id?: string
        status_message_id?: number
        status_text?: string
      }>(event.payload)

      if (p.session_id && p.status_text && p.status_message_id) {
        findConversationRuntime(p.session_id)?.pushStatusPill('status_media_failed', p.status_text, p.status_message_id)
        rememberPendingMessage(p.session_id, p.status_text)
      }

      if (p.error && !$screenLocked.get()) {
        notify({ kind: 'warning', message: p.error })
      }

      break
    }

    default:
      break
  }
}
