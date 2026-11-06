export const accounts = {
  accounts: {
    title: '账户',
    currentAccount: '当前账户',
    switchAccount: '切换账户',
    accountList: '可切换账户',
    accountLoadFailed: '账户读取失败，请重试',
    addAccount: '添加账户',
    removeAccount: '移除账户',
    removableAccounts: '选择要移除的账户',
    accountLoading: '读取账户中…'
  },
  activation: {
    addTitle: '添加账户',
    addSubtitle: '粘贴新账户的激活码。验证失败时当前账户会保持不变。',
    title: (brandName: string) => `激活 ${brandName}`,
    subtitle: '粘贴您收到的激活码以开始使用。',
    close: '关闭',
    placeholder: '在此粘贴激活码…',
    cancel: '取消',
    submit: '激活',
    submitBusy: '激活中…',
    failed: '激活失败，请检查激活码后重试'
  }
}
