import type { IpcMain, WebContents } from 'electron'

import { IPC, type PresentationMode, type PresentationState, type StageActivity } from '@ipc/contracts'

import { registerDesktopPresentationIpc } from '../../ipc/desktop-presentation'
import { sendToWindow } from '../../shared/utils'
import type { createDesktopBackgroundRuntime } from '../desktop-background-runtime'
import type { createDesktopHealth } from '../desktop-health'
import type { createDesktopHost } from '../desktop-host'
import type { createDesktopWindows } from '../desktop-windows'

interface DesktopIpcRuntime {
  windows: ReturnType<typeof createDesktopWindows>
  background: ReturnType<typeof createDesktopBackgroundRuntime>
  health: ReturnType<typeof createDesktopHealth>
  native: ReturnType<typeof createDesktopHost>
  authenticated: () => boolean
  isSpriteSender: (sender: WebContents) => boolean
  hostReady: () => void | Promise<void>
  snapshot: () => PresentationState
  setMode: (mode: PresentationMode, sender?: WebContents) => Promise<PresentationState>
  setDisplay: (id: number, sender: WebContents) => Promise<PresentationState>
  setTrayLayout: (sender: WebContents, raw: unknown) => Promise<void>
  captureDesktopEligibility: (sender: WebContents, epoch?: number) => () => boolean
  assertDesktop: (sender: WebContents) => void
  assertSettings: (sender: WebContents) => void
  serial: <T>(task: () => Promise<T>) => Promise<T>
  isDisposed: () => boolean
  publish: () => void
  setStageActivity: (activity: StageActivity) => void
}

/** 桌面 IPC 注册表：只做通道到装配根命令的映射。 */
export function registerDesktopIpc(ipcMain: IpcMain, runtime: DesktopIpcRuntime): () => void {
  const { windows, background, health, native } = runtime

  return registerDesktopPresentationIpc(
    ipcMain,
    {
      isDesktopSender: windows.isDesktopSender,
      isSpriteSender: runtime.isSpriteSender,
      desktopWindow: sender => (windows.isDesktopSender(sender) ? windows.getInteractive() : null),
      backgroundWindow: windows.getBackgroundWindow,
      assertSettings: runtime.assertSettings,
      authenticated: runtime.authenticated
    },
    {
      getState: runtime.snapshot,
      setMode: runtime.setMode,
      setDisplay: runtime.setDisplay,
      setTrayLayout: runtime.setTrayLayout,
      requestAccessibility: async () => {
        native.requestAccessibility()
        runtime.publish()

        return runtime.snapshot()
      },
      reportReady: () => {
        const main = windows.getInteractive()

        if (main) {
          windows.reportReady(main)
        }

        health.heartbeat()
      },
      focus: (sender, epoch) => {
        const eligible = runtime.captureDesktopEligibility(sender, epoch)

        return runtime.serial(async () => {
          runtime.assertDesktop(sender)

          if (!eligible()) {
            return false
          }

          return native.focus(windows.getInteractive()!.getNativeWindowHandle(), eligible)
        })
      },
      heartbeat: health.heartbeat,
      hostReady: runtime.hostReady,
      setBackground: (sender, raw) => (runtime.isDisposed() ? Promise.resolve() : background.set(sender, raw)),
      backgroundReady: win => {
        sendToWindow(win, IPC.event.backgroundMedia, background.getState())
        windows.reportReady(win)
      },
      acknowledgeBackground: background.acknowledge,
      stageActivity: activity => {
        if (!runtime.isDisposed()) {
          runtime.setStageActivity(activity)
        }
      }
    }
  )
}
