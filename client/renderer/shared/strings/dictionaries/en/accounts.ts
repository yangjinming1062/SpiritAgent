import type { Dictionary } from '../zh'
export const accounts: Pick<Dictionary, 'accounts' | 'activation'> = {
  accounts: {
    title: 'Accounts',
    currentAccount: 'Current account',
    switchAccount: 'Switch account',
    accountList: 'Available accounts',
    accountLoadFailed: 'Could not read accounts. Try again.',
    addAccount: 'Add account',
    removeAccount: 'Remove account',
    removableAccounts: 'Choose an account to remove',
    accountLoading: 'Reading accounts…'
  },
  activation: {
    addTitle: 'Add Account',
    addSubtitle: 'Paste the new account’s activation code. Your current account stays active if verification fails.',
    title: (brandName: string) => `Activate ${brandName}`,
    subtitle: 'Paste the activation code you received to get started.',
    close: 'Close',
    placeholder: 'Paste the activation code here…',
    cancel: 'Cancel',
    submit: 'Activate',
    submitBusy: 'Activating…',
    failed: 'Activation failed. Check the code and try again.'
  }
}
