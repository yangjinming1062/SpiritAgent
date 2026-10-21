import type { ReactNode } from 'react'
import { useLayoutEffect, useRef } from 'react'

import { AssetLibraryCard, AssetLibraryGrid } from '@/app/components/asset-library-card'
import { AssetLibraryHeader, type AssetLibrarySearch, AssetLibraryToolbar } from '@/app/components/asset-library-header'
import type { DesktopVideoSet, DesktopVideoState } from '@/modules/desktop-videos'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { Search, Sparkles } from '@/shared/lib/icons'
import { SettingCard, SettingsContent } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { DesktopLifeCover } from './desktop-life-preview'
import { resolutionLabel } from './desktop-life-resolution'
import { $desktopLifeUi, updateDesktopLifeUi } from './desktop-life-state'
import { desktopLifeActionName, desktopLifeActionStatus, desktopLifePendingAction } from './desktop-life-status'
import { useDesktopLifeStrings } from './desktop-life-strings'

interface DesktopLifeLibraryViewProps {
  sets: DesktopVideoSet[]
  state: DesktopVideoState | null
  loading: boolean
  busy: boolean
  savingPreferences: boolean
  feedback: ReactNode
  focusTarget: number | 'create' | null
  issue: ReactNode
  search: AssetLibrarySearch
  onCreate: () => void
  onOpenSet: (set: DesktopVideoSet) => void
  onSavePreferences: (change: { autonomous_enabled?: boolean; pinned?: boolean }) => void
}

export function DesktopLifeLibraryView({
  sets,
  state,
  loading,
  busy,
  savingPreferences,
  feedback,
  focusTarget,
  issue,
  search,
  onCreate,
  onOpenSet,
  onSavePreferences
}: DesktopLifeLibraryViewProps): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const common = useStrings().common
  const scrollRef = useRef<HTMLDivElement>(null)
  const createButton = useRef<HTMLButtonElement>(null)

  const visibleSets = sets.filter(set =>
    search.matches(set.title, ...set.actions.flatMap(action => [action.name, action.description]))
  )

  useLayoutEffect(() => {
    const element = scrollRef.current
    const scope = captureAuthScope()

    if (element) {
      element.scrollTop = $desktopLifeUi.get().libraryScrollTop ?? 0

      if (focusTarget !== null) {
        const card =
          typeof focusTarget === 'number'
            ? element.querySelector<HTMLButtonElement>(`[data-asset-id="${focusTarget}"]`)
            : null

        const target = card ?? createButton.current
        target?.focus({ preventScroll: true })
      }
    }

    const savePosition = (): void => {
      if (element && scope?.()) {
        updateDesktopLifeUi({ libraryScrollTop: element.scrollTop })
      }
    }

    element?.addEventListener('scroll', savePosition)

    return () => element?.removeEventListener('scroll', savePosition)
  }, [focusTarget])

  return (
    <SettingsContent scrollRef={scrollRef}>
      <div className="space-y-4">
        <AssetLibraryHeader
          description={t.hint}
          policies={
            state
              ? [
                  {
                    id: 'desktop-autonomy',
                    label: t.autonomy,
                    description: t.autonomyHint,
                    status: savingPreferences
                      ? common.saving
                      : state.autonomous_enabled
                        ? state.pinned
                          ? t.pinnedHint
                          : t.policyEnabled
                        : t.policyDisabled,
                    checked: state.autonomous_enabled,
                    disabled: busy,
                    ariaLabel: t.autonomy,
                    onChange: () => onSavePreferences({ autonomous_enabled: !state.autonomous_enabled })
                  },
                  {
                    id: 'desktop-pin',
                    label: t.pin,
                    description: t.pinHint,
                    status: savingPreferences ? common.saving : state.pinned ? t.policyEnabled : t.policyDisabled,
                    checked: state.pinned,
                    disabled: busy,
                    ariaLabel: t.pin,
                    onChange: () => onSavePreferences({ pinned: !state.pinned })
                  }
                ]
              : []
          }
        >
          <AssetLibraryToolbar
            createDisabled={busy}
            createLabel={t.createSet}
            createRef={createButton}
            onCreate={onCreate}
            onQueryChange={search.onQueryChange}
            query={search.query}
            searchLabel={t.searchBackgrounds}
          />
        </AssetLibraryHeader>
        {feedback}
        {issue}
        {loading && !sets.length && (
          <p className="py-8 text-center text-sm text-muted" role="status">
            {t.loading}
          </p>
        )}
        {!loading && !visibleSets.length && !issue && (
          <SettingCard>
            <div className="space-y-3 p-6 text-center">
              {search.searching ? (
                <Search className="mx-auto size-6 text-muted" />
              ) : (
                <Sparkles className="mx-auto size-6 text-accent" />
              )}
              <p className="text-xs text-muted">{search.searching ? t.noMatchingBackgrounds : t.noSets}</p>
              {!search.searching && <p className="text-[11px] leading-relaxed text-faint">{t.emptyHint}</p>}
            </div>
          </SettingCard>
        )}
        <AssetLibraryGrid>
          {visibleSets.map(set => {
            const idle = set.actions.find(action => action.key === 'idle')
            const pending = desktopLifePendingAction(set)
            const status = pending ?? idle

            return (
              <AssetLibraryCard
                currentLabel={set.is_current ? t.current : undefined}
                description={resolutionLabel(set.target_width, set.target_height)}
                footer={
                  <>
                    <span className="min-w-0 flex-1 truncate text-[10px] text-faint">
                      {status
                        ? `${status.key !== 'idle' ? `${desktopLifeActionName(status, t)} · ` : ''}${desktopLifeActionStatus(status, t, common.videoQueue, common.actionVariants)}`
                        : t.notCreated}
                    </span>
                    <button className="shrink-0 text-[10px] text-muted" onClick={() => onOpenSet(set)} type="button">
                      {pending ? t.continue : t.view}
                    </button>
                  </>
                }
                id={set.id}
                key={set.id}
                onOpen={() => onOpenSet(set)}
                openLabel={`${t.view}: ${set.title.trim() || t.newSceneName}`}
                preview={
                  <DesktopLifeCover
                    url={
                      idle?.current?.poster_url ||
                      idle?.draft?.candidate_poster_url ||
                      idle?.draft?.image_url ||
                      set.base_image_url
                    }
                  />
                }
                title={set.title.trim() || t.newSceneName}
              />
            )
          })}
        </AssetLibraryGrid>
      </div>
    </SettingsContent>
  )
}
