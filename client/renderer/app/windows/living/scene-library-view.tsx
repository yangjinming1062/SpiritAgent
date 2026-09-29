import type React from 'react'

import { type ActiveScene, PAGE_SIZE, type SceneAsset, type ScenePolicy } from '@/modules/scene'
import { AlertCircle, Loader2, Plus, RefreshCw, Search, Sparkles } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, BTN_SUBTLE, HINT_TEXT, INPUT_CLASS, SettingCard, SettingRow, Toggle } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

interface SceneLibraryViewProps {
  activeScene: ActiveScene | null
  entries: SceneAsset[]
  onActivate: (sceneId: string) => void
  onCreate: () => void
  onOpen: (sceneId: string) => void
  onPolicyChange: () => void
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
  taskBusy: boolean
  total: number
}

export function SceneLibraryView({
  activeScene,
  entries,
  onActivate,
  onCreate,
  onOpen,
  onPolicyChange,
  onRetryLoad,
  onSearch,
  onSetPage,
  onQueryChange,
  page,
  pending,
  policy,
  query,
  regenerating,
  slow,
  status,
  taskBusy,
  total
}: SceneLibraryViewProps): React.JSX.Element {
  const t = useStrings().living.scene

  return (
    <>
      <p className="text-[11px] leading-relaxed text-faint">{t.intro}</p>

      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-strong">{t.historyTitle}</h2>
          <p className="mt-0.5 text-[10px] text-faint">{total}</p>
        </div>
        <button className={cn(BTN_PRIMARY, 'shrink-0')} disabled={taskBusy} onClick={onCreate} type="button">
          <Plus className="size-3.5" />
          <span>{t.generateButton}</span>
        </button>
      </div>

      {pending ? (
        <SettingCard className="border border-accent-line/40">
          <button
            className="flex w-full min-w-0 items-center gap-2 p-3 text-left"
            onClick={() => onOpen(pending.id)}
            type="button"
          >
            <Loader2 className="size-4 shrink-0 animate-spin text-accent" />
            <span className="min-w-0 flex-1 truncate text-xs text-body">
              {pending.stage === 'waiting_upload' ? t.waitingUploadOverlay : slow ? t.slow : t.pendingOverlay}
            </span>
            <span className="shrink-0 text-[10px] text-accent">{t.openDetails}</span>
          </button>
        </SettingCard>
      ) : null}

      {regenerating ? (
        <SettingCard className="border border-accent-line/40">
          <button
            className="flex w-full min-w-0 items-center gap-2 p-3 text-left"
            onClick={() => onOpen(regenerating.id)}
            type="button"
          >
            <Loader2 className="size-4 shrink-0 animate-spin text-accent" />
            <span className="min-w-0 flex-1 truncate text-xs text-body">{t.imageRegenerating}</span>
            <span className="shrink-0 text-[10px] text-accent">{t.openDetails}</span>
          </button>
        </SettingCard>
      ) : null}

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
      </form>

      {status === 'error' ? (
        <SettingCard>
          <div className="flex items-center gap-2 p-3 text-xs text-danger-fg">
            <AlertCircle className="size-4 shrink-0" />
            <span className="min-w-0 flex-1">{t.loadFailed}</span>
            <button className={BTN_SUBTLE} onClick={onRetryLoad} type="button">
              <RefreshCw className="size-3.5" />
              <span>{t.refresh}</span>
            </button>
          </div>
        </SettingCard>
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
                        className="h-full w-full object-cover"
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
              <button className={BTN_SUBTLE} disabled={taskBusy} onClick={onCreate} type="button">
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

      <SettingCard>
        <SettingRow
          description={t.policyDesc}
          label={
            <span className="flex items-center gap-1.5">
              <span>{t.policyLabel}</span>
            </span>
          }
        >
          <div className="flex items-center gap-2.5">
            <span className="text-[11px] text-faint">
              {policy === 'locked' ? t.policyStatusLocked : t.policyStatusUnlocked}
            </span>
            <Toggle ariaLabel={t.policyToggleAria} checked={policy !== 'locked'} onChange={onPolicyChange} />
          </div>
        </SettingRow>
      </SettingCard>
    </>
  )
}
