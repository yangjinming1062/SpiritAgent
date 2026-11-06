import { RENDERER_LIMITS } from '@/shared/constants'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { persistString, registerStorageClearHandler } from '@/shared/lib/storage'
import { errorMessage } from '@runtime'

import { $chatSessionId, setCompanionSessionId } from '../chat-store'

import {
  $archivedLoading,
  $archivedNextCursor,
  $archivedSessions,
  $searchLoading,
  $searchNextCursor,
  $searchResults,
  $sessions,
  $sessionsError,
  $sessionsLoading,
  $sessionsNextCursor,
  $sessionSort,
  isCompanionSession,
  SESSION_SORT_KEY,
  type SessionSort,
  sessionTokens
} from './state'

let searchCursorQuery = ''

export function setSessionSort(sort: SessionSort): void {
  if (sort === $sessionSort.get()) {
    return
  }

  $sessionSort.set(sort)
  persistString(SESSION_SORT_KEY, sort)
  void fetchSessions()
}

export async function fetchSessions(append = false): Promise<void> {
  const cursor = append ? $sessionsNextCursor.get() : null

  if (append && (!cursor || $sessionsLoading.get())) {
    return
  }

  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    return
  }

  const token = ++sessionTokens.sessions
  const isCurrent = (): boolean => token === sessionTokens.sessions && authCurrent()
  $sessionsLoading.set(true)
  $sessionsError.set(null)

  try {
    const res = await requestResource('sessions.list_sessions', { query: { order: $sessionSort.get(), cursor } })

    if (isCurrent()) {
      const incomingIds = new Set(res.sessions.map(session => session.id))

      const sessions = append
        ? [...$sessions.get().filter(session => !incomingIds.has(session.id)), ...res.sessions]
        : res.sessions

      $sessionsNextCursor.set(res.next_cursor)
      // 列表只返回一页；当前会话不在其中时沿用本地条目，避免专业对话因找不到当前会话而切走。
      const activeId = $chatSessionId.get()
      const active = sessions.some(s => s.id === activeId) ? undefined : $sessions.get().find(s => s.id === activeId)
      $sessions.set(active ? [...sessions, active] : sessions)
      const companion = sessions.find(session => session.kind === 'special' && isCompanionSession(session))

      if (companion) {
        setCompanionSessionId(companion.id)
      }
    }
  } catch (err) {
    if (isCurrent()) {
      log.error('session-list', 'Failed to fetch sessions:', err)
      $sessionsError.set(errorMessage(err))
    }
  } finally {
    if (token === sessionTokens.sessions) {
      $sessionsLoading.set(false)
    }
  }
}

export async function fetchArchived(append = false): Promise<void> {
  const cursor = append ? $archivedNextCursor.get() : null

  if (append && (!cursor || $archivedLoading.get())) {
    return
  }

  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    return
  }

  const token = ++sessionTokens.archived
  const isCurrent = (): boolean => token === sessionTokens.archived && authCurrent()
  $archivedLoading.set(true)

  try {
    const res = await requestResource('sessions.list_sessions', {
      query: { archived: 'only', limit: RENDERER_LIMITS.sessionPageSize, cursor }
    })

    if (isCurrent()) {
      const incomingIds = new Set(res.sessions.map(session => session.id))
      $archivedSessions.set(
        append
          ? [...$archivedSessions.get().filter(session => !incomingIds.has(session.id)), ...res.sessions]
          : res.sessions
      )
      $archivedNextCursor.set(res.next_cursor)
    }
  } catch (err) {
    if (isCurrent()) {
      log.error('session-list', 'Failed to fetch archived sessions:', err)
    }
  } finally {
    if (token === sessionTokens.archived) {
      $archivedLoading.set(false)
    }
  }
}

export async function runSessionSearch(query: string, append = false): Promise<void> {
  const q = query.trim()
  const cursor = append ? $searchNextCursor.get() : null

  if (append && (!cursor || $searchLoading.get() || q !== searchCursorQuery)) {
    return
  }

  if (!q) {
    sessionTokens.search++
    $searchResults.set([])
    $searchNextCursor.set(null)
    searchCursorQuery = ''
    $searchLoading.set(false)

    return
  }

  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    return
  }

  const token = ++sessionTokens.search
  const isCurrent = (): boolean => token === sessionTokens.search && authCurrent()
  $searchLoading.set(true)

  try {
    const res = await requestResource('sessions.list_sessions', { query: { q, archived: 'include', cursor } })

    if (isCurrent()) {
      const incomingIds = new Set(res.sessions.map(session => session.id))
      $searchResults.set(
        append
          ? [...$searchResults.get().filter(session => !incomingIds.has(session.id)), ...res.sessions]
          : res.sessions
      )
      $searchNextCursor.set(res.next_cursor)
      searchCursorQuery = q
    }
  } catch (err) {
    if (isCurrent()) {
      log.error('session-list', 'Failed to search sessions:', err)
    }
  } finally {
    if (token === sessionTokens.search) {
      $searchLoading.set(false)
    }
  }
}

registerStorageClearHandler(() => {
  searchCursorQuery = ''
})
