import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import {
  $persona,
  assemblePersona,
  hydratePersona,
  PERSONALITY_PRESETS,
  type PersonalityPreset,
  RELATIONSHIP_PRESETS,
  type RelationshipPreset,
  SPEAKING_STYLE_PRESETS,
  type SpeakingStylePreset
} from '@/companion'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_PRIMARY, Chip, INPUT_CLASS, WizardModal } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

interface PersonaRetuneProps {
  initial: {
    name: string
    personality: string
    speaking_style: string
    relationship: string
    user_call_name: string
    user_gender: string
    user_age_bucket: string
    user_hobbies: string
    user_freeform: string
  }
  onClose: () => void
}

// 字段 schema：每一步持有一组字段。``presets`` 的类型是全部已知 preset token 的联合再加 ''
// （speakingStyle 用的「自动派生」标记）。这样 STEPS 里写成「喜爱」这种拼写错误会编译失败，
// 而不是默默渲染出一个空 chip。
// species / character_gender / appearance 在这里不可编辑。
type PresetValue = PersonalityPreset | RelationshipPreset | SpeakingStylePreset | ''

type PersonaFieldKey =
  | 'relationship'
  | 'name'
  | 'personality'
  | 'speakingStyle'
  | 'userAgeBucket'
  | 'userCallName'
  | 'userFreeform'
  | 'userGender'
  | 'userHobbies'

type FieldSchema = {
  key: PersonaFieldKey
  label: string
  presets?: readonly PresetValue[]
  max?: number
  placeholder?: string
  multiline?: boolean
}

type StepSchema = { title: string; fields: FieldSchema[] }

type ReviewRow = { fallback?: string; key: PersonaFieldKey; label: string }

