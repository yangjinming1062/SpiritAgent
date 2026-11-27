import { type ReactNode, useMemo } from 'react'

import type { ApiSchemas } from './api.generated'
import type { lifeCopy } from './life-copy'
import { LifeMedia, type LifeMediaTile } from './life-media'
import type { LifeDestination } from './life-reader-state'
import {
  groupLifeEpisodes,
  lifeDayFormatter,
  type LifeEpisode,
  lifeEpisodeLabel,
  lifeStepStatus
} from './life-trajectory'

type Copy = typeof lifeCopy.zh
export type LifeResultsView = (reference: LifeEpisode['result_ref']) => ReactNode

function ReadingNotes({ reading, t }: { reading: NonNullable<LifeEpisode['reading_result']>; t: Copy }) {
  return (
    <details className="life-notes">
      <summary>{t.readingNotes}</summary>
      <p>{reading.notes}</p>
      <ul>
        {reading.sources.map(source => (
          <li key={source.url}>
            <a href={source.url} rel="noreferrer" target="_blank">
              {source.title || source.url}
            </a>
            {' · '}
            {source.content_kind === 'summary' ? t.sourceSummary : source.excerpted ? t.sourceExcerpt : t.sourceText}
          </li>
        ))}
      </ul>
    </details>
  )
}

export function LifePursuitDetail({
  detail,
  locale,
  t,
  results
}: {
  detail: ApiSchemas['LifePursuitPublicDetail']
  locale: string
  t: Copy
  results: LifeResultsView
}) {
  return (
    <article className="life-pursuit-detail">
      <div className="life-metadata">
        <span>{t.statuses[detail.status]}</span>
        <time dateTime={detail.started_at}>
          {new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }).format(new Date(detail.started_at))}
        </time>
      </div>
      <h1>{detail.title || t.pursuitFallback}</h1>
      {detail.narrative && <p>{detail.narrative}</p>}
      <div className="life-actions">{results(detail.result_ref)}</div>
      {detail.steps.length > 0 && (
        <details className="life-notes">
          <summary>{t.stepsTitle}</summary>
          <ol>
            {detail.steps.map(step => (
              <li key={step.id}>
                <strong>{t.stepStates[lifeStepStatus(step)]}</strong>
                {step.narrative && step.disposition !== 'waiting' && <p>{step.narrative}</p>}
                {step.reading_result && <ReadingNotes reading={step.reading_result} t={t} />}
                <div className="life-actions">{results(step.result_ref)}</div>
              </li>
            ))}
          </ol>
        </details>
      )}
    </article>
  )
}

export function LifeEpisodes({
  active,
  entries,
  locale,
  context,
  navigate,
  t,
  results,
  mediaTile
}: {
  active: boolean
  entries: LifeEpisode[]
  locale: string
  context: LifeDestination
  navigate: (place: LifeDestination) => void
  t: Copy
  results: LifeResultsView
  mediaTile: LifeMediaTile
}) {
  const days = useMemo(() => groupLifeEpisodes(entries), [entries])
  const formatter = useMemo(() => lifeDayFormatter(locale), [locale])
  return (
    <div className="life-days">
      {days.map(({ date, entries: day }) => (
        <section className="life-day" key={date}>
          <h2>
            <time dateTime={date}>{formatter.format(new Date(date))}</time>
          </h2>
          <ol>
            {day.map(entry => {
              const { topic_id: topicId, pursuit_id: pursuitId } = entry
              return (
                <li key={entry.id}>
                  <article>
                    <header className="life-entry-heading">
                      {entry.title && <h3>{entry.title}</h3>}
                      <span className="life-kind">{t.episodeLabels[lifeEpisodeLabel(entry)]}</span>
                    </header>
                    {entry.narrative && <p>{entry.narrative}</p>}
                    <LifeMedia active={active} media={entry.media} t={t.media} Tile={mediaTile} />
                    {entry.reading_result && <ReadingNotes reading={entry.reading_result} t={t} />}
                    <div className="life-actions">
                      {results(entry.result_ref)}
                      {entry.has_related_episodes && (context.kind !== 'topic' || context.id !== topicId) && (
                        <button
                          className="life-link"
                          data-life-open={'topic-' + entry.id}
                          onClick={() => navigate({ kind: 'topic', id: topicId })}
                          type="button"
                        >
                          {entry.topic_title && entry.topic_title !== entry.title
                            ? t.theme + ' · ' + entry.topic_title
                            : t.openTopic}
                        </button>
                      )}
                      {pursuitId && (context.kind !== 'pursuit' || context.id !== pursuitId) && (
                        <button
                          className="life-link"
                          data-life-open={'pursuit-' + entry.id}
                          onClick={() => navigate({ kind: 'pursuit', id: pursuitId })}
                          type="button"
                        >
                          {t.details}
                        </button>
                      )}
                    </div>
                  </article>
                </li>
              )
            })}
          </ol>
        </section>
      ))}
    </div>
  )
}
