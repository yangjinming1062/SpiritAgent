import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { $persona, applyPersonaResponse, hydratePersona } from '@/features/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { failureText, ipcErrorStatus } from '@/shared/lib/ipc-error'
import { authedResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import { BTN_GHOST, BTN_SUBTLE, CreationActionBar, CreationFeedback, HINT_TEXT, SettingsSectionIntro } from '@/ui'
import type { PersonaEditableField, PersonaUpdateRequest } from '@protocol'

import { $companionEditors, updateCompanionEditors } from './companion-editor-state'
import { PersonaGroups } from './persona-editor'
import {
  groupOf,
  PERSONA_FIELD_KEYS,
  personaBase,
  type PersonaFieldErrors,
  personaFieldMeta,
  type PersonaGroupId,
  personaValues,
  REQUIRED_FIELDS
} from './persona-groups'
import { PortraitCard } from './portrait-card'

/** 校验失败或字段缺失时把焦点送到对应输入；分组尚未展开时等一帧再找。 */
function focusField(key: PersonaEditableField, attempt = 0): void {
  const element = document.getElementById(`persona-field-${key}`)

  if (element instanceof HTMLElement) {
    element.focus()
    element.scrollIntoView({ block: 'nearest' })

    return
  }

  if (attempt < 5) {
    window.requestAnimationFrame(() => focusField(key, attempt + 1))
  }
}

export function PersonaPage(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.persona
  const persona = useStore($persona)
  const draft = useStore($companionEditors).persona
  const [openGroups, setOpenGroups] = useState<readonly PersonaGroupId[]>([])
  const [editingName, setEditingName] = useState(false)
  const [errors, setErrors] = useState<PersonaFieldErrors>({})
  const [failure, setFailure] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const begin = useAsyncGuard()

  const latest = personaValues(persona)
  const latestBase = personaBase(persona)
  const meta = personaFieldMeta(t)

  const dirty = Boolean(draft && PERSONA_FIELD_KEYS.some(key => draft.values[key].trim() !== (draft.base[key] ?? '')))

  const conflict = Boolean(draft && PERSONA_FIELD_KEYS.some(key => latestBase[key] !== draft.base[key]))

  const changedElsewhere = draft ? PERSONA_FIELD_KEYS.filter(key => latest[key] !== (draft.base[key] ?? '')) : []

  const ensureDraft = (): void => {
    if (!draft) {
      updateCompanionEditors({ persona: { values: latest, base: latestBase } })
    }
  }

  const reset = (): void => {
    updateCompanionEditors({ persona: null })
    setOpenGroups([])
    setEditingName(false)
    setErrors({})
    setFailure(null)
  }

  const save = (): void => {
    if (!draft) {
      return
    }

    const nextErrors: PersonaFieldErrors = {}

    if (!draft.values.name.trim()) {
      nextErrors.name = t.hintEmptyName
    }

    if (!draft.values.personality.trim()) {
      nextErrors.personality = t.personalityRequired
    }

    if (!draft.values.speaking_style.trim()) {
      nextErrors.speaking_style = t.hintEmptySpeakingStyle
    }

    setErrors(nextErrors)

    const first = REQUIRED_FIELDS.find(key => nextErrors[key])

    if (first) {
      setFailure(null)

      if (first === 'name') {
        setEditingName(true)
      }

      const group = groupOf(first)

      if (group) {
        setOpenGroups(current => (current.includes(group) ? current : [...current, group]))
      }

      focusField(first)

      return
    }

    const isLive = begin()
    const isCurrent = captureAuthScope()

    if (!isCurrent) {
      return
    }

    setBusy(true)
    setFailure(null)

    void (async () => {
      try {
        const expectedFields = Object.fromEntries(PERSONA_FIELD_KEYS.map(key => [key, draft.base[key]]))

        const body: PersonaUpdateRequest = {
          definition_json: {
            name: draft.values.name.trim(),
            personality: draft.values.personality.trim(),
            relationship: draft.values.relationship.trim(),
            speaking_style: draft.values.speaking_style.trim(),
            age: draft.values.age.trim(),
            occupation: draft.values.occupation.trim(),
            background: draft.values.background.trim()
          },
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

        if (isLive()) {
          setOpenGroups([])
          setEditingName(false)
          setErrors({})
        }
      } catch (error) {
        if (!isCurrent()) {
          return
        }

        const refreshed = await hydratePersona({ silent: true })

        if (!isCurrent()) {
          return
        }

        const current = personaValues($persona.get())

        if (refreshed.ok && PERSONA_FIELD_KEYS.every(key => current[key] === draft.values[key].trim())) {
          if (JSON.stringify($companionEditors.get().persona) === JSON.stringify(draft)) {
            updateCompanionEditors({ persona: null })
          }
        } else if (isLive()) {
          setFailure(
            failureText(error, ipcErrorStatus(error) === 409 ? dict.common.changedElsewhere : t.hintSaveFailed)
          )
        }
      } finally {
        if (isLive()) {
          setBusy(false)
        }
      }
    })()
  }

  const showBar = dirty || conflict || failure !== null

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0">
        <SettingsSectionIntro hint={t.intro} title={t.title} />
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain pb-4 pr-1">
        <PortraitCard
          disabled={busy}
          editingName={editingName}
          nameError={errors.name}
          onDraftNeeded={ensureDraft}
          onEditingNameChange={setEditingName}
        />
        <PersonaGroups
          disabled={busy}
          errors={errors}
          onDraftNeeded={ensureDraft}
          onToggleGroup={id =>
            setOpenGroups(current => (current.includes(id) ? current.filter(item => item !== id) : [...current, id]))
          }
          openGroups={openGroups}
        />
      </div>
      {conflict && draft ? (
        <div className="mb-2 shrink-0 rounded-xl border border-line-hairline p-3 text-xs">
          <p className="text-[11px] text-warning">{dict.common.changedElsewhere}</p>
          <dl className="mt-2 space-y-1">
            {changedElsewhere.map(key => (
              <div key={key}>
                <dt className="text-[11px] text-muted">{meta[key].label}</dt>
                <dd className="whitespace-pre-wrap break-words text-[13px] text-body">{latest[key] || '—'}</dd>
              </div>
            ))}
          </dl>
          <button
            className={cn(BTN_SUBTLE, 'mt-2')}
            disabled={busy}
            onClick={() => updateCompanionEditors({ persona: { ...draft, base: latestBase } })}
            type="button"
          >
            {dict.common.keepMyEdits}
          </button>
        </div>
      ) : null}
      {showBar ? (
        <CreationActionBar
          description={
            failure ? (
              <CreationFeedback state="error" title={failure} />
            ) : dirty ? (
              <span className={HINT_TEXT}>{dict.common.unsavedChanges}</span>
            ) : undefined
          }
        >
          <button className={BTN_SUBTLE} disabled={busy || !dirty || conflict} onClick={save} type="button">
            {busy ? dict.common.saving : dict.common.saveChanges}
          </button>
          <button className={BTN_GHOST} disabled={busy} onClick={reset} type="button">
            {dict.common.cancel}
          </button>
        </CreationActionBar>
      ) : null}
    </div>
  )
}
