import { atom, computed } from 'nanostores'

import { ipcErrorStatus } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, persistString, registerStorageClearHandler, storedString } from '@/shared/lib/storage'
import { $gateway, type SpiritAgentGatewayLike } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type {
  SessionInfo,
  SessionResumeResponse,
  SystemPresetListResponse,
  SystemPresetSummary,
  UndoResponse
} from '@protocol'

import type { ConversationHistorySync, ConversationRuntime } from './chat-runtime'
import {
  $chatDraftFromUndo,
  $chatSessionId,
  $companionSessionId,
  conversationRuntimes,
  findConversationRuntime,
  getConversationRuntime,
  hydrateChatMessages,
  hydrateSessionSettings,
  resetChatMessages,
  resetSessionContextUsage,
  retainConversationRuntime,
  setChatSession,
  setCompanionSessionId
} from './chat-store'
import { sessionDisplayTitle } from './preset-labels'
import {
  forgetSessionHistory,
  loadLocalSessionHistory,
  rememberFullHistory,
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
let presetsLoad: { gateway: SpiritAgentGatewayLike; epoch: number; promise: Promise<void> } | null = null
// 会话跳转（切换、打开主对话、新建、派生）共用：只有最后发起的跳转可以改写当前会话视图。
let navigationToken = 0

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

  return findSessionInfo(id)
}

