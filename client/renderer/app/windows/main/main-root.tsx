import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { PresentationModeButton } from '@/app/components/presentation-mode-switch'
import { CompanionMenu } from '@/app/components/surface-companion/companion-menu'
import { SurfaceCompanion } from '@/app/components/surface-companion/surface-companion'
import { CompanionHub } from '@/app/features/companion/companion-page'
import { ConversationPage } from '@/app/features/conversation/conversation-page'
import { DiaryPage } from '@/app/features/diary/diary-page'
import { PostsPage } from '@/app/features/posts/posts-page'
import { SceneBackdrop } from '@/app/features/scene/scene-backdrop'
import { AppSettings } from '@/app/features/settings/app-settings'
import {
  $appNavigationRevision,
  $appNavigationTarget,
  $appPage,
  applyAppNavigation
} from '@/app/navigation/app-navigation'
import { SpriteStatusBadge } from '@/modules/character'
import {
  $chatSessionId,
  $companionSessionId,
  ensureCompanionSession,
  pendingMessages,
  setChatSession,
  switchSession,
  useConversationUnread
} from '@/modules/conversation'
import { hydrateDiaryUnread } from '@/modules/diary'
import { hydratePostsUnread } from '@/modules/posts'
import { hydrateScene } from '@/modules/scene'
import { useInteractiveRegion, useWindowMouseCapture } from '@/shared'
import { PanelActivityProvider } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { BTN_SUBTLE, WindowControls } from '@/shared/panel'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $mainChatActive } from '@/shared/store/chat-visibility'
import { $gatewayState } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { $surfaceOpenVisible, $surfaceScreenLocked } from '@/shared/store/surfaces'
import { $uiStateError, $uiStateReady, retryUiState } from '@/shared/store/ui-state'
import { useStrings } from '@/shared/strings'
import type { AppNavigationTarget, AppPage } from '@ipc/contracts'

import { MainRail } from './main-rail'
import styles from './main.module.css'

