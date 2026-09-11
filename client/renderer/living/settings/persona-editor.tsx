import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { $persona, assemblePersona, hydratePersona, PERSONALITY_PRESETS, RELATIONSHIP_PRESETS } from '@/companion'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE, Chip, FIELD_LABEL, INPUT_CLASS, SECTION_TITLE } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

// 可编辑的 persona 字段：name / relationship / personality。
// 锁定的视觉锚点字段（species / gender / appearance）刻意不可编辑——见 docs/DESIGN.md §5.4。
export function PersonaSection(): React.JSX.Element {
  const dict = useStrings()
  const t = dict.settings.persona

  const persona = useStore($persona)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(persona?.name ?? '')
  const [relationship, setRelationship] = useState(persona?.relationship ?? '')
  const [personality, setPersonality] = useState(persona?.personality ?? '')
  const [saving, setSaving] = useState(false)
  const [hint, setHint] = useState<string | null>(null)

  const startEdit = (): void => {
    setName(persona?.name ?? '')
    setRelationship(persona?.relationship ?? '')
    setPersonality(persona?.personality ?? '')
    setHint(null)
    setEditing(true)
  }

  const save = async (): Promise<void> => {
    const trimmed = name.trim()

    if (!trimmed) {
      setHint(t.hintEmptyName)

      return
    }

    setSaving(true)
    setHint(null)

    // C2：PUT 与 hydrate 的失败模式分开——PUT 成功后即便 GET 短暂失败，
    // 也不能当成保存失败（诱导用户重试会造成重复写入）。
    // 把当前 persona 作为 previous 传入，让锁定的视觉锚点字段原样带回。
    let putOk = false

    try {
      await window.spiritagent.api({
        body: {
          definition_json: JSON.stringify(
            assemblePersona(
              {
                name: trimmed,
                personality: personality.trim(),
                relationship: relationship.trim()
              },
              persona ?? undefined
            )
          )
        },
        method: 'PUT',
        path: '/api/companion/persona'
      })
      putOk = true
    } catch {
      setHint(t.hintSaveFailed)
      setSaving(false)

      return
    }

    if (putOk) {
      const result = await hydratePersona({ silent: true })

      if (!result.ok) {
        // 后端已经有人设，本地副本没刷出来。给一条更温和的提示，
        // 让用户知道下次 hydrate 之前（下一次保存、重启等）看到的是旧值。
        setHint(t.hintHydrateFailed)
      }
    }

    setEditing(false)
    setSaving(false)
  }

  if (!editing) {
    const tags = [persona?.relationship, persona?.personality].filter(Boolean)

    return (
      <section>
        <p className={cn(SECTION_TITLE, 'mb-2')}>{t.sectionTitle}</p>
        <div className="liquid-glass-card space-y-3 rounded-2xl p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="size-2 shrink-0 rounded-full bg-accent shadow-[0_0_6px_var(--ui-accent)]" />
                <p className="truncate text-[15px] font-medium tracking-tight text-strong">
                  {persona?.name ?? t.defaultName}
                </p>
              </div>
              <p className="mt-1 text-[13px] leading-relaxed text-body">
                {tags.length ? tags.join(' · ') : t.noPersonality}
              </p>
            </div>
            <button className={BTN_GHOST} onClick={startEdit} type="button">
              {t.editAction}
            </button>
          </div>
          {persona?.appearance ? <p className="text-[11px] leading-relaxed text-muted">{persona.appearance}</p> : null}
        </div>
      </section>
    )
  }

  return (
    <section>
      <p className={cn(SECTION_TITLE, 'mb-1.5')}>{t.editHeading}</p>
      <div className="space-y-3">
        <label className="block">
          <span className={FIELD_LABEL}>{t.nameLabel}</span>
          <input
            className={INPUT_CLASS}
            onChange={e => setName(e.target.value)}
            placeholder={t.namePlaceholder}
            value={name}
          />
        </label>
        <label className="block">
          <span className={FIELD_LABEL}>{t.relationshipLabel}</span>
          <input
            className={INPUT_CLASS}
            onChange={e => setRelationship(e.target.value)}
            placeholder={t.relationshipPlaceholder}
            value={relationship}
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {RELATIONSHIP_PRESETS.map(p => (
              <Chip active={relationship === p} key={p} label={p} onClick={() => setRelationship(p)} />
            ))}
          </div>
        </label>
        <label className="block">
          <span className={FIELD_LABEL}>{t.personalityLabel}</span>
          <input
            className={INPUT_CLASS}
            onChange={e => setPersonality(e.target.value)}
            placeholder={t.personalityPlaceholder}
            value={personality}
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {PERSONALITY_PRESETS.map(p => (
              <Chip active={personality === p} key={p} label={p} onClick={() => setPersonality(p)} />
            ))}
          </div>
        </label>
        {hint && <p className="text-[11px] text-amber-300/90">{hint}</p>}
        <div className="flex gap-2">
          <button
            className={cn(BTN_SUBTLE, 'flex-1')}
            disabled={saving}
            onClick={() => setEditing(false)}
            type="button"
          >
            {dict.common.cancel}
          </button>
          <button className={cn(BTN_PRIMARY, 'flex-1')} disabled={saving} onClick={() => void save()} type="button">
            {saving ? dict.common.saving : dict.common.save}
          </button>
        </div>
      </div>
    </section>
  )
}
