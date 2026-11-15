import { PERSONALITY_PRESETS, RELATIONSHIP_PRESETS, SPEAKING_STYLE_PRESETS } from '@/features/character'
import type { Dictionary } from '@/shared/strings'
import type { PersonaEditableField } from '@protocol'

/**
 * 资料分组：名字与形象由名片承载，分组卡只编辑这些字段。
 * 保存仍是整份人物定义 + expected_fields，一次提交覆盖所有分组。
 */
export const PERSONA_GROUPS = [
  { id: 'bond', fields: ['relationship', 'personality', 'speaking_style'] },
  { id: 'background', fields: ['age', 'occupation', 'background'] }
] as const

export type PersonaGroupId = (typeof PERSONA_GROUPS)[number]['id']
export type PersonaValues = Record<PersonaEditableField, string>
export type PersonaBase = Record<PersonaEditableField, string | null>
export type PersonaFieldErrors = Partial<Record<PersonaEditableField, string>>

export const PERSONA_FIELD_KEYS: readonly PersonaEditableField[] = [
  'name',
  'age',
  'occupation',
  'relationship',
  'personality',
  'speaking_style',
  'background'
]

/** 提交前必须非空的字段；其余字段允许留空。 */
export const REQUIRED_FIELDS: readonly PersonaEditableField[] = ['name', 'personality', 'speaking_style']

export const FIELD_PRESETS: Partial<Record<PersonaEditableField, readonly string[]>> = {
  relationship: RELATIONSHIP_PRESETS,
  personality: PERSONALITY_PRESETS,
  speaking_style: SPEAKING_STYLE_PRESETS
}

export type PersonaDict = Dictionary['settings']['persona']

export function personaFieldMeta(t: PersonaDict): Record<PersonaEditableField, { label: string; placeholder: string }> {
  return {
    name: { label: t.nameLabel, placeholder: t.namePlaceholder },
    age: { label: t.ageLabel, placeholder: t.agePlaceholder },
    occupation: { label: t.occupationLabel, placeholder: t.occupationPlaceholder },
    relationship: { label: t.relationshipLabel, placeholder: t.relationshipPlaceholder },
    personality: { label: t.personalityLabel, placeholder: t.personalityPlaceholder },
    speaking_style: { label: t.speakingStyleLabel, placeholder: t.speakingStylePlaceholder },
    background: { label: t.backgroundLabel, placeholder: t.backgroundPlaceholder }
  }
}

export function personaGroupTitle(t: PersonaDict, id: PersonaGroupId): string {
  return id === 'bond' ? t.groups.relationship : t.groups.background
}

interface PersonaLike {
  name?: string
  age?: string
  occupation?: string
  relationship?: string
  personality?: string
  speaking_style?: string
  background?: string
}

export function personaValues(persona: PersonaLike | null): PersonaValues {
  return {
    name: persona?.name ?? '',
    personality: persona?.personality ?? '',
    relationship: persona?.relationship ?? '',
    speaking_style: persona?.speaking_style ?? '',
    age: persona?.age ?? '',
    occupation: persona?.occupation ?? '',
    background: persona?.background ?? ''
  }
}

export function personaBase(persona: PersonaLike | null): PersonaBase {
  return {
    name: persona?.name ?? null,
    personality: persona?.personality ?? null,
    relationship: persona?.relationship ?? null,
    speaking_style: persona?.speaking_style ?? null,
    age: persona?.age ?? null,
    occupation: persona?.occupation ?? null,
    background: persona?.background ?? null
  }
}

export function fieldMaxLength(field: PersonaEditableField): number {
  return field === 'background' ? 2000 : 500
}

export function isMultilineField(field: PersonaEditableField): boolean {
  return field === 'background'
}

export function groupOf(field: PersonaEditableField): PersonaGroupId | null {
  return PERSONA_GROUPS.find(group => (group.fields as readonly PersonaEditableField[]).includes(field))?.id ?? null
}
