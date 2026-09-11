import type { SpiritAgentApiRequest } from '@ipc/contracts'

import { $auth } from '@/shared/store/auth'

import { currentClearEpoch } from './storage'

/** 鉴权 RPC 的统一返回：不把 auth-loss / 网络错 / 后端错 / void body 压成同一个 null，
 * 调用方按 reason 分流——避免 mesh2d 等 store 把"登出 race"误判成"请求成功无返回"而把状态卡在 'generating'。 */
type AuthedApiResult<T> =
  | { ok: true; value: T | null }
  | { ok: false; reason: 'unauth' }
  | { ok: false; reason: 'err'; error: unknown }

export async function authedApi<T>(opts: SpiritAgentApiRequest): Promise<AuthedApiResult<T>> {
  if ($auth.get().kind !== 'authenticated') {
    return { ok: false, reason: 'unauth' }
  }

  // 同步快照 clearEpoch：IPC 往返里若触发登出，epoch 会被推进——本次 response 视为过期，
  // 调用方不需要再各自重读 $auth 防御「收到响应 → 写入持久层 → 写完发现 auth 已经翻了」这条路径。
  const epoch = currentClearEpoch()

  try {
    const value = (await window.spiritagent.api<T>(opts)) as T | null

    if ($auth.get().kind !== 'authenticated' || currentClearEpoch() !== epoch) {
      return { ok: false, reason: 'unauth' }
    }

    return { ok: true, value }
  } catch (error) {
    return { ok: false, error, reason: 'err' }
  }
}
