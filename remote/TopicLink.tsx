import type { ReactElement, ReactNode } from 'react'
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

import {
  type CompanionLinkBubble,
  companionLinkUrl,
  isTopicFeedbackSnapshot,
  topicFeedbackIsCurrent,
  type TopicFeedbackSnapshot,
  type TopicFeedbackValue
} from '@protocol'

import { compareResourceIds } from '../shared/resource-id'

import type { RemoteApi, RemoteGateway } from './api'
import { errorText } from './api'
import { Notice, timestamp, useAlive, useTask } from './ui'

interface FeedbackState {
  feedback: Record<string, TopicFeedbackSnapshot>
  saving: Set<string>
  errors: Record<string, string>
  reload: () => void
  update: (id: string, value: TopicFeedbackValue) => Promise<void>
}

const TopicFeedback = createContext<FeedbackState | null>(null)

export function TopicFeedbackProvider({
  api,
  gateway,
  topicIds,
  children
}: {
  api: RemoteApi
  gateway: RemoteGateway
  topicIds: string[]
  children: ReactNode
}): ReactElement {
  const alive = useAlive()
  const { runLatest } = useTask()
  const [feedback, setFeedback] = useState<Record<string, TopicFeedbackSnapshot>>({})
  const [saving, setSaving] = useState(new Set<string>())
  const [errors, setErrors] = useState<Record<string, string>>({})
  const snapshots = useRef(feedback)
  const pending = useRef(new Set<string>())
  const reads = useRef(new Map<string, number>())
  const topicKey = [...new Set(topicIds)].sort(compareResourceIds).join(',')
  const ids = useRef(topicIds)
  ids.current = topicKey ? topicKey.split(',') : []

  const apply = useCallback(
    (value: TopicFeedbackSnapshot) => {
      if (
        alive() &&
        ids.current.includes(value.topic_id) &&
        topicFeedbackIsCurrent(value, snapshots.current[value.topic_id])
      ) {
        snapshots.current = { ...snapshots.current, [value.topic_id]: value }
        setFeedback(snapshots.current)
        return true
      }
      return false
    },
    [alive]
  )

  const load = useCallback(async () => {
    const requested = ids.current
    await Promise.all(
      Array.from({ length: Math.ceil(requested.length / 100) }, async (_, index) => {
        const batch = requested.slice(index * 100, (index + 1) * 100)
        const revisions = batch.map(id => {
          const revision = (reads.current.get(id) ?? 0) + 1
          reads.current.set(id, revision)
          return revision
        })
        const current = (id: string, offset: number) =>
          alive() && ids.current.includes(id) && reads.current.get(id) === revisions[offset]
        try {
          const result = await api.invoke('companion_topics.read_feedback', { query: { topic_ids: batch } })
          if (!Array.isArray(result)) {
            throw new Error('无法读取话题反馈')
          }
          const incoming = new Map(result.filter(isTopicFeedbackSnapshot).map(value => [value.topic_id, value]))
          batch.forEach((id, offset) => {
            const snapshot = incoming.get(id)
            // 读取代次只隔离错误；快照按服务端时间合并，避免重放事件丢弃更新回包。
            if (snapshot) {
              apply(snapshot)
            }
            if (current(id, offset)) {
              setErrors(previous => ({ ...previous, [id]: snapshot ? '' : '无法读取话题反馈' }))
            }
          })
        } catch (failure) {
          batch.forEach((id, offset) => {
            if (current(id, offset)) {
              setErrors(previous => ({ ...previous, [id]: errorText(failure) }))
            }
          })
        }
      })
    )
  }, [alive, api, apply])

  useEffect(() => {
    void runLatest(load)
  }, [topicKey, load, runLatest])

  useEffect(() => {
    const removeEvent = gateway.subscribeEvent(event => {
      if (event.type === 'companion.topic_feedback.updated' && isTopicFeedbackSnapshot(event.payload)) {
        const id = event.payload.topic_id
        if (apply(event.payload)) {
          reads.current.set(id, (reads.current.get(id) ?? 0) + 1)
          setErrors(previous => ({ ...previous, [id]: '' }))
        }
      }
    })
    let wasOpen = gateway.open
    const removeState = gateway.subscribeState(open => {
      if (open && !wasOpen) {
        void runLatest(load)
      }
      wasOpen = open
    })

    return () => {
      removeEvent()
      removeState()
    }
  }, [alive, gateway, apply, load, runLatest])

  const update = async (id: string, value: TopicFeedbackValue): Promise<void> => {
    const current = snapshots.current[id]
    if (!alive() || !ids.current.includes(id) || !current || pending.current.has(id)) {
      return
    }
    pending.current.add(id)
    reads.current.set(id, (reads.current.get(id) ?? 0) + 1)
    setSaving(new Set(pending.current))
    setErrors(previous => ({ ...previous, [id]: '' }))
    try {
      const result = await api.invoke('companion_topics.update_feedback', {
        path: { topic_id: id },
        body: { value: current.value === value ? null : value }
      })

      if (!isTopicFeedbackSnapshot(result) || result.topic_id !== id) {
        throw new Error('无法保存话题反馈')
      }
      apply(result)
    } catch (failure) {
      if (alive()) {
        setErrors(previous => ({ ...previous, [id]: errorText(failure) }))
      }
    } finally {
      pending.current.delete(id)
      if (alive()) {
        setSaving(new Set(pending.current))
      }
    }
  }

  return (
    <TopicFeedback.Provider value={{ feedback, saving, errors, reload: () => void runLatest(load), update }}>
      {children}
    </TopicFeedback.Provider>
  )
}

export function TopicLink({ bubble }: { bubble: CompanionLinkBubble }): ReactElement {
  const state = useContext(TopicFeedback)
  if (!state) {
    throw new Error('话题反馈缺少会话上下文')
  }
  const feedback = state.feedback[bubble.topic_id]
  const busy = state.saving.has(bubble.topic_id)
  const error = state.errors[bubble.topic_id]
  const url = companionLinkUrl(bubble)
  const platforms = { x: 'X', xiaohongshu: '小红书', douyin: '抖音' }

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
            onClick={() => void state.update(bubble.topic_id, value)}
            title={feedback?.value === value ? '再次点击撤销反馈' : undefined}
            type="button"
          >
            {value === 'like' ? '喜欢这类' : '少推荐这类'}
          </button>
        ))}
      </div>
      {error ? (
        <Notice error={error}>
          <button disabled={busy} onClick={state.reload} type="button">
            重试
          </button>
        </Notice>
      ) : null}
    </div>
  )
}
