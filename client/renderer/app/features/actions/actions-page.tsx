import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import {
  type ActionClipKind,
  type ActionDefinition,
  actionGroups,
  type ActionLibraryGroup,
  createAction,
  deleteAction,
  loadMoreActions,
  refreshActionGroups,
  refreshActions,
  retryActionReview,
  updateAction
} from '@/modules/visuals'
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
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { isActionDesignValid, parseActionConditions } from '../../../../../shared/actions'

interface ActionDraft {
  name: string
  motion_description: string
  use_when: string
  avoid_when: string
  clip_kind: ActionClipKind
  duration_seconds: number
  allowed_triggers?: ActionDefinition['allowed_triggers']
  reason?: string
}

function emptyDraft(): ActionDraft {
  return { name: '', motion_description: '', use_when: '', avoid_when: '', clip_kind: 'once', duration_seconds: 4 }
}

function draftOf(action: ActionDefinition): ActionDraft {
  return {
    name: action.name,
    motion_description: action.motion_description,
    use_when: action.use_when.join('\n'),
    avoid_when: action.avoid_when.join('\n'),
    clip_kind: action.clip_kind,
    duration_seconds: action.duration_seconds,
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
    allowed_triggers: action.allowed_triggers,
    reason: action.reason
  }
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

  const editingId = editing?.id

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

    const timer = setTimeout(() => void refreshActions(query, false, group), 200)

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
    }, 5000)

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
                      draft: { ...editing.draft, clip_kind: value === 'loop' ? 'loop' : 'once' }
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
                <Badge>{action.source === 'preset' ? t.builtin : t.custom}</Badge>
                {group === 'not_in_library' && <Badge>{t.statusLabels[action.status]}</Badge>}
              </div>
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
                <button
                  className={BTN_SUBTLE}
                  disabled={busy || action.status === 'pending' || action.status === 'reviewing'}
                  onClick={() =>
                    setEditing(
                      action.source === 'preset'
                        ? { id: null, draft: draftOf(action) }
                        : { id: action.id, draft: draftOf(action), expectedUpdatedAt: action.updated_at }
                    )
                  }
                  type="button"
                >
                  {action.source === 'preset' ? t.copy : t.edit}
                </button>
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
