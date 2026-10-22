import { atom, computed } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { ipcErrorStatus } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { currentClearEpoch, persistString, registerStorageClearHandler, storedString } from '@/shared/lib/storage'
import { $locale } from '@/shared/store/locale'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { SessionInfo, SessionSnapshot, SystemPresetSummary, UndoResponse } from '@protocol'
import { errorMessage } from '@runtime'

import type { ConversationRuntime } from './chat-runtime'
import type { ConversationHistorySync } from './chat-runtime-types'
import {
  $chatDraftFromUndo,
  $chatSessionId,
  $companionSessionId,
  conversationRuntimes,
  findConversationRuntime,
  getConversationRuntime,
  retainConversationRuntime,
  setChatSession,
  setCompanionSessionId
} from './chat-store'
import { sessionDisplayTitle } from './preset-labels'
import {
  forgetSessionHistory,
  loadLocalSessionHistory,
  rememberFullHistory,
  requestSessionSnapshot,
  SessionHistoryChangedError,
  syncSessionHistory
} from './session-history-cache'
import { removeVoicePlayback } from './voice-playback'

export type SessionSort = 'created' | 'messages' | 'recent'

const SESSION_SORTS: readonly SessionSort[] = ['recent', 'created', 'messages']
const SESSION_SORT_KEY = 'da.companion.sessionSort'
export const TITLE_MAX_CHARS = 80

export function isCompanionSession(session: null | SessionInfo | undefined): boolean {
  return session?.system_preset_id === 'companion'
}

export const $sessions = atom<SessionInfo[]>([])
export const $sessionsLoading = atom(false)
export const $sessionsError = atom<string | null>(null)
export const $sessionNavigationError = atom<{ sessionId: string | null; error: string } | null>(null)
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
let presetsLoad: { epoch: number; promise: Promise<void> } | null = null
// 会话跳转（切换、打开主对话、新建、派生）共用：只有最后发起的跳转可以改写当前会话视图。
let navigationToken = 0

function parseSessionSort(raw: null | string): SessionSort {
  return SESSION_SORTS.find(sort => sort === raw) ?? 'recent'
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

export async function ensureChatSession(runtime?: ConversationRuntime): Promise<string> {
  const existing = runtime?.$chatSessionId.get() ?? $chatSessionId.get()

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
  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    return
  }

  const token = ++sessionsToken
  const isCurrent = (): boolean => token === sessionsToken && authCurrent()
  $sessionsLoading.set(true)
  $sessionsError.set(null)

  try {
    const res = await requestResource('sessions.list_sessions', { query: { order: $sessionSort.get() } })

    if (isCurrent()) {
      const sessions = res.sessions || []
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
    if (token === sessionsToken) {
      $sessionsLoading.set(false)
    }
  }
}

export async function fetchArchived(): Promise<void> {
  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    return
  }

  const token = ++archivedToken
  const isCurrent = (): boolean => token === archivedToken && authCurrent()
  $archivedLoading.set(true)

  try {
    const res = await requestResource('sessions.list_sessions', { query: { archived: 'only', limit: 100 } })

    if (isCurrent()) {
      $archivedSessions.set(res.sessions || [])
    }
  } catch (err) {
    if (isCurrent()) {
      log.error('session-list', 'Failed to fetch archived sessions:', err)
    }
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

  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    return
  }

  const token = ++searchToken
  const isCurrent = (): boolean => token === searchToken && authCurrent()
  $searchLoading.set(true)

  try {
    const res = await requestResource('sessions.list_sessions', { query: { q, archived: 'include' } })

    if (isCurrent()) {
      $searchResults.set(res.sessions || [])
    }
  } catch (err) {
    if (isCurrent()) {
      log.error('session-list', 'Failed to search sessions:', err)
    }
  } finally {
    if (token === searchToken) {
      $searchLoading.set(false)
    }
  }
}

type SessionPatchBody = { archived?: boolean; pinned?: boolean; title?: string }

