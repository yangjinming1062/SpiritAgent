import type { PersonaPayload } from './persona'
import type { PersonaDefinition } from './persona-store'

// PersonaDefinition（camelCase）是渲染端的 $persona 形状；
// PersonaPayload（snake_case）是后端响应字段。保存路径的 PUT 载荷由
// persona-retune 按表单局部状态内联构造，不走本 mapper。

export function personaFromWire(payload: PersonaPayload): PersonaDefinition {
  return {
    name: payload.name,
    personality: payload.personality,
    speakingStyle: payload.speaking_style ?? '',
    ...(payload.relationship !== undefined && { relationship: payload.relationship }),
    ...(payload.biological_type !== undefined && { biological_type: payload.biological_type }),
    ...(payload.gender !== undefined && { gender: payload.gender }),
    ...(payload.appearance !== undefined && { appearance: payload.appearance })
  }
}
