import type { ReactElement } from 'react'
import { useCallback, useEffect, useState } from 'react'

import {
  type CompanionLinkBubble,
  companionLinkUrl,
  isTopicFeedbackSnapshot,
  topicFeedbackIsCurrent,
  type TopicFeedbackSnapshot,
  type TopicFeedbackValue
} from '@protocol'

import type { RemoteApi, RemoteGateway } from './api'
import { Notice, timestamp, useAlive, useTask } from './ui'

export function TopicLink({
  bubble,
  api,
  gateway
}: {
  bubble: CompanionLinkBubble
  api: RemoteApi
  gateway: RemoteGateway
}): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [feedback, setFeedback] = useState<TopicFeedbackSnapshot | null>(null)
  const url = companionLinkUrl(bubble)
  const platforms = { x: 'X', xiaohongshu: '小红书', douyin: '抖音' }

  const apply = useCallback(
    (value: unknown) => {
      if (alive() && isTopicFeedbackSnapshot(value) && value.topic_id === bubble.topic_id) {
        setFeedback(current => (topicFeedbackIsCurrent(value, current ?? undefined) ? value : current))
      }
    },
    [alive, bubble.topic_id]
  )

  const load = useCallback(async () => {
    const snapshots = await api.request<unknown>(`/api/companion/topics/feedback?topic_ids=${bubble.topic_id}`)

    if (!Array.isArray(snapshots)) {
      throw new Error('无法读取话题反馈')
    }

    const snapshot = snapshots.find(value => isTopicFeedbackSnapshot(value) && value.topic_id === bubble.topic_id)

    if (!snapshot) {
      throw new Error('无法读取话题反馈')
    }

    apply(snapshot)
  }, [api, bubble.topic_id, apply])

  useEffect(() => {
    const removeEvent = gateway.subscribeEvent(event => {
      if (event.type === 'companion.topic_feedback.updated') {
        apply(event.payload)
      }
    })
    const removeState = gateway.subscribeState(open => {
      if (open) {
        void runLatest(load)
      }
    })
    void runLatest(load)

    return () => {
      removeEvent()
      removeState()
    }
  }, [gateway, apply, load, runLatest])

  const update = (value: TopicFeedbackValue): void => {
    void run(async () => {
      const result = await api.request<unknown>(`/api/companion/topics/${bubble.topic_id}/feedback`, 'PUT', {
        value: feedback?.value === value ? null : value
      })

      if (!isTopicFeedbackSnapshot(result) || result.topic_id !== bubble.topic_id) {
        throw new Error('无法保存话题反馈')
      }

      apply(result)
    })
  }

  return (
    <div className="topic-link">
      <small className="muted">
        {platforms[bubble.platform]} · {timestamp(bubble.published_at)}
      </small>
      <p>{bubble.title}</p>
      {url ? (
        <a href={url} rel="noopener noreferrer" target="_blank">
          查看原文
        </a>
      ) : (
        <span className="muted">原始链接不可用</span>
      )}
      <div className="topic-feedback">
        {(['like', 'less'] as const).map(value => (
          <button
            aria-pressed={feedback?.value === value}
            className={feedback?.value === value ? 'selected' : 'subtle'}
            disabled={!feedback || busy}
            key={value}
            onClick={() => update(value)}
            title={feedback?.value === value ? '再次点击撤销反馈' : undefined}
            type="button"
          >
            {value === 'like' ? '喜欢这类' : '少推荐这类'}
          </button>
        ))}
      </div>
      {error ? (
        <Notice>
          {error}
          <button disabled={busy} onClick={() => void runLatest(load)} type="button">
            重试
          </button>
        </Notice>
      ) : null}
    </div>
  )
}
