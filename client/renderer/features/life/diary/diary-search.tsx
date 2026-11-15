import { useStore } from '@nanostores/react'
import { useEffect } from 'react'
import type React from 'react'

import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, Disclosure, ErrorBanner, LoadingBlock } from '@/ui'
import { ArchiveSearchFeedback, ArchiveSearchForm } from '@/ui/archive-search'

import { archiveSearchCopy, emptyArchiveSearch } from '../../../../../shared/archive-search'

import { $diaryUi, updateDiaryUi } from './diary-state'
import styles from './diary.module.css'
import { diarySearch, searchDiary } from './search'

export function DiaryArchiveSearch({
  foreground,
  onOpen
}: {
  foreground: boolean
  onOpen: (date: string) => void
}): React.JSX.Element {
  const ui = useStore($diaryUi)
  const result = useStore(diarySearch.$data)
  const busy = useStore(diarySearch.$loading)
  const failed = useStore(diarySearch.$error)
  const t = archiveSearchCopy(useStore($locale))
  const life = useStrings().life
  const applied = ui.search.applied

  useEffect(() => {
    if (foreground && applied) {
      void searchDiary(applied)
    } else if (!applied) {
      diarySearch.clear()
    }
  }, [foreground, applied])

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Disclosure chevron summaryClassName="text-[11px] text-muted" title={life.searchLabel} variant="inline">
          <ArchiveSearchForm
            busy={busy}
            label={t.diary}
            onChange={search =>
              updateDiaryUi({ search, ...(search.applied !== ui.search.applied ? { scrollTop: 0 } : {}) })
            }
            value={ui.search}
          />
        </Disclosure>
        {applied && (
          <button
            className={BTN_SUBTLE}
            onClick={() => updateDiaryUi({ search: emptyArchiveSearch(), scrollTop: 0 })}
            type="button"
          >
            {life.clearFilter}
          </button>
        )}
      </div>
      {applied && (
        <>
          {failed && <ErrorBanner message={t.failed} onRetry={() => void searchDiary(applied)} retryLabel={t.retry} />}
          {busy && !result ? (
            <LoadingBlock label={t.loading} />
          ) : (
            result && (
              <>
                <ArchiveSearchFeedback count={result.entries.length} status={result.search} />
                <div className={styles.searchResults}>
                  {result.entries.map(item => (
                    <article className={styles.entry} key={item.id}>
                      <time dateTime={item.entry_date}>{item.entry_date}</time>
                      <h3 className={styles.entryTitle}>{item.title}</h3>
                      <p className={styles.searchExcerpt}>
                        {item.body.slice(0, 160)}
                        {item.body.length > 160 ? '…' : ''}
                      </p>
                      <button className={BTN_SUBTLE} onClick={() => onOpen(item.entry_date)} type="button">
                        {t.open}
                      </button>
                    </article>
                  ))}
                </div>
              </>
            )
          )}
        </>
      )}
    </>
  )
}
