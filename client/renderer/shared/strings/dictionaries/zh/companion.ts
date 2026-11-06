export const companion = {
  companion: {
    menu: {
      quietOff: (minutes: number) => `结束安静（剩 ${minutes} 分钟）`,
      quietOn: (minutes: number) => `安静 ${minutes} 分钟`,
      resetPosition: '一键归位',
      hide: '隐藏角色',
      activate: '激活 / 登录',
      openMainWindow: '打开主窗口'
    },
    egg: {
      preparing: '加载伙伴造型中…',
      failed: '伙伴造型加载失败',
      unavailable: '还没有可呈现的影像',
      retry: '重试',
      openVisuals: '查看影像',
      ariaLabel: '伙伴的蛋'
    }
  },
  presets: {
    names: {
      companion: '陪伴',
      copywriter: '文案秘书',
      language_teacher: '语言老师',
      general: '通用助手'
    },
    descriptions: {
      copywriter: '起草、润色与整理文案、邮件和会议记录，保留原意，贴合受众与场合。',
      language_teacher: '按水平与目标讲解、纠错和练习，提供忠实自然的翻译。',
      general: '不带角色设定的通用对话：保留工具调用、记忆与环境提示，只去掉具体角色与专业指引。'
    }
  }
}
