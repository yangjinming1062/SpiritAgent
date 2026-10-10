import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import {
  $persona,
  assemblePersona,
  hydratePersona,
  PERSONALITY_PRESETS,
  RELATIONSHIP_PRESETS,
  SPEAKING_STYLE_PRESETS
} from '@/modules/character'
import { useAutoSave } from '@/shared/hooks/use-auto-save'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_SUBTLE, Chip, FIELD_LABEL, HINT_TEXT, INPUT_CLASS, SECTION_TITLE } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

type PersonaDraft = Record<'name' | 'personality' | 'relationship' | 'speakingStyle', string>

interface PersonaField {
  key: keyof PersonaDraft
  label: string
  placeholder: string
  presets?: readonly string[]
  required?: boolean
}

function draftOf(persona: ReturnType<typeof $persona.get>): PersonaDraft {
  return {
    name: persona?.name ?? '',
    personality: persona?.personality ?? '',
    relationship: persona?.relationship ?? '',
    speakingStyle: persona?.speaking_style ?? ''
  }
}

// 可编辑的 persona 字段：name/relationship/personality/speaking_style；锁定的视觉锚点字段（biological_type/gender）刻意不可编辑——见 docs/DESIGN.md「身份锁定与角色卡」。
export function PersonaSection(): React.JSX.Element {
  const dict = useStrings()
  const t = dict.settings.persona

  const persona = useStore($persona)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(() => draftOf(persona))
  const [hint, setHint] = useState<string | null>(null)
  // 查看态降级提示与编辑态 hint 分槽：hydrate 失败提示不能串进编辑校验文案，反之亦然。
  const [staleHint, setStaleHint] = useState<string | null>(null)
  const mountedRef = useRef(true)

  useEffect(
    () => () => {
      mountedRef.current = false
    },
    []
  )

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
      key: 'speakingStyle',
      label: t.speakingStyleLabel,
      placeholder: t.speakingStylePlaceholder,
      presets: SPEAKING_STYLE_PRESETS,
      required: true
    }
  ]

  const setField = (key: keyof PersonaDraft, value: string): void =>
    setDraft(previous => ({ ...previous, [key]: value }))

  const startEdit = (): void => {
    setDraft(draftOf(persona))
    setHint(null)
    setEditing(true)
  }

  const hasValidDraft = draft.name.trim().length > 0 && draft.speakingStyle.trim().length > 0

  const hasChanges =
    draft.name.trim() !== (persona?.name ?? '') ||
    draft.personality.trim() !== (persona?.personality ?? '') ||
    draft.relationship.trim() !== (persona?.relationship ?? '') ||
    draft.speakingStyle.trim() !== (persona?.speaking_style ?? '')

  useEffect(() => {
    if (!editing) {
      return
    }

    if (!draft.name.trim()) {
      setHint(t.hintEmptyName)
    } else if (!draft.speakingStyle.trim()) {
      setHint(t.hintEmptySpeakingStyle)
    } else {
      setHint(null)
    }
  }, [draft.name, draft.speakingStyle, editing, t.hintEmptyName, t.hintEmptySpeakingStyle])

  const persist = async (nextDraft: PersonaDraft): Promise<void> => {
    const trimmed = nextDraft.name.trim()
    const trimmedSpeakingStyle = nextDraft.speakingStyle.trim()

    if (!trimmed) {
      throw new Error(t.hintEmptyName)
    }

    if (!trimmedSpeakingStyle) {
      throw new Error(t.hintEmptySpeakingStyle)
    }

    // PUT 与 hydrate 的失败模式分开—— PUT 成功后即便 GET 短暂失败也不能当成保存失败（诱导用户重试会造成重复写入）；把当前 persona 作为 previous 传入，让锁定字段原样带回。
    await window.spiritagent.api({
      body: {
        definition_json: JSON.stringify(
          assemblePersona(
            {
              name: nextDraft.name.trim(),
              personality: nextDraft.personality.trim(),
              relationship: nextDraft.relationship.trim(),
              speaking_style: nextDraft.speakingStyle.trim()
            },
            persona ?? undefined
          )
        )
      },
      method: 'PUT',
      path: '/api/companion/persona'
    })

    const result = await hydratePersona({ silent: true })

    // 本地副本未刷出时给查看态降级提示：当前展示的是旧值，下次保存或重启后更新。
    if (mountedRef.current) {
      setStaleHint(result.ok ? null : t.hintHydrateFailed)
    }
  }

  const { status: saveStatus } = useAutoSave({
    dirty: editing && hasValidDraft && hasChanges,
    onError: error => {
      if (mountedRef.current) {
        setHint(error instanceof Error && error.message ? error.message : t.hintSaveFailed)
      }
    },
    onSave: persist,
    value: draft
  })

  if (!editing) {
    const saved = draftOf(persona)
    const details = fields.filter(({ key }) => key !== 'name' && saved[key])

    return (
      <section>
        <p className={cn(SECTION_TITLE, 'mb-2')}>{t.sectionTitle}</p>
        <div className="liquid-glass-card rounded-2xl p-4">
          <div className="flex min-w-0 items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="size-2 shrink-0 rounded-full bg-accent shadow-[0_0_6px_var(--ui-accent)]" />
                <p
                  className="truncate text-[15px] font-medium tracking-tight text-strong"
                  title={persona?.name || t.defaultName}
                >
                  {persona?.name || t.defaultName}
                </p>
              </div>
              {details.length > 0 ? (
                <dl className="mt-2 space-y-1.5">
                  {details.map(({ key, label }) => (
                    <div className="flex min-w-0 items-baseline gap-2" key={key}>
                      <dt className="w-24 shrink-0 text-[12px] text-muted">
                        {label}
                        {t.detailSeparator}
                      </dt>
                      <dd className="min-w-0 flex-1 truncate text-[13px] leading-5 text-body" title={saved[key]}>
                        {saved[key]}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="mt-1 text-[13px] leading-relaxed text-body">{t.noPersonality}</p>
              )}
              {staleHint && <p className="mt-2 text-xs text-amber-300/90">{staleHint}</p>}
            </div>
            <button className={cn(BTN_GHOST, 'shrink-0 whitespace-nowrap')} onClick={startEdit} type="button">
              {t.editAction}
            </button>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section>
      <p className={cn(SECTION_TITLE, 'mb-1.5')}>{t.editHeading}</p>
      <div className="space-y-3">
        {fields.map(({ key, label, placeholder, presets, required }) => (
          <label className="block" key={key}>
            <span className={FIELD_LABEL}>{label}</span>
            <input
              className={INPUT_CLASS}
              onChange={e => setField(key, e.target.value)}
              placeholder={placeholder}
              required={required}
              value={draft[key]}
            />
            {presets && (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {presets.map(p => (
                  <Chip active={draft[key] === p} key={p} label={p} onClick={() => setField(key, p)} />
                ))}
              </div>
            )}
          </label>
        ))}
        {hint && <p className="text-[11px] text-amber-300/90">{hint}</p>}
        <div className="flex gap-2">
          <button
            className={cn(BTN_SUBTLE, 'flex-1')}
            onClick={() => {
              setHint(null)
              setEditing(false)
            }}
            type="button"
          >
            {dict.common.cancel}
          </button>
          {saveStatus === 'saving' && <span className={cn(HINT_TEXT, 'self-center')}>{dict.common.saving}</span>}
          {saveStatus === 'saved' && <span className={cn(HINT_TEXT, 'self-center')}>{t.hintSaved}</span>}
          {saveStatus === 'error' && <span className="self-center text-[10px] text-danger-fg">{t.hintSaveFailed}</span>}
        </div>
      </div>
    </section>
  )
}
