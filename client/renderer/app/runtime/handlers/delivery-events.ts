import { isSpriteOverlayVisible } from '@/app/workflows/proactive-delivery'
import { openSessionSurface } from '@/app/workflows/session-delivery'
import { $effectiveTier, $screenLocked } from '@/modules/character'
import {
  chatDisplayText,
  findConversationRuntime,
  invalidateSessionHistory,
  rememberPendingMessage,
  setCompanionSessionId,
  showMediaHint
} from '@/modules/conversation'
import { type GatewayEvent } from '@/shared/lib/gateway-protocol'
import { $chatVisible } from '@/shared/store/chat-visibility'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

// 主动消息与通知投递：companion.message / system.notification / 视频任务完成。提醒是否出现由打扰档位、锁屏与聊天可见性共同裁决；精灵旁提示另需精灵实际可见，不可见时由未读承接。

export function handleDeliveryEvent(event: GatewayEvent): void {
  switch (event.type) {
    case 'companion.message': {
      const payload = event.payload
      const bubbles = payload.bubbles

      if (!payload.session_id || !Number.isSafeInteger(payload.message_id) || payload.message_id <= 0) {
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
      const payload = event.payload
      const message = payload.message.trim()

      if (message) {
        const sessionId = payload.session_id
        const sys = getStrings().notifications.system
        notify({
          kind: payload.kind,
          title: payload.title || sys.scheduledTask,
          message,
          durationMs: payload.kind === 'error' ? 0 : undefined,
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

    case 'video_gen.queue': {
      const payload = event.payload

      if (payload.session_id && payload.media_id) {
        invalidateSessionHistory(payload.session_id)
        findConversationRuntime(payload.session_id)?.updateVideoQueue(payload.media_id, payload.video_queue)
      }

      break
    }

    case 'video_gen.completed': {
      // 后台视频任务完成（WSEvent outbox 路径，信封不带 session_id，载荷自带）。
      const { session_id: sessionId, media } = event.payload

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
          showMediaHint(message, sessionId ?? undefined)
        }
      }

      break
    }

    case 'video_gen.failed': {
      const payload = event.payload

      if (payload.session_id && payload.status_text && payload.status_message_id) {
        findConversationRuntime(payload.session_id)?.pushStatusPill(
          'status_media_failed',
          payload.status_text,
          payload.status_message_id
        )
        rememberPendingMessage(payload.session_id, payload.status_text)
      }

      if (payload.error && !$screenLocked.get()) {
        notify({ kind: 'warning', message: payload.error })
      }

      break
    }

    default:
      break
  }
}
