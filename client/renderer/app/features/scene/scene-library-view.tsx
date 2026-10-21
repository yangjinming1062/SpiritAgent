import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { AssetLibraryCard, AssetLibraryGrid } from '@/app/components/asset-library-card'
import { AssetLibraryHeader, AssetLibraryToolbar } from '@/app/components/asset-library-header'
import {
  $sceneCreationIssue,
  type ActiveScene,
  finishSceneCreationWait,
  PAGE_SIZE,
  type SceneAsset,
  type ScenePolicy
} from '@/modules/scene'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { Loader2, RefreshCw, Sparkles } from '@/shared/lib/icons'
import { BTN_PRIMARY, BTN_SUBTLE, CreationFeedback, HINT_TEXT, SettingCard } from '@/shared/panel'
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
      <AssetLibraryHeader
        description={t.intro}
        policies={[
          {
            id: 'scene-autonomy',
            label: t.policyLabel,
            description: t.policyDesc,
            status: policyBusy
              ? strings.common.saving
              : policy === 'locked'
                ? t.policyStatusLocked
                : t.policyStatusUnlocked,
            checked: policy !== 'locked',
            disabled: policyBusy,
            ariaLabel: t.policyToggleAria,
            onChange: onPolicyChange
          }
        ]}
      >
        <AssetLibraryToolbar
          createLabel={t.generateButton}
          onCreate={onCreate}
          onQueryChange={onQueryChange}
          onSearch={onSearch}
          query={query}
          searchButtonLabel={t.searchButton}
          searchLabel={t.search}
        />
      </AssetLibraryHeader>

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
        <AssetLibraryGrid>
          {entries.map(entry => {
            const isCurrent = entry.id === activeScene?.id
            const isRegenerating = entry.regeneration?.status === 'pending'

            return (
              <AssetLibraryCard
                currentLabel={isCurrent ? t.historyCurrentLabel : undefined}
                description={entry.description || t.needsDescription}
                footer={
                  <>
                    <span className={`${HINT_TEXT} min-w-0 flex-1 truncate`}>
                      {isRegenerating ? t.imageRegenerating : t.statuses[entry.status]}
                    </span>
                    {!isCurrent && entry.status === 'ready' ? (
                      <button className={BTN_SUBTLE} onClick={() => onActivate(entry.id)} type="button">
                        {t.historyRollbackLabel}
                      </button>
                    ) : (
                      <span className="shrink-0 text-[10px] text-muted">{t.openDetails}</span>
                    )}
                  </>
                }
                id={entry.id}
                key={entry.id}
                onOpen={() => onOpen(entry.id)}
                openLabel={`${t.openDetails}: ${entry.title || t.untitled}`}
                preview={
                  <>
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
                  </>
                }
                title={entry.title || t.untitled}
              />
            )
          })}
        </AssetLibraryGrid>
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
