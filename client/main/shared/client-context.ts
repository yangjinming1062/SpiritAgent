import os from 'node:os'

import { spiritagentHome } from '../security/paths'

interface BuildClientContextOptions {
  spiritagentHome?: null | string
  desktopVersion?: string
}

interface ClientContextResult {
  client_context: {
    environment_hints: string
    platform_hints: string
  }
  client_version: string
}

export function buildClientContext(options: BuildClientContextOptions = {}): ClientContextResult {
  const platform = process.platform
  const arch = process.arch
  const desktopVersion = options.desktopVersion ?? 'unknown'
  const home = options.spiritagentHome ?? spiritagentHome()

  const lines = [
    `${platform} ${os.release()}`,
    `arch=${arch}`,
    desktopVersion !== 'unknown' ? `spiritagent-desktop=${desktopVersion}` : null,
    process.versions?.node ? `node=${process.versions.node}` : null,
    home ? `spiritagent_home=${home}` : null
  ].filter(Boolean)

  return {
    client_context: {
      environment_hints: lines.join('; '),
      platform_hints: `SpiritAgentDesktop/${desktopVersion} (${platform}; ${arch})`
    },
    client_version: desktopVersion
  }
}
