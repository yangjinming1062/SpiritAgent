import { useStore } from '@nanostores/react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useState } from 'react'

import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { cn } from '@/shared/lib/utils'
import { $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import { type CompanionLinkBubble, companionLinkUrl, type TopicFeedbackValue } from '@protocol'

import { SOCIAL_PLATFORMS } from '../../../../shared/api.generated'

import { formatConversationTime } from './conversation-time'
import { $topicFeedback, $topicFeedbackSaving, loadTopicFeedback, setTopicFeedback } from './topic-feedback-store'

export function ChatLinkCard({ bubble }: { bubble: CompanionLinkBubble }): ReactElement {
  const dict = useStrings().chat.topic
  const locale = useStore($locale)
  const active = usePanelActivity()
  const begin = useAsyncGuard()
  const feedback = useStore($topicFeedback, { keys: [bubble.topic_id], deps: [bubble.topic_id] })[bubble.topic_id]
  const saving = useStore($topicFeedbackSaving, { keys: [bubble.topic_id], deps: [bubble.topic_id] })[bubble.topic_id]
  const gateway = useStore($gatewayState)
  const [error, setError] = useState(false)
  const url = companionLinkUrl(bubble)
  const time = formatConversationTime(Date.parse(bubble.published_at))

  const load = useCallback(() => {
    const isAlive = begin()
    setError(false)
    void loadTopicFeedback(bubble.topic_id).catch(() => {
      if (isAlive()) {
        setError(true)
      }
    })
  }, [bubble.topic_id, begin])

  useEffect(load, [load, gateway])

  const update = async (value: TopicFeedbackValue): Promise<void> => {
    const isAlive = begin()
    setError(false)

    try {
      await setTopicFeedback(bubble.topic_id, feedback?.value === value ? null : value)
    } catch {
      if (isAlive()) {
        setError(true)
      }
    }
  }

  return (
    <div
      className="flex max-w-80 flex-col gap-2 rounded-2xl border border-line-standard bg-surface-card px-4 py-3 text-xs shadow-xs"
      data-message-text
    >
      <span className="text-[11px] text-muted">
        {SOCIAL_PLATFORMS[bubble.platform].labels[locale]}
        {time ? ` · ${time}` : ''}
      </span>
      <p className="whitespace-pre-wrap break-words font-medium text-strong">{bubble.title}</p>
      {url ? (
        <a
          className="w-fit text-accent underline-offset-2 hover:underline focus-visible:outline-2"
          href={url}
          rel="noopener noreferrer"
          target="_blank"
        >
          {dict.open}
        </a>
      ) : (
        <span className="text-muted">{dict.unavailable}</span>
      )}
      <div className="flex flex-wrap gap-1.5">
        {(['like', 'less'] as const).map(value => (
          <button
            aria-pressed={feedback?.value === value}
            className={cn(
              'rounded-full border border-line-standard px-2.5 py-1 transition hover:bg-fill-hover focus-visible:outline-2 disabled:opacity-50',
              feedback?.value === value && 'border-accent-line bg-accent-soft text-accent'
            )}
            disabled={!active || !feedback || saving}
            key={value}
            onClick={() => void update(value)}
            title={feedback?.value === value ? dict.undo : undefined}
            type="button"
          >
            {dict[value]}
          </button>
        ))}
      </div>
      {error ? (
        <button className="w-fit text-amber-500" disabled={!active} onClick={load} type="button">
          {dict.failed}
        </button>
      ) : null}
    </div>
  )
}
