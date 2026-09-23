import { openSessionSurface } from '@/app/workflows/session-delivery'
import { $effectiveTier, $screenLocked } from '@/modules/character'
import {
  $chatSessionId,
  chatDisplayText,
  finalizeCompanionReply,
  pushMediaMessage,
  pushProactiveMessage,
  rememberPendingMessage,
  showMediaHint
} from '@/modules/conversation'
import { type GatewayEvent } from '@/shared/lib/gateway-protocol'
import { $chatVisible } from '@/shared/store/chat-visibility'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { ChatMediaItem, CompanionBubble } from '@/shared/types/spiritagent'

import { decodePayload } from '../gateway-event-util'

// 主动消息与通知投递：companion.message / system.notification / 视频任务完成 / IM 通道提醒。
// 提醒是否出现由打扰档位、锁屏与聊天可见性共同裁决。

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

      const text = payload?.text ?? ''

      const displayText = chatDisplayText(text)

      if (payload?.session_id && displayText) {
        rememberPendingMessage(payload.session_id, displayText)
      }

      if (payload?.session_id === $chatSessionId.get()) {
        if (payload.bubbles && payload.message_id) {
          finalizeCompanionReply(payload.bubbles, payload.message_id, payload.media, undefined, true)
        } else {
          pushProactiveMessage(text, payload.media, payload.message_id)
        }
      }

      if (displayText && $effectiveTier.get() !== 'still' && !$screenLocked.get() && !$chatVisible.get()) {
        showMediaHint(displayText, payload?.session_id)
      }

      break
    }

    case 'system.notification': {
      const p = decodePayload<{ kind?: string; title?: string; message?: string; session_id?: string }>(event.payload)
      const message = p?.message?.trim()

      if (message) {
        const sessionId = p?.session_id
        const sys = getStrings().notifications.system
        notify({
          kind: p?.kind === 'error' || p?.kind === 'warning' || p?.kind === 'success' ? p.kind : 'info',
          title: p?.title || sys.scheduledTask,
          message,
          durationMs: p?.kind === 'error' ? 0 : undefined,
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

      const sessionId = p?.session_id

      const media: ChatMediaItem[] = p?.media?.length ? p.media : p?.url ? [{ type: 'video', url: p.url }] : []

      if (!media.length) {
        break
      }

      const sys = getStrings().notifications.system
      const message = sys.videoReady

      if (sessionId) {
        rememberPendingMessage(sessionId, message)
      }

      if (sessionId && sessionId === $chatSessionId.get()) {
        pushMediaMessage(media)
      } else if (!$screenLocked.get()) {
        // 正在看别的会话或轻语时用通知承载跳转；对话界面收起时用精灵气泡提示。
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
        } else {
          showMediaHint(message, sessionId)
        }
      }

      break
    }

    case 'video_gen.failed': {
      const p = decodePayload<{ error?: string }>(event.payload)

      if (p?.error && !$screenLocked.get()) {
        notify({ kind: 'warning', message: p.error })
      }

      break
    }

    case 'channel.status': {
      // IM 通道绑定状态变化（outbox；Hub 设置页以 REST 为真相源，这里只做桌面提醒）。
      const p = decodePayload<{ channel?: string; status?: string; error?: string }>(event.payload)
      const sys = getStrings().notifications.system
      const label = p?.channel === 'weixin_ilink' ? sys.channelWeixin : sys.channelLabel

      const text =
        p?.status === 'connected'
          ? sys.channelConnected(label)
          : p?.status === 'login_required'
            ? sys.channelLoginRequired(label)
            : p?.status === 'error'
              ? sys.channelError(label, p?.error)
              : null

      if (text && !$screenLocked.get()) {
        notify({ kind: p?.status === 'error' ? 'error' : 'info', message: text })
      }

      break
    }

    case 'channel.peer_request': {
      // 陌生对端首次来信（outbox）：提示主人到设置「聊天通道」审批。
      const p = decodePayload<{ channel?: string; peer_id?: string; peer_name?: string; preview?: string }>(
        event.payload
      )

      const sys = getStrings().notifications.system
      const label = p?.channel === 'weixin_ilink' ? sys.channelWeixin : sys.channelLabel
      const name = p?.peer_name || p?.peer_id || ''

      if (!$screenLocked.get()) {
        notify({
          kind: 'info',
          message: sys.channelPeerRequest(label, name),
          detail: p?.preview
        })
      }

      break
    }

    default:
      break
  }
}