export function MainRoot(): React.JSX.Element {
  useWindowMouseCapture(1, { setIgnoreMouseEvents: shellApi().surface.setIgnoreMouseEvents })
  const shellRef = useRef<HTMLDivElement>(null)
  const pendingNavigation = useRef<AppNavigationTarget | null>(null)
  useInteractiveRegion('main-shell', shellRef, undefined, undefined, 1)
  const page = useStore($appPage)
  const target = useStore($appNavigationTarget)
  const navigationRevision = useStore($appNavigationRevision)
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const uiStateReady = useStore($uiStateReady)
  const uiStateError = useStore($uiStateError)
  const companionSessionId = useStore($companionSessionId)
  const pending = useStore(pendingMessages.$atom)
  const { hasUnread: companionUnread } = useConversationUnread(companionSessionId)
  const openVisible = useStore($surfaceOpenVisible)
  const locked = useStore($surfaceScreenLocked)
  const presentation = useStore($presentation)
  const [foreground, setForeground] = useState(() => document.hasFocus())
  const [documentVisible, setDocumentVisible] = useState(() => document.visibilityState === 'visible')
  const [overlayOpen, setOverlayOpen] = useState(false)
  const beginAsync = useAsyncGuard()
  const dict = useStrings()
  const t = dict.living.rail

  const labels: Record<AppPage, string> = {
    chat: t.chat,
    posts: t.posts,
    diary: t.diary,
    companion: t.companionFallback,
    settings: t.settings
  }

  const pageTitle = labels[page]
  const authenticated = auth.kind === 'authenticated'
  const accountId = authenticated ? auth.snapshot.accountId : null
  const visible = openVisible && documentVisible && !locked && presentation.effectiveMode === 'window'

  const active =
    visible &&
    foreground &&
    authenticated &&
    uiStateReady &&
    !overlayOpen &&
    !['starting', 'recovering'].includes(presentation.status)

  const reveal = target.messageKey ? { key: target.messageKey, request: navigationRevision } : undefined

  useEffect(() => {
    const updateFocus = (): void => setForeground(document.hasFocus())
    const updateVisibility = (): void => setDocumentVisible(document.visibilityState === 'visible')
    window.addEventListener('focus', updateFocus)
    window.addEventListener('blur', updateFocus)
    document.addEventListener('visibilitychange', updateVisibility)

    return () => {
      window.removeEventListener('focus', updateFocus)
      window.removeEventListener('blur', updateFocus)
      document.removeEventListener('visibilitychange', updateVisibility)
    }
  }, [])

  useEffect(() => {
    document.title = `${dict.brand.name} · ${pageTitle}`
    document.documentElement.dataset.appPage = page

    return () => {
      delete document.documentElement.dataset.appPage
    }
  }, [dict.brand.name, page, pageTitle])

  useEffect(() => {
    $mainChatActive.set(page === 'chat' && active)

    return () => $mainChatActive.set(false)
  }, [page, active])

  useEffect(() => {
    const navigate = (next: AppNavigationTarget): void => {
      if ($auth.get().kind !== 'authenticated' || !$uiStateReady.get()) {
        pendingNavigation.current = next

        return
      }

      const isLive = beginAsync()
      void applyAppNavigation(next).catch(error => {
        if (isLive()) {
          notifyError(error, t.chat)
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
  }, [authenticated, uiStateReady, beginAsync, t.chat])

  useEffect(() => {
    if (!authenticated || !uiStateReady || gatewayState !== 'open') {
      return
    }

    void hydrateScene()
    void hydratePostsUnread()
    void hydrateDiaryUnread()
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
          notifyError(error, t.chat)
        }
      })

    return () => {
      disposed = true
    }
  }, [authenticated, uiStateReady, accountId, gatewayState, beginAsync, t.chat])

  useEffect(() => {
    if (visible && authenticated) {
      void hydratePostsUnread()
      void hydrateDiaryUnread()
    }
  }, [visible, foreground, authenticated, accountId])

  return (
    <div className={styles.windowFrame}>
      <SurfaceCompanion />
      <div className={styles.contentFrame}>
        <SceneBackdrop />
        <div aria-hidden="true" className={styles.glassPlate} />
        <div className={styles.shell} data-surface="main" ref={shellRef}>
          <header
            className={styles.titlebar}
            onDoubleClick={event => {
              if (event.target instanceof Element && !event.target.closest('button')) {
                void shellApi().surface.maximize()
              }
            }}
          >
            <div className={styles.titleArea}>
              <SpriteStatusBadge compact />
            </div>
            <div className={styles.titleActions}>
              <PresentationModeButton className={styles.modeButton} />
              <CompanionMenu />
              <WindowControls />
            </div>
          </header>
          {uiStateError && (
            <div
              className="flex shrink-0 items-center gap-3 border-b border-line-hairline bg-surface-card px-5 py-3 text-sm"
              role="alert"
            >
              <p className="min-w-0 flex-1">
                <strong className="block text-strong">{dict.common.pageRestoreFailed}</strong>
                <span className="break-words text-muted">{uiStateError}</span>
              </p>
              <button className={BTN_SUBTLE} onClick={retryUiState} type="button">
                {dict.common.retry}
              </button>
            </div>
          )}
          <div
            className={styles.body}
            inert={!authenticated || !uiStateReady || ['starting', 'recovering'].includes(presentation.status)}
          >
            <MainRail
              chatUnread={pending.length > 0 || companionUnread}
              companionUnread={companionUnread}
              labels={labels}
              onOverlayChange={setOverlayOpen}
            />
            <main className={styles.stage} data-page={page}>
              <div className={styles.pageViewport} hidden={page !== 'chat'}>
                <ConversationPage
                  active={page === 'chat' && active}
                  companionSessionId={companionSessionId}
                  companionUnread={companionUnread}
                  containerRef={shellRef}
                  foreground={foreground}
                  receiveExternalAttachments={page === 'chat' && visible}
                  reveal={reveal}
                  viewId="main-chat"
                  visible={page === 'chat' && visible}
                />
              </div>
              <PanelActivityProvider active={active && page !== 'chat'}>
                {page === 'posts' && <PostsPage containerRef={shellRef} foreground={active} mediaViewId="main-posts" />}
                {page === 'diary' && <DiaryPage foreground={active} />}
                {page === 'companion' && <CompanionHub />}
                {page === 'settings' && <AppSettings />}
              </PanelActivityProvider>
            </main>
          </div>
        </div>
      </div>
    </div>
  )
}