// 以对话方式分步调整性格（含 user_*），单次 PUT 收尾、保留既有长期记忆。
export function PersonaRetune({ initial, onClose }: PersonaRetuneProps): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.persona

  const persona = useStore($persona)
  const [step, setStep] = useState<number>(0)
  const [saving, setSaving] = useState(false)
  const [hint, setHint] = useState<null | string>(null)

  const steps = useRef<StepSchema[]>([
    {
      title: t.steps.name,
      fields: [{ key: 'name', label: t.fields.name, max: 64, placeholder: t.fields.namePlaceholder }]
    },
    {
      title: t.steps.relationship,
      fields: [{ key: 'relationship', label: t.fields.relationship, presets: RELATIONSHIP_PRESETS }]
    },
    {
      title: t.steps.personalityStyle,
      fields: [
        { key: 'personality', label: t.fields.personality, presets: PERSONALITY_PRESETS },
        {
          key: 'speakingStyle',
          label: t.fields.speakingStyle,
          placeholder: t.fields.speakingStylePlaceholder,
          presets: [...SPEAKING_STYLE_PRESETS, '']
        }
      ]
    },
    {
      title: t.steps.aboutBasics,
      fields: [
        { key: 'userCallName', label: t.fields.userCallName },
        { key: 'userGender', label: t.fields.userGender },
        { key: 'userAgeBucket', label: t.fields.userAgeBucket }
      ]
    },
    {
      title: t.steps.aboutHobbies,
      fields: [
        { key: 'userHobbies', label: t.fields.userHobbies },
        { key: 'userFreeform', label: t.fields.userFreeform, multiline: true }
      ]
    }
  ]).current

  const reviewRows = useRef<ReviewRow[]>([
    { key: 'name', label: t.reviewRows.name },
    { key: 'relationship', label: t.reviewRows.relationship },
    { key: 'personality', label: t.reviewRows.personality },
    { fallback: t.reviewRows.speakingStyleFallback, key: 'speakingStyle', label: t.reviewRows.speakingStyle },
    { key: 'userCallName', label: t.reviewRows.userCallName },
    { key: 'userGender', label: t.reviewRows.userGender },
    { key: 'userAgeBucket', label: t.reviewRows.userAgeBucket },
    { key: 'userHobbies', label: t.reviewRows.userHobbies },
    { key: 'userFreeform', label: t.reviewRows.userFreeform }
  ]).current

  // 跟踪向导是否还挂载。保存中途关闭模态框会卸载组件；进行中的 ``save()`` 仍会跑完。
  const mountedRef = useRef(true)
  useEffect(
    () => () => {
      mountedRef.current = false
    },
    []
  )

  const [name, setName] = useState(initial.name)
  const [relationship, setRelationship] = useState(initial.relationship)
  const [personality, setPersonality] = useState(initial.personality)
  const [speakingStyle, setSpeakingStyle] = useState(initial.speaking_style)
  const [userCallName, setUserCallName] = useState(initial.user_call_name)
  const [userGender, setUserGender] = useState(initial.user_gender)
  const [userAgeBucket, setUserAgeBucket] = useState(initial.user_age_bucket)
  const [userHobbies, setUserHobbies] = useState(initial.user_hobbies)
  const [userFreeform, setUserFreeform] = useState(initial.user_freeform)

  // 以字段 ``key`` 为键的 setter 映射。避免每一步写 switch/case。
  const setters: Record<PersonaFieldKey, (v: string) => void> = {
    relationship: setRelationship,
    name: setName,
    personality: setPersonality,
    speakingStyle: setSpeakingStyle,
    userAgeBucket: setUserAgeBucket,
    userCallName: setUserCallName,
    userFreeform: setUserFreeform,
    userGender: setUserGender,
    userHobbies: setUserHobbies
  }

  const values: Record<PersonaFieldKey, string> = {
    relationship,
    name,
    personality,
    speakingStyle,
    userAgeBucket,
    userCallName,
    userFreeform,
    userGender,
    userHobbies
  }

  const totalSteps = steps.length + 1
  const isReview = step === steps.length

  const next = (): void => setStep(s => Math.min(s + 1, totalSteps - 1))
  const prev = (): void => setStep(s => Math.max(s - 1, 0))

  const save = async (): Promise<void> => {
    const trimmed = name.trim()

    if (!trimmed) {
      setHint(t.hintEmptyName)
      setStep(0)

      return
    }

    setSaving(true)
    setHint(null)

    // C2：把 PUT（写）与 hydrate（读）的失败模式分开——与 persona-editor 同理。
    // PUT 成功之后即便 GET 短暂失败，也不能被当成保存失败。
    //
    // 把当前 persona 作为 `previous` 传入，让锁定的视觉锚点字段原样带回——见 docs/DESIGN.md §5.4。
    let putOk = false

    try {
      await window.spiritagent.api({
        body: {
          definition_json: JSON.stringify(
            assemblePersona(
              {
                name: trimmed,
                personality,
                speaking_style: speakingStyle,
                relationship,
                user_call_name: userCallName,
                user_gender: userGender,
                user_age_bucket: userAgeBucket,
                user_hobbies: userHobbies,
                user_freeform: userFreeform
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
      if (mountedRef.current) {
        setHint(t.retuneSaveFailed)
        setSaving(false)
      }

      return
    }

    if (!putOk) {
      return
    }

    const result = await hydratePersona({ silent: true })

    if (!mountedRef.current) {
      return
    }

    if (!result.ok) {
      // 后端已经有人设，本地副本没刷出来。给一条更温和的提示，
      // 让用户知道下次 hydrate 之前（下一次保存、重启等）看到的是旧值。
      setHint(t.hintHydrateFailed)
      setSaving(false)

      return
    }

    setSaving(false)
    onClose()
  }

  return (
    <WizardModal
      footer={
        <>
          <button className={BTN_GHOST} disabled={step === 0 || saving} onClick={prev} type="button">
            {t.retunePrev}
          </button>
          <span className="ml-auto text-[10px] text-muted">
            {step + 1} / {totalSteps}
          </span>
          {!isReview ? (
            <button className={BTN_PRIMARY} disabled={saving} onClick={next} type="button">
              {t.retuneNext}
            </button>
          ) : (
            <button className={BTN_PRIMARY} disabled={saving} onClick={() => void save()} type="button">
              {saving ? dict.common.saving : dict.common.save}
            </button>
          )}
        </>
      }
      onClose={onClose}
      regionId="persona-retune"
      title={t.retuneModalTitle}
    >
      {hint && <p className="mb-2 text-xs text-amber-300/90">{hint}</p>}

      {!isReview ? (
        <div className="space-y-2.5">
          <p className="text-[11px] text-body">{t.retuneStepPrefix(step + 1, steps[step].title)}</p>
          {steps[step].fields.map(field => (
            <Field
              autoDerivedLabel={t.autoDerivedChip}
              field={field}
              key={field.key}
              onChange={setters[field.key]}
              value={values[field.key]}
            />
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-[11px] text-body">{t.retuneStepPrefix(step + 1, t.retuneReviewTitle)}</p>
          <dl className="space-y-1 rounded-xl border border-line-hairline bg-surface-card p-3 text-[11px]">
            {reviewRows.map(row => (
              <Row key={row.key} label={row.label} value={values[row.key] || row.fallback || t.retuneEmpty} />
            ))}
          </dl>
        </div>
      )}
    </WizardModal>
  )
}

interface FieldProps {
  autoDerivedLabel: string
  field: FieldSchema
  value: string
  onChange: (v: string) => void
}

function Field({ autoDerivedLabel, field, value, onChange }: FieldProps): React.ReactElement {
  // presets 列表的最后一项如果是空字符串，表示「自动派生 / 清空」选项——
  // 只有 speaking_style 上才有意义。
  const isClearPreset = field.presets && field.presets[field.presets.length - 1] === ''
  const max = field.max ?? Infinity
  const handleChange = (v: string) => onChange(max === Infinity ? v : v.slice(0, max))

  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-body">{field.label}</span>
      {field.multiline ? (
        <textarea
          className={cn(INPUT_CLASS, 'resize-none')}
          onChange={e => handleChange(e.target.value)}
          placeholder={field.placeholder}
          rows={2}
          value={value}
        />
      ) : (
        <input
          className={INPUT_CLASS}
          onChange={e => handleChange(e.target.value)}
          placeholder={field.placeholder}
          value={value}
        />
      )}
      {field.presets && field.presets.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {field.presets.map(p => {
            const isClear = p === '' && isClearPreset
            const active = isClear ? value === '' : value === p

            return (
              <Chip
                active={active}
                key={p || 'clear'}
                label={isClear ? autoDerivedLabel : p}
                onClick={() => handleChange(p)}
              />
            )
          })}
        </div>
      )}
    </label>
  )
}

function Row({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div className="flex gap-2">
      <dt className="w-20 shrink-0 text-muted">{label}</dt>
      <dd className="flex-1 text-strong">{value}</dd>
    </div>
  )
}
