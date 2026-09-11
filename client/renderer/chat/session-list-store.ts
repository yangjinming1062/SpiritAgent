import { atom, computed } from 'nanostores'

import {
  $chatDraftFromUndo,
  $chatSessionId,
  hydrateChatMessages,
  hydrateSessionSettings,
  resetChatMessages,
  resetSessionContextUsage,
  setChatSession
} from '@/chat/chat-store'
import { unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { persistString, registerStorageClearHandler, storedString } from '@/shared/lib/storage'
import { $gateway } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type {
  SessionInfo,
  SessionResumeResponse,
  SystemPresetListResponse,
  SystemPresetSummary,
  UndoResponse
} from '@/shared/types/spiritagent'

export type SessionSort = 'created' | 'messages' | 'recent'

const SESSION_SORTS: readonly SessionSort[] = ['recent', 'created', 'messages']
const SESSION_SORT_KEY = 'da.companion.sessionSort'
export const TITLE_MAX_CHARS = 80

export function isCompanionSession(session: null | SessionInfo | undefined): boolean {
  if (!session) {
    return false
  }

  return session.system_preset_id === 'companion' || session.kind === 'companion'
}

export const $companionSessionId = atom<string | null>(null)
export const $sessions = atom<SessionInfo[]>([])
export const $sessionsLoading = atom(false)
export const $sessionSort = atom<SessionSort>(parseSessionSort(storedString(SESSION_SORT_KEY)))
export const $sessionSearch = atom('')
export const $searchResults = atom<SessionInfo[]>([])
export const $searchLoading = atom(false)
export const $archivedSessions = atom<SessionInfo[]>([])
export const $archivedLoading = atom(false)
export const $archiveOpen = atom(false)

// 系统预设元数据（不含 body，body 永远不下发到客户端）。预设体变更需后端重启，所以进程内缓存足够。
export const $systemPresets = atom<SystemPresetSummary[]>([])
export const $systemPresetsLoading = atom(false)
export const $systemPresetsFetched = atom(false)

// 每个 fetch 系列各自自增，避免慢响应覆盖更新的结果。
let sessionsToken = 0
let archivedToken = 0
let searchToken = 0
let presetsToken = 0

function parseSessionSort(raw: null | string): SessionSort {
  return SESSION_SORTS.includes(raw as SessionSort) ? (raw as SessionSort) : 'recent'
}

// 内部查询兜底（供 renameSession 等命令式操作回滚用）。
function findSessionInfo(sessionId: string): SessionInfo | undefined {
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

  return (
    $sessions.get().find(s => s.id === id) ??
    $archivedSessions.get().find(s => s.id === id) ??
    $searchResults.get().find(s => s.id === id)
  )
}

export const $currentSessionTitle = computed([$chatSessionId, $sessions, $archivedSessions, $searchResults], () => {
  const info = findCurrentSession()

  return (info?.title && info.title.trim()) || getStrings().chat.defaultSessionTitle
})

export const $currentSessionKind = computed(
  [$chatSessionId, $sessions, $archivedSessions, $searchResults],
  () => findCurrentSession()?.kind ?? ''
)

export async function ensureChatSession(): Promise<string> {
  const existing = $chatSessionId.get()

  if (existing) {
    return existing
  }

  const sessionId = await openMainSession()

  if (!sessionId) {
    throw new Error(getStrings().chat.openMainSessionFailed)
  }

  return sessionId
}

export function setSessionSort(sort: SessionSort): void {
  if (sort === $sessionSort.get()) {
    return
  }

  $sessionSort.set(sort)
  persistString(SESSION_SORT_KEY, sort)
  void fetchSessions()
}

export async function fetchSessions(): Promise<void> {
  const token = ++sessionsToken
  $sessionsLoading.set(true)

  try {
    const res = await window.spiritagent.api<{ sessions: SessionInfo[] }>({
      path: `/api/sessions?order=${$sessionSort.get()}`
    })

    if (token === sessionsToken) {
      $sessions.set(res.sessions || [])
      const companion = (res.sessions || []).find(isCompanionSession)

      if (companion) {
        $companionSessionId.set(companion.id)
      }
    }
  } catch (err) {
    log.error('session-list', 'Failed to fetch sessions:', err)
  } finally {
    if (token === sessionsToken) {
      $sessionsLoading.set(false)
    }
  }
}

export async function fetchArchived(): Promise<void> {
  const token = ++archivedToken
  $archivedLoading.set(true)

  try {
    const res = await window.spiritagent.api<{ sessions: SessionInfo[] }>({
      path: '/api/sessions?archived=only&limit=100'
    })

    if (token === archivedToken) {
      $archivedSessions.set(res.sessions || [])
    }
  } catch (err) {
    log.error('session-list', 'Failed to fetch archived sessions:', err)
  } finally {
    if (token === archivedToken) {
      $archivedLoading.set(false)
    }
  }
}

export async function runSessionSearch(query: string): Promise<void> {
  const q = query.trim()

  if (!q) {
    searchToken++
    $searchResults.set([])
    $searchLoading.set(false)

    return
  }

  const token = ++searchToken
  $searchLoading.set(true)

  try {
    const res = await window.spiritagent.api<{ sessions: SessionInfo[] }>({
      path: `/api/sessions/search?q=${encodeURIComponent(q)}&archived=include`
    })

    if (token === searchToken) {
      $searchResults.set(res.sessions || [])
    }
  } catch (err) {
    log.error('session-list', 'Failed to search sessions:', err)
  } finally {
    if (token === searchToken) {
      $searchLoading.set(false)
    }
  }
}

type SessionPatchBody = { archived?: boolean; pinned?: boolean; title?: string }

async function patchSessionOrThrow(sessionId: string, body: SessionPatchBody): Promise<void> {
  await window.spiritagent.api({ body, method: 'PATCH', path: `/api/sessions/${sessionId}` })
}

async function patchSession(sessionId: string, body: SessionPatchBody): Promise<boolean> {
  try {
    await patchSessionOrThrow(sessionId, body)

    return true
  } catch (err) {
    log.error('session-list', 'Failed to patch session:', err)

    return false
  }
}

function applyLocalTitle(sessionId: string, title: null | string): void {
  const patch = (list: SessionInfo[]): SessionInfo[] =>
    list.some(s => s.id === sessionId) ? list.map(s => (s.id === sessionId ? { ...s, title } : s)) : list

  $sessions.set(patch($sessions.get()))
  $archivedSessions.set(patch($archivedSessions.get()))
  $searchResults.set(patch($searchResults.get()))
}

export async function renameSession(sessionId: string, title: string): Promise<void> {
  const next = title.trim().slice(0, TITLE_MAX_CHARS)
  const previous = findSessionInfo(sessionId)?.title ?? null

  if (!next || next === previous) {
    return
  }

  applyLocalTitle(sessionId, next)

  try {
    await patchSessionOrThrow(sessionId, { title: next })
  } catch (err) {
    applyLocalTitle(sessionId, previous)
    log.error('session-list', 'Failed to rename session:', err)
    notify({
      kind: 'error',
      message: unwrapIpcErrorMessage(err).startsWith('403 ')
        ? getStrings().chat.sessionRename.forbidden
        : getStrings().chat.sessionRename.failed
    })

    return
  }

  void fetchSessions()

  if ($archivedSessions.get().some(s => s.id === sessionId)) {
    void fetchArchived()
  }
}

export async function pinSession(sessionId: string, pinned: boolean): Promise<void> {
  if (!(await patchSession(sessionId, { pinned }))) {
    return
  }

  void fetchSessions()
}

export async function archiveSession(sessionId: string, archived: boolean): Promise<void> {
  if (!(await patchSession(sessionId, { archived }))) {
    return
  }

  // 归档的若是当前会话，切回主对话，避免聊天窗停在一个已收起的对话上。
  if (archived && $chatSessionId.get() === sessionId) {
    await openMainSession()
  }

  void fetchSessions()
  void fetchArchived()
}

export async function createNewSession(systemPresetId?: string | null): Promise<string | null> {
  const gw = $gateway.get()

  if (!gw) {
    return null
  }

  try {
    const params: Record<string, unknown> = {}

    if (systemPresetId) {
      params.system_preset_id = systemPresetId
    }

    const res = await gw.request<{ session_id: string; info?: SessionResumeResponse['info'] }>('session.create', params)
    setChatSession(res.session_id)
    resetChatMessages()

    if (res.info) {
      hydrateSessionSettings(res.info)
    }

    resetSessionContextUsage(res.info?.context_window)
    void fetchSessions()

    return res.session_id
  } catch (err) {
    log.error('session-list', 'Failed to create session:', err)

    return null
  }
}

/** 拉取系统预设元数据；force=true 用于预设变更（需后端重启）后的强制重拉。 */
export async function fetchSystemPresets(force = false): Promise<void> {
  const gw = $gateway.get()

  if (!gw) {
    return
  }

  if (!force && $systemPresetsFetched.get()) {
    return
  }

  const token = ++presetsToken
  $systemPresetsLoading.set(true)

  try {
    const res = await gw.request<SystemPresetListResponse>('system.list_presets', {})

    if (token === presetsToken) {
      $systemPresets.set(res.presets || [])
      $systemPresetsFetched.set(true)
    }
  } catch (err) {
    log.error('session-list', 'Failed to fetch system presets:', err)
  } finally {
    if (token === presetsToken) {
      $systemPresetsLoading.set(false)
    }
  }
}

/** 从源会话的某条消息派生新会话：调用 session.fork RPC，命中后立即自动挂载新会话并 hydrate 历史。失败返回 null。 */
export async function forkConversation(sourceSessionId: string, sourceMessageId: number): Promise<string | null> {
  const gw = $gateway.get()

  if (!gw) {
    return null
  }

  try {
    const res = await gw.request<SessionResumeResponse>('session.fork', {
      source_session_id: sourceSessionId,
      source_message_id: sourceMessageId
    })

    // 与 switchSession 同一形态：先 setChatSession 清残留状态 + 持久化新 id，再 hydrate 灌消息流
    setChatSession(res.session_id)
    hydrateChatMessages(res.messages || [], res.info)

    resetSessionContextUsage(res.info?.context_window)
    // 刷新抽屉让新会话出现在列表（默认按 parent_id 隐藏，开 include_subagents 才能看到）
    void fetchSessions()

    return res.session_id
  } catch (err) {
    log.error('session-list', 'Failed to fork session:', err)

    return null
  }
}

/** 撤回消息：在同一会话内硬删除 ``Message.id >= source_message_id`` 的全部行（含锚点本身），并把锚点载荷落回输入框作为草稿。失败返回 null，错误已记录日志。 */
export async function undoToMessage(sessionId: string, sourceMessageId: number): Promise<UndoResponse | null> {
  const gw = $gateway.get()

  if (!gw) {
    return null
  }

  try {
    const res = await gw.request<UndoResponse>('session.undo_to_message', {
      session_id: sessionId,
      source_message_id: sourceMessageId,
      confirmed: true
    })

    if (res.anchor) {
      $chatDraftFromUndo.set({
        session_id: res.session_id,
        text: res.anchor.text ?? '',
        content_type: res.anchor.content_type ?? 'text',
        media_json: res.anchor.media_json ?? null
      })
    }

    if (Array.isArray(res.messages)) {
      hydrateChatMessages(res.messages)
    }

    return res
  } catch (err) {
    log.error('session-list', 'undoToMessage failed:', err)

    return null
  }
}

let switchSessionToken = 0

export async function switchSession(sessionId: string): Promise<void> {
  const gw = $gateway.get()

  if (!gw) {
    return
  }

  const token = ++switchSessionToken

  try {
    const res = await gw.request<SessionResumeResponse>('session.resume', { session_id: sessionId })

    // 快速 A→B 切换时丢弃过期响应，避免旧会话写回覆盖新会话。
    if (token !== switchSessionToken) {
      return
    }

    setChatSession(sessionId)
    hydrateChatMessages(res.messages || [], res.info)
  } catch (err) {
    if (token === switchSessionToken) {
      log.error('session-list', 'Failed to switch session:', err)
    }
  }
}

let openMainPromise: Promise<string | null> | null = null

// 挂载主会话并加载其对话流。
export async function openMainSession(onMounted?: (res: SessionResumeResponse) => void): Promise<string | null> {
  if (openMainPromise) {
    return openMainPromise
  }

  const gw = $gateway.get()

  if (!gw) {
    return null
  }

  openMainPromise = (async () => {
    try {
      const res = await gw.request<SessionResumeResponse>('session.get_main')
      $companionSessionId.set(res.session_id)
      setChatSession(res.session_id)
      hydrateChatMessages(res.messages || [], res.info)
      onMounted?.(res)

      return res.session_id
    } catch (err) {
      log.error('session-list', 'Failed to open main session:', err)

      return null
    } finally {
      openMainPromise = null
    }
  })()

  return openMainPromise
}

registerStorageClearHandler(() => {
  $companionSessionId.set(null)
  $sessions.set([])
  $archivedSessions.set([])
  $searchResults.set([])
})

export async function deleteSession(sessionId: string): Promise<void> {
  try {
    await window.spiritagent.api({ method: 'DELETE', path: `/api/sessions/${sessionId}` })
  } catch (err) {
    log.error('session-list', 'Failed to delete session:', err)

    return
  }

  if ($chatSessionId.get() === sessionId) {
    await openMainSession()
  }

  void fetchSessions()
  void fetchArchived()
}
