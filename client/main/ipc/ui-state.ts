import { randomUUID } from 'node:crypto'

import type { BrowserWindow, IpcMain, WebContents } from 'electron'

import {
  type AppNavigationTarget,
  IPC,
  isCompanionEditorUiSnapshot,
  isLifeUiSnapshot,
  isVisualsUiSnapshot,
  type MainUiStateData,
  type MainUiStateSnapshot,
  type MainUiStateUpdate,
  normalizeAppNavigation,
  type UiInputBusyState,
  type UiStateReply,
  type UiStateRequest
} from '@ipc/contracts'

interface UiStateOptions {
  accountId: () => string | null
  authIdentity: () => string | null
  isViewSender: (sender: WebContents) => boolean
  isInputSender: (sender: WebContents) => boolean
  isActiveSender: (sender: WebContents) => boolean
  onBusyChanged: () => void
}

interface WaitingReply {
  sender: WebContents
  authSessionId: string
  request: UiStateRequest
  resolve: () => void
  reject: (error: Error) => void
}

function stateData(raw: MainUiStateData): MainUiStateData {
  if (!isLifeUiSnapshot(raw?.life)) {
    throw new Error('生活页状态无效。')
  }

  const conversation = raw?.conversation

  if (
    !conversation ||
    !(conversation.selectedSessionId === null || typeof conversation.selectedSessionId === 'string') ||
    !Array.isArray(conversation.drafts) ||
    !conversation.drafts.every(
      draft =>
        draft &&
        (draft.sessionId === null || typeof draft.sessionId === 'string') &&
        typeof draft.text === 'string' &&
        Array.isArray(draft.externalPaths) &&
        draft.externalPaths.every(path => typeof path === 'string') &&
        (draft.pending === null || ['image', 'video', 'file', 'folder'].includes(draft.pending?.type)) &&
        (draft.editing === null || typeof draft.editing?.text === 'string') &&
        (draft.scrollAnchor === null || Number.isFinite(draft.scrollAnchor?.top))
    )
  ) {
    throw new Error('对话状态无效。')
  }

  const visuals = raw.visuals

  if (!isVisualsUiSnapshot(visuals)) {
    throw new Error('影像页状态无效。')
  }

  const companionEditors = raw.companionEditors

  if (!isCompanionEditorUiSnapshot(companionEditors)) {
    throw new Error('伙伴编辑状态无效。')
  }

  return structuredClone({
    navigation: normalizeAppNavigation(raw.navigation),
    life: raw.life,
    conversation,
    visuals,
    companionEditors
  })
}

