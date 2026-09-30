import { isSpriteOverlayVisible } from '@/app/workflows/proactive-delivery'
import { reportInteractionStat, setSpriteState, triggerFootGlowPulse } from '@/modules/character'
import {
  $chatDraftFromUndo,
  $chatSessionId,
  $chatTurnInFlight,
  $turnHadBubbleBreak,
  appendAssistantDelta,
  appendAssistantReasoningDelta,
  beginAssistantMessage,
  bindTrailingAssistantMessageId,
  bindTrailingUserMessageIds,
  chatDisplayText,
  clearPendingPrompts,
  finalizeAssistantMessage,
  finalizeCompanionReply,
  hydrateChatMessages,
  hydrateEditedChatMessages,
  invalidateSessionHistory,
  markAssistantTerminal,
  pushStatusPill,
  rememberFullHistory,
  setSessionContextUsage,
  setTurnHadBubbleBreak,
  showMediaHint,
  submitPendingBatch,
  updateMediaBubble,
  updateVoiceBubble
} from '@/modules/conversation'
import { cancelVoiceBar } from '@/modules/speech'
import { type GatewayEvent, type SlashCommandResultPayload } from '@/shared/lib/gateway-protocol'
import { getStrings } from '@/shared/strings'
import type { ChatMediaItem, CompanionBubble, SessionMessage } from '@/shared/types/spiritagent'

import { decodePayload, type EventRouteContext } from '../gateway-event-util'

// 会话回合事件处理：message.* 与 slash/压缩/撤回的状态更新；精灵表现命令（thinking/idle）经呈现端口下达。

