import { useEffect } from 'react'

import { setCompanionLifecycle } from '@/modules/character'
import { useMainProcessListener } from '@/shared/hooks/use-main-process-listener'
import { requestGateway } from '@/shared/lib/gateway-request'
import { log } from '@/shared/lib/log'
import { $auth, applyAuthBroadcast, expireSession, hydrateAuth } from '@/shared/store/auth'

/**
 * 按当前会话重解伴生 lifecycle（unauthed / onboarding / ready）。
 * 各窗口是独立渲染进程，nanostores 不互通；换号 clearCompanionStorage 会把本窗
 * lifecycle 重置为 unauthed，必须各自重新解析，不能只依赖精灵窗写入的 localStorage。
 */
export async function syncCompanionLifecycle(): Promise<void> {
  if ($auth.get().kind !== 'authenticated') {
    setCompanionLifecycle('unauthed')

    return
  }

  let state: { complete?: boolean } | null = null

  try {
    state = await window.spiritagent.api<{ complete?: boolean }>({
      path: '/api/companion/onboarding/state'
    })
  } catch (err) {
    log.warn('account-lifecycle', 'onboarding state REST failed', err)

    try {
      state = await requestGateway<{ complete?: boolean }>('onboarding.get_state', {})
    } catch (gatewayErr) {
      log.warn('account-lifecycle', 'onboarding state gateway failed', gatewayErr)
      state = null
    }
  }

  // 仅 complete === true 视为已完成；查询失败按未完成落 onboarding，由向导或蛋形入口暴露状态。
  setCompanionLifecycle(state?.complete === true ? 'ready' : 'onboarding')
}

// 各窗口独立水合认证并订阅变更，让连接与操作使用当前会话。
export function useAccountLifecycle(): void {
  useEffect(() => {
    // finally：水合失败也要重解 lifecycle，避免本窗停在 clear 后的 unauthed。
    void hydrateAuth().finally(() => {
      void syncCompanionLifecycle()
    })
  }, [])

  useMainProcessListener(
    'onAuthChanged',
    payload => {
      void applyAuthBroadcast(payload).finally(() => {
        void syncCompanionLifecycle()
      })
    },
    []
  )
  useMainProcessListener('onSessionExpired', sessionId => void expireSession(sessionId), [])
}
