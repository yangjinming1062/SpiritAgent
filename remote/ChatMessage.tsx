import type { ReactElement } from 'react'
import { useRef } from 'react'

import { multimodalParts } from '../shared/message-content'

import type { RemoteApi } from './api'
import { useTask } from './tasks'
import { TopicLink } from './TopicLink'
import type { CompanionBubble, SessionMessage } from './types'
import { Media, Notice, pauseMedia, videoQueueText } from './ui'

export function Message({
  message,
  api,
  refresh,
  userId,
  device,
  onReplaceBubble,
  replace,
  replacing,
  replaceDisabled
}: {
  message: SessionMessage
  api: RemoteApi
  refresh: () => void
  userId: string
  device: string
  onReplaceBubble: (bubbleIndex: number, bubble: CompanionBubble) => void
  replace?: () => void
  replacing?: boolean
  replaceDisabled?: boolean
}): ReactElement {
  const content = typeof message.content === 'string' ? message.content : ''
  const parts = message.content_type === 'multimodal_v1' ? multimodalParts(content) : null

  return (
    <div className={`message ${message.role}`}>
      <div className="bubble">
        {message.content_type === 'companion_reply' ? (
          message.bubbles.map((bubble, index) => (
            <Bubble
              bubble={bubble}
              key={index}
              refresh={refresh}
              voice={{
                progressKey: `remote:${userId}:${device}:voice:${message.id}:${index}`,
                regenerate: async () => {
                  const next = await api.invoke('sessions.retry_voice_bubble', {
                    path: { message_id: message.id, bubble_index: index }
                  })
                  onReplaceBubble(index, next)
                }
              }}
            />
          ))
        ) : (
          <>
            {parts
              ? parts.map((part, index) =>
                  part.type === 'text' ? (
                    <p key={index}>{part.text}</p>
                  ) : (
                    <div key={index}>
                      <Media refresh={refresh} type={part.type} url={part.url} />
                      {part.transcript && <p>{part.transcript}</p>}
                    </div>
                  )
                )
              : content && <p>{content}</p>}
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

export function Bubble({
  bubble,
  refresh,
  voice
}: {
  bubble: CompanionBubble
  refresh: () => void
  /** 已持久化消息里的语音气泡：携带播放进度键与重新生成入口。 */
  voice?: { progressKey: string; regenerate: () => Promise<void> }
}): ReactElement {
  const { run, busy, error } = useTask()
  const audioRef = useRef<HTMLAudioElement>(null)
  const savedSecond = useRef(-1)

  if (bubble.type === 'text') {
    return <p>{bubble.text}</p>
  }

  if (bubble.type === 'link') {
    return <TopicLink bubble={bubble} key={bubble.topic_id} />
  }

  if (bubble.type === 'voice') {
    const audio = bubble.audio

    return (
      <div className="voice-bubble">
        <p>{bubble.text}</p>
        {audio ? (
          <audio
            controls
            onEnded={() => {
              if (voice) {
                localStorage.setItem(voice.progressKey, String(audio.duration))
              }
            }}
            onError={refresh}
            onLoadedMetadata={() => {
              const saved = voice ? Number(localStorage.getItem(voice.progressKey)) : 0

              if (audioRef.current && Number.isFinite(saved) && saved < audioRef.current.duration) {
                audioRef.current.currentTime = saved
              }
            }}
            onPause={() => {
              if (audioRef.current && voice) {
                localStorage.setItem(voice.progressKey, String(audioRef.current.currentTime))
              }
            }}
            onPlay={event => {
              pauseMedia(event.currentTarget)
            }}
            onTimeUpdate={() => {
              const current = audioRef.current

              if (current && voice && Math.floor(current.currentTime) !== savedSecond.current) {
                savedSecond.current = Math.floor(current.currentTime)
                localStorage.setItem(voice.progressKey, String(current.currentTime))
              }
            }}
            preload="none"
            ref={audioRef}
            src={audio.url}
          />
        ) : voice ? (
          <button className="subtle" disabled={busy} onClick={() => run(voice.regenerate)} type="button">
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
              ? '正在核对原任务'
              : bubble.error || '本次制作未完成'}
      </span>
    </div>
  )
}
