import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $presentedOutfit, hydrateWardrobe } from '@/modules/character'
import type { ActionClipKind, ActionDefinition } from '@/modules/visuals'
import {
  $visualsPresentation,
  actionGroups,
  type ActionLibraryGroup,
  type ActionMediaEntry,
  createAction,
  deleteAction,
  loadMoreActions,
  refreshActionGroups,
  refreshActions,
  retryActionReview,
  updateAction,
  visualsApi
} from '@/modules/visuals'
import { RENDERER_TIMINGS } from '@/shared/constants'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import {
  Badge,
  BTN_DANGER,
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SUBTLE,
  CapsuleTabs,
  confirmAction,
  EmptyState,
  ErrorBanner,
  FIELD_LABEL,
  INPUT_CLASS,
  LoadingBlock,
  PanelSelect,
  SearchField,
  SettingCard,
  SettingsContent,
  SettingsSectionIntro
} from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import {
  ACTION_MOTION_INTENSITIES,
  ACTION_PERFORMANCE_ROLES,
  ACTION_SEMANTIC_TAGS,
  type ActionSemanticTag,
  isActionDesignValid,
  parseActionConditions
} from '../../../../../shared/actions'

interface ActionDraft {
  name: string
  motion_description: string
  use_when: string
  avoid_when: string
  clip_kind: ActionClipKind
  duration_seconds: number
  semantic_tags: ActionSemanticTag[]
  performance_role: ActionDefinition['performance_role'] | null
  motion_intensity: ActionDefinition['motion_intensity']
  interruptible: boolean
  allowed_triggers?: ActionDefinition['allowed_triggers']
  reason?: string
}

function emptyDraft(): ActionDraft {
  return {
    name: '',
    motion_description: '',
    use_when: '',
    avoid_when: '',
    clip_kind: 'once',
    duration_seconds: 4,
    semantic_tags: [],
    performance_role: null,
    motion_intensity: 'normal',
    interruptible: true
  }
}

function draftOf(action: ActionDefinition): ActionDraft {
  return {
    name: action.name,
    motion_description: action.motion_description,
    use_when: action.use_when.join('\n'),
    avoid_when: action.avoid_when.join('\n'),
    clip_kind: action.clip_kind,
    duration_seconds: action.duration_seconds,
    semantic_tags: [...action.semantic_tags],
    performance_role: action.performance_role,
    motion_intensity: action.motion_intensity,
    interruptible: action.interruptible,
    allowed_triggers: action.allowed_triggers,
    reason: action.reason
  }
}

function draftBody(draft: ActionDraft) {
  return {
    name: draft.name.trim(),
    motion_description: draft.motion_description.trim(),
    use_when: parseActionConditions(draft.use_when),
    avoid_when: parseActionConditions(draft.avoid_when),
    clip_kind: draft.clip_kind,
    duration_seconds: draft.duration_seconds,
    semantic_tags: draft.semantic_tags,
    performance_role: draft.performance_role ?? (draft.clip_kind === 'loop' ? 'sustained' : 'reaction'),
    motion_intensity: draft.motion_intensity,
    interruptible: draft.interruptible,
    allowed_triggers: draft.allowed_triggers,
    reason: draft.reason
  }
}

function actionBody(action: ActionDefinition) {
  return {
    name: action.name,
    motion_description: action.motion_description,
    use_when: action.use_when,
    avoid_when: action.avoid_when,
    clip_kind: action.clip_kind,
    duration_seconds: action.duration_seconds,
    semantic_tags: action.semantic_tags,
    performance_role: action.performance_role,
    motion_intensity: action.motion_intensity,
    interruptible: action.interruptible,
    allowed_triggers: action.allowed_triggers,
    reason: action.reason
  }
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
            {entry.clip_kind && <Badge>{entry.clip_kind === 'loop' ? t.clipLoop : t.clipOnce}</Badge>}
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
      {!entries.some(entry => entry.stage_ready) && <p className="mt-1 text-[11px] text-faint">{t.mediaStageHint}</p>}
    </div>
  )
}

export function ActionsPage(): React.JSX.Element {
  const auth = useStore($auth)

  return <ActionsPageContent key={auth.kind === 'authenticated' ? auth.snapshot.sessionId : auth.kind} />
}

