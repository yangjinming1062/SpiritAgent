import { IconArrowLeft, IconMicrophone, IconPhoto, IconPlus, IconSend, IconSquare, IconX } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { GatewayEvent, RemoteApi, RemoteGateway } from './api'
import { errorText, readImage, record } from './api'
import { advanceTurn, commandHistory, eventsAfterSnapshot, mergeMessages } from './chat-state'
import type {
  ActiveTurn,
  ChatAttachment,
  CompanionBubble,
  History,
  Resume,
  SessionInfo,
  SessionMessage,
  SystemPresetSummary
} from './types'
import { Empty, Media, Notice, PageHeader, pauseMedia, timestamp, useAlive, useTask } from './ui'

interface PendingPrompt {
  id: string
  text: string
  attachments: ChatAttachment[]
  retry?: number
}

function readPendingPrompt(key: string): PendingPrompt | null {
  const stored = sessionStorage.getItem(key)

  if (stored === null) {
    return null
  }

  const value: unknown = JSON.parse(stored)

  if (
    !record(value) ||
    typeof value.id !== 'string' ||
    typeof value.text !== 'string' ||
    !Array.isArray(value.attachments) ||
    (value.retry !== undefined &&
      (typeof value.retry !== 'number' || !Number.isInteger(value.retry) || value.retry <= 0))
  ) {
    throw new Error('待确认的消息记录无法读取，请核对会话后重新登录')
  }

  const attachments: ChatAttachment[] = value.attachments.map((attachment: unknown) => {
    if (
      !record(attachment) ||
      (attachment.type !== 'image' && attachment.type !== 'video') ||
      typeof attachment.url !== 'string'
    ) {
      throw new Error('待确认的消息附件无法读取，请核对会话后重新登录')
    }

    return { type: attachment.type, url: attachment.url }
  })

  return {
    id: value.id,
    text: value.text,
    attachments,
    retry: typeof value.retry === 'number' ? value.retry : undefined
  }
}

export function clearChatState(): void {
  for (const key of Object.keys(sessionStorage)) {
    if (/^remote:\d+:\d+:submission:/.test(key)) {
      sessionStorage.removeItem(key)
    }
  }
}

