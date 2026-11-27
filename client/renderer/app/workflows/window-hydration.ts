import { useEffect } from 'react'

import { hydratePersona, hydratePortrait } from '@/features/character'
import {
  $chatSessionId,
  $companionSessionId,
  ensureCompanionSession,
  findConversationRuntime,
  setChatSession,
  switchSession
} from '@/features/conversation'
import { hydrateCreationActivity } from '@/features/creation'
import { hydrateScene } from '@/features/creation/environment'
import { hydrateDiaryUnread } from '@/features/life/diary'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
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
  const ready = authenticated && uiStateReady && gatewayState === 'open'
  useWindowSession(accountId, ready, errorLabel)

  useEffect(() => {
    if (ready) {
      void hydrateScene()
      void hydrateCreationActivity()
    }
  }, [accountId, ready])

  // 未读补查：网关连通或界面可见时各查一次；同一轮不重复触发，避免在途结果被自己作废。
  useEffect(() => {
    if (!authenticated || (gatewayState !== 'open' && !visible)) {
      return
    }

    void hydrateDiaryUnread()
  }, [visible, foreground, authenticated, accountId, gatewayState])
}

interface DesktopWindowHydration {
  accountId: string | null
  gatewayState: string
  uiStateReady: boolean
  errorLabel: string
}

// 桌面窗口承载多个页面：登录后补齐人物、场景与未读，联网后再恢复陪伴会话。
export function useDesktopWindowHydration({
  accountId,
  gatewayState,
  uiStateReady,
  errorLabel
}: DesktopWindowHydration): void {
  useWindowSession(accountId, !!accountId && gatewayState === 'open' && uiStateReady, errorLabel)

  useEffect(() => {
    if (!accountId) {
      return
    }

    void hydratePersona()
    void hydratePortrait()
    void hydrateScene()
    void hydrateCreationActivity()
    void hydrateDiaryUnread()
  }, [accountId, gatewayState])
}

function useWindowSession(accountId: string | null, ready: boolean, errorLabel: string): void {
  const beginAsync = useAsyncGuard()
  useEffect(() => {
    if (!ready) {
      return
    }

    const accountCurrent = beginAsync()
    let disposed = false
    const isLive = (): boolean => !disposed && accountCurrent() && $uiStateReady.get()

    const restore = async () => {
      const known = $companionSessionId.get()

      const companion =
        known && findConversationRuntime(known)?.$historyHydrated.get() ? known : await ensureCompanionSession()

      if (!isLive()) {
        return
      }

      if (companion && !$chatSessionId.get()) {
        setChatSession(companion)
      }

      const selected = $chatSessionId.get()

      // 已水合会话的断线恢复归网关；窗口只补首次选择缺少的基线。
      if (selected && !findConversationRuntime(selected)?.$historyHydrated.get()) {
        await switchSession(selected)
      }
    }

    void restore().catch(error => {
      if (isLive()) {
        notifyError(error, errorLabel)
      }
    })

    return () => {
      disposed = true
    }
  }, [accountId, ready, beginAsync, errorLabel])
}
