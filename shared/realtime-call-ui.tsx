import './realtime-call.css'

import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react'

import type { ApiOperations } from './api.generated'
import type { CallLevels } from './realtime-audio'
import type { CallPart, RealtimeCallSnapshot } from './realtime-call'

export type CallRecordPage = ApiOperations['sessions.get_call']['response']

export const callCopy = {
  zh: {
    call: '通话',
    back: '返回聊天',
    history: '通话记录',
    loading: '正在读取通话记录…',
    historyLoadFailed: '无法读取通话记录',
    retry: '重试',
    more: '查看更多',
    empty: '这次通话没有已确认的发言',
    user: '你',
    companion: '伙伴',
    mute: '静音',
    unmute: '取消静音',
    captions: '字幕',
    hangup: '挂断',
    cancel: '取消拨号',
    connecting: '正在连接…',
    listening: '正在倾听',
    thinking: '正在思考',
    speaking: '正在讲话',
    muted: '麦克风已静音',
    ending: '正在结束…',
    error: '通话已中断'
  },
  en: {
    call: 'Call',
    back: 'Back to chat',
    history: 'Call transcript',
    loading: 'Loading transcript…',
    historyLoadFailed: 'Could not load the call transcript',
    retry: 'Retry',
    more: 'Load more',
    empty: 'No confirmed speech in this call',
    user: 'You',
    companion: 'Companion',
    mute: 'Mute',
    unmute: 'Unmute',
    captions: 'Captions',
    hangup: 'Hang up',
    cancel: 'Cancel call',
    connecting: 'Connecting…',
    listening: 'Listening',
    thinking: 'Thinking',
    speaking: 'Speaking',
    muted: 'Microphone muted',
    ending: 'Ending…',
    error: 'Call interrupted'
  }
}

type Copy = typeof callCopy.zh

export function formatCallDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor(seconds / 60) % 60
  const tail = [minutes, seconds % 60].map(value => String(value).padStart(2, '0')).join(':')
  return hours ? `${hours}:${tail}` : tail
}

export function useCallView(scope: string | null, stopped: boolean) {
  const [view, setView] = useState<{
    scope: string | null
    recordId?: string
  } | null>(null)
  const trigger = useRef<HTMLElement | null>(null)
  const isOpen = scope !== null && view !== null && view.scope === scope
  const recordId = isOpen ? view.recordId : undefined

  const open = useCallback(
    (element: HTMLElement, id?: string): void => {
      if (scope === null) {
        return
      }
      trigger.current = element
      setView({ scope, recordId: id })
    },
    [scope]
  )

  const close = useCallback((): void => {
    setView(null)
    requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true }))
  }, [])

  useEffect(() => {
    if (view && (view.scope !== scope || (view.recordId === undefined && stopped))) {
      close()
    }
  }, [close, scope, stopped, view])

  return { isOpen, recordId, open, close }
}

const BAR_HEIGHTS = [0.28, 0.43, 0.57, 0.82, 1, 0.73, 0.93, 0.66, 0.48, 0.34, 0.22]

function CallWave({ levels, enabled }: { levels: () => CallLevels; enabled: boolean }) {
  const wave = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = wave.current
    if (!enabled || !node) {
      return
    }

    const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)')
    let previous = 0
    const timer = setInterval(() => {
      if (reduceMotion.matches) {
        return
      }
      const { microphone, playback } = levels()
      const source = microphone >= 0.018 ? 'mic' : 'output'
      const target = Math.min(1, Math.max(0, (source === 'mic' ? microphone : playback) - 0.008) * 7)
      previous = Math.max(target, previous * 0.65)
      node.dataset.source = source
      node.style.setProperty('--call-level', String(0.12 + previous * 0.88))
    }, 50)
    return () => {
      clearInterval(timer)
      node.style.removeProperty('--call-level')
    }
  }, [enabled, levels])

  return (
    <div aria-hidden="true" className="sa-call-wave" ref={wave}>
      {BAR_HEIGHTS.map((height, index) => (
        <span key={index} style={{ height: `${height * 100}%` }} />
      ))}
    </div>
  )
}

