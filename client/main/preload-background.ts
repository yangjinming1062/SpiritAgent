import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'

import { type DesktopBackground, type DesktopBackgroundPlayback, IPC } from '@ipc/contracts'

contextBridge.exposeInMainWorld('desktopBackground', {
  ready: () => ipcRenderer.invoke(IPC.invoke.backgroundReady),
  acknowledge: (playback: DesktopBackgroundPlayback) => ipcRenderer.invoke(IPC.invoke.backgroundAcknowledge, playback),
  onMedia: (callback: (background: DesktopBackground) => void): (() => void) => {
    const listener = (_event: IpcRendererEvent, background: DesktopBackground): void => callback(background)
    ipcRenderer.on(IPC.event.backgroundMedia, listener)

    return () => ipcRenderer.removeListener(IPC.event.backgroundMedia, listener)
  }
})
