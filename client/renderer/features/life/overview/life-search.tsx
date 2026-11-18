import { useStore } from '@nanostores/react'
import { useEffect } from 'react'
import type React from 'react'

import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, Disclosure, ErrorBanner, HINT_TEXT, LoadingBlock } from '@/ui'
import { ArchiveSearchFeedback, ArchiveSearchForm } from '@/ui/archive-search'

import { archiveSearchCopy, emptyArchiveSearch } from '../../../../../shared/archive-search'
import styles from '../life.module.css'
import { lifeSearch, searchLife } from '../search'

import { $lifeUi, updateLifeUi } from './life-state'
import { LifeTrajectory } from './life-trajectory'

/**
 * 生活记录：未提交搜索时由调用方提供最近变化，提交后切换为搜索结果；
 * 搜索表单收进折叠区，结果与时间线共用同一可视区。
 */
export function LifeArchiveSearch({
  visible,
  pursuitId,
  topicId,
  revision,
  onSelectPursuit,
  onSelectTopic,
  fallback
}: {
  visible: boolean
  pursuitId: string | null
  topicId: string | null
  revision: string
  onSelectPursuit: (id: string | null) => void
  onSelectTopic: (id: string | null) => void
  fallback: React.ReactNode
}): React.JSX.Element {
  const ui = useStore($lifeUi)
  const result = useStore(lifeSearch.$data)
  const busy = useStore(lifeSearch.$loading)
  const failed = useStore(lifeSearch.$error)
  const locale = useStore($locale)
  const strings = useStrings()
  const t = archiveSearchCopy(locale)
  const life = strings.life
  const applied = ui.search.applied

  useEffect(() => {
    if (visible && applied) {
      void searchLife(applied, pursuitId ?? undefined, topicId ?? undefined)
    } else if (!applied) {
      lifeSearch.clear()
    }
  }, [visible, applied, pursuitId, topicId, revision])

  return (
    <section className={styles.section}>
      <h2>{life.recordsTitle}</h2>
      <div className="flex flex-wrap items-center gap-2">
        <Disclosure chevron summaryClassName="text-[11px] text-muted" title={life.searchLabel} variant="inline">
          <ArchiveSearchForm
            busy={busy}
            label={t.life}
            onChange={search =>
              updateLifeUi({ search, ...(search.applied !== ui.search.applied ? { scrollTop: 0 } : {}) })
            }
            value={ui.search}
          />
        </Disclosure>
        {applied && (
          <button
            className={BTN_SUBTLE}
            onClick={() => updateLifeUi({ search: emptyArchiveSearch(), scrollTop: 0 })}
            type="button"
          >
            {life.clearFilter}
          </button>
        )}
        {topicId !== null && (
          <>
            <span className={HINT_TEXT}>{life.topicScope}</span>
            <button className={BTN_SUBTLE} onClick={() => onSelectTopic(null)} type="button">
              {life.recordsScopeClear}
            </button>
          </>
        )}
        {pursuitId !== null && (
          <>
            <span className={HINT_TEXT}>{life.recordsScope}</span>
            <button className={BTN_SUBTLE} onClick={() => onSelectPursuit(null)} type="button">
              {life.recordsScopeClear}
            </button>
          </>
        )}
      </div>
      {applied ? (
        <>
          {failed && (
            <ErrorBanner
              message={t.failed}
              onRetry={() => void searchLife(applied, pursuitId ?? undefined, topicId ?? undefined)}
              retryLabel={t.retry}
            />
          )}
          {busy && !result ? (
            <LoadingBlock label={t.loading} />
          ) : (
            result && (
              <>
                <ArchiveSearchFeedback count={result.items.length} status={result.search} />
                <LifeTrajectory
                  entries={result.items}
                  onPursuit={onSelectPursuit}
                  onTopic={onSelectTopic}
                  topicId={topicId}
                />
              </>
            )
          )}
        </>
      ) : (
        fallback
      )}
    </section>
  )
}
