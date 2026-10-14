import { getStrings } from '@/shared/strings'
import type { ActiveTurnSnapshot, SessionMessage, SessionResumeResponse, SessionRuntimeInfo } from '@protocol'

import {
  mergeIncrementalHistoryRows,
  mergeLiveHistoryRows,
  preserveLiveHistoryRows,
  projectSessionMessages
} from './chat-history-projection'
import type { ChatMessageController } from './chat-message-controller'
import type { ConversationState } from './chat-runtime-state'
import type { ConversationHistorySync } from './chat-runtime-types'
import type { ChatSubmissionController } from './chat-submission-controller'
import { nextChatMessageId } from './conversation-state'
import { activeVoiceMessageId, conversationVoiceSink } from './voice-link'

export function createChatHistoryController(
  state: ConversationState,
  messages: ChatMessageController,
  submission: ChatSubmissionController,
  isCurrent: () => boolean
) {
  const {
    $chatMessageList,
    $chatMessageBodies,
    $chatSessionId,
    $lastAssistantStreaming,
    $chatTurnInFlight,
    $turnHadBubbleBreak,
    $pendingPromptBatch,
    $chatSessionPresetId,
    $historyHydrated,
    $runtimeRevision,
    $sessionCacheUsage,
    $sessionContextUsage,
    hydrateSessionInfo,
    resetSessionContextUsage
  } = state.stores

  const {
    forgetDeletedVoiceMessages,
    markAssistantTerminal,
    beginAssistantMessage,
    appendAssistantDelta,
    appendAssistantReasoningDelta,
    finalizeAssistantMessage,
    setAssistantTool
  } = messages.api

  const { companionBubbleBody, splitUserBubblesEnabled, clearReplyState, clearInflightReply } = messages.internal
  const { bindTrailingUserMessageIds, mergePersistedUserMessages } = submission

  let snapshotPosition: { streamId: string; seq: number } | null = null

  // 用从后端加载的会话替换面板的聊天记录；其他窗口可能正在连发或等待提交确认，历史修订不能删掉未落库的输入。
  function hydrateEditedChatMessages(messages: SessionMessage[]): void {
    forgetDeletedVoiceMessages(messages)
    state.historyEditRevision++
    const pendingIds = new Set($pendingPromptBatch.get().map(item => item.messageId))

    const pendingRows = $chatMessageList
      .get()
      .filter(
        item =>
          pendingIds.has(item.id) ||
          (state.submission.submittedBubbleIds.has(item.id) && item.backendMessageId === undefined)
      )

    const previousBodies = $chatMessageBodies.get()

    $chatTurnInFlight.set(true)
    hydrateChatMessages(messages)

    for (const item of pendingRows) {
      $chatMessageBodies.setKey(item.id, previousBodies[item.id])
    }

    $chatMessageList.set([...$chatMessageList.get(), ...pendingRows])
  }

  function hydrateChatMessages(messages: SessionMessage[], info?: SessionRuntimeInfo): void {
    state.historyReplacementRevision++
    replaceChatMessages(messages, info)
  }

  function replaceChatMessages(messages: SessionMessage[], info?: SessionRuntimeInfo, cancelVoice = true): void {
    if (cancelVoice) {
      conversationVoiceSink().cancel($chatSessionId.get())
    }

    if (info) {
      hydrateSessionInfo(info)
    }

    const { items, bodies, totalChars } = projectSessionMessages(messages, {
      splitUserBubbles: splitUserBubblesEnabled(),
      nextMessageId: nextChatMessageId,
      projectBubble: companionBubbleBody
    })

    $chatMessageBodies.set(bodies)
    $chatMessageList.set(items)
    $lastAssistantStreaming.set(false)

    // 估算 Token 占用（~3 字符/Token）；分项清零避免切换会话残留，无 info 的本会话重水合沿用当前上下文上限。
    const approxTokens = Math.round(totalChars / 3)
    const contextLimit = info ? info.context_window || 0 : $sessionContextUsage.get().contextLimit
    resetSessionContextUsage(contextLimit, approxTokens)
    $historyHydrated.set(true)
  }

  function applyRemoteSnapshot(
    snapshot: Partial<SessionResumeResponse>,
    historySync?: ConversationHistorySync
  ): boolean {
    if (
      !isCurrent() ||
      !Array.isArray(snapshot.messages) ||
      (historySync && historySync.historyRevision !== state.historyReplacementRevision)
    ) {
      return false
    }

    // 宿主的增量广播可能先于本窗口的完整历史，未初始化时不能据此建立历史基底。
    if (snapshot.incremental && !$historyHydrated.get()) {
      return false
    }

    if (snapshot.resumed) {
      if (snapshot.info) {
        hydrateSessionInfo(snapshot.info)
      }

      if (snapshot.replayed_count === 0 && snapshot.active_turn?.running && !$chatTurnInFlight.get()) {
        restoreActiveTurn(snapshot.active_turn)
      }

      return true
    }

    if (snapshot.stream_id && typeof snapshot.current_seq === 'number') {
      if (snapshotPosition?.streamId === snapshot.stream_id && snapshot.current_seq <= snapshotPosition.seq) {
        if (snapshot.current_seq === snapshotPosition.seq && snapshot.info) {
          hydrateSessionInfo(snapshot.info)
        }

        return true
      }
    }

    if (snapshot.incremental) {
      const original = $chatMessageList.get()
      const originalBodies = $chatMessageBodies.get()
      const incomingIds = new Set(snapshot.messages.map(message => message.id))
      replaceChatMessages(snapshot.messages, snapshot.info, false)
      const incoming = $chatMessageList.get()
      const incomingBodies = $chatMessageBodies.get()

      const merged = mergeIncrementalHistoryRows(original, incoming, incomingIds)
      $chatMessageBodies.set(
        Object.fromEntries(merged.map(item => [item.id, incomingBodies[item.id] ?? originalBodies[item.id]]))
      )
      $chatMessageList.set(merged)
    } else if (!hydrateSyncedChatMessages(snapshot.messages, snapshot.info, historySync ?? captureHistorySync())) {
      return false
    }

    restoreActiveTurn(snapshot.active_turn, snapshot.info?.running)
    const last = snapshot.last_submission

    if (last && !['accepted', 'running'].includes(last.status)) {
      state.submission.settledRequestId = last.request_id
    }

    if (!$chatTurnInFlight.get() && last && ['failed', 'cancelled', 'interrupted'].includes(last.status)) {
      const latestUser = $chatMessageList
        .get()
        .findLast(item => item.role === 'user' && item.backendMessageId !== undefined)

      if (latestUser?.backendMessageId && last.message_ids?.includes(latestUser.backendMessageId)) {
        bindTrailingUserMessageIds(last.message_ids, last.request_id)

        if (state.submission.unconfirmedSubmission?.requestId === last.request_id) {
          state.submission.unconfirmedSubmission = null
        }

        state.submission.activeRequestId = last.request_id
        markAssistantTerminal(
          last.status === 'cancelled'
            ? { cancelled: true, beforePending: true }
            : {
                beforePending: true,
                error: last.error || getStrings().chat.connectionInterrupted,
                retryMessageId: last.retry_message_id ?? undefined
              }
        )
        state.submission.activeRequestId = null
      }
    }

    if (snapshot.stream_id && typeof snapshot.current_seq === 'number') {
      snapshotPosition = { streamId: snapshot.stream_id, seq: snapshot.current_seq }
    }

    return true
  }

  function restoreActiveTurn(snapshot?: ActiveTurnSnapshot | null, running = false): void {
    if (!isCurrent()) {
      return
    }

    const bodies = $chatMessageBodies.get()
    const previousRequestId = state.submission.activeRequestId

    const keep = $chatMessageList.get().filter(item => {
      const body = bodies[item.id]

      return !(
        item.role === 'assistant' &&
        item.backendMessageId === undefined &&
        (body?.streaming ||
          body?.error === getStrings().chat.connectionInterrupted ||
          (body?.requestId && (body.requestId === previousRequestId || body.requestId === snapshot?.request_id)))
      )
    })

    $chatMessageList.set(keep)
    $chatMessageBodies.set(Object.fromEntries(keep.map(item => [item.id, bodies[item.id]])))
    $lastAssistantStreaming.set(false)
    clearInflightReply()
    state.submission.activeRequestId = snapshot?.request_id ?? null
    $chatTurnInFlight.set(snapshot?.running ?? running)

    if (!snapshot) {
      return
    }

    mergePersistedUserMessages(snapshot.messages ?? [], snapshot.request_id, snapshot.message_ids)
    const endedBubbles = snapshot.bubbles.filter(bubble => bubble.type === 'text')

    for (const bubble of endedBubbles) {
      beginAssistantMessage()
      appendAssistantDelta(bubble.text)
      finalizeAssistantMessage()
    }

    $turnHadBubbleBreak.set(endedBubbles.length > 0)

    if (snapshot.text || snapshot.reasoning || snapshot.tools.length) {
      beginAssistantMessage()

      if (snapshot.text) {
        appendAssistantDelta(snapshot.text)
      }

      if (snapshot.reasoning) {
        appendAssistantReasoningDelta(snapshot.reasoning)
      }

      for (const tool of snapshot.tools) {
        if (tool.name) {
          setAssistantTool(tool.name)
        }
      }

      if (!snapshot.tools.some(tool => tool.status === 'running')) {
        setAssistantTool(null)
      }
    }
  }

  // 重置消息列表与 bodies，不触碰 $chatSessionId 与 pending batch。
  function resetChatMessages(): void {
    state.historyReplacementRevision++
    $sessionCacheUsage.set(null)
    clearReplyState()
    conversationVoiceSink().cancel($chatSessionId.get())
    $chatMessageList.set([])
    $chatMessageBodies.set({})
    $chatSessionPresetId.set(null)
    $lastAssistantStreaming.set(false)
    $chatTurnInFlight.set(false)
    $turnHadBubbleBreak.set(false)
  }

  const captureHistorySync = (): ConversationHistorySync => ({
    revision: $runtimeRevision.get(),
    historyRevision: state.historyReplacementRevision,
    messages: $chatMessageList.get(),
    bodies: $chatMessageBodies.get()
  })

  const hydrateSyncedChatMessages = (
    messages: SessionMessage[],
    info?: SessionRuntimeInfo,
    snapshot?: ConversationHistorySync
  ): boolean => {
    // 编辑、撤回及清空的全量结果有独立权威，较早发起的同步不能恢复其已删除行。
    if (!isCurrent() || state.historyReplacementRevision !== (snapshot?.historyRevision ?? 0)) {
      return false
    }

    const liveBodies = $chatMessageBodies.get()

    const { changed, liveList } = preserveLiveHistoryRows(
      $chatMessageList.get(),
      liveBodies,
      snapshot,
      activeVoiceMessageId(),
      $runtimeRevision.get()
    )

    const inFlight = $chatTurnInFlight.get()
    replaceChatMessages(messages, info, liveList.length === 0)

    if (liveList.length > 0) {
      const history = $chatMessageList.get()
      const next = mergeLiveHistoryRows(history, liveList)

      const hydratedBodies = $chatMessageBodies.get()
      $chatMessageBodies.set(
        Object.fromEntries(next.map(item => [item.id, liveBodies[item.id] ?? hydratedBodies[item.id]]))
      )
      $chatMessageList.set(next)
      $lastAssistantStreaming.set(liveList.some(item => liveBodies[item.id]?.streaming))
    }

    $chatTurnInFlight.set(changed ? inFlight : (info?.running ?? inFlight))

    return true
  }

  return {
    hydrateEditedChatMessages,
    hydrateChatMessages,
    applyRemoteSnapshot,
    restoreActiveTurn,
    resetChatMessages,
    captureHistorySync,
    hydrateSyncedChatMessages
  }
}