export function Chat({
  api,
  gateway,
  connected,
  refresh,
  userId,
  device
}: {
  api: RemoteApi
  gateway: RemoteGateway
  connected: boolean
  refresh: number
  userId: number
  device: number
}): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [sessionTotal, setSessionTotal] = useState(0)

  const [seenAt, setSeenAt] = useState<Record<string, number>>(() => {
    try {
      const stored: unknown = JSON.parse(localStorage.getItem(`remote:${userId}:${device}:read`) ?? '{}')

      return record(stored)
        ? Object.fromEntries(
            Object.entries(stored).filter((entry): entry is [string, number] => typeof entry[1] === 'number')
          )
        : {}
    } catch {
      return {}
    }
  })

  const [presets, setPresets] = useState<SystemPresetSummary[]>([])
  const [preset, setPreset] = useState('companion')
  const [showCreate, setShowCreate] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [mainId, setMainId] = useState('')
  const metadataLoaded = useRef(false)
  const listRefreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [messages, setMessages] = useState<SessionMessage[]>([])
  const [turn, setTurn] = useState<ActiveTurn | null>(null)
  const [readOnly, setReadOnly] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<ChatAttachment[]>([])
  const attachmentsRef = useRef(attachments)
  attachmentsRef.current = attachments
  const [failure, setFailure] = useState('')
  const [retryMessage, setRetryMessage] = useState<number | null>(null)
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const selectedRef = useRef(selected)
  selectedRef.current = selected
  const navigation = useRef(0)
  const hydration = useRef<{
    sessionId: string
    revision: number
    events: GatewayEvent[]
  } | null>(null)
  const applyEvent = useRef<(event: GatewayEvent) => void>(() => {})
  const listRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const recordingTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const recordingRevision = useRef(0)
  const pending = useRef<PendingPrompt | null>(null)
  const draftKey = (id: string) => `remote:${userId}:${device}:draft:${id}`
  const promptKey = (id: string) => `remote:${userId}:${device}:submission:${id}`

  const releaseRecording = useCallback(() => {
    recordingRevision.current++
    clearTimeout(recordingTimer.current)

    if (recorder.current && recorder.current.state !== 'inactive') {
      recorder.current.onstop = null

      try {
        recorder.current.stop()
      } catch (failure) {
        console.warn('录音停止失败', errorText(failure))
      }
    }

    stream.current?.getTracks().forEach(track => track.stop())
    stream.current = null
    recorder.current = null
    if (alive()) {
      setRecording(false)
      setTranscribing(false)
    }
  }, [alive])

  useEffect(() => {
    const hidden = () => {
      if (document.visibilityState !== 'visible') {
        releaseRecording()
      }
    }

    document.addEventListener('visibilitychange', hidden)

    return () => {
      document.removeEventListener('visibilitychange', hidden)
      releaseRecording()
    }
  }, [releaseRecording])

  const loadSessions = useCallback(
    async (offset = 0) => {
      const result = await api.request<{
        sessions: SessionInfo[]
        total: number
      }>(`/api/sessions?limit=100&offset=${offset}`)

      if (alive()) {
        setSessions(previous =>
          offset
            ? [...previous, ...result.sessions.filter(session => !previous.some(old => old.id === session.id))]
            : result.sessions
        )
        setSessionTotal(result.total)
      }
    },
    [api, alive]
  )

  const scheduleListRefresh = useCallback(() => {
    if (listRefreshTimer.current === undefined) {
      listRefreshTimer.current = setTimeout(() => {
        listRefreshTimer.current = undefined
        void runLatest(() => loadSessions())
      }, 200)
    }
  }, [loadSessions, runLatest])

  const resume = useCallback(
    async (id: string) => {
      const revision = ++navigation.current
      const buffered = {
        sessionId: id,
        revision,
        events: [] as GatewayEvent[]
      }
      hydration.current = buffered

      try {
        const result = await gateway.request<Resume>('session.resume', {
          session_id: id
        })

        if (!alive() || revision !== navigation.current || selectedRef.current !== id) {
          return
        }

        const incoming = mergeMessages(result.messages, result.active_turn?.messages ?? [])
        const firstId = incoming[0]?.id ?? 0
        // 快照已覆盖的全量替换仍使旧分页失效，不能重放它的旧正文。
        const historyReplaced = buffered.events.some(
          event => event.seq !== undefined && event.seq <= (result.current_seq ?? 0) && commandHistory(event) !== null
        )
        setMessages(previous =>
          result.truncated && !historyReplaced
            ? mergeMessages(
                previous.filter(message => (message.id ?? 0) < firstId),
                incoming
              )
            : incoming
        )
        setTurn(result.active_turn ?? null)
        const receipt = result.last_submission

        if (!result.active_turn && receipt && (receipt.status === 'failed' || receipt.status === 'interrupted')) {
          setFailure(receipt.error || '上一回合已中断，请核对会话后重试')
          setRetryMessage(receipt.retry_message_id ?? null)
        }

        const pendingKey = `remote:${userId}:${device}:submission:${id}`
        const prior = readPendingPrompt(pendingKey)

        if (prior && (receipt?.request_id === prior.id || result.active_turn?.request_id === prior.id)) {
          sessionStorage.removeItem(pendingKey)
          pending.current = null
          setDraft(value => (value.trim() === prior.text ? '' : value))
          setAttachments(value =>
            value.length === prior.attachments.length &&
            value.every(
              (attachment, index) =>
                attachment.type === prior.attachments[index].type && attachment.url === prior.attachments[index].url
            )
              ? []
              : value
          )
          sessionStorage.removeItem(`remote:${userId}:${device}:draft:${id}`)
        }
        setReadOnly(result.info?.is_automation === true)
        setHasMore(result.truncated === true || Boolean(result.next_cursor))
        if (document.visibilityState === 'visible') {
          setSeenAt(previous => {
            const next = { ...previous, [id]: Date.now() / 1000 }
            localStorage.setItem(`remote:${userId}:${device}:read`, JSON.stringify(next))

            return next
          })
        }
        hydration.current = null
        eventsAfterSnapshot(buffered.events, result).forEach(event => applyEvent.current(event))
      } finally {
        if (hydration.current === buffered) {
          hydration.current = null

          if (alive() && selectedRef.current === id && revision === navigation.current) {
            buffered.events.forEach(event => applyEvent.current(event))
          }
        }
      }
    },
    [alive, gateway, userId, device]
  )

  useEffect(() => {
    void runLatest(async () => {
      await loadSessions()

      if (gateway.open && !metadataLoaded.current) {
        const [main, catalog] = await Promise.all([
          gateway.request<Resume>('session.get_main'),
          gateway.request<{ presets: SystemPresetSummary[] }>('system.list_presets')
        ])

        if (alive()) {
          setMainId(main.session_id)
          setPresets(catalog.presets)
          metadataLoaded.current = true
        }
      }

      if (gateway.open && selectedRef.current) {
        await resume(selectedRef.current)
      }
    })
  }, [refresh, connected, gateway, loadSessions, resume, alive, runLatest])

  applyEvent.current = event => {
    const payload = record(event.payload) ? event.payload : {}
    const sessionId = event.session_id ?? (typeof payload.session_id === 'string' ? payload.session_id : undefined)

    if (sessionId !== selectedRef.current) {
      return
    }

    setTurn(previous => advanceTurn(previous, event))

    const replacement = commandHistory(event)

    if (replacement !== null) {
      setMessages(replacement)
      setHasMore(false)
      scheduleListRefresh()
    } else if (event.type === 'message.start') {
      setFailure('')
      setRetryMessage(null)
    } else if (event.type === 'message.persisted' && Array.isArray(payload.messages)) {
      setMessages(previous => mergeMessages(previous, payload.messages as SessionMessage[]))

      if (payload.request_id === pending.current?.id) {
        pending.current = null

        if (selectedRef.current) {
          sessionStorage.removeItem(promptKey(selectedRef.current))
          sessionStorage.removeItem(draftKey(selectedRef.current))
        }

        setDraft('')
        setAttachments([])
        setFailure('')
      }
    } else if (event.type === 'message.complete') {
      if (selectedRef.current) {
        if (payload.request_id === pending.current?.id) {
          pending.current = null
          sessionStorage.removeItem(promptKey(selectedRef.current))
          sessionStorage.removeItem(draftKey(selectedRef.current))
        }
        const id = selectedRef.current
        void runLatest(() => resume(id))
      }

      scheduleListRefresh()
    } else if (event.type === 'session.state' && payload.running === false) {
      if (typeof payload.error === 'string') {
        setFailure(payload.error)
      }
    } else if (event.type === 'error') {
      setFailure(typeof payload.message === 'string' ? payload.message : '回复暂时未完成')
      setRetryMessage(typeof payload.retry_message_id === 'number' ? payload.retry_message_id : null)
    } else if (
      event.type === 'message.media' ||
      event.type === 'message.voice' ||
      event.type === 'message.edited' ||
      event.type === 'message.deleted'
    ) {
      if (selectedRef.current) {
        const id = selectedRef.current
        void runLatest(() => resume(id))
      }
    }
  }

  useEffect(
    () =>
      gateway.subscribeEvent(event => {
        const payload = record(event.payload) ? event.payload : {}
        const sessionId = event.session_id ?? (typeof payload.session_id === 'string' ? payload.session_id : undefined)

        if (hydration.current && sessionId === hydration.current.sessionId) {
          hydration.current.events.push(event)
        } else {
          applyEvent.current(event)
        }
      }),
    [gateway]
  )

  useEffect(() => {
    const remove = gateway.subscribeEvent(event => {
      if (event.type !== 'session.list_changed') {
        return
      }

      const payload = record(event.payload) ? event.payload : {}

      if (payload.deleted === true && payload.session_id === selectedRef.current) {
        navigation.current++
        selectedRef.current = null
        setSelected(null)
        releaseRecording()
      }

      scheduleListRefresh()
    })

    return () => {
      clearTimeout(listRefreshTimer.current)
      remove()
    }
  }, [gateway, releaseRecording, scheduleListRefresh])

  useEffect(() => {
    const list = listRef.current

    if (list && list.scrollHeight - list.scrollTop - list.clientHeight < 260) {
      list.scrollTop = list.scrollHeight
    }
  }, [messages, turn])

  const openSession = async (id: string) => {
    releaseRecording()
    selectedRef.current = id
    setSelected(id)
    setMessages([])
    setTurn(null)
    const prior = readPendingPrompt(promptKey(id))
    setDraft(prior?.text ?? sessionStorage.getItem(draftKey(id)) ?? '')
    setAttachments(prior?.attachments ?? [])
    setFailure('')
    setRetryMessage(null)
    pending.current = prior
    await resume(id)
    requestAnimationFrame(() => {
      if (listRef.current) {
        listRef.current.scrollTop = listRef.current.scrollHeight
      }
    })
  }

  const submit = async (retry?: number) => {
    const id = selectedRef.current

    if (!id || !connected || readOnly || turn?.running) {
      return
    }

    const request = pending.current ?? {
      id: crypto.randomUUID(),
      text: draft.trim(),
      attachments,
      retry
    }

    if (!request.text && request.attachments.length === 0 && !request.retry) {
      return
    }

    if (request.attachments.reduce((sum, attachment) => sum + attachment.url.length, 0) > 10 * 1024 * 1024) {
      throw new Error('图片总大小过大，请减少图片或缩小后再发送')
    }

    try {
      sessionStorage.setItem(promptKey(id), JSON.stringify(request))
    } catch (failure) {
      throw new Error('无法保存待确认的发送记录，请减少图片大小或检查浏览器存储权限', { cause: failure })
    }
    pending.current = request
    setFailure('')

    try {
      const result = await gateway.request<{ status?: string; error?: string }>('prompt.submit', {
        session_id: id,
        request_id: request.id,
        ...(request.retry
          ? { retry_message_id: request.retry }
          : {
              text: request.text,
              attachments: request.attachments.map(attachment => ({
                type: attachment.type,
                file_url: attachment.url
              }))
            }),
        response_preference: 'text'
      })

      if (readPendingPrompt(promptKey(id))?.id === request.id) {
        sessionStorage.removeItem(promptKey(id))
      }
      if (sessionStorage.getItem(draftKey(id))?.trim() === request.text) {
        sessionStorage.removeItem(draftKey(id))
      }

      if (alive() && selectedRef.current === id) {
        if (pending.current?.id === request.id) {
          pending.current = null
          setDraft(value => (value.trim() === request.text ? '' : value))
          setAttachments(value => (value === request.attachments ? [] : value))
          sessionStorage.removeItem(draftKey(id))
        }

        if (result.status === 'failed' || result.status === 'interrupted') {
          setFailure(result.error || '上一回合已结束，请查看会话后重新提交')
        }

        await resume(id)
      }
    } catch (failure) {
      if (alive() && selectedRef.current === id && pending.current?.id === request.id) {
        setFailure(errorText(failure))
      }
    }
  }

  const recordVoice = async () => {
    if (recording && recorder.current) {
      recorder.current.stop()

      return
    }

    releaseRecording()
    pauseMedia()
    const revision = recordingRevision.current
    const id = selectedRef.current
    const microphone = await navigator.mediaDevices.getUserMedia({
      audio: true
    })

    if (!alive() || revision !== recordingRevision.current || id !== selectedRef.current) {
      microphone.getTracks().forEach(track => track.stop())

      return
    }

    stream.current = microphone

    const format = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(value =>
      MediaRecorder.isTypeSupported(value)
    )

    let media: MediaRecorder

    try {
      media = new MediaRecorder(microphone, format ? { mimeType: format } : undefined)
    } catch (failure) {
      releaseRecording()
      throw failure
    }
    recorder.current = media
    const chunks: Blob[] = []

    media.ondataavailable = event => {
      if (event.data.size > 0) {
        chunks.push(event.data)
      }
    }

    media.onstop = () => {
      clearTimeout(recordingTimer.current)

      if (!alive() || revision !== recordingRevision.current || id !== selectedRef.current) {
        microphone.getTracks().forEach(track => track.stop())

        return
      }

      recorder.current = null
      stream.current = null
      microphone.getTracks().forEach(track => track.stop())
      setRecording(false)
      setTranscribing(true)
      const form = new FormData()
      form.append(
        'audio_file',
        new Blob(chunks, { type: media.mimeType }),
        media.mimeType.includes('mp4') ? 'recording.m4a' : 'recording.webm'
      )
      void api
        .request<{ text: string }>('/api/media/stt', 'POST', form)
        .then(result => {
          if (alive() && revision === recordingRevision.current && id === selectedRef.current) {
            setDraft(previous => previous + (previous ? '\n' : '') + result.text)
          }
        })
        .catch(failure => {
          if (alive() && revision === recordingRevision.current) {
            setFailure(errorText(failure))
          }
        })
        .finally(() => {
          if (alive() && revision === recordingRevision.current) {
            setTranscribing(false)
          }
        })
    }

    media.onerror = () => {
      if (alive() && revision === recordingRevision.current) {
        releaseRecording()
        setFailure('录音失败，请重新录制')
      }
    }

    try {
      media.start()
    } catch (failure) {
      releaseRecording()
      throw failure
    }

    setRecording(true)
    recordingTimer.current = setTimeout(() => {
      if (media.state === 'recording') {
        media.stop()
      }
    }, 60_000)
  }

  if (!selected) {
    const listed = sessions.filter(session => session.id !== mainId)

    return (
      <div className="page">
        <PageHeader
          action={
            <button
              aria-label="新建会话"
              className="icon-button"
              onClick={() => setShowCreate(value => !value)}
              type="button"
            >
              <IconPlus />
            </button>
          }
          subtitle="无论在哪里，都能继续聊下去"
          title="消息"
        />
        {error ? <Notice>{error}</Notice> : null}
        {showCreate ? (
          <section className="card">
            <h3>新建工作会话</h3>
            <select aria-label="选择预设" onChange={event => setPreset(event.target.value)} value={preset}>
              {presets
                .filter(item => item.id !== 'companion')
                .map(item => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
            </select>
            <button
              className="primary full"
              disabled={!connected || busy}
              onClick={() =>
                run(async () => {
                  const result = await gateway.request<{ session_id: string }>('session.create', {
                    system_preset_id:
                      preset === 'companion' ? presets.find(item => item.id !== 'companion')?.id : preset
                  })

                  await loadSessions()
                  setShowCreate(false)
                  await openSession(result.session_id)
                })
              }
              type="button"
            >
              开始新会话
            </button>
          </section>
        ) : null}
        {mainId ? (
          <button
            className="companion-chat"
            disabled={!connected || busy}
            onClick={() => run(() => openSession(mainId))}
            type="button"
          >
            <span className="avatar">✦</span>
            <span>
              <strong>我的伙伴</strong>
              <small>陪伴、灵感，还有日常的每一件小事</small>
            </span>
            <span className="chevron">›</span>
          </button>
        ) : null}
        <h2 className="section-label">工作会话</h2>
        <div className="session-list">
          {listed.map(item => (
            <button
              className="session-row"
              disabled={!connected || busy}
              key={item.id}
              onClick={() => run(() => openSession(item.id))}
              type="button"
            >
              <span className="avatar small">{(item.title ?? '工作').slice(0, 1)}</span>
              <span className="session-copy">
                <strong>{item.title || '新的会话'}</strong>
                <small>{item.preview || '开始一段新的对话'}</small>
              </span>
              <time>{timestamp(item.last_active)}</time>
              {item.message_count > 0 && item.last_active > (seenAt[item.id] ?? 0) ? (
                <i aria-label="未读" className="session-unread" />
              ) : null}
            </button>
          ))}
        </div>
        {sessions.length < sessionTotal ? (
          <button
            className="load-more"
            disabled={busy}
            onClick={() => run(() => loadSessions(sessions.length))}
            type="button"
          >
            更多会话
          </button>
        ) : null}
        {listed.length === 0 ? <Empty title="从一件想做的事开始">点击右上角，创建工作会话。</Empty> : null}
      </div>
    )
  }

  const title = selected === mainId ? '我的伙伴' : sessions.find(item => item.id === selected)?.title || '工作会话'

  const refreshMessages = () => {
    void run(() => resume(selected))
  }

  return (
    <div className="chat-page">
      <header className="chat-header">
        <button
          aria-label="返回会话列表"
          className="icon-button"
          onClick={() => {
            navigation.current++
            selectedRef.current = null
            setSelected(null)
            releaseRecording()
          }}
          type="button"
        >
          <IconArrowLeft />
        </button>
        <span className="avatar small">✦</span>
        <div>
          <strong>{title}</strong>
          <small>{turn?.running ? '正在回复' : connected ? '与你保持连接' : '连接中'}</small>
        </div>
        {turn?.running ? (
          <button
            className="subtle"
            onClick={() =>
              run(async () => {
                await gateway.request('session.interrupt', {
                  session_id: selected
                })
                await resume(selected)
              })
            }
            type="button"
          >
            停止
          </button>
        ) : null}
      </header>
      <div className="message-scroll" ref={listRef}>
        {hasMore ? (
          <button
            className="load-more"
            disabled={busy}
            onClick={() =>
              run(async () => {
                const before = messages.find(message => typeof message.id === 'number')?.id
                const height = listRef.current?.scrollHeight ?? 0

                const result = await gateway.request<History>('session.history', {
                  session_id: selected,
                  before_id: before,
                  limit: 60
                })

                if (alive() && selectedRef.current === selected) {
                  setMessages(previous => mergeMessages(result.messages, previous))
                  setHasMore(result.has_more)
                  requestAnimationFrame(() => {
                    if (listRef.current) {
                      listRef.current.scrollTop += listRef.current.scrollHeight - height
                    }
                  })
                }
              })
            }
            type="button"
          >
            查看更早的消息
          </button>
        ) : null}
        {messages
          .filter(message => message.role === 'assistant' || message.role === 'user' || message.subtype)
          .map((message, index) => (
            <Message
              api={api}
              device={device}
              key={message.id ?? `message-${index}`}
              message={message}
              refresh={refreshMessages}
              userId={userId}
            />
          ))}
        {turn ? (
          <div className="message assistant">
            <div className="bubble">
              {turn.bubbles.map((bubble, index) => (
                <Bubble api={api} bubble={bubble} key={index} refresh={refreshMessages} />
              ))}
              {turn.text ? (
                <p>{turn.text}</p>
              ) : turn.bubbles.length === 0 ? (
                <span className="typing">
                  <i />
                  <i />
                  <i />
                </span>
              ) : null}
              {turn.tools.map(tool => (
                <p className="tool-progress" key={tool.call_id}>
                  {tool.status === 'complete' ? '✓' : '◌'} {tool.name}
                </p>
              ))}
            </div>
          </div>
        ) : null}
        {error || failure ? (
          <Notice>
            {failure || error}
            {retryMessage ? (
              <button onClick={() => run(() => submit(retryMessage))} type="button">
                重试回复
              </button>
            ) : pending.current ? (
              <button disabled={!connected} onClick={() => run(() => submit())} type="button">
                确认并重试发送
              </button>
            ) : null}
          </Notice>
        ) : null}
      </div>
      {readOnly ? (
        <div className="read-only">自动化会话可查看，任务由原来的计划管理。</div>
      ) : (
        <form
          className="composer"
          onSubmit={event => {
            event.preventDefault()
            void run(() => submit())
          }}
        >
          {attachments.length > 0 ? (
            <div className="attachments">
              {attachments.map((attachment, index) => (
                <div key={index}>
                  <img alt="待发送图片" src={attachment.url} />
                  <button
                    aria-label="移除图片"
                    onClick={() => setAttachments(previous => previous.filter((_, item) => item !== index))}
                    type="button"
                  >
                    <IconX size={14} />
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          {recording ? (
            <div className="recording-status">
              ● 录音中，点击麦克风完成{' '}
              <button onClick={releaseRecording} type="button">
                取消
              </button>
            </div>
          ) : null}
          <div className="composer-row">
            <button
              aria-label="添加图片"
              className="icon-button"
              disabled={busy || recording}
              onClick={() => fileRef.current?.click()}
              type="button"
            >
              <IconPhoto />
            </button>
            <textarea
              aria-label="消息"
              disabled={Boolean(pending.current)}
              maxLength={100000}
              onChange={event => {
                setDraft(event.target.value)
                sessionStorage.setItem(draftKey(selected), event.target.value)
              }}
              placeholder={transcribing ? '正在转写，请稍候…' : '说点什么…'}
              rows={1}
              value={draft}
            />
            <button
              aria-label={recording ? '完成录音' : '录音转文字'}
              className={`icon-button ${recording ? 'recording' : ''}`}
              disabled={transcribing || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder}
              onClick={() => run(recordVoice)}
              type="button"
            >
              {recording ? <IconSquare size={18} /> : <IconMicrophone />}
            </button>
            <button
              aria-label="发送"
              className="send-button"
              disabled={
                !connected ||
                busy ||
                Boolean(turn?.running) ||
                recording ||
                transcribing ||
                (!draft.trim() && attachments.length === 0)
              }
              type="submit"
            >
              <IconSend size={20} />
            </button>
          </div>
          <input
            accept="image/png,image/jpeg,image/webp,image/gif"
            hidden
            multiple
            onChange={event => {
              const files = [...(event.target.files ?? [])]
              event.target.value = ''
              const target = selected
              void run(async () => {
                const images = await Promise.all(files.slice(0, 4).map(readImage))

                if (alive() && selectedRef.current === target) {
                  const next = [
                    ...attachmentsRef.current,
                    ...images.map(image => ({
                      type: 'image' as const,
                      url: image.url
                    }))
                  ].slice(0, 4)

                  if (next.reduce((sum, attachment) => sum + attachment.url.length, 0) > 10 * 1024 * 1024) {
                    throw new Error('图片总大小过大，请减少图片或缩小后再发送')
                  }

                  setAttachments(next)
                }
              })
            }}
            ref={fileRef}
            type="file"
          />
        </form>
      )}
    </div>
  )
}

function Message({
  message,
  api,
  refresh,
  userId,
  device
}: {
  message: SessionMessage
  api: RemoteApi
  refresh: () => void
  userId: number
  device: number
}): ReactElement {
  let content =
    typeof message.content === 'string' ? message.content : typeof message.text === 'string' ? message.text : ''

  let parts: unknown[] = []

  if (message.content_type === 'multimodal_v1' && typeof message.content === 'string') {
    try {
      const decoded: unknown = JSON.parse(message.content)

      if (Array.isArray(decoded)) {
        parts = decoded
        content = ''
      }
    } catch {
      // 无法解析的历史内容保留原文，不能丢弃消息。
    }
  }

  return (
    <div className={`message ${message.role}`}>
      <div className="bubble">
        {message.content_type === 'companion_reply' ? (
          message.bubbles.map((bubble, index) => (
            <Bubble
              api={api}
              bubble={bubble}
              device={device}
              index={index}
              key={index}
              messageId={message.id}
              refresh={refresh}
              userId={userId}
            />
          ))
        ) : (
          <>
            {content ? <p>{content.replace(/(?:^|\n)MEDIA:\s*\S+/g, '')}</p> : null}
            {parts.map((part: unknown, index) =>
              record(part) && part.type === 'input_text' && typeof part.text === 'string' ? (
                <p key={index}>{part.text}</p>
              ) : record(part) && part.type === 'input_image' && typeof part.image_url === 'string' ? (
                <Media key={index} refresh={refresh} type="image" url={part.image_url} />
              ) : record(part) && part.type === 'input_video' && typeof part.video_url === 'string' ? (
                <Media key={index} refresh={refresh} type="video" url={part.video_url} />
              ) : null
            )}
            {message.media?.map((media, index) => (
              <Media key={index} refresh={refresh} type={media.type} url={media.url} />
            ))}
          </>
        )}
        {message.reasoning ? (
          <details className="reasoning">
            <summary>思考过程</summary>
            {message.reasoning}
          </details>
        ) : null}
      </div>
    </div>
  )
}

function Bubble({
  bubble,
  api,
  refresh,
  messageId,
  index = 0,
  userId,
  device
}: {
  bubble: CompanionBubble
  api: RemoteApi
  refresh: () => void
  messageId?: number
  index?: number
  userId?: number
  device?: number
}): ReactElement {
  const { run, busy, error } = useTask()
  const audioRef = useRef<HTMLAudioElement>(null)
  const savedSecond = useRef(-1)
  const progressKey =
    userId !== undefined && device !== undefined && messageId !== undefined
      ? `remote:${userId}:${device}:voice:${messageId}:${index}`
      : null

  if (bubble.type === 'text') {
    return <p>{bubble.text}</p>
  }

  if (bubble.type === 'voice') {
    return (
      <div className="voice-bubble">
        <p>{bubble.text}</p>
        {bubble.audio ? (
          <audio
            controls
            onEnded={() => {
              if (progressKey) {
                localStorage.setItem(progressKey, String(bubble.audio?.duration ?? 0))
              }
            }}
            onError={refresh}
            onLoadedMetadata={() => {
              const audio = audioRef.current
              const saved = progressKey ? Number(localStorage.getItem(progressKey)) : 0

              if (audio && Number.isFinite(saved) && saved < audio.duration) {
                audio.currentTime = saved
              }
            }}
            onPause={() => {
              if (audioRef.current && progressKey) {
                localStorage.setItem(progressKey, String(audioRef.current.currentTime))
              }
            }}
            onPlay={event => {
              pauseMedia(event.currentTarget)
            }}
            onTimeUpdate={() => {
              const audio = audioRef.current

              if (audio && progressKey && Math.floor(audio.currentTime) !== savedSecond.current) {
                savedSecond.current = Math.floor(audio.currentTime)
                localStorage.setItem(progressKey, String(audio.currentTime))
              }
            }}
            preload="none"
            ref={audioRef}
            src={bubble.audio.url}
          />
        ) : messageId ? (
          <button
            className="subtle"
            disabled={busy}
            onClick={() =>
              run(async () => {
                await api.request(`/api/sessions/messages/${messageId}/voice/${index}`, 'POST', {})
                refresh()
              })
            }
            type="button"
          >
            重新生成语音
          </button>
        ) : (
          <span className="muted">语音暂不可用</span>
        )}
        {error ? <Notice>{error}</Notice> : null}
      </div>
    )
  }

  return bubble.status === 'ready' && bubble.url ? (
    <Media refresh={refresh} type={bubble.type} url={bubble.url} />
  ) : (
    <div className="media-pending">
      <span>
        {bubble.type === 'video' ? '视频' : '图片'} ·{' '}
        {bubble.status === 'pending' ? '正在制作' : bubble.status === 'result_unknown' ? '结果待确认' : '制作失败'}
      </span>
      {bubble.error ? <p>{bubble.error}</p> : null}
      <button onClick={refresh} type="button">
        刷新状态
      </button>
    </div>
  )
}