// 固定预设会话按当前界面语言显示预设名，界面语言变化时随之更新。
export const $currentSessionTitle = computed(
  [$chatSessionId, $sessions, $archivedSessions, $searchResults, $systemPresets, $locale],
  () => {
    const info = findCurrentSession()
    const dict = getStrings()

    return (info && sessionDisplayTitle(dict, info, $systemPresets.get())) || dict.chat.defaultSessionTitle
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
  const token = ++sessionsToken
  $sessionsLoading.set(true)

  try {
    const res = await window.spiritagent.api<{ sessions: SessionInfo[] }>({
      path: `/api/sessions?order=${$sessionSort.get()}`
    })

    if (token === sessionsToken) {
      const sessions = res.sessions || []
      // 列表只返回一页；当前会话不在其中时沿用本地条目，避免工作台因找不到当前会话而切走。
      const activeId = $chatSessionId.get()
      const active = sessions.some(s => s.id === activeId) ? undefined : $sessions.get().find(s => s.id === activeId)
      $sessions.set(active ? [...sessions, active] : sessions)
      const companion = sessions.find(session => session.kind === 'special' && isCompanionSession(session))

      if (companion) {
        setCompanionSessionId(companion.id)
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
  const epoch = currentClearEpoch()
  const gw = $gateway.get()

  if (!gw) {
    return null
  }

  const token = ++navigationToken

  try {
    const res = await gw.request<{ session_id: string; info?: SessionResumeResponse['info'] }>('session.create', {
      system_preset_id: systemPresetId
    })

    if (epoch !== currentClearEpoch() || $gateway.get() !== gw) {
      return null
    }

    if (token === navigationToken) {
      setChatSession(res.session_id)
      resetChatMessages()

      if (res.info) {
        hydrateSessionSettings(res.info)
      }

      resetSessionContextUsage(res.info?.context_window)
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
  const gw = $gateway.get()
  const epoch = currentClearEpoch()

  if (!gw || $systemPresetsFetched.get()) {
    return Promise.resolve()
  }

  if (presetsLoad?.gateway === gw && presetsLoad.epoch === epoch) {
    return presetsLoad.promise
  }

  const token = ++presetsToken
  const isCurrent = (): boolean => token === presetsToken && epoch === currentClearEpoch() && $gateway.get() === gw
  $systemPresetsLoading.set(true)

  const load = Promise.resolve()
    .then(() => gw.request<SystemPresetListResponse>('system.list_presets', {}))
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

  presetsLoad = { gateway: gw, epoch, promise: load }

  return load
}

// session.fork 不返回列表条目，先按派生规则用源会话信息补齐；列表刷新后以服务端条目为准，标题带副本后缀。
function forkSessionInfo(sourceSessionId: string, res: SessionResumeResponse): SessionInfo {
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

/** 从源会话某条消息派生新会话（session.fork）并挂载 hydrate；发起后已换号或换网关返回 null，请求失败向上抛出。 */
export async function forkConversation(sourceSessionId: string, sourceMessageId: number): Promise<string | null> {
  const epoch = currentClearEpoch()
  const gw = $gateway.get()

  if (!gw) {
    throw new Error(getStrings().chat.fork.internalError)
  }

  const token = ++navigationToken
  const isCurrent = (): boolean => epoch === currentClearEpoch() && $gateway.get() === gw

  try {
    const res = await gw.request<SessionResumeResponse>('session.fork', {
      source_session_id: sourceSessionId,
      source_message_id: sourceMessageId
    })

    if (!isCurrent()) {
      return null
    }

    rememberFullHistory(res.session_id, res.messages || [], {
      currentSeq: res.current_seq,
      info: res.info,
      nextCursor: res.next_cursor,
      truncated: res.truncated
    })
    // 先补入本地条目，工作台才能立即挂载对话面板而不切走；随后刷新列表对齐服务端。
    $sessions.set([forkSessionInfo(sourceSessionId, res), ...$sessions.get()])

    if (token === navigationToken) {
      setChatSession(res.session_id)
      hydrateChatMessages(res.messages || [], res.info)
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
  const epoch = currentClearEpoch()
  const gw = $gateway.get()

  if (!gw) {
    throw new Error(getStrings().chat.undo.internalError)
  }

  const isCurrent = (): boolean => epoch === currentClearEpoch() && $gateway.get() === gw

  try {
    const res = await gw.request<UndoResponse>('session.undo_to_message', {
      session_id: sessionId,
      source_message_id: sourceMessageId,
      confirmed: true
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
let companionLoadGateway: SpiritAgentGatewayLike | null = null
let companionLoadEpoch = -1

// 固定轻语只水合陪伴runtime，不参与主对话的navigationToken。
export async function ensureCompanionSession(): Promise<string | null> {
  const gateway = $gateway.get()
  const epoch = currentClearEpoch()

  if (!gateway) {
    return null
  }

  if (companionLoad && companionLoadGateway === gateway && companionLoadEpoch === epoch) {
    return companionLoad
  }

  const load = (async () => {
    try {
      const knownId = $companionSessionId.get()
      const knownRuntime = knownId ? findConversationRuntime(knownId) : undefined
      const snapshot = knownRuntime?.captureHistorySync()
      const result = await gateway.request<SessionResumeResponse>('session.get_main')

      if (epoch !== currentClearEpoch() || $gateway.get() !== gateway) {
        return null
      }

      setCompanionSessionId(result.session_id)
      const runtime = getConversationRuntime(result.session_id)

      const accepted = runtime.applyRemoteSnapshot(result, knownRuntime === runtime ? snapshot : undefined)

      if (accepted) {
        void runtime.recoverUnconfirmedSubmission()
        rememberFullHistory(result.session_id, result.messages || [], {
          currentSeq: result.current_seq,
          info: result.info,
          nextCursor: result.next_cursor,
          truncated: result.truncated
        })
      }

      return result.session_id
    } catch (error) {
      if (epoch === currentClearEpoch()) {
        log.error('session-list', 'Failed to hydrate companion session:', error)
      }

      return null
    }
  })()

  companionLoad = load
  companionLoadGateway = gateway
  companionLoadEpoch = epoch

  try {
    return await load
  } finally {
    if (companionLoad === load) {
      companionLoad = null
      companionLoadGateway = null
      companionLoadEpoch = -1
    }
  }
}

// 挂载服务端快照：快照已挂上同一会话时不再重置，保留展示快照期间入列的待发消息；回合状态以服务端 running 为准。
function mountSyncedSession(result: SessionResumeResponse, historySync?: ConversationHistorySync): boolean {
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

export async function switchSession(sessionId: string): Promise<void> {
  const gw = $gateway.get()

  if (!gw) {
    return
  }

  const token = ++navigationToken
  const epoch = currentClearEpoch()
  const isCurrent = (): boolean => token === navigationToken && epoch === currentClearEpoch() && $gateway.get() === gw
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
    } else if (local) {
      setChatSession(sessionId)
      runtime.hydrateSyncedChatMessages(local.messages, local.info, localSnapshot)
    }

    const snapshot = runtime.captureHistorySync()

    const synced = await syncSessionHistory({
      sessionId,
      request: body => gw.request<SessionResumeResponse>('session.resume', { session_id: sessionId, ...body })
    })

    // 快速 A→B 切换时丢弃过期响应，避免旧会话写回覆盖新会话；未传活水位 last_seq，服务端只走增量或全量。
    if (!isCurrent()) {
      return
    }

    mountSyncedSession(synced, snapshot)
  } catch (err) {
    if (isCurrent()) {
      log.error('session-list', 'Failed to switch session:', err)
    }
  } finally {
    release()
  }
}

let openMainPromise: Promise<string | null> | null = null
let openMainToken = 0
let openMainGateway: SpiritAgentGatewayLike | null = null
let openMainEpoch = -1

// 挂载主会话并加载其对话流。被更晚的会话跳转取代后仍完成挂载与缓存，但不再改写当前视图。
export async function openMainSession(onMounted?: (res: SessionResumeResponse) => void): Promise<string | null> {
  const gw = $gateway.get()
  const epoch = currentClearEpoch()

  if (!gw) {
    return null
  }

  if (openMainPromise && openMainToken === navigationToken && openMainGateway === gw && openMainEpoch === epoch) {
    return openMainPromise
  }

  const token = ++navigationToken
  const isCurrent = (): boolean => epoch === currentClearEpoch() && $gateway.get() === gw
  const isLatest = (): boolean => token === navigationToken

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
            setChatSession(knownCompanionId)
            const runtime = getConversationRuntime(knownCompanionId)

            if (!runtime.$historyHydrated.get()) {
              runtime.hydrateSyncedChatMessages(local.messages, local.info)
            }
          }

          try {
            const runtime = getConversationRuntime(knownCompanionId)
            const snapshot = runtime.captureHistorySync()

            const synced = await syncSessionHistory({
              sessionId: knownCompanionId,
              request: body =>
                gw.request<SessionResumeResponse>('session.resume', { session_id: knownCompanionId, ...body })
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

            // 增量失败回落 get_main 全量挂载。
          }
        }
      }

      const snapshots = new Map(
        conversationRuntimes().map(runtime => [runtime.$chatSessionId.get(), runtime.captureHistorySync()])
      )

      const res = await gw.request<SessionResumeResponse>('session.get_main')

      if (!isCurrent()) {
        return null
      }

      setCompanionSessionId(res.session_id)

      const runtime = getConversationRuntime(res.session_id)

      const accepted = isLatest()
        ? mountSyncedSession(res, snapshots.get(res.session_id))
        : runtime.applyRemoteSnapshot(res, snapshots.get(res.session_id))

      if (accepted) {
        void runtime.recoverUnconfirmedSubmission()
        rememberFullHistory(res.session_id, res.messages || [], {
          currentSeq: res.current_seq,
          info: res.info,
          nextCursor: res.next_cursor,
          truncated: res.truncated
        })
      }

      onMounted?.(res)

      return res.session_id
    } catch (err) {
      if (isCurrent()) {
        log.error('session-list', 'Failed to open main session:', err)
      }

      return null
    }
  })()

  openMainPromise = load
  openMainGateway = gw
  openMainEpoch = epoch
  openMainToken = token

  try {
    return await load
  } finally {
    if (openMainPromise === load) {
      openMainPromise = null
      openMainGateway = null
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
  openMainGateway = null
  openMainEpoch = -1
  companionLoad = null
  companionLoadGateway = null
  companionLoadEpoch = -1
  $sessions.set([])
  $sessionsLoading.set(false)
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

export async function deleteSession(sessionId: string): Promise<void> {
  const epoch = currentClearEpoch()

  try {
    await window.spiritagent.api({ method: 'DELETE', path: `/api/sessions/${sessionId}` })
  } catch (err) {
    log.error('session-list', 'Failed to delete session:', err)

    return
  }

  if (epoch !== currentClearEpoch()) {
    return
  }

  forgetSessionHistory(sessionId)
  removeVoicePlayback(sessionId)

  if ($chatSessionId.get() === sessionId) {
    await openMainSession()
  }

  if (epoch !== currentClearEpoch()) {
    return
  }

  // 未能切回主对话时它仍是当前会话，刷新会沿用本地条目，删除时直接移除。
  $sessions.set($sessions.get().filter(s => s.id !== sessionId))
  void fetchSessions()
  void fetchArchived()
}
