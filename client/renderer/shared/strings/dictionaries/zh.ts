export const dict = {
  brand: {
    name: '唤生',
    fullName: '唤生桌面'
  },

  common: {
    videoQueue: {
      waiting: '视频排队中',
      rate_limited: '服务繁忙，等待重试',
      blocked: '等待核对生成结果'
    },
    done: '完成',
    moreActions: '更多操作',
    creationDetails: '制作资料',
    useForDesktopLife: '用于这个桌面背景',
    companionControl: {
      title: '伙伴',
      show: '显示伙伴',
      left: '左侧',
      right: '右侧',
      temporaryEdge: '侧边空间不足，伙伴暂时收起',
      temporaryMaximized: '窗口最大化时伙伴暂时收起',
      saveFailed: '保存伙伴显示设置失败，请重试',
      dragTitle: (name: string) => `拖动${name}可移动整个窗口`
    },
    save: '保存',
    saving: '保存中…',
    cancel: '取消',
    clear: '清除',
    close: '关闭',
    copied: '已复制',
    copyFailed: '复制失败',
    loading: '加载中…',
    maximize: '最大化',
    minimize: '最小化',
    remove: '移除',
    restore: '还原',
    retry: '重试',
    pageRestoreFailed: '页面恢复失败',
    pickDate: '选择日期',
    datePicker: { year: '年', month: '月', day: '日' },
    imagePick: { tooLarge: '图片太大，请换一张较小的图片', readFailed: '无法读取所选图片，请重试' },
    processing: '处理中…'
  },

  selfSource: {
    guidanceAction: '需要制作图片？获取提示词与参考图',
    open: '使用自己的图片',
    openTitle: '使用自己的图片',
    hint: '已有图片可以直接选图采纳，无需生成。需要在外部制作时，展开提示词与参考图；采纳前请核对身份、视觉风格与画面要求。',
    referenceTitle: '参考图（随提示词一起提供）',
    referenceHint: '把参考图和提示词一起交给外部工具；缩略图上可复制或另存，点击查看大图。',
    referenceMissing: '暂时没有可用的参考图。请先完成伙伴形象，再返回这里继续。',
    referenceZoom: '参考图预览',
    refs: {
      avatarSeed: '头像',
      fullbodySeed: '全身形象'
    },
    promptLoading: '正在准备提示词…',
    promptFailed: '提示词获取失败，请重试。',
    retryPrompt: '重新获取提示词',
    copyPrompt: '复制提示词',
    copied: '已复制',
    copyRefImage: '复制图片',
    copiedRefImage: '图片已复制',
    saveRefImage: '另存图片',
    saveRefImageFailed: '另存失败，请重试。',
    copyRefImageFailed: '复制失败，请重试。',
    pickTitle: '选择生成好的图片',
    pickImage: '选择图片',
    replaceImage: '换一张',
    adopt: '使用这张图',
    adopting: '正在保存…',
    adoptFailed: '保存失败，请重试。',
    useAi: '改用 AI 生成'
  },

  generationActions: {
    aiTitle: '调整画面',
    edit: '微调',
    regenerate: '重新生成',
    generate: '生成',
    editHint: '只修改要求的部分，其余保持不变',
    editRequiresFeedback: '微调需先填写要求',
    regenerateHint: '按参考与要求整体重绘',
    groupHint: '微调只修改要求的部分，其余保持不变；重新生成按参考与要求整体重绘。',
    selfTitle: '更多方式',
    selfHint: '上传自己的图片，或查看制作参考。'
  },

  boot: {
    desktopBootFailedWithMessage: (message: string) => `桌面启动失败：${message}`,
    errors: {
      desktopBootFailed: '桌面启动失败',
      bridgeUnavailable: '桌面组件不可用，请重启应用',
      desktopReconnectFailed: '与后端连接已中断，应用正在持续重试。'
    },
    failure: {
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
      methodNotAllowed: (brandFullName: string) =>
        `暂时无法完成操作，请稍后重试。如果持续出现，请检查 ${brandFullName} 是否需要更新。`,
      microphonePermission: '麦克风权限已被拒绝。',
      serviceUnavailable: 'AI 服务暂时无法使用，请稍后重试。已保存的内容仍然保留。'
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
  },

  voice: {
    title: '声音',
    intro: '选择固定音色，或为伙伴设计、克隆一个专属声音。',
    noTtsConfigured: '语音暂时不可用，你仍然可以继续用文字聊天。稍后再来选择声音。',
    noVoicesForLanguage: '暂时没有适合当前语言的声音，你仍然可以继续用文字聊天。',
    loadFailed: '音色列表加载失败',
    sampleLine: (name: string) => (name ? `你好呀，我是${name}。这是我的声音～` : '你好呀，这是我的声音～'),
    genderFilterAria: '声线筛选',
    genderFilters: {
      '': '全部',
      female: '女声',
      male: '男声',
      neutral: '中性'
    },
    preview: '试听',
    use: '使用',
    inUse: '使用中',
    noMatch: '当前筛选无匹配音色。',
    designHeading: '设计专属音色',
    designTab: '设计音色',
    designPlaceholder: '描述你想要的音色…',
    designGenerate: '重新试听',
    designGenerating: '生成中…',
    designFailed: '声音生成失败，请稍后重试。',
    methodAria: '声音来源',
    fixedHeading: '选择音色',
    cloneHeading: '克隆声音',
    useThisVoice: '使用这个声音',
    saving: '正在保存…',
    showRecommended: '只看推荐声音',
    showAll: '查看全部声音',
    designIntro: '让伙伴根据角色资料自主设计声音，也可以自己编写或修改音色描述。',
    autoDesign: '自主设计',
    autoDesignUnavailable: '自主设计暂未配置，可以手动填写音色描述并试听。',
    designUnavailable: '音色设计暂未配置，请选择固定音色或克隆声音。',
    designGuide: '查看描述建议',
    feedbackLabel: '调整意见',
    feedbackPlaceholder: '哪里不满意？例如：声音再柔和一点，保留原来的成熟感…',
    adjustDesign: '按意见调整',
    savedVoice: '已保存的声音',
    savedUnavailable: '这个声音暂时不可用，请检查语音配置或选择其他声音。',
    cloneIntro: '录制或上传清晰、自然的单人说话音频。建议 5–30 秒，最多 60 秒，文件不超过 24 MiB。',
    cloneUnavailable: '声音克隆暂未配置，请选择固定音色或设计音色。',
    startRecording: '开始录音',
    stopRecording: '停止录音',
    rerecord: '重新录制',
    uploadAudio: '上传音频',
    recordingHint: '正在录音，60 秒后会自动停止…',
    uploadingAudio: '正在处理音频…',
    sampleDuration: (seconds: number) => `参考音频 · ${seconds} 秒`,
    previewOriginal: '播放原音',
    clonePreview: '克隆并试听',
    emptyAudio: '音频为空，请重新录制或上传。',
    audioTooLarge: '音频文件不能超过 24 MiB。',
    uploadFailed: '音频上传失败，请重试。',
    micUnavailable: '无法使用麦克风，请检查系统权限或上传音频。',
    previewFailed: '试听播放失败，请重试。',
    onboardingHint: '选择喜欢的声音并确认。之后可以随时在「伙伴 → 声音」中调整。',
    describePreference: '描述想要的声音',
    continueText: '先用文字聊天'
  },
  settings: {
    nav: {
      about: '关于',
      theme: '主题',
      interaction: '交互',
      navAriaLabel: '设置分区导航',
      persona: '角色',
      shortcuts: '快捷键',
      voice: '音色',
      companion: '伙伴'
    },
    companion: {
      heading: '伙伴显示',
      hint: '调整伙伴的显示比例和位置。',
      companionSize: '伙伴大小',
      companionSizeHint: '伙伴形象的默认显示比例。',
      scaleAria: '伙伴大小',
      resetPosition: '伙伴位置归位',
      resetPositionDesc: '将伙伴移回当前屏幕的默认位置。'
    },
    shortcuts: {
      heading: '全局快捷键',
      intro: '在系统任意界面通过全局快捷键唤起或隐藏伙伴。点击按键框即可录制新组合。',
      toggleVisibility: '隐藏 / 显示伙伴',
      toggleVisibilityDesc: '快速显示或隐藏窗口模式的伙伴窗口。',
      openMain: '显示 / 隐藏主窗口',
      openMainDesc: '打开或收起主窗口；桌面模式下打开或置前主对话。',
      pressKeysPrompt: '请按下组合键…',
      pressKeysHint: '按 Esc 取消录制，按 Backspace 或 Delete 清空',
      recordingAria: '正在录制快捷键',
      editAria: '点击修改快捷键',
      cancelHint: 'Esc 取消',
      registeredOk: '已成功注册全局热键',
      registerFailed: '热键注册失败',
      clearAria: '清空快捷键',
      clearTitle: '禁用 / 清空快捷键',
      resetAll: '恢复全部默认',
      resetAria: '恢复默认快捷键',
      resetTitle: (value: string) => `恢复为默认值 (${value})`,
      defaultLabel: '默认',
      empty: '未设置'
    },
    remote: {
      heading: '手机访问',
      intro:
        '用手机扫码打开你的专属页面，随时与伙伴聊天、查看动态，管理伙伴资料、场景与衣柜。电脑在线时还可以调用本机工具。',
      connect: '连接手机',
      refreshCode: '重新生成二维码',
      cancelCode: '取消二维码',
      copyLink: '复制链接',
      copied: '已复制',
      copyFailed: '链接复制失败',
      connectFailed: '无法生成二维码',
      cancelFailed: '取消二维码失败',
      qrPrompt: '用手机相机扫码，在浏览器打开',
      expiry: (seconds: number) =>
        `二维码将在 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} 后失效`,
      expired: '二维码已过期，请重新生成',
      paired: '手机已连接',
      cancelled: '二维码已取消',
      pollFailed: '连接状态暂不可用，请重试',
      devices: '已授权设备',
      devicesIntro: '设备授权有效期为 30 天。撤销后，该设备将立即退出登录。',
      empty: '还没有连接手机',
      refresh: '刷新',
      loadFailed: '设备列表加载失败',
      lastSeen: '最近访问',
      expires: '授权到期',
      revoke: '撤销授权',
      revokeAll: '撤销全部授权',
      revokeTitle: '撤销设备授权？',
      revokeAllTitle: '撤销全部设备授权？',
      revokeDescription: '手机将退出登录，其发起的在途对话和本机调用将停止。已执行的操作不会撤回。',
      revokeFailed: '撤销授权失败'
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
      downloadUpdate: '下载更新',
      retryDownload: '重新下载',
      downloading: (percent: number) => `正在下载更新 ${percent}%`,
      preparing: '正在准备安装…',
      updateDownloaded: (value: number | string) => `v${value} 已就绪，重启后完成安装`,
      restartNow: '立即重启',
      checkError: (value: string) => `检查更新失败：${value}`,
      downloadError: (value: string) => `下载更新失败：${value}`,
      installError: (value: string) => `安装更新失败：${value}`
    },
    runner: {
      title: '执行器配置',
      intro: '配置底层执行器的相关设置。修改后会自动保存，重启执行器后生效。',
      loading: '正在加载执行器配置...',
      failedLoad: '执行器配置加载失败',
      save: '保存配置',
      saveSuccess: '配置已保存',
      saveFailed: '配置保存失败',
      terminal: '终端设置',
      terminalEnvType: '环境类型',
      envLocal: '本机',
      ssh: 'SSH 连接',
      sshHost: '主机地址',
      sshPort: '端口',
      sshUser: '用户名',
      sshPassword: '密码',
      sshKey: '私钥路径',
      security: '安全',
      securityRedactSecrets: '屏蔽敏感信息',
      browser: '浏览器设置',
      browserAllowPrivateUrls: '允许内网访问'
    },
    skills: {
      title: '可用技能',
      intro: '选择伙伴可以使用的能力，修改后立即生效。',
      loading: '正在加载技能…',
      loadError: '无法从磁盘读取技能列表。',
      saveError: '无法保存技能开关。',
      refreshError: '已保存，但当前对话尚未更新技能列表，请再切换一次。',
      emptyTitle: '未安装任何技能',
      emptyDesc: (brandName: string) => `请重新安装 ${brandName} 以恢复内置技能。`,
      hiddenByPlatformTitle: '当前操作系统没有可用技能',
      hiddenByPlatformDesc: (brandName: string) =>
        `本版本 ${brandName} 内置的技能面向其他操作系统。请在支持的操作系统上重新安装 ${brandName} 后再启用。`
    },
    inference: {
      heading: '推理与对话',
      intro:
        '仅配置普通对话的默认参数、思考深度与上下文压缩策略。每个固定对话使用独立场景默认值，并在其会话窗口内修改；设置修改后会自动保存。',
      loading: '加载中…',
      saveFailed: '无法保存推理与对话设置。',
      saved: '推理与对话设置已保存。',
      loadFailed: '推理与对话设置加载失败',
      agentDefaults: {
        heading: '智能体默认',
        intro: '适用于未单独设置参数的普通对话；不影响任何固定对话。',
        reasoningEffort: '推理深度',
        reasoningEffortDesc:
          '模型每轮推理的强度。「关闭」不推理，从「极简」到「终极」逐级加深；超出供应商上限时自动使用其最高支持档。',
        backgroundReview: '后台记忆整理',
        backgroundReviewDesc: '异步从普通对话历史中抽取记忆，不控制固定对话的记忆整理。',
        reasoningOptions: {
          none: '关闭',
          minimal: '极简',
          low: '低',
          medium: '中',
          high: '高',
          xhigh: '超高',
          max: '极限',
          ultra: '终极'
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
        chatTemperatureDesc: '普通对话的默认生成温度。较低的值更严谨稳定，较高的值更发散有创意。',
        titleTemperature: '标题生成温度',
        titleTemperatureDesc: '根据首轮对话自动生成会话标题的温度。建议保持较低以保证概括准确性。',
        compressionTemperature: '上下文压缩温度',
        compressionTemperatureDesc: '对话历史超长时生成对话摘要的温度。建议保持 0 以保证事实忠实度。'
      }
    },
    interaction: {
      title: '陪伴方式',
      intro: '选择伙伴什么时候主动找你，以及平时可以做些什么。',
      moreOptions: '更多陪伴选项',
      voiceHeading: '回应与录音',
      responsePreference: '回应偏好',
      responsePreferenceText: '偏好文字',
      responsePreferenceVoice: '偏好语音',
      responsePreferenceDesc: '告诉伙伴你通常更喜欢文字还是语音。伙伴会结合情景选择。',
      recording: '录音时长上限',
      recordingDesc: '单条语音录音的最大时长，到达上限后自动停止录制并发送。',
      recordingSecondsSuffix: '秒',
      recordingSaveFailed: '保存录音时长失败',
      recordingLoadFailed: '录音时长设置加载失败，请稍后重试',
      tierHeading: '你喜欢怎样的陪伴',
      tierHint: '只约束伙伴的主动行为，你发起的交互不受限。',
      tierAriaLabel: '打扰档位',
      tiers: {
        still: { label: '只回应我', hint: '等你来聊天，平时安静陪着你' },
        normal: { label: '偶尔主动问候', hint: '用文字轻轻打个招呼，保持在原处' },
        autonomous: { label: '自由活动', hint: '可以主动说话、走动，与你自然互动' }
      },
      smartHeading: '伙伴的日常',
      smartHint: '允许伙伴记录生活，发现你们感兴趣的新内容。',
      idleAffect: '闲暇时的表情和动作',
      idleAffectAria: '空闲情境情绪',
      idleAffectDesc: '选择自由活动时，让窗口旁的伙伴在长时间空闲后自然活动。',
      autonomy: '自己选择走动的位置',
      autonomyAria: '自主空间决策',
      autonomyDesc: '选择自由活动时，让窗口旁的伙伴结合当前情况选择停留和走动的位置。',
      postsEnabled: '动态创作',
      postsEnabledDesc: '允许伙伴全天自主创作并发布文字、图片、视频或语音动态，离线时也可发布；所有类型共用发布限额',
      informationDiscovery: '关注新资讯',
      informationDiscoveryDesc: '允许伙伴夜间关注你们感兴趣的新内容，在合适的时候分享。'
    },
    persona: {
      characterCard: {
        edit: '编辑',
        title: '角色卡',
        description: '从已确认的头像和全身图整理辅助描述，外形始终以图片为准。服装、发型、发色、妆容和配饰由衣柜管理。',
        portrait: '头像',
        body: '全身形象',
        portraitFeatures: '面部与头部',
        bodyFeatures: '身体特征',
        restore: '恢复自动值',
        edited: '已修改',
        unknown: '未提取或不适用',
        analyzing: '正在从头像和全身图整理固定外形，无需逐项确认。',
        failed: '分析失败。已确认的图片和已生效的角色资料仍保留，可重试。',
        retry: '重试分析',
        extract: '重新提取',
        reload: '重新加载',
        saved: '已保存辅助描述。后续生成仍以已确认图片保持外形。',
        conflict: '角色卡已在其他位置更新，你的修改已保留。请检查后合并保存。',
        merge: '保留修改并使用最新版本',
        loadFailed: '角色卡加载失败，请重试',
        operationFailed: '操作失败，当前修改已保留',
        fields: {
          head_shape: '头部轮廓',
          facial_features: '五官细节',
          facial_surface: '面部表面特征',
          head_identifiers: '头部辨识特征',
          body_shape: '整体体型',
          proportions: '身体比例',
          limbs_and_appendages: '肢体与固有附属结构',
          body_surface: '身体材质与标记'
        }
      },
      title: '伙伴资料',
      intro: '查看名字、性格和相处方式。编辑后保存修改，或取消本次编辑。',
      sectionTitle: '角色',
      sectionMemory: '长期记忆',
      mediaReviewTitle: '待确认的动作视频',
      mediaReviewLoadError: '待确认动作加载失败，请刷新重试',
      mediaReviewEmpty: '当前没有待确认的动作',
      mediaReviewRefresh: '刷新',
      fullbodyReference: {
        title: '伙伴的全身形象',
        hint: 'AI 会根据刚才确认的头像准备全身形象。满意后继续选择声音。',
        confirmHint: '确认后自动整理角色卡，再准备默认视频与初始房间场景；你可以继续完成引导。',
        candidateHint:
          '这是待采纳的新全身形象。身材可以改变；采纳后才会作为后续生成的身体依据。旧视频会继续播放，直到你制作好新视频形象。',
        acceptCandidate: '采纳新全身形象',
        retryCandidateAnalysis: '重试身体特征分析',
        acceptCandidateFailed: '采纳失败，请重试',
        retryCandidateAnalysisFailed: '分析失败，请重试',
        adoptHint: '采纳后直接确认这张全身形象，并在后台整理角色卡、准备默认视频与初始房间场景。',
        adoptPreviewFailed: '图片已保存，但尚未取得对应预览。请重新加载后确认。',
        empty: '还没有全身形象，可以根据当前头像与角色设定生成。',
        loading: '正在准备全身形象，请稍候…',
        referenceLabel: '用户参考图',
        refLabel: '参考图（可选）',
        refHint:
          '可以附加一张参考图，让生成结果更符合预期；用于补充体型、服装和姿态线索，不会直接成为本步结果。面容与物种以已确认头像为准。',
        chooseReference: '选择参考图',
        replaceReference: '更换参考图',
        removeReference: '移除参考图',
        pickError: '图片选择失败，请尝试较小的 PNG、JPEG、WebP 或 GIF 图片。',
        descriptionLabel: '全身描述（可选）',
        descriptionPlaceholder: '比如：修长体型、机械手臂、深蓝长袍、自然站立…',
        descriptionHint: '面容与物种以已确认头像为准；描述只用于本次生成。',
        feedbackLabel: '想怎么调整？（可留空）',
        feedbackPlaceholder: '希望怎样展现体态或姿势？可留空直接生成。',
        generate: '生成全身形象',
        regenerate: '重新生成全身形象',
        editDisabledByReference: '附参考图时不可微调，请先移除参考图',
        reload: '重新加载',
        enlarge: '放大查看全身形象',
        historyTitle: '历史形象',
        historyVersion: '版本',
        historyCurrent: '当前',
        historyCurrentUnavailable: '当前预览暂不可用，可以从下方选择历史版本。',
        historySelectedHint: '已选中较早的版本。确认会先恢复这张图；要继续调整，请先将它设为当前版本。',
        restoreVersion: '设为当前版本',
        restoreFailed: '恢复此版本失败，请重试。',
        back: '上一步',
        continue: '确认并继续',
        errors: {
          load: '全身形象读取失败，请重新加载。',
          generate: '生成未完成，已保留原图。请先重新加载查看结果，再决定是否重试生成。',
          preview: '图片已保存，但预览加载失败，请重新加载。'
        }
      },
      editAction: '编辑',
      editHeading: '编辑角色',
      defaultName: '伙伴',
      noPersonality: '还没设定性格',
      nameLabel: '名字',
      namePlaceholder: '给伙伴起个名字',
      relationshipLabel: '角色定位',
      relationshipPlaceholder: '或者自由描述…',
      personalityLabel: '性格',
      personalityPlaceholder: '自由描述…',
      detailSeparator: '：',
      speakingStyleLabel: '说话风格',
      speakingStylePlaceholder: '描述想要的语气和表达习惯',
      hintEmptyName: '请填写名字',
      hintEmptySpeakingStyle: '请填写说话风格',
      hintSaved: '已保存',
      hintSaveFailed: '保存失败，稍后再试'
    },
    theme: {
      heading: '主题',
      hint: '主题应用到主窗口、轻语和桌面模式的界面，伙伴形象不受影响。',
      themesHeading: '主题方案',
      themesAriaLabel: '主题方案',
      activeBadge: '生效中',
      themes: {
        night: { label: '夜色', description: '深邃沉静，适合夜间使用' },
        day: { label: '日色', description: '温暖明亮，充满活力' },
        'night-clear': { label: '夜色透明', description: '暗底清透，场景从窗壳透出' },
        'day-clear': { label: '日色透明', description: '亮底清透，场景从窗壳透出' }
      },
      materialHeading: '材质效果说明',
      materialTransparent: '清透模式（透明）',
      materialTransparentDesc: '启用液态玻璃与磨砂透视，场景背景透出流动光影，窗口模式中的伙伴形象保持通透。',
      materialTransparentDayNight: '日色透明 / 夜色透明',
      materialSolid: '实底模式（经典）',
      materialSolidDesc: '实体不透明表面，石墨与暖纸底板，具有极高对比度，适合复杂桌面背景环境。',
      materialSolidDayNight: '日色 / 夜色',
      reduceTransparency: '减少透明效果',
      reduceTransparencyDesc: '关闭玻璃模糊并加实底板，降低合成开销；窗口外侧真实透明不受影响。'
    },
    memory: {
      tabAriaLabel: '记忆类型',
      presetLabel: '查看谁记住的事情',
      tabActive: (count: string | number) => `正在使用 · ${count}`,
      tabCandidate: (count: string | number) => `待整理 · ${count}`,
      tabInvalidated: (count: string | number) => `已停用 · ${count}`,
      tabExpired: (count: string | number) => `已过期 · ${count}`,
      profile: {
        title: '我的资料',
        hint: '引导时收集的个人资料，伙伴会一直记得。可随时修改或补填；删除后伙伴不再记得该条，之后仍可重新填写。',
        fields: {
          user_call_name: '称呼',
          user_gender: '性别',
          user_birthday: '生日',
          user_hobbies: '爱好',
          user_freeform: '补充'
        }
      },
      maintenanceHint: '伙伴会自行审阅证据和维护记忆，无需你确认。只有有效且未过期的记忆用于互动。',
      basis: { explicit: '明确表达', inferred: '推断', observed: '事件观察', system: '系统记录' },
      usage: { contextual: '按需使用', background: '长期背景' },
      stance: { supports: '支持依据', opposes: '相反依据' },
      evidence: '详情与依据',
      expires: '有效期至',
      updated: '更新',
      loading: '加载中…',
      loadFailedHint: '加载失败',
      loadFailedToast: '加载长期记忆失败',
      saveFailedHint: '保存失败，请重试',
      saveFailedToast: '保存记忆失败',
      deleteFailedHint: '未能遗忘，请重试',
      deleteFailedToast: '删除记忆失败',
      charCount: (count: number) => `${count} 字`,
      empty: '这里还没有记忆。没有值得长期保留的信息时，伙伴不会写入。',
      saved: '已保存',
      saving: '保存中…',
      delete: '删除'
    }
  },

  skills: {
    tabSkills: '技能',
    tabToolsets: '工具集',
    all: '全部',
    searchSkills: '搜索技能…',
    searchToolsets: '搜索工具集…',
    loading: '正在加载能力…',
    noSkillsTitle: '未找到技能',
    noSkillsDesc: '尝试更宽泛的搜索或其他分类。',
    loadFailedTitle: '技能列表加载失败',
    loadFailedDesc: '请稍后重试；如仍失败，请重新安装应用以恢复内置技能。',
    noToolsetsTitle: '未找到工具集',
    noToolsetsDesc: '尝试更宽泛的搜索词。',
    noDescription: '暂无描述。',
    toolsetsEnabled: (enabled: number, total: number) => `已启用 ${enabled}/${total} 个工具集`,
    toolsetsLoadFailed: '工具集加载失败',
    toolsetsLoadFailedDesc: '请稍后重试。',
    toolsetsSaveFailed: '无法保存工具集开关'
  },

  toolsets: {
    browser_automation: { label: '操作浏览器', description: '打开网页、查找内容，协助完成网页上的操作。' },
    file_operations: { label: '文件操作', description: '读写、补丁、目录与文件搜索。' },
    terminal: { label: '终端', description: '在本机或 SSH 远程主机上执行命令行。' },
    code_execution: { label: '代码执行', description: '沙箱 Python 执行与受限调用。' },
    process_management: { label: '进程管理', description: '后台进程的启动与跟踪。' },
    skills_system: { label: '技能系统', description: '列出、查看与管理 Skill 内容。' },
    memory: { label: '记忆', description: '长期记忆的写入、检索与删除。' },
    web_tools: { label: '联网工具', description: '网络搜索与网页内容抽取。' },
    image_generation: { label: '图片生成', description: '通过云端模型生成图片。' },
    messaging: { label: '消息', description: '通过 Webhook 发送消息。' },
    scheduled_tasks: { label: '定时任务', description: 'Cron 触发与周期调度。' },
    agent_delegation: { label: '子代理委托', description: '派生子会话与子代理。' },
    computer_use: { label: '桌面操控', description: '截屏并用鼠标、键盘操作桌面应用。' },
    media_analysis: { label: '多媒体分析', description: '图片分析。' },
    system_awareness: { label: '系统感知', description: '空闲时长、锁屏、焦点窗口、全屏与屏幕坐标等系统状态探测。' }
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
  },

  chat: {
    replaceReply: '换一个回答',
    replacingReply: '正在换一个回答…',
    topic: {
      platforms: { x: 'X', xiaohongshu: '小红书', douyin: '抖音' },
      open: '查看原文',
      unavailable: '原始链接不可用',
      like: '喜欢这类',
      less: '少推荐这类',
      undo: '再次点击撤销反馈',
      failed: '反馈暂不可用，点击重试'
    },
    defaultSessionTitle: '日常对话',
    inputPlaceholder: '跟它说，或把文件拖过来',
    typing: '正在输入...',
    openMainSessionFailed: '无法打开日常对话',

    filesReceived: (count: number) => `收到 ${count} 个文件`,
    attachmentsAdded: (count: number) => `添加了 ${count} 个附件`,
    filesHandoffFailed: '文件转交失败，请重新拖入',
    connectionInterrupted: '连接中断，重连后同步回复。',
    sendFailed: '发送失败',

    media: {
      generating: '视频生成中…',
      generationFailed: '生成失败',
      resultUnknown: '生成结果尚未确认',
      imageLoading: '图片加载中…',
      loadFailed: '媒体加载失败',
      reviewHint: '请预览并确认是否采纳。此素材目前仅供预览。',
      reviewAccept: '确认并采纳',
      reviewReject: '不采纳',
      reviewRejected: '此媒体未被采纳。',
      reviewLoadError: '核对状态读取失败，请稍后重试',
      reviewAcceptError: '采纳失败，请稍后重试'
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
      uploadInterrupted: '上传已中断，请重试。',
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
      retry: '语音暂不可用，点击重试',
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
      notRecognized: '没有识别到语音，请重试或改用文字输入',
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
      completed: (count: number) => `它做了 ${count} 步`,
      genericName: '工具'
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
      contextUnknown: '未配置',
      reasoningOff: '已关闭',

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

      cacheUsageTitle: '最近完成回合 · 缓存命中率',
      cacheUsagePending: '暂无本次运行的回合统计',
      cacheUsageUnavailable: '供应商未提供',
      cacheUsagePartial: '部分数据',
      cacheUsageTokens: (cached: string, input: string) => `命中 / 输入：${cached} / ${input} Tokens`,
      cacheUsageRequests: (reported: number, total: number) => `已报告请求：${reported} / ${total}`,

      statusHealthy: '上下文空间充裕，对话可以顺畅进行。',
      statusWarning: '上下文逐渐累积，靠近自动压缩阈值，可随时整理。',
      statusCritical: '上下文负荷较高，已达到自动压缩线，建议立即压缩。',

      compressing: '正在压缩较早的对话…',
      compressAction: '立即压缩上下文',

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
      forbidden: '固定对话不能改名',
      failed: '改名失败，已恢复原名称'
    }
  },

  companion: {
    menu: {
      quietOff: (minutes: number) => `结束安静（剩 ${minutes} 分钟）`,
      quietOn: (minutes: number) => `安静 ${minutes} 分钟`,
      resetPosition: '一键归位',
      hide: '隐藏角色',
      activate: '激活 / 登录'
    },
    egg: {
      preparing: '正在准备伙伴动作…',
      generating: '伙伴动作生成中…',
      failed: '动作生成失败',
      unavailable: '伙伴动作尚未就绪',
      retry: '重试',
      openWardrobe: '打开衣柜',
      ariaLabel: '伙伴的蛋'
    }
  },

  living: {
    rail: {
      chat: '聊天',
      posts: '动态',
      diary: '日记',
      unread: '有未读信息',
      wardrobe: '衣柜',
      remote: '远程',
      scene: '场景',
      settings: '设置',
      companionFallback: '伙伴',
      avatarMood: (name: string) => `${name} 的表情反馈`
    },
    posts: {
      intro: '查看伙伴分享的文字、图片、视频和语音，也可以留下评论。',
      expand: '展开全文',
      collapse: '收起全文',
      videoRecoveryHeading: '待核对的视频动态',
      videoRecoveryHint:
        '这些动态的原视频结果或身份尚待确认，系统没有重新制作或发布。查看原视频后可决定采纳或放弃；采纳不会补做旁白。',
      videoIdentityReviewPending: '身份待确认，未自动发布',
      videoRecoveryLoadFailed: '待核对视频加载失败，请刷新动态页。',
      videoRecoveryStatuses: {
        pending: '原视频任务尚未完成',
        ready: '已有视频成品，请先查看再采纳',
        failed: '原视频任务已失败',
        unknown: '原视频结果尚未确认',
        discarded: '已放弃'
      },
      videoQuery: '查询原任务',
      videoRefresh: '刷新',
      videoAdopt: '采纳并发布',
      videoDiscard: '放弃',
      videoCleanupPending: '已放弃本次发布，部分本地素材尚待清理。',
      videoRetryCleanup: '重试清理',
      videoQueryFailed: '暂时无法查询原视频任务，请稍后再试。',
      videoAdoptFailed: '暂时无法采纳，请检查当前形象、动态设置和发布额度。',
      videoDiscardFailed: '放弃未完成，请稍后重试。',
      videoDiscardTitle: '放弃这次视频动态？',
      videoDiscardDescription:
        '本次动态不会再发布；系统会清理它独占的本地视频。供应商任务可能仍在运行，已支付的费用不会因此退还。',
      loadMore: '加载更多',
      replyPending: '正在回复…',
      replyRetry: '回复失败 · 重试',
      replyRetryFailed: '重试未成功，请稍后再试',

      loading: '正在翻看相册…',
      empty: '还没有留下什么动态。',
      loadFailed: '动态加载失败',
      noTitle: '无题',
      commentPlaceholder: '写评论…',
      commentSend: '发送',
      commentSending: '发送中…',
      commentDelete: '删除',
      commentFailed: '评论发送失败',
      commentDeleteFailed: '评论删除失败',
      userLabel: '我',
      contentTypeLabels: { text: '文字', image: '图片', video: '视频', audio: '语音' } as Record<string, string>,
      contentTypeFallback: '动态'
    },
    diary: {
      intro: '按日期翻阅伙伴留下的记录与感受。',
      previousMonth: '上个月',
      nextMonth: '下个月',
      loading: '翻开日记本中…',
      todayBadge: '今日',
      signature: (name: string) => `—— ${name} 的日记`,
      emptyTitle: '这一天还没有日记',
      emptyHintToday: '伙伴会在一天结束后决定是否留下日记。',
      emptyHintOther: '这一天没有日记记录哦～',
      loadFailed: '日记加载失败',
      weekHeader: ['一', '二', '三', '四', '五', '六', '日'] as ReadonlyArray<string>,
      weekDayNames: ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'] as ReadonlyArray<string>,
      dateFormat: (dateStr: string, weekDay: string) => `${dateStr} · ${weekDay}`
    },
    outfit: {
      empty: '还没有着装，先设计第一套吧。',
      wardrobeHeading: '着装',
      outfitCount: (count: number) => `${count} 套`,
      zoomOutfit: (name: string) => `查看${name}大图`,
      policyLabel: '允许伙伴自主换装',
      policyDesc: '开启后，伙伴可在夜间挑选已有着装或为自己设计新装。',
      policyStatusLocked: '已关闭',
      policyStatusUnlocked: '已开启',
      policyToggleAria: '允许伙伴自主换装',
      policyFailed: '换装设置保存失败',
      deleteFailed: '删除着装失败',
      confirmFailed: '确认着装失败，请重试',
      retryDescription: '补全描述',
      descriptionRetryFailed: '外观描述补全失败，请稍后重试',
      wearing: '穿着中',
      statusLabels: {
        draft: '草稿',
        failed: '确认失败',
        expired: '已过期'
      } as Record<string, string>,
      actions: {
        continueDesign: '继续设计',
        continueDesignTitle: '微调重绘这套着装',
        retry: '重试',
        delete: '删除',
        deleteTitle: '删除这套着装'
      },
      imageAlt: '着装立绘',
      previewPlaceholderDesigning: '描述想要的着装，生成后在这里预览',
      previewHint: '服装、发型、配饰都可以换；五官与体型保持不变',
      generating: '生成中…',
      startAction: '设计新着装',
      designIntro: '描述想要的着装，例如「水手服换成米白色针织毛衣和棕色长裙，发型改为低马尾」；也可以附一张参考图。',
      confirmAction: '确认入柜',
      processing: '处理中…',
      discard: '稍后继续',
      confirmHint: '确认入柜后自动准备待机和拖拽动作，就绪后可在动作页选择穿上。',
      refImageAttached: '已附参考图',
      refImageAlt: '参考图',
      zoomRefImage: '查看参考大图',
      retryLast: '重试上次生成',
      placeholderRefining: '想调整哪里？继续描述…（Enter 发送）',
      placeholderInitial: '描述想要的着装…（Enter 发送，Shift+Enter 换行）',
      attachImage: '附参考图',
      attachImageTitle: '附参考图（可选，仅首次生成）',
      send: '开始制作',
      design: {
        byReference: '（按参考图设计）',
        pickReferenceTitle: '选择服装参考图',
        drafted: '草稿已生成，见上方预览。继续描述可以微调重绘，满意就确认入柜。',
        refined: '已按反馈微调，见上方预览。继续描述可以再微调，满意就确认入柜。',
        previewFailed: '草稿已生成，但预览加载失败，请到衣柜页重新打开草稿。',
        timedOut: '等待结果超时，生成可能仍在继续。请稍后在衣柜页查看草稿，确认结果后再重试。',
        generateFailed: '着装生成失败，请稍后重试',
        resumeReady: '继续微调这套草稿，或直接确认入柜。',
        resumePreviewMissing: '草稿预览尚未加载，可继续描述微调，或稍后到衣柜页重新打开。',
        promptRequiresInput: '请先描述想要的着装，或在设计区附上一张参考图，再获取提示词。'
      }
    },
    appearance: {
      videoReady: (clips: number) => `动作已就绪（包含 ${clips} 个动作）`,
      actionsHeading: '动作',
      actionCount: (count: number) => `${count} 个动作`,
      videoActionReady: '已就绪',
      videoActionGenerating: '生成中',
      videoActionFailed: '生成失败',
      videoActionResultUnknown: '结果待核对',
      videoActionResultUnknownHint: '提交结果待核对，请先核对生成任务，结果明确前无法重新生成此动作。',
      videoActionReview: '待确认',
      videoActionReviewHint: '请预览待确认动作，再决定是否采纳。',
      videoActionCurrent: '已采纳动作',
      videoActionPending: '待确认动作',
      videoActionPendingHint: '采纳后替换当前动作；不采纳则保留原动作。',
      videoActionCompactHint: '选择一项查看',
      videoActionOnDemandShort: '按需',
      videoActionSearch: '搜索动作',
      videoActionNoMatch: '没有匹配的动作',
      videoActionEmpty: '选择一个动作查看预览与操作。',
      videoActionCancel: '取消',
      videoOutfitNotReady: '这套着装的基础动作尚未就绪。',
      videoPacksLoadFailed: '动作包加载失败',
      videoSelectOutfit: '返回衣柜并选择一套着装，查看它专属的动作。',
      videoBackToOutfits: '返回',
      videoLoading: '正在读取这套着装的动作…',
      videoIdle: '待机',
      videoWalkLeft: '向左行走',
      videoWalkRight: '向右行走',
      videoDrag: '悬空拖拽',
      videoPeekLeft: '向左探身',
      videoPeekRight: '向右探身',
      videoPeekCalibrationFailed: '定位未通过',
      videoPeekCalibrationFailedHint: '素材已生成，但探身定位未通过检查。可重新生成后再试。',
      videoResume: '继续处理已有素材',
      videoActivate: '穿这套',
      videoIdentityReviewHint: '新动作包的角色外形需要你核对；当前形象保持可用。',
      videoIdentityReviewActivate: '确认形象并穿上',
      videoActive: '使用中',
      videoActionOnDemand: '系统动作在需要时由服务补齐；也可在此手动生成。',
      videoGenMissingAction: '生成这个动作',
      videoRedoAction: '重新生成这个动作',
      videoFeedback: '希望怎样调整姿态或动作（可选）',
      videoGenAction: '准备基础动作',
      videoRegenAction: '重新制作基础动作',
      videoRegenTitle: '重新生成动作包',
      videoRegenBody: '将按当前着装参考重新生成待机视频和拖拽图片；当前穿着保持不变，就绪后可选择穿上。',
      videoGenStagePose: '正在准备动作姿态…',
      videoGenStageScript: '正在按角色性格设计动作姿态与演绎…',
      videoGenStageSubmit: '正在提交生成任务…',
      videoGenStageGenerate: '动作素材生成中，通常需要几分钟…',
      videoGenStageDownload: '正在下载生成结果…',
      videoGenStageProcess: '正在处理透明素材并检查质量…',
      videoGenStagePublish: '正在发布动作包…',
      videoGenStageDefault: '动作包生成中…',
      videoGenBusy: '所请求的动作仍在生成或结果待核对，请等待结果明确后再试。',
      videoPackHasReadyActions: '这套着装已有就绪动作，请按需重新生成单个动作。',
      videoGenRequestFailed: '动作包生成请求失败，请稍后重试',
      videoActivateFailed: '穿上这套着装失败，请稍后重试',
      videoGenHint:
        '确认入柜后会自动准备待机视频和拖拽图片，其他动作按需制作。当前穿着保持不变，基础动作就绪后可选择穿上。'
    },
    scene: {
      intro: '描述一个喜欢的地方，AI 会把它变成聊天窗口的背景。满意后再使用。',
      referenceHint: '参考图提供环境与构图线索，伙伴自身形象不会在场景图中重复出现。',
      referenceLabel: '场景参考图',
      chooseReference: '选择参考图',
      replaceReference: '更换参考图',
      removeReference: '移除参考图',
      referenceError: '图片无法读取，请选择有效的 PNG / JPEG / WebP / GIF 图片。',
      notesLabel: '环境描述',
      notesPlaceholder: '例如：傍晚的海边，柔和霞光映在沙滩与海面上。',
      targetSize: (width: number, height: number) => `场景尺寸：${width} × ${height}`,
      regenerationTargetSize: (width: number, height: number) => `重新生成将使用：${width} × ${height}`,
      generationTargetSize: (width: number, height: number) => `生成目标：${width} × ${height}`,
      sourceSize: (width: number, height: number) => `原图尺寸：${width} × ${height}`,
      imageSize: (width: number, height: number) => `图片尺寸：${width} × ${height}`,
      uploadHint: '建议选用不重复包含伙伴的背景图。上传源文件保留，展示图按场景尺寸等比裁切。',
      noScene: '尚未启用场景',
      pendingOverlay: '场景正在准备…',
      pendingOverlayHint: '图片保存并完成描述后进入场景库，当前背景保持不变。',
      waitingUploadOverlay: '等待上传场景图片',
      waitingUploadHint: '上传后保留源文件并适配场景尺寸，先保存到场景库，再由你选择启用。',
      waitingUploadAction: '选择图片上传',
      generateButton: '创建背景',
      generatingButton: '创建中…',
      emptyLibrary: '场景库还是空的，创建或上传一个场景开始布置生活空间。',
      emptySearch: '没有找到匹配的场景。',
      loading: '正在读取场景库…',
      loadFailed: '场景库加载失败，请重试。',
      detailLoadFailed: '场景信息暂时无法读取，可能已被删除。',
      openDetails: '查看场景详情',
      backToLibrary: '返回',
      newSceneTitle: '创建背景',
      searchButton: '搜索',
      imageRegenerate: '重新生成图片',
      saveAndRegenerate: '保存并重新生成',
      imageRegenerating: '正在重新生成图片；当前场景仍可使用。',
      imageRegenerationFailed: '重新生成失败，原图片仍然保留。',
      historyAltFallback: '场景图片',
      historyCurrentLabel: '当前',
      historyRollbackLabel: '使用此背景',
      viewOriginal: '放大查看',
      historyDeleteLabel: '删除',
      historyDeleteConfirmTitle: '删除这个场景？',
      historyDeleteConfirmDescription: '图片及场景信息会从场景库中删除，此操作不可恢复。',
      policyLabel: '允许伙伴自主更换背景',
      policyDesc: '开启后，伙伴可以创建新背景或切换已有背景；夜间也会遵守你的夜间活动设置。',
      policyStatusLocked: '已关闭',
      policyStatusUnlocked: '已开启',
      policyToggleAria: '允许伙伴自主新增与切换',
      savedHint: '创建完成后先保存，再手动启用。',
      slow: '仍在生成，完成后会自动更新。',
      imageLoadFailed: '场景图片暂时无法显示，请重试加载。',
      retryImage: '重新加载图片',
      cancelTask: '取消任务',
      search: '搜索标题或描述',
      titleLabel: '简短标题',
      descriptionLabel: '环境描述',
      save: '保存',
      cancel: '取消',
      edit: '编辑信息',
      retryAnalysis: '重试图片与描述检查',
      retryGeneration: '重试生成',
      previous: '上一页',
      next: '下一页',
      untitled: '待命名场景',
      needsDescription: '图片与描述齐全后才能启用。',
      statuses: {
        pending: '准备中',
        ready: '已保存',
        description_failed: '描述待补全',
        failed: '创建失败',
        cancelled: '已取消'
      }
    },
    sceneBackdrop: {
      pendingText: '场景准备中'
    },
    toasts: {
      sceneReady: '已保存到场景库',
      sceneImageRegenerated: '场景图片已更新',
      sceneRegenerateFailed: '场景操作失败，请查看原因后重试',
      sceneStatusRefreshFailed: '场景状态读取失败，请稍后重试',
      sceneRollbackSuccess: '已切换场景',
      sceneDeleteFailed: '删除失败…请稍后重试',
      sceneDeleteSuccess: '已删除场景',
      sceneLockFailed: '锁定设置没改成功',
      sceneLocked: '已锁定自主场景变化',
      sceneUnlocked: '已允许自主场景变化'
    }
  },

  // 系统预设的界面名称与说明。后端目录（backend/services/domains/conversation/presets.py）只有中文；中文须与目录一致并同步修改，未知预设由调用方回落目录值。
  presets: {
    names: {
      companion: '陪伴',
      copywriter: '文案秘书',
      language_teacher: '语言老师'
    },
    descriptions: {
      copywriter: '起草、润色与整理文案、邮件和会议记录，保留原意，贴合受众与场合。',
      language_teacher: '按水平与目标讲解、纠错和练习，提供忠实自然的翻译。'
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
      fixedHeading: '固定对话',
      specialHeading: '日常辅助',
      loadingSpecial: '加载预设会话中…',
      noSpecial: '暂无日常辅助会话',
      regularHeading: '常规对话',
      loadingSessions: '加载会话中…',
      noRegular: '暂无常规对话，点击上方新建',
      specialStationFallback: '日常辅助',
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
      expandAria: '展开运行轨迹',
      collapseAria: '折叠运行轨迹',
      collapseTitle: '折叠',
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
      artifactsEmpty: '这一轮还没有生成图或视频'
    }
  },

  whisper: {
    close: '关闭轻语'
  }
}

export type Dictionary = typeof dict
