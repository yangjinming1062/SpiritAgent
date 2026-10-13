import type { BrowserWindow, dialog, MessageBoxOptions } from 'electron'

import type { DesktopAccount } from '@ipc/contracts'
import { errorMessage } from '@runtime'

import * as runnerConfigStore from './runner-config-store'

const STRINGS = {
  zh: {
    removeConfirm: (username: string) => `从本机移除「${username}」？之后若想使用此账户，需要重新输入激活码。`,
    removeCurrentConfirm: (username: string) =>
      `从本机移除当前账户「${username}」并退出登录？之后若想使用此账户，需要重新输入激活码。`,
    confirmRemove: '移除',
    cancel: '取消',
    removeFailed: '移除账户失败'
  },
  en: {
    removeConfirm: (username: string) =>
      `Remove “${username}” from this device? You will need its activation code to add it again.`,
    removeCurrentConfirm: (username: string) =>
      `Remove the current account “${username}” and sign out? You will need its activation code to add it again.`,
    confirmRemove: 'Remove',
    cancel: 'Cancel',
    removeFailed: 'Could not remove account'
  }
} as const

let confirmationBusy = false

function getStrings(): (typeof STRINGS)['en' | 'zh'] {
  return STRINGS[runnerConfigStore.read().language === 'en' ? 'en' : 'zh']
}

export async function confirmAccountRemoval({
  account,
  dialog: dialogApi,
  parent
}: {
  account: Pick<DesktopAccount, 'active' | 'username'>
  dialog: Pick<typeof dialog, 'showMessageBox'>
  parent?: BrowserWindow
}): Promise<boolean> {
  if (confirmationBusy) {
    return false
  }

  confirmationBusy = true

  try {
    const t = getStrings()

    const options: MessageBoxOptions = {
      buttons: [t.cancel, t.confirmRemove],
      cancelId: 0,
      defaultId: 0,
      message: account.active ? t.removeCurrentConfirm(account.username) : t.removeConfirm(account.username),
      noLink: true,
      type: 'warning'
    }

    const result = parent ? await dialogApi.showMessageBox(parent, options) : await dialogApi.showMessageBox(options)

    return result.response === 1
  } finally {
    confirmationBusy = false
  }
}

export function showAccountRemovalError(dialogApi: Pick<typeof dialog, 'showErrorBox'>, error: unknown): void {
  dialogApi.showErrorBox(getStrings().removeFailed, errorMessage(error))
}
