import { getStrings } from '@/shared/strings'
import type { ActiveTurnSnapshot, SessionMessage, SessionRuntimeInfo, SessionSnapshot } from '@protocol'

import { compareResourceIds, isResourceId } from '../../../../shared/resource-id'

import { mergeLiveHistoryRows, preserveLiveHistoryRows, projectSessionMessages } from './chat-history-projection'
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
    $replacingReplyMessageId,
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
    appendCompanionBubble,
    setAssistantTool
  } = messages.api

  const { companionBubbleBody, splitUserBubblesEnabled, clearReplyState, clearInflightReply } = messages.internal
  const { bindTrailingUserMessageIds, mergePersistedUserMessages } = submission

  let lastSnapshotReadId = 0

  function replaceAssistantReplyMessage(replacedMessageId: string, message: SessionMessage): void {
    if (!isCurrent() || !isResourceId(message.id)) {
      return
    }

    const original = $chatMessageList.get()
    const bodies = $chatMessageBodies.get()
    const replacedIndex = original.findIndex(item => item.backendMessageId === replacedMessageId)

    if (
      replacedIndex < 0 &&
      original.some(
        item => item.backendMessageId !== undefined && compareResourceIds(item.backendMessageId, message.id) >= 0
      )
    ) {
      return
    }

    const keep = original.filter(
      item => item.backendMessageId !== replacedMessageId && item.backendMessageId !== message.id
    )

    const projected = projectSessionMessages([message], {
      splitUserBubbles: splitUserBubblesEnabled(),
      nextMessageId: nextChatMessageId,
      projectBubble: companionBubbleBody
    })

    const pendingIndex = keep.findIndex(item => item.role === 'user' && item.backendMessageId === undefined)

    const index =
      replacedIndex < 0
        ? pendingIndex < 0
          ? keep.length
          : pendingIndex
        : original.slice(0, replacedIndex).filter(item => item.backendMessageId !== message.id).length

    const activeVoice = activeVoiceMessageId()

    if (original.some(item => item.backendMessageId === replacedMessageId && item.id === activeVoice)) {
      conversationVoiceSink().cancel($chatSessionId.get())
    }

    state.historyReplacementRevision++

    if ($replacingReplyMessageId.get() === replacedMessageId) {
      clearInflightReply()
      $replacingReplyMessageId.set(null)
      $lastAssistantStreaming.set(false)
    }

    $chatMessageBodies.set({ ...Object.fromEntries(keep.map(item => [item.id, bodies[item.id]])), ...projected.bodies })
    $chatMessageList.set([...keep.slice(0, index), ...projected.items, ...keep.slice(index)])
  }

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

  type HistoryProjection = ReturnType<typeof projectSessionMessages>

  /** 历史投影：不落库，由调用方决定合并活行后一次性写入。 */
  function projectHistory(messages: SessionMessage[]): HistoryProjection {
    return projectSessionMessages(messages, {
      splitUserBubbles: splitUserBubblesEnabled(),
      nextMessageId: nextChatMessageId,
      projectBubble: companionBubbleBody
    })
  }

  /** 投影结果一次性落库：消息列表、气泡体、上下文用量与水合标记。 */
  function commitHistory(projection: HistoryProjection, info?: SessionRuntimeInfo): void {
    $chatMessageBodies.set(projection.bodies)
    $chatMessageList.set(projection.items)
    $lastAssistantStreaming.set(false)

    // 估算 Token 占用（~3 字符/Token）；分项清零避免切换会话残留，无 info 的本会话重水合沿用当前上下文上限。
    const approxTokens = Math.round(projection.totalChars / 3)
    const contextLimit = info ? info.context_window || 0 : $sessionContextUsage.get().contextLimit
    resetSessionContextUsage(contextLimit, approxTokens)
    $historyHydrated.set(true)
  }

  function replaceChatMessages(messages: SessionMessage[], info?: SessionRuntimeInfo, cancelVoice = true): void {
    if (cancelVoice) {
      conversationVoiceSink().cancel($chatSessionId.get())
    }

    if (info) {
      hydrateSessionInfo(info)
    }

    commitHistory(projectHistory(messages), info)
  }

  function applyRemoteSnapshot(snapshot: Partial<SessionSnapshot>, historySync?: ConversationHistorySync): boolean {
    if (!isCurrent() || !Array.isArray(snapshot.messages)) {
      return false
    }

    if (snapshot.snapshot_read_id !== undefined && snapshot.snapshot_read_id <= lastSnapshotReadId) {
      return true
    }

    if (historySync && historySync.historyRevision !== state.historyReplacementRevision) {
      return false
    }

    const historyChanged =
      snapshot.history_revision !== undefined && snapshot.history_revision !== (state.serverHistoryRevision ?? 0)

    if (historyChanged) {
      hydrateEditedChatMessages(snapshot.messages)
    } else if (!hydrateSyncedChatMessages(snapshot.messages, snapshot.info, historySync ?? captureHistorySync())) {
      return false
    }

    restoreActiveTurn(snapshot.active_turn, snapshot.info?.running)
    state.serverHistoryRevision = snapshot.history_revision ?? state.serverHistoryRevision
    const last = snapshot.last_submission

    if (last && !['accepted', 'running'].includes(last.status)) {
      state.submission.settledRequestId = last.request_id
    }

    if (
      !$chatTurnInFlight.get() &&
      last &&
      !last.replace_reply_message_id &&
      ['failed', 'cancelled', 'interrupted'].includes(last.status)
    ) {
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

    lastSnapshotReadId = snapshot.snapshot_read_id ?? lastSnapshotReadId

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
    $replacingReplyMessageId.set(snapshot?.replace_reply_message_id ?? null)

    if (!snapshot) {
      return
    }

    if (snapshot.replace_reply_message_id) {
      return
    }

    mergePersistedUserMessages(snapshot.messages ?? [], snapshot.request_id, snapshot.message_ids)
    const assistantMessageId = snapshot.assistant_message_id

    if (isResourceId(assistantMessageId)) {
      snapshot.bubbles.forEach((bubble, index) => appendCompanionBubble(assistantMessageId, index, bubble))
    } else {
      for (const bubble of snapshot.bubbles) {
        if (bubble.type === 'text') {
          beginAssistantMessage()
          appendAssistantDelta(bubble.text)
          finalizeAssistantMessage()
        }
      }
    }

    $turnHadBubbleBreak.set(snapshot.bubbles.length > 0)

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
    $replacingReplyMessageId.set(null)
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

    if (liveList.length === 0) {
      conversationVoiceSink().cancel($chatSessionId.get())
    }

    if (info) {
      hydrateSessionInfo(info)
    }

    const projection = projectHistory(messages)

    if (liveList.length > 0) {
      // 活行与投影结果合并后只落库一次：中间态不再出现"活行短暂消失"。
      const next = mergeLiveHistoryRows(projection.items, liveList)

      commitHistory(
        {
          ...projection,
          bodies: Object.fromEntries(next.map(item => [item.id, liveBodies[item.id] ?? projection.bodies[item.id]])),
          items: next
        },
        info
      )
      $lastAssistantStreaming.set(liveList.some(item => liveBodies[item.id]?.streaming))
    } else {
      commitHistory(projection, info)
    }

    $chatTurnInFlight.set(changed ? inFlight : (info?.running ?? inFlight))

    return true
  }

  return {
    hydrateEditedChatMessages,
    replaceAssistantReplyMessage,
    hydrateChatMessages,
    applyRemoteSnapshot,
    restoreActiveTurn,
    resetChatMessages,
    captureHistorySync,
    hydrateSyncedChatMessages
  }
}