async function patchSessionOrThrow(sessionId: string, body: SessionPatchBody): Promise<void> {
  await requestResource('sessions.patch_session', { path: { session_id: sessionId }, body })
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
  const patch = (list: SessionInfo[]): SessionInfo[] => {
    let found = false

    const next = list.map(s => {
      if (s.id !== sessionId) {
        return s
      }

      found = true

      return { ...s, title }
    })

    return found ? next : list
  }

  $sessions.set(patch($sessions.get()))
  $archivedSessions.set(patch($archivedSessions.get()))
  $searchResults.set(patch($searchResults.get()))
}

export async function renameSession(sessionId: string, title: string): Promise<void> {
  const epoch = currentClearEpoch()
  const next = title.trim().slice(0, TITLE_MAX_CHARS)
  const previous = findSessionInfo(sessionId)?.title ?? null

  if (!next || next === previous) {
    return
  }

  applyLocalTitle(sessionId, next)

  try {
    await patchSessionOrThrow(sessionId, { title: next })
  } catch (err) {
    if (epoch !== currentClearEpoch()) {
      return
    }

    applyLocalTitle(sessionId, previous)
    log.error('session-list', 'Failed to rename session:', err)
    notify({
      kind: 'error',
      message:
        ipcErrorStatus(err) === 403 ? getStrings().chat.sessionRename.forbidden : getStrings().chat.sessionRename.failed
    })

    return
  }

  if (epoch !== currentClearEpoch()) {
    return
  }

  void fetchSessions()

  if ($archivedSessions.get().some(s => s.id === sessionId)) {
    void fetchArchived()
  }
}

export async function pinSession(sessionId: string, pinned: boolean): Promise<void> {
  const epoch = currentClearEpoch()

  if (!(await patchSession(sessionId, { pinned }))) {
    return
  }

  if (epoch !== currentClearEpoch()) {
    return
  }

  // 刷新结果不含当前会话时沿用本地条目，置顶状态需先写入本地。
  $sessions.set($sessions.get().map(s => (s.id === sessionId ? { ...s, pinned } : s)))
  void fetchSessions()
}

export async function archiveSession(sessionId: string, archived: boolean): Promise<void> {
  const epoch = currentClearEpoch()

  if (!(await patchSession(sessionId, { archived }))) {
    return
  }

  if (epoch !== currentClearEpoch()) {
    return
  }

  // 归档的若是当前会话，切回主对话，避免聊天窗停在一个已收起的对话上。
  if (archived && $chatSessionId.get() === sessionId) {
    await openMainSession()
  }

  if (epoch !== currentClearEpoch()) {
    return
  }

  // 未能切回主对话时它仍是当前会话，刷新会沿用本地条目，归档时直接移除。
  if (archived) {
    $sessions.set($sessions.get().filter(s => s.id !== sessionId))
  }

  void fetchSessions()
  void fetchArchived()
}

export function onSessionListChanged(payload: { session_id?: string; deleted?: boolean }): void {
  if (payload.deleted && payload.session_id) {
    forgetSessionHistory(payload.session_id)
    removeVoicePlayback(payload.session_id)

    if ($chatSessionId.get() === payload.session_id) {
      setChatSession(null)
      void openMainSession()
    }
  }

  void fetchSessions()

  if ($archiveOpen.get()) {
    void fetchArchived()
  }

  if ($sessionSearch.get().trim()) {
    void runSessionSearch($sessionSearch.get())
  }
}

export async function createNewSession(systemPresetId: string): Promise<string | null> {
  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    return null
  }

  const token = ++navigationToken

  const isCurrentNavigation = (): boolean => token === navigationToken && authCurrent()

  try {
    const res = await requestResource('sessions.create_session', { body: { system_preset_id: systemPresetId } })

    if (!authCurrent()) {
      return null
    }

    if (token === navigationToken) {
      const runtime = getConversationRuntime(res.session_id)
      const snapshot = await requestSessionSnapshot(res.session_id)

      if (!isCurrentNavigation()) {
        return null
      }

      runtime.applyRemoteSnapshot(snapshot)
      setChatSession(res.session_id)
    }

    void fetchSessions()

    return res.session_id
  } catch (err) {
    log.error('session-list', 'Failed to create session:', err)

    return null
  }
}

