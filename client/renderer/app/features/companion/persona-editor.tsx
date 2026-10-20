import { useStore } from '@nanostores/react'
import { useState } from 'react'

import {
  $persona,
  applyPersonaResponse,
  hydratePersona,
  PERSONALITY_PRESETS,
  RELATIONSHIP_PRESETS,
  SPEAKING_STYLE_PRESETS
} from '@/modules/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage, ipcErrorStatus } from '@/shared/lib/ipc-error'
import { authedResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import {
  BTN_GHOST,
  BTN_SUBTLE,
  Chip,
  CreationActionBar,
  CreationFeedback,
  FIELD_LABEL,
  HINT_TEXT,
  INPUT_CLASS,
  SECTION_TITLE,
  WizardModal
} from '@/shared/panel'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import type { PersonaEditableField, PersonaUpdateRequest } from '@protocol'

import { $companionEditors, updateCompanionEditors } from './companion-editor-state'

type PersonaValues = Record<PersonaEditableField, string>
interface PersonaField {
  key: PersonaEditableField
  label: string
  placeholder: string
  presets?: readonly string[]
}

function valuesOf(persona: ReturnType<typeof $persona.get>): PersonaValues {
  return {
    name: persona?.name ?? '',
    personality: persona?.personality ?? '',
    relationship: persona?.relationship ?? '',
    speaking_style: persona?.speaking_style ?? ''
  }
}

function baseOf(persona: ReturnType<typeof $persona.get>): Record<PersonaEditableField, string | null> {
  return {
    name: persona?.name ?? null,
    personality: persona?.personality ?? null,
    relationship: persona?.relationship ?? null,
    speaking_style: persona?.speaking_style ?? null
  }
}

export function PersonaSection({
  editorOpen,
  onEditRequested,
  onEditorClosed
}: {
  editorOpen: boolean
  onEditRequested: () => void
  onEditorClosed: () => void
}): React.JSX.Element {
  const dict = useStrings()
  const t = dict.settings.persona
  const english = useStore($locale) === 'en'
  const persona = useStore($persona)
  const draft = useStore($companionEditors).persona
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  const begin = useAsyncGuard()

  const fields: readonly PersonaField[] = [
    { key: 'name', label: t.nameLabel, placeholder: t.namePlaceholder },
    {
      key: 'relationship',
      label: t.relationshipLabel,
      placeholder: t.relationshipPlaceholder,
      presets: RELATIONSHIP_PRESETS
    },
    {
      key: 'personality',
      label: t.personalityLabel,
      placeholder: t.personalityPlaceholder,
      presets: PERSONALITY_PRESETS
    },
    {
      key: 'speaking_style',
      label: t.speakingStyleLabel,
      placeholder: t.speakingStylePlaceholder,
      presets: SPEAKING_STYLE_PRESETS
    }
  ]

  const dirty = draft && fields.some(({ key }) => draft.values[key].trim() !== (draft.base[key] ?? ''))
  const latest = valuesOf(persona)
  const latestBase = baseOf(persona)
  const conflict = draft && fields.some(({ key }) => latestBase[key] !== draft.base[key])

  const save = async (): Promise<void> => {
    if (!draft) {
      return
    }

    if (!draft.values.name.trim() || !draft.values.speaking_style.trim()) {
      setHint(!draft.values.name.trim() ? t.hintEmptyName : t.hintEmptySpeakingStyle)

      return
    }

    if (!draft.values.personality.trim()) {
      setHint(english ? 'Enter a personality.' : '请填写性格。')

      return
    }

    const isLive = begin()
    const isCurrent = captureAuthScope()

    if (!isCurrent) {
      return
    }

    setBusy(true)
    setHint(null)

    try {
      const expectedFields = Object.fromEntries(fields.map(({ key }) => [key, draft.base[key]]))

      const body: PersonaUpdateRequest = {
        definition_json: JSON.stringify({
          name: draft.values.name.trim(),
          personality: draft.values.personality.trim(),
          relationship: draft.values.relationship.trim(),
          speaking_style: draft.values.speaking_style.trim()
        }),
        expected_fields: expectedFields
      }

      const saved = await authedResource('companion.put_persona', { body })

      if (!saved.ok) {
        if (saved.reason === 'err') {
          throw saved.error
        }

        return
      }

      if (!isCurrent()) {
        return
      }

      if (!saved.value) {
        throw new Error(t.hintSaveFailed)
      }

      applyPersonaResponse(saved.value)

      if (JSON.stringify($companionEditors.get().persona) === JSON.stringify(draft)) {
        updateCompanionEditors({ persona: null })
      }
    } catch (error) {
      if (!isCurrent()) {
        return
      }

      const refreshed = await hydratePersona({ silent: true })

      if (!isCurrent()) {
        return
      }

      const current = valuesOf($persona.get())

      if (refreshed.ok && fields.every(({ key }) => current[key] === draft.values[key].trim())) {
        if (JSON.stringify($companionEditors.get().persona) === JSON.stringify(draft)) {
          updateCompanionEditors({ persona: null })
        }
      } else if (isLive()) {
        setHint(
          backendDetailMessage(
            error,
            ipcErrorStatus(error) === 409
              ? english
                ? 'Details changed on another device. Review the latest values.'
                : '资料已在其他设备修改，请核对最新内容。'
              : t.hintSaveFailed
          )
        )
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  if (!draft || !editorOpen) {
    return (
      <section>
        <p className={cn(SECTION_TITLE, 'mb-2')}>{t.sectionTitle}</p>
        <div className="liquid-glass-card rounded-2xl p-4">
          <div className="flex min-w-0 items-start gap-3">
            <div className="min-w-0 flex-1">
              <p className="break-words text-[15px] font-medium text-strong">{persona?.name || t.defaultName}</p>
              <dl className="mt-2 space-y-2">
                {fields
                  .filter(({ key }) => key !== 'name' && latest[key])
                  .map(({ key, label }) => (
                    <div className="flex min-w-0 flex-wrap items-baseline gap-x-3" key={key}>
                      <dt className="w-24 shrink-0 text-xs text-muted">{label}</dt>
                      <dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-sm leading-6 text-body">
                        {latest[key]}
                      </dd>
                    </div>
                  ))}
              </dl>
            </div>
            <button
              className={cn(BTN_GHOST, 'shrink-0')}
              onClick={() => {
                if (!draft) {
                  updateCompanionEditors({ persona: { values: latest, base: latestBase } })
                }

                onEditRequested()
                setHint(null)
              }}
              type="button"
            >
              {t.editAction}
            </button>
          </div>
        </div>
      </section>
    )
  }

  return (
    <WizardModal
      closeDisabled={busy}
      escClose={!busy}
      footer={
        <CreationActionBar
          description={
            (busy || hint) && (
              <CreationFeedback
                state={busy ? 'working' : 'error'}
                title={busy ? undefined : hint || t.hintSaveFailed}
              />
            )
          }
        >
          <button
            aria-atomic="true"
            aria-live="polite"
            className={BTN_SUBTLE}
            disabled={busy || !dirty || !!conflict}
            onClick={() => void save()}
            type="button"
          >
            {busy ? dict.common.saving : english ? 'Save changes' : '保存修改'}
          </button>
          <button
            className={BTN_GHOST}
            disabled={busy}
            onClick={() => {
              updateCompanionEditors({ persona: null })
              setHint(null)
              onEditorClosed()
            }}
            type="button"
          >
            {dict.common.cancel}
          </button>
        </CreationActionBar>
      }
      onClose={onEditorClosed}
      regionId="persona-editor"
      title={t.editHeading}
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <p className={SECTION_TITLE}>{t.editHeading}</p>
          {dirty && <span className={HINT_TEXT}>{english ? 'Unsaved changes' : '未保存修改'}</span>}
        </div>
        {fields.map(({ key, label, placeholder, presets }) => (
          <label className="block" key={key}>
            <span className={FIELD_LABEL}>{label}</span>
            <input
              className={INPUT_CLASS}
              disabled={busy}
              maxLength={500}
              onChange={event =>
                updateCompanionEditors({
                  persona: { ...draft, values: { ...draft.values, [key]: event.target.value } }
                })
              }
              placeholder={placeholder}
              value={draft.values[key]}
            />
            {presets && (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {presets.map(value => (
                  <Chip
                    active={draft.values[key] === value}
                    key={value}
                    label={value}
                    onClick={() => {
                      if (!busy) {
                        updateCompanionEditors({ persona: { ...draft, values: { ...draft.values, [key]: value } } })
                      }
                    }}
                  />
                ))}
              </div>
            )}
          </label>
        ))}
        {conflict && (
          <div className="rounded-xl border border-line-hairline p-3 text-xs text-amber-300/90">
            <p>
              {english
                ? 'These details changed on another device. Review the latest values before saving.'
                : '资料已在其他设备修改，请核对最新内容后再保存。'}
            </p>
            <dl className="mt-2 space-y-1">
              {fields
                .filter(({ key }) => latest[key] !== (draft.base[key] ?? ''))
                .map(({ key, label }) => (
                  <div key={key}>
                    <dt>{label}</dt>
                    <dd className="whitespace-pre-wrap break-words text-body">{latest[key] || '—'}</dd>
                  </div>
                ))}
            </dl>
            <button
              className={cn(BTN_SUBTLE, 'mt-2')}
              disabled={busy}
              onClick={() => updateCompanionEditors({ persona: { ...draft, base: latestBase } })}
              type="button"
            >
              {english ? 'Keep my edits after review' : '已核对，保留我的修改'}
            </button>
          </div>
        )}
      </div>
    </WizardModal>
  )
}
