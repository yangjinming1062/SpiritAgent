import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { AppContentPage } from '@/app/components/app-content-page'
import { $appNavigationRevision, $appNavigationTarget, $appPage } from '@/app/navigation/app-navigation'
import { useMainWindowHydration } from '@/app/workflows/window-hydration'
import { useSurfaceNavigation } from '@/app/workflows/window-navigation'
import { PresentationModeButton } from '@/features/character'
import { CompanionMenu } from '@/features/character/surface-companion'
import {
  $chatSessionId,
  $companionSessionId,
  getConversationRuntime,
  pendingMessages,
  useConversationUnread
} from '@/features/conversation'
import { ConversationPage } from '@/features/conversation/conversation-page'
import { LifeArea, LifeDrawer } from '@/features/life/life-area'
import { PanelActivityProvider } from '@/shared/context/panel-activity'
import { Home } from '@/shared/lib/icons'
import { useInteractiveRegion, useWindowMouseCapture } from '@/shared/lib/interactive-regions'
import { shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { $mainChatActive } from '@/shared/store/chat-visibility'
import { $gatewayState } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import {
  $surfaceCompanion,
  $surfaceOpenVisible,
  $surfaceScreenLocked,
  setSurfaceCompanion
} from '@/shared/store/surfaces'
import { $uiStateError, $uiStateReady, retryUiState } from '@/shared/store/ui-state'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, WindowControls } from '@/ui'
import type { AppPage } from '@ipc/contracts'

import { MainRail } from './main-rail'
import styles from './main.module.css'

export function MainRoot(): React.JSX.Element {
  useWindowMouseCapture(1, { setIgnoreMouseEvents: shellApi().surface.setIgnoreMouseEvents })
  const shellRef = useRef<HTMLDivElement>(null)
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
  const dict = useStrings()
  const t = dict.living.rail

  const labels: Record<AppPage, string> = {
    chat: t.chat,
    life: t.life,
    creation: t.creation,
    companion: t.companionFallback,
    settings: t.settings
  }

  const companion = useStore($surfaceCompanion)
  const selectedSessionId = useStore($chatSessionId)
  const selectedRuntime = getConversationRuntime(selectedSessionId)
  const presetId = useStore(selectedRuntime.$chatSessionPresetId)
  const readOnly = useStore(selectedRuntime.$chatSessionReadOnly)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const [narrow, setNarrow] = useState(true)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [expandedSession, setExpandedSession] = useState<string | null>(null)
  const work = page === 'chat' && (readOnly || Boolean(presetId && presetId !== 'companion'))

  const wantsLife =
    companion.preference.enabled && (!work || (selectedSessionId !== null && expandedSession === selectedSessionId))

  const lifeShown = wantsLife && (!narrow || drawerOpen)
  const lifeModal = narrow && lifeShown
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
    !lifeModal &&
    !['starting', 'recovering'].includes(presentation.status)

  const reveal =
    target.focus.page === 'chat' && target.focus.messageKey
      ? { key: target.focus.messageKey, request: navigationRevision }
      : undefined

  useSurfaceNavigation(t.chat)
  useMainWindowHydration({
    authenticated,
    accountId,
    gatewayState,
    uiStateReady,
    visible,
    foreground,
    errorLabel: t.chat
  })

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
    const element = workspaceRef.current

    if (!element) {
      return
    }

    const measure = (): void => setNarrow(element.clientWidth < 960)
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    measure()

    return () => observer.disconnect()
  }, [])

  const revealLife = (): void => {
    setExpandedSession(selectedSessionId)
    setDrawerOpen(true)
  }

  const openLife = (): void => {
    revealLife()

    if (!companion.preference.enabled) {
      void setSurfaceCompanion({ ...companion.preference, enabled: true }).catch(error =>
        notifyError(error, dict.life.show)
      )
    }
  }

  const closeLife = (): void => {
    if (narrow) {
      setDrawerOpen(false)
    } else {
      void setSurfaceCompanion({ ...companion.preference, enabled: false }).catch(error =>
        notifyError(error, dict.life.hide)
      )
    }
  }

  return (
    <div className={styles.windowFrame}>
      <div className={styles.contentFrame}>
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
            <div className={styles.titleActions}>
              <PresentationModeButton className={styles.modeButton} />
              {(narrow || !lifeShown) && (
                <button aria-label={dict.life.show} className={styles.modeButton} onClick={openLife} type="button">
                  <Home size={14} />
                  {dict.life.title}
                </button>
              )}
              <CompanionMenu onShow={revealLife} />
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
              labels={labels}
              onOverlayChange={setOverlayOpen}
            />
            <div className={styles.workspace} ref={workspaceRef}>
              {!narrow && lifeShown && (
                <div className={styles.lifeSlot} style={{ order: companion.preference.side === 'left' ? -1 : 1 }}>
                  <LifeArea onClose={closeLife} visible={visible && authenticated && uiStateReady} />
                </div>
              )}
              {lifeModal && (
                <LifeDrawer
                  onClose={() => setDrawerOpen(false)}
                  side={companion.preference.side}
                  visible={visible && authenticated && uiStateReady}
                />
              )}
              <main className={styles.stage} data-page={page} inert={lifeModal}>
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
                  {/* surface 关闭（隐藏）时卸载非聊天页：再次打开回到一级列表，草稿与筛选在内存 store 不受影响。 */}
                  {page !== 'chat' && openVisible && (
                    <AppContentPage
                      containerRef={shellRef}
                      foreground={active}
                      mediaViewId="main-posts"
                      page={page}
                      visible={visible && authenticated && uiStateReady}
                    />
                  )}
                </PanelActivityProvider>
              </main>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
