import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'

import { type DesktopBackground, IPC } from '@ipc/contracts'

contextBridge.exposeInMainWorld('desktopBackground', {
  ready: () => ipcRenderer.invoke(IPC.invoke.backgroundReady),
  onImage: (callback: (background: DesktopBackground) => void): (() => void) => {
    const listener = (_event: IpcRendererEvent, background: DesktopBackground): void => callback(background)
    ipcRenderer.on(IPC.event.backgroundImage, listener)

    return () => ipcRenderer.removeListener(IPC.event.backgroundImage, listener)
  }
})
