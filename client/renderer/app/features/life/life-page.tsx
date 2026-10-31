import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef } from 'react'

import { applyAppNavigation } from '@/app/navigation/app-navigation'
import {
  $lifeDetail,
  $lifeDetailError,
  $lifeDetailLoading,
  $lifeEvents,
  $lifeEventsError,
  $lifeEventsHasMore,
  $lifeEventsLoading,
  $lifePursuits,
  $lifePursuitsError,
  $lifePursuitsHasMore,
  $lifePursuitsLoading,
  $lifePursuitsNextCursor,
  lifeTimeline,
  loadLifeDetail,
  loadLifeEvents,
  loadLifePursuits
} from '@/modules/life'
import { BTN_GHOST, BTN_SUBTLE, CapsuleTabs, ErrorBanner, LoadingBlock } from '@/shared/panel'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'

import { isResourceId } from '../../../../../shared/resource-id'
import { updateAppearanceUi } from '../appearance/appearance-ui-state'
import { updateSceneUi } from '../scene/scene-ui-state'
import { updateVisualsUi } from '../visuals/visuals-state'

import { LifeOverview } from './life-overview'
import { $lifeUi, $lifeUiRestoreRevision, selectLifePursuit, updateLifeUi } from './life-state'
import styles from './life.module.css'
import { useLife } from './use-life'

