import { useStore } from '@nanostores/react'
import { useState } from 'react'

import {
  $persona,
  assemblePersona,
  hydratePersona,
  PERSONALITY_PRESETS,
  RELATIONSHIP_PRESETS,
  SPEAKING_STYLE_PRESETS
} from '@/modules/character'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE, Chip, FIELD_LABEL, INPUT_CLASS, SECTION_TITLE } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

// 可编辑的 persona 字段：name / relationship / personality / speaking_style。
// 锁定的视觉锚点字段（biological_type / gender）刻意不可编辑——见 docs/DESIGN.md §5.4。
export function PersonaSection(): React.JSX.Element {
  const dict = useStrings()
  const t = dict.settings.persona

  const persona = useStore($persona)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(persona?.name ?? '')
  const [relationship, setRelationship] = useState(persona?.relationship ?? '')
  const [personality, setPersonality] = useState(persona?.personality ?? '')
  const [speakingStyle, setSpeakingStyle] = useState(persona?.speakingStyle ?? '')
  const [saving, setSaving] = useState(false)
  const [hint, setHint] = useState<string | null>(null)

  const startEdit = (): void => {
    setName(persona?.name ?? '')
    setRelationship(persona?.relationship ?? '')
    setPersonality(persona?.personality ?? '')
    setSpeakingStyle(persona?.speakingStyle ?? '')
    setHint(null)
    setEditing(true)
  }

  const save = async (): Promise<void> => {
    const trimmed = name.trim()
    const trimmedSpeakingStyle = speakingStyle.trim()

    if (!trimmed) {
      setHint(t.hintEmptyName)

      return
    }

    if (!trimmedSpeakingStyle) {
      setHint(t.hintEmptySpeakingStyle)

      return
    }

    setSaving(true)
    setHint(null)

    // PUT 与 hydrate 的失败模式分开——PUT 成功后即便 GET 短暂失败，
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
                relationship: relationship.trim(),
                speaking_style: trimmedSpeakingStyle
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
    const details = [
      { label: t.relationshipLabel, value: persona?.relationship },
      { label: t.personalityLabel, value: persona?.personality },
      { label: t.speakingStyleLabel, value: persona?.speakingStyle }
    ].filter(({ value }) => Boolean(value))

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
                  title={persona?.name ?? t.defaultName}
                >
                  {persona?.name ?? t.defaultName}
                </p>
              </div>
              {details.length > 0 ? (
                <dl className="mt-2 space-y-1.5">
                  {details.map(({ label, value }) => (
                    <div className="flex min-w-0 items-baseline gap-2" key={label}>
                      <dt className="w-24 shrink-0 text-[12px] text-muted">
                        {label}
                        {t.detailSeparator}
                      </dt>
                      <dd className="min-w-0 flex-1 truncate text-[13px] leading-5 text-body" title={value}>
                        {value}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="mt-1 text-[13px] leading-relaxed text-body">{t.noPersonality}</p>
              )}
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
        <label className="block">
          <span className={FIELD_LABEL}>{t.speakingStyleLabel}</span>
          <input
            className={INPUT_CLASS}
            onChange={e => setSpeakingStyle(e.target.value)}
            placeholder={t.speakingStylePlaceholder}
            required
            value={speakingStyle}
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {SPEAKING_STYLE_PRESETS.map(p => (
              <Chip active={speakingStyle === p} key={p} label={p} onClick={() => setSpeakingStyle(p)} />
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
