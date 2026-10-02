import { atom } from 'nanostores'

import {
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  registerStorageRestoreHandler,
  storedString
} from '@/shared/lib/storage'

import type { ChatUndoDraft, PendingExternalAttachment, ProactiveBubbleState } from './chat-runtime'

export const DEFAULT_CONTEXT_LIMIT = 1_000_000
export const CHAT_SESSION_ID_KEY = registerCompanionStorageKey('da.companion.chatSessionId')
const COMPANION_SESSION_ID_KEY = registerCompanionStorageKey('da.companion.companionSessionId')
export const FLUSH_DEBOUNCE_MS = 4000

let idCounter = 0
export const nextChatMessageId = (): string => `m${++idCounter}`

let bubbleTimer: ReturnType<typeof setTimeout> | null = null
let bubbleGeneration = 0
// 主会话加载、列表与主动投递共同校准陪伴归属。
export const $companionSessionId = atom<string | null>(storedString(COMPANION_SESSION_ID_KEY))

export function setCompanionSessionId(id: string): void {
  $companionSessionId.set(id)
  persistString(COMPANION_SESSION_ID_KEY, id)
}

// 撤回落草稿总线：undo 成功后由 session-list-store 写入；多窗口订阅需按 session_id 过滤，避免 A 撤回落到 B 的输入框。
export const $chatDraftFromUndo = atom<ChatUndoDraft | null>(null)

export const $proactiveBubble = atom<ProactiveBubbleState | null>(null)

let externalNonce = 0

export const $pendingExternalAttachment = atom<PendingExternalAttachment | null>(null)

export function pushExternalAttachment(paths: string[]): void {
  $pendingExternalAttachment.set({ paths, nonce: ++externalNonce })
}

export function clearExternalAttachment(): void {
  $pendingExternalAttachment.set(null)
}

export function setProactiveBubble(state: ProactiveBubbleState | null, lingerMs?: number): void {
  if (bubbleTimer) {
    clearTimeout(bubbleTimer)
    bubbleTimer = null
  }

  $proactiveBubble.set(state)

  if (state && lingerMs != null && lingerMs > 0) {
    const gen = ++bubbleGeneration

    bubbleTimer = setTimeout(() => {
      // 连续主动消息/媒体提示时，只清理自己这一代的气泡。
      if (gen === bubbleGeneration) {
        $proactiveBubble.set(null)
        bubbleTimer = null
      }
    }, lingerMs)
  }
}

export function showMediaHint(text: string, sessionId?: string): void {
  setProactiveBubble(sessionId ? { text, sessionId } : { text }, 8000)
}

registerStorageClearHandler(() => {
  setProactiveBubble(null)
  $companionSessionId.set(null)
  $pendingExternalAttachment.set(null)
  $chatDraftFromUndo.set(null)
})
registerStorageRestoreHandler(() => {
  $companionSessionId.set(storedString(COMPANION_SESSION_ID_KEY))
})
