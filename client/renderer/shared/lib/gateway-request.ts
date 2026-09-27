import { $gateway } from '@/shared/store/gateway'

// 重连归宿主运行时；业务请求失败后不自动重放可能已生效的操作。
export async function requestGateway<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  const gateway = $gateway.get()

  if (!gateway) {
    throw new Error('SpiritAgent gateway unavailable')
  }

  return gateway.request<T>(method, params)
}
