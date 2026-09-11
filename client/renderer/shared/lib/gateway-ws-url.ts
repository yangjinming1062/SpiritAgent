import type { SpiritAgentConnectionPublic } from '@ipc/contracts'

interface ResolveGatewayWsUrlDeps {
  getGatewayWsUrl?: () => Promise<string>
}

export async function resolveGatewayWsUrl(
  desktop: ResolveGatewayWsUrlDeps,
  conn: Pick<SpiritAgentConnectionPublic, 'wsUrl'>
): Promise<string> {
  const mint = desktop.getGatewayWsUrl

  if (mint) {
    const fresh = await mint().catch(() => null)

    if (fresh) {
      return fresh
    }
  }

  return conn.wsUrl
}
