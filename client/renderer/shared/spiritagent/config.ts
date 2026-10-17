import { requestResource } from '@/shared/lib/resource-request'
import type { SpiritAgentConfigResponse } from '@protocol'

export async function getSpiritAgentConfig(): Promise<SpiritAgentConfigResponse> {
  const { config } = await requestResource('config.get_config')

  return config
}

export async function saveSpiritAgentConfig(
  config: SpiritAgentConfigResponse
): Promise<{ config: SpiritAgentConfigResponse }> {
  const response = await requestResource('config.put_config', { body: { config: { ...config } } })

  return { config: response.config }
}
