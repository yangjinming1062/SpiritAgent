export const chat = {
  chat: {
    nav: {
      showConversations: '展开对话列表',
      hideConversations: '收起对话列表',
      companion: '陪伴对话',
      assistant: '专业对话',
      readOnly: '任务对话 · 只读',
      execution: '执行详情',
      closeExecution: '关闭执行详情',
      pinned: '置顶',
      recent: '最近对话',
      running: '运行中',
      loadFailed: '对话加载失败，请重试。已有内容与草稿已保留。'
    },
    replaceReply: '换一个回答',
    replacingReply: '换一个回答中…',
    topic: {
      open: '查看原文',
      unavailable: '原始链接不可用',
      like: '喜欢这类',
      less: '少推荐这类',
      undo: '再次点击撤销反馈',
      failed: '反馈暂不可用'
    },
    defaultSessionTitle: '日常对话',
    inputPlaceholder: '跟它说，或把文件拖过来',
    typing: '输入中…',
    openMainSessionFailed: '无法打开日常对话',

    filesReceived: (count: number) => `收到 ${count} 个文件`,
    attachmentsAdded: (count: number) => `添加了 ${count} 个附件`,
    filesHandoffFailed: '文件转交失败，请重新拖入',
    connectionInterrupted: '连接中断，重连后同步回复。',
    sendFailed: '发送失败',

    media: {
      generating: '正在制作…',
      generationFailed: '生成失败',
      resultUnknown: '生成结果尚未确认',
      reviewPending: '等待选择候选',
      imageLoading: '图片加载中…',
      loadFailed: '媒体加载失败'
    },

    copy: {
      label: '复制消息',
      failed: '复制消息失败'
    },

    contextMenu: {
      label: '消息操作',
      copySelection: '复制所选文本'
    },

    fork: {
      label: '从这条消息派生新对话',
      inFlight: '派生新对话中…',
      otherBusy: '另一条正在派生',
      failed: '派生对话失败',
      internalError: '派生失败：服务端拒绝或网络异常'
    },

    undo: {
      label: '撤回此条消息',
      inFlight: '撤回中…',
      otherBusy: '另一条正在撤回',
      failed: '撤回消息失败',
      internalError: '撤回失败：服务端拒绝或网络异常',
      confirmTitle: '撤回这条消息？',
      confirmDescription: '此操作将一并删除其后所有消息。',
      confirmLabel: '撤回',
      extraImagesNotRestored: (count: number) => `输入框一次只能附加一张图片，另有 ${count} 张图片未恢复`
    },

    edit: {
      label: '编辑消息',
      cancel: '取消编辑 (Esc)',
      send: '发送修改并重新生成回复',
      hint: '发送后重新生成回复，保留原消息附件',
      retainedImage: '原消息图片，发送后保留',
      retainedVideo: '原消息视频，发送后保留',
      confirmTitle: '继续发送修改？',
      confirmDescription: '这不是最后一条消息，发送修改后将删除其后的所有消息。',
      confirmLabel: '发送修改',
      stale: '消息状态已变化，请等待回复结束或重新选择这条消息',
      failed: '编辑消息失败，修改内容已保留'
    },

    attachment: {
      pendingImageAlt: '待发送图片',
      loading: '加载中…',
      sendingImage: '图片发送中…',
      addedImage: '已附加图片',
      removeImage: '移除附加图片',
      uploading: '上传中…',
      uploadInterrupted: '上传已中断，请重试',
      ready: '已就绪',
      retry: '重试',
      removeVideo: '移除附加视频',
      fileBadge: '文件',
      removeFile: '移除附加文件',
      folderBadge: '文件夹',
      removeFolder: '移除附加文件夹'
    },

    slash: {
      confirmRequired: '该命令需要二次确认',
      busyStop: '请先停止当前生成',
      genericFailed: '命令执行失败',
      unknownWithSuggestions: (commands: readonly string[]) =>
        `未知命令。可选: ${commands.map(s => `/${s}`).join(', ')}`,
      unknown: '未知命令',
      inFlight: '上一条命令还在执行中',
      pendingAttachment: '请先发送或取消附件再执行命令',
      confirmTitle: (name: string) => `执行 /${name}？`,
      confirmDescription: '该操作会影响历史消息。',
      confirmLabel: '执行',
      popoverTitle: '命令',
      popoverConfirm: '需确认'
    },

    picker: {
      videoFilterName: '视频文件',
      selectVideo: '选择视频',
      imageFilterName: '图片文件',
      selectImage: '选择图片',
      selectFile: '选择文件',
      selectFolder: '选择文件夹',
      openFailed: '无法打开文件选择'
    },

    voice: {
      retry: '语音暂不可用',
      retryShort: '重试',
      play: '播放语音',
      pause: '暂停播放',
      resume: '继续播放',
      unheard: '未听',
      listened: '已听',
      paused: '已暂停',
      playing: '播放中',
      loading: '加载中',
      failed: '播放失败',
      progress: '播放进度',
      saveFailed: '无法保存语音播放进度',
      controls: '语音控制',
      autoplay: '自动播放语音',
      restart: '从头播放',
      retryPlayback: '重试播放',
      hideTranscript: '收起文本',
      showTranscript: '查看文本'
    },

    voiceInput: {
      busy: '语音服务正忙，请稍后再试',
      micUnavailable: '无法使用麦克风录制语音'
    },

    input: {
      assistantPlaceholder: '输入指令、提问，或将文件拖入…',
      readOnlyHint: '任务会话 · 只读',
      addAttachment: '添加附件',
      addFile: '添加文件',
      addFolder: '添加文件夹',
      addImage: '添加图片',
      addVideo: '添加视频',
      slashShortcut: '命令快捷',
      slashShortcutHint: '命令快捷（输入 / 也能触发）',
      releaseToSendVoice: '松开发送语音',
      pressToRecordVoice: '按住录制语音消息',
      startRealtimeCall: '开始实时通话',
      stopRealtimeCall: '结束实时通话',
      muteRealtimeCall: '静音通话',
      unmuteRealtimeCall: '取消静音',
      realtimeCallConnecting: '正在连接通话…',
      realtimeCallListening: '通话中 · 正在倾听',
      realtimeCallThinking: '通话中 · 正在思考',
      realtimeCallSpeaking: '通话中 · 正在讲话',
      realtimeCallMuted: '通话中 · 麦克风已静音',
      realtimeCallError: '通话连接失败',
      stopGenerating: '停止生成',
      stopPending: '正在停止，等待确认',
      sendMessage: '发送消息',
      sendMessageShortcut: '发送消息 (Enter)'
    },

    emptyHint: '说点什么，或发送文件、图片、视频',

    time: {
      weekdays: ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'] as readonly string[],
      yesterdayAt: (time: string) => `昨天 ${time}`,
      weekdayAt: (weekday: string, time: string) => `${weekday} ${time}`,
      monthDayAt: (month: number, day: number, time: string) => `${month}月${day}日 ${time}`,
      fullDateAt: (year: number, month: number, day: number, time: string) => `${year}年${month}月${day}日 ${time}`
    },

    reasoning: {
      thinkingStreaming: '思考中…',
      reasoningStreaming: '推理中…',
      done: '已完成思考',
      collapse: '收起',
      expand: '展开查看'
    },

    summary: {
      emptyBody: '（无摘要内容）',
      cancelled: '已停止'
    },

    tools: {
      busy: (name: string) => `它正在忙… (${name})`,
      completed: (count: number) => `它做了 ${count} 步`,
      genericName: '工具'
    },

    submit: {
      videoUploadFailed: '视频上传失败，请重新选择',
      videoUploading: '视频正在上传处理中，请稍候…',
      unknownCommand: (name: string) => `未知命令: /${name}。试试 /help 查看可用命令。`,
      displayVideo: '（视频）',
      promptVideo: '请看这段视频',
      promptImage: '请看这张图片',
      fileInlinePrefix: (name: string, path: string) => `[文件: ${name}] ${path}`,
      folderInlinePrefix: (name: string, path: string) => `[文件夹: ${name}] ${path}`,
      attachmentsHeading: (names: string) => `附件：${names}`,
      attachmentsJoiner: '、'
    },

    params: {
      openPanel: '高级对话设置',
      tabs: {
        context: '上下文',
        temperature: '温度',
        reasoning: '思考'
      },
      temperaturePresets: {
        precise: '严谨',
        balanced: '平衡',
        divergent: '发散'
      },
      temperatureStyles: {
        precise: '严谨',
        balanced: '平衡',
        divergent: '发散'
      },
      reasoningOptions: {
        none: '关闭',
        minimal: '极简',
        low: '低',
        medium: '中',
        high: '高',
        xhigh: '超高',
        max: '极限',
        ultra: '终极'
      },
      reasoningHints: {
        high: '深推理，适合复杂方案与证明。',
        low: '短思考，适合简单问答。',
        medium: '常规推导，适合写作与语言学习。',
        none: '直接作答，响应最快。',
        minimal: '极轻思考，略高于关闭，适合极简问答。',
        xhigh: '超深推理，适合复杂推演与研究级任务。',
        max: '逼近供应商上限的极限推理，尽量多想。',
        ultra: '产品最高档；供应商不支持时自动降级到其最高支持档。'
      },

      thresholdMarker: (pct: number) => `自动压缩阈值节点 (${pct}%)`,

      manualCompressFailed: '手动压缩上下文失败',

      thresholdSliderHeading: '上下文窗口负载',
      thresholdSliderSubheading: '/ 自动压缩线',
      thresholdSliderTriggerLabel: '触发节点:',
      thresholdSliderAria: '自动压缩阈值触发线（左右拖动调节）',
      thresholdSliderMin: '30% 紧凑压缩',
      thresholdSliderHint: '左右拖动手柄调节阈值',
      thresholdSliderMax: '100% 满额触发',
      thresholdSliderDescription: '当会话上下文达到设定比例时，后台自动总结早期历史，释放空间并保持对话连贯。',

      saveFailed: '会话参数保存失败，请重试',
      resetConfirm: '当前对话已恢复跟随全局设置',
      compressSuccessMore: (count: number) => `已成功整理并压缩 ${count} 条历史会话`,
      compressNotNeeded: '当前历史消息较少，暂无需压缩',

      sessionScopeBadge: '当前会话',
      sessionScopeBadgeTitle: '仅覆盖当前对话的参数，恢复默认后跟随全局设置',
      closePanel: '关闭面板',

      statsUsed: '当前已用',
      statsMax: '最大容量',
      statsPercent: '占用比例',
      statsTokens: 'Tokens',
      statsNodeAt: (pct: number) => `节点 ${pct}%`,

      cacheUsageTitle: '最近完成回合 · 缓存命中率',
      cacheUsagePending: '暂无本次运行的回合统计',
      cacheUsageUnavailable: '供应商未提供',
      cacheUsagePartial: '部分数据',
      cacheUsageTokens: (cached: string, input: string) => `命中 / 输入：${cached} / ${input} Tokens`,
      cacheUsageRequests: (reported: number, total: number) => `已报告请求：${reported} / ${total}`,

      statusHealthy: '上下文空间充裕，对话可以顺畅进行。',
      statusWarning: '上下文逐渐累积，靠近自动压缩阈值，可随时整理。',
      statusCritical: '上下文负荷较高，已达到自动压缩线，建议立即压缩。',

      compressing: '压缩较早的对话中…',
      compressAction: '立即压缩上下文',

      temperatureLabel: '采样温度',
      temperatureSliderAria: '采样温度',
      temperatureScaleMin: '0.00 严谨',
      temperatureScaleMid: '0.70 平衡',
      temperatureScaleMax: '1.00 发散',
      temperatureHint: '低更确定，高更发散。仅对当前会话生效。',

      reasoningLabel: '思考推理深度',
      reasoningFallbackLabel: '关闭',

      scopeNote: '仅覆盖当前对话；恢复默认后跟随全局',
      scopeNoteTitle: '仅覆盖当前对话的参数，恢复默认后跟随全局设置',
      resetButtonTitle: '清除当前对话的参数覆盖，重新跟随全局设置',
      resetButton: '恢复默认'
    },

    presetPicker: {
      title: '选择新对话的预设',
      intro: '这条对话的系统提示词将从这里固定；选定后仍可改名、归档或删除。',
      confirm: '创建',
      cancel: '取消',
      fetchFailed: '加载预设失败，请稍后再试',
      pickOne: '请先选择一个预设'
    },

    sessionRename: {
      action: '重命名',
      inputLabel: '对话名称',
      placeholder: '输入对话名称',
      hint: 'Enter 保存 · Esc 取消',
      forbidden: '系统对话不能改名',
      failed: '改名失败，已恢复原名称'
    }
  },
  conversation: {
    sessionSidebar: {
      title: '会话',
      new: '新建对话',
      searchAria: '搜索会话',
      searchPlaceholder: '搜索会话…',
      searchResultsHeading: '搜索结果',
      searching: '搜索中…',
      noMatch: '没有找到匹配会话',
      badgeArchived: '已归档',
      systemHeading: '系统对话',
      loadingSystem: '加载系统对话中…',
      noSystem: '暂无系统对话',
      ordinaryHeading: '普通对话',
      loadingSessions: '加载会话中…',
      noOrdinary: '暂无普通对话，点击上方新建',
      systemSessionFallback: '我的伙伴',
      newSessionFallback: '新对话',
      messageCount: (n: number) => `${n} 条消息`,
      sortOptions: {
        recent: '按最近活跃排序',
        created: '按创建时间排序',
        messages: '按消息数排序'
      },
      actions: {
        pin: '置顶',
        unpin: '取消置顶',
        archive: '归档',
        restore: '恢复',
        delete: '删除'
      },
      time: {
        now: '刚刚',
        todayPrefix: (time: string) => `今天 ${time}`,
        yesterdayPrefix: (time: string) => `昨天 ${time}`,
        dateFormat: (month: number, day: number, time: string) => `${month}月${day}日 ${time}`
      },
      archive: {
        loading: '加载中…',
        empty: '暂无归档会话',
        collapse: '收起归档会话',
        expand: (count: number) => `已归档会话${count > 0 ? ` (${count})` : ''}`
      }
    },
    runRail: {
      label: '运行轨迹',
      subtitle: (steps: number) => `${steps} 步 · 本轮`,
      idleSubtitle: '空闲中',
      toolsTitle: '本轮工具',
      toolsSubtitleIdle: '尚未动手',
      toolsSubtitleSteps: (steps: number) => `${steps} 步`,
      preparing: '准备中…',
      toolsEmpty: '这一轮还没动手',
      artifactsTitle: '本会话工件',
      artifactsSubtitleEmpty: '尚无',
      artifactsSubtitleCount: (count: number) => `${count} 项`,
      artifactsEmpty: '这一轮暂无生成图或视频'
    }
  }
}
