import { map } from 'nanostores'

import { apiSucceeded, captureAuthScope } from '@/shared/lib/authed-api'
import { authedResource } from '@/shared/lib/resource-request'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import {
  isTopicFeedbackSnapshot,
  topicFeedbackIsCurrent,
  type TopicFeedbackSnapshot,
  type TopicFeedbackValue
} from '@protocol'

export const $topicFeedback = map<Record<number, TopicFeedbackSnapshot>>({})
export const $topicFeedbackSaving = map<Record<number, boolean>>({})
const reads = new Map<number, Promise<void>>()

registerStorageClearHandler(() => {
  reads.clear()
  $topicFeedback.set({})
  $topicFeedbackSaving.set({})
})

export function onTopicFeedbackUpdated(payload: unknown): void {
  if (isTopicFeedbackSnapshot(payload) && topicFeedbackIsCurrent(payload, $topicFeedback.get()[payload.topic_id])) {
    $topicFeedback.setKey(payload.topic_id, payload)
  }
}

export function loadTopicFeedback(topicId: number): Promise<void> {
  const existing = reads.get(topicId)

  if (existing) {
    return existing
  }

  const isCurrent = captureAuthScope()

  if (!isCurrent) {
    return Promise.resolve()
  }

  const request = (async () => {
    const result = await authedResource('companion_topics.read_feedback', { query: { topic_ids: [topicId] } })

    if (!isCurrent()) {
      return
    }

    if (!apiSucceeded(result, 'topic-feedback', 'load failed') || !Array.isArray(result.value)) {
      throw new Error('无法读取话题反馈')
    }

    const snapshot = result.value.find(value => isTopicFeedbackSnapshot(value) && value.topic_id === topicId)

    if (!snapshot) {
      throw new Error('无法读取话题反馈')
    }

    onTopicFeedbackUpdated(snapshot)
  })().finally(() => {
    if (reads.get(topicId) === request) {
      reads.delete(topicId)
    }
  })

  reads.set(topicId, request)

  return request
}

export async function setTopicFeedback(topicId: number, value: TopicFeedbackValue): Promise<void> {
  const isCurrent = captureAuthScope()

  if (!isCurrent || $topicFeedbackSaving.get()[topicId]) {
    return
  }

  $topicFeedbackSaving.setKey(topicId, true)

  try {
    const result = await authedResource('companion_topics.update_feedback', {
      path: { topic_id: topicId },
      body: { value }
    })

    if (!isCurrent()) {
      return
    }

    if (
      !apiSucceeded(result, 'topic-feedback', 'save failed') ||
      !isTopicFeedbackSnapshot(result.value) ||
      result.value.topic_id !== topicId
    ) {
      throw new Error('无法保存话题反馈')
    }

    onTopicFeedbackUpdated(result.value)
  } finally {
    if (isCurrent()) {
      $topicFeedbackSaving.setKey(topicId, false)
    }
  }
}
