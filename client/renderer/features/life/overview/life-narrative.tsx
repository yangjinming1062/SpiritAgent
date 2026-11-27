import { useStore } from '@nanostores/react'
import { useMemo } from 'react'

import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import { ErrorBanner, LoadingBlock } from '@/ui'

import { LifeMedia } from '../../../../../shared/life-media'
import { lifeDayFormatter, lifeEpisodeLabel } from '../../../../../shared/life-trajectory'
import styles from '../life.module.css'
import { $lifeError, $lifeLoading, $lifeState, refreshLife } from '../store'

import { DesktopLifeThumbnail } from './life-media'

export function LifeNarrative({ visible }: { visible: boolean }) {
  const state = useStore($lifeState)
  const loading = useStore($lifeLoading)
  const failed = useStore($lifeError)
  const locale = useStore($locale)
  const formatter = useMemo(() => lifeDayFormatter(locale), [locale])
  const strings = useStrings()
  const t = strings.life
  const entry = state?.latest_episode
  const preview = entry?.media[0]

  return (
    <>
      {entry ? (
        <article className={styles.eventBody}>
          <div className={styles.episodeMeta}>
            <span>{t.episodeLabels[lifeEpisodeLabel(entry)]}</span>
            <time dateTime={entry.occurred_at}>{formatter.format(new Date(entry.local_date))}</time>
          </div>
          {entry.title && <h3>{entry.title}</h3>}
          {entry.narrative && <p className={styles.activity}>{entry.narrative}</p>}
          {preview && (preview.kind === 'image' || preview.poster_url) && (
            <LifeMedia active={visible} compact media={[preview]} t={t.media} Tile={DesktopLifeThumbnail} />
          )}
        </article>
      ) : !state && loading ? (
        <LoadingBlock label={t.loading} />
      ) : (
        !failed && <p className={styles.quiet}>{t.unavailable}</p>
      )}
      {failed && (
        <ErrorBanner message={t.failed} onRetry={() => void refreshLife()} retryLabel={strings.common.retry} />
      )}
    </>
  )
}