/** 拉取系统预设元数据；同网关共用在途请求，已拉取过则跳过。 */
export function fetchSystemPresets(): Promise<void> {
  const authCurrent = captureAuthScope()
  const epoch = currentClearEpoch()

  if (!authCurrent || $systemPresetsFetched.get()) {
    return Promise.resolve()
  }

  if (presetsLoad?.epoch === epoch) {
    return presetsLoad.promise
  }

  const token = ++presetsToken
  const isCurrent = (): boolean => token === presetsToken && authCurrent()
  $systemPresetsLoading.set(true)

  const load = Promise.resolve()
    .then(() => requestResource('resource_catalog.get_presets'))
    .then(res => {
      if (isCurrent()) {
        $systemPresets.set(res.presets || [])
        $systemPresetsFetched.set(true)
      }
    })
    .catch(err => {
      if (isCurrent()) {
        log.error('session-list', 'Failed to fetch system presets:', err)
      }
    })
    .finally(() => {
      if (token === presetsToken) {
        $systemPresetsLoading.set(false)
      }

      if (presetsLoad?.promise === load) {
        presetsLoad = null
      }
    })

  presetsLoad = { epoch, promise: load }

  return load
}

// 派生资源不返回列表条目，先按派生规则用源会话信息补齐；列表刷新后以服务端条目为准，标题带副本后缀。
function forkSessionInfo(sourceSessionId: string, res: SessionSnapshot): SessionInfo {
  const source = findSessionInfo(sourceSessionId)
  const now = Date.now()

  return {
    archived: false,
    id: res.session_id,
    input_tokens: 0,
    kind: res.info?.kind ?? 'standard',
    last_active: now,
    message_count: res.message_count,
    output_tokens: 0,
    pinned: false,
    preview: null,
    started_at: now,
    system_preset_icon_key: source?.system_preset_icon_key ?? null,
    system_preset_id: source?.system_preset_id ?? null,
    title: source?.title ?? null,
    tool_call_count: 0
  }
}

/** 从源会话某条消息派生新会话并挂载 hydrate；发起后已换号或换网关返回 null，请求失败向上抛出。 */
export async function forkConversation(sourceSessionId: string, sourceMessageId: number): Promise<string | null> {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    throw new Error(getStrings().chat.fork.internalError)
  }

  const token = ++navigationToken

  try {
    const created = await requestResource('sessions.fork_session', {
      path: { session_id: sourceSessionId },
      body: { source_message_id: sourceMessageId }
    })

    if (!isCurrent()) {
      return null
    }

    const res = await requestSessionSnapshot(created.session_id)

    if (!isCurrent()) {
      return null
    }

    rememberFullHistory(res.session_id, res.messages, { info: res.info })
    // 先补入本地条目，派生对话立即显示来源标题；随后刷新列表对齐服务端。
    $sessions.set([forkSessionInfo(sourceSessionId, res), ...$sessions.get()])

    if (token === navigationToken) {
      getConversationRuntime(res.session_id).applyRemoteSnapshot(res)
      setChatSession(res.session_id)
    }

    void fetchSessions()

    return res.session_id
  } catch (err) {
    if (!isCurrent()) {
      return null
    }

    log.error('session-list', 'Failed to fork session:', err)

    throw err
  }
}

