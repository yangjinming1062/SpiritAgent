import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { hydratePersona, hydratePortrait } from '@/features/character'
import { $chatSessionId, ensureCompanionSession, setChatSession, switchSession } from '@/features/conversation'
import { hydrateCreationActivity } from '@/features/creation'
import { hydrateScene } from '@/features/creation/environment'
import { hydrateDiaryUnread } from '@/features/life/diary'
import { hydratePostsUnread } from '@/features/life/posts'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { $gateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { $uiStateReady } from '@/shared/store/ui-state'

interface MainWindowHydration {
  authenticated: boolean
  accountId: string | null
  gatewayState: string
  uiStateReady: boolean
  visible: boolean
  foreground: boolean
  errorLabel: string
}

// 主窗口只负责展示，账户就绪后的场景、陪伴会话与未读补查统一在这里完成。
export function useMainWindowHydration({
  authenticated,
  accountId,
  gatewayState,
  uiStateReady,
  visible,
  foreground,
  errorLabel
}: MainWindowHydration): void {
  const beginAsync = useAsyncGuard()

  useEffect(() => {
    if (!authenticated || !uiStateReady || gatewayState !== 'open') {
      return
    }

    void hydrateScene()
    void hydrateCreationActivity()
    const accountCurrent = beginAsync()
    let disposed = false
    const isLive = (): boolean => !disposed && accountCurrent() && $uiStateReady.get()
    void ensureCompanionSession()
      .then(async sessionId => {
        if (!isLive()) {
          return
        }

        if (sessionId && !$chatSessionId.get()) {
          setChatSession(sessionId)
        }

        const selected = $chatSessionId.get()

        if (selected) {
          await switchSession(selected)
        }
      })
      .catch(error => {
        if (isLive()) {
          notifyError(error, errorLabel)
        }
      })

    return () => {
      disposed = true
    }
  }, [authenticated, uiStateReady, accountId, gatewayState, beginAsync, errorLabel])

  // 未读补查：网关连通或界面可见时各查一次；同一轮不重复触发，避免在途结果被自己作废。
  useEffect(() => {
    if (!authenticated || (gatewayState !== 'open' && !visible)) {
      return
    }

    void hydratePostsUnread()
    void hydrateDiaryUnread()
  }, [visible, foreground, authenticated, accountId, gatewayState])
}

interface DesktopWindowHydration {
  authenticated: boolean
  gatewayState: string
  uiStateReady: boolean
  errorLabel: string
}

// 桌面窗口承载多个页面：登录后补齐人物、场景与未读，联网后再恢复陪伴会话。
export function useDesktopWindowHydration({
  authenticated,
  gatewayState,
  uiStateReady,
  errorLabel
}: DesktopWindowHydration): void {
  const gateway = useStore($gateway)
  const initialized = useRef(false)

  useEffect(() => {
    if (!authenticated) {
      return
    }

    void hydratePersona()
    void hydratePortrait()
    void hydrateScene()
    void hydrateCreationActivity()
    void hydratePostsUnread()
    void hydrateDiaryUnread()
  }, [authenticated, gatewayState])

  useEffect(() => {
    if (gatewayState !== 'open' || !uiStateReady) {
      return
    }

    let disposed = false
    void ensureCompanionSession()
      .then(id => {
        if (disposed || !id) {
          return
        }

        if (!initialized.current) {
          initialized.current = true
          const target = new URLSearchParams(window.location.search).get('sessionId')

          const selected = target || $chatSessionId.get() || id
          setChatSession(selected)
          void switchSession(selected)
        }
      })
      .catch(error => {
        if (!disposed) {
          notifyError(error, errorLabel)
        }
      })

    return () => {
      disposed = true
    }
  }, [gateway, gatewayState, uiStateReady, errorLabel])
}
