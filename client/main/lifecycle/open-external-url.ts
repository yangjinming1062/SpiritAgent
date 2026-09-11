import { fileURLToPath } from 'node:url'

import { shell } from 'electron'

import { errorMessage } from '../shared/utils'

/**
 * 打开外部链接/本地路径：仅放行 http/https/mailto 与 file:，
 * file: 走 openPath，失败则 reveal in folder。
 */
export function createOpenExternalUrl(rememberLog: (chunk: string) => void): (rawUrl: string) => boolean {
  return function openExternalUrl(rawUrl: string): boolean {
    const raw = String(rawUrl || '').trim()

    if (!raw) {
      return false
    }

    let parsed: URL

    try {
      parsed = new URL(raw)
    } catch {
      return false
    }

    if (parsed.protocol === 'file:') {
      let localPath: string

      try {
        localPath = fileURLToPath(parsed.toString())
      } catch {
        return false
      }

      void shell
        .openPath(localPath)
        .then(error => {
          if (!error) {
            return
          }

          rememberLog(`[file] openPath failed: ${error}; revealing in folder instead`)

          try {
            shell.showItemInFolder(localPath)
          } catch (revealError) {
            const msg = errorMessage(revealError)
            rememberLog(`[file] showItemInFolder failed: ${msg}`)
          }
        })
        .catch(error => rememberLog(`[file] openPath rejected: ${errorMessage(error)}`))

      return true
    }

    if (!['http:', 'https:', 'mailto:'].includes(parsed.protocol)) {
      return false
    }

    const url = parsed.toString()
    shell.openExternal(url).catch(error => rememberLog(`[link] openExternal failed: ${errorMessage(error)}`))

    return true
  }
}
