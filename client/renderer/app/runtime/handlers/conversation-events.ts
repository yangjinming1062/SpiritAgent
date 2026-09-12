import type { SpeechStyle } from '@ipc/contracts'

import { $screenLocked, reportInteractionStat, setSpriteState, triggerFootGlowPulse } from '@/modules/character'
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
  hydrateChatMessages,
  markAssistantTerminal,
  pushStatusPill,
  rememberFullHistory,
  setSessionContextUsage,
  setTurnHadBubbleBreak,
  showMediaHint,
  submitPendingBatch
} from '@/modules/conversation'
import { cancelVoiceBar, isLivingVoiceBarActive } from '@/modules/speech'
import { type GatewayEvent, type SlashCommandResultPayload } from '@/shared/lib/gateway-protocol'
import { $chatVisible } from '@/shared/store/chat-visibility'
import { getStrings } from '@/shared/strings'
import type { ChatMediaItem, SessionMessage } from '@/shared/types/spiritagent'

import { speechText } from '../../../../shared/speech-text'
import { decodePayload, type EventRouteContext } from '../gateway-event-util'

// 会话回合事件处理器：message.* 与 slash / 压缩 / 撤回的会话状态更新。
// 精灵表现命令（thinking / idle）经呈现端口下达。

export function handleConversationEvent(event: GatewayEvent, ctx: EventRouteContext): void {
  switch (event.type) {
    case 'message.start':
      beginAssistantMessage()
      setTurnHadBubbleBreak(false)
      setSpriteState('thinking')

      break
    case 'message.delta': {
      const payload = decodePayload<{ text?: string; speech_style?: SpeechStyle }>(event.payload)
      const text = payload?.text ?? ''

      if (text) {
        appendAssistantDelta(text, payload?.speech_style)
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
      // 后端把回合切成了连续的气泡——收尾当前气泡；
      // 下一条 message.delta 会开一个新气泡（后端已在它们之间插入 0.5–1.5 秒停顿）。
      setTurnHadBubbleBreak(true)
      finalizeAssistantMessage()

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
        speech_style?: SpeechStyle
        media?: ChatMediaItem[]
        message_id?: number
        reasoning?: string
        text?: string
        usage?: { completion_tokens?: number; prompt_tokens?: number; total_tokens?: number }
      }>(event.payload)

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

      // 锁屏状态下，抑制渲染层的提示。
      const screenLocked = $screenLocked.get()

      // 多气泡回合：每个气泡各自携带流式文本；
      // payload.text 是整轮（包含两个气泡）的全文，会覆盖最后一个气泡。
      // 这种情况下保留 last.text。媒体与正文正交，始终挂到最后一格。
      const hadBreak = $turnHadBubbleBreak.get()
      finalizeAssistantMessage(
        hadBreak ? undefined : payload?.text,
        payload?.media,
        hadBreak ? undefined : payload?.reasoning,
        { speechStyle: payload?.speech_style }
      )

      if (typeof payload?.message_id === 'number') {
        bindTrailingAssistantMessageId(payload.message_id)
      }

      // 媒体已送达但对话界面收起：气泡只做轻量系统提示，点击打开轻语/生活空间查看。
      if (payload?.media?.length && !$chatVisible.get() && !screenLocked) {
        const sys = getStrings().notifications.system
        showMediaHint(payload.media.some(m => m.type === 'video') ? sys.videoReady : sys.imageReady)
      }

      const livingVoiceActive = isLivingVoiceBarActive()

      if (!livingVoiceActive || !speechText(text)) {
        setSpriteState('idle', { force: true })
      }

      triggerFootGlowPulse('completed', 1200)

      // 每日互动统计——chat_turn 仅在确有文本可统计时计数
      if (!ctx.isProxy && text.trim()) {
        reportInteractionStat('chat_turn')
      }

      // in-flight 回合结束——清掉标记并冲刷用户在回合运行期间排队的消息
      // （合并为单次批量提交）。
      $chatTurnInFlight.set(false)
      submitPendingBatch()

      break
    }

    case 'error': {
      cancelVoiceBar()
      $chatTurnInFlight.set(false)
      clearPendingPrompts()
      // 强制重置为 idle——精灵在 'thinking' / 'working' 时，
      // 优先级门控会静默拒绝普通的状态转换。
      const message = decodePayload<{ message?: string }>(event.payload)?.message ?? getStrings().chat.sendFailed
      markAssistantTerminal({ error: message })
      setSpriteState('idle', { force: true })
      triggerFootGlowPulse('failed', 2000)

      break
    }

    case 'command.result': {
      // 服务端在 command.dispatch RPC response 之外另行广播此事件（PROTOCOL §1.3）；
      // 触发它的窗口已通过 RPC 路径自己渲染过 pill，本路径只服务其他窗口的同步渲染。
      // RPC 路径的 pushStatusPill 已在 slash command 执行中幂等执行。
      const payload = decodePayload<SlashCommandResultPayload>(event.payload)

      const r = payload?.result

      if (!r) {
        break
      }

      // 仅同步 status_cleared / compress_summary 等历史变化（hydrate=true）：
      // 其他窗口需要本地 hydrateChatMessages，否则会显示陈旧消息列表。
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
      // 自动压缩单行插入；手动 /压缩 走 command.result + hydrate=true，互斥互补（PROTOCOL §1.3）。
      const p = decodePayload<{ subtype?: string; text?: string; message_id?: number }>(event.payload)

      if (p?.subtype === 'compress_summary' && typeof p.text === 'string') {
        pushStatusPill('compress_summary', p.text)
      }

      break
    }

    case 'message.deleted': {
      // 多窗口同步：session.undo_to_message RPC 之外，服务端另发此事件给同 user 的其他窗口。
      // 发起窗口已通过 RPC 路径 hydrate，其它窗口借本事件追上。session-id 过滤已在路由统一完成。
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
