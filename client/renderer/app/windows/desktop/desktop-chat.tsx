import { useStore } from '@nanostores/react'
import type React from 'react'
import { type RefObject, useEffect, useRef, useState } from 'react'

import { RunRail } from '@/app/features/workbench/run-rail'
import { SessionSidebar } from '@/app/features/workbench/session-sidebar'
import { $persona } from '@/modules/character'
import {
  $chatSessionId,
  $currentSessionTitle,
  $sessions,
  ChatPanel,
  ConversationInput,
  ConversationSurface,
  ConversationViewProvider,
  isCompanionSession,
  useChatInput,
  useIsReadOnlySession
} from '@/modules/conversation'
import { MediaViewerOverlay } from '@/modules/media'
import { PanelActivityProvider } from '@/shared/context/panel-activity'
import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { ArrowLeft, List } from '@/shared/lib/icons'
import { definePersistedBoolean, registerCompanionStorageKey } from '@/shared/lib/storage'
import { $gatewayState } from '@/shared/store/gateway'

import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

const CHAT_SIDEBAR_COLLAPSED_KEY = registerCompanionStorageKey('da.desktop.chatSidebarCollapsed')
const sidebarCollapsed = definePersistedBoolean({ key: CHAT_SIDEBAR_COLLAPSED_KEY, fallback: false })

export function DesktopChat({
  active,
  visible,
  foreground,
  companionSessionId,
  companionUnread,
  reveal,
  containerRef
}: {
  active: boolean
  visible: boolean
  foreground: boolean
  companionSessionId: string | null
  companionUnread: boolean
  reveal?: { key: string; request: number }
  containerRef: RefObject<HTMLElement | null>
}): React.JSX.Element {
  const sessionId = useStore($chatSessionId)
  const sessions = useStore($sessions)
  const current = sessions.find(session => session.id === sessionId)
  const collapsed = useStore(sidebarCollapsed.$atom)
  const [narrow, setNarrow] = useState(false)
  const layoutRef = useRef<HTMLDivElement>(null)
  const sidebarToggle = useRef<HTMLButtonElement>(null)
  const t = useDesktopStrings()

  useEffect(() => {
    const element = layoutRef.current

    if (!element) {
      return
    }

    const update = (): void => setNarrow(element.clientWidth < 760)
    const observer = new ResizeObserver(update)
    observer.observe(element)
    update()

    return () => observer.disconnect()
  }, [])

  const collapseSidebar = (): void => {
    sidebarCollapsed.set(true)
    sidebarToggle.current?.focus()
  }

  useEscapeKey(collapseSidebar, { enabled: narrow && !collapsed && active, capture: false })

  const companion = Boolean(
    (companionSessionId && sessionId === companionSessionId) ||
    (current?.kind === 'special' && isCompanionSession(current))
  )

  return (
    <div className={styles.chatLayout} data-narrow={narrow} ref={layoutRef}>
      {!collapsed && (
        <>
          {narrow && (
            <button
              aria-label={t.hideConversations}
              className={styles.chatSidebarBackdrop}
              onClick={collapseSidebar}
              type="button"
            />
          )}
          <div className={styles.sessionSidebar} id="desktop-conversations">
            <SessionSidebar
              companionUnread={companionUnread}
              headerAction={
                <button
                  aria-label={t.hideConversations}
                  className={styles.chatToolbarButton}
                  onClick={collapseSidebar}
                  title={t.hideConversations}
                  type="button"
                >
                  <ArrowLeft size={16} />
                </button>
              }
              includeCompanion
            />
          </div>
        </>
      )}
      <ConversationViewProvider
        active={active}
        foreground={foreground}
        sessionId={sessionId}
        viewId="desktop-main"
        visible={visible}
      >
        <MainChat
          companion={companion}
          headingActions={
            <>
              <button
                aria-controls="desktop-conversations"
                aria-expanded={!collapsed}
                aria-label={collapsed ? t.showConversations : t.hideConversations}
                className={styles.chatToolbarButton}
                onClick={() => sidebarCollapsed.set(!collapsed)}
                ref={sidebarToggle}
                title={collapsed ? t.showConversations : t.hideConversations}
                type="button"
              >
                <List size={18} />
              </button>
            </>
          }
          reveal={companion ? reveal : undefined}
        />
        <MediaViewerOverlay containerRef={containerRef} viewId="desktop-main" windowId={1} />
      </ConversationViewProvider>
      {!companion && <RunRail />}
    </div>
  )
}

function MainChat({
  companion,
  headingActions,
  reveal
}: {
  companion: boolean
  headingActions: React.ReactNode
  reveal?: { key: string; request: number }
}): React.JSX.Element {
  const gatewayState = useStore($gatewayState)
  const readOnly = useIsReadOnlySession()
  const scrollRef = useRef<HTMLDivElement>(null)
  const persona = useStore($persona)
  const title = useStore($currentSessionTitle)
  const t = useDesktopStrings()

  return (
    <ChatPanel
      className={styles.chatMain}
      gatewayState={gatewayState}
      groupAvatars
      heading={
        <div className={styles.chatHeading}>
          {headingActions}
          <div className={styles.chatIdentity}>
            <strong>{companion ? persona?.name || title : title}</strong>
            <span>{readOnly ? t.readOnlyConversation : companion ? t.companionConversation : t.workConversation}</span>
          </div>
        </div>
      }
      inputWrapperClassName={styles.chatInput}
      isReadOnlySession={readOnly}
      reveal={reveal}
      scrollRef={scrollRef}
      surfaceClassName={styles.chatSurface}
      variant={companion ? 'living' : 'workbench'}
    />
  )
}

export function DesktopWhisper({
  sessionId,
  active,
  visible,
  foreground,
  containerRef
}: {
  sessionId: string | null
  active: boolean
  visible: boolean
  foreground: boolean
  containerRef: RefObject<HTMLElement | null>
}): React.JSX.Element {
  return (
    <PanelActivityProvider active={active && visible && foreground}>
      <ConversationViewProvider
        active={active}
        foreground={foreground}
        receiveExternalAttachments
        sessionId={sessionId}
        viewId="desktop-whisper"
        visible={visible}
      >
        <WhisperChat />
        <MediaViewerOverlay containerRef={containerRef} viewId="desktop-whisper" windowId={1} />
      </ConversationViewProvider>
    </PanelActivityProvider>
  )
}

function WhisperChat(): React.JSX.Element {
  const gatewayState = useStore($gatewayState)
  const scrollRef = useRef<HTMLDivElement>(null)
  const input = useChatInput({ gatewayState, isReadOnlySession: false })

  return (
    <>
      <ConversationSurface className={styles.whisperMessages} groupAvatars scrollRef={scrollRef} variant="living" />
      <div className={styles.whisperInput}>
        <ConversationInput {...input.inputProps} variant="living" />
      </div>
    </>
  )
}