export function LifePage({
  visible = true,
  standalone = false
}: {
  visible?: boolean
  standalone?: boolean
}): React.JSX.Element {
  const revision = useLife(visible)
  const ui = useStore($lifeUi)
  const restoreRevision = useStore($lifeUiRestoreRevision)
  const pursuitCursor = ui.pursuitCursors[ui.pursuitPage]
  const pursuits = useStore($lifePursuits)
  const pursuitsHasMore = useStore($lifePursuitsHasMore)
  const pursuitsNextCursor = useStore($lifePursuitsNextCursor)
  const pursuitsLoading = useStore($lifePursuitsLoading)
  const pursuitsError = useStore($lifePursuitsError)
  const events = useStore($lifeEvents)
  const eventsHasMore = useStore($lifeEventsHasMore)
  const eventsLoading = useStore($lifeEventsLoading)
  const eventsError = useStore($lifeEventsError)
  const detail = useStore($lifeDetail)
  const detailLoading = useStore($lifeDetailLoading)
  const detailError = useStore($lifeDetailError)
  const locale = useStore($locale)
  const strings = useStrings()
  const t = strings.life
  const root = useRef<HTMLDivElement>(null)
  const pendingScroll = useRef<number | null>(ui.scrollTop)
  const timeline = lifeTimeline(events)
  const formatter = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' })

  const formatDate = (value: string): string =>
    Number.isNaN(Date.parse(value)) ? '' : formatter.format(new Date(value))

  const openResult = (kind: 'visual' | 'outfit' | 'scene' | 'action', id: unknown): void => {
    if (!isResourceId(id)) {
      return
    }

    if (kind === 'visual') {
      updateVisualsUi({ selectedId: id, detailOpen: true })
    }

    if (kind === 'outfit') {
      updateAppearanceUi({ selectedOutfitId: id })
    }

    if (kind === 'scene') {
      updateSceneUi({ view: { kind: 'detail', sceneId: id } })
    }

    void applyAppNavigation({
      page: 'companion',
      companionSection:
        kind === 'visual' ? 'visuals' : kind === 'outfit' ? 'wardrobe' : kind === 'scene' ? 'environment' : 'actions'
    })
  }

  useEffect(() => {
    if (visible) {
      void loadLifePursuits(ui.status, pursuitCursor)
    }
  }, [visible, revision, ui.status, pursuitCursor])

  useEffect(() => {
    if (visible) {
      void loadLifeDetail(ui.selectedId)
    }
  }, [visible, revision, ui.selectedId])

  useEffect(() => {
    if (visible) {
      void loadLifeEvents(ui.eventPage, ui.selectedId ?? undefined)
    }
  }, [visible, revision, ui.eventPage, ui.selectedId])

  useLayoutEffect(() => {
    pendingScroll.current = $lifeUi.get().scrollTop
  }, [restoreRevision])

  // 请求 effect 先更新 loading；导入不同筛选时等待新内容，普通后台刷新不接管用户滚动。
  useEffect(() => {
    if (
      visible &&
      root.current &&
      pendingScroll.current !== null &&
      !$lifePursuitsLoading.get() &&
      !$lifeEventsLoading.get() &&
      !$lifeDetailLoading.get() &&
      !$lifePursuitsError.get() &&
      !$lifeEventsError.get() &&
      !$lifeDetailError.get()
    ) {
      root.current.scrollTop = pendingScroll.current
      pendingScroll.current = null
    }
  }, [
    visible,
    restoreRevision,
    pursuitsLoading,
    eventsLoading,
    detailLoading,
    pursuitsError,
    eventsError,
    detailError,
    pursuits,
    events,
    detail
  ])

  const pagination = (
    page: number,
    hasMore: boolean,
    busy: boolean,
    onChange: (value: number) => void
  ): React.ReactNode =>
    page > 0 || hasMore ? (
      <div className={styles.pagination}>
        <button className={BTN_SUBTLE} disabled={busy || page === 0} onClick={() => onChange(page - 1)} type="button">
          {t.previous}
        </button>
        <span>{page + 1}</span>
        <button className={BTN_SUBTLE} disabled={busy || !hasMore} onClick={() => onChange(page + 1)} type="button">
          {t.next}
        </button>
      </div>
    ) : null

  return (
    <div
      className={styles.page}
      onScroll={event => {
        if (visible && pendingScroll.current === null) {
          updateLifeUi({ scrollTop: event.currentTarget.scrollTop })
        }
      }}
      ref={root}
    >
      <header className={styles.pageHeading}>
        <h1>{t.title}</h1>
        <p>{t.intro}</p>
      </header>
      {standalone && <LifeOverview showLifeLink={false} visible={visible} />}
      {ui.selectedId !== null ? (
        <section className={styles.section}>
          <button className={BTN_GHOST} onClick={() => selectLifePursuit(null)} type="button">
            {t.back}
          </button>
          {detailError && (
            <ErrorBanner
              message={t.failed}
              onRetry={() => void loadLifeDetail(ui.selectedId)}
              retryLabel={strings.common.retry}
            />
          )}
          {detailLoading && !detail ? (
            <LoadingBlock label={t.loading} />
          ) : detail?.id === ui.selectedId ? (
            <article className={styles.detail}>
              <span className={styles.eyebrow}>{t.statuses[detail.status]}</span>
              <h2>{t.ongoing}</h2>
              {detail.narrative && <p>{detail.narrative}</p>}
              <time dateTime={detail.started_at}>{formatDate(detail.started_at)}</time>
              {detail.status_changed_at && (
                <time dateTime={detail.status_changed_at}>
                  {t.statuses[detail.status]} · {formatDate(detail.status_changed_at)}
                </time>
              )}
              <h3>{t.stepsTitle}</h3>
              {detail.steps.map(step => (
                <div className={styles.detailField} key={step.id}>
                  <strong>{t.stepStates[step.state]}</strong>
                  {step.narrative && <p>{step.narrative}</p>}
                  {(
                    [
                      ['visual', step.result_ref.visual_id, t.openVisual],
                      ['outfit', step.result_ref.outfit_id, t.openOutfit],
                      ['scene', step.result_ref.scene_id, t.openScene],
                      ['action', step.result_ref.action_id, t.openAction]
                    ] as const
                  ).map(([kind, id, label]) =>
                    isResourceId(id) ? (
                      <button className={BTN_SUBTLE} key={kind} onClick={() => openResult(kind, id)} type="button">
                        {label}
                      </button>
                    ) : null
                  )}
                  {step.started_at && <time dateTime={step.started_at}>{formatDate(step.started_at)}</time>}
                  {step.reading_result && (
                    <details>
                      <summary>{t.readingNotes}</summary>
                      <p>{step.reading_result.notes}</p>
                      <ul>
                        {step.reading_result.sources.map(source => (
                          <li key={source.url}>
                            <a href={source.url} rel="noreferrer" target="_blank">
                              {source.title || source.url}
                            </a>{' '}
                            ·{' '}
                            {source.content_kind === 'summary'
                              ? t.sourceSummary
                              : source.excerpted
                                ? t.sourceExcerpt
                                : t.sourceText}
                            <time dateTime={source.retrieved_at}> · {formatDate(source.retrieved_at)}</time>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              ))}
            </article>
          ) : null}
        </section>
      ) : (
        <section className={styles.section}>
          <h2>{t.pursuitsTitle}</h2>
          <CapsuleTabs
            ariaLabel={t.pursuitsTitle}
            onChange={status => updateLifeUi({ status, pursuitPage: 0, pursuitCursors: [null] })}
            options={(['active', 'paused', 'completed', 'abandoned', 'all'] as const).map(value => ({
              value,
              label: t.statuses[value]
            }))}
            value={ui.status}
          />
          {pursuitsError && (
            <ErrorBanner
              message={t.failed}
              onRetry={() => void loadLifePursuits(ui.status, pursuitCursor)}
              retryLabel={strings.common.retry}
            />
          )}
          {pursuitsLoading && pursuits.length === 0 ? (
            <LoadingBlock label={t.loading} />
          ) : pursuits.length === 0 ? (
            !pursuitsError && <p className={styles.empty}>{t.emptyPursuits}</p>
          ) : (
            <div className={styles.pursuits}>
              {pursuits.map(pursuit => (
                <button
                  className={styles.pursuit}
                  key={pursuit.id}
                  onClick={() => selectLifePursuit(pursuit.id)}
                  type="button"
                >
                  <span className={styles.eyebrow}>{t.statuses[pursuit.status]}</span>
                  <strong>{pursuit.narrative || t.ongoing}</strong>
                  <time dateTime={pursuit.updated_at}>{formatDate(pursuit.updated_at)}</time>
                </button>
              ))}
            </div>
          )}
          {pagination(ui.pursuitPage, pursuitsHasMore, pursuitsLoading, pursuitPage =>
            updateLifeUi({
              pursuitPage,
              pursuitCursors:
                pursuitPage > ui.pursuitPage && pursuitsNextCursor
                  ? [...ui.pursuitCursors.slice(0, ui.pursuitPage + 1), pursuitsNextCursor]
                  : ui.pursuitCursors
            })
          )}
        </section>
      )}
      <section className={styles.section}>
        <h2>{t.eventsTitle}</h2>
        {eventsError && (
          <ErrorBanner
            message={t.failed}
            onRetry={() => void loadLifeEvents(ui.eventPage, ui.selectedId ?? undefined)}
            retryLabel={strings.common.retry}
          />
        )}
        {eventsLoading && events.length === 0 ? (
          <LoadingBlock label={t.loading} />
        ) : (
          <ol className={styles.timeline}>
            {timeline.map(entry => (
              <li key={entry.id}>
                <time dateTime={entry.occurredAt}>{formatDate(entry.occurredAt)}</time>
                <div className={styles.eventBody}>
                  <span className={styles.eyebrow}>{t.eventLabels[entry.kind]}</span>
                  {entry.text && <p>{entry.text}</p>}
                  <div className={styles.actions}>
                    {entry.pursuitId && entry.pursuitId !== ui.selectedId && (
                      <button className={BTN_GHOST} onClick={() => selectLifePursuit(entry.pursuitId)} type="button">
                        {t.details}
                      </button>
                    )}
                    {entry.visualId && (
                      <button
                        className={BTN_GHOST}
                        onClick={() => {
                          updateVisualsUi({ selectedId: entry.visualId, detailOpen: true })
                          void applyAppNavigation({ page: 'companion', companionSection: 'visuals' })
                        }}
                        type="button"
                      >
                        {t.openVisual}
                      </button>
                    )}
                    {entry.postId && (
                      <button
                        className={BTN_GHOST}
                        onClick={() => void applyAppNavigation({ page: 'posts' })}
                        type="button"
                      >
                        {t.openPosts}
                      </button>
                    )}
                    {entry.diaryId && (
                      <button
                        className={BTN_GHOST}
                        onClick={() => void applyAppNavigation({ page: 'diary' })}
                        type="button"
                      >
                        {t.openDiary}
                      </button>
                    )}
                  </div>
                </div>
              </li>
            ))}
            {!eventsLoading && !eventsError && timeline.length === 0 && (
              <li className={styles.empty}>{t.emptyEvents}</li>
            )}
          </ol>
        )}
        {eventsHasMore && (
          <button
            className={BTN_SUBTLE}
            disabled={eventsLoading}
            onClick={() => updateLifeUi({ eventPage: ui.eventPage + 1 })}
            type="button"
          >
            {t.moreEvents}
          </button>
        )}
      </section>
    </div>
  )
}
