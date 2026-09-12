import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { net, protocol } from 'electron'

import { resolveReadableFileForIpc } from '../security/hardening'
import { STREAMABLE_MEDIA_EXTS } from '../shared/mime'

export const MEDIA_PROTOCOL = 'spiritagent-media'

// 仅允许从应用缓存目录流式读出——渲染层不得经自定义协议探测任意绝对路径。
function isUnderMediaRoot(resolvedPath: string, roots: readonly string[]): boolean {
  const normalized = path.resolve(resolvedPath)

  return roots.some(root => {
    const rootResolved = path.resolve(root)

    return normalized === rootResolved || normalized.startsWith(rootResolved + path.sep)
  })
}

function mediaProtocolRoots(spiritagentHome: string): string[] {
  return [path.join(spiritagentHome, 'cache'), path.join(spiritagentHome, 'audio')]
}

export function registerMediaProtocol(spiritagentHome: string): void {
  const roots = mediaProtocolRoots(spiritagentHome)

  protocol.handle(MEDIA_PROTOCOL, async request => {
    let resolvedPath: string

    try {
      const url = new URL(request.url)
      const rawPath = decodeURIComponent(url.pathname)

      const filePath = process.platform === 'win32' ? rawPath.replace(/^\/+([A-Za-z]:)/, '$1') : rawPath

      ;({ resolvedPath } = await resolveReadableFileForIpc(filePath, { purpose: 'Media stream' }))
    } catch {
      return new Response('Media not found', { status: 404 })
    }

    if (!isUnderMediaRoot(resolvedPath, roots)) {
      return new Response('Media path not allowed', { status: 403 })
    }

    if (!STREAMABLE_MEDIA_EXTS.has(path.extname(resolvedPath).toLowerCase())) {
      return new Response('Unsupported media type', { status: 415 })
    }

    return net.fetch(pathToFileURL(resolvedPath).toString(), {
      bypassCustomProtocolHandlers: true,
      headers: request.headers
    })
  })
}

export function registerMediaProtocolScheme(): void {
  protocol.registerSchemesAsPrivileged([
    {
      privileges: {
        secure: true,
        standard: true,
        stream: true,
        supportFetchAPI: true
      },
      scheme: MEDIA_PROTOCOL
    }
  ])
}
