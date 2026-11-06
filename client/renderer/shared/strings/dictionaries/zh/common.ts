import { uiCopy } from '../../../../../../shared/ui-copy'

export const common = {
  brand: {
    name: '唤生',
    fullName: '唤生桌面'
  },
  common: {
    actionVariants: {
      title: '各画面制作进度',
      readyWithStatus: (status: string) => `可播放 · ${status}`,
      remakeFailed: '重做失败',
      statuses: {
        not_generated: '待制作',
        queued: '等待制作',
        processing: '制作中',
        ready: '可播放',
        failed: '制作失败',
        blocked: '暂时无法制作',
        disabled: '已停用',
        excluded: '已移除'
      }
    },
    videoQueue: {
      waiting: '视频排队中',
      rate_limited: '服务繁忙，等待重试',
      blocked: '等待核对生成结果'
    },
    done: '完成',
    moreActions: '更多操作',
    creationDetails: '制作资料',
    companionControl: {
      title: '生活区',
      show: '显示生活区',
      left: '左侧',
      right: '右侧',
      saveFailed: '保存生活区显示设置失败，请重试'
    },
    save: '保存',
    saving: uiCopy.saving,
    cancel: '取消',
    clear: '清除',
    close: '关闭',
    copied: uiCopy.copied,
    copyFailed: uiCopy.copyFailed,
    loading: uiCopy.loading,
    maximize: '最大化',
    minimize: '最小化',
    remove: '移除',
    restore: '还原',
    retry: uiCopy.retry,
    pageRestoreFailed: '页面恢复失败',
    pickDate: '选择日期',
    datePicker: { year: '年', month: '月', day: '日' },
    imagePick: { tooLarge: '图片太大，请换一张较小的图片', readFailed: '无法读取所选图片，请重试' },
    processing: uiCopy.processing,
    // 中性措辞取自根 shared/ui-copy.ts，与手机端逐字一致。
    operationFailed: uiCopy.operationFailed,
    saveChanges: '保存修改',
    unsavedChanges: '未保存修改',
    keepMyEdits: '已核对，保留我的修改',
    forget: '忘记',
    changedElsewhere: '资料已在其他设备修改，请核对最新内容。',
    view: '查看',
    back: '返回',
    search: '搜索',
    previous: '上一页',
    next: '下一页',
    loadMore: '加载更多',
    totalItems: (count: number) => `共 ${count} 项`,
    pageOf: (page: number, total: number) => `第 ${page} / ${total} 页`,
    refresh: '刷新'
  },
  notifications: {
    region: '通知',
    hide: '隐藏',
    show: '显示',
    more: (count: number) => `另外 ${count} 条通知`,
    clearAll: '全部清除',
    dismiss: '关闭通知',
    details: '详情',
    copyDetail: '复制详情',
    errors: {
      methodNotAllowed: (brandFullName: string) =>
        `暂时无法完成操作，请稍后重试。如果持续出现，请检查 ${brandFullName} 是否需要更新。`,
      microphonePermission: '麦克风权限已被拒绝。'
    },
    voice: {
      invalidTitle: '音色已失效',
      invalidMessage: (name: string) =>
        `你之前选的音色「${name}」已不可用，已临时改用默认音色，请在「伙伴 → 声音」中重新选择。`,
      invalidAction: '选择声音'
    },
    system: {
      view: '查看',
      scheduledTask: '定时任务',
      imageReady: '图片已生成，点击查看',
      videoReady: '视频已生成，点击查看'
    }
  },
  errors: {
    boundaryTitle: '界面出错了',
    boundaryDesc: '此视图遇到意外错误。你的对话和设置是安全的。',
    reloadWindow: '重新加载窗口'
  },
  ui: {
    lightbox: {
      backdropAria: '点击背景关闭',
      closePreview: '关闭预览',
      zoomIn: '放大',
      zoomOut: '缩小',
      zoomReset: '重置',
      zoomPercent: (n: number) => `${n}%`,
      zoomHint: '滚轮缩放 · 拖动平移 · 双击放大/还原'
    },
    search: {
      clear: '清除搜索'
    }
  }
}
