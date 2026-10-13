import { type BrowserWindow, type IpcMain, screen, type WebContents } from 'electron'

import {
  DESKTOP_COMPANION_ACTIVITY_PRIORITY,
  type DesktopCompanionActivityState,
  IPC,
  type PresentationMode,
  type PresentationState,
  type StageActivity
} from '@ipc/contracts'

interface PresentationAccess {
  isDesktopSender: (sender: WebContents) => boolean
  isSpriteSender: (sender: WebContents) => boolean
  desktopWindow: (sender: WebContents) => BrowserWindow | null
  backgroundWindow: (sender: WebContents) => BrowserWindow | undefined
  assertSettings: (sender: WebContents) => void
  authenticated: () => boolean
  authIdentity: () => string | null
}

interface PresentationOperations {
  getState: () => PresentationState
  setMode: (mode: PresentationMode, sender: WebContents) => Promise<PresentationState>
  setDisplay: (id: number, sender: WebContents) => Promise<PresentationState>
  companionActivity: (source: 'host' | 'desktop', state: DesktopCompanionActivityState, voicePreparing: boolean) => void
  reportReady: () => void
  focus: (sender: WebContents, epoch: number) => Promise<boolean>
  heartbeat: () => void
  hostReady: () => void | Promise<void>
  setBackground: (sender: WebContents, raw: unknown) => Promise<void>
  backgroundReady: (win: BrowserWindow) => void
  acknowledgeBackground: (raw: unknown) => void
  stageActivity: (activity: StageActivity) => void
}

export function registerDesktopPresentationIpc(
  ipcMain: IpcMain,
  access: PresentationAccess,
  operations: PresentationOperations
): () => void {
  const channels: string[] = []

  const register: IpcMain['handle'] = (channel, handler) => {
    ipcMain.handle(channel, handler)
    channels.push(channel)
  }

  const assertDesktop = (sender: WebContents): void => {
    if (!access.isDesktopSender(sender)) {
      throw new Error('仅桌面入口允许此操作。')
    }
  }

  register(IPC.invoke.presentationCompanionActivity, (event, raw: unknown) => {
    const source = access.isDesktopSender(event.sender)
      ? 'desktop'
      : access.isSpriteSender(event.sender)
        ? 'host'
        : null

    const activity = raw as { state?: unknown; voicePreparing?: unknown; authSessionId?: unknown } | null

    if (
      !source ||
      typeof activity?.state !== 'string' ||
      !Object.hasOwn(DESKTOP_COMPANION_ACTIVITY_PRIORITY, activity.state) ||
      typeof activity.voicePreparing !== 'boolean'
    ) {
      throw new Error('无效伙伴活动。')
    }

    if (access.authenticated() && activity.authSessionId === access.authIdentity()) {
      operations.companionActivity(source, activity.state as DesktopCompanionActivityState, activity.voicePreparing)
    }
  })
  register(IPC.invoke.presentationSetIgnoreMouseEvents, (event, raw: unknown) => {
    const win = access.desktopWindow(event.sender)
    const value = raw as { ignore?: unknown; forward?: unknown } | null

    if (
      !win ||
      typeof value?.ignore !== 'boolean' ||
      (value.forward !== undefined && typeof value.forward !== 'boolean')
    ) {
      throw new Error('无效桌面鼠标捕获。')
    }

    win.setIgnoreMouseEvents(value.ignore, { forward: value.forward === true })
  })
  register(IPC.invoke.presentationGetState, operations.getState)
  register(IPC.invoke.presentationSetMode, (event, raw: unknown) => {
    access.assertSettings(event.sender)

    if (raw !== 'desktop' && raw !== 'window') {
      throw new Error('无效呈现方式。')
    }

    return operations.setMode(raw, event.sender)
  })
  register(IPC.invoke.presentationSetDisplay, (event, id: unknown) => {
    access.assertSettings(event.sender)

    if (typeof id !== 'number' || !screen.getAllDisplays().some(display => display.id === id)) {
      throw new Error('显示器不可用。')
    }

    return operations.setDisplay(id, event.sender)
  })
  register(IPC.invoke.presentationReportReady, event => {
    if (!access.isDesktopSender(event.sender)) {
      throw new Error('无效桌面窗口。')
    }

    operations.reportReady()
  })
  register(IPC.invoke.presentationFocus, (event, epoch: unknown) => {
    assertDesktop(event.sender)

    if (typeof epoch !== 'number' || !Number.isSafeInteger(epoch)) {
      throw new Error('无效桌面代次。')
    }

    return operations.focus(event.sender, epoch)
  })
  register(IPC.invoke.presentationHeartbeat, event => {
    if (!access.isDesktopSender(event.sender)) {
      throw new Error('无效桌面心跳。')
    }

    operations.heartbeat()
  })
  register(IPC.invoke.presentationHostReady, event => {
    if (!access.isSpriteSender(event.sender)) {
      throw new Error('仅精灵宿主允许恢复桌面。')
    }

    return operations.hostReady()
  })
  register(IPC.invoke.presentationSetBackground, (event, raw: unknown) => operations.setBackground(event.sender, raw))
  register(IPC.invoke.backgroundReady, event => {
    const win = access.backgroundWindow(event.sender)

    if (!win) {
      throw new Error('无效背景窗口。')
    }

    operations.backgroundReady(win)
  })
  register(IPC.invoke.backgroundAcknowledge, (event, raw: unknown) => {
    if (!access.backgroundWindow(event.sender)) {
      throw new Error('无效背景窗口。')
    }

    operations.acknowledgeBackground(raw)
  })
  register(IPC.invoke.presentationStageActivity, (event, activity: StageActivity) => {
    if (!access.isSpriteSender(event.sender)) {
      throw new Error('仅精灵宿主允许上报活动。')
    }

    if (
      !activity ||
      typeof activity.locked !== 'boolean' ||
      typeof activity.idleSeconds !== 'number' ||
      !Number.isFinite(activity.idleSeconds) ||
      !['autonomous', 'normal', 'still'].includes(activity.effectiveTier)
    ) {
      throw new Error('无效活动。')
    }

    operations.stageActivity(activity)
  })

  return () => {
    for (const channel of channels) {
      ipcMain.removeHandler(channel)
    }
  }
}
