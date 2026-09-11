import { definePersistedEnum } from '@/shared/lib/storage'

export type RenderMode = '2d' | '3d'

const renderModePersisted = definePersistedEnum<RenderMode>({
  allowed: ['2d', '3d'] as const,
  fallback: '2d',
  key: 'da.companion.renderMode'
})

export const $renderMode = renderModePersisted.$atom
export const setRenderMode = renderModePersisted.set
