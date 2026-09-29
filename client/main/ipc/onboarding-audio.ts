import fs from 'node:fs'
import path from 'node:path'

import { IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import { dataUrlFromBuffer } from '../shared/mime'

const TAG_RE = /^onboarding\.[a-z0-9.]+$/
const MAX_BYTES = 256 * 1024

interface OnboardingAudioIpcDeps {
  appRoot: string
  spiritagentHome: string
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
  appRoot,
  spiritagentHome,
  hardening,
  ipcMain,
  mimeTypeForPath
}: OnboardingAudioIpcDeps): void {
  const audioRoot = path.resolve(spiritagentHome, 'audio', 'onboarding', 'zh')

  // 如果 appRoot 是 client/ 或 client/dist-electron，则解析到仓库根目录
  let repoRoot = appRoot

  if (path.basename(repoRoot) === 'dist-electron') {
    repoRoot = path.dirname(repoRoot)
  }

  if (path.basename(repoRoot) === 'client') {
    repoRoot = path.dirname(repoRoot)
  }

  const devAudioRoot = path.resolve(repoRoot, 'installer/payload/onboarding-audio/zh')

  ipcMain.handle(IPC.invoke.onboardingAudioRead, async (_event, tag: string) => {
    if (typeof tag !== 'string' || !TAG_RE.test(tag)) {
      throw new Error(`invalid onboarding audio tag: ${tag}`)
    }

    let targetPath = path.join(audioRoot, `${tag}.mp3`)

    if (!fs.existsSync(targetPath)) {
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