/** 撤回消息：同会话内硬删除 Message.id >= source_message_id 的全部行（含锚点），锚点载荷落回输入框作草稿；发起后已换号或换网关返回 null，请求失败向上抛出。 */
export async function undoToMessage(sessionId: string, sourceMessageId: number): Promise<UndoResponse | null> {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    throw new Error(getStrings().chat.undo.internalError)
  }

  try {
    const res = await requestResource('sessions.undo', {
      path: { session_id: sessionId },
      body: { source_message_id: sourceMessageId, confirmed: true }
    })

    if (!isCurrent()) {
      return null
    }

    if (res.anchor) {
      const attachments = res.anchor.attachments ?? []

      $chatDraftFromUndo.set({
        session_id: res.session_id,
        text: res.anchor.text ?? '',
        attachments
      })

      // 输入框一次只附加一张图片（见 use-chat-input 的草稿回填）；事件路径同样回填，提示只在发起窗口给一次。
      if (attachments.length > 1) {
        notify({ kind: 'info', message: getStrings().chat.undo.extraImagesNotRestored(attachments.length - 1) })
      }
    }

    if (Array.isArray(res.messages)) {
      rememberFullHistory(res.session_id, res.messages)

      // 已切到其他会话时只更新缓存，不改写当前视图。
      const runtime = findConversationRuntime(sessionId)

      if (runtime) {
        runtime.forgetDeletedVoiceMessages(res.messages)
        runtime.hydrateChatMessages(res.messages)
      }
    }

    return res
  } catch (err) {
    if (!isCurrent()) {
      return null
    }

    log.error('session-list', 'undoToMessage failed:', err)

    throw err
  }
}

let companionLoad: Promise<string | null> | null = null
let companionLoadEpoch = -1

// 固定轻语只水合陪伴runtime，不参与主对话的navigationToken。
export async function ensureCompanionSession(): Promise<string | null> {
  const authCurrent = captureAuthScope()
  const epoch = currentClearEpoch()

  if (!authCurrent) {
    return null
  }

  if (companionLoad && companionLoadEpoch === epoch) {
    return companionLoad
  }

  const load = (async () => {
    try {
      const knownId = $companionSessionId.get()
      const knownRuntime = knownId ? findConversationRuntime(knownId) : undefined
      const snapshot = knownRuntime?.captureHistorySync()
      const result = await resumeMainSession()

      if (!authCurrent()) {
        return null
      }

      const runtime = getConversationRuntime(result.session_id)
      mountMainSnapshot(result, knownRuntime === runtime ? snapshot : undefined, false)

      return result.session_id
    } catch (error) {
      if (epoch === currentClearEpoch()) {
        log.error('session-list', 'Failed to hydrate companion session:', error)
      }

      return null
    }
  })()

  companionLoad = load
  companionLoadEpoch = epoch

  try {
    return await load
  } finally {
    if (companionLoad === load) {
      companionLoad = null
      companionLoadEpoch = -1
    }
  }
}

// 挂载服务端快照：快照已挂上同一会话时不再重置，保留展示快照期间入列的待发消息；回合状态以服务端 running 为准。
function mountSyncedSession(result: SessionSnapshot, historySync?: ConversationHistorySync): boolean {
  if ($chatSessionId.get() !== result.session_id) {
    setChatSession(result.session_id)
  }

  const runtime = getConversationRuntime(result.session_id)
  const accepted = runtime.applyRemoteSnapshot(result, historySync)

  if (accepted) {
    void runtime.recoverUnconfirmedSubmission()
  }

  return accepted
}

// 主会话快照收尾：登记陪伴归属，select 时选中会话；接受后恢复在途提交并写历史缓存。
function mountMainSnapshot(
  res: SessionSnapshot,
  historySync: ConversationHistorySync | undefined,
  select: boolean
): void {
  setCompanionSessionId(res.session_id)
  const runtime = getConversationRuntime(res.session_id)

  if (select && $chatSessionId.get() !== res.session_id) {
    setChatSession(res.session_id)
  }

  const accepted = runtime.applyRemoteSnapshot(res, historySync)

  if (accepted) {
    void runtime.recoverUnconfirmedSubmission()
    rememberFullHistory(res.session_id, res.messages, { info: res.info })
  }
}

