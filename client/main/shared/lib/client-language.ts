import { IPC } from '@ipc/contracts'

import { broadcastToAllWindows } from '../utils'

import { buildPrefsHydratedFromConfig } from './config-sync'
import * as store from './runner-config-store'

export async function setClientLanguage(language: unknown): Promise<{ error?: string; ok: boolean }> {
  if (language !== 'zh' && language !== 'en') {
    return { error: 'Unsupported language', ok: false }
  }

  const result = await store.mutate(
    config => {
      config.language = language
    },
    { pushRunner: false }
  )

  if (result.ok) {
    broadcastToAllWindows(IPC.event.prefsHydrated, buildPrefsHydratedFromConfig(store.read()))
  }

  return result
}
