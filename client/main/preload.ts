import { contextBridge, ipcRenderer, webUtils } from 'electron'

import { IPC } from '@ipc/contracts'

import { createDesktopBridge } from '../shared/ipc/bridge'

async function getDroppedPaths(files: File[]): Promise<string[]> {
  const paths: string[] = []

  for (const file of files) {
    try {
      const path = webUtils.getPathForFile(file)

      if (path) {
        paths.push(path)
      }
    } catch {
      // 单个文件无法解析不影响同批的其他文件。
    }
  }

  if (paths.length) {
    await ipcRenderer.invoke(IPC.invoke.registerUserSelectedPaths, paths)
  }

  return paths
}

contextBridge.exposeInMainWorld('spiritagent', createDesktopBridge(ipcRenderer, process.platform, getDroppedPaths))
