import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import {
  $sceneCreationIssue,
  type ActiveScene,
  finishSceneCreationWait,
  PAGE_SIZE,
  type SceneAsset,
  type ScenePolicy
} from '@/modules/scene'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { Loader2, Plus, RefreshCw, Search, Sparkles } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, CreationFeedback, HINT_TEXT, INPUT_CLASS, SettingCard, Toggle } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

interface SceneLibraryViewProps {
  activeScene: ActiveScene | null
  entries: SceneAsset[]
  onActivate: (sceneId: string) => void
  onCreate: () => void
  onCheckStatus: () => Promise<void>
  onOpen: (sceneId: string) => void
  onPolicyChange: () => void
  policyBusy: boolean
  onRetryLoad: () => void
  onSearch: () => void
  onSetPage: (page: number) => void
  onQueryChange: (query: string) => void
  page: number
  pending: ActiveScene | null
  policy: ScenePolicy
  query: string
  regenerating: ActiveScene | null
  slow: boolean
  status: 'idle' | 'loading' | 'loaded' | 'error'
  total: number
}

export function SceneLibraryView({
  activeScene,
  entries,
  onActivate,
  onCreate,
  onCheckStatus,
  onOpen,
  onPolicyChange,
  onRetryLoad,
  onSearch,
  onSetPage,
  onQueryChange,
  page,
  pending,
  policy,
  policyBusy,
  query,
  regenerating,
  slow,
  status,
  total
}: SceneLibraryViewProps): React.JSX.Element {
  const strings = useStrings()
  const t = strings.living.scene
  const creationIssue = useStore($sceneCreationIssue)
  const [checking, setChecking] = useState(false)
  const begin = useAsyncGuard()
  const task = pending || regenerating
  const stage = regenerating?.regeneration?.stage || pending?.stage

  const checkStatus = async (): Promise<void> => {
    const isLive = begin()
    setChecking(true)

    try {
      await onCheckStatus()
    } finally {
      if (isLive()) {
        setChecking(false)
      }
    }
  }

  return (
    <>
      <SettingCard>
        <div className="flex items-center justify-between gap-4 p-4">
          <div className="min-w-0">
            <p className="text-sm font-medium text-strong">{t.policyLabel}</p>
            <p className="mt-1 text-xs leading-relaxed text-muted">{t.policyDesc}</p>
            <p aria-live="polite" className="mt-1 text-xs text-muted">
              {policyBusy ? strings.common.saving : policy === 'locked' ? t.policyStatusLocked : t.policyStatusUnlocked}
            </p>
          </div>
          <Toggle
            ariaLabel={t.policyToggleAria}
            checked={policy !== 'locked'}
            disabled={policyBusy}
            onChange={onPolicyChange}
          />
        </div>
      </SettingCard>
      <p className="text-[11px] leading-relaxed text-faint">{t.intro}</p>

      <form
        className="flex min-w-0 gap-2"
        onSubmit={event => {
          event.preventDefault()
          onSearch()
        }}
      >
        <input
          aria-label={t.search}
          className={cn(INPUT_CLASS, 'min-w-0 flex-1')}
          onChange={event => onQueryChange(event.target.value)}
          placeholder={t.search}
          value={query}
        />
        <button className={cn(BTN_SUBTLE, 'shrink-0 whitespace-nowrap px-3')} type="submit">
          <Search className="size-3.5" />
          <span>{t.searchButton}</span>
        </button>
        <button className={cn(BTN_PRIMARY, 'shrink-0 whitespace-nowrap')} onClick={onCreate} type="button">
          <Plus className="size-3.5" />
          <span>{t.generateButton}</span>
        </button>
      </form>

      {creationIssue?.outcome === 'unknown' ? (
        <CreationFeedback
          actions={
            <>
              <button className={BTN_SUBTLE} disabled={checking} onClick={() => void checkStatus()} type="button">
                {checking ? strings.common.loading : t.inspectLibrary}
              </button>
              {creationIssue.inspected ? (
                <button
                  className={BTN_PRIMARY}
                  disabled={checking}
                  onClick={() => finishSceneCreationWait(creationIssue)}
                  type="button"
                >
                  {t.finishWaiting}
                </button>
              ) : null}
            </>
          }
          description={creationIssue.inspected ? t.inspectionHint : t.submissionUnknown}
          state="unknown"
          title={strings.creation.unknown}
        />
      ) : null}
      {task ? (
        <CreationFeedback
          actions={
            <button className={BTN_SUBTLE} onClick={() => onOpen(task.id)} type="button">
              {t.openDetails}
            </button>
          }
          description={
            pending?.stage === 'waiting_upload'
              ? t.waitingUploadHint
              : regenerating
                ? strings.creation.canLeave
                : t.pendingOverlayHint
          }
          state={pending?.stage === 'waiting_upload' ? 'waiting' : 'working'}
          title={
            pending?.stage === 'waiting_upload'
              ? t.waitingUploadOverlay
              : stage === 'analyze'
                ? t.analyzing
                : stage === 'evaluating' || stage === 'submitting'
                  ? t.checking
                  : regenerating
                    ? t.imageRegenerating
                    : slow
                      ? t.slow
                      : t.pendingOverlay
          }
        />
      ) : null}

      {status === 'error' ? (
        <CreationFeedback
          actions={
            <button className={BTN_SUBTLE} onClick={onRetryLoad} type="button">
              <RefreshCw className="size-3.5" />
              <span>{strings.common.retry}</span>
            </button>
          }
          state="error"
          title={t.loadFailed}
        />
      ) : null}

      {entries.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 min-[620px]:grid-cols-2 min-[900px]:grid-cols-3">
          {entries.map(entry => {
            const isCurrent = entry.id === activeScene?.id
            const isRegenerating = entry.regeneration?.status === 'pending'

            return (
              <SettingCard className="min-w-0" key={entry.id}>
                <button
                  aria-label={`${t.openDetails}: ${entry.title || t.untitled}`}
                  className="block w-full min-w-0 text-left"
                  onClick={() => onOpen(entry.id)}
                  type="button"
                >
                  <div className="relative aspect-video w-full overflow-hidden bg-fill-trough">
                    {entry.url ? (
                      <img
                        alt={entry.title || t.historyAltFallback}
                        className="h-full w-full object-contain"
                        src={entry.url}
                      />
                    ) : (
                      <div className="grid h-full place-items-center text-faint">
                        <Sparkles className="size-5" />
                      </div>
                    )}
                    {isRegenerating ? (
                      <span className="absolute bottom-2 left-2 inline-flex items-center gap-1.5 rounded-full bg-black/60 px-2 py-1 text-[10px] text-white">
                        <Loader2 className="size-3 animate-spin" />
                        {t.imageRegenerating}
                      </span>
                    ) : null}
                  </div>
                  <div className="space-y-1.5 p-3">
                    <h3 className="flex min-w-0 items-center gap-2 text-xs font-medium text-strong">
                      <span className="min-w-0 flex-1 truncate">{entry.title || t.untitled}</span>
                      {isCurrent ? (
                        <span className="shrink-0 rounded-full bg-accent/15 px-2 py-0.5 text-[10px] text-accent">
                          {t.historyCurrentLabel}
                        </span>
                      ) : null}
                    </h3>
                    <p className="line-clamp-2 min-h-8 whitespace-pre-wrap break-words text-[10px] leading-relaxed text-faint">
                      {entry.description || t.needsDescription}
                    </p>
                  </div>
                </button>
                <div className="flex items-center justify-between gap-2 border-t border-line-hairline px-3 py-2">
                  <span className={cn(HINT_TEXT, 'min-w-0 truncate')}>
                    {isRegenerating ? t.imageRegenerating : t.statuses[entry.status]}
                  </span>
                  {!isCurrent && entry.status === 'ready' ? (
                    <button className={BTN_SUBTLE} onClick={() => onActivate(entry.id)} type="button">
                      {t.historyRollbackLabel}
                    </button>
                  ) : (
                    <span className="shrink-0 text-[10px] text-muted">{t.openDetails}</span>
                  )}
                </div>
              </SettingCard>
            )
          })}
        </div>
      ) : status === 'loading' || status === 'idle' ? (
        <div className="flex items-center justify-center gap-2 py-12 text-xs text-faint">
          <Loader2 className="size-4 animate-spin" />
          {t.loading}
        </div>
      ) : status !== 'error' ? (
        <SettingCard>
          <div className="space-y-3 p-6 text-center">
            <Sparkles className="mx-auto size-6 text-accent" />
            <p className="text-xs text-muted">{query.trim() ? t.emptySearch : t.emptyLibrary}</p>
            {total === 0 && !query.trim() ? (
              <button className={BTN_SUBTLE} onClick={onCreate} type="button">
                {t.generateButton}
              </button>
            ) : null}
          </div>
        </SettingCard>
      ) : null}

      {total > PAGE_SIZE ? (
        <div className="flex items-center justify-between gap-3">
          <button className={BTN_SUBTLE} disabled={page === 0} onClick={() => onSetPage(page - 1)} type="button">
            {t.previous}
          </button>
          <span className={HINT_TEXT}>
            {page + 1} / {Math.ceil(total / PAGE_SIZE)}
          </span>
          <button
            className={BTN_SUBTLE}
            disabled={(page + 1) * PAGE_SIZE >= total}
            onClick={() => onSetPage(page + 1)}
            type="button"
          >
            {t.next}
          </button>
        </div>
      ) : null}
    </>
  )
}
