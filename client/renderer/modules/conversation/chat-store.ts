import { atom, map, type ReadableAtom, type WritableAtom } from 'nanostores'

import {
  persistString,
  registerStorageClearHandler,
  registerStorageRestoreHandler,
  storedString
} from '@/shared/lib/storage'

import { type ConversationRuntime, createConversationRuntime, type SessionSettings } from './chat-runtime'
import { $companionSessionId, CHAT_SESSION_ID_KEY } from './conversation-state'
import { conversationVoiceSink } from './voice-link'
export type {
  ChatEditDraft,
  ChatMessageBody,
  ChatMessageListItem,
  ChatSessionKind,
  PendingAttachment,
  SessionContextUsage,
  SessionSettings
} from './chat-runtime'
export {
  $chatDraftFromUndo,
  $companionSessionId,
  $pendingExternalAttachment,
  $proactiveBubble,
  clearExternalAttachment,
  pushExternalAttachment,
  setCompanionSessionId,
  setProactiveBubble,
  showMediaHint
} from './conversation-state'

export const $chatSessionId = atom<string | null>(storedString(CHAT_SESSION_ID_KEY))
const runtimes = new Map<string | null, ConversationRuntime>()
const runtimeReferences = new Map<ConversationRuntime, number>()

export function getConversationRuntime(sessionId: string | null): ConversationRuntime {
  let runtime = runtimes.get(sessionId)

  if (!runtime) {
    runtime = createConversationRuntime(sessionId)
    runtimes.set(sessionId, runtime)
    const stopPruning = runtime.$chatTurnInFlight.listen(pruneConversationRuntimes)
    const dispose = runtime.dispose

    runtime.dispose = () => {
      stopPruning()
      dispose()
    }
  }

  return runtime
}

export function retainConversationRuntime(runtime: ConversationRuntime): () => void {
  runtimeReferences.set(runtime, (runtimeReferences.get(runtime) ?? 0) + 1)

  return () => {
    const count = runtimeReferences.get(runtime) ?? 0

    if (count <= 1) {
      runtimeReferences.delete(runtime)
    } else {
      runtimeReferences.set(runtime, count - 1)
    }

    pruneConversationRuntimes()
  }
}

function pruneConversationRuntimes(): void {
  for (const [sessionId, runtime] of runtimes) {
    if (
      sessionId === $chatSessionId.get() ||
      sessionId === $companionSessionId.get() ||
      runtimeReferences.has(runtime) ||
      runtime.$chatTurnInFlight.get() ||
      runtime.$pendingPromptBatch.get().length > 0
    ) {
      continue
    }

    runtime.dispose()
    runtimes.delete(sessionId)
  }
}

export function findConversationRuntime(sessionId: string): ConversationRuntime | undefined {
  return runtimes.get(sessionId)
}

export function conversationRuntimes(): ConversationRuntime[] {
  return [...runtimes.values()]
}

export function runtimeForMessage(messageId: string): ConversationRuntime | undefined {
  return conversationRuntimes().find(runtime => runtime.$chatMessageBodies.get()[messageId] !== undefined)
}

const $activeRuntime = atom(getConversationRuntime($chatSessionId.get()))
$chatSessionId.listen(sessionId => $activeRuntime.set(getConversationRuntime(sessionId)))

export function activeConversationRuntime(): ConversationRuntime {
  return $activeRuntime.get()
}

// 旧窗口公开store投影到当前runtime；异步工作直接持有发起时的runtime。
function followActiveRuntime(bind: (runtime: ConversationRuntime) => () => void): void {
  let stop: (() => void) | undefined
  $activeRuntime.subscribe(runtime => {
    stop?.()
    stop = bind(runtime)
  })
}

function projectRead<T>(select: (runtime: ConversationRuntime) => ReadableAtom<T>): WritableAtom<T> {
  const projected = atom(select(activeConversationRuntime()).get())
  const apply = projected.set.bind(projected)
  followActiveRuntime(runtime => select(runtime).subscribe(apply))

  return projected
}

function projectAtom<T>(select: (runtime: ConversationRuntime) => WritableAtom<T>): WritableAtom<T> {
  const projected = projectRead(select)
  projected.set = value => select(activeConversationRuntime()).set(value)

  return projected
}

function projectMap(
  select: (runtime: ConversationRuntime) => ConversationRuntime['$chatMessageBodies']
): ConversationRuntime['$chatMessageBodies'] {
  const projected = map(select(activeConversationRuntime()).get())
  const apply = projected.set.bind(projected)
  const applyKey = projected.setKey.bind(projected)
  followActiveRuntime(runtime =>
    select(runtime).subscribe((value, _old, key) => {
      if (key === undefined) {
        apply(value)
      } else {
        applyKey(key, value[key])
      }
    })
  )
  projected.set = value => select(activeConversationRuntime()).set(value)
  projected.setKey = (key, value) => select(activeConversationRuntime()).setKey(key, value)

  return projected
}

