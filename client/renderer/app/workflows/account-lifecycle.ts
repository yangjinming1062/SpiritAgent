import { useEffect } from 'react'

import { setCompanionLifecycle } from '@/modules/character'
import { useMainProcessListener } from '@/shared/hooks/use-main-process-listener'
import { authedApi } from '@/shared/lib/authed-api'
import { requestGateway } from '@/shared/lib/gateway-request'
import { log } from '@/shared/lib/log'
import { currentClearEpoch } from '@/shared/lib/storage'
import { $auth, applyAuthBroadcast, expireSession, hydrateAuth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'

interface OnboardingState {
  complete?: boolean
}

// 上次查询没有得到结果时为 true，网关连通后据此重查。
let lifecycleUnresolved = false

/** 按当前会话重解伴生 lifecycle（unauthed/onboarding/ready）。各窗口是独立渲染进程，nanostores 不互通；换号 clearCompanionStorage 会把本窗 lifecycle 重置为 unauthed，必须各自重新解析。 */
async function syncCompanionLifecycle(): Promise<void> {
  const auth = $auth.get()

  if (auth.kind !== 'authenticated') {
    lifecycleUnresolved = false
    setCompanionLifecycle('unauthed')

    return
  }

  const sessionId = auth.snapshot.sessionId
  const epoch = currentClearEpoch()

  const isCurrent = (): boolean => {
    const current = $auth.get()

    return current.kind === 'authenticated' && current.snapshot.sessionId === sessionId && currentClearEpoch() === epoch
  }

  let complete: boolean | undefined
  const rest = await authedApi<OnboardingState>({ path: '/api/companion/onboarding/state' })

  if (rest.ok) {
    complete = rest.value?.complete
  } else if (rest.reason === 'unauth') {
    // 会话已切换，由新会话的同步负责。
    return
  } else {
    log.warn('account-lifecycle', 'onboarding state REST failed', rest.error)

    try {
      const state = await requestGateway<OnboardingState>('onboarding.get_state', {})

      if (!isCurrent()) {
        return
      }

      complete = state?.complete
    } catch (gatewayErr) {
      if (!isCurrent()) {
        return
      }

      log.warn('account-lifecycle', 'onboarding state gateway failed', gatewayErr)
    }
  }

  // 只按服务端明确的完成状态切换；查询失败保留当前值，网关连通后重查，不把离线启动的已完成用户误送回 onboarding。
  if (typeof complete !== 'boolean') {
    lifecycleUnresolved = true

    return
  }

  lifecycleUnresolved = false
  setCompanionLifecycle(complete ? 'ready' : 'onboarding')
}

// 各窗口独立水合认证并订阅变更，让连接与操作使用当前会话。
export function useAccountLifecycle(): void {
  useEffect(() => {
    // finally：水合失败也要重解 lifecycle，避免本窗停在 clear 后的 unauthed。
    void hydrateAuth().finally(() => {
      void syncCompanionLifecycle()
    })
  }, [])

  useEffect(
    () =>
      $gatewayState.listen(state => {
        if (state === 'open' && lifecycleUnresolved) {
          void syncCompanionLifecycle()
        }
      }),
    []
  )

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
