import type { SpiritAgentConfigResponse } from '@/shared/types/spiritagent'

export async function getSpiritAgentConfig(): Promise<SpiritAgentConfigResponse> {
  const { config } = await window.spiritagent.api<{ config: SpiritAgentConfigResponse }>({ path: '/api/config' })

  return config
}

export async function saveSpiritAgentConfig(
  config: SpiritAgentConfigResponse
): Promise<{ config: SpiritAgentConfigResponse }> {
  const response = await window.spiritagent.api<{ config: SpiritAgentConfigResponse }>({
    body: { config },
    method: 'PUT',
    path: '/api/config'
  })

  return { config: response.config }
}
