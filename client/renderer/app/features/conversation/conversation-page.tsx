import { useStore } from '@nanostores/react'
import type React from 'react'
import { type RefObject, useEffect, useId, useRef, useState } from 'react'

import { RunRail } from '@/app/features/execution/run-rail'
import { $persona } from '@/modules/character'
import {
  $chatSessionId,
  $currentSessionTitle,
  $sessionNavigationError,
  $systemPresets,
  ChatPanel,
  ConversationViewProvider,
  getConversationRuntime,
  openMainSession,
  presetDisplayName,
  switchSession,
  useIsReadOnlySession
} from '@/modules/conversation'
import { MediaViewerOverlay } from '@/modules/media'
import { PanelActivityProvider } from '@/shared/context/panel-activity'
import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { ArrowLeft, List } from '@/shared/lib/icons'
import { definePersistedBoolean, registerCompanionStorageKey } from '@/shared/lib/storage'
import { $gatewayState } from '@/shared/store/gateway'
import { useStrings } from '@/shared/strings'

import styles from './conversation-page.module.css'
import { useConversationStrings } from './conversation-strings'
import { SessionSidebar } from './session-sidebar'
import { usePendingConversationFeed } from './use-pending-conversation-feed'

const SIDEBAR_COLLAPSED_KEY = registerCompanionStorageKey('da.conversation.sidebarCollapsed')
const sidebarCollapsed = definePersistedBoolean({ key: SIDEBAR_COLLAPSED_KEY, fallback: false })

export interface ConversationPageProps {
  active: boolean
  visible: boolean
  foreground: boolean
  companionSessionId: string | null
  companionUnread: boolean
  reveal?: { key: string; request: number }
  containerRef: RefObject<HTMLElement | null>
  viewId: string
  receiveExternalAttachments?: boolean
  windowId?: number
}

export function ConversationPage({
  active,
  visible,
  foreground,
  companionSessionId,
  companionUnread,
  reveal,
  containerRef,
  viewId,
  receiveExternalAttachments = true,
  windowId = 1
}: ConversationPageProps): React.JSX.Element {
  const sessionId = useStore($chatSessionId)
  const runtime = getConversationRuntime(sessionId)
  const sessionKind = useStore(runtime.$chatSessionKind)
  const sessionPresetId = useStore(runtime.$chatSessionPresetId)
  const collapsed = useStore(sidebarCollapsed.$atom)
  const [narrow, setNarrow] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [railOpen, setRailOpen] = useState(false)
  const layoutRef = useRef<HTMLDivElement>(null)
  const sidebarRef = useRef<HTMLDivElement>(null)
  const sidebarToggle = useRef<HTMLButtonElement>(null)
  const railToggle = useRef<HTMLButtonElement>(null)
  const sidebarId = useId()
  const railId = useId()
  const t = useConversationStrings()
  const sidebarOpen = narrow ? drawerOpen : !collapsed

  useEffect(() => {
    const element = layoutRef.current

    if (!element) {
      return
    }

    const update = (): void => setNarrow(element.clientWidth < 920)
    const observer = new ResizeObserver(update)
    observer.observe(element)
    update()

    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!active || !visible || !foreground) {
      setDrawerOpen(false)
      setRailOpen(false)
    }
  }, [active, visible, foreground])

  useEffect(() => setRailOpen(false), [sessionId])

  useEffect(() => {
    if (narrow && drawerOpen && active && visible && foreground) {
      sidebarRef.current?.querySelector<HTMLInputElement>('input')?.focus()
    }
  }, [narrow, drawerOpen, active, visible, foreground])

  const closeSidebar = (): void => {
    if (narrow) {
      setDrawerOpen(false)
    } else {
      sidebarCollapsed.set(true)
    }

    sidebarToggle.current?.focus()
  }

  const closeRail = (): void => {
    setRailOpen(false)
    railToggle.current?.focus()
  }

  useEscapeKey(closeSidebar, {
    enabled: narrow && sidebarOpen && !railOpen && active && visible && foreground,
    capture: false
  })

  const companion = sessionPresetId === 'companion'
  const fixedCompanion = companion && sessionKind === 'special' && sessionId === companionSessionId

  return (
    <PanelActivityProvider active={active && visible && foreground}>
      <div className={styles.layout} data-narrow={narrow} ref={layoutRef}>
        {sidebarOpen && (
          <>
            {narrow && (
              <button
                aria-label={t.hideConversations}
                className={styles.backdrop}
                onClick={closeSidebar}
                type="button"
              />
            )}
            <div
              aria-label={narrow ? t.showConversations : undefined}
              aria-modal={narrow ? true : undefined}
              className={styles.sidebar}
              id={sidebarId}
              onKeyDown={event => {
                if (!narrow || event.key !== 'Tab') {
                  return
                }

                const focusable = [
                  ...event.currentTarget.querySelectorAll<HTMLElement>('button, input, [tabindex="0"]')
                ].filter(element => !element.hasAttribute('disabled') && element.getClientRects().length > 0)

                const first = focusable[0]
                const last = focusable.at(-1)

                if (event.shiftKey && document.activeElement === first) {
                  event.preventDefault()
                  last?.focus()
                } else if (!event.shiftKey && document.activeElement === last) {
                  event.preventDefault()
                  first?.focus()
                }
              }}
              ref={sidebarRef}
              role={narrow ? 'dialog' : undefined}
            >
              <SessionSidebar
                companionUnread={companionUnread}
                headerAction={
                  <button
                    aria-label={t.hideConversations}
                    className={styles.toolbarButton}
                    onClick={closeSidebar}
                    title={t.hideConversations}
                    type="button"
                  >
                    <ArrowLeft size={16} />
                  </button>
                }
                onSessionSelected={() => {
                  if (narrow) {
                    closeSidebar()
                  }
                }}
              />
            </div>
          </>
        )}
        <ConversationViewProvider
          active={active && !(narrow && sidebarOpen) && !railOpen}
          draftScope="main"
          foreground={foreground}
          receiveExternalAttachments={receiveExternalAttachments}
          sessionId={sessionId}
          viewId={viewId}
          visible={visible}
        >
          <MainChat
            companion={companion}
            fixedCompanion={fixedCompanion}
            headingActions={
              <button
                aria-controls={sidebarId}
                aria-expanded={sidebarOpen}
                aria-label={sidebarOpen ? t.hideConversations : t.showConversations}
                className={styles.toolbarButton}
                onClick={() => (narrow ? setDrawerOpen(!drawerOpen) : sidebarCollapsed.set(!collapsed))}
                ref={sidebarToggle}
                title={sidebarOpen ? t.hideConversations : t.showConversations}
                type="button"
              >
                <List size={18} />
              </button>
            }
            railAction={
              !companion && (
                <button
                  aria-controls={railId}
                  aria-expanded={railOpen}
                  className={styles.detailButton}
                  data-active={railOpen}
                  onClick={() => setRailOpen(!railOpen)}
                  ref={railToggle}
                  type="button"
                >
                  {t.execution}
                </button>
              )
            }
            reveal={reveal}
          />
          {railOpen && !companion && (
            <>
              <button aria-label={t.closeExecution} className={styles.backdrop} onClick={closeRail} type="button" />
              <RunRail closeLabel={t.closeExecution} id={railId} onClose={closeRail} title={t.execution} />
            </>
          )}
          <MediaViewerOverlay containerRef={containerRef} viewId={viewId} windowId={windowId} />
        </ConversationViewProvider>
      </div>
    </PanelActivityProvider>
  )
}

