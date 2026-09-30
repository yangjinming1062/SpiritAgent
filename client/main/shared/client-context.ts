import os from 'node:os'

interface BuildClientContextOptions {
  desktopVersion: string
  spiritagentHome: string
}

interface ClientContextResult {
  client_context: {
    environment_hints: string
    platform_hints: string
  }
  client_version: string
}

export function buildClientContext({
  desktopVersion,
  spiritagentHome
}: BuildClientContextOptions): ClientContextResult {
  const platform = process.platform
  const arch = process.arch

  const lines = [
    `${platform} ${os.release()}`,
    `arch=${arch}`,
    `spiritagent-desktop=${desktopVersion}`,
    `node=${process.versions.node}`,
    `spiritagent_home=${spiritagentHome}`
  ]

  return {
    client_context: {
      environment_hints: lines.join('; '),
      platform_hints: `SpiritAgentDesktop/${desktopVersion} (${platform}; ${arch})`
    },
    client_version: desktopVersion
  }
}
