export const dict = {
  brand: {
    name: '唤生',
    fullName: '唤生桌面',
    copyright: 'Copyright © 2026 唤生',
    slogan: ['让想象中的伙伴，来到你的桌面。', '会陪伴、能做事的专属 AI 伙伴。']
  },

  common: {
    apply: '应用',
    back: '返回',
    save: '保存',
    saving: '保存中…',
    cancel: '取消',
    change: '更改',
    choose: '选择',
    clear: '清除',
    close: '关闭',
    confirm: '确认',
    connect: '连接',
    connecting: '连接中',
    continue: '继续',
    copied: '已复制',
    copy: '复制',
    copyFailed: '复制失败',
    delete: '删除',
    done: '完成',
    error: '错误',
    failed: '失败',
    free: '免费',
    loading: '加载中…',
    notSet: '未设置',
    refresh: '刷新',
    remove: '移除',
    replace: '替换',
    retry: '重试',
    run: '运行',
    send: '发送',
    set: '设置',
    skip: '跳过',
    update: '更新',
    on: '开',
    off: '关'
  },

  boot: {
    ready: (brandFullName: string) => `${brandFullName} 已就绪`,
    desktopBootFailedWithMessage: (message: string) => `桌面启动失败：${message}`,
    steps: {
      connectingGateway: '正在连接桌面网关',
      startingDesktopConnection: '正在启动桌面连接',
      startingSpiritAgentDesktop: (brandFullName: string) => `正在启动 ${brandFullName}…`
    },
    errors: {
      desktopBootFailed: '桌面启动失败',
      desktopReconnectFailed: '与后端连接已中断，应用正在持续重试。'
    },
    failure: {
      title: (brandName: string) => `${brandName} 无法启动`,
      description: '后台网关没有启动。请尝试下面的恢复步骤；这里不会删除你的对话或设置。',
      retry: '重试'
    }
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
      elevenLabsNeedsKey: 'ElevenLabs STT 需要 ELEVENLABS_API_KEY。',
      elevenLabsRejectedKey: 'ElevenLabs 拒绝了该 API key (401)。',
      methodNotAllowed: (brandFullName: string) =>
        `桌面后端拒绝了该请求 (405 Method Not Allowed)。请尝试重启 ${brandFullName}。`,
      microphonePermission: '麦克风权限已被拒绝。',
      openaiRejectedApiKey: 'OpenAI 拒绝了该 API key。',
      openaiRejectedApiKeyWithStatus: (status: number | string) =>
        `OpenAI 拒绝了该 API key (${status} invalid_api_key)。`,
      openaiTtsNeedsKey: 'OpenAI TTS 需要 VOICE_TOOLS_OPENAI_KEY 或 OPENAI_API_KEY。'
    },
    voice: {
      invalidTitle: '音色已失效',
      invalidMessage: (name: string) =>
        `你之前选的音色「${name}」已不在当前目录，已临时用默认音色，去伙伴设置里重新挑一个吧～`,
      invalidAction: '去设置'
    },
    system: {
      view: '查看',
      scheduledTask: '定时任务',
      imageReady: '图片已生成，点击查看',
      videoReady: '视频已生成，点击查看',
      channelLabel: 'IM 通道',
      channelWeixin: '微信',
      channelConnected: (label: string) => `${label}已连接`,
      channelLoginRequired: (label: string) => `${label}登录已过期，请到设置重新扫码`,
      channelError: (label: string, detail?: string) => `${label}通道异常${detail ? `：${detail}` : ''}`,
      channelPeerRequest: (label: string, name: string) => `${label}收到新消息${name ? `：${name}` : ''}`
    }
  },

  activation: {
    title: (brandName: string) => `激活 ${brandName}`,
    subtitle: '粘贴您收到的激活码以开始使用。',
    close: '关闭',
    placeholder: '在此粘贴激活码…',
    cancel: '取消',
    submit: '激活',
    submitBusy: '激活中…'
  },

  settings: {
    title: '应用设置',
    closeSettings: '关闭设置',
    nav: {
      inference: '推理与对话',
      about: '关于',
      appearance: '外观',
      channels: '聊天通道',
      interaction: '交互',
      navAriaLabel: '设置分区导航',
      persona: '角色与记忆',
      runner: '本机执行器',
      shortcuts: '快捷键',
      skills: '技能与工具',
      voice: '音色'
    },
    shortcuts: {
      heading: '全局快捷键',
      intro: '在系统任意界面通过全局快捷键唤起或隐藏伴侣。点击按键框即可录制新组合。',
      toggleVisibility: '隐藏 / 显示伴侣',
      toggleVisibilityDesc: '快速在桌面显示或隐藏伙伴窗口。',
      openLiving: '唤起 / 隐藏生活空间',
      openLivingDesc: '快速唤起或隐藏生活空间沉浸式陪伴窗口。',
      openWorkbench: '唤起 / 隐藏工作台',
      openWorkbenchDesc: '快速唤起或隐藏工作台生产力窗口。',
      pressKeysPrompt: '请按下组合键…',
      pressKeysHint: '按 Esc 取消录制，按 Backspace 或 Delete 清空',
      resetAll: '恢复全部默认',
      resetAllSuccess: '已恢复默认快捷键',
      conflictError: '快捷键已被系统或其他应用占用',
      empty: '未设置'
    },
    channels: {
      heading: '聊天通道',
      intro: '让同一个伙伴在微信等 IM 上陪聊——人设与记忆和桌面共享，桌面端可回看但不可代答。',
      loadFailed: '通道状态加载失败',
      statusLabels: {
        connected: '已连接',
        login_pending: '等待扫码',
        login_required: '需重新登录',
        error: '异常',
        disabled: '未启用'
      } as Record<string, string>,
      weixin: {
        title: '微信',
        intro: '扫码登录你的微信个人号（官方 ClawBot 通道）。伙伴只能回复消息，不能主动发起。',
        loginAction: '扫码登录',
        retryAction: '重新获取二维码',
        logoutAction: '退出登录',
        logoutConfirmTitle: '退出微信登录？',
        logoutConfirmDescription: '退出后伙伴将不再回复微信消息，重新登录需要再次扫码。',
        loginStartFailed: '登录启动失败',
        loginSuccess: '微信已连接',
        logoutSuccess: '已退出微信登录',
        logoutFailed: '退出失败',
        qrPrompt: '打开微信扫一扫',
        scanedPrompt: '已扫码，请在手机上确认',
        expiredPrompt: '二维码已过期，请重新获取',
        connectedAs: (name: string) => `已连接${name ? `：${name}` : ''}`
      },
      peers: {
        title: '对端审批',
        intro: '陌生对端首次来信会收到配对提示，批准后才能与伙伴对话；被拉黑者静默。',
        empty: '暂无对端记录',
        approve: '批准',
        block: '拉黑',
        remove: '删除',
        pendingLabel: '待审批',
        allowedLabel: '已批准',
        blockedLabel: '已拉黑',
        actionFailed: '操作失败',
        requestToast: (channel: string, peer: string) => `${channel}上有人想和伙伴聊天：${peer}`
      }
    },
    appearance: {
      heading: '外观',
      hint: '主题同时应用到生活空间与工作台两个窗口，伙伴形象不受影响。'
    },
    about: {
      heading: (brandFullName: string) => brandFullName,
      version: (value: number | string) => `版本 ${value}`,
      versionUnavailable: '版本不可用',
      intro: '桌面客户端版本与更新管理。',
      checkForUpdates: '检查更新',
      checking: '检查中…',
      upToDate: '已是最新版本',
      upToDateWithVersion: (value: number | string) => `已是最新版本（v${value}）`,
      updateAvailable: (value: number | string) => `v${value} 可用`,
      updateDownloaded: (value: number | string) => `v${value} 已就绪,等待重启安装`,
      updateError: (value: string) => `检查更新失败:${value}`
    },
    runner: {
      title: '执行器配置',
      intro: '配置底层执行器的相关设置。修改这些设置需要重启执行器才能生效。',
      loading: '正在加载执行器配置...',
      failedLoad: '执行器配置加载失败',
      save: '保存配置',
      saveSuccess: '配置已保存',
      saveFailed: '配置保存失败',
      terminal: '终端设置',
      terminalEnvType: '环境类型',
      ssh: 'SSH 连接',
      sshHost: '主机地址',
      sshPort: '端口',
      sshUser: '用户名',
      sshPassword: '密码',
      sshKey: '私钥路径',
      security: '安全',
      securityRedactSecrets: '屏蔽敏感信息',
      browser: '浏览器设置',
      browserAllowPrivateUrls: '允许内网访问',
      debug: '调试开关',
      debugInterrupt: '中断模式'
    },
    skills: {
      title: '技能',
      intro:
        '下方每一项对应 $SPIRITAGENT_HOME/skills 下的一个 category 目录。开启或关闭会即时推送给执行器;启用集会在每个对话轮次发给后端,让模型只看到你能调用的本地技能。',
      loading: '正在加载技能…',
      loadError: '无法从磁盘读取技能列表。',
      saveError: '无法保存技能开关。',
      refreshError: '本地已保存,但后端会话未刷新 — 下一轮对话仍可能看到旧的技能集合,请再次切换。',
      emptyTitle: '未安装任何技能',
      emptyDesc: (brandName: string) => `请重新安装 ${brandName} 以恢复内置技能。`,
      hiddenByPlatformTitle: '当前操作系统没有可用技能',
      hiddenByPlatformDesc: (brandName: string) =>
        `本版本 ${brandName} 内置的技能面向其他操作系统。请在支持的操作系统上重新安装 ${brandName} 后再启用。`
    },
    inference: {
      heading: '推理与对话',
      intro:
        '仅配置普通对话的默认参数、思考深度与上下文压缩策略。每个特殊会话使用独立场景默认值，并在其会话窗口内修改。',
      loading: '加载中…',
      saveFailed: '无法保存推理与对话设置。',
      saved: '推理与对话设置已保存。',
      agentDefaults: {
        heading: '智能体默认',
        intro: '适用于未单独设置参数的普通对话；不影响任何特殊会话。',
        reasoningEffort: '推理深度',
        reasoningEffortDesc: '模型每轮推理的强度。none 关闭推理，low/medium/high 逐级加深。',
        backgroundReview: '后台记忆整理',
        backgroundReviewDesc: '异步从普通对话历史中抽取记忆，不控制特殊会话的记忆整理。',
        reasoningOptions: {
          none: '关闭',
          low: '低',
          medium: '中',
          high: '高'
        }
      },
      contextCompression: {
        heading: '对话压缩',
        intro: '长对话接近上下文窗口上限时,自动用摘要替换最早的消息,让单次会话持续更久。',
        enableCompression: '启用上下文压缩',
        enableCompressionDesc: '关闭后仅保留最近 40 条消息(确定性截断),不做语义摘要。',
        threshold: '压缩阈值',
        thresholdDesc: '上下文占用达到窗口的该比例时触发压缩（30%–100%）。'
      },
      temperature: {
        heading: '模型温度',
        intro:
          '控制不同场景下的模型输出随机性与创造力。界面统一使用 0–1 刻度，发起请求时会自动映射到当前供应商的实际范围。',
        chatTemperature: '普通对话默认温度',
        chatTemperatureDesc: '工作台普通对话的默认生成温度。较低的值更严谨稳定，较高的值更发散有创意。',
        titleTemperature: '标题生成温度',
        titleTemperatureDesc: '根据首轮对话自动生成会话标题的温度。建议保持较低以保证概括准确性。',
        compressionTemperature: '上下文压缩温度',
        compressionTemperatureDesc: '对话历史超长时生成记忆摘要的温度。建议保持 0 以保证事实忠实度。'
      }
    },
    interaction: {
      title: '交互',
      intro: '伙伴怎么回应你、什么时候可以打扰你。',
      voiceHeading: '对话与语音',
      responseMode: '回应方式',
      responseModeText: '默认文字',
      responseModeVoice: '始终语音',
      responseModeDesc: '只控制生活空间陪伴对话，工作台始终文字。',
      recording: '录音时长上限',
      recordingDesc: '单条语音录音的最大时长，到达上限后自动停止录制并发送。',
      recordingSecondsSuffix: '秒',
      recordingSaveFailed: '保存录音时长失败',
      tierHeading: '打扰档位',
      tierHint: '只约束伙伴的主动行为，你发起的交互不受限。',
      tierAriaLabel: '打扰档位',
      smartHeading: '智能反应与自主行为',
      smartHint: '让伙伴具备更智能的思考与决策能力；关闭可降低 LLM 调用消耗。',
      pokeThinking: '戳击思考回应',
      pokeThinkingAria: '戳击思考回应',
      pokeThinkingDesc: '戳击时由 LLM 生成反应文案与表情（关闭使用预制反馈）；拖拽始终使用本地预制反馈',
      idleAffect: '空闲情境表达',
      idleAffectAria: '空闲情境情绪',
      idleAffectDesc: '自主档且桌面精灵显示时，空闲 30 分钟以上由 LLM 决定情境表情与动作',
      autonomy: '自主空间决策',
      autonomyAria: '自主空间决策',
      autonomyDesc: '自主档且桌面精灵显示时由 LLM 决定漫游、栖身与靠近（关闭按本地规则）',
      autonomousMedia: '夜间自主心意创作',
      autonomousMediaAria: '夜间自主心意创作',
      autonomousMediaDesc: '休息窗口内允许伙伴主动生成图片或短视频，并保存到生活空间片刻',
      autonomousVoice: '夜间自主语音心意',
      autonomousVoiceAria: '夜间自主语音心意',
      autonomousVoiceDesc: '休息窗口内允许伙伴使用当前音色录制语音，或为惊喜视频和图片添加配音'
    },
    persona: {
      title: '角色与记忆',
      intro: '人设怎么改、记得什么，都在这里。',
      sectionTitle: '角色',
      sectionMemory: '长期记忆',
      editAction: '编辑',
      editHeading: '编辑角色',
      defaultName: '伙伴',
      noPersonality: '还没设定性格',
      nameLabel: '名字',
      namePlaceholder: '给我起个名字',
      relationshipLabel: '角色定位',
      relationshipPlaceholder: '或者自由描述…',
      personalityLabel: '性格',
      personalityPlaceholder: '自由描述…',
      hintEmptyName: '得给我起个名字呀',
      hintSaveFailed: '保存失败，稍后再试',
      hintHydrateFailed: '已保存，但本地刷新失败，稍后再试',
      retuneAction: '重新对话微调性格',
      retuneHint: '以对话方式分步调整名字、性格、说话风格与你的信息（保留现有长期记忆）',
      retuneModalTitle: '重新对话微调性格',
      retuneLoadFailed: '暂时拉不到个人资料，稍后再试',
      retunePrev: '上一步',
      retuneNext: '下一步',
      retuneStepPrefix: (n: number, total: string | number) => `第 ${n} 步 · ${total}`,
      retuneReviewTitle: '回顾',
      retuneEmpty: '—',
      retuneSaveFailed: '保存失败了，稍后再试',
      autoDerivedChip: '自动派生',
      fields: {
        name: '角色名',
        namePlaceholder: '给你起个名字',
        relationship: '关系 / 角色定位',
        personality: '性格',
        speakingStyle: '说话风格（显式可选）',
        speakingStylePlaceholder: '留空将根据性格自动派生',
        userCallName: '希望被怎么称呼',
        userGender: '你的性别',
        userAgeBucket: '年龄段',
        userHobbies: '爱好',
        userFreeform: '还有什么想告诉我'
      },
      steps: {
        name: '角色定义：名称',
        relationship: '关系 / 角色定位',
        personalityStyle: '性格 与 说话风格',
        aboutBasics: '让伙伴更了解你：基础',
        aboutHobbies: '让伙伴更了解你：爱好 & 补充'
      },
      reviewRows: {
        name: '名字',
        relationship: '关系',
        personality: '性格',
        speakingStyle: '说话风格',
        speakingStyleFallback: '自动派生',
        userCallName: '称呼',
        userGender: '我的性别',
        userAgeBucket: '年龄段',
        userHobbies: '爱好',
        userFreeform: '补充'
      }
    },
    voice: {
      title: '音色',
      intro: '选择适合当前系统语言的说话音色，或设计一个专属音色。',
      noTtsConfigured: '尚未配置 TTS 供应商。',
      noVoicesForLanguage: '已配置的 TTS 供应商没有适配当前系统语言的音色。',
      genderFilterAria: '声线筛选',
      preview: '试听',
      use: '使用',
      inUse: '使用中',
      noMatch: '当前筛选无匹配音色。',
      designHeading: '设计专属音色',
      designPlaceholder: '描述你想要的音色…',
      designGenerate: '生成预览',
      designGenerating: '生成中…',
      designFailed: '生成失败，换个描述试试？'
    },
    theme: {
      themesHeading: '主题方案',
      themesAriaLabel: '主题方案',
      activeBadge: '生效中',
      materialHeading: '材质效果说明',
      materialTransparent: '清透模式（透明）',
      materialTransparentDesc: '启用液态玻璃与磨砂透视，房间图透出流动光影，桌面伴侣通透灵动。',
      materialTransparentDayNight: '日色透明 / 夜色透明',
      materialSolid: '实底模式（经典）',
      materialSolidDesc: '实体不透明表面，石墨与暖纸底板，具有极高对比度，适合复杂桌面背景环境。',
      materialSolidDayNight: '日色 / 夜色'
    },
    memory: {
      tabAriaLabel: '记忆类型',
      presetLabel: '当前预设',
      tabRecall: (count: string | number) => `主动召回 · ${count}`,
      tabAutoInject: (count: string | number) => `自动注入 · ${count}`,
      userProfileHint: (count: string | number) => `${count} 个 user_profile 由你独占`,
      loading: '加载中…',
      loadFailedHint: '加载失败',
      loadFailedToast: '加载长期记忆失败',
      saveFailedHint: '保存失败，已回滚',
      saveFailedToast: '保存记忆失败',
      deleteFailedHint: '删除失败，已回滚',
      deleteFailedToast: '删除记忆失败',
      emptyRecall: '还没有召回记忆。精灵会在对话中主动写下。',
      saved: '已保存',
      saving: '保存中…',
      delete: '删除',
      autoInjectIntro: (max: number) =>
        `自动注入段每次对话都默念一遍，LLM 写入时已限 ${max} 字符。这里你可以查看或修正。`,
      autoInjectUpdated: '更新',
      autoInjectChars: (current: number, max: number) => `${current} / ${max} chars`,
      autoInjectEmpty: '（空）让精灵在对话中自然填入',
      autoInjectSlotHints: {
        communicationStyle: '回答怎么框定（详略程度、口吻风格、是否使用列表等）',
        rapportState: '当前关系/熟悉度阶段',
        interactionPattern: '典型使用节奏（夜间高频、短对话等）',
        moodPattern: '近期情绪倾向（模式，不是当下 mood）',
        relationshipSignal: '信任/打趣频率/正式度'
      } as Record<string, string>
    }
  },

  skills: {
    tabSkills: '技能',
    tabToolsets: '工具集',
    all: '全部',
    other: '其他',
    searchSkills: '搜索技能…',
    searchToolsets: '搜索工具集…',
    loading: '正在加载能力…',
    noSkillsTitle: '未找到技能',
    noSkillsDesc: '尝试更宽泛的搜索或其他分类。',
    loadFailedTitle: '技能列表加载失败',
    loadFailedDesc: '请稍后重试,或检查 $SPIRITAGENT_HOME/skills 目录。',
    noToolsetsTitle: '未找到工具集',
    noToolsetsDesc: '尝试更宽泛的搜索词。',
    noDescription: '暂无描述。',
    toolsetsEnabled: (enabled: number, total: number) => `已启用 ${enabled}/${total} 个工具集`,
    skillsLoadFailed: '技能加载失败',
    toolsetsRefreshFailed: '工具集刷新失败'
  },

  toolsets: {
    browser_automation: { label: '浏览器自动化', description: '导航、点击、快照、Cookie/CDP 等多后端浏览器能力。' },
    file_operations: { label: '文件操作', description: '读写、补丁、目录与文件搜索。' },
    terminal: { label: '终端', description: '本地/Docker/SSH 后端的命令行执行。' },
    code_execution: { label: '代码执行', description: '沙箱 Python 执行与受限调用。' },
    process_management: { label: '进程管理', description: '后台进程的启动与跟踪。' },
    skills_system: { label: '技能系统', description: '列出、查看与管理 Skill 内容。' },
    memory: { label: '记忆', description: '长期记忆的写入、检索与删除。' },
    web_tools: { label: '联网工具', description: '网络搜索与网页内容抽取。' },
    image_generation: { label: '图片生成', description: '通过云端模型生成图片。' },
    messaging: { label: '消息', description: '通过 Webhook 发送消息。' },
    scheduled_tasks: { label: '定时任务', description: 'Cron 触发与周期调度。' },
    agent_delegation: { label: '子代理委托', description: '派生子会话与子代理。' },
    computer_use: { label: '桌面操控', description: '通过 Windows 后端接管桌面。' },
    media_analysis: { label: '多媒体分析', description: '图片分析。' }
  },

  errors: {
    boundaryTitle: '界面出错了',
    boundaryDesc: '此视图遇到意外错误。你的对话和设置是安全的。',
    reloadWindow: '重新加载窗口'
  },

  ui: {
    search: {
      clear: '清除搜索'
    }
  },

  chat: {
    defaultSessionTitle: '日常对话',
    inputPlaceholder: '跟它说，或把文件拖过来',
    typing: '正在输入...',
    openMainSessionFailed: '无法打开日常对话',

    filesReceived: (count: number) => `收到 ${count} 个文件`,
    attachmentsAdded: (count: number) => `添加了 ${count} 个附件`,
    sendFailed: '发送失败',

    media: {
      imageLoading: '图片加载中…',
      videoLoading: '视频加载中…'
    },

    copy: {
      label: '复制消息',
      copied: '已复制',
      failed: '复制消息失败'
    },

    fork: {
      label: '从这条消息派生新对话',
      inFlight: '正在派生新对话…',
      otherBusy: '另一条正在派生',
      failed: '派生对话失败',
      internalError: '派生失败：服务端拒绝或网络异常'
    },

    undo: {
      label: '撤回此条消息',
      inFlight: '正在撤回…',
      otherBusy: '另一条正在撤回',
      failed: '撤回消息失败',
      internalError: '撤回失败：服务端拒绝或网络异常',
      confirm: '撤回这条消息？此操作将一并删除其后所有消息。'
    },

    play: {
      label: '朗读',
      preparing: '正在准备语音…',
      stop: '停止朗读',
      channelBusy: '语音通道忙',
      failed: '语音朗读失败'
    },

    attachment: {
      pendingImageAlt: '待发送图片',
      loading: '加载中…',
      sendingImage: '图片发送中…',
      addedImage: '已附加图片',
      removeImage: '移除附加图片',
      uploading: '上传中…',
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
      confirmMessage: (name: string) => `执行 /${name}？该操作会影响历史消息。`,
      popoverTitle: '命令',
      popoverConfirm: '需确认'
    },

    picker: {
      videoFilterName: '视频文件',
      selectVideo: '选择视频',
      imageFilterName: '图片文件',
      selectImage: '选择图片',
      selectFile: '选择文件',
      selectFolder: '选择文件夹'
    },

    voice: {
      play: '播放语音',
      stop: '停止播放语音',
      collapse: '收起',
      showTranscript: '查看文本'
    },

    input: {
      workbenchPlaceholder: '输入指令、提问，或将文件拖入…',
      readOnlyHint: 'IM 对话 · 只读',
      addAttachment: '添加附件',
      addFile: '添加文件',
      addFolder: '添加文件夹',
      addImage: '添加图片',
      addVideo: '添加视频',
      slashShortcut: '命令快捷',
      slashShortcutHint: '命令快捷（输入 / 也能触发）',
      releaseToSendVoice: '松开发送语音',
      pressToRecordVoice: '按住录制语音消息',
      stopGenerating: '停止生成',
      sendMessage: '发送消息',
      sendMessageShortcut: '发送消息 (Enter)'
    },

    emptyHint: '说点什么，或发送文件/图片/视频给我看看～',

    time: {
      weekdays: ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'] as readonly string[],
      yesterdayAt: (time: string) => `昨天 ${time}`,
      weekdayAt: (weekday: string, time: string) => `${weekday} ${time}`,
      monthDayAt: (month: number, day: number, time: string) => `${month}月${day}日 ${time}`,
      fullDateAt: (year: number, month: number, day: number, time: string) => `${year}年${month}月${day}日 ${time}`
    },

    reasoning: {
      thinkingStreaming: '正在思考…',
      reasoningStreaming: '正在推理…',
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
      completed: (count: number) => `它做了 ${count} 步`
    },

    submit: {
      videoUploadFailed: '视频上传失败，请重新选择',
      videoUploading: '视频正在上传处理中，请稍候…',
      unknownCommand: (name: string) => `未知命令: /${name}。试试 /help 查看可用命令。`,
      displayVideo: '（视频）',
      displayImage: '（图片）',
      displayFile: (name: string) => `[文件] ${name}`,
      displayFolder: (name: string) => `[文件夹] ${name}`,
      promptVideo: '请看这段视频',
      promptImage: '请看这张图片',
      promptFile: (path: string) => `@file:${path}`,
      promptFolder: (path: string) => `@folder:${path}`,
      fileInlinePrefix: (name: string, path: string) => `[文件: ${name}] ${path}`,
      folderInlinePrefix: (name: string, path: string) => `[文件夹: ${name}] ${path}`,
      attachmentsHeading: (names: string) => `附件：${names}`,
      attachmentsJoiner: '、'
    },

    params: {
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
        low: '低',
        medium: '中',
        high: '高'
      },
      reasoningHints: {
        high: '深推理，适合复杂方案与证明。',
        low: '短思考，适合简单问答。',
        medium: '常规推导，适合开发与排错。',
        none: '直接作答，响应最快。'
      },

      contextCapsuleAria: '查看上下文记忆与压缩管理',
      contextCapsuleTitle: (used: string, total: string, pct: string, threshold: number) =>
        `当前会话上下文：${used} / ${total} Tokens (${pct}%) · 自动压缩阈值: ${threshold}% · 点击展开管理`,
      temperatureCapsuleAria: '配置当前会话采样温度',
      temperatureCapsuleTitle: (temp: string, label: string) => `当前会话采样温度: ${temp} (${label}) · 点击配置`,
      reasoningCapsuleAria: '配置当前会话思考推理深度',
      reasoningCapsuleTitle: (label: string) => `当前会话思考推理深度: ${label} · 点击配置`,
      reasoningOff: '已关闭',
      reasoningCapsuleOff: '思考关',
      reasoningCapsuleOn: (label: string) => `思考: ${label}`,

      thresholdMarker: (pct: number) => `自动压缩阈值节点 (${pct}%)`,

      manualCompressRunning: '正在压缩当前会话上下文…',
      manualCompressTooltipInline: (used: string, total: string, pct: string) =>
        `上下文：${used} / ${total} Tokens (${pct}%)`,
      manualCompressThresholdInline: (pct: number) => `压缩节点: ${pct}%`,
      manualCompressClickInline: '点击立即压缩',
      manualCompressButtonAria: '手动压缩上下文',
      manualCompressButtonTitle: '点击手动压缩当前会话上下文',
      manualCompressThresholdMarker: (pct: number) => `压缩阈值节点 (${pct}%)`,
      manualCompressSuccess: (count: number) => `已成功压缩 ${count} 条早期对话历史`,
      manualCompressNotNeeded: '当前历史消息较少，无需压缩',
      manualCompressFailed: '手动压缩上下文失败',

      thresholdSliderHeading: '上下文窗口负载',
      thresholdSliderSubheading: '/ 自动压缩线',
      thresholdSliderTriggerLabel: '触发节点:',
      thresholdSliderAria: '自动压缩阈值触发线（左右拖动调节）',
      thresholdSliderMin: '30% 紧凑压缩',
      thresholdSliderHint: '左右拖动手柄调节阈值',
      thresholdSliderMax: '100% 满额触发',
      thresholdSliderDescription: '当会话上下文达到设定比例时，后台自动总结提炼早期历史，释放空间保障记忆连贯。',

      saveFailed: '会话参数保存失败，请重试。',
      resetConfirm: '已恢复当前会话参数为默认配置',
      compressSuccessMore: (count: number) => `已成功整理并压缩 ${count} 条历史会话`,
      compressNotNeeded: '当前历史消息较少，暂无需压缩',

      sessionScopeBadge: '当前会话',
      sessionScopeBadgeTitle: '此面板修改仅保存在当前会话中，独立生效，不会改变全局默认配置',
      closePanel: '关闭面板',

      statsUsed: '当前已用',
      statsMax: '最大容量',
      statsPercent: '占用比例',
      statsTokens: 'Tokens',
      statsNodeAt: (pct: number) => `节点 ${pct}%`,

      statusHealthy: '上下文状态充裕，拥有充沛的记忆空间保障顺畅交流。',
      statusWarning: '上下文逐渐累积，靠近自动压缩阈值，可随时整理。',
      statusCritical: '上下文负荷较高，已达到自动压缩线，建议立即整理记忆。',

      compressing: '正在提取提炼并压缩记忆…',
      compressAction: '整理历史记忆 · 立即压缩上下文',

      temperatureLabel: '采样温度',
      temperatureSliderAria: '采样温度',
      temperatureScaleMin: '0.00 严谨',
      temperatureScaleMid: '0.70 平衡',
      temperatureScaleMax: '1.00 发散',
      temperatureHint: '低更确定，高更发散。仅对当前会话生效。',

      reasoningLabel: '思考推理深度',
      reasoningFallbackLabel: '关闭',

      scopeNote: '仅对当前会话生效 · 自动保存',
      scopeNoteTitle: '此配置仅对当前会话生效，自动持久化保存',
      resetButtonTitle: '将当前会话的对话参数重置为默认配置',
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
      forbidden: '系统预设对话不能改名',
      failed: '改名失败，已恢复原名称'
    }
  },

  companion: {
    statusBusy: '忙碌中',
    statusCompanion: '陪伴中'
  },

  living: {
    title: '生活空间',
    goToWorkbench: '前往工作台',
    rail: {
      chat: '对话',
      moments: '片刻',
      diary: '日记',
      wardrobe: '衣橱',
      appearance: '形象',
      channels: '通道',
      room: '房间',
      settings: '设置',
      companionFallback: '伙伴',
      avatarMood: (name: string) => `${name} 的表情反馈`
    },
    moments: {
      loading: '正在翻看相册…',
      empty: '还没有留下什么片刻。',
      noTitle: '无题',
      kindLabels: {
        emotion: '心情',
        greeting: '问候',
        milestone: '里程碑',
        scene: '房间',
        together: '在一起',
        user: '随笔'
      } as Record<string, string>,
      kindFallback: '片刻'
    },
    diary: {
      loading: '翻开日记本中…',
      todayBadge: '今日',
      mood: (mood: string) => `心情 · ${mood}`,
      signature: (name: string) => `—— ${name} 的日记`,
      emptyTitle: '这一天还没有日记',
      emptyHintToday: '今日日记将在夜间整理生成，晚点再来翻看吧～',
      emptyHintOther: '这一天没有日记记录哦～',
      weekHeader: ['一', '二', '三', '四', '五', '六', '日'] as ReadonlyArray<string>,
      weekDayNames: ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'] as ReadonlyArray<string>,
      dateFormat: (dateStr: string, weekDay: string) => `${dateStr} · ${weekDay}`
    },
    wardrobe: {
      preview: {
        views: '预览视角',
        stage: '资产包动画预览',
        front: '正面动作',
        left: '左侧贴边',
        right: '右侧贴边',
        action: '播放动作',
        play: '播放',
        pause: '暂停',
        replay: '重新播放',
        loop: '循环预览 · 不改变穿着',
        loading: '正在加载本地或云端资产…',
        failed: '预览加载失败，立绘仍可查看',
        noPose: '这套资产暂未包含扶边姿态',
        retry: '重试加载',
        actions: {
          idle: '呼吸与眨眼',
          wave_left: '左手挥手',
          wave_right: '右手挥手',
          look_away_left: '向左看',
          look_away_right: '向右看',
          turn_body_left: '向左转身',
          turn_body_right: '向右转身',
          petting: '摸头反应'
        }
      },
      empty: '还没有就绪的 2D 形象，生成 2D 动画资产后即可换装。',
      policyLabel: '角色自主换装',
      policyDesc: '开启后，伙伴可在夜间挑选已有外观或为自己设计新装。',
      policyStatusLocked: '已锁定',
      policyStatusUnlocked: '允许换装',
      policyToggleAria: '允许角色自主换装',
      wearing: '穿着中',
      statusLabels: {
        draft: '草稿',
        splitting: '切分中…',
        failed: '切分失败',
        expired: '已过期'
      } as Record<string, string>,
      autoWearAfterSplit: '切分完成后自动穿上',
      actions: {
        continueDesign: '继续设计',
        continueDesignTitle: '继续设计这套草稿',
        wear: '穿着',
        wearTitle: '穿上这套外观',
        retry: '重试',
        delete: '删除',
        deleteTitle: '删除这套外观'
      },
      imageAlt: '外观立绘',
      previewPlaceholderDesigning: '描述想要的着装，生成后在这里预览',
      previewPlaceholderIdle: '选择左侧外观预览资产与动作，或开始设计新外观',
      previewHint: '服装、发型、配饰都可以换；五官与体型保持不变',
      generating: '生成中…',
      startPrompt: '用一段描述（可附参考图）让伙伴换上新装',
      startAction: '开始新设计',
      designIntro:
        '描述想要的着装，例如「水手服换成米白色针织毛衣和棕色长裙，发型改为低马尾」；也可以附一张参考图。每小时最多生成一套。',
      confirmAndWear: '确认入柜并穿上',
      processing: '处理中…',
      discard: '放弃草稿',
      confirmHint: '确认后进行 2D 切分，完成后自动穿上（期间保持当前外观）',
      refImageAttached: '已附参考图',
      refImageAlt: '参考图',
      placeholderRefining: '想微调哪里？继续描述…（Enter 发送）',
      placeholderInitial: '描述想要的着装…（Enter 发送，Shift+Enter 换行）',
      attachImage: '附参考图',
      attachImageTitle: '附参考图（可选，仅首次生成）',
      send: '发送'
    },
    appearance: {
      renderMode: '渲染模式',
      renderModeHint:
        '切到 3D 会先在向导内逐张生成 3D 正面立绘（多视角供应商再补一张背面立绘），每张均需手动点按触发；全部确认后触发云端 3D 模型生成（1~3 分钟），生成期间显示 2D 动画版（或程序化蛋过渡）；生成失败永久保持 2D 动画版；切回 2D 立即生效。',
      mode2d: '2D 动画版',
      mode3d: '3D 立体版',
      mesh2dFailed: '2D 动画资产生成失败',
      mesh2dMissing: '2D 动画资产尚未生成',
      mesh2dRetry: '重新切分',
      companionSize: '形象大小',
      companionSizeHint: '精灵在桌面上的默认显示比例。',
      scaleRange: '0.3×–3× 连续可调，1× 为默认。',
      scaleAria: '形象大小'
    },
    room: {
      intro: '换个心情、回滚到之前的房间、或者锁定房间政策——都在这里。',
      noBackdrop: '暂无生效的房间图',
      currentBadge: '当前房间',
      currentAltFallback: '当前生效房间',
      pendingOverlay: '正在收拾新房间…',
      pendingOverlayHint: '结合伙伴形象与起居意向装点中，稍等片刻即可入住',
      failedOverlay: '房间收拾失败',
      failedOverlayHint: '生成服务暂时繁忙或网络异常，可点击重试',
      retryButton: '重新尝试',
      briefFallback: '温暖舒适的起居空间',
      briefPending: '新房间装点中…',
      subbriefReady: '基于当前形象与生活气息量身设计',
      subbriefPending: '就绪后会自动无缝切入',
      generateButton: '生成新房间',
      generatingButton: '生成中…',
      historyTitle: '回滚历史',
      historySubtitle: '最近 5 张',
      historyEmpty: '还没有可回滚的历史',
      historyAltFallback: '历史房间',
      historyCurrentLabel: '当前',
      historyRollbackLabel: '换回此间',
      historyCurrentAria: '当前正在使用的房间',
      historyRollbackAria: (id: string) => `回滚到历史房间 ${id}`,
      policyTitle: '房间政策',
      policyLabel: '角色自主换房',
      policyDesc: '锁定后角色拒绝自主换房，仅响应换装联动与你的主动请求；解锁后角色可随心情时机自主换房。',
      policyStatusLocked: '已锁定',
      policyStatusUnlocked: '允许换房',
      policyToggleAria: '允许角色自主换房'
    },
    roomBackdrop: {
      failedText: '房间还在收拾…',
      pendingText: '房间收拾中'
    },
    toasts: {
      roomSlow: '房间生成耗时较长，请稍后重试',
      roomReady: '新房间已就绪！',
      roomReadyAlt: '新房间收拾好啦！',
      roomRegenerateFailed: '换个房间失败了…过会儿再试试',
      roomRollbackFailed: '回滚失败…历史房间可能已失效',
      roomRollbackSuccess: '已换回之前的房间',
      roomLockFailed: '锁定设置没改成功',
      roomLocked: '已锁定：拒绝角色自主换房',
      roomUnlocked: '已解锁：角色可以自主换房',
      roomFailedFallback: '房间生成失败了，请稍后重试'
    }
  },

  workbench: {
    title: '工作台',
    stationBadge: '工作工位',
    stationSettingsBadge: '工位设置',
    stationSettingsTooltip: '工位环境配置',
    stationEnvironment: '工位环境',
    backToChat: '返回对话',
    backToChatTooltip: '返回工位对话 (Esc)',
    openLiving: '生活空间',
    openLivingTooltip: '切换到生活空间',
    companionTitle: (brandName: string) => `${brandName} 伴工精灵（按住可拖动整个工作台，轻点互动）`,
    station: {
      tabsAria: '工位设置分区导航',
      tabs: {
        inference: '推理与对话',
        runner: '本机执行器',
        skills: '技能与工具'
      }
    },
    sessionSidebar: {
      title: 'Sessions',
      new: '新建对话',
      searchAria: '搜索会话',
      searchPlaceholder: '搜索会话…',
      searchResultsHeading: '搜索结果',
      searching: '搜索中…',
      noMatch: '没有找到匹配会话',
      badgeArchived: '已归档',
      specialHeading: '专业工作预设',
      loadingSpecial: '加载预设会话中…',
      noSpecial: '暂无专业预设会话',
      regularHeading: '常规对话',
      loadingSessions: '加载会话中…',
      noRegular: '暂无常规对话，点击上方新建',
      specialStationFallback: '专业工位',
      newSessionFallback: '新对话',
      messageCount: (n: number) => `${n} 条消息`,
      sortOptions: {
        recent: '按最近活跃排序',
        created: '按创建时间排序',
        messages: '按消息数排序'
      },
      presetMeta: {
        copywriter: '文案秘书',
        developer: '开发工程师',
        language_teacher: '语言老师',
        product_manager: '产品经理'
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
      expandAria: '展开运行轨迹',
      collapseAria: '折叠运行轨迹',
      collapseTitle: '折叠',
      subtitle: (steps: number) => `${steps} 步 · 本轮`,
      idleSubtitle: '空闲中',
      stepsUnit: '步',
      toolsTitle: '本轮工具',
      toolsSubtitleIdle: '尚未动手',
      toolsSubtitleSteps: (steps: number) => `${steps} 步`,
      preparing: '准备中…',
      toolsEmpty: '这一轮还没动手',
      artifactsTitle: '本会话工件',
      artifactsSubtitleEmpty: '尚无',
      artifactsSubtitleCount: (count: number) => `${count} 项`,
      artifactsEmpty: '这一轮还没有生成图或视频'
    }
  },

  whisper: {
    close: '关闭轻语'
  }
}

export type Dictionary = typeof dict
