import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'

import { type DesktopBackground, type DesktopBackgroundPlayback, IPC } from '@ipc/contracts'

import type { DesktopBackgroundBridge } from '../shared/ipc/bridge'

const backgroundBridge = {
  ready: () => ipcRenderer.invoke(IPC.invoke.backgroundReady),
  acknowledge: (playback: DesktopBackgroundPlayback) => ipcRenderer.invoke(IPC.invoke.backgroundAcknowledge, playback),
  onMedia: (callback: (background: DesktopBackground) => void): (() => void) => {
    const listener = (_event: IpcRendererEvent, background: DesktopBackground): void => callback(background)
    ipcRenderer.on(IPC.event.backgroundMedia, listener)

    return () => ipcRenderer.removeListener(IPC.event.backgroundMedia, listener)
  }
} satisfies DesktopBackgroundBridge

contextBridge.exposeInMainWorld('desktopBackground', backgroundBridge)
