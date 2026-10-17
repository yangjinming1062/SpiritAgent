import { setCompanionLifecycle } from '@/modules/character'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { requestResource } from '@/shared/lib/resource-request'
import { desktopEvents } from '@/shared/platform/desktop'
import { applyAuthBroadcast, expireSession, hydrateAuth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'

interface OnboardingState {
  complete?: boolean
}

export function createAccountLifecycleRuntime() {
  let disposed = false
  let started = false
  let unresolved = false
  let generation = 0
  const subscriptions: Array<() => void> = []

  async function sync(): Promise<void> {
    if (disposed) {
      return
    }

    const currentGeneration = ++generation
    const authScope = captureAuthScope()
    const isCurrent = (): boolean => !disposed && currentGeneration === generation && Boolean(authScope?.())

    if (!authScope) {
      unresolved = false
      setCompanionLifecycle('unauthed')

      return
    }

    let complete: boolean | undefined
    let state: OnboardingState | undefined

    try {
      state = await requestResource('onboarding.get_onboarding')
    } catch (error) {
      if (isCurrent()) {
        log.warn('account-lifecycle', 'onboarding state failed', error)
      }
    }

    if (!isCurrent()) {
      return
    }

    complete = state?.complete

    // 查询失败保留已展示状态，网关连通后重查。
    unresolved = typeof complete !== 'boolean'

    if (!unresolved) {
      setCompanionLifecycle(complete ? 'ready' : 'onboarding')
    }
  }

  function start(): void {
    if (started || disposed) {
      return
    }

    started = true
    const events = desktopEvents()
    subscriptions.push(
      $gatewayState.listen(state => {
        if (state === 'open' && unresolved) {
          void sync()
        }
      }),
      events.onAuthChanged(payload => {
        if (disposed) {
          return
        }

        generation += 1
        void applyAuthBroadcast(payload).finally(() => {
          void sync()
        })
      }),
      events.onSessionExpired(sessionId => {
        if (!disposed) {
          void expireSession(sessionId)
        }
      })
    )
    void hydrateAuth().finally(() => {
      void sync()
    })
  }

  return {
    start,
    dispose: () => {
      disposed = true
      generation += 1
      subscriptions.splice(0).forEach(stop => stop())
    }
  }
}
