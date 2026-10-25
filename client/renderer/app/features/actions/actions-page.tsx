import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import {
  $actions,
  $actionsError,
  $actionsLoading,
  $actionsNextCursor,
  type ActionClipKind,
  type ActionDefinition,
  createAction,
  deleteAction,
  loadMoreActions,
  refreshActions,
  updateAction
} from '@/modules/visuals'
import {
  Badge,
  BTN_DANGER,
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SUBTLE,
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

interface ActionDraft {
  name: string
  motion_description: string
  use_when: string
  avoid_when: string
  clip_kind: ActionClipKind
  duration_seconds: number
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
    duration_seconds: action.duration_seconds
  }
}

function lines(value: string): string[] {
  return value
    .split('\n')
    .map(item => item.trim())
    .filter(Boolean)
}

export function ActionsPage(): React.JSX.Element {
  const dict = useStrings()
  const t = dict.actions
  const auth = useStore($auth)
  const actions = useStore($actions)
  const loading = useStore($actionsLoading)
  const error = useStore($actionsError)
  const nextCursor = useStore($actionsNextCursor)
  const [query, setQuery] = useState('')

  const [editing, setEditing] = useState<
    { id: null; draft: ActionDraft } | { id: string; draft: ActionDraft; expectedUpdatedAt: string } | null
  >(null)

  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      return
    }

    const timer = setTimeout(() => void refreshActions(query), 200)

    return () => clearTimeout(timer)
  }, [auth.kind, query])

  const visible = actions

  const save = (): void => {
    if (!editing) {
      return
    }

    const draft = editing.draft

    const body = {
      name: draft.name.trim(),
      motion_description: draft.motion_description.trim(),
      use_when: lines(draft.use_when),
      avoid_when: lines(draft.avoid_when),
      clip_kind: draft.clip_kind,
      duration_seconds: draft.duration_seconds
    }

    setBusy(true)

    void (
      editing.id === null
        ? createAction(body)
        : updateAction(editing.id, { ...body, expected_updated_at: editing.expectedUpdatedAt })
    )
      .then(() => setEditing(null))
      .catch(error_ => notifyError(error_, t.saveFailed))
      .finally(() => setBusy(false))
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

      setBusy(true)

      void deleteAction(action.id)
        .catch(error_ => notifyError(error_, t.deleteFailed))
        .finally(() => setBusy(false))
    })
  }

  return (
    <SettingsContent>
      <SettingsSectionIntro hint={t.intro} title={t.title} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchField ariaLabel={t.searchAria} onChange={setQuery} placeholder={t.searchPlaceholder} value={query} />
        <button className={BTN_PRIMARY} onClick={() => setEditing({ id: null, draft: emptyDraft() })} type="button">
          {t.create}
        </button>
      </div>
      {error && <ErrorBanner message={t.loadFailed} />}
      {editing && (
        <SettingCard className="mb-3">
          <div className="flex flex-col gap-3 p-4">
            <h3 className="text-sm font-medium">{editing.id === null ? t.create : t.edit}</h3>
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
            <div className="flex gap-2">
              <button
                className={BTN_PRIMARY}
                disabled={busy || !editing.draft.name.trim()}
                onClick={save}
                type="button"
              >
                {t.save}
              </button>
              <button className={BTN_GHOST} disabled={busy} onClick={() => setEditing(null)} type="button">
                {t.cancel}
              </button>
            </div>
          </div>
        </SettingCard>
      )}
      {loading && actions.length === 0 ? (
        <LoadingBlock label={t.loading} />
      ) : visible.length === 0 ? (
        <EmptyState description={query ? t.emptySearch : t.empty} title={t.title} />
      ) : (
        <SettingCard divided={false}>
          {visible.map(action => (
            <div className="flex flex-col gap-2 border-b border-line-hairline p-4 last:border-b-0" key={action.id}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-strong">{action.name}</span>
                <Badge>{action.clip_kind === 'loop' ? t.clipLoop : t.clipOnce}</Badge>
                <Badge>{`${action.duration_seconds}${t.durationSuffix}`}</Badge>
                <Badge>{t.statusLabels[action.status] ?? action.status}</Badge>
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
              {action.review_reason && (
                <p className="text-[11px] text-warning-fg">
                  {t.reviewReasonLabel}：{action.review_reason}
                </p>
              )}
              <div className="flex gap-2">
                <button
                  className={BTN_SUBTLE}
                  disabled={busy}
                  onClick={() =>
                    setEditing({ id: action.id, draft: draftOf(action), expectedUpdatedAt: action.updated_at })
                  }
                  type="button"
                >
                  {t.edit}
                </button>
                <button className={BTN_DANGER} disabled={busy} onClick={() => remove(action)} type="button">
                  {t.deleteLabel}
                </button>
              </div>
            </div>
          ))}
        </SettingCard>
      )}
      {nextCursor ? (
        <button className={BTN_SUBTLE} disabled={loading || busy} onClick={() => void loadMoreActions()} type="button">
          {t.loadMore}
        </button>
      ) : null}
    </SettingsContent>
  )
}
