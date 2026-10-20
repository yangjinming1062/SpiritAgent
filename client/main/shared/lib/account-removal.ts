import type { dialog } from 'electron'

import { errorMessage } from '@runtime'

import * as runnerConfigStore from './runner-config-store'

const STRINGS = {
  zh: {
    removeFailed: '移除账户失败'
  },
  en: {
    removeFailed: 'Could not remove account'
  }
} as const

function getStrings(): (typeof STRINGS)['en' | 'zh'] {
  return STRINGS[runnerConfigStore.read().language === 'en' ? 'en' : 'zh']
}

export function showAccountRemovalError(dialogApi: Pick<typeof dialog, 'showErrorBox'>, error: unknown): void {
  dialogApi.showErrorBox(getStrings().removeFailed, errorMessage(error))
}
