import { atom, computed, map } from 'nanostores'

import type { CacheUsageSummary, SessionRuntimeInfo } from '@protocol'

import { normalizeChatSessionKind, toSessionSettings } from './chat-message-rules'
import type { ConversationSubmission } from './chat-requests'
import type {
  ChatMessageBody,
  ChatMessageListItem,
  ChatSessionKind,
  PendingPromptItem,
  SessionContextUsage,
  SessionSettings
} from './chat-runtime-types'

interface PromptSubmissionState {
  submissionGeneration: number
  submittedBubbleIds: Set<string>
  submittedRequestId: string | null
  activeRequestId: string | null
  settledRequestId: string | null
  submissionRecovery: Promise<void> | null
  unconfirmedSubmission: { params: ConversationSubmission; fingerprint: string; requestId: string } | null
}

// 每个会话只有这一份状态；控制器持有同一对象，不复制消息或在途提交。
export function createConversationState(sessionId: string | null) {
  const $historyHydrated = atom(false)

  const $runtimeRevision = atom(0)

  const $chatMessageList = atom<ChatMessageListItem[]>([])

  const $chatMessageBodies = map<Record<string, ChatMessageBody>>({})

  const $lastAssistantStreaming = atom<boolean>(false)

  const $chatStreamingTick = atom<number>(0)

  const $chatSessionId = atom<string | null>(sessionId)

  const $chatSessionKind = atom<ChatSessionKind>('standard')

  const $chatSessionPresetId = atom<string | null>(null)

  const $chatSessionReadOnly = atom(false)

  const $pendingPromptBatch = atom<PendingPromptItem[]>([])

  const $chatTurnInFlight = atom<boolean>(false)
  const $stoppingRequestId = atom<string | null>(null)
  const $replacingReplyMessageId = atom<string | null>(null)

  const $replaceableAssistantMessage = computed(
    [
      $chatMessageList,
      $chatMessageBodies,
      $chatTurnInFlight,
      $pendingPromptBatch,
      $chatSessionReadOnly,
      $stoppingRequestId
    ],
    (list, bodies, inFlight, pending, readOnly, stopping): ChatMessageListItem | null => {
      if (readOnly || inFlight || stopping !== null || pending.length > 0) {
        return null
      }

      const last = list.findLast(
        item =>
          item.subtype === 'status_proactive' ||
          (!item.subtype && !bodies[item.id]?.error && !bodies[item.id]?.cancelled)
      )

      return last?.role === 'assistant' && last.backendMessageId && bodies[last.id]?.canReplaceReply ? last : null
    }
  )

  const $retryableAssistantMessage = computed(
    [
      $chatMessageList,
      $chatMessageBodies,
      $chatTurnInFlight,
      $pendingPromptBatch,
      $chatSessionReadOnly,
      $stoppingRequestId
    ],
    (list, bodies, inFlight, pending, readOnly, stopping): ChatMessageListItem | null => {
      if (readOnly || inFlight || stopping !== null || pending.length > 0) {
        return null
      }

      const last = list.at(-1)
      const body = last ? bodies[last.id] : undefined
      const user = list.findLast(item => item.role === 'user')

      return last?.role === 'assistant' &&
        body?.error &&
        body.retryMessageId === user?.backendMessageId &&
        body.retryMessageId
        ? last
        : null
    }
  )

  // 当后端在 in-flight 回合期间发出 bubble.break 时置位，防止 message.complete 的全文/推理覆盖末尾气泡。
  const $turnHadBubbleBreak = atom<boolean>(false)

  const $sessionSettings = atom<SessionSettings>({})

  function hydrateSessionInfo(info: SessionRuntimeInfo): void {
    $chatSessionKind.set(normalizeChatSessionKind(info.kind))
    $chatSessionReadOnly.set(info.is_automation === true)
    $chatSessionPresetId.set(info.system_preset_id ?? null)
    $sessionSettings.set(toSessionSettings(info.settings))
  }

  function updateSessionSetting<K extends keyof SessionSettings>(key: K, value: SessionSettings[K]): void {
    $sessionSettings.set({
      ...$sessionSettings.get(),
      [key]: value
    })
  }

  const $sessionContextUsage = atom<SessionContextUsage>({
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    contextLimit: 0
  })

  const $sessionCacheUsage = atom<CacheUsageSummary | null>(null)

  function setSessionCacheUsage(usage?: CacheUsageSummary): void {
    const validCount = (value: unknown): value is number =>
      typeof value === 'number' && Number.isSafeInteger(value) && value >= 0

    if (
      !usage ||
      !validCount(usage.request_count) ||
      !validCount(usage.reported_request_count) ||
      usage.reported_request_count > usage.request_count ||
      (usage.reported_request_count === 0
        ? usage.input_tokens !== null || usage.cached_input_tokens !== null || usage.cache_hit_rate !== null
        : !validCount(usage.input_tokens) ||
          !validCount(usage.cached_input_tokens) ||
          usage.cached_input_tokens > usage.input_tokens ||
          (usage.input_tokens === 0
            ? usage.cache_hit_rate !== null
            : typeof usage.cache_hit_rate !== 'number' ||
              !Number.isFinite(usage.cache_hit_rate) ||
              usage.cache_hit_rate < 0 ||
              usage.cache_hit_rate > 1))
    ) {
      $sessionCacheUsage.set(null)

      return
    }

    $sessionCacheUsage.set(usage)
  }

  function setSessionContextUsage(usage: Partial<SessionContextUsage>): void {
    const current = $sessionContextUsage.get()
    const promptTokens = usage.promptTokens ?? current.promptTokens
    const completionTokens = usage.completionTokens ?? current.completionTokens

    const totalTokens =
      usage.totalTokens ??
      (usage.promptTokens !== undefined || usage.completionTokens !== undefined
        ? promptTokens + completionTokens
        : current.totalTokens)

    const contextLimit = usage.contextLimit ?? current.contextLimit

    $sessionContextUsage.set({
      promptTokens,
      completionTokens,
      totalTokens,
      contextLimit
    })
  }

  function resetSessionContextUsage(contextLimit?: null | number, totalTokens = 0): void {
    $sessionCacheUsage.set(null)
    $sessionContextUsage.set({
      promptTokens: 0,
      completionTokens: 0,
      totalTokens,
      contextLimit: contextLimit ?? 0
    })
  }

  const submission: PromptSubmissionState = {
    submissionGeneration: 0,
    submittedBubbleIds: new Set(),
    submittedRequestId: null,
    activeRequestId: null,
    settledRequestId: null,
    submissionRecovery: null,
    unconfirmedSubmission: null
  }

  return {
    stores: {
      $historyHydrated,
      $runtimeRevision,
      $chatMessageList,
      $chatMessageBodies,
      $lastAssistantStreaming,
      $chatStreamingTick,
      $chatSessionId,
      $chatSessionKind,
      $chatSessionPresetId,
      $chatSessionReadOnly,
      $pendingPromptBatch,
      $chatTurnInFlight,
      $stoppingRequestId,
      $replacingReplyMessageId,
      $replaceableAssistantMessage,
      $retryableAssistantMessage,
      $turnHadBubbleBreak,
      $sessionSettings,
      $sessionContextUsage,
      $sessionCacheUsage,
      hydrateSessionInfo,
      updateSessionSetting,
      setSessionCacheUsage,
      setSessionContextUsage,
      resetSessionContextUsage
    },
    submission,
    historyEditRevision: 0,
    historyReplacementRevision: 0,
    serverHistoryRevision: undefined as number | undefined
  }
}

export type ConversationState = ReturnType<typeof createConversationState>
