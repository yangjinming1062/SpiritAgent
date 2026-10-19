import { IconArrowLeft, IconMicrophone, IconPhoto, IconPlus, IconSend, IconSquare, IconX } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { ApiResponse, HttpCallOptions } from '../shared/api'
import { EVENT_POLICIES } from '../shared/api.generated'
import { eventsAfterSnapshot, isRecord } from '../shared/gateway'
import { createTurnCancellation, type TurnReference } from '../shared/turn-cancellation'

import type { GatewayEvent, RemoteApi, RemoteGateway } from './api'
import { ApiError, errorText, readImage } from './api'
import { activeReplyIsPersisted, advanceTurn, commandHistory, mergeMessages } from './chat-state'
import { TopicFeedbackProvider, TopicLink } from './TopicLink'
import type {
  ActiveTurn,
  ChatAttachment,
  CompanionBubble,
  SessionInfo,
  SessionMessage,
  SystemPresetSummary
} from './types'
import {
  Empty,
  ImageInput,
  Media,
  Notice,
  PageHeader,
  pauseMedia,
  timestamp,
  useAlive,
  useTask,
  videoQueueText
} from './ui'

interface PendingPrompt {
  id: string
  text: string
  attachments: ChatAttachment[]
  retry?: number
  replace?: number
}

