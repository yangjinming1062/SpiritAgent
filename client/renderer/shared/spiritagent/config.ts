import type { SpiritAgentConfigPutRequest, SpiritAgentConfigResponse } from '@/shared/types/spiritagent'

export async function getSpiritAgentConfig(): Promise<SpiritAgentConfigResponse> {
  const response = await window.spiritagent.api<{ config?: SpiritAgentConfigResponse } | SpiritAgentConfigResponse>({
    path: '/api/config'
  })

  return (response as { config?: SpiritAgentConfigResponse }).config ?? (response as SpiritAgentConfigResponse)
}

export async function saveSpiritAgentConfig(
  config: SpiritAgentConfigPutRequest
): Promise<{ config: SpiritAgentConfigResponse }> {
  const response = await window.spiritagent.api<{ config: SpiritAgentConfigResponse }>({
    body: { config },
    method: 'PUT',
    path: '/api/config'
  })

  return { config: response.config }
}