export async function switchSession(sessionId: string): Promise<void> {
  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    return
  }

  const token = ++navigationToken
  $sessionNavigationError.set(null)

  const isCurrent = (): boolean => token === navigationToken && authCurrent()

  const runtime = getConversationRuntime(sessionId)
  const release = retainConversationRuntime(runtime)
  const localSnapshot = runtime.captureHistorySync()

  try {
    const local = await loadLocalSessionHistory(sessionId)

    if (!isCurrent()) {
      return
    }

    if (runtime.$historyHydrated.get()) {
      setChatSession(sessionId)
    } else if (local?.info) {
      runtime.hydrateSyncedChatMessages(local.messages, local.info, localSnapshot)
      setChatSession(sessionId)
    }

    const snapshot = runtime.captureHistorySync()

    const synced = await syncSessionHistory({
      sessionId,
      request: () => requestSessionSnapshot(sessionId)
    })

    // 快速 A→B 切换时丢弃过期响应，避免旧会话写回覆盖新会话。
    if (!isCurrent()) {
      return
    }

    mountSyncedSession(synced, snapshot)
  } catch (err) {
    if (isCurrent()) {
      log.error('session-list', 'Failed to switch session:', err)
      $sessionNavigationError.set({ sessionId, error: errorMessage(err) })
    }
  } finally {
    release()
  }
}

// 模式交接须确认历史已恢复；普通列表切换仍保留原位错误提示的语义。
export async function restoreConversationSelection(sessionId: string): Promise<void> {
  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    throw new Error('Conversation restore requires an authenticated account')
  }

  const token = ++navigationToken
  const runtime = getConversationRuntime(sessionId)
  const release = retainConversationRuntime(runtime)

  const ownerCurrent = (): boolean => authCurrent() && token === navigationToken && runtime.isCurrent()

  const assertCurrent = (): void => {
    if (!ownerCurrent()) {
      throw new Error('Conversation restore owner changed')
    }
  }

  $sessionNavigationError.set(null)

  try {
    const localSnapshot = runtime.captureHistorySync()
    const local = await loadLocalSessionHistory(sessionId)
    assertCurrent()

    if (!runtime.$historyHydrated.get() && local?.info) {
      runtime.hydrateSyncedChatMessages(local.messages, local.info, localSnapshot)
    }

    setChatSession(sessionId)

    if (authCurrent()) {
      const snapshot = runtime.captureHistorySync()

      try {
        const synced = await syncSessionHistory({
          sessionId,
          request: () => requestSessionSnapshot(sessionId)
        })

        assertCurrent()

        if (synced.session_id !== sessionId) {
          throw new Error('Conversation restore returned a different session')
        }

        if (runtime.applyRemoteSnapshot(synced, snapshot)) {
          void runtime.recoverUnconfirmedSubmission()
        }
      } catch (error) {
        assertCurrent()
        $sessionNavigationError.set({ sessionId, error: errorMessage(error) })

        if (!runtime.$historyHydrated.get()) {
          throw error
        }
      }
    }

    assertCurrent()

    if ($chatSessionId.get() !== sessionId || !runtime.$historyHydrated.get()) {
      throw new Error('Conversation history could not be restored')
    }
  } finally {
    release()
  }
}

let openMainPromise: Promise<string | null> | null = null
let openMainToken = 0
let openMainEpoch = -1

