import fs from 'node:fs'
import path from 'node:path'

import { IPC } from '@ipc/contracts'
import type { App, IpcMain } from 'electron'

import { dataUrlFromBuffer } from '../shared/mime'

const TAG_RE = /^onboarding\.[a-z0-9.]+$/
export const MAX_BYTES = 256 * 1024

interface OnboardingAudioIpcDeps {
  app?: null | Partial<App>
  appRoot?: string
  spiritagentHome?: null | string
  devAudioRoot?: string
  hardening: {
    resolveReadableFileForIpc: (
      filePath: string,
      options?: { maxBytes?: number; purpose?: string }
    ) => Promise<{ resolvedPath: string; stat: fs.Stats }>
  }
  ipcMain: IpcMain
  mimeTypeForPath: (filePath: string) => string
}

export function registerOnboardingAudioIpc({
  app,
  appRoot,
  spiritagentHome,
  devAudioRoot: explicitDevAudioRoot,
  hardening,
  ipcMain,
  mimeTypeForPath
}: OnboardingAudioIpcDeps): void {
  if (!hardening) {
    throw new Error('registerOnboardingAudioIpc: hardening is required')
  }

  if (!spiritagentHome) {
    throw new Error('registerOnboardingAudioIpc: spiritagentHome is required')
  }

  if (typeof mimeTypeForPath !== 'function') {
    throw new Error('registerOnboardingAudioIpc: mimeTypeForPath is required')
  }

  const audioRoot = path.resolve(spiritagentHome, 'audio', 'onboarding', 'zh')

  let devAudioRoot = explicitDevAudioRoot

  if (!devAudioRoot) {
    const baseAppPath = appRoot || (typeof app?.getAppPath === 'function' ? app.getAppPath() : process.cwd())
    // 如果 baseAppPath 是 client/ 或 client/dist-electron，则解析到仓库根目录
    let repoRoot = baseAppPath

    if (path.basename(repoRoot) === 'dist-electron') {
      repoRoot = path.dirname(repoRoot)
    }

    if (path.basename(repoRoot) === 'client') {
      repoRoot = path.dirname(repoRoot)
    }

    devAudioRoot = path.resolve(repoRoot, 'installer/payload/onboarding-audio/zh')
  }

  ipcMain.handle(IPC.invoke.onboardingAudioRead, async (_event, tag: string) => {
    if (typeof tag !== 'string' || !TAG_RE.test(tag)) {
      throw new Error(`invalid onboarding audio tag: ${tag}`)
    }

    let targetPath = path.join(audioRoot, `${tag}.mp3`)

    if (!fs.existsSync(targetPath) && devAudioRoot) {
      const devPath = path.join(devAudioRoot, `${tag}.mp3`)

      if (fs.existsSync(devPath)) {
        targetPath = devPath
      }
    }

    const { resolvedPath } = await hardening.resolveReadableFileForIpc(targetPath, {
      maxBytes: MAX_BYTES,
      purpose: 'Onboarding audio'
    })

    const data = await fs.promises.readFile(resolvedPath)
    const mimeType = mimeTypeForPath(resolvedPath)

    return { bytes: data.length, dataUrl: dataUrlFromBuffer(data, mimeType), mimeType, tag }
  })
}
