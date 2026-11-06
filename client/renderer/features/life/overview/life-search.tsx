import { useStore } from '@nanostores/react'
import { useEffect } from 'react'
import type React from 'react'

import { $locale } from '@/shared/store/locale'
import { BTN_GHOST, ErrorBanner, LoadingBlock } from '@/ui'
import { ArchiveSearchFeedback, ArchiveSearchForm } from '@/ui/archive-search'

import { archiveSearchCopy } from '../../../../../shared/archive-search'
import styles from '../life.module.css'
import { lifeSearch, searchLife } from '../search'

import { $lifeUi, updateLifeUi } from './life-state'

export function LifeArchiveSearch({
  visible,
  pursuitId,
  revision,
  onSelectPursuit
}: {
  visible: boolean
  pursuitId: string | null
  revision: string
  onSelectPursuit: (id: string) => void
}): React.JSX.Element {
  const ui = useStore($lifeUi)
  const result = useStore(lifeSearch.$data)
  const busy = useStore(lifeSearch.$loading)
  const failed = useStore(lifeSearch.$error)
  const locale = useStore($locale)
  const t = archiveSearchCopy(locale)
  const applied = ui.search.applied

  useEffect(() => {
    if (visible && applied) {
      void searchLife(applied, pursuitId ?? undefined)
    } else if (!applied) {
      lifeSearch.clear()
    }
  }, [visible, applied, pursuitId, revision])

  return (
    <section className={styles.section}>
      <ArchiveSearchForm
        busy={busy}
        label={t.life}
        onChange={search => updateLifeUi({ search, ...(search.applied !== ui.search.applied ? { scrollTop: 0 } : {}) })}
        value={ui.search}
      />
      {applied && (
        <>
          {failed && (
            <ErrorBanner
              message={t.failed}
              onRetry={() => void searchLife(applied, pursuitId ?? undefined)}
              retryLabel={t.retry}
            />
          )}
          {busy && !result ? (
            <LoadingBlock label={t.loading} />
          ) : (
            result && (
              <>
                <ArchiveSearchFeedback count={result.items.length} status={result.search} />
                <ol className={styles.timeline}>
                  {result.items.map(item => (
                    <li key={item.id}>
                      <time dateTime={item.occurred_at}>{new Date(item.occurred_at).toLocaleString(locale)}</time>
                      <div className={styles.eventBody}>
                        <span className={styles.eyebrow}>
                          {t.subjects[item.subject]} · {t.facts[item.fact_type]}
                        </span>
                        {item.narrative && <p>{item.narrative}</p>}
                        {item.reading_result && (
                          <details>
                            <summary>{t.notes}</summary>
                            <p>{item.reading_result.notes}</p>
                            <ul>
                              {item.reading_result.sources.map(source => (
                                <li key={source.url}>
                                  <a href={source.url} rel="noreferrer" target="_blank">
                                    {source.title || source.url}
                                  </a>
                                  {' · '}
                                  {source.content_kind === 'summary'
                                    ? locale === 'en'
                                      ? 'Summary'
                                      : '摘要'
                                    : source.excerpted
                                      ? locale === 'en'
                                        ? 'Excerpt'
                                        : '摘录'
                                      : locale === 'en'
                                        ? 'Text'
                                        : '正文'}
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                        {item.pursuit_id && (
                          <button
                            className={BTN_GHOST}
                            onClick={() => {
                              if (item.pursuit_id) {
                                onSelectPursuit(item.pursuit_id)
                              }
                            }}
                            type="button"
                          >
                            {t.details}
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              </>
            )
          )}
        </>
      )}
    </section>
  )
}
