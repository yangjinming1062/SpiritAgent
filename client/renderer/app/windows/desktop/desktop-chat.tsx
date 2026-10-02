import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef } from 'react'

import { RunRail } from '@/app/features/workbench/run-rail'
import { SessionSidebar } from '@/app/features/workbench/session-sidebar'
import {
  $chatSessionId,
  $sessions,
  ChatPanel,
  ConversationInput,
  ConversationSurface,
  ConversationViewProvider,
  isCompanionSession,
  useChatInput,
  useIsReadOnlySession
} from '@/modules/conversation'
import { PanelActivityProvider } from '@/shared/context/panel-activity'
import { $gatewayState } from '@/shared/store/gateway'

import styles from './desktop.module.css'

export function DesktopChat({
  active,
  visible,
  foreground,
  companionSessionId
}: {
  active: boolean
  visible: boolean
  foreground: boolean
  companionSessionId: string | null
}): React.JSX.Element {
  const sessionId = useStore($chatSessionId)
  const sessions = useStore($sessions)
  const current = sessions.find(session => session.id === sessionId)

  const companion = Boolean(
    (companionSessionId && sessionId === companionSessionId) ||
    (current?.kind === 'special' && isCompanionSession(current))
  )

  return (
    <div className={styles.chatLayout}>
      <div className={styles.sessionSidebar}>
        <SessionSidebar includeCompanion />
      </div>
      <ConversationViewProvider
        active={active}
        foreground={foreground}
        sessionId={sessionId}
        viewId="desktop-main"
        visible={visible}
      >
        <MainChat companion={companion} />
      </ConversationViewProvider>
      {!companion && <RunRail />}
    </div>
  )
}

function MainChat({ companion }: { companion: boolean }): React.JSX.Element {
  const gatewayState = useStore($gatewayState)
  const readOnly = useIsReadOnlySession()
  const scrollRef = useRef<HTMLDivElement>(null)

  return (
    <ChatPanel
      className={styles.chatMain}
      gatewayState={gatewayState}
      inputWrapperClassName={styles.chatInput}
      isReadOnlySession={readOnly}
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
  foreground
}: {
  sessionId: string | null
  active: boolean
  visible: boolean
  foreground: boolean
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
      <ConversationSurface className={styles.whisperMessages} scrollRef={scrollRef} variant="living" />
      <div className={styles.whisperInput}>
        <ConversationInput {...input.inputProps} variant="living" />
      </div>
    </>
  )
}