export const $chatMessageList = projectAtom(runtime => runtime.$chatMessageList)
export const $chatMessageBodies = projectMap(runtime => runtime.$chatMessageBodies)
export const $lastAssistantStreaming = projectAtom(runtime => runtime.$lastAssistantStreaming)
export const $chatStreamingTick = projectAtom(runtime => runtime.$chatStreamingTick)
export const $chatSessionKind = projectAtom(runtime => runtime.$chatSessionKind)
export const $pendingPromptBatch = projectAtom(runtime => runtime.$pendingPromptBatch)
export const $chatTurnInFlight = projectAtom(runtime => runtime.$chatTurnInFlight)
export const $lastEditableUserMessage = projectRead(runtime => runtime.$lastEditableUserMessage)
export const $retryableAssistantMessage = projectRead(runtime => runtime.$retryableAssistantMessage)
export const $turnHadBubbleBreak = projectAtom(runtime => runtime.$turnHadBubbleBreak)
export const $sessionSettings = projectAtom(runtime => runtime.$sessionSettings)
export const $sessionContextUsage = projectAtom(runtime => runtime.$sessionContextUsage)
export const retryAssistantReply = (
  ...args: Parameters<ConversationRuntime['retryAssistantReply']>
): ReturnType<ConversationRuntime['retryAssistantReply']> => activeConversationRuntime().retryAssistantReply(...args)
export const hydrateSessionSettings = (
  ...args: Parameters<ConversationRuntime['hydrateSessionSettings']>
): ReturnType<ConversationRuntime['hydrateSessionSettings']> =>
  activeConversationRuntime().hydrateSessionSettings(...args)

export function updateSessionSetting<K extends keyof SessionSettings>(key: K, value: SessionSettings[K]): void {
  activeConversationRuntime().updateSessionSetting(key, value)
}

export const setSessionContextUsage = (
  ...args: Parameters<ConversationRuntime['setSessionContextUsage']>
): ReturnType<ConversationRuntime['setSessionContextUsage']> =>
  activeConversationRuntime().setSessionContextUsage(...args)
export const resetSessionContextUsage = (
  ...args: Parameters<ConversationRuntime['resetSessionContextUsage']>
): ReturnType<ConversationRuntime['resetSessionContextUsage']> =>
  activeConversationRuntime().resetSessionContextUsage(...args)
export const hydrateEditedChatMessages = (
  ...args: Parameters<ConversationRuntime['hydrateEditedChatMessages']>
): ReturnType<ConversationRuntime['hydrateEditedChatMessages']> =>
  activeConversationRuntime().hydrateEditedChatMessages(...args)
export const hydrateChatMessages = (
  ...args: Parameters<ConversationRuntime['hydrateChatMessages']>
): ReturnType<ConversationRuntime['hydrateChatMessages']> => activeConversationRuntime().hydrateChatMessages(...args)
export const pushProactiveMessage = (
  ...args: Parameters<ConversationRuntime['pushProactiveMessage']>
): ReturnType<ConversationRuntime['pushProactiveMessage']> => activeConversationRuntime().pushProactiveMessage(...args)
export const pushMediaMessage = (
  ...args: Parameters<ConversationRuntime['pushMediaMessage']>
): ReturnType<ConversationRuntime['pushMediaMessage']> => activeConversationRuntime().pushMediaMessage(...args)
export const pushUserMessage = (
  ...args: Parameters<ConversationRuntime['pushUserMessage']>
): ReturnType<ConversationRuntime['pushUserMessage']> => activeConversationRuntime().pushUserMessage(...args)
export const bindTrailingUserMessageIds = (
  ...args: Parameters<ConversationRuntime['bindTrailingUserMessageIds']>
): ReturnType<ConversationRuntime['bindTrailingUserMessageIds']> =>
  activeConversationRuntime().bindTrailingUserMessageIds(...args)
export const bindTrailingAssistantMessageId = (
  ...args: Parameters<ConversationRuntime['bindTrailingAssistantMessageId']>
): ReturnType<ConversationRuntime['bindTrailingAssistantMessageId']> =>
  activeConversationRuntime().bindTrailingAssistantMessageId(...args)
export const pushStatusPill = (
  ...args: Parameters<ConversationRuntime['pushStatusPill']>
): ReturnType<ConversationRuntime['pushStatusPill']> => activeConversationRuntime().pushStatusPill(...args)
export const pushPendingPrompt = (
  ...args: Parameters<ConversationRuntime['pushPendingPrompt']>
): ReturnType<ConversationRuntime['pushPendingPrompt']> => activeConversationRuntime().pushPendingPrompt(...args)
export const clearPendingPrompts = (
  ...args: Parameters<ConversationRuntime['clearPendingPrompts']>
): ReturnType<ConversationRuntime['clearPendingPrompts']> => activeConversationRuntime().clearPendingPrompts(...args)
export const setTurnHadBubbleBreak = (
  ...args: Parameters<ConversationRuntime['setTurnHadBubbleBreak']>
): ReturnType<ConversationRuntime['setTurnHadBubbleBreak']> =>
  activeConversationRuntime().setTurnHadBubbleBreak(...args)
