import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { applyAppNavigation } from '@/app/navigation/app-navigation'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'
import { $uiStateReady } from '@/shared/store/ui-state'
import type { AppNavigationTarget } from '@ipc/contracts'

interface NavigationReveal {
  key: string
  request: number
}

// 宿主导航请求可能早于账户与界面恢复，先暂存一条，就绪后再执行。
export function useSurfaceNavigation(errorLabel: string): void {
  const authenticated = useStore($auth).kind === 'authenticated'
  const uiStateReady = useStore($uiStateReady)
  const beginAsync = useAsyncGuard()
  const pendingNavigation = useRef<AppNavigationTarget | null>(null)

  useEffect(() => {
    const navigate = (next: AppNavigationTarget): void => {
      if ($auth.get().kind !== 'authenticated' || !$uiStateReady.get()) {
        pendingNavigation.current = next

        return
      }

      const isLive = beginAsync()
      void applyAppNavigation(next).catch(error => {
        if (isLive()) {
          notifyError(error, errorLabel)
        }
      })
    }

    const stop = shellApi().surface.onNavigate(navigate)

    if (authenticated && uiStateReady && pendingNavigation.current) {
      const next = pendingNavigation.current
      pendingNavigation.current = null
      navigate(next)
    }

    return stop
  }, [authenticated, uiStateReady, beginAsync, errorLabel])
}

// 桌面宿主导航完成后才把消息定位交给页面，避免在切页前投递失效。
export function useDesktopNavigation(errorLabel: string): {
  unreadReveal: NavigationReveal | undefined
  reveal: (key: string) => void
  clearReveal: () => void
} {
  const beginAsync = useAsyncGuard()
  const request = useRef(0)
  const [unreadReveal, setUnreadReveal] = useState<NavigationReveal>()

  const reveal = useCallback((key: string): void => setUnreadReveal({ key, request: ++request.current }), [])
  const clearReveal = useCallback((): void => setUnreadReveal(undefined), [])

  useEffect(
    () =>
      shellApi().desktop.onNavigate(payload => {
        const isLive = beginAsync()
        void applyAppNavigation(payload)
          .then(() => {
            if (isLive() && payload.focus.page === 'chat' && payload.focus.messageKey) {
              setUnreadReveal({ key: payload.focus.messageKey, request: ++request.current })
            }
          })
          .catch(error => {
            if (isLive()) {
              notifyError(error, errorLabel)
            }
          })
      }),
    [beginAsync, errorLabel]
  )

  return { unreadReveal, reveal, clearReveal }
}
