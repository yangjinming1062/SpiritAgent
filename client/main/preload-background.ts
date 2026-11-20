import { contextBridge, ipcRenderer } from 'electron'

import { createBackgroundBridge } from '../shared/ipc/bridge'

contextBridge.exposeInMainWorld('desktopBackground', createBackgroundBridge(ipcRenderer))