function ActionsPageContent(): React.JSX.Element {
  const dict = useStrings()
  const t = dict.actions
  const auth = useStore($auth)
  const [group, setGroup] = useState<ActionLibraryGroup>('in_library')
  const actions = useStore(actionGroups[group].rows)
  const inLibraryActions = useStore(actionGroups.in_library.rows)
  const notInLibraryActions = useStore(actionGroups.not_in_library.rows)
  const loading = useStore(actionGroups[group].loading)
  const loaded = useStore(actionGroups[group].loaded)
  const error = useStore(actionGroups[group].error)
  const nextCursor = useStore(actionGroups[group].nextCursor)
  const [query, setQuery] = useState('')

  const [editing, setEditing] = useState<
    { id: null; draft: ActionDraft } | { id: string; draft: ActionDraft; expectedUpdatedAt: string } | null
  >(null)

  const [busy, setBusy] = useState(false)
  const [clipBusyId, setClipBusyId] = useState<string | null>(null)

  const [mediaPanel, setMediaPanel] = useState<{
    expandedId: string | null
    loadingId: string | null
    errorId: string | null
    rows: Record<string, ActionMediaEntry[]>
  }>({ expandedId: null, loadingId: null, errorId: null, rows: {} })

  const defaultOutfit = useStore($presentedOutfit)
  const presentation = useStore($visualsPresentation)
  const mediaGeneration = useRef(0)

  const stageKey = JSON.stringify([
    presentation?.static_presentation?.identity_hash ?? presentation?.composition?.identity_hash,
    presentation?.static_presentation?.outfit?.content_hash ?? presentation?.composition?.outfit_material?.content_hash,
    presentation?.target_width,
    presentation?.target_height,
    presentation?.ambient_catalog.map(entry => [entry.visual_id, entry.version])
  ])

  useEffect(() => {
    mediaGeneration.current += 1
    setMediaPanel({ expandedId: null, loadingId: null, errorId: null, rows: {} })
  }, [stageKey])

  const begin = useAsyncGuard()

  const editingId = editing?.id

  useEffect(() => {
    if (auth.kind === 'authenticated') {
      void hydrateWardrobe()
    }
  }, [auth])

  const makeClip = (action: ActionDefinition): void => {
    if (!defaultOutfit || busy || clipBusyId) {
      return
    }

    const scope = captureAuthScope()
    const live = begin()

    setClipBusyId(action.id)
    void visualsApi
      .createActorClip({
        outfit_id: defaultOutfit.id,
        action_id: action.id
      })
      .then(() => {
        if (scope?.() && live()) {
          notify({ message: t.makeClipDone })
        }
      })
      .catch(error_ => {
        if (scope?.() && live()) {
          notifyError(error_, t.makeClipFailed)
        }
      })
      .finally(() => {
        if (scope?.() && live()) {
          setClipBusyId(null)
        }
      })
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

    const scope = captureAuthScope()
    setMediaPanel(current => ({ ...current, expandedId: action.id, loadingId: action.id, errorId: null }))
    const generation = mediaGeneration.current
    void visualsApi
      .listActionMedia(action.id)
      .then(response => {
        if (scope?.() && generation === mediaGeneration.current) {
          setMediaPanel(current => ({
            ...current,
            rows: { ...current.rows, [action.id]: response.media },
            loadingId: current.loadingId === action.id ? null : current.loadingId
          }))
        }
      })
      .catch(() => {
        if (scope?.() && generation === mediaGeneration.current) {
          setMediaPanel(current => ({
            ...current,
            errorId: action.id,
            loadingId: current.loadingId === action.id ? null : current.loadingId
          }))
        }
      })
  }

  const latestEditingAction =
    editingId === null || editingId === undefined
      ? undefined
      : [...inLibraryActions, ...notInLibraryActions].find(action => action.id === editingId)

  const editingLocked = Boolean(
    editingId !== null &&
    editingId !== undefined &&
    (latestEditingAction?.status === 'pending' || latestEditingAction?.status === 'reviewing')
  )

  useEffect(() => {
    if (editingId === null || editingId === undefined) {
      return
    }

    const latest = [...inLibraryActions, ...notInLibraryActions].find(action => action.id === editingId)

    if (!latest) {
      return
    }

    setEditing(current => {
      if (
        !current ||
        current.id !== editingId ||
        current.expectedUpdatedAt === latest.updated_at ||
        JSON.stringify(draftBody(current.draft)) !== JSON.stringify(actionBody(latest))
      ) {
        return current
      }

      // 脏草稿保留原修订；草稿未改时推进到最新修订，避免后台评审更新后无法继续保存。
      return { ...current, expectedUpdatedAt: latest.updated_at }
    })
  }, [editingId, inLibraryActions, notInLibraryActions])

  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      return
    }

    const timer = setTimeout(() => void refreshActions(query, false, group), RENDERER_TIMINGS.actionSearchDebounceMs)

    return () => clearTimeout(timer)
  }, [auth, group, query])

  const hasPendingReview = [...inLibraryActions, ...notInLibraryActions].some(
    action => action.status === 'pending' || action.status === 'reviewing'
  )

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

  const visible = actions

  const validDraft = editing && isActionDesignValid(draftBody(editing.draft))

  const save = (): void => {
    if (!editing || busy || editingLocked || !validDraft) {
      return
    }

    const draft = editing.draft
    const scope = captureAuthScope()
    const body = draftBody(draft)

    setBusy(true)

    void (
      editing.id === null
        ? createAction(body)
        : updateAction(editing.id, { ...body, expected_updated_at: editing.expectedUpdatedAt })
    )
      .then(action => {
        if (!scope?.()) {
          return
        }

        setEditing(null)
        setGroup(action.status === 'approved' ? 'in_library' : 'not_in_library')
        setQuery('')
      })
      .catch(error_ => {
        if (scope?.()) {
          notifyError(error_, t.saveFailed)
        }
      })
      .finally(() => {
        if (scope?.()) {
          setBusy(false)
        }
      })
  }

  const remove = (action: ActionDefinition): void => {
    const scope = captureAuthScope()
    void confirmAction({
      title: t.deleteTitle,
      description: action.name,
      confirmLabel: t.deleteLabel,
      variant: 'destructive'
    }).then(confirmed => {
      if (!confirmed || !scope?.()) {
        return
      }

      setBusy(true)

      void deleteAction(action.id)
        .then(() => {
          if (scope()) {
            setEditing(current => (current?.id === action.id ? null : current))
          }
        })
        .catch(error_ => {
          if (scope()) {
            notifyError(error_, t.deleteFailed)
          }
        })
        .finally(() => {
          if (scope()) {
            setBusy(false)
          }
        })
    })
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
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchField ariaLabel={t.searchAria} onChange={setQuery} placeholder={t.searchPlaceholder} value={query} />
        <button
          className={BTN_PRIMARY}
          disabled={busy}
          onClick={() => setEditing({ id: null, draft: emptyDraft() })}
          type="button"
        >
          {t.create}
        </button>
      </div>
      {error && <ErrorBanner message={t.loadFailed} />}
      {editing && (
        <SettingCard className="mb-3">
          <fieldset className="flex flex-col gap-3 p-4" disabled={busy || editingLocked}>
            <h3 className="text-sm font-medium">{editing.id === null ? t.create : t.edit}</h3>
            {editingLocked ? <p className="text-[11px] text-warning-fg">{t.editingLocked}</p> : null}
            <label className="block">
              <span className={FIELD_LABEL}>{t.nameLabel}</span>
              <input
                className={INPUT_CLASS}
                onChange={event =>
                  setEditing({ ...editing, draft: { ...editing.draft, name: event.currentTarget.value } })
                }
                value={editing.draft.name}
              />
            </label>
            <label className="block">
              <span className={FIELD_LABEL}>{t.motionLabel}</span>
              <textarea
                className={`${INPUT_CLASS} min-h-24`}
                onChange={event =>
                  setEditing({ ...editing, draft: { ...editing.draft, motion_description: event.currentTarget.value } })
                }
                value={editing.draft.motion_description}
              />
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className={FIELD_LABEL}>{t.useWhenLabel}</span>
                <textarea
                  className={`${INPUT_CLASS} min-h-16`}
                  onChange={event =>
                    setEditing({ ...editing, draft: { ...editing.draft, use_when: event.currentTarget.value } })
                  }
                  value={editing.draft.use_when}
                />
              </label>
              <label className="block">
                <span className={FIELD_LABEL}>{t.avoidWhenLabel}</span>
                <textarea
                  className={`${INPUT_CLASS} min-h-16`}
                  onChange={event =>
                    setEditing({ ...editing, draft: { ...editing.draft, avoid_when: event.currentTarget.value } })
                  }
                  value={editing.draft.avoid_when}
                />
              </label>
            </div>
            <p className="text-[11px] text-faint">{t.conditionsHint}</p>
            <div className="flex flex-wrap items-center gap-3">
              <div>
                <span className={FIELD_LABEL}>{t.clipLabel}</span>
                <PanelSelect
                  ariaLabel={t.clipLabel}
                  onChange={value =>
                    setEditing({
                      ...editing,
                      draft: { ...editing.draft, clip_kind: value, performance_role: null }
                    })
                  }
                  options={[
                    { value: 'once', label: t.clipOnce },
                    { value: 'loop', label: t.clipLoop }
                  ]}
                  value={editing.draft.clip_kind}
                />
              </div>
              <label className="block">
                <span className={FIELD_LABEL}>{t.durationLabel}</span>
                <input
                  className={`${INPUT_CLASS} w-24`}
                  max={15}
                  min={1}
                  onChange={event =>
                    setEditing({
                      ...editing,
                      draft: { ...editing.draft, duration_seconds: Number(event.currentTarget.value) || 1 }
                    })
                  }
                  type="number"
                  value={editing.draft.duration_seconds}
                />
              </label>
            </div>
            <div>
              <span className={FIELD_LABEL}>{t.semanticTagsLabel}</span>
              <div className="flex flex-wrap gap-1.5">
                {ACTION_SEMANTIC_TAGS.map(tag => {
                  const active = editing.draft.semantic_tags.includes(tag)

                  return (
                    <button
                      aria-pressed={active}
                      className={active ? BTN_PRIMARY : BTN_GHOST}
                      key={tag}
                      onClick={event => {
                        event.preventDefault()
                        setEditing({
                          ...editing,
                          draft: {
                            ...editing.draft,
                            semantic_tags: active
                              ? editing.draft.semantic_tags.filter(item => item !== tag)
                              : editing.draft.semantic_tags.length >= 8
                                ? editing.draft.semantic_tags
                                : [...editing.draft.semantic_tags, tag]
                          }
                        })
                      }}
                      type="button"
                    >
                      {t.semanticTagLabels[tag]}
                    </button>
                  )
                })}
              </div>
              <p className="mt-1 text-[11px] text-faint">{t.semanticTagsHint}</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <span className={FIELD_LABEL}>{t.performanceRoleLabel}</span>
                <PanelSelect<ActionDefinition['performance_role'] | ''>
                  ariaLabel={t.performanceRoleLabel}
                  onChange={value =>
                    setEditing({
                      ...editing,
                      draft: {
                        ...editing.draft,
                        performance_role: value || null
                      }
                    })
                  }
                  options={[
                    { value: '', label: '—' },
                    ...ACTION_PERFORMANCE_ROLES.filter(
                      role => (role === 'reaction') === (editing.draft.clip_kind === 'once')
                    ).map(role => ({ value: role, label: t.performanceRoles[role] }))
                  ]}
                  value={editing.draft.performance_role ?? ''}
                />
              </div>
              <div>
                <span className={FIELD_LABEL}>{t.motionIntensityLabel}</span>
                <PanelSelect<ActionDefinition['motion_intensity']>
                  ariaLabel={t.motionIntensityLabel}
                  onChange={value =>
                    setEditing({
                      ...editing,
                      draft: {
                        ...editing.draft,
                        motion_intensity: value
                      }
                    })
                  }
                  options={ACTION_MOTION_INTENSITIES.map(level => ({
                    value: level,
                    label: t.motionIntensities[level]
                  }))}
                  value={editing.draft.motion_intensity}
                />
                <p className="mt-1 text-[11px] text-faint">{t.motionIntensityHint}</p>
              </div>
            </div>
            <label className="flex items-center gap-2 text-xs text-muted">
              <input
                checked={editing.draft.interruptible}
                onChange={event =>
                  setEditing({ ...editing, draft: { ...editing.draft, interruptible: event.currentTarget.checked } })
                }
                type="checkbox"
              />
              {t.interruptibleLabel}
            </label>
          </fieldset>
          <div className="flex gap-2 px-4 pb-4">
            <button
              className={BTN_PRIMARY}
              disabled={busy || editingLocked || !validDraft}
              onClick={save}
              type="button"
            >
              {t.save}
            </button>
            <button className={BTN_GHOST} disabled={busy} onClick={() => setEditing(null)} type="button">
              {t.cancel}
            </button>
          </div>
        </SettingCard>
      )}
      {!loaded || (loading && actions.length === 0) ? (
        <LoadingBlock label={t.loading} />
      ) : visible.length === 0 ? (
        <EmptyState
          description={query ? t.emptySearch : group === 'in_library' ? t.empty : t.emptyUnlisted}
          title={t.title}
        />
      ) : (
        <SettingCard divided={false}>
          {visible.map(action => (
            <div className="flex flex-col gap-2 border-b border-line-hairline p-4 last:border-b-0" key={action.id}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-strong">{action.name}</span>
                <Badge>{action.clip_kind === 'loop' ? t.clipLoop : t.clipOnce}</Badge>
                <Badge>{`${action.duration_seconds}${t.durationSuffix}`}</Badge>
                <Badge>{t.motionIntensities[action.motion_intensity]}</Badge>
                <Badge>{action.source === 'preset' ? t.builtin : t.custom}</Badge>
                {group === 'not_in_library' && <Badge>{t.statusLabels[action.status]}</Badge>}
              </div>
              {action.semantic_tags.length > 0 && (
                <p className="text-[11px] text-faint">{`${t.semanticTagsLabel}：${action.semantic_tags.map(tag => t.semanticTagLabels[tag]).join('、')}`}</p>
              )}
              <p className="whitespace-pre-wrap text-xs text-muted">{action.motion_description}</p>
              {(action.use_when.length > 0 || action.avoid_when.length > 0) && (
                <p className="text-[11px] text-faint">
                  {[
                    action.use_when.length > 0 ? `${t.useWhenLabel}：${action.use_when.join('、')}` : '',
                    action.avoid_when.length > 0 ? `${t.avoidWhenLabel}：${action.avoid_when.join('、')}` : ''
                  ]
                    .filter(Boolean)
                    .join('；')}
                </p>
              )}
              {group === 'not_in_library' && action.review_reason && (
                <p className="text-[11px] text-warning-fg">
                  {t.reviewReasonLabel}：{action.review_reason}
                </p>
              )}
              <div className="flex gap-2">
                {group === 'in_library' && defaultOutfit && action.status === 'approved' && (
                  <button
                    className={BTN_SUBTLE}
                    disabled={busy || clipBusyId !== null}
                    onClick={() => makeClip(action)}
                    type="button"
                  >
                    {clipBusyId === action.id ? t.makeClipBusy : t.makeClip}
                  </button>
                )}
                <button
                  className={BTN_SUBTLE}
                  disabled={busy || mediaPanel.loadingId !== null}
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
                    className={BTN_SUBTLE}
                    disabled={busy || action.status === 'pending' || action.status === 'reviewing'}
                    onClick={() =>
                      setEditing({ id: action.id, draft: draftOf(action), expectedUpdatedAt: action.updated_at })
                    }
                    type="button"
                  >
                    {t.edit}
                  </button>
                )}
                {(action.status === 'failed' || action.status === 'deferred') && (
                  <button
                    className={BTN_SUBTLE}
                    disabled={busy || editing?.id === action.id}
                    onClick={() => {
                      const scope = captureAuthScope()
                      setBusy(true)
                      void retryActionReview(action)
                        .catch(error_ => {
                          if (scope?.()) {
                            notifyError(error_, t.retryFailed)
                          }
                        })
                        .finally(() => {
                          if (scope?.()) {
                            setBusy(false)
                          }
                        })
                    }}
                    type="button"
                  >
                    {t.retry}
                  </button>
                )}
                {action.source !== 'preset' && (
                  <button className={BTN_DANGER} disabled={busy} onClick={() => remove(action)} type="button">
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
      {nextCursor ? (
        <button
          className={BTN_SUBTLE}
          disabled={loading || busy}
          onClick={() => void loadMoreActions(group)}
          type="button"
        >
          {t.loadMore}
        </button>
      ) : null}
    </SettingsContent>
  )
}
