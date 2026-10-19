import type React from 'react'

import { REASONING_EFFORT_VALUES } from '@/shared/lib/reasoning-effort'
import { cn } from '@/shared/lib/utils'
import { CapsuleTabs, HINT_TEXT, SECTION_TITLE, SettingCard, SettingRow, Slider, Toggle } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

export interface InferenceFormState {
  reasoning_effort: string
  enable_background_review: boolean
  enable_context_compression: boolean
  context_compression_threshold: number
  chat_temperature: number
  compression_temperature: number
  title_generation_temperature: number
}

const TEMPERATURE_FIELDS = [
  { field: 'chat_temperature', labelKey: 'chatTemperature', descKey: 'chatTemperatureDesc' },
  { field: 'title_generation_temperature', labelKey: 'titleTemperature', descKey: 'titleTemperatureDesc' },
  { field: 'compression_temperature', labelKey: 'compressionTemperature', descKey: 'compressionTemperatureDesc' }
] as const

function FormSection({
  children,
  heading,
  intro
}: {
  children: React.ReactNode
  heading: string
  intro: string
}): React.JSX.Element {
  return (
    <section>
      <p className={cn(SECTION_TITLE, 'mb-1')}>{heading}</p>
      {intro ? <p className={cn(HINT_TEXT, 'mb-2')}>{intro}</p> : null}
      <SettingCard>{children}</SettingCard>
    </section>
  )
}

function NumberSetting({
  label,
  description,
  value,
  min = 0,
  step = 0.01,
  percent = false,
  onChange
}: {
  label: string
  description: string
  value: number
  min?: number
  step?: number
  percent?: boolean
  onChange: (value: number) => void
}): React.JSX.Element {
  return (
    <SettingRow description={description} label={label}>
      <div className="flex w-48 items-center gap-3">
        <Slider
          ariaLabel={label}
          max={1}
          min={min}
          onChange={next => onChange(Math.min(1, Math.max(min, Math.round(next * 100) / 100)))}
          step={step}
          value={value}
        />
        <span className="w-10 shrink-0 text-right font-mono text-xs text-body">
          {percent ? `${Math.round(value * 100)}%` : value.toFixed(2)}
        </span>
      </div>
    </SettingRow>
  )
}

export function InferenceForm({
  state,
  update
}: {
  state: InferenceFormState
  update: (patch: Partial<InferenceFormState>) => void
}): React.JSX.Element {
  const { agentDefaults: agent, contextCompression: compression, temperature } = useStrings().settings.inference

  return (
    <>
      <FormSection heading={agent.heading} intro={agent.intro}>
        <SettingRow description={agent.reasoningEffortDesc} label={agent.reasoningEffort}>
          <CapsuleTabs
            ariaLabel={agent.reasoningEffort}
            onChange={value => update({ reasoning_effort: value })}
            options={REASONING_EFFORT_VALUES.map(value => ({ value, label: agent.reasoningOptions[value] }))}
            size="sm"
            value={state.reasoning_effort}
          />
        </SettingRow>
        <SettingRow description={agent.backgroundReviewDesc} label={agent.backgroundReview}>
          <Toggle
            ariaLabel={agent.backgroundReview}
            checked={state.enable_background_review}
            onChange={value => update({ enable_background_review: value })}
          />
        </SettingRow>
      </FormSection>
      <FormSection heading={compression.heading} intro={compression.intro}>
        <SettingRow description={compression.enableCompressionDesc} label={compression.enableCompression}>
          <Toggle
            ariaLabel={compression.enableCompression}
            checked={state.enable_context_compression}
            onChange={value => update({ enable_context_compression: value })}
          />
        </SettingRow>
        <NumberSetting
          description={compression.thresholdDesc}
          label={compression.threshold}
          min={0.3}
          onChange={value => update({ context_compression_threshold: value })}
          percent
          step={0.05}
          value={state.context_compression_threshold}
        />
      </FormSection>
      <FormSection heading={temperature.heading} intro={temperature.intro}>
        {TEMPERATURE_FIELDS.map(({ field, labelKey, descKey }) => (
          <NumberSetting
            description={temperature[descKey]}
            key={field}
            label={temperature[labelKey]}
            onChange={value => update({ [field]: value })}
            value={state[field]}
          />
        ))}
      </FormSection>
    </>
  )
}