// 挂载主会话并加载其对话流。被更晚的会话跳转取代后仍完成挂载与缓存，但不再改写当前视图。
export async function openMainSession(onMounted?: (res: SessionSnapshot) => void): Promise<string | null> {
  const authCurrent = captureAuthScope()
  const epoch = currentClearEpoch()

  if (!authCurrent) {
    return null
  }

  if (openMainPromise && openMainToken === navigationToken && openMainEpoch === epoch) {
    return openMainPromise
  }

  const token = ++navigationToken
  const isCurrent = authCurrent
  const isLatest = (): boolean => token === navigationToken
  $sessionNavigationError.set(null)

  const load = (async () => {
    try {
      // 已知陪伴会话 id 时走本地秒开 + 增量；未知（首装/清缓存）才 get_main 全量。
      const knownCompanionId = $companionSessionId.get()

      if (knownCompanionId) {
        const local = await loadLocalSessionHistory(knownCompanionId)

        if (!isCurrent()) {
          return null
        }

        if (local) {
          if (isLatest()) {
            const runtime = getConversationRuntime(knownCompanionId)

            if (!runtime.$historyHydrated.get()) {
              runtime.hydrateSyncedChatMessages(local.messages, local.info)
            }

            setChatSession(knownCompanionId)
          }

          try {
            const runtime = getConversationRuntime(knownCompanionId)
            const snapshot = runtime.captureHistorySync()

            const synced = await syncSessionHistory({
              sessionId: knownCompanionId,
              request: () => requestSessionSnapshot(knownCompanionId)
            })

            if (!isCurrent()) {
              return null
            }

            // 同步期间已切到其他会话时不覆盖其视图。
            if (isLatest() && $chatSessionId.get() === knownCompanionId) {
              mountSyncedSession(synced, snapshot)
            }

            onMounted?.(synced)

            return knownCompanionId
          } catch (error) {
            if (!isCurrent()) {
              return null
            }

            if (error instanceof SessionHistoryChangedError) {
              log.warn('session-list', 'History is changing; keeping current session:', error)

              return knownCompanionId
            }

            // 增量失败回源读取主会话并完整挂载。
          }
        }
      }

      const snapshots = new Map(
        conversationRuntimes().map(runtime => [runtime.$chatSessionId.get(), runtime.captureHistorySync()])
      )

      const res = await resumeMainSession()

      if (!isCurrent()) {
        return null
      }

      mountMainSnapshot(res, snapshots.get(res.session_id), isLatest())

      onMounted?.(res)

      return res.session_id
    } catch (err) {
      if (isCurrent()) {
        log.error('session-list', 'Failed to open main session:', err)

        if (isLatest()) {
          $sessionNavigationError.set({ sessionId: $companionSessionId.get(), error: errorMessage(err) })
        }
      }

      return null
    }
  })()

  openMainPromise = load
  openMainEpoch = epoch
  openMainToken = token

  try {
    return await load
  } finally {
    if (openMainPromise === load) {
      openMainPromise = null
      openMainEpoch = -1
    }
  }
}

registerStorageClearHandler(() => {
  sessionsToken++
  archivedToken++
  searchToken++
  presetsToken++
  presetsLoad = null
  navigationToken++
  openMainPromise = null
  openMainEpoch = -1
  companionLoad = null
  companionLoadEpoch = -1
  $sessions.set([])
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

export async function deleteSession(sessionId: string): Promise<boolean> {
  const epoch = currentClearEpoch()

  try {
    await requestResource('sessions.delete_session', { path: { session_id: sessionId } })
  } catch (err) {
    log.error('session-list', 'Failed to delete session:', err)

    return false
  }

  if (epoch !== currentClearEpoch()) {
    return false
  }

  forgetSessionHistory(sessionId)
  removeVoicePlayback(sessionId)

  if ($chatSessionId.get() === sessionId) {
    const companionId = $companionSessionId.get()

    if (companionId && companionId !== sessionId) {
      setChatSession(companionId)
      await switchSession(companionId)
    } else {
      setChatSession(null)
      await openMainSession()
    }
  }

  if (epoch !== currentClearEpoch()) {
    return false
  }

  // 删除条目不随列表刷新保留。
  $sessions.set($sessions.get().filter(s => s.id !== sessionId))
  void fetchSessions()
  void fetchArchived()

  return true
}

async function resumeMainSession(): Promise<SessionSnapshot> {
  const authCurrent = captureAuthScope()
  const resource = await requestResource('sessions.get_main_session')

  if (!authCurrent?.()) {
    throw new Error('Account changed')
  }

  return requestSessionSnapshot(resource.session_id)
}
