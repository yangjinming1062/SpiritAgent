import { atom, computed } from 'nanostores'

import { registerStorageClearHandler, storedString } from '@/shared/lib/storage'
import { $locale } from '@/shared/store/locale'
import { getStrings } from '@/shared/strings'
import type { SessionInfo, SystemPresetSummary } from '@protocol'

import { $chatSessionId, $companionSessionId } from '../chat-store'
import { sessionDisplayTitle } from '../preset-labels'

export type SessionSort = 'created' | 'messages' | 'recent'

const SESSION_SORTS: readonly SessionSort[] = ['recent', 'created', 'messages']
export const SESSION_SORT_KEY = 'da.companion.sessionSort'
export const TITLE_MAX_CHARS = 80

/** 各查询与导航各自的代次；迟到回包按代次丢弃，避免慢响应覆盖更新的结果。 */
export const sessionTokens = { archived: 0, navigation: 0, presets: 0, search: 0, sessions: 0 }

export function isCompanionSession(session: null | SessionInfo | undefined): boolean {
  return session?.system_preset_id === 'companion'
}

export const $sessions = atom<SessionInfo[]>([])
export const $sessionsLoading = atom(false)
export const $sessionsNextCursor = atom<string | null>(null)
export const $sessionsError = atom<string | null>(null)
export const $sessionNavigationError = atom<{ sessionId: string | null; error: string } | null>(null)
export const $sessionSort = atom<SessionSort>(parseSessionSort(storedString(SESSION_SORT_KEY)))
export const $sessionSearch = atom('')
export const $searchResults = atom<SessionInfo[]>([])
export const $searchLoading = atom(false)
export const $searchNextCursor = atom<string | null>(null)
export const $archivedSessions = atom<SessionInfo[]>([])
export const $archivedLoading = atom(false)
export const $archivedNextCursor = atom<string | null>(null)
export const $archiveOpen = atom(false)

// 系统预设元数据（不含 body，body 永远不下发到客户端）。预设体变更需后端重启，所以进程内缓存足够。
export const $systemPresets = atom<SystemPresetSummary[]>([])
export const $systemPresetsLoading = atom(false)
export const $systemPresetsFetched = atom(false)

function parseSessionSort(raw: null | string): SessionSort {
  return SESSION_SORTS.find(sort => sort === raw) ?? 'recent'
}

// 内部查询兜底（供 renameSession 等命令式操作回滚用）。
export function findSessionInfo(sessionId: string): SessionInfo | undefined {
  return (
    $sessions.get().find(s => s.id === sessionId) ??
    $archivedSessions.get().find(s => s.id === sessionId) ??
    $searchResults.get().find(s => s.id === sessionId)
  )
}

function findCurrentSession(): SessionInfo | undefined {
  const id = $chatSessionId.get()

  if (!id) {
    return undefined
  }

  return findSessionInfo(id)
}

export const $currentSessionTitle = computed(
  [$chatSessionId, $companionSessionId, $sessions, $archivedSessions, $searchResults, $locale],
  () => {
    const info = findCurrentSession()
    const dict = getStrings()
    const sessionId = $chatSessionId.get()

    return (
      (info && sessionDisplayTitle(dict, info)) ||
      (sessionId && sessionId === $companionSessionId.get()
        ? dict.conversation.sessionSidebar.systemSessionFallback
        : dict.chat.defaultSessionTitle)
    )
  }
)

registerStorageClearHandler(() => {
  sessionTokens.sessions++
  sessionTokens.archived++
  sessionTokens.search++
  sessionTokens.presets++
  sessionTokens.navigation++
  $sessions.set([])
  $sessionsNextCursor.set(null)
  $searchNextCursor.set(null)
  $archivedNextCursor.set(null)
  $sessionsLoading.set(false)
  $sessionsError.set(null)
  $sessionNavigationError.set(null)
  $sessionSort.set('recent')
  $sessionSearch.set('')
  $archivedSessions.set([])
  $archivedLoading.set(false)
  $archiveOpen.set(false)
  $searchResults.set([])
  $searchLoading.set(false)
  $systemPresets.set([])
  $systemPresetsFetched.set(false)
  $systemPresetsLoading.set(false)
})
