import { useStore } from '@nanostores/react'
import type { ChangeEvent } from 'react'

import { $persona } from '@/features/character'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import { Badge, BTN_GHOST, Chip, FIELD_LABEL, INPUT_CLASS, SECTION_TITLE } from '@/ui'
import type { PersonaEditableField } from '@protocol'

import { $companionEditors, updateCompanionEditors } from './companion-editor-state'
import {
  FIELD_PRESETS,
  fieldMaxLength,
  isMultilineField,
  PERSONA_GROUPS,
  personaBase,
  type PersonaFieldErrors,
  personaFieldMeta,
  type PersonaGroupId,
  personaGroupTitle,
  personaValues
} from './persona-groups'

/**
 * 两组人物设定：组内就地编辑，组间共享同一份草稿；
 * 保存与冲突核对由页面统一负责，这里只渲染与写入字段值。
 */
export function PersonaGroups({
  disabled,
  errors,
  onDraftNeeded,
  onToggleGroup,
  openGroups
}: {
  disabled: boolean
  errors: PersonaFieldErrors
  onDraftNeeded: () => void
  onToggleGroup: (id: PersonaGroupId) => void
  openGroups: readonly PersonaGroupId[]
}): React.JSX.Element {
  const dict = useStrings()
  const t = dict.settings.persona
  const persona = useStore($persona)
  const draft = useStore($companionEditors).persona
  const values = draft?.values ?? personaValues(persona)
  const meta = personaFieldMeta(t)

  const change = (key: PersonaEditableField, value: string): void => {
    const next = draft ?? { values: personaValues(persona), base: personaBase(persona) }
    updateCompanionEditors({ persona: { ...next, values: { ...next.values, [key]: value } } })
  }

  return (
    <div className="space-y-4">
      {PERSONA_GROUPS.map(group => {
        const open = openGroups.includes(group.id)
        const unfilled = group.fields.filter(key => !values[key].trim()).length

        return (
          <section className="liquid-glass-card rounded-2xl p-4" key={group.id}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2">
                <p className={SECTION_TITLE}>{personaGroupTitle(t, group.id)}</p>
                {unfilled > 0 ? <Badge>{t.unfilledCount(unfilled)}</Badge> : null}
              </div>
              <button
                aria-expanded={open}
                className={BTN_GHOST}
                disabled={disabled}
                onClick={() => {
                  if (!open) {
                    onDraftNeeded()
                  }

                  onToggleGroup(group.id)
                }}
                type="button"
              >
                {open ? t.collapseAction : t.editAction}
              </button>
            </div>
            {open ? (
              <div className="mt-3 space-y-3">
                {group.fields.map(key => {
                  const { label, placeholder } = meta[key]
                  const presets = FIELD_PRESETS[key]
                  const error = errors[key]

                  return (
                    <div key={key}>
                      <label className="block" htmlFor={`persona-field-${key}`}>
                        <span className={FIELD_LABEL}>{label}</span>
                        {isMultilineField(key) ? (
                          <textarea
                            aria-describedby={error ? `persona-field-${key}-error` : undefined}
                            aria-invalid={Boolean(error)}
                            className={INPUT_CLASS}
                            disabled={disabled}
                            id={`persona-field-${key}`}
                            maxLength={fieldMaxLength(key)}
                            onChange={(event: ChangeEvent<HTMLTextAreaElement>) => change(key, event.target.value)}
                            placeholder={placeholder}
                            rows={5}
                            value={values[key]}
                          />
                        ) : (
                          <input
                            aria-describedby={error ? `persona-field-${key}-error` : undefined}
                            aria-invalid={Boolean(error)}
                            className={INPUT_CLASS}
                            disabled={disabled}
                            id={`persona-field-${key}`}
                            maxLength={fieldMaxLength(key)}
                            onChange={(event: ChangeEvent<HTMLInputElement>) => change(key, event.target.value)}
                            placeholder={placeholder}
                            value={values[key]}
                          />
                        )}
                      </label>
                      {presets ? (
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {presets.map(value => (
                            <Chip
                              active={values[key] === value}
                              key={value}
                              label={value}
                              onClick={() => {
                                if (!disabled) {
                                  change(key, value)
                                }
                              }}
                            />
                          ))}
                        </div>
                      ) : null}
                      {error ? (
                        <p className="mt-1 text-[11px] text-danger-fg" id={`persona-field-${key}-error`}>
                          {error}
                        </p>
                      ) : null}
                    </div>
                  )
                })}
              </div>
            ) : (
              <dl className="mt-3 space-y-2">
                {group.fields.map(key => (
                  <div className="flex min-w-0 flex-wrap items-baseline gap-x-3" key={key}>
                    <dt className="w-24 shrink-0 text-[11px] text-muted">{meta[key].label}</dt>
                    <dd
                      className={cn(
                        'min-w-0 flex-1 whitespace-pre-wrap break-words text-[13px] leading-6',
                        values[key].trim() ? 'text-body' : 'text-muted'
                      )}
                    >
                      {values[key].trim() || t.unfilled}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        )
      })}
    </div>
  )
}
