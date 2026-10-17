import { syncConversationActivity } from '@/app/workflows/conversation-activity'
import { isSpriteOverlayVisible } from '@/app/workflows/proactive-delivery'
import { reportInteractionStat, triggerFootGlowPulse } from '@/modules/character'
import {
  $chatDraftFromUndo,
  $companionSessionId,
  chatDisplayText,
  type ConversationRuntime,
  invalidateSessionHistory,
  rememberFullHistory,
  rememberPendingMessage,
  removeVoicePlayback,
  showMediaHint
} from '@/modules/conversation'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { getStrings } from '@/shared/strings'
import type { ChatMediaItem, CompanionBubble } from '@protocol'

import type { EventRouteContext } from '../gateway-event-util'

// 会话回合事件：message.*、Slash、压缩与撤回。

export function handleConversationEvent(
  event: GatewayEvent,
  ctx: EventRouteContext,
  runtime: ConversationRuntime
): void {
  if (!runtime.isCurrent()) {
    return
  }

  const {
    $chatSessionId,
    $chatTurnInFlight,
    $turnHadBubbleBreak,
    appendAssistantDelta,
    appendAssistantReasoningDelta,
    appendCompanionBubble,
    beginAssistantMessage,
    bindTrailingAssistantMessageId,
    mergePersistedUserMessages,
    applySessionState,
    recoverUnconfirmedSubmission,
    clearPendingPrompts,
    finalizeAssistantMessage,
    finalizeCompanionReply,
    forgetDeletedVoiceMessages,
    hydrateChatMessages,
    hydrateEditedChatMessages,
    markAssistantTerminal,
    pushStatusPill,
    setSessionCacheUsage,
    setSessionContextUsage,
    setTurnHadBubbleBreak,
    submitPendingBatch,
    updateMediaBubble,
    updateVoiceBubble
  } = runtime

  const rememberReply = (text?: string, bubbles?: CompanionBubble[], media?: ChatMediaItem[]): void => {
    const sessionId = runtime.$chatSessionId.get()

    if (!sessionId || sessionId !== $companionSessionId.get()) {
      return
    }

    const preview = chatDisplayText(
      bubbles?.findLast(bubble => 'text' in bubble)?.text ??
        bubbles?.findLast(bubble => bubble.type === 'link')?.title ??
        text ??
        ''
    )

    const hasMedia = media?.length || bubbles?.some(bubble => bubble.type === 'image' || bubble.type === 'video')

    if (preview || hasMedia) {
      rememberPendingMessage(
        sessionId,
        preview ||
          (media?.some(item => item.type === 'video') || bubbles?.some(bubble => bubble.type === 'video')
            ? getStrings().notifications.system.videoReady
            : getStrings().notifications.system.imageReady)
      )
    }
  }

  switch (event.type) {
    case 'message.start': {
      $chatTurnInFlight.set(true)
      const payload = event.payload
      runtime.$replacingReplyMessageId.set(payload.replace_reply_message_id ?? null)

      if (!payload.replace_reply_message_id) {
        beginAssistantMessage()
      }

      setTurnHadBubbleBreak(false)
      syncConversationActivity()

      break
    }

    case 'message.delta': {
      const payload = event.payload
      const text = payload.text

      if (text) {
        appendAssistantDelta(text)
      }

      break
    }

    case 'message.reasoning.delta': {
      const text = event.payload.text

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

    case 'message.bubble': {
      // 结构化回复逐泡交付：按气泡顺序到达，语音泡的音频已就绪（失败时为空，同 message.voice 降级）。
      const payload = event.payload

      const exists = runtime.$chatMessageList
        .get()
        .some(
          item =>
            item.backendMessageId === payload.message_id &&
            runtime.$chatMessageBodies.get()[item.id]?.replyIndex === payload.bubble_index
        )

      appendCompanionBubble(payload.message_id, payload.bubble_index, payload.bubble)

      if (!exists) {
        rememberReply(undefined, [payload.bubble])
      }

      break
    }

    case 'message.media': {
      const payload = event.payload

      if (payload.session_id === $chatSessionId.get()) {
        updateMediaBubble(payload.message_id, payload.media_id, payload.bubble)
      }

      break
    }

    case 'message.audio': {
      const payload = event.payload

      if (payload.session_id !== $chatSessionId.get()) {
        break
      }

      for (const item of runtime.$chatMessageList.get()) {
        if (item.backendMessageId !== payload.message_id) {
          continue
        }

        const body = runtime.$chatMessageBodies.get()[item.id]

        if (!body?.attachments) {
          continue
        }

        runtime.$chatMessageBodies.setKey(item.id, {
          ...body,
          attachments: body.attachments.map(attachment =>
            attachment.type === 'audio' && attachment.url === payload.audio_url
              ? {
                  ...attachment,
                  ...(typeof payload.duration === 'number' ? { duration: payload.duration } : {}),
                  ...(typeof payload.transcript === 'string' ? { transcript: payload.transcript } : {})
                }
              : attachment
          )
        })
      }

      break
    }

    case 'message.voice': {
      const payload = event.payload

      if (payload.session_id === $chatSessionId.get()) {
        updateVoiceBubble(payload.message_id, payload.bubble_index, payload.bubble)
      }

      break
    }

    case 'message.persisted': {
      const payload = event.payload

      if (payload.role === 'user') {
        mergePersistedUserMessages(payload.messages, payload.request_id ?? undefined, payload.message_ids)
      }

      break
    }

    case 'session.snapshot': {
      const snapshot = event.payload

      runtime.applyRemoteSnapshot(snapshot)
      syncConversationActivity()

      break
    }

    case 'session.state': {
      const payload = event.payload

      applySessionState(payload)

      void recoverUnconfirmedSubmission()
      submitPendingBatch()

      syncConversationActivity()

      break
    }

    case 'message.complete': {
      const payload = event.payload

      const bubbles = payload.bubbles ?? undefined
      const media = payload.media ?? undefined
      const text = chatDisplayText(payload.text ?? '')

      const replyExists =
        payload.message_id !== null &&
        runtime.$chatMessageList.get().some(item => item.backendMessageId === payload.message_id)

      setSessionCacheUsage(payload.cache_usage ?? undefined)

      if (payload.usage) {
        setSessionContextUsage({
          contextLimit: payload.usage.context_window ?? undefined,
          completionTokens: payload.usage.completion_tokens ?? undefined,
          promptTokens: payload.usage.prompt_tokens ?? undefined,
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
        finalizeCompanionReply(bubbles, payload.message_id, payload.reasoning ?? undefined)
      } else {
        finalizeAssistantMessage(
          hadBreak ? undefined : (payload.text ?? undefined),
          media,
          hadBreak ? undefined : (payload.reasoning ?? undefined)
        )

        if (typeof payload.message_id === 'number') {
          bindTrailingAssistantMessageId(payload.message_id)
        }
      }

      if (!replyExists) {
        rememberReply(payload.text ?? undefined, bubbles, media)
      }

      const lastReply = runtime.$chatMessageList.get().findLast(item => item.backendMessageId === payload.message_id)

      if (lastReply) {
        runtime.$chatMessageBodies.setKey(lastReply.id, {
          ...runtime.$chatMessageBodies.get()[lastReply.id],
          canReplaceReply: payload.can_replace_reply === true
        })
      }

      // 媒体已送达但对话界面收起：可见精灵用系统提示引导到轻语或主对话；不可见或锁屏时不弹出，媒体保留在会话历史。
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

      triggerFootGlowPulse('completed', 1200)

      // 每日互动统计—— chat_turn 仅在确有文本可统计时计数
      if (ctx.isPresentationHost && (bubbles?.some(bubble => 'text' in bubble && bubble.text.trim()) || text.trim())) {
        reportInteractionStat('chat_turn')
      }

      // in-flight 回合结束——清标记并冲刷回合期间排队的消息（合并为单次批量提交）。
      $chatTurnInFlight.set(false)
      void recoverUnconfirmedSubmission()
      submitPendingBatch()

      syncConversationActivity()

      break
    }

    case 'error': {
      $chatTurnInFlight.set(false)

      const payload = event.payload

      if (runtime.$replacingReplyMessageId.get() || payload.replace_reply_message_id) {
        runtime.$replacingReplyMessageId.set(null)
        pushStatusPill('status_command_result', payload.message)
        syncConversationActivity()

        break
      }

      clearPendingPrompts()
      markAssistantTerminal({
        error: payload.message,
        retryMessageId:
          Number.isSafeInteger(payload.retry_message_id) && (payload.retry_message_id ?? 0) > 0
            ? (payload.retry_message_id ?? undefined)
            : undefined
      })
      syncConversationActivity()
      triggerFootGlowPulse('failed', 2000)

      break
    }

    case 'command.result': {
      const result = event.payload.result

      // 仅同步 status_cleared/compress_summary 等历史变化（hydrate=true）：其他窗口需本地 hydrateChatMessages，否则显示陈旧列表。
      if (result.status === 'ok' && result.hydrate && result.payload && 'messages' in result.payload) {
        const messages = result.payload.messages

        if (messages) {
          const sid = $chatSessionId.get()
          hydrateChatMessages(messages)

          if (sid) {
            rememberFullHistory(sid, messages)

            if ('cleared_count' in result.payload) {
              removeVoicePlayback(sid)
            }
          }
        }
      }

      break
    }

    case 'compress.completed': {
      // 自动压缩单行插入；手动 /压缩 走 command.result + hydrate=true，互斥互补（PROTOCOL「事件路由」）。
      const payload = event.payload

      if (payload.subtype === 'compress_summary') {
        pushStatusPill('compress_summary', payload.text, payload.message_id ?? undefined)
      }

      break
    }

    case 'message.replaced': {
      const payload = event.payload

      runtime.replaceAssistantReplyMessage(payload.replaced_message_id, payload.message)
      const sid = $chatSessionId.get()

      if (sid) {
        removeVoicePlayback(sid, [payload.replaced_message_id])
        invalidateSessionHistory(sid)
      }

      break
    }

    case 'message.edited': {
      const payload = event.payload

      hydrateEditedChatMessages(payload.messages)
      const sid = $chatSessionId.get()

      if (sid) {
        rememberFullHistory(sid, payload.messages)
      }

      break
    }

    case 'message.deleted': {
      const payload = event.payload

      const sid = payload.session_id || $chatSessionId.get()
      forgetDeletedVoiceMessages(payload.messages)
      hydrateChatMessages(payload.messages)

      if (sid) {
        rememberFullHistory(sid, payload.messages)
      }

      // 跟随窗口从事件 payload 取 anchor 推到草稿总线——对话组件用 session_id 过滤应用。
      if (payload.session_id) {
        $chatDraftFromUndo.set({
          session_id: payload.session_id,
          text: payload.anchor.text,
          attachments: payload.anchor.attachments
        })
      }

      break
    }

    default:
      break
  }
}