export const schedulePendingFlush = (
  ...args: Parameters<ConversationRuntime['schedulePendingFlush']>
): ReturnType<ConversationRuntime['schedulePendingFlush']> => activeConversationRuntime().schedulePendingFlush(...args)
export const cancelPendingFlush = (
  ...args: Parameters<ConversationRuntime['cancelPendingFlush']>
): ReturnType<ConversationRuntime['cancelPendingFlush']> => activeConversationRuntime().cancelPendingFlush(...args)
export const submitPendingBatch = (
  ...args: Parameters<ConversationRuntime['submitPendingBatch']>
): ReturnType<ConversationRuntime['submitPendingBatch']> => activeConversationRuntime().submitPendingBatch(...args)
export const beginAssistantMessage = (
  ...args: Parameters<ConversationRuntime['beginAssistantMessage']>
): ReturnType<ConversationRuntime['beginAssistantMessage']> =>
  activeConversationRuntime().beginAssistantMessage(...args)
export const appendAssistantDelta = (
  ...args: Parameters<ConversationRuntime['appendAssistantDelta']>
): ReturnType<ConversationRuntime['appendAssistantDelta']> => activeConversationRuntime().appendAssistantDelta(...args)
export const appendAssistantReasoningDelta = (
  ...args: Parameters<ConversationRuntime['appendAssistantReasoningDelta']>
): ReturnType<ConversationRuntime['appendAssistantReasoningDelta']> =>
  activeConversationRuntime().appendAssistantReasoningDelta(...args)
export const setAssistantTool = (
  ...args: Parameters<ConversationRuntime['setAssistantTool']>
): ReturnType<ConversationRuntime['setAssistantTool']> => activeConversationRuntime().setAssistantTool(...args)
export const finalizeAssistantMessage = (
  ...args: Parameters<ConversationRuntime['finalizeAssistantMessage']>
): ReturnType<ConversationRuntime['finalizeAssistantMessage']> =>
  activeConversationRuntime().finalizeAssistantMessage(...args)
export const finalizeCompanionReply = (
  ...args: Parameters<ConversationRuntime['finalizeCompanionReply']>
): ReturnType<ConversationRuntime['finalizeCompanionReply']> =>
  activeConversationRuntime().finalizeCompanionReply(...args)
export const updateMediaBubble = (
  ...args: Parameters<ConversationRuntime['updateMediaBubble']>
): ReturnType<ConversationRuntime['updateMediaBubble']> => activeConversationRuntime().updateMediaBubble(...args)
export const updateVoiceBubble = (
  ...args: Parameters<ConversationRuntime['updateVoiceBubble']>
): ReturnType<ConversationRuntime['updateVoiceBubble']> => activeConversationRuntime().updateVoiceBubble(...args)
export const forgetDeletedVoiceMessages = (
  ...args: Parameters<ConversationRuntime['forgetDeletedVoiceMessages']>
): ReturnType<ConversationRuntime['forgetDeletedVoiceMessages']> =>
  activeConversationRuntime().forgetDeletedVoiceMessages(...args)
export const markAssistantTerminal = (
  ...args: Parameters<ConversationRuntime['markAssistantTerminal']>
): ReturnType<ConversationRuntime['markAssistantTerminal']> =>
  activeConversationRuntime().markAssistantTerminal(...args)
export const resetChatMessages = (
  ...args: Parameters<ConversationRuntime['resetChatMessages']>
): ReturnType<ConversationRuntime['resetChatMessages']> => activeConversationRuntime().resetChatMessages(...args)

export function setChatSession(id: string | null): void {
  const previous = $chatSessionId.get()

  if (id !== previous) {
    conversationVoiceSink().cancel(previous, 'selection')
  }

  getConversationRuntime(id)
  $chatSessionId.set(id)
  persistString(CHAT_SESSION_ID_KEY, id)
  pruneConversationRuntimes()
}

registerStorageClearHandler(() => {
  for (const runtime of runtimes.values()) {
    runtime.cancelPendingFlush()
    runtime.clearPendingPrompts()
    runtime.resetChatMessages()
    runtime.dispose()
  }

  runtimes.clear()
  runtimeReferences.clear()
  $chatSessionId.set(null)
  $activeRuntime.set(getConversationRuntime($chatSessionId.get()))
})
registerStorageRestoreHandler(() => {
  $chatSessionId.set(storedString(CHAT_SESSION_ID_KEY))
})
