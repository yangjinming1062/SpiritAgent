import { useStore } from '@nanostores/react'
import { IconBadgeCc, IconMicrophoneOff } from '@tabler/icons-react'
import { createContext, type ReactNode, useContext, useEffect } from 'react'

import { useRealtimeCall } from '@/features/speech'
import { Mic, Phone, PhoneOff } from '@/shared/lib/icons'
import { requestResource } from '@/shared/lib/resource-request'
import { presentationPorts } from '@/shared/presentation-ports'
import { $auth } from '@/shared/store/auth'
import { $locale } from '@/shared/store/locale'

import {
  callCopy,
  CallTranscriptPage,
  formatCallDuration,
  LiveCallPage,
  useCallView
} from '../../../../shared/realtime-call-ui'

import { CompanionAvatar } from './companion-avatar'
import { useConversationView } from './conversation-view'

const OpenCallRecord = createContext<((element: HTMLElement, id: string) => void) | null>(null)

export function CallRecordButton({ id, duration }: { id: string; duration: number }): React.JSX.Element {
  const copy = callCopy[useStore($locale)]
  const open = useContext(OpenCallRecord)

  return (
    <button
      aria-label={`${copy.history} · ${formatCallDuration(duration)}`}
      className="sa-call-record"
      onClick={event => open?.(event.currentTarget, id)}
      type="button"
    >
      <Phone aria-hidden size={16} /> {copy.call} · {formatCallDuration(duration)}
    </button>
  )
}

export function CallSurface({
  children,
  className,
  enabled = true
}: {
  children: (start: ((element: HTMLButtonElement) => void) | undefined, open: boolean) => ReactNode
  className?: string
  enabled?: boolean
}): React.JSX.Element {
  const conversation = useConversationView()
  const sessionId = useStore(conversation.runtime.$chatSessionId)
  const auth = useStore($auth)
  const authId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const copy = callCopy[useStore($locale)]
  const locked = useStore(presentationPorts().$screenLocked)
  const name = useStore(presentationPorts().$companionName)
  const eligible = enabled && conversation.visible && !locked
  const call = useRealtimeCall({ sessionId, eligible })

  const view = useCallView(
    eligible && authId && sessionId ? `${authId}:${sessionId}` : null,
    call.snapshot.state === 'stopped'
  )

  const open = Boolean(eligible && view.isOpen)
  const { close, recordId } = view

  useEffect(() => {
    if (!open) {
      return
    }

    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape' && (recordId || call.snapshot.state === 'error')) {
        event.preventDefault()
        event.stopImmediatePropagation()
        close()
      }
    }

    window.addEventListener('keydown', onKeyDown, true)

    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [open, call.snapshot.state, close, recordId])

  return (
    <OpenCallRecord.Provider value={view.open}>
      <div className={`sa-call-host relative flex min-h-0 flex-1 flex-col ${className ?? ''}`}>
        {children(
          eligible && authId && sessionId
            ? element => {
                view.open(element)
                void call.start()
              }
            : undefined,
          open
        )}
        {open &&
          sessionId &&
          (recordId ? (
            <CallTranscriptPage
              close={view.close}
              copy={copy}
              key={recordId}
              load={afterId =>
                requestResource('sessions.get_call', {
                  path: { session_id: sessionId, record_id: recordId },
                  query: { after_id: afterId }
                })
              }
              name={name}
            />
          ) : (
            <LiveCallPage
              acknowledge={call.receivedPart}
              avatar={<CompanionAvatar />}
              close={view.close}
              copy={copy}
              hangup={call.hangup}
              icons={{
                mic: <Mic aria-hidden size={18} />,
                muted: <IconMicrophoneOff aria-hidden size={18} />,
                captions: <IconBadgeCc aria-hidden size={18} />,
                hangup: <PhoneOff aria-hidden size={18} />
              }}
              levels={call.levels}
              mute={call.toggleMute}
              name={name}
              snapshot={call.snapshot}
            />
          ))}
      </div>
    </OpenCallRecord.Provider>
  )
}
