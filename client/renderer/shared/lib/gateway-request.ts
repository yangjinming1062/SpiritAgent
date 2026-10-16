import { $gateway } from '@/shared/store/gateway'

import type { RpcMethod, RpcRequest, RpcResponse } from './gateway-protocol'

// 连接由主进程管理，业务请求失败后不自动重放可能已生效的操作。
export async function requestGateway<K extends RpcMethod>(method: K, params: RpcRequest<K>): Promise<RpcResponse<K>> {
  const gateway = $gateway.get()

  if (!gateway) {
    throw new Error('SpiritAgent gateway unavailable')
  }

  return gateway.request(method, params)
}
