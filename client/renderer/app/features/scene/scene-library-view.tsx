import { useStore } from '@nanostores/react'
import type React from 'react'

import { AssetLibraryCard, AssetLibraryGrid } from '@/app/components/asset-library-card'
import { AssetLibraryHeader, LibraryToolbar } from '@/app/components/asset-library-header'
import {
  $sceneCreationIssue,
  finishSceneCreationWait,
  PAGE_SIZE,
  type SceneAsset,
  type ScenePolicy
} from '@/modules/scene'
import { usePanelAction } from '@/shared/hooks/use-panel-action'
import { RefreshCw, Sparkles } from '@/shared/lib/icons'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  CreationFeedback,
  HINT_TEXT,
  Pager,
  SettingCard,
  Spinner
} from '@/shared/panel'
import { useStrings } from '@/shared/strings'

interface SceneLibraryViewProps {
  entries: SceneAsset[]
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
  pending: SceneAsset | null
  policy: ScenePolicy
  query: string
  regenerating: SceneAsset | null
  slow: boolean
  status: 'idle' | 'loading' | 'loaded' | 'error'
  total: number
  hasMore: boolean
}

export function SceneLibraryView({
  entries,
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
  total,
  hasMore
}: SceneLibraryViewProps): React.JSX.Element {
  const strings = useStrings()
  const t = strings.living.scene
  const creationIssue = useStore($sceneCreationIssue)
  const { run, busyKey } = usePanelAction()
  const task = pending || regenerating
  const stage = regenerating?.regeneration?.stage || pending?.stage
  const checking = busyKey === 'check'

  const checkStatus = (): void => {
    void run(() => onCheckStatus(), { failure: t.loadFailed, key: 'check' })
  }

  // 列表顶部单一状态条：加载失败 > 提交未知 > 进行中任务，同屏至多一条。
  const statusBanner = status === 'error' ? (
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
  ) : creationIssue?.outcome === 'unknown' ? (
    <CreationFeedback
      actions={
        <>
          <button className={BTN_SUBTLE} disabled={checking} onClick={checkStatus} type="button">
            {checking ? strings.common.loading : t.inspectLibrary}
          </button>
          {creationIssue.inspected ? (
            <button
              className={BTN_PRIMARY}
              disabled={checking}
              onClick={() => finishSceneCreationWait(creationIssue)}
              type="button"
            >
              {strings.creation.finishWaiting}
            </button>
          ) : null}
        </>
      }
      description={creationIssue.inspected ? strings.creation.inspectionHint : t.submissionUnknown}
      state="unknown"
      title={strings.creation.unknown}
    />
  ) : task ? (
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
  ) : null

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
        <LibraryToolbar
          createLabel={t.generateButton}
          onCreate={onCreate}
          onQueryChange={onQueryChange}
          onSearch={onSearch}
          query={query}
          searchLabel={t.search}
          searchMode="submit"
        />
      </AssetLibraryHeader>

      {statusBanner}

      {status === 'error' ? null : entries.length > 0 ? (
        <AssetLibraryGrid>
          {entries.map(entry => {
            const isRegenerating = entry.regeneration?.status === 'pending'

            return (
              <AssetLibraryCard
                description={entry.description || t.needsDescription}
                footer={
                  <>
                    <span className={`${HINT_TEXT} min-w-0 flex-1 truncate`}>
                      {isRegenerating ? t.imageRegenerating : t.statuses[entry.status]}
                    </span>
                    <span className="shrink-0 text-[10px] text-muted">{t.openDetails}</span>
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
                        <Spinner size="sm" />
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
      ) : status === 'loading' || status === 'idle' ? null : (
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
      )}

      <Pager
        disabled={status === 'loading'}
        hasMore={hasMore}
        onPageChange={onSetPage}
        page={page}
        total={total}
        totalPages={Math.ceil(total / PAGE_SIZE)}
      />
    </>
  )
}
