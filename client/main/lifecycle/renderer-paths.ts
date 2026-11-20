import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { UI_THEME_URL_PARAM } from '@ipc/contracts'

import { fileExists } from '../shared/utils'

function unpackedPathFor(filePath: string): string {
  return filePath.replace(/app\.asar(?=$|[\\/])/, 'app.asar.unpacked')
}

/** 应用图标取首个存在的候选：Windows 任务栏/窗口图标优先 .ico（多尺寸位图）；macOS dock 用 png 即可。 */
export function createAppIconResolver(appRoot: string): () => null | string {
  const candidates = [
    ...(process.platform === 'win32' ? [path.join(appRoot, 'assets', 'icon.ico')] : []),
    path.join(appRoot, 'assets', 'icon.png'),
    path.join(appRoot, 'assets', 'icon.ico'),
    path.join(process.resourcesPath, 'icon.ico'),
    path.join(unpackedPathFor(appRoot), 'icon.ico')
  ]

  return () => candidates.find(fileExists) || null
}

// app.getAppPath() 在开发版与 asar 包内都指向应用根，构建产物统一位于 dist。
export function createRendererUrl(appRoot: string, devServer?: string | null) {
  return function rendererUrlFor(role: 'main' | 'sprite' | 'desktop' | 'desktop-background', theme?: string): string {
    const htmlFile = `${role}.html`
    const url = devServer ? new URL(`${devServer}/${htmlFile}`) : pathToFileURL(path.join(appRoot, 'dist', htmlFile))

    if (theme) {
      url.searchParams.set(UI_THEME_URL_PARAM, theme)
    }

    return url.toString()
  }
}