// 仅在本机进程内保留账户视图；字节和文件授权仍由既有资源入口管理。
export function createUiStateBridge(options: UiStateOptions) {
  const states = new Map<string, MainUiStateSnapshot>()

  const inputs = new Map<
    WebContents,
    {
      authSessionId: string
      state: UiInputBusyState
      unbind: () => void
    }
  >()

  const waiting = new Map<string, WaitingReply>()
  let revision = 0
  let disposed = false
  let unregister: (() => void) | undefined
  let owner: { sender: WebContents; accountId: string; authSessionId: string } | null = null

  function releaseOwner(): void {
    if (owner) {
      owner.sender.removeListener('destroyed', releaseOwner)
      owner.sender.removeListener('render-process-gone', releaseOwner)
      owner = null
    }
  }

  function forgetInput(sender: WebContents): void {
    inputs.get(sender)?.unbind()
    inputs.delete(sender)
  }

  function clearInputs(): void {
    for (const sender of inputs.keys()) {
      forgetInput(sender)
    }
  }

  function isOwner(sender: WebContents): boolean {
    if (
      owner &&
      (owner.sender.isDestroyed() ||
        owner.accountId !== options.accountId() ||
        owner.authSessionId !== options.authIdentity())
    ) {
      releaseOwner()
    }

    return owner?.sender === sender && !sender.isDestroyed()
  }

  function assignOwner(sender: WebContents, authSessionId: string): void {
    const accountId = assertSession(sender, authSessionId)
    releaseOwner()
    owner = { sender, accountId, authSessionId }
    sender.once('destroyed', releaseOwner)
    sender.once('render-process-gone', releaseOwner)
  }

  function assertSession(sender: WebContents, authSessionId: string, input = false): string {
    const accountId = options.accountId()

    if (
      disposed ||
      sender.isDestroyed() ||
      !accountId ||
      options.authIdentity() !== authSessionId ||
      !(input ? options.isInputSender(sender) : options.isViewSender(sender))
    ) {
      throw new Error('当前界面不能访问此账户状态。')
    }

    return accountId
  }

  function write(sender: WebContents, update: MainUiStateUpdate): MainUiStateSnapshot {
    const accountId = assertSession(sender, update.authSessionId)

    const snapshot = {
      ...stateData(update),
      accountId,
      authSessionId: update.authSessionId,
      revision: ++revision
    }

    states.set(accountId, snapshot)

    return snapshot
  }

  function getSnapshot(): MainUiStateSnapshot | null {
    const accountId = options.accountId()
    const authSessionId = options.authIdentity()
    const saved = accountId ? states.get(accountId) : null

    return saved && authSessionId ? structuredClone({ ...saved, authSessionId }) : null
  }

  function request(win: BrowserWindow, message: UiStateRequest): Promise<void> {
    const authSessionId = options.authIdentity()

    if (
      !authSessionId ||
      message.authSessionId !== authSessionId ||
      win.isDestroyed() ||
      win.webContents.isDestroyed()
    ) {
      return Promise.reject(new Error('界面交接已失效。'))
    }

    const sender = win.webContents

    return new Promise<void>((resolve, reject) => {
      const onDestroyed = (): void => finish(new Error('界面交接期间窗口已关闭。'))
      const onRendererGone = (): void => finish(new Error('界面交接期间渲染进程已退出。'))
      const timer = setTimeout(() => finish(new Error('界面状态交接超时。')), 10000)
      sender.once('destroyed', onDestroyed)
      sender.once('render-process-gone', onRendererGone)

      const finish = (error?: Error): void => {
        clearTimeout(timer)
        sender.removeListener('destroyed', onDestroyed)
        sender.removeListener('render-process-gone', onRendererGone)
        waiting.delete(message.requestId)

        if (error) {
          reject(error)
        } else {
          resolve()
        }
      }

      waiting.set(message.requestId, {
        sender,
        authSessionId,
        request: message,
        resolve: () => finish(),
        reject: error => finish(error)
      })

      try {
        sender.send(IPC.event.uiStateRequest, message)
      } catch (error) {
        finish(error instanceof Error ? error : new Error('界面状态发送失败。'))
      }
    })
  }

  function blockedReason(): string | null {
    const activities: UiInputBusyState[] = []

    for (const [sender, input] of inputs) {
      if (sender.isDestroyed() || input.authSessionId !== options.authIdentity() || !options.isInputSender(sender)) {
        forgetInput(sender)
      } else {
        activities.push(input.state)
      }
    }

    return activities.some(state => state.recording)
      ? '请结束录音后切换运行模式。'
      : activities.some(state => state.transcribing)
        ? '语音正在转写，完成后可以切换运行模式。'
        : activities.some(state => state.uploading)
          ? '附件正在上传，完成或取消后可以切换运行模式。'
          : null
  }

  function register(ipcMain: IpcMain): () => void {
    ipcMain.handle(IPC.invoke.uiStateGet, (event, authSessionId: string) => {
      if (disposed) {
        return null
      }

      assertSession(event.sender, authSessionId)

      return getSnapshot()
    })
    ipcMain.handle(IPC.invoke.uiStateUpdate, (event, update: MainUiStateUpdate) => {
      if (disposed) {
        return
      }

      assertSession(event.sender, update?.authSessionId)

      if (isOwner(event.sender) && options.isActiveSender(event.sender)) {
        write(event.sender, update)
      }
    })
    ipcMain.handle(IPC.invoke.uiStateReady, (event, authSessionId: string, restoredRevision: number) => {
      if (disposed) {
        return false
      }

      const accountId = assertSession(event.sender, authSessionId)
      const savedRevision = states.get(accountId)?.revision ?? 0
      const ownsState = isOwner(event.sender)

      if (!options.isActiveSender(event.sender) || (owner && !ownsState) || restoredRevision !== savedRevision) {
        return false
      }

      assignOwner(event.sender, authSessionId)

      return true
    })
    ipcMain.handle(IPC.invoke.uiStateInputBusy, (event, authSessionId: string, raw: UiInputBusyState) => {
      if (disposed) {
        return
      }

      assertSession(event.sender, authSessionId, true)

      const state = {
        recording: raw?.recording === true,
        transcribing: raw?.transcribing === true,
        uploading: raw?.uploading === true
      }

      const sender = event.sender
      const existing = inputs.get(sender)

      if (existing) {
        existing.authSessionId = authSessionId
        existing.state = state
      } else {
        const onGone = (): void => {
          forgetInput(sender)
          options.onBusyChanged()
        }

        sender.once('destroyed', onGone)
        sender.once('render-process-gone', onGone)
        inputs.set(sender, {
          authSessionId,
          state,
          unbind: () => {
            sender.removeListener('destroyed', onGone)
            sender.removeListener('render-process-gone', onGone)
          }
        })
      }

      options.onBusyChanged()
    })
    ipcMain.handle(IPC.invoke.uiStateReply, (event, reply: UiStateReply) => {
      const pending = waiting.get(reply?.requestId)

      if (!pending || pending.sender !== event.sender) {
        return
      }

      try {
        assertSession(event.sender, reply.authSessionId)

        if (pending.authSessionId !== reply.authSessionId || reply.kind !== pending.request.kind) {
          throw new Error('界面交接账户已变化。')
        }

        if (reply.kind === 'capture') {
          if ('error' in reply) {
            throw new Error(reply.error)
          }

          if (!isOwner(event.sender)) {
            throw new Error('来源界面已失去状态交接资格。')
          }

          write(event.sender, { ...reply.data, authSessionId: reply.authSessionId })
        } else if (reply.error) {
          throw new Error(reply.error)
        }

        pending.resolve()
      } catch (error) {
        pending.reject(error instanceof Error ? error : new Error('界面交接失败。'))
      }
    })

    unregister = () => {
      for (const channel of [
        IPC.invoke.uiStateGet,
        IPC.invoke.uiStateUpdate,
        IPC.invoke.uiStateReady,
        IPC.invoke.uiStateInputBusy,
        IPC.invoke.uiStateReply
      ]) {
        ipcMain.removeHandler(channel)
      }
    }

    return unregister
  }

  function beginShutdown(): void {
    if (disposed) {
      return
    }

    disposed = true
    releaseOwner()

    for (const pending of waiting.values()) {
      pending.reject(new Error('应用正在退出。'))
    }

    states.clear()
    clearInputs()
  }

  return {
    register,
    blockedReason,
    getSnapshot,
    isOwner,
    releaseOwner,
    capture: async (win: BrowserWindow): Promise<MainUiStateSnapshot | null> => {
      if (!isOwner(win.webContents)) {
        throw new Error('当前界面状态尚未恢复，不能交接。')
      }

      await request(win, { kind: 'capture', requestId: randomUUID(), authSessionId: options.authIdentity() ?? '' })

      return getSnapshot()
    },
    restore: async (win: BrowserWindow, target?: AppNavigationTarget): Promise<void> => {
      const identity = options.authIdentity()

      if (!identity) {
        throw new Error('当前账户状态已失效。')
      }

      const snapshot = getSnapshot()

      if (snapshot) {
        const next = normalizeAppNavigation(target)

        if (next.page || next.sessionId) {
          const page = next.page ?? (next.sessionId ? 'chat' : snapshot.navigation.page)

          snapshot.navigation = {
            ...snapshot.navigation,
            page,
            section: next.section ?? snapshot.navigation.section,
            settingsTab: next.section ? next.settingsTab : snapshot.navigation.settingsTab,
            companionSection: next.companionSection ?? snapshot.navigation.companionSection
          }
          snapshot.conversation.selectedSessionId = next.sessionId ?? snapshot.conversation.selectedSessionId
        }

        await request(win, { kind: 'restore', requestId: randomUUID(), authSessionId: identity, snapshot })
      }

      if ((getSnapshot()?.revision ?? 0) !== (snapshot?.revision ?? 0)) {
        throw new Error('来源界面状态已更新，请重新切换运行模式。')
      }

      assignOwner(win.webContents, identity)
    },
    accountChanged: (): void => {
      releaseOwner()
      clearInputs()

      for (const pending of waiting.values()) {
        pending.reject(new Error('界面交接账户已变化。'))
      }
    },
    forgetAccount: (accountId: string): void => {
      states.delete(accountId)
    },
    beginShutdown,
    dispose: (): void => {
      beginShutdown()
      unregister?.()
    }
  }
}
