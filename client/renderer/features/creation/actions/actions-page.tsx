import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { RENDERER_TIMINGS } from '@/shared/constants'
import { usePanelAction } from '@/shared/hooks/use-panel-action'
import { failureText } from '@/shared/lib/ipc-error'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'
import {
  Badge,
  BTN_DANGER,
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SUBTLE,
  CapsuleTabs,
  confirmAction,
  CreationFeedback,
  EmptyState,
  ErrorBanner,
  FIELD_LABEL,
  INPUT_CLASS,
  LoadingBlock,
  Pager,
  SettingCard,
  SettingsContent,
  SettingsSectionIntro
} from '@/ui'
import { LibraryToolbar } from '@/ui/asset-library-header'

import { composeDescribedInput } from '../../../../../shared/described-input'
import {
  $visualsPresentation,
  type ActionDefinition,
  type ActionLibraryGroup,
  type ActionMediaEntry,
  visualsApi
} from '../visuals'
import { $presentedOutfit, hydrateWardrobe } from '../wardrobe'

import {
  actionGroups,
  createAction,
  deleteAction,
  interpretAction,
  loadMoreActions,
  refreshActionGroups,
  refreshActions,
  retryActionReview,
  updateAction
} from './actions-store'

interface Composer {
  actionId: string | null
  expectedUpdatedAt: string | null
  description: string
  refinement: string
  preview: { draft: Record<string, unknown>; summary: string; details: { label: string; value: string }[] } | null
}

const EMPTY_COMPOSER: Composer = {
  actionId: null,
  expectedUpdatedAt: null,
  description: '',
  refinement: '',
  preview: null
}