export function handleConversationEvent(event: GatewayEvent, ctx: EventRouteContext): void {
  switch (event.type) {
    case 'message.start':
      $chatTurnInFlight.set(true)
      beginAssistantMessage()
      setTurnHadBubbleBreak(false)
      setSpriteState('thinking')

      break
    case 'message.delta': {
      const payload = decodePayload<{ text?: string }>(event.payload)
      const text = payload?.text ?? ''

      if (text) {
        appendAssistantDelta(text)
      }

      break
    }

    case 'message.reasoning.delta': {
      const text = decodePayload<{ text?: string }>(event.payload)?.text ?? ''

      if (text) {
        appendAssistantReasoningDelta(text)
      }

      break
    }

    case 'message.break': {
      // 后端把回合切成连续气泡——收尾当前气泡，下一条 message.delta 开新气泡（后端已插入 0.5–1.5 秒停顿）。
      setTurnHadBubbleBreak(true)
      finalizeAssistantMessage()

      break
    }

    case 'message.media': {
      const payload = decodePayload<{
        session_id: string
        message_id: number
        media_id: string
        bubble: CompanionBubble
      }>(event.payload)

      if (typeof payload.session_id === 'string') {
        invalidateSessionHistory(payload.session_id)
      }

      if (
        payload.session_id === $chatSessionId.get() &&
        typeof payload.message_id === 'number' &&
        typeof payload.media_id === 'string' &&
        (payload.bubble?.type === 'image' || payload.bubble?.type === 'video')
      ) {
        updateMediaBubble(payload.message_id, payload.media_id, payload.bubble)
      }

      break
    }

    case 'message.voice': {
      const payload = decodePayload<{
        session_id: string
        message_id: number
        bubble_index: number
        bubble: CompanionBubble
      }>(event.payload)

      if (typeof payload.session_id === 'string') {
        invalidateSessionHistory(payload.session_id)
      }

      if (
        payload.session_id === $chatSessionId.get() &&
        typeof payload.message_id === 'number' &&
        typeof payload.bubble_index === 'number' &&
        payload.bubble
      ) {
        updateVoiceBubble(payload.message_id, payload.bubble_index, payload.bubble)
      }

      break
    }

    case 'message.persisted': {
      const p = decodePayload<{ role?: string; message_ids?: unknown }>(event.payload)

      if (p?.role === 'user' && Array.isArray(p.message_ids)) {
        bindTrailingUserMessageIds(p.message_ids.filter((id): id is number => typeof id === 'number'))
      }

      break
    }

    case 'message.complete': {
      const payload = decodePayload<{
        bubbles?: CompanionBubble[]
        media?: ChatMediaItem[]
        message_id?: number
        reasoning?: string
        text?: string
        usage?: { completion_tokens?: number; prompt_tokens?: number; total_tokens?: number }
      }>(event.payload)

      // 形状异常时按缺省处理：本分支抛错会跳过下方回合收尾，让 $chatTurnInFlight 卡住。
      const bubbles = Array.isArray(payload.bubbles) ? payload.bubbles : undefined
      const media = Array.isArray(payload.media) ? payload.media : undefined
      const text = chatDisplayText(payload?.text ?? '')

      if (payload?.usage) {
        setSessionContextUsage({
          completionTokens: payload.usage.completion_tokens,
          promptTokens: payload.usage.prompt_tokens,
          totalTokens:
            payload.usage.total_tokens ??
            (payload.usage.prompt_tokens && payload.usage.completion_tokens
              ? payload.usage.prompt_tokens + payload.usage.completion_tokens
              : undefined)
        })
      }

      // 多气泡回合：payload.text 是整轮全文会覆盖最后一个气泡，故保留 last.text；媒体与正文正交，始终挂到最后一格。
      const hadBreak = $turnHadBubbleBreak.get()

      if (bubbles && typeof payload.message_id === 'number') {
        finalizeCompanionReply(bubbles, payload.message_id, payload.reasoning)
      } else {
        finalizeAssistantMessage(hadBreak ? undefined : payload?.text, media, hadBreak ? undefined : payload?.reasoning)

        if (typeof payload?.message_id === 'number') {
          bindTrailingAssistantMessageId(payload.message_id)
        }
      }

      // 媒体已送达但对话界面收起：精灵可见时气泡只做轻量系统提示，点击打开轻语/生活空间；不可见或锁屏时不弹出，媒体已在会话历史中。
      if (
        (media?.length ||
          bubbles?.some(bubble => (bubble.type === 'image' || bubble.type === 'video') && bubble.status === 'ready')) &&
        isSpriteOverlayVisible()
      ) {
        const sys = getStrings().notifications.system
        showMediaHint(
          media?.some(m => m.type === 'video') || bubbles?.some(b => b.type === 'video' && b.status === 'ready')
            ? sys.videoReady
            : sys.imageReady
        )
      }

      setSpriteState('idle', { force: true })

      triggerFootGlowPulse('completed', 1200)

      // 每日互动统计—— chat_turn 仅在确有文本可统计时计数
      if (!ctx.isProxy && (bubbles?.some(bubble => 'text' in bubble && bubble.text.trim()) || text.trim())) {
        reportInteractionStat('chat_turn')
      }

      // in-flight 回合结束——清标记并冲刷回合期间排队的消息（合并为单次批量提交）。
      $chatTurnInFlight.set(false)
      submitPendingBatch()

      break
    }

    case 'error': {
      cancelVoiceBar()
      $chatTurnInFlight.set(false)
      clearPendingPrompts()
      // 强制重置为 idle：thinking/working 时优先级门控会静默拒绝普通状态转换。
      const message = decodePayload<{ message?: string }>(event.payload)?.message ?? getStrings().chat.sendFailed
      markAssistantTerminal({ error: message })
      setSpriteState('idle', { force: true })
      triggerFootGlowPulse('failed', 2000)

      break
    }

    case 'command.result': {
      // 服务端在 command.dispatch RPC response 之外另行广播此事件（PROTOCOL「事件路由」）；触发窗口已通过 RPC 渲染过 pill，本路径只服务其他窗口，RPC 路径的 pushStatusPill 已幂等执行。
      const payload = decodePayload<SlashCommandResultPayload>(event.payload)

      const r = payload?.result

      if (!r) {
        break
      }

      // 仅同步 status_cleared/compress_summary 等历史变化（hydrate=true）：其他窗口需本地 hydrateChatMessages，否则显示陈旧列表。
      if (r.status === 'ok' && r.hydrate) {
        const messages = decodePayload<{ messages?: unknown }>(r.payload).messages

        if (Array.isArray(messages)) {
          const sid = $chatSessionId.get()
          hydrateChatMessages(messages as SessionMessage[])

          if (sid) {
            rememberFullHistory(sid, messages as SessionMessage[])
          }
        }
      }

      break
    }

    case 'compress.completed': {
      // 自动压缩单行插入；手动 /压缩 走 command.result + hydrate=true，互斥互补（PROTOCOL「事件路由」）。
      const p = decodePayload<{ subtype?: string; text?: string; message_id?: number }>(event.payload)

      if (p?.subtype === 'compress_summary' && typeof p.text === 'string') {
        pushStatusPill('compress_summary', p.text)
      }

      break
    }

    case 'message.edited': {
      const payload = decodePayload<{ messages?: SessionMessage[] }>(event.payload)

      if (Array.isArray(payload?.messages)) {
        cancelVoiceBar()
        hydrateEditedChatMessages(payload.messages)
        const sid = $chatSessionId.get()

        if (sid) {
          rememberFullHistory(sid, payload.messages)
        }
      }

      break
    }

    case 'message.deleted': {
      // 多窗口同步：session.undo_to_message RPC 之外服务端另发此事件给同 user 其他窗口；发起窗口已通过 RPC hydrate，本事件供其它窗口追上（session-id 过滤已在路由统一完成）。
      const p = decodePayload<{
        session_id?: string
        deleted_count?: number
        anchor?: { text?: string; content_type?: string; media_json?: string | null }
        messages?: unknown[]
      }>(event.payload)

      if (Array.isArray(p?.messages)) {
        const sid = p.session_id || $chatSessionId.get()
        hydrateChatMessages(p.messages as SessionMessage[])

        if (sid) {
          rememberFullHistory(sid, p.messages as SessionMessage[])
        }
      }

      // 跟随窗口从事件 payload 取 anchor 推到草稿总线——对话组件用 session_id 过滤应用。
      if (p?.session_id && p.anchor) {
        $chatDraftFromUndo.set({
          session_id: p.session_id,
          text: p.anchor.text ?? '',
          content_type: p.anchor.content_type ?? 'text',
          media_json: p.anchor.media_json ?? null
        })
      }

      break
    }

    default:
      break
  }
}
