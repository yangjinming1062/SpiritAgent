import { atom } from 'nanostores'

import {
  persistString,
  registerStorageClearHandler,
  registerStorageRestoreHandler,
  storedString
} from '@/shared/lib/storage'

import { type ConversationRuntime, createConversationRuntime } from './chat-runtime'
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
} from './chat-runtime-types'
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

    // 同步重挂载会重新 retain；等本轮 effect 清理和登记完成后再释放 runtime。
    queueMicrotask(pruneConversationRuntimes)
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
  for (const runtime of runtimes.values()) {
    if (runtime.$chatMessageBodies.get()[messageId] !== undefined) {
      return runtime
    }
  }

  return undefined
}

// 选择与账户恢复只登记会话；展示和异步操作直接持有所属 runtime。
getConversationRuntime($chatSessionId.get())
$chatSessionId.listen(sessionId => {
  getConversationRuntime(sessionId)
})

export function setChatSession(id: string | null): void {
  const previous = $chatSessionId.get()

  if (id !== previous) {
    conversationVoiceSink().cancel(previous, 'selection')
    runtimes.get(previous)?.setSessionCacheUsage()
    runtimes.get(id)?.setSessionCacheUsage()
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
  getConversationRuntime($chatSessionId.get())
})
registerStorageRestoreHandler(() => {
  $chatSessionId.set(storedString(CHAT_SESSION_ID_KEY))
})
