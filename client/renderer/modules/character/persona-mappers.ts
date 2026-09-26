import type { PersonaPayload } from './persona'
import type { PersonaDefinition } from './persona-store'

// PersonaDefinition（camelCase）是渲染端的 $persona 形状；
// PersonaPayload（snake_case）是后端响应字段。本文件只负责入站映射。

export function personaFromWire(payload: PersonaPayload): PersonaDefinition {
  return {
    name: payload.name,
    personality: payload.personality,
    speakingStyle: payload.speaking_style ?? '',
    ...(payload.relationship !== undefined && { relationship: payload.relationship }),
    ...(payload.biological_type !== undefined && { biological_type: payload.biological_type }),
    ...(payload.gender !== undefined && { gender: payload.gender })
  }
}
