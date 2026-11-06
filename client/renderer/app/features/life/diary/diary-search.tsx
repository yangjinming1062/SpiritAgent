import { useStore } from '@nanostores/react'
import { useEffect } from 'react'
import type React from 'react'

import { ArchiveSearchFeedback, ArchiveSearchForm } from '@/app/components/archive-search'
import { diarySearch, searchDiary } from '@/modules/diary'
import { BTN_SUBTLE, ErrorBanner, LoadingBlock } from '@/shared/panel'
import { $locale } from '@/shared/store/locale'

import { archiveSearchCopy } from '../../../../../../shared/archive-search'

import { $diaryUi, updateDiaryUi } from './diary-state'
import styles from './diary.module.css'

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
      <ArchiveSearchForm
        busy={busy}
        label={t.diary}
        onChange={search =>
          updateDiaryUi({ search, ...(search.applied !== ui.search.applied ? { scrollTop: 0 } : {}) })
        }
        value={ui.search}
      />
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
