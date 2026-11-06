import type React from 'react'

import { ImageIcon, Pencil, Trash2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import {
  AssetLibraryCard,
  AssetLibraryGrid,
  AssetLibraryHeader,
  BTN_SUBTLE,
  confirmAction,
  CreationFeedback,
  Disclosure,
  LibraryToolbar,
  SettingCard,
  SettingsContent
} from '@/ui'

import { selectAppearanceDraft } from './appearance-ui-state'
import { OutfitUnknownActions } from './outfit-feedback'
import type { OutfitSectionModel } from './use-outfit-section'

const CARD_ACTION_CLASS =
  'inline-flex h-6 items-center justify-center rounded-lg bg-fill-faint px-1.5 text-muted transition hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40'

/** 造型列表：政策开关、搜索、卡片操作与结果未知反馈。 */
export function OutfitList({ model }: { model: OutfitSectionModel }): React.JSX.Element {
  const {
    session,
    t,
    common,
    dict,
    wardrobeStatus,
    outfitPolicy,
    policyBusy,
    togglePolicy,
    search,
    startDesign,
    visibleOutfits,
    busyId,
    completeDescription,
    removeOutfit,
    setZoomUrl,
    setDesigning,
    onSelectOutfit,
    libraryRef
  } = model

  return (
    <SettingsContent scrollRef={libraryRef}>
      <div className="space-y-4">
        {(session.busy || session.resultUnknown) && (
          <CreationFeedback
            actions={
              session.resultUnknown ? (
                <OutfitUnknownActions model={model} />
              ) : (
                <button className={BTN_SUBTLE} onClick={() => setDesigning(true)} type="button">
                  {t.actions.continueDesign}
                </button>
              )
            }
            description={
              session.resultUnknown
                ? session.inspected
                  ? dict.creation.inspectionHint
                  : t.design.timedOut
                : dict.creation.preserveCurrent
            }
            state={session.resultUnknown ? 'unknown' : 'working'}
            title={session.resultUnknown ? dict.creation.unknown : t.generating}
          />
        )}
        <AssetLibraryHeader
          policies={[
            {
              id: 'outfit-autonomy',
              label: t.policyLabel,
              description: t.policyDesc,
              status: policyBusy
                ? common.saving
                : outfitPolicy === 'locked'
                  ? t.policyStatusLocked
                  : t.policyStatusUnlocked,
              checked: outfitPolicy !== 'locked',
              disabled: policyBusy,
              ariaLabel: t.policyToggleAria,
              onChange: () => void togglePolicy()
            }
          ]}
        >
          <LibraryToolbar
            createLabel={t.startAction}
            onCreate={startDesign}
            onQueryChange={search.onQueryChange}
            query={search.query}
            searchLabel={t.search}
          />
        </AssetLibraryHeader>

        <AssetLibraryGrid>
          {visibleOutfits.map(outfit => {
            const task = session.tasks.get(outfit.id)

            const statusLabel =
              task === 'unknown'
                ? dict.creation.unknown
                : task === 'confirming'
                  ? common.saving
                  : task === 'generating'
                    ? t.generating
                    : (t.statusLabels[outfit.status] ?? '')

            const canEdit = outfit.status === 'draft'

            const continueDesign = (): void => {
              selectAppearanceDraft(outfit.id)
              setDesigning(true)
            }

            const openOutfit = (): void => {
              if (canEdit) {
                continueDesign()
              } else {
                onSelectOutfit(outfit.id)
              }
            }

            return (
              <AssetLibraryCard
                description={
                  outfit.description ||
                  (outfit.status === 'ready' &&
                  (outfit.descriptionStatus === 'pending' || outfit.descriptionStatus === 'processing')
                    ? t.descriptionPending
                    : t.noDescription)
                }
                footer={
                  <>
                    <span className="min-w-0 flex-1 truncate text-[10px] text-faint">
                      {statusLabel || (outfit.status === 'ready' ? t.saved : '')}
                    </span>
                    <button className={BTN_SUBTLE} disabled={busyId === outfit.id} onClick={openOutfit} type="button">
                      {canEdit ? t.actions.continueDesign : t.openDetails}
                    </button>

                    <Disclosure
                      bodyClassName="flex flex-wrap gap-1"
                      className="w-full"
                      summaryClassName="text-[10px] text-muted"
                      title={dict.common.moreActions}
                      variant="inline"
                    >
                      <div className="mt-2 flex flex-wrap gap-1">
                        {outfit.fullbodyUrl ? (
                          <button
                            aria-label={t.zoomOutfit(outfit.name)}
                            className={CARD_ACTION_CLASS}
                            onClick={() => setZoomUrl(outfit.fullbodyUrl)}
                            title={t.imageAlt}
                            type="button"
                          >
                            <ImageIcon className="size-3.5" />
                          </button>
                        ) : null}
                        {canEdit && (
                          <button
                            aria-label={t.actions.continueDesign}
                            className={CARD_ACTION_CLASS}
                            onClick={continueDesign}
                            title={t.actions.continueDesignTitle}
                            type="button"
                          >
                            <Pencil className="size-3.5" />
                          </button>
                        )}
                        {outfit.status === 'ready' && outfit.descriptionStatus === 'failed' && (
                          <button
                            aria-label={t.retryDescription}
                            className={CARD_ACTION_CLASS}
                            disabled={busyId === outfit.id}
                            onClick={() => completeDescription(outfit.id)}
                            title={outfit.descriptionError ?? t.retryDescription}
                            type="button"
                          >
                            {t.retryDescription}
                          </button>
                        )}
                        <button
                          aria-label={t.actions.delete}
                          className={cn(CARD_ACTION_CLASS, 'hover:text-danger-fg')}
                          disabled={busyId === outfit.id || Boolean(task)}
                          onClick={() => {
                            void confirmAction({
                              title: t.actions.deleteTitle,
                              description: outfit.name,
                              confirmLabel: t.actions.delete,
                              variant: 'destructive'
                            }).then(confirmed => {
                              if (confirmed) {
                                removeOutfit(outfit.id)
                              }
                            })
                          }}
                          title={t.actions.deleteTitle}
                          type="button"
                        >
                          <Trash2 className="size-3.5" />
                        </button>
                      </div>
                    </Disclosure>
                  </>
                }
                id={outfit.id}
                key={outfit.id}
                onOpen={openOutfit}
                openLabel={`${t.openDetails}: ${outfit.name}`}
                preview={
                  <>
                    {outfit.fullbodyUrl ? (
                      <img
                        alt={outfit.name}
                        className="absolute inset-0 h-full w-full object-contain"
                        loading="lazy"
                        src={outfit.fullbodyUrl}
                      />
                    ) : (
                      <div className="grid h-full place-items-center text-faint">
                        <ImageIcon className="size-5" />
                      </div>
                    )}
                  </>
                }
                previewRatio="portrait"
                title={outfit.name}
              />
            )
          })}
        </AssetLibraryGrid>
        {visibleOutfits.length === 0 ? (
          <SettingCard>
            <p className="p-6 text-center text-xs text-muted">
              {wardrobeStatus === 'loading' || wardrobeStatus === 'idle'
                ? t.loading
                : search.searching
                  ? t.emptySearch
                  : t.empty}
            </p>
          </SettingCard>
        ) : null}
      </div>
    </SettingsContent>
  )
}
