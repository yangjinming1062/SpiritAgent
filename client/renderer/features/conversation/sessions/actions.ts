import { captureAuthScope } from '@/shared/lib/authed-api'
import { ipcErrorStatus } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { currentClearEpoch } from '@/shared/lib/storage'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { SessionInfo, SessionSnapshot, UndoResponse } from '@protocol'

import {
  $chatDraftFromUndo,
  $chatSessionId,
  $companionSessionId,
  findConversationRuntime,
  getConversationRuntime,
  setChatSession
} from '../chat-store'
import {
  forgetSessionHistory,
  rememberFullHistory,
  requestSessionSnapshot
} from '../session-history-cache'
import { removeVoicePlayback } from '../voice-playback'

import { fetchArchived, fetchSessions } from './list'
import { openMainSession, switchSession } from './selection'
import {
  $archivedSessions,
  $searchResults,
  $sessions,
  findSessionInfo,
  sessionTokens,
  TITLE_MAX_CHARS
} from './state'

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

export async function createNewSession(systemPresetId: string): Promise<string | null> {
  const authCurrent = captureAuthScope()

  if (!authCurrent) {
    return null
  }

  const token = ++sessionTokens.navigation

  const isCurrentNavigation = (): boolean => token === sessionTokens.navigation && authCurrent()

  try {
    const res = await requestResource('sessions.create_session', { body: { system_preset_id: systemPresetId } })

    if (!authCurrent()) {
      return null
    }

    if (token === sessionTokens.navigation) {
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

// 派生资源不返回列表条目，先按派生规则用源会话信息补齐；列表刷新后以服务端条目为准，标题带副本后缀。
function forkSessionInfo(sourceSessionId: string, res: SessionSnapshot): SessionInfo {
  const source = findSessionInfo(sourceSessionId)
  const now = Date.now()

  return {
    archived: false,
    id: res.session_id,
    kind: res.info?.kind ?? 'standard',
    last_active: now,
    message_count: res.message_count,
    pinned: false,
    preview: null,
    started_at: now,
    system_preset_icon_key: source?.system_preset_icon_key ?? null,
    system_preset_id: source?.system_preset_id ?? null,
    title: source?.title ?? null
  }
}

/** 从源会话某条消息派生新会话并挂载 hydrate；发起后已换号或换网关返回 null，请求失败向上抛出。 */
export async function forkConversation(sourceSessionId: string, sourceMessageId: string): Promise<string | null> {
  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    throw new Error(getStrings().chat.fork.internalError)
  }

  const token = ++sessionTokens.navigation

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

    if (token === sessionTokens.navigation) {
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
export async function undoToMessage(sessionId: string, sourceMessageId: string): Promise<UndoResponse | null> {
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