function MainChat({
  companion,
  fixedCompanion,
  headingActions,
  railAction,
  reveal
}: {
  companion: boolean
  fixedCompanion: boolean
  headingActions: React.ReactNode
  railAction: React.ReactNode
  reveal?: { key: string; request: number }
}): React.JSX.Element {
  const gatewayState = useStore($gatewayState)
  const readOnly = useIsReadOnlySession()
  usePendingConversationFeed()
  const navigationError = useStore($sessionNavigationError)
  const scrollRef = useRef<HTMLDivElement>(null)
  const persona = useStore($persona)
  const title = useStore($currentSessionTitle)
  const sessionId = useStore($chatSessionId)
  const presets = useStore($systemPresets)
  const runtime = getConversationRuntime(sessionId)
  const sessionPresetId = useStore(runtime.$chatSessionPresetId)
  const preset = presets.find(preset => preset.id === sessionPresetId)
  const dict = useStrings()
  const t = useConversationStrings()

  return (
    <ChatPanel
      className={styles.main}
      gatewayState={gatewayState}
      groupAvatars
      heading={
        <div className={styles.heading}>
          {headingActions}
          <div className={styles.identity}>
            <strong>{fixedCompanion ? persona?.name || title : title}</strong>
            <span>
              {readOnly ? t.readOnly : preset ? presetDisplayName(dict, preset) : companion ? t.companion : t.assistant}
            </span>
          </div>
          {railAction}
        </div>
      }
      inputWrapperClassName={styles.input}
      isReadOnlySession={readOnly}
      notice={
        navigationError && (
          <div className={styles.error} role="alert">
            <span>
              {t.loadFailed} {navigationError.error}
            </span>
            <button
              className={styles.detailButton}
              onClick={() =>
                void (navigationError.sessionId ? switchSession(navigationError.sessionId) : openMainSession())
              }
              type="button"
            >
              {t.retry}
            </button>
          </div>
        )
      }
      reveal={reveal}
      scrollRef={scrollRef}
      surfaceClassName={styles.surface}
      variant={companion ? 'companion' : 'assistant'}
    />
  )
}
