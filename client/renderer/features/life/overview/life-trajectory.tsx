import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { openCreation } from '@/app/navigation/app-navigation'
import { InlineMedia } from '@/features/media'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { requestResource } from '@/shared/lib/resource-request'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, ErrorBanner, LoadingBlock } from '@/ui'

import {
  formatLifeDay,
  groupLifeEpisodes,
  type LifeEpisode,
  lifeEpisodeLabel,
  lifeResults
} from '../../../../../shared/life-trajectory'
import styles from '../life.module.css'

export function LifeReadingNotes({
  reading
}: {
  reading: NonNullable<LifeEpisode['reading_result']>
}): React.JSX.Element {
  const { life: t } = useStrings()

  return (
    <details className={styles.reading}>
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

function VisualVersionPreview({ id, versionId }: { id: string; versionId: string }): React.JSX.Element {
  const strings = useStrings()

  const { data, isLoading, reload } = useAsyncLoader(() =>
    requestResource('companion_visuals.get_visual_version', { path: { visual_id: id, version_id: versionId } })
  )

  if (isLoading) {
    return <LoadingBlock label={strings.common.loading} />
  }

  if (!data) {
    return <ErrorBanner message={strings.chat.media.loadFailed} onRetry={reload} retryLabel={strings.common.retry} />
  }

  return (
    <InlineMedia
      alt={data.description || data.title}
      audioUrl={null}
      mediaType={data.media_kind}
      url={data.media_url}
    />
  )
}

function LifeVisualResult({ id, versionId }: { id: string; versionId: string }): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const { life: t } = useStrings()

  return (
    <details className={styles.reading} onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>{t.openVisual}</summary>
      {open && <VisualVersionPreview id={id} versionId={versionId} />}
    </details>
  )
}

export function LifeResultLinks({ reference }: { reference: LifeEpisode['result_ref'] }): React.JSX.Element {
  const { life: t } = useStrings()

  const targets = {
    outfit: { section: 'wardrobe', target: 'outfitId', label: t.openOutfit },
    scene: { section: 'environment', target: 'sceneId', label: t.openScene },
    action: { section: 'actions', target: 'actionId', label: t.openAction }
  } as const

  return (
    <>
      {lifeResults(reference).map(link => {
        if (link.kind === 'visual') {
          return <LifeVisualResult id={link.id} key={link.versionId} versionId={link.versionId} />
        }

        const { id, kind } = link
        const { section, target, label } = targets[kind]

        return (
          <button
            className={BTN_SUBTLE}
            key={kind}
            onClick={() => void openCreation(section, { [target]: id })}
            type="button"
          >
            {label}
          </button>
        )
      })}
    </>
  )
}

export function LifeEpisodeBody({
  entry,
  featured = false,
  onTopic,
  onPursuit,
  topicId: activeTopic
}: {
  entry: LifeEpisode
  featured?: boolean
  onTopic?: (id: string) => void
  onPursuit?: (id: string) => void
  topicId?: string | null
}): React.JSX.Element {
  const { life: t } = useStrings()
  const locale = useStore($locale)
  const { pursuit_id: pursuitId, topic_id: topicId, topic_title: topicTitle } = entry

  return (
    <article className={styles.eventBody}>
      <div className={styles.episodeMeta}>
        <span>{t.episodeLabels[lifeEpisodeLabel(entry)]}</span>
        {featured && <time dateTime={entry.occurred_at}>{formatLifeDay(entry.local_date, locale)}</time>}
      </div>
      {entry.title && <h3>{entry.title}</h3>}
      {entry.narrative && <p className={featured ? styles.activity : undefined}>{entry.narrative}</p>}
      {onTopic && entry.reading_result && <LifeReadingNotes reading={entry.reading_result} />}
      {onTopic && (
        <div className={styles.actions}>
          <LifeResultLinks reference={entry.result_ref} />
          {topicTitle && activeTopic !== topicId && (
            <button className={BTN_SUBTLE} onClick={() => onTopic(topicId)} type="button">
              {topicTitle === entry.title ? t.openTopic : topicTitle}
            </button>
          )}
          {pursuitId && onPursuit && (
            <button className={BTN_SUBTLE} onClick={() => onPursuit(pursuitId)} type="button">
              {t.details}
            </button>
          )}
        </div>
      )}
    </article>
  )
}

export function LifeTrajectory({
  entries,
  onPursuit,
  onTopic,
  topicId
}: {
  entries: readonly LifeEpisode[]
  onPursuit: (id: string) => void
  onTopic: (id: string) => void
  topicId?: string | null
}): React.JSX.Element {
  const locale = useStore($locale)

  return (
    <div className={styles.timeline}>
      {groupLifeEpisodes(entries).map(({ date, entries }) => (
        <section className={styles.episodeDay} key={date}>
          <h3>
            <time dateTime={date}>{formatLifeDay(date, locale)}</time>
          </h3>
          <ol>
            {entries.map(entry => (
              <li key={entry.id}>
                <LifeEpisodeBody entry={entry} onPursuit={onPursuit} onTopic={onTopic} topicId={topicId} />
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  )
}
