import os from 'node:os'

import { spiritagentHome } from '../security/paths'

interface BuildClientContextOptions {
  arch?: string
  spiritagentHome?: null | string
  desktopVersion?: string
  nodeVersion?: null | string
  platform?: string
  release?: string
  userAgent?: null | string
}

interface ClientContextResult {
  client_context: {
    environment_hints: string
    platform_hints: string
  }
  client_version: string
}

export function buildClientContext(options: BuildClientContextOptions = {}): ClientContextResult {
  const platform = options.platform ?? process.platform
  const arch = options.arch ?? process.arch
  const release = options.release ?? os.release()
  const nodeVersion = options.nodeVersion ?? process.versions?.node ?? null
  const desktopVersion = options.desktopVersion ?? 'unknown'
  const userAgent = options.userAgent ?? null
  const home = options.spiritagentHome ?? spiritagentHome()

  const lines = [
    `${platform} ${release}`,
    `arch=${arch}`,
    desktopVersion !== 'unknown' ? `spiritagent-desktop=${desktopVersion}` : null,
    nodeVersion ? `node=${nodeVersion}` : null,
    home ? `spiritagent_home=${home}` : null
  ].filter(Boolean)

  return {
    client_context: {
      environment_hints: lines.join('; '),
      platform_hints: userAgent || `SpiritAgentDesktop/${desktopVersion} (${platform}; ${arch})`
    },
    client_version: desktopVersion
  }
}