export function LiveCallPage({
  avatar,
  name,
  copy,
  snapshot,
  levels,
  mute,
  hangup,
  close,
  acknowledge,
  icons
}: {
  avatar: ReactNode
  name: string
  copy: Copy
  snapshot: RealtimeCallSnapshot
  levels: () => CallLevels
  mute: () => void
  hangup: () => void
  close: () => void
  acknowledge: (part: CallPart) => void
  icons: {
    mic: ReactNode
    muted: ReactNode
    captions: ReactNode
    hangup: ReactNode
  }
}) {
  const [captions, setCaptions] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const heading = useRef<HTMLHeadingElement>(null)
  const forcedCaption = useRef<HTMLParagraphElement>(null)
  const { connectedAt, state, muted, part, error } = snapshot
  const failed = state === 'error'
  const ending = state === 'ending'

  useEffect(() => heading.current?.focus(), [])
  useEffect(() => {
    if (connectedAt === null || failed || ending) {
      return
    }
    const update = () => setElapsed(performance.now() - connectedAt)
    update()
    const timer = setInterval(update, 250)
    return () => clearInterval(timer)
  }, [connectedAt, failed, ending])

  useEffect(() => {
    if (!part || failed || ending) {
      return
    }
    let next = 0
    // 回执只代表强制字幕已经进入可见帧。
    const frame = requestAnimationFrame(() => {
      next = requestAnimationFrame(() => {
        if (!document.hidden && forcedCaption.current?.getClientRects().length) {
          acknowledge(part)
        }
      })
    })
    return () => {
      cancelAnimationFrame(frame)
      cancelAnimationFrame(next)
    }
  }, [part, failed, ending, acknowledge])

  const status = state === 'idle' || state === 'stopped' ? copy.connecting : copy[state]

  return (
    <section aria-label={copy.call} className="sa-call-page">
      <div className="sa-call-center">
        <div className="sa-call-avatar">{avatar}</div>
        <h2 ref={heading} tabIndex={-1}>
          {name || copy.companion}
        </h2>
        <p className="sa-call-state" role="status">
          {status}
          {muted && !failed && !ending ? ` · ${copy.muted}` : ''}
        </p>
        <CallWave enabled={connectedAt !== null && !ending && !failed} levels={levels} />
        {connectedAt !== null && <time>{formatCallDuration(elapsed)}</time>}
        {!failed && !ending && (part || captions) && (
          <div className="sa-call-captions">
            {captions && snapshot.transcript && (
              <p>
                <strong>{copy.user}</strong> {snapshot.transcript}
              </p>
            )}
            {part ? (
              <p ref={forcedCaption} role="status">
                <strong>{name || copy.companion}</strong> {part.text}
              </p>
            ) : (
              captions &&
              snapshot.assistant && (
                <p>
                  <strong>{name || copy.companion}</strong> {snapshot.assistant}
                </p>
              )
            )}
          </div>
        )}
        {error && (
          <p className="sa-call-error" role={failed ? 'alert' : 'status'}>
            {error}
          </p>
        )}
      </div>
      <footer className="sa-call-controls">
        {failed ? (
          <button onClick={close} type="button">
            {copy.back}
          </button>
        ) : (
          <>
            <button aria-pressed={muted} disabled={connectedAt === null || ending} onClick={mute} type="button">
              {muted ? icons.muted : icons.mic}
              <span>{muted ? copy.unmute : copy.mute}</span>
            </button>
            <button aria-pressed={captions} disabled={ending} onClick={() => setCaptions(!captions)} type="button">
              {icons.captions}
              <span>{copy.captions}</span>
            </button>
            <button className="sa-call-hangup" disabled={ending} onClick={hangup} type="button">
              {icons.hangup}
              <span>{connectedAt === null ? copy.cancel : copy.hangup}</span>
            </button>
          </>
        )}
      </footer>
    </section>
  )
}

export function CallTranscriptPage({
  copy,
  name,
  load,
  close
}: {
  copy: Copy
  name: string
  load: (afterId: string | null, signal: AbortSignal) => Promise<CallRecordPage>
  close: () => void
}) {
  const [page, setPage] = useState<CallRecordPage | null>(null)
  const [error, setError] = useState(false)
  const [request, setRequest] = useState<{ afterId: string | null } | null>({
    afterId: null
  })
  const back = useRef<HTMLButtonElement>(null)
  const latestLoad = useRef(load)
  latestLoad.current = load

  useEffect(() => back.current?.focus(), [])
  useEffect(() => {
    if (!request) {
      return
    }
    const controller = new AbortController()
    void latestLoad
      .current(request.afterId, controller.signal)
      .then(result => {
        if (!controller.signal.aborted) {
          setPage(previous => ({
            ...result,
            utterances: request.afterId && previous ? [...previous.utterances, ...result.utterances] : result.utterances
          }))
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError(true)
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setRequest(null)
        }
      })
    return () => controller.abort()
  }, [request])

  return (
    <section aria-label={copy.history} className="sa-call-page sa-call-transcript">
      <header>
        <button onClick={close} ref={back} type="button">
          {copy.back}
        </button>
        <h2>{copy.history}</h2>
        {page && (
          <p>
            <time dateTime={page.started_at}>{new Date(page.started_at).toLocaleString()}</time>
            {' · '}
            {formatCallDuration(page.duration_ms)}
          </p>
        )}
      </header>
      <div className="sa-call-transcript-lines">
        {page?.utterances.map((line, index) => (
          <p key={index}>
            <strong>{line.role === 'user' ? copy.user : name || copy.companion}</strong>
            {line.text}
          </p>
        ))}
        {page && !page.utterances.length && !page.next_cursor && <p>{copy.empty}</p>}
        {request && <p role="status">{copy.loading}</p>}
        {error && <p role="alert">{copy.historyLoadFailed}</p>}
        {!request && (error || page?.next_cursor) && (
          <button
            onClick={() => {
              setError(false)
              setRequest({ afterId: page?.next_cursor ?? null })
            }}
            type="button"
          >
            {error ? copy.retry : copy.more}
          </button>
        )}
      </div>
    </section>
  )
}
