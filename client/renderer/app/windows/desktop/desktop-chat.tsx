import { useStore } from '@nanostores/react'
import type React from 'react'
import { type RefObject, useRef } from 'react'

import { ConversationInput, ConversationSurface, ConversationViewProvider, useChatInput } from '@/modules/conversation'
import { MediaViewerOverlay } from '@/modules/media'
import { PanelActivityProvider } from '@/shared/context/panel-activity'
import { $gatewayState } from '@/shared/store/gateway'

import styles from './desktop.module.css'

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
      <ConversationSurface className={styles.whisperMessages} groupAvatars scrollRef={scrollRef} variant="companion" />
      <div className={styles.whisperInput}>
        <ConversationInput {...input.inputProps} variant="companion" />
      </div>
    </>
  )
}