function readPendingPrompt(key: string): PendingPrompt | null {
  const stored = sessionStorage.getItem(key)

  if (stored === null) {
    return null
  }

  const value: unknown = JSON.parse(stored)

  if (
    !isRecord(value) ||
    typeof value.id !== 'string' ||
    typeof value.text !== 'string' ||
    !Array.isArray(value.attachments) ||
    (value.retry !== undefined &&
      (typeof value.retry !== 'number' || !Number.isInteger(value.retry) || value.retry <= 0)) ||
    (value.replace !== undefined &&
      (typeof value.replace !== 'number' || !Number.isInteger(value.replace) || value.replace <= 0))
  ) {
    throw new Error('待确认的消息记录无法读取，请核对会话后重新登录')
  }

  const attachments: ChatAttachment[] = value.attachments.map((attachment: unknown) => {
    if (
      !isRecord(attachment) ||
      (attachment.type !== 'image' && attachment.type !== 'video' && attachment.type !== 'audio') ||
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
    retry: typeof value.retry === 'number' ? value.retry : undefined,
    replace: typeof value.replace === 'number' ? value.replace : undefined
  }
}

async function readTurnReceipt(api: RemoteApi, turn: TurnReference, options?: HttpCallOptions) {
  try {
    return await api.invoke(
      'sessions.get_turn',
      { path: { session_id: turn.sessionId, request_id: turn.requestId } },
      options
    )
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      return null
    }
    throw error
  }
}

export function clearChatState(): void {
  try {
    for (const key of Object.keys(sessionStorage)) {
      if (/^remote:\d+:\d+:(?:submission|cancellation):/.test(key)) {
        sessionStorage.removeItem(key)
      }
    }
  } catch {
    // 存储受限不能阻止撤销连接和释放录音。
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

      return isRecord(stored)
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
  const historyRevisions = useRef(new Map<string, number>())
  const listRefreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [messages, setMessages] = useState<SessionMessage[]>([])
  const [turn, setTurn] = useState<ActiveTurn | null>(null)
  const [stoppingId, setStoppingId] = useState<string | null>(null)
  const turnRef = useRef(turn)
  turnRef.current = turn
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
  const settledRequests = useRef(new Set<string>())
  const finishCancellation = useRef<
    (receipt: ApiResponse<'sessions.get_turn'> | null, turn: TurnReference, error?: unknown) => Promise<void>
  >(async () => {})
  const cancellations = useMemo(
    () =>
      createTurnCancellation({
        isCurrent: alive,
        read: (turn, signal) => readTurnReceipt(api, turn, { signal, timeoutMs: 15_000 }),
        cancel: (turn, signal) =>
          api.invoke(
            'sessions.cancel_turn',
            {
              path: { session_id: turn.sessionId, request_id: turn.requestId }
            },
            { signal, timeoutMs: 15_000 }
          ),
        onSettled: (receipt, turn) => finishCancellation.current(receipt, turn),
        onRejected: (error, turn) => finishCancellation.current(null, turn, error),
        onError: (error, turn) => {
          if (alive() && selectedRef.current === turn.sessionId) {
            setFailure(errorText(error))
          }
        }
      }),
    [api, alive]
  )
  useEffect(() => () => cancellations.close(), [cancellations])
  const draftKey = (id: string) => `remote:${userId}:${device}:draft:${id}`
  const promptKey = (id: string) => `remote:${userId}:${device}:submission:${id}`
  const cancellationKey = useCallback((id: string) => `remote:${userId}:${device}:cancellation:${id}`, [userId, device])

  const confirmPrompt = useCallback(
    (id: string, request: PendingPrompt) => {
      const submissionKey = `remote:${userId}:${device}:submission:${id}`
      const savedDraftKey = `remote:${userId}:${device}:draft:${id}`
      const stored = readPendingPrompt(submissionKey)
      if (stored && stored.id !== request.id) {
        return
      }
      sessionStorage.removeItem(submissionKey)
      if (!request.replace && sessionStorage.getItem(savedDraftKey)?.trim() === request.text) {
        sessionStorage.removeItem(savedDraftKey)
      }
      if (!alive() || selectedRef.current !== id || pending.current?.id !== request.id) {
        return
      }
      pending.current = null
      if (!request.replace) {
        setDraft(value => (value.trim() === request.text ? '' : value))
        setAttachments(value =>
          value.length === request.attachments.length &&
          value.every(
            (attachment, index) =>
              attachment.type === request.attachments[index].type && attachment.url === request.attachments[index].url
          )
            ? []
            : value
        )
      }
    },
    [alive, userId, device]
  )

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
      const result = await api.invoke('sessions.list_sessions', { query: { limit: 100, offset } })

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
        events: [] as GatewayEvent[]
      }
      hydration.current = buffered

      try {
        const result = await gateway.snapshot(id)

        if (!alive() || revision !== navigation.current || selectedRef.current !== id) {
          return
        }

        const incoming = mergeMessages(result.messages, result.active_turn?.messages ?? [])
        const firstId = incoming[0]?.id ?? 0
        // 快照已覆盖的全量替换仍使旧分页失效，不能重放它的旧正文。
        const historyReplaced =
          (result.history_revision ?? 0) !== (historyRevisions.current.get(id) ?? 0) ||
          buffered.events.some(
            event =>
              event.stream_id === result.stream_id &&
              event.seq !== undefined &&
              event.seq <= (result.current_seq ?? 0) &&
              (event.type === 'message.replaced' || commandHistory(event) !== null)
          )
        historyRevisions.current.set(id, result.history_revision ?? 0)
        setMessages(previous =>
          result.truncated && !historyReplaced
            ? mergeMessages(
                previous.filter(message => (message.id ?? 0) < firstId),
                incoming
              )
            : incoming
        )
        setTurn(
          result.active_turn && !settledRequests.current.has(result.active_turn.request_id) ? result.active_turn : null
        )
        const receipt = result.last_submission

        if (!result.active_turn && receipt && (receipt.status === 'failed' || receipt.status === 'interrupted')) {
          setFailure(receipt.error || '上一回合已中断，请核对会话后重试')
          setRetryMessage(receipt.retry_message_id ?? null)
        }

        const pendingKey = `remote:${userId}:${device}:submission:${id}`
        const prior = readPendingPrompt(pendingKey)
        let confirmed = receipt?.request_id === prior?.id || result.active_turn?.request_id === prior?.id
        if (prior && !confirmed) {
          const known = await readTurnReceipt(api, { sessionId: id, requestId: prior.id })
          confirmed = known !== null
          if (!alive() || revision !== navigation.current || selectedRef.current !== id) {
            return
          }
          if (known && (known.status === 'failed' || known.status === 'interrupted')) {
            setFailure(known.error || '上一回合已中断，请核对会话后重试')
          }
          if (known) {
            cancellations.observe({ sessionId: id, requestId: known.request_id }, known)
          }
        }

        if (prior && confirmed) {
          confirmPrompt(id, prior)
        }
        setReadOnly(result.info?.is_automation === true)
        setHasMore(result.truncated === true || Boolean(result.next_cursor))
        const stopId = sessionStorage.getItem(cancellationKey(id))
        if (stopId) {
          setStoppingId(stopId)
          void cancellations.request({ sessionId: id, requestId: stopId })
        }
        if (document.visibilityState === 'visible') {
          setSeenAt(previous => {
            const next = { ...previous, [id]: Date.now() / 1000 }
            try {
              localStorage.setItem(`remote:${userId}:${device}:read`, JSON.stringify(next))
            } catch {
              // 会话已读位置仍保留在本次页面内存中。
            }

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
    [alive, api, cancellationKey, cancellations, confirmPrompt, gateway, userId, device]
  )

  finishCancellation.current = async (receipt, turn, error) => {
    setStoppingId(current => (current === turn.requestId ? null : current))
    settledRequests.current.add(turn.requestId)
    if (settledRequests.current.size > 500) {
      settledRequests.current.delete(settledRequests.current.values().next().value!)
    }
    if (sessionStorage.getItem(cancellationKey(turn.sessionId)) === turn.requestId) {
      sessionStorage.removeItem(cancellationKey(turn.sessionId))
    }
    if (!alive()) {
      return
    }
    if (pending.current?.id === turn.requestId) {
      pending.current = null
      sessionStorage.removeItem(promptKey(turn.sessionId))
    }
    if (selectedRef.current === turn.sessionId) {
      if (turnRef.current?.request_id && turnRef.current.request_id !== turn.requestId) {
        return
      }
      setTurn(current => (current?.request_id === turn.requestId ? null : current))
      if (error) {
        setFailure(errorText(error))
      } else {
        setFailure(
          receipt?.status === 'failed' || receipt?.status === 'interrupted' ? receipt.error || '回复已中断' : ''
        )
      }
      await resume(turn.sessionId)
    }
  }

  useEffect(() => {
    void runLatest(async () => {
      await loadSessions()

      if (!metadataLoaded.current) {
        const [main, catalog] = await Promise.all([
          api.invoke('sessions.get_main_session'),
          api.invoke('resource_catalog.get_presets')
        ])

        if (alive()) {
          setMainId(main.session_id)
          setPresets(catalog.presets)
          metadataLoaded.current = true
        }
      }

      if (selectedRef.current) {
        await resume(selectedRef.current)
      }
    })
  }, [refresh, loadSessions, resume, alive, runLatest, api])

  applyEvent.current = event => {
    const sessionId = event.session_id

    if (sessionId !== selectedRef.current) {
      return
    }
    if (
      event.type !== 'session.snapshot' &&
      EVENT_POLICIES[event.type].baseline &&
      'request_id' in event.payload &&
      typeof event.payload.request_id === 'string' &&
      settledRequests.current.has(event.payload.request_id)
    ) {
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
    } else if (event.type === 'message.persisted') {
      setMessages(previous => mergeMessages(previous, event.payload.messages))

      if (pending.current?.id === event.payload.request_id && selectedRef.current) {
        confirmPrompt(selectedRef.current, pending.current)
        setFailure('')
      }
    } else if (event.type === 'message.replaced') {
      const message = event.payload.message
      setMessages(previous =>
        mergeMessages(
          previous.filter(item => item.id !== event.payload.replaced_message_id),
          [message]
        )
      )
      pauseMedia()
      setFailure('')
      if (pending.current?.id === event.payload.request_id && selectedRef.current) {
        confirmPrompt(selectedRef.current, pending.current)
      }
      scheduleListRefresh()
    } else if (event.type === 'message.complete') {
      if (selectedRef.current) {
        if (pending.current?.id === event.payload.request_id) {
          confirmPrompt(selectedRef.current, pending.current)
        }
        const id = selectedRef.current
        void runLatest(() => resume(id))
      }

      scheduleListRefresh()
    } else if (event.type === 'session.state' && event.payload.running === false) {
      if (event.payload.request_id) {
        settledRequests.current.add(event.payload.request_id)
      }
      if (
        event.payload.status === 'completed' &&
        typeof event.payload.replace_reply_message_id === 'number' &&
        Array.isArray(event.payload.messages)
      ) {
        const reply = event.payload.messages.find(
          message => message.id === event.payload.result_message_id && message.role === 'assistant'
        )

        if (reply) {
          if (messages.some(message => message.id === event.payload.replace_reply_message_id)) {
            pauseMedia()
          }
          setMessages(previous =>
            mergeMessages(
              previous.filter(item => item.id !== event.payload.replace_reply_message_id),
              [reply]
            )
          )
        }
        if (pending.current?.id === event.payload.request_id && selectedRef.current) {
          confirmPrompt(selectedRef.current, pending.current)
        }
      }
      if (typeof event.payload.error === 'string') {
        setFailure(event.payload.error)
      }
    } else if (event.type === 'error') {
      setFailure(event.payload.message)
      setRetryMessage(event.payload.retry_message_id ?? null)
    } else if (
      event.type === 'message.media' ||
      event.type === 'video_gen.queue' ||
      event.type === 'message.voice' ||
      event.type === 'message.audio' ||
      event.type === 'message.edited' ||
      event.type === 'message.deleted'
    ) {
      if (selectedRef.current) {
        const id = selectedRef.current
        void runLatest(() => resume(id))
      }
    }
  }

  useEffect(() => {
    const remove = gateway.subscribeEvent(event => {
      if (hydration.current && event.session_id === hydration.current.sessionId) {
        hydration.current.events.push(event)
      } else {
        applyEvent.current(event)
      }

      if (event.type !== 'session.list_changed') {
        return
      }

      if (event.payload.deleted && event.payload.session_id === selectedRef.current) {
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
    setDraft((prior?.replace ? undefined : prior?.text) ?? sessionStorage.getItem(draftKey(id)) ?? '')
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

  const submit = async (retry?: number, replace?: number) => {
    const id = selectedRef.current

    if (!id || readOnly || turn?.running) {
      return
    }

    const awaitingReceipt = pending.current !== null
    const request = pending.current ?? {
      id: crypto.randomUUID(),
      text: replace ? '' : draft.trim(),
      attachments: replace ? [] : attachments,
      retry,
      replace
    }

    if (!request.text && request.attachments.length === 0 && !request.retry && !request.replace) {
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
    if (request.replace) {
      pauseMedia()
    }
    setFailure('')

    try {
      const known = awaitingReceipt ? await readTurnReceipt(api, { sessionId: id, requestId: request.id }) : null
      if (awaitingReceipt && !known && cancellations.requested({ sessionId: id, requestId: request.id })) {
        return
      }
      const result =
        known ??
        (await api.invoke('sessions.submit_turn', {
          path: { session_id: id },
          body: {
            request_id: request.id,
            ...(request.replace
              ? { replace_reply_message_id: request.replace }
              : request.retry
                ? { retry_message_id: request.retry }
                : {
                    text: request.text,
                    attachments: request.attachments.map(attachment => ({
                      type: attachment.type,
                      file_url: attachment.url
                    }))
                  }),
            response_preference: 'text'
          }
        }))

      cancellations.observe({ sessionId: id, requestId: request.id }, result)

      confirmPrompt(id, request)

      if (alive() && selectedRef.current === id) {
        if (result.status === 'failed' || result.status === 'interrupted') {
          setFailure(result.error || '上一回合已结束，请查看会话后重新提交')
        }

        await resume(id)
      }
    } catch (failure) {
      if (failure instanceof ApiError && failure.status !== null && failure.status >= 400 && failure.status < 500) {
        cancellations.rejected({ sessionId: id, requestId: request.id }, failure)
      }
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
    if (!id) {
      setFailure('请先选择会话')
      return
    }
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
        'file',
        new Blob(chunks, { type: media.mimeType }),
        media.mimeType.includes('mp4') ? 'recording.m4a' : 'recording.webm'
      )
      form.append('session_id', id)
      void api
        .invoke('media.upload_chat_audio', { body: form })
        .then(result => {
          if (alive() && revision === recordingRevision.current && id === selectedRef.current) {
            setAttachments(previous => [...previous, { type: 'audio', url: result.url }])
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
          title="聊天"
        />
        {error ? <Notice>{error}</Notice> : null}
        {showCreate ? (
          <section className="card">
            <h3>新建对话</h3>
            <label className="field-label">
              想聊些什么？
              <select aria-label="选择对话类型" onChange={event => setPreset(event.target.value)} value={preset}>
                {presets.map(item => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="primary full"
              disabled={busy || !presets.some(item => item.id === preset)}
              onClick={() =>
                run(async () => {
                  const result = await api.invoke('sessions.create_session', {
                    body: { system_preset_id: preset }
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
            disabled={busy}
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
        <h2 className="section-label">最近对话</h2>
        <div className="session-list">
          {listed.map(item => (
            <button
              className="session-row"
              disabled={busy}
              key={item.id}
              onClick={() => run(() => openSession(item.id))}
              type="button"
            >
              <span className="avatar small">{(item.title ?? '对话').slice(0, 1)}</span>
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
        {listed.length === 0 ? <Empty title="从一句话开始">直接和伙伴聊聊，或点击右上角开始一段新对话。</Empty> : null}
      </div>
    )
  }

  const title = selected === mainId ? '我的伙伴' : sessions.find(item => item.id === selected)?.title || '新的对话'
  const lastReplyMessageId = messages.findLast(item => item.role === 'assistant' || item.role === 'user')?.id

  const refreshMessages = () => {
    void run(() => resume(selected))
  }

  const visibleTurn = turn && !turn.replace_reply_message_id && !activeReplyIsPersisted(turn, messages) ? turn : null
  const topicIds = [
    ...messages.flatMap(message => (message.content_type === 'companion_reply' ? message.bubbles : [])),
    ...(visibleTurn?.bubbles ?? [])
  ]
    .filter(bubble => bubble.type === 'link')
    .map(bubble => bubble.topic_id)

  const content = (
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
          <small>
            {stoppingId && (!turn || turn.request_id === stoppingId)
              ? '正在停止，等待确认'
              : turn?.running
                ? '正在回复'
                : connected
                  ? '与你保持连接'
                  : '连接中'}
          </small>
        </div>
        {turn?.running || pending.current ? (
          <button
            className="subtle"
            onClick={() => {
              const stop = async () => {
                const requestId = turn?.request_id ?? pending.current?.id
                if (!requestId) {
                  return
                }
                sessionStorage.setItem(cancellationKey(selected), requestId)
                setStoppingId(requestId)
                await cancellations.request({ sessionId: selected, requestId })
              }
              void stop().catch(error => {
                if (alive()) {
                  setFailure(errorText(error))
                }
              })
            }}
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

                const result = await api.invoke('sessions.get_session_history', {
                  path: { session_id: selected },
                  query: { limit: 60, before_id: before }
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
              replace={
                message.can_replace_reply && message.id === lastReplyMessageId
                  ? () => run(() => submit(undefined, message.id))
                  : undefined
              }
              replaceDisabled={readOnly || busy || Boolean(turn?.running) || Boolean(pending.current)}
              replacing={turn?.replace_reply_message_id === message.id}
              userId={userId}
            />
          ))}
        {visibleTurn ? (
          <div className="message assistant">
            <div className="bubble">
              {visibleTurn.bubbles.map((bubble, index) => (
                <Bubble api={api} bubble={bubble} key={index} refresh={refreshMessages} />
              ))}
              {visibleTurn.text ? (
                <p>{visibleTurn.text}</p>
              ) : visibleTurn.bubbles.length === 0 ? (
                <span className="typing">
                  <i />
                  <i />
                  <i />
                </span>
              ) : null}
              {visibleTurn.tools.length ? (
                <details className="reasoning">
                  <summary>正在处理你的请求 · 查看进度</summary>
                  {visibleTurn.tools.map(tool => (
                    <p className="tool-progress" key={tool.call_id}>
                      {tool.status === 'complete' ? '✓' : '◌'} {tool.name}
                    </p>
                  ))}
                </details>
              ) : null}
            </div>
          </div>
        ) : null}
        {error || failure ? (
          <Notice error={failure || error}>
            {retryMessage ? (
              <button onClick={() => run(() => submit(retryMessage))} type="button">
                重试回复
              </button>
            ) : pending.current ? (
              <button disabled={busy} onClick={() => run(() => submit())} type="button">
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
                  <Media type={attachment.type} url={attachment.url} />
                  <button
                    aria-label="移除附件"
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
              placeholder={transcribing ? '正在上传录音，请稍候…' : '说点什么…'}
              rows={1}
              value={draft}
            />
            <button
              aria-label={recording ? '完成录音' : '录制语音'}
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
          <ImageInput
            multiple
            onFiles={files => {
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
          />
        </form>
      )}
    </div>
  )

  return (
    <TopicFeedbackProvider api={api} gateway={gateway} key={selected} topicIds={topicIds}>
      {content}
    </TopicFeedbackProvider>
  )
}

function Message({
  message,
  api,
  refresh,
  userId,
  device,
  replace,
  replacing,
  replaceDisabled
}: {
  message: SessionMessage
  api: RemoteApi
  refresh: () => void
  userId: number
  device: number
  replace?: () => void
  replacing?: boolean
  replaceDisabled?: boolean
}): ReactElement {
  let content = typeof message.content === 'string' ? message.content : ''

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
              isRecord(part) && part.type === 'input_text' && typeof part.text === 'string' ? (
                <p key={index}>{part.text}</p>
              ) : isRecord(part) && part.type === 'input_image' && typeof part.image_url === 'string' ? (
                <Media key={index} refresh={refresh} type="image" url={part.image_url} />
              ) : isRecord(part) && part.type === 'input_video' && typeof part.video_url === 'string' ? (
                <Media key={index} refresh={refresh} type="video" url={part.video_url} />
              ) : isRecord(part) && part.type === 'input_audio' && typeof part.audio_url === 'string' ? (
                <div key={index}>
                  <Media refresh={refresh} type="audio" url={part.audio_url} />
                  {typeof part.transcript === 'string' ? <p>{part.transcript}</p> : null}
                </div>
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
        {replace ? (
          <button className="subtle" disabled={replaceDisabled} onClick={replace} type="button">
            {replacing ? '正在换一个回答…' : '换一个回答'}
          </button>
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

  if (bubble.type === 'link') {
    return <TopicLink bubble={bubble} key={bubble.topic_id} />
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
                await api.invoke('sessions.retry_voice_bubble', {
                  path: { message_id: messageId, bubble_index: index }
                })
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
        {bubble.video_queue
          ? videoQueueText(bubble.video_queue)
          : bubble.status === 'pending'
            ? '正在制作'
            : bubble.status === 'result_unknown'
              ? '结果待确认'
              : '制作失败'}
      </span>
      {bubble.error ? <p>{bubble.error}</p> : null}
      <button onClick={refresh} type="button">
        刷新状态
      </button>
    </div>
  )
}