function ActionMediaPanel({
  entries,
  error,
  loading,
  t,
  visualsLabels
}: {
  entries: ActionMediaEntry[]
  error: boolean
  loading: boolean
  t: ReturnType<typeof useStrings>['actions']
  visualsLabels: ReturnType<typeof useStrings>['visuals']
}): React.JSX.Element {
  if (loading) {
    return <LoadingBlock label={t.mediaLoading} />
  }

  if (error) {
    return <p className="text-[11px] text-danger-fg">{t.mediaLoadFailed}</p>
  }

  if (entries.length === 0) {
    return <p className="text-[11px] text-faint">{t.mediaEmpty}</p>
  }

  return (
    <div className="rounded-lg border border-line-hairline bg-fill-faint p-2">
      <p className="mb-1 text-[11px] font-medium text-muted">{t.mediaTitle}</p>
      <div className="flex flex-col gap-1">
        {entries.map(entry => (
          <div className="flex flex-wrap items-center gap-1 text-[11px] text-muted" key={entry.visual_id}>
            <span>{visualsLabels.assetKindLabels[entry.asset_kind]}</span>
            <Badge>{entry.media_kind === 'video' ? visualsLabels.mediaKindVideo : visualsLabels.mediaKindImage}</Badge>
            {entry.stage_ready ? (
              <Badge tone="accent">{t.mediaStageReady}</Badge>
            ) : (
              <span>{t.mediaStageUnavailable}</span>
            )}
            <span>{entry.outfit_name || t.mediaOutfitUnknown}</span>
            <span>v{entry.version}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** 动作页：新建与修改都从一句描述开始，先看整理结果再保存并评审。 */
export function ActionsPage(): React.JSX.Element {
  const auth = useStore($auth)

  return <ActionsPageContent key={auth.kind === 'authenticated' ? auth.snapshot.sessionId : auth.kind} />
}

function ActionsPageContent(): React.JSX.Element {
  const dict = useStrings()
  const t = dict.actions
  const studio = dict.studio
  const auth = useStore($auth)
  const [group, setGroup] = useState<ActionLibraryGroup>('in_library')
  const actions = useStore(actionGroups[group].rows)
  const loading = useStore(actionGroups[group].loading)
  const loaded = useStore(actionGroups[group].loaded)
  const error = useStore(actionGroups[group].error)
  const nextCursor = useStore(actionGroups[group].nextCursor)
  const [query, setQuery] = useState('')
  const [composer, setComposer] = useState<Composer | null>(null)
  const [interpreting, setInterpreting] = useState(false)
  const [composerError, setComposerError] = useState('')
  const { run, busy, busyKey } = usePanelAction()
  const defaultOutfit = useStore($presentedOutfit)
  const presentation = useStore($visualsPresentation)

  const [mediaPanel, setMediaPanel] = useState<{
    expandedId: string | null
    loadingId: string | null
    errorId: string | null
    rows: Record<string, ActionMediaEntry[]>
  }>({ expandedId: null, loadingId: null, errorId: null, rows: {} })

  const mediaGeneration = useRef(0)

  const stageKey = JSON.stringify([
    presentation?.static_presentation?.identity_hash ?? presentation?.composition?.identity_hash,
    presentation?.static_presentation?.outfit?.content_hash ?? presentation?.composition?.outfit_material?.content_hash,
    presentation?.target_width,
    presentation?.target_height
  ])

  useEffect(() => {
    mediaGeneration.current += 1
    setMediaPanel({ expandedId: null, loadingId: null, errorId: null, rows: {} })
  }, [stageKey])

  useEffect(() => {
    if (auth.kind === 'authenticated') {
      void hydrateWardrobe()
    }
  }, [auth])

  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      return
    }

    const timer = setTimeout(() => void refreshActions(query, false, group), RENDERER_TIMINGS.actionSearchDebounceMs)

    return () => clearTimeout(timer)
  }, [auth, group, query])

  const hasPendingReview = actions.some(action => action.status === 'pending' || action.status === 'reviewing')

  useEffect(() => {
    if (!hasPendingReview) {
      return
    }

    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void refreshActionGroups()
      }
    }, RENDERER_TIMINGS.actionReviewPollMs)

    return () => clearInterval(timer)
  }, [hasPendingReview])

  const makeClip = (action: ActionDefinition): void => {
    if (!defaultOutfit) {
      return
    }

    void run(
      () =>
        visualsApi.create({
          asset_kind: 'actor',
          media_kind: 'video',
          outfit_id: defaultOutfit.id,
          action_id: action.id,
          requirements: ''
        }),
      { failure: t.makeClipFailed, success: t.makeClipDone, key: `clip:${action.id}` }
    )
  }

  const toggleMedia = (action: ActionDefinition): void => {
    if (mediaPanel.expandedId === action.id) {
      setMediaPanel(current => ({ ...current, expandedId: null }))

      return
    }

    if (mediaPanel.rows[action.id]) {
      setMediaPanel(current => ({ ...current, expandedId: action.id }))

      return
    }

    setMediaPanel(current => ({ ...current, expandedId: action.id, loadingId: action.id, errorId: null }))
    const generation = mediaGeneration.current
    void run(() => visualsApi.listActionMedia(action.id), { failure: t.mediaLoadFailed }).then(result => {
      if (generation !== mediaGeneration.current) {
        return
      }

      if (result === undefined) {
        setMediaPanel(current => ({
          ...current,
          errorId: action.id,
          loadingId: current.loadingId === action.id ? null : current.loadingId
        }))

        return
      }

      setMediaPanel(current => ({
        ...current,
        rows: { ...current.rows, [action.id]: result.media },
        loadingId: current.loadingId === action.id ? null : current.loadingId
      }))
    })
  }

  const interpret = async (): Promise<void> => {
    if (!composer || interpreting) {
      return
    }

    const description = composeDescribedInput(composer.description, composer.refinement)

    if (!description) {
      return
    }

    setInterpreting(true)
    setComposerError('')

    try {
      const result = await interpretAction(description, composer.actionId ?? undefined)
      setComposer(current =>
        current
          ? {
              ...current,
              description,
              refinement: '',
              preview: {
                draft: result.draft,
                summary: result.summary,
                details: result.details
              },
              expectedUpdatedAt: result.expected_updated_at ?? current.expectedUpdatedAt
            }
          : current
      )
    } catch (failure) {
      setComposerError(failureText(failure, studio.interpretFailed))
    } finally {
      setInterpreting(false)
    }
  }

  const save = (): void => {
    if (!composer?.preview) {
      return
    }

    void run(
      async () =>
        composer.actionId
          ? updateAction(composer.actionId, composer.preview!.draft, composer.expectedUpdatedAt ?? '')
          : createAction(composer.preview!.draft),
      { failure: t.saveFailed, key: 'save' }
    )?.then(action => {
      if (action) {
        setComposer(null)
        setGroup(action.status === 'approved' ? 'in_library' : 'not_in_library')
        setQuery('')
      }
    })
  }

  const remove = (action: ActionDefinition): void => {
    void confirmAction({
      title: t.deleteTitle,
      description: action.name,
      confirmLabel: t.deleteLabel,
      variant: 'destructive'
    }).then(confirmed => {
      if (!confirmed) {
        return
      }

      void run(
        async () => {
          await deleteAction(action.id)
          setComposer(current => (current?.actionId === action.id ? null : current))
        },
        { failure: t.deleteFailed, key: `delete:${action.id}` }
      )
    })
  }

  const retryReview = (action: ActionDefinition): void => {
    void run(() => retryActionReview(action), { failure: t.retryFailed, key: `retry:${action.id}` })
  }

  return (
    <SettingsContent>
      <SettingsSectionIntro hint={t.intro} title={t.title} />
      <div className="mb-3">
        <CapsuleTabs
          ariaLabel={t.title}
          onChange={setGroup}
          options={[
            { value: 'in_library', label: t.inLibrary },
            { value: 'not_in_library', label: t.notInLibrary }
          ]}
          value={group}
        />
      </div>
      <div className="mb-3">
        <LibraryToolbar
          createDisabled={busy}
          createLabel={t.create}
          onCreate={() => setComposer({ ...EMPTY_COMPOSER })}
          onQueryChange={setQuery}
          query={query}
          searchLabel={t.searchAria}
        />
      </div>
      {error && <ErrorBanner message={t.loadFailed} />}
      {composer && (
        <SettingCard className="mb-3">
          <div className="flex flex-col gap-3 p-4">
            <h3 className="text-sm font-medium">{composer.actionId ? studio.actionEditTitle : t.create}</h3>
            {composerError && <p className="text-[11px] text-danger-fg">{composerError}</p>}
            {!composer.preview && (
              <label className="block">
                <span className={FIELD_LABEL}>{studio.actionCreate}</span>
                <textarea
                  className={`${INPUT_CLASS} min-h-20`}
                  onChange={event =>
                    setComposer(current => (current ? { ...current, description: event.currentTarget.value } : current))
                  }
                  value={composer.description}
                />
              </label>
            )}
            {composer.preview && (
              <div className="space-y-2">
                <p className="text-sm font-semibold text-strong">{composer.preview.summary}</p>
                <dl className="grid gap-1 text-xs text-muted">
                  {composer.preview.details.map(item => (
                    <div className="flex gap-2" key={`${item.label}:${item.value}`}>
                      <dt className="shrink-0 text-faint">{item.label}</dt>
                      <dd className="min-w-0 break-words">{item.value}</dd>
                    </div>
                  ))}
                </dl>
                <label className="block">
                  <span className={FIELD_LABEL}>{studio.refineLabel}</span>
                  <input
                    className={INPUT_CLASS}
                    onChange={event =>
                      setComposer(current =>
                        current ? { ...current, refinement: event.currentTarget.value } : current
                      )
                    }
                    placeholder={studio.refinePlaceholder}
                    value={composer.refinement}
                  />
                </label>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <button
                className={BTN_PRIMARY}
                disabled={interpreting || busyKey === 'save'}
                onClick={() => (composer.preview ? save() : void interpret())}
                type="button"
              >
                {interpreting ? studio.describing : composer.preview ? studio.actionSave : studio.describe}
              </button>
              {composer.preview && (
                <button className={BTN_SUBTLE} disabled={interpreting} onClick={() => void interpret()} type="button">
                  {studio.refine}
                </button>
              )}
              <button className={BTN_GHOST} onClick={() => setComposer(null)} type="button">
                {t.cancel}
              </button>
              {composer.actionId && (
                <button
                  className={BTN_DANGER}
                  disabled={busyKey !== null}
                  onClick={() => {
                    const target = actions.find(action => action.id === composer.actionId)

                    if (target) {
                      remove(target)
                    }
                  }}
                  type="button"
                >
                  {t.deleteLabel}
                </button>
              )}
            </div>
          </div>
        </SettingCard>
      )}
      {!loaded || (loading && actions.length === 0) ? (
        <LoadingBlock label={t.loading} />
      ) : actions.length === 0 ? (
        <EmptyState
          description={query ? t.emptySearch : group === 'in_library' ? t.empty : t.emptyUnlisted}
          title={t.title}
        />
      ) : (
        <SettingCard divided={false}>
          {actions.map(action => (
            <div className="flex flex-col gap-2 border-b border-line-hairline p-4 last:border-b-0" key={action.id}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-strong">{action.name}</span>
                <Badge>{action.clip_kind === 'loop' ? t.clipLoop : t.clipOnce}</Badge>
                <Badge>{`${action.duration_seconds}${t.durationSuffix}`}</Badge>
                <Badge>{t.motionIntensities[action.motion_intensity]}</Badge>
                <Badge>{action.source === 'preset' ? t.builtin : t.custom}</Badge>
              </div>
              <p className="whitespace-pre-wrap text-xs text-muted">{action.motion_description}</p>
              {group === 'not_in_library' && (
                <CreationFeedback
                  description={
                    action.review_reason
                      ? `${t.reviewReasonLabel}：${action.review_reason}`
                      : t.motionIntensities[action.motion_intensity]
                  }
                  state={
                    action.status === 'failed' || action.status === 'rejected'
                      ? 'error'
                      : action.status === 'deferred'
                        ? 'waiting'
                        : 'review'
                  }
                  title={t.statusLabels[action.status]}
                />
              )}
              <div className="flex flex-wrap gap-2">
                {group === 'in_library' && defaultOutfit && action.status === 'approved' && (
                  <button
                    className={BTN_SUBTLE}
                    disabled={busyKey !== null}
                    onClick={() => makeClip(action)}
                    type="button"
                  >
                    {busyKey === `clip:${action.id}` ? t.makeClipBusy : t.makeClip}
                  </button>
                )}
                <button
                  className={BTN_GHOST}
                  disabled={busyKey !== null}
                  onClick={() => toggleMedia(action)}
                  type="button"
                >
                  {mediaPanel.loadingId === action.id
                    ? t.mediaLoading
                    : mediaPanel.expandedId === action.id
                      ? t.mediaHide
                      : t.mediaShow}
                </button>
                {action.source !== 'preset' && (
                  <button
                    className={BTN_GHOST}
                    disabled={busyKey !== null || action.status === 'pending' || action.status === 'reviewing'}
                    onClick={() =>
                      setComposer({
                        actionId: action.id,
                        expectedUpdatedAt: action.updated_at,
                        description: '',
                        refinement: '',
                        preview: null
                      })
                    }
                    type="button"
                  >
                    {t.edit}
                  </button>
                )}
                {(action.status === 'failed' || action.status === 'deferred') && (
                  <button
                    className={BTN_SUBTLE}
                    disabled={busyKey !== null}
                    onClick={() => retryReview(action)}
                    type="button"
                  >
                    {t.retry}
                  </button>
                )}
                {action.source !== 'preset' && (
                  <button
                    className={BTN_DANGER}
                    disabled={busyKey !== null}
                    onClick={() => remove(action)}
                    type="button"
                  >
                    {t.deleteLabel}
                  </button>
                )}
              </div>
              {mediaPanel.expandedId === action.id && (
                <ActionMediaPanel
                  entries={mediaPanel.rows[action.id] ?? []}
                  error={mediaPanel.errorId === action.id}
                  loading={mediaPanel.loadingId === action.id}
                  t={t}
                  visualsLabels={dict.visuals}
                />
              )}
            </div>
          ))}
        </SettingCard>
      )}
      <Pager
        busy={loading}
        className="mt-3"
        hasMore={Boolean(nextCursor)}
        mode="more"
        onLoadMore={() => void loadMoreActions(group)}
      />
    </SettingsContent>
  )
}
