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
        text?: string
        bubbles?: CompanionBubble[]
        session_id?: string
        message_id?: number
        media?: ChatMediaItem[]
      }>(event.payload)

      const bubbles = Array.isArray(payload.bubbles) ? payload.bubbles : undefined
      const text = bubbles ? (bubbles.findLast(b => 'text' in b)?.text ?? '') : (payload.text ?? '')

      const displayText = chatDisplayText(text)

      // 此事件由服务端写入唯一陪伴主会话；会话列表尚未加载时也能确定提醒归属。
      if (payload.session_id) {
        setCompanionSessionId(payload.session_id)
      }

      if (payload.session_id && displayText) {
        rememberPendingMessage(payload.session_id, displayText)
      }

      const runtime = payload.session_id ? findConversationRuntime(payload.session_id) : undefined

      if (runtime) {
        if (bubbles && payload.message_id) {
          runtime.finalizeCompanionReply(bubbles, payload.message_id, undefined, true)
        } else {
          runtime.pushProactiveMessage(text, payload.media, payload.message_id)
        }
      }

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
        task_id?: string
        url?: string
        session_id?: string
        media?: ChatMediaItem[]
      }>(event.payload)

      const sessionId = p.session_id

      const media: ChatMediaItem[] = p.media?.length ? p.media : p.url ? [{ type: 'video', url: p.url }] : []

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
