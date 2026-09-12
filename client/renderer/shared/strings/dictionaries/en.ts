import type { Dictionary } from './zh'

export const dict: Dictionary = {
  brand: {
    name: 'SpiritAgent',
    fullName: 'SpiritAgent Desktop',
    copyright: 'Copyright © 2026 SpiritAgent',
    slogan: [
      'Bring the companion you imagine to your desktop.',
      'A personal AI companion that keeps you company and gets things done.'
    ]
  },

  common: {
    apply: 'Apply',
    back: 'Back',
    save: 'Save',
    saving: 'Saving…',
    cancel: 'Cancel',
    change: 'Change',
    choose: 'Choose',
    clear: 'Clear',
    close: 'Close',
    confirm: 'Confirm',
    connect: 'Connect',
    connecting: 'Connecting',
    continue: 'Continue',
    copied: 'Copied',
    copy: 'Copy',
    copyFailed: 'Copy failed',
    delete: 'Delete',
    done: 'Done',
    error: 'Error',
    failed: 'Failed',
    free: 'Free',
    loading: 'Loading…',
    notSet: 'Not set',
    refresh: 'Refresh',
    remove: 'Remove',
    replace: 'Replace',
    retry: 'Retry',
    run: 'Run',
    send: 'Send',
    set: 'Set',
    skip: 'Skip',
    update: 'Update',
    on: 'On',
    off: 'Off'
  },

  boot: {
    ready: (brandFullName: string) => `${brandFullName} is ready`,
    desktopBootFailedWithMessage: (message: string) => `Desktop failed to start: ${message}`,
    steps: {
      connectingGateway: 'Connecting to the desktop gateway',
      startingDesktopConnection: 'Starting the desktop connection',
      startingSpiritAgentDesktop: (brandFullName: string) => `Starting ${brandFullName}…`
    },
    errors: {
      desktopBootFailed: 'Desktop failed to start',
      desktopReconnectFailed: 'Lost connection to the backend. The app is retrying in the background.'
    },
    failure: {
      title: (brandName: string) => `${brandName} cannot start`,
      description:
        'The backend gateway is not running. Try the recovery steps below — your conversations and settings will not be deleted.',
      retry: 'Retry'
    }
  },

  notifications: {
    region: 'Notifications',
    hide: 'Hide',
    show: 'Show',
    more: (count: number) => `${count} more notifications`,
    clearAll: 'Clear all',
    dismiss: 'Dismiss notification',
    details: 'Details',
    copyDetail: 'Copy details',
    errors: {
      elevenLabsNeedsKey: 'ElevenLabs STT requires ELEVENLABS_API_KEY.',
      elevenLabsRejectedKey: 'ElevenLabs rejected the API key (401).',
      methodNotAllowed: (brandFullName: string) =>
        `The desktop backend rejected this request (405 Method Not Allowed). Please try restarting ${brandFullName}.`,
      microphonePermission: 'Microphone permission was denied.',
      openaiRejectedApiKey: 'OpenAI rejected the API key.',
      openaiRejectedApiKeyWithStatus: (status: number | string) =>
        `OpenAI rejected the API key (${status} invalid_api_key).`,
      openaiTtsNeedsKey: 'OpenAI TTS requires VOICE_TOOLS_OPENAI_KEY or OPENAI_API_KEY.'
    },
    voice: {
      invalidTitle: 'Voice no longer available',
      invalidMessage: (name: string) =>
        `The voice "${name}" you previously selected is no longer in the catalogue. We've temporarily fallen back to the default — pick another one in Companion settings.`,
      invalidAction: 'Open settings'
    },
    system: {
      view: 'View',
      scheduledTask: 'Scheduled task',
      imageReady: 'Image ready — click to view',
      videoReady: 'Video ready — click to view',
      channelLabel: 'IM channel',
      channelWeixin: 'WeChat',
      channelConnected: (label: string) => `${label} connected`,
      channelLoginRequired: (label: string) => `${label} login expired — rescan in Settings`,
      channelError: (label: string, detail?: string) => `${label} channel error${detail ? `: ${detail}` : ''}`,
      channelPeerRequest: (label: string, name: string) => `${label} new message${name ? `: ${name}` : ''}`
    }
  },

  activation: {
    title: (brandName: string) => `Activate ${brandName}`,
    subtitle: 'Paste the activation code you received to get started.',
    close: 'Close',
    placeholder: 'Paste the activation code here…',
    cancel: 'Cancel',
    submit: 'Activate',
    submitBusy: 'Activating…'
  },

  settings: {
    title: 'App settings',
    closeSettings: 'Close settings',
    nav: {
      inference: 'Inference & chat',
      about: 'About',
      appearance: 'Appearance',
      channels: 'Chat channels',
      interaction: 'Interaction',
      navAriaLabel: 'Settings section navigation',
      persona: 'Persona & memory',
      runner: 'Local runner',
      shortcuts: 'Shortcuts',
      skills: 'Skills & tools',
      voice: 'Voice'
    },
    shortcuts: {
      heading: 'Global shortcuts',
      intro:
        'Summon or hide the companion from anywhere on your system. Click a key field to record a new combination.',
      toggleVisibility: 'Hide / show companion',
      toggleVisibilityDesc: 'Quickly show or hide the companion window on the desktop.',
      openLiving: 'Show / hide Living Space',
      openLivingDesc: 'Quickly summon or hide the immersive Living Space window.',
      openWorkbench: 'Show / hide Workbench',
      openWorkbenchDesc: 'Quickly summon or hide the productivity Workbench window.',
      pressKeysPrompt: 'Press a key combination…',
      pressKeysHint: 'Press Esc to cancel, Backspace or Delete to clear',
      resetAll: 'Restore all defaults',
      resetAllSuccess: 'Default shortcuts restored',
      conflictError: 'This shortcut is taken by the system or another app',
      empty: 'Not set'
    },
    channels: {
      heading: 'Chat channels',
      intro:
        'Let the same companion chat with you on WeChat and other IM apps — personality and memory are shared with the desktop, and the desktop can review but not reply.',
      loadFailed: 'Failed to load channel status',
      statusLabels: {
        connected: 'Connected',
        login_pending: 'Waiting for scan',
        login_required: 'Login required',
        error: 'Error',
        disabled: 'Disabled'
      } as Record<string, string>,
      weixin: {
        title: 'WeChat',
        intro:
          'Scan to log in with your personal WeChat account (official ClawBot channel). The companion can only reply, never initiate.',
        loginAction: 'Scan to log in',
        retryAction: 'Refresh QR code',
        logoutAction: 'Log out',
        logoutConfirmTitle: 'Log out of WeChat?',
        logoutConfirmDescription:
          'After logging out, the companion will no longer reply on WeChat. Re-logging in requires scanning again.',
        loginStartFailed: 'Login failed to start',
        loginSuccess: 'WeChat connected',
        logoutSuccess: 'Logged out of WeChat',
        logoutFailed: 'Logout failed',
        qrPrompt: 'Open WeChat and scan',
        scanedPrompt: 'Scanned — please confirm on your phone',
        expiredPrompt: 'QR code expired, please refresh',
        connectedAs: (name: string) => `Connected${name ? `: ${name}` : ''}`
      },
      peers: {
        title: 'Peer approvals',
        intro:
          'Unknown peers will receive a pairing prompt the first time they message; only approved peers can chat with the companion. Blocked peers are silently ignored.',
        empty: 'No peer records yet',
        approve: 'Approve',
        block: 'Block',
        remove: 'Remove',
        pendingLabel: 'Pending',
        allowedLabel: 'Approved',
        blockedLabel: 'Blocked',
        actionFailed: 'Action failed',
        requestToast: (channel: string, peer: string) =>
          `Someone on ${channel} wants to chat with the companion: ${peer}`
      }
    },
    appearance: {
      heading: 'Appearance',
      hint: "Themes apply to both the Living Space and the Workbench windows; the companion's appearance is unaffected."
    },
    about: {
      heading: (brandFullName: string) => brandFullName,
      version: (value: number | string) => `Version ${value}`,
      versionUnavailable: 'Version unavailable',
      intro: 'Desktop client version and update management.',
      checkForUpdates: 'Check for updates',
      checking: 'Checking…',
      upToDate: 'Up to date',
      upToDateWithVersion: (value: number | string) => `Up to date (v${value})`,
      updateAvailable: (value: number | string) => `v${value} available`,
      updateDownloaded: (value: number | string) => `v${value} ready, restart to install`,
      updateError: (value: string) => `Update check failed: ${value}`
    },
    runner: {
      title: 'Runner configuration',
      intro: 'Configure the local runner. Changes require restarting the runner to take effect.',
      loading: 'Loading runner configuration…',
      failedLoad: 'Failed to load runner configuration',
      save: 'Save configuration',
      saveSuccess: 'Configuration saved',
      saveFailed: 'Failed to save configuration',
      terminal: 'Terminal settings',
      terminalEnvType: 'Environment type',
      ssh: 'SSH connection',
      sshHost: 'Host',
      sshPort: 'Port',
      sshUser: 'Username',
      sshPassword: 'Password',
      sshKey: 'Private key path',
      security: 'Security',
      securityRedactSecrets: 'Redact sensitive output',
      browser: 'Browser settings',
      browserAllowPrivateUrls: 'Allow intranet access',
      debug: 'Debug toggles',
      debugInterrupt: 'Interrupt mode'
    },
    skills: {
      title: 'Skills',
      intro:
        'Each entry below maps to a category directory under $SPIRITAGENT_HOME/skills. Toggling is pushed to the runner immediately; the enabled set is sent to the backend every turn so the model only sees the local skills you can call.',
      loading: 'Loading skills…',
      loadError: 'Could not read the skill list from disk.',
      saveError: 'Could not save the skill toggle.',
      refreshError:
        'Saved locally, but the backend session was not refreshed — the next turn may still see the old skill set, please toggle again.',
      emptyTitle: 'No skills installed',
      emptyDesc: (brandName: string) => `Reinstall ${brandName} to restore the built-in skills.`,
      hiddenByPlatformTitle: 'No skills available for this OS',
      hiddenByPlatformDesc: (brandName: string) =>
        `The skills bundled with this ${brandName} build target other operating systems. Reinstall ${brandName} on a supported OS to enable them.`
    },
    inference: {
      heading: 'Inference & chat',
      intro:
        'Configure defaults for ordinary conversations only. Each special session has scenario defaults and is configured within its own conversation window.',
      loading: 'Loading…',
      saveFailed: 'Could not save inference & chat settings.',
      saved: 'Inference & chat settings saved.',
      agentDefaults: {
        heading: 'Agent defaults',
        intro: 'Applies to ordinary conversations without session overrides. Does not affect special sessions.',
        reasoningEffort: 'Reasoning depth',
        reasoningEffortDesc:
          'How hard the model reasons each turn. none disables reasoning; low / medium / high deepen progressively.',
        backgroundReview: 'Background memory consolidation',
        backgroundReviewDesc:
          'Extracts memories from ordinary conversations without changing memory consolidation in special sessions.',
        reasoningOptions: {
          none: 'Off',
          low: 'Low',
          medium: 'Medium',
          high: 'High'
        }
      },
      contextCompression: {
        heading: 'Context compression',
        intro:
          'When a long conversation nears the context window limit, older messages are automatically replaced with summaries so the session can keep going.',
        enableCompression: 'Enable context compression',
        enableCompressionDesc:
          'When disabled, only the latest 40 messages are kept (deterministic truncation) — no semantic summarization.',
        threshold: 'Compression threshold',
        thresholdDesc: 'Compression triggers when context usage reaches this fraction of the window (30%–100%).'
      },
      temperature: {
        heading: 'Model temperature',
        intro:
          "Controls randomness and creativity in different scenarios. The UI uses a unified 0–1 scale; requests are auto-mapped to the provider's native range.",
        chatTemperature: 'Default conversation temperature',
        chatTemperatureDesc:
          'Default generation temperature for ordinary conversations. Lower values are more deterministic, higher values more creative.',
        titleTemperature: 'Title generation temperature',
        titleTemperatureDesc:
          'Temperature for auto-generating session titles from the first turn. Keep low for accurate summarisation.',
        compressionTemperature: 'Context compression temperature',
        compressionTemperatureDesc:
          'Temperature for generating memory summaries of long histories. Recommended to keep at 0 for factual fidelity.'
      }
    },
    interaction: {
      title: 'Interaction',
      intro: 'How the companion responds and when it may interrupt you.',
      voiceHeading: 'Chat & voice',
      responseMode: 'Response mode',
      responseModeText: 'Text by default',
      responseModeVoice: 'Always voice',
      responseModeDesc: 'Only affects Living Space chats; the Workbench always uses text.',
      recording: 'Recording length cap',
      recordingDesc: 'Maximum length of a single voice recording — auto-stops and sends at the limit.',
      recordingSecondsSuffix: 's',
      recordingSaveFailed: 'Failed to save recording duration',
      tierHeading: 'Disturbance tier',
      tierHint: "Only constrains the companion's proactive behaviour — your actions are never restricted.",
      tierAriaLabel: 'Disturbance tier',
      smartHeading: 'Smart reactions & autonomy',
      smartHint: 'Enables smarter reasoning and decision-making; disable to reduce LLM calls.',
      pokeThinking: 'Poke reactions',
      pokeThinkingAria: 'Poke reactions',
      pokeThinkingDesc:
        'When poked, the LLM generates reaction copy and expressions (off uses preset feedback). Drags always use local presets.',
      idleAffect: 'Idle situational expression',
      idleAffectAria: 'Idle situational mood',
      idleAffectDesc:
        'On autonomy and with the desktop pet visible, the LLM picks situational expressions after 30+ idle minutes.',
      autonomy: 'Autonomous space decisions',
      autonomyAria: 'Autonomous space decisions',
      autonomyDesc:
        'On autonomy and with the desktop pet visible, the LLM decides where to roam, perch, and approach (off uses local rules).',
      autonomousMedia: 'Overnight surprise creations',
      autonomousMediaAria: 'Overnight surprise creations',
      autonomousMediaDesc:
        'Let the companion create an image or short video during the rest window and save it to Living Space moments.',
      autonomousVoice: 'Overnight voice surprises',
      autonomousVoiceAria: 'Overnight voice surprises',
      autonomousVoiceDesc:
        'Let the companion use its current voice for an audio keepsake or add narration to a surprise image or video.'
    },
    persona: {
      title: 'Persona & memory',
      intro: 'Edit the persona and review what the companion remembers — all in one place.',
      sectionTitle: 'Persona',
      sectionMemory: 'Long-term memory',
      editAction: 'Edit',
      editHeading: 'Edit persona',
      defaultName: 'Companion',
      noPersonality: 'No personality set yet',
      nameLabel: 'Name',
      namePlaceholder: 'Give me a name',
      relationshipLabel: 'Relationship',
      relationshipPlaceholder: 'Or describe freely…',
      personalityLabel: 'Personality',
      personalityPlaceholder: 'Describe freely…',
      hintEmptyName: "You'll need to give me a name first",
      hintSaveFailed: 'Save failed, please try again',
      hintHydrateFailed: 'Saved, but the local refresh failed — please try again',
      retuneAction: 'Retune via chat',
      retuneHint:
        'Step-by-step chat retuning for name, personality, speaking style and your info (existing long-term memory is kept)',
      retuneModalTitle: 'Retune persona via chat',
      retuneLoadFailed: "Couldn't load your profile right now — please try again later",
      retunePrev: 'Back',
      retuneNext: 'Next',
      retuneStepPrefix: (n: number, total: string | number) => `Step ${n} · ${total}`,
      retuneReviewTitle: 'Review',
      retuneEmpty: '—',
      retuneSaveFailed: 'Save failed, please try again',
      autoDerivedChip: 'Auto-derive',
      fields: {
        name: 'Persona name',
        namePlaceholder: 'Give me a name',
        relationship: 'Relationship / role',
        personality: 'Personality',
        speakingStyle: 'Speaking style (optional)',
        speakingStylePlaceholder: 'Leave blank to auto-derive from personality',
        userCallName: 'How should I address you',
        userGender: 'Your gender',
        userAgeBucket: 'Age range',
        userHobbies: 'Hobbies',
        userFreeform: "Anything else you'd like to share"
      },
      steps: {
        name: 'Persona definition: name',
        relationship: 'Relationship / role',
        personalityStyle: 'Personality & speaking style',
        aboutBasics: 'Help the companion know you: basics',
        aboutHobbies: 'Help the companion know you: hobbies & extras'
      },
      reviewRows: {
        name: 'Name',
        relationship: 'Relationship',
        personality: 'Personality',
        speakingStyle: 'Speaking style',
        speakingStyleFallback: 'Auto-derive',
        userCallName: 'Call name',
        userGender: 'My gender',
        userAgeBucket: 'Age range',
        userHobbies: 'Hobbies',
        userFreeform: 'Notes'
      }
    },
    voice: {
      title: 'Voice',
      intro: 'Pick a voice for the current system language, or design a custom one.',
      noTtsConfigured: 'No TTS provider is configured.',
      noVoicesForLanguage: 'The configured TTS providers have no voices for the current system language.',
      genderFilterAria: 'Tone filter',
      preview: 'Preview',
      use: 'Use',
      inUse: 'In use',
      noMatch: 'No voices match the current filter.',
      designHeading: 'Design a custom voice',
      designPlaceholder: 'Describe the voice you want…',
      designGenerate: 'Generate preview',
      designGenerating: 'Generating…',
      designFailed: 'Generation failed — try a different description?'
    },
    theme: {
      themesHeading: 'Theme presets',
      themesAriaLabel: 'Theme presets',
      activeBadge: 'Active',
      materialHeading: 'Material effects',
      materialTransparent: 'Clear mode (transparent)',
      materialTransparentDesc:
        'Enables liquid glass and frosted translucency — the room image flows with light, and the desktop companion feels airy.',
      materialTransparentDayNight: 'Day transparent / Night transparent',
      materialSolid: 'Solid mode (classic)',
      materialSolidDesc:
        'Opaque surfaces in graphite and warm paper, with strong contrast — fits busier desktop backgrounds.',
      materialSolidDayNight: 'Day / Night'
    },
    memory: {
      tabAriaLabel: 'Memory type',
      presetLabel: 'Current preset',
      tabRecall: (count: string | number) => `Recall · ${count}`,
      tabAutoInject: (count: string | number) => `Auto-inject · ${count}`,
      userProfileHint: (count: string | number) => `${count} user_profile entries are yours alone`,
      loading: 'Loading…',
      loadFailedHint: 'Failed to load',
      loadFailedToast: 'Failed to load long-term memory',
      saveFailedHint: 'Save failed, rolled back',
      saveFailedToast: 'Failed to save memory',
      deleteFailedHint: 'Delete failed, rolled back',
      deleteFailedToast: 'Failed to delete memory',
      emptyRecall: 'No recall memories yet — the companion will write some as you chat.',
      saved: 'Saved',
      saving: 'Saving…',
      delete: 'Delete',
      autoInjectIntro: (max: number) =>
        `Auto-injected slots are whispered to the LLM each turn — already capped at ${max} chars on write. You can review or edit them here.`,
      autoInjectUpdated: 'Updated',
      autoInjectChars: (current: number, max: number) => `${current} / ${max} chars`,
      autoInjectEmpty: '(Empty) — the companion will fill these in naturally during chat',
      autoInjectSlotHints: {
        communicationStyle: 'How answers are framed (detail level, tone, lists, etc.)',
        rapportState: 'Current rapport / familiarity stage',
        interactionPattern: 'Typical usage rhythm (e.g. late-night heavy use, short sessions)',
        moodPattern: 'Recent emotional pattern (not the current mood)',
        relationshipSignal: 'Trust / banter frequency / formality'
      } as Record<string, string>
    }
  },

  skills: {
    tabSkills: 'Skills',
    tabToolsets: 'Toolsets',
    all: 'All',
    other: 'Other',
    searchSkills: 'Search skills…',
    searchToolsets: 'Search toolsets…',
    loading: 'Loading capabilities…',
    noSkillsTitle: 'No skills found',
    noSkillsDesc: 'Try a broader search or another category.',
    loadFailedTitle: 'Failed to load skill list',
    loadFailedDesc: 'Please try again later, or check the $SPIRITAGENT_HOME/skills directory.',
    noToolsetsTitle: 'No toolsets found',
    noToolsetsDesc: 'Try a broader search term.',
    noDescription: 'No description available.',
    toolsetsEnabled: (enabled: number, total: number) => `${enabled}/${total} toolsets enabled`,
    skillsLoadFailed: 'Failed to load skills',
    toolsetsRefreshFailed: 'Failed to refresh toolsets'
  },

  toolsets: {
    browser_automation: {
      label: 'Browser automation',
      description: 'Multi-backend browser capabilities: navigation, clicks, snapshots, cookies/CDP.'
    },
    file_operations: { label: 'File operations', description: 'Read/write, patches, directory and file search.' },
    terminal: { label: 'Terminal', description: 'Local / Docker / SSH-backed command execution.' },
    code_execution: { label: 'Code execution', description: 'Sandboxed Python execution with restricted calls.' },
    process_management: { label: 'Process management', description: 'Background process startup and tracking.' },
    skills_system: { label: 'Skill system', description: 'List, view and manage Skill contents.' },
    memory: { label: 'Memory', description: 'Write, retrieve and delete long-term memories.' },
    web_tools: { label: 'Web tools', description: 'Web search and page content extraction.' },
    image_generation: { label: 'Image generation', description: 'Generate images via cloud models.' },
    messaging: { label: 'Messaging', description: 'Send messages via Webhook.' },
    scheduled_tasks: { label: 'Scheduled tasks', description: 'Cron triggers and periodic scheduling.' },
    agent_delegation: { label: 'Sub-agent delegation', description: 'Spawn sub-sessions and sub-agents.' },
    computer_use: { label: 'Desktop control', description: 'Take over the desktop via the Windows backend.' },
    media_analysis: { label: 'Media analysis', description: 'Image analysis.' }
  },

  errors: {
    boundaryTitle: 'Something went wrong',
    boundaryDesc: 'This view hit an unexpected error. Your conversations and settings are safe.',
    reloadWindow: 'Reload window'
  },

  ui: {
    search: {
      clear: 'Clear search'
    }
  },

  chat: {
    defaultSessionTitle: 'Daily chat',
    inputPlaceholder: 'Say something, or drag a file over',
    typing: 'Typing...',
    openMainSessionFailed: 'Could not open daily chat',

    filesReceived: (count: number) => `${count} file${count === 1 ? '' : 's'} received`,
    attachmentsAdded: (count: number) => `${count} attachment${count === 1 ? '' : 's'} added`,
    sendFailed: 'Send failed',

    media: {
      imageLoading: 'Loading image…',
      videoLoading: 'Loading video…'
    },

    copy: {
      label: 'Copy message',
      copied: 'Copied',
      failed: 'Failed to copy message'
    },

    fork: {
      label: 'Fork new conversation from this message',
      inFlight: 'Forking new conversation…',
      otherBusy: 'Another fork is in progress',
      failed: 'Fork failed',
      internalError: 'Fork failed: server rejected the request or the network is down'
    },

    undo: {
      label: 'Undo this message',
      inFlight: 'Undoing…',
      otherBusy: 'Another undo is in progress',
      failed: 'Undo failed',
      internalError: 'Undo failed: server rejected the request or the network is down',
      confirm: 'Undo this message? This will also delete every message after it.'
    },

    play: {
      label: 'Read aloud',
      preparing: 'Preparing voice…',
      stop: 'Stop reading',
      channelBusy: 'Voice channel busy',
      failed: 'Voice playback failed'
    },

    attachment: {
      pendingImageAlt: 'Image pending to send',
      loading: 'Loading…',
      sendingImage: 'Sending image…',
      addedImage: 'Image attached',
      removeImage: 'Remove attached image',
      uploading: 'Uploading…',
      ready: 'Ready',
      retry: 'Retry',
      removeVideo: 'Remove attached video',
      fileBadge: 'File',
      removeFile: 'Remove attached file',
      folderBadge: 'Folder',
      removeFolder: 'Remove attached folder'
    },

    slash: {
      confirmRequired: 'This command needs a second confirmation',
      busyStop: 'Please stop the current generation first',
      genericFailed: 'Command failed',
      unknownWithSuggestions: (commands: readonly string[]) =>
        `Unknown command. Try: ${commands.map(s => `/${s}`).join(', ')}`,
      unknown: 'Unknown command',
      inFlight: 'The previous command is still running',
      pendingAttachment: 'Please send or cancel the attachment before running the command',
      confirmMessage: (name: string) => `Run /${name}? This will affect the message history.`,
      popoverTitle: 'Commands',
      popoverConfirm: 'Needs confirmation'
    },

    picker: {
      videoFilterName: 'Video files',
      selectVideo: 'Select video',
      imageFilterName: 'Image files',
      selectImage: 'Select image',
      selectFile: 'Select file',
      selectFolder: 'Select folder'
    },

    voice: {
      play: 'Play voice',
      stop: 'Stop voice playback',
      collapse: 'Collapse',
      showTranscript: 'Show transcript'
    },

    input: {
      workbenchPlaceholder: 'Type a command, ask a question, or drag a file in…',
      readOnlyHint: 'IM conversation · read-only',
      addAttachment: 'Add attachment',
      addFile: 'Add file',
      addFolder: 'Add folder',
      addImage: 'Add image',
      addVideo: 'Add video',
      slashShortcut: 'Slash shortcut',
      slashShortcutHint: 'Slash shortcut (typing / works too)',
      releaseToSendVoice: 'Release to send voice',
      pressToRecordVoice: 'Press and hold to record a voice message',
      stopGenerating: 'Stop generating',
      sendMessage: 'Send message',
      sendMessageShortcut: 'Send message (Enter)'
    },

    emptyHint: 'Say something, or send a file / image / video my way~',

    time: {
      weekdays: ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'] as readonly string[],
      yesterdayAt: (time: string) => `Yesterday ${time}`,
      weekdayAt: (weekday: string, time: string) => `${weekday} ${time}`,
      monthDayAt: (month: number, day: number, time: string) => `${month}/${day} ${time}`,
      fullDateAt: (year: number, month: number, day: number, time: string) => `${year}/${month}/${day} ${time}`
    },

    reasoning: {
      thinkingStreaming: 'Thinking…',
      reasoningStreaming: 'Reasoning…',
      done: 'Thinking complete',
      collapse: 'Collapse',
      expand: 'Show details'
    },

    summary: {
      emptyBody: '(no summary content)',
      cancelled: 'Stopped'
    },

    tools: {
      busy: (name: string) => `Working… (${name})`,
      completed: (count: number) => `Completed ${count} step${count === 1 ? '' : 's'}`
    },

    submit: {
      videoUploadFailed: 'Video upload failed, please pick another',
      videoUploading: 'Video is uploading, please wait…',
      unknownCommand: (name: string) => `Unknown command: /${name}. Try /help to see available commands.`,
      displayVideo: '(video)',
      displayImage: '(image)',
      displayFile: (name: string) => `[file] ${name}`,
      displayFolder: (name: string) => `[folder] ${name}`,
      promptVideo: 'Please look at this video',
      promptImage: 'Please look at this image',
      promptFile: (path: string) => `@file:${path}`,
      promptFolder: (path: string) => `@folder:${path}`,
      fileInlinePrefix: (name: string, path: string) => `[file: ${name}] ${path}`,
      folderInlinePrefix: (name: string, path: string) => `[folder: ${name}] ${path}`,
      attachmentsHeading: (names: string) => `Attachments: ${names}`,
      attachmentsJoiner: ', '
    },

    params: {
      tabs: {
        context: 'Context',
        temperature: 'Temperature',
        reasoning: 'Reasoning'
      },
      temperaturePresets: {
        precise: 'Precise',
        balanced: 'Balanced',
        divergent: 'Creative'
      },
      temperatureStyles: {
        precise: 'Precise',
        balanced: 'Balanced',
        divergent: 'Creative'
      },
      reasoningOptions: {
        none: 'Off',
        low: 'Low',
        medium: 'Medium',
        high: 'High'
      },
      reasoningHints: {
        high: 'Deep reasoning — suited for complex plans and proofs.',
        low: 'Quick thinking — best for simple Q&A.',
        medium: 'Standard reasoning — good for development and debugging.',
        none: 'Direct answers — fastest response.'
      },

      contextCapsuleAria: 'View context memory and compression management',
      contextCapsuleTitle: (used: string, total: string, pct: string, threshold: number) =>
        `Current context: ${used} / ${total} tokens (${pct}%) · auto-compress threshold: ${threshold}% · click to expand`,
      temperatureCapsuleAria: 'Configure current session sampling temperature',
      temperatureCapsuleTitle: (temp: string, label: string) =>
        `Current sampling temperature: ${temp} (${label}) · click to configure`,
      reasoningCapsuleAria: 'Configure current session reasoning depth',
      reasoningCapsuleTitle: (label: string) => `Current reasoning depth: ${label} · click to configure`,
      reasoningOff: 'Off',
      reasoningCapsuleOff: 'Thinking off',
      reasoningCapsuleOn: (label: string) => `Thinking: ${label}`,

      thresholdMarker: (pct: number) => `Auto-compress threshold (${pct}%)`,

      manualCompressRunning: 'Compressing current session context…',
      manualCompressTooltipInline: (used: string, total: string, pct: string) =>
        `Context: ${used} / ${total} tokens (${pct}%)`,
      manualCompressThresholdInline: (pct: number) => `Threshold: ${pct}%`,
      manualCompressClickInline: 'Click to compress now',
      manualCompressButtonAria: 'Manually compress context',
      manualCompressButtonTitle: 'Click to manually compress the current session context',
      manualCompressThresholdMarker: (pct: number) => `Compression threshold (${pct}%)`,
      manualCompressSuccess: (count: number) => `Successfully compressed ${count} early messages`,
      manualCompressNotNeeded: 'Not much history to compress yet',
      manualCompressFailed: 'Failed to manually compress context',

      thresholdSliderHeading: 'Context window load',
      thresholdSliderSubheading: '/ auto-compress line',
      thresholdSliderTriggerLabel: 'Trigger point:',
      thresholdSliderAria: 'Auto-compress threshold (drag left/right to adjust)',
      thresholdSliderMin: '30% tighter compression',
      thresholdSliderHint: 'Drag the handle left/right to adjust the threshold',
      thresholdSliderMax: '100% full capacity',
      thresholdSliderDescription:
        'When context reaches the chosen ratio, older history is automatically summarised in the background to free up room and keep memory coherent.',

      saveFailed: 'Could not save session parameters. Please try again.',
      resetConfirm: 'Session parameters reset to defaults',
      compressSuccessMore: (count: number) => `Successfully tidied and compressed ${count} earlier messages`,
      compressNotNeeded: 'Not much history to compress yet',

      sessionScopeBadge: 'Current session',
      sessionScopeBadgeTitle: 'Changes here apply only to this session — global defaults are not affected',
      closePanel: 'Close panel',

      statsUsed: 'Used',
      statsMax: 'Max capacity',
      statsPercent: 'Usage',
      statsTokens: 'Tokens',
      statsNodeAt: (pct: number) => `Threshold ${pct}%`,

      statusHealthy: 'Plenty of headroom — memory is comfortable for smooth conversation.',
      statusWarning: 'Context is filling up — nearing the auto-compress threshold; tidy anytime.',
      statusCritical: 'Context is heavy — past the auto-compress line; compress now to keep memory coherent.',

      compressing: 'Distilling and compressing memory…',
      compressAction: 'Tidy history · compress context now',

      temperatureLabel: 'Sampling temperature',
      temperatureSliderAria: 'Sampling temperature',
      temperatureScaleMin: '0.00 precise',
      temperatureScaleMid: '0.70 balanced',
      temperatureScaleMax: '1.00 creative',
      temperatureHint: 'Lower is more deterministic, higher is more creative. Applies to this session only.',

      reasoningLabel: 'Reasoning depth',
      reasoningFallbackLabel: 'Off',

      scopeNote: 'Applies to this session only · auto-saved',
      scopeNoteTitle: 'These settings apply to this session and are saved automatically',
      resetButtonTitle: 'Reset this session parameters to defaults',
      resetButton: 'Reset to defaults'
    },

    presetPicker: {
      title: 'Pick a preset for the new conversation',
      intro:
        'The system prompt for this conversation is fixed from here; you can still rename, archive, or delete it later.',
      confirm: 'Create',
      cancel: 'Cancel',
      fetchFailed: 'Failed to load presets — please try again later',
      pickOne: 'Please pick a preset first'
    },

    sessionRename: {
      action: 'Rename',
      inputLabel: 'Conversation name',
      placeholder: 'Enter a name',
      hint: 'Enter to save · Esc to cancel',
      forbidden: 'System preset conversations cannot be renamed',
      failed: 'Rename failed, restored previous name'
    }
  },

  companion: {
    statusBusy: 'Busy',
    statusCompanion: 'With you'
  },

  living: {
    title: 'Living Space',
    goToWorkbench: 'Open Workbench',
    rail: {
      chat: 'Chat',
      moments: 'Moments',
      diary: 'Diary',
      wardrobe: 'Wardrobe',
      appearance: 'Look',
      channels: 'Channels',
      room: 'Room',
      settings: 'Settings',
      companionFallback: 'Companion',
      avatarMood: (name: string) => `${name}'s mood`
    },
    moments: {
      loading: 'Flipping through the album…',
      empty: 'No moments captured yet.',
      noTitle: 'Untitled',
      kindLabels: {
        emotion: 'Feeling',
        greeting: 'Greeting',
        milestone: 'Milestone',
        scene: 'Scene',
        together: 'Together',
        user: 'Note'
      } as Record<string, string>,
      kindFallback: 'Moment'
    },
    diary: {
      loading: 'Opening the journal…',
      todayBadge: 'Today',
      mood: (mood: string) => `Mood · ${mood}`,
      signature: (name: string) => `— ${name}'s diary`,
      emptyTitle: 'No diary entry for this day',
      emptyHintToday: "Today's entry is written overnight — check back a little later.",
      emptyHintOther: 'No diary entry recorded for this day.',
      weekHeader: ['M', 'T', 'W', 'T', 'F', 'S', 'S'] as ReadonlyArray<string>,
      weekDayNames: [
        'Sunday',
        'Monday',
        'Tuesday',
        'Wednesday',
        'Thursday',
        'Friday',
        'Saturday'
      ] as ReadonlyArray<string>,
      dateFormat: (dateStr: string, weekDay: string) => `${dateStr} · ${weekDay}`
    },
    wardrobe: {
      preview: {
        views: 'Preview views',
        stage: 'Asset pack animation preview',
        front: 'Front actions',
        left: 'Left edge',
        right: 'Right edge',
        action: 'Animation',
        play: 'Play',
        pause: 'Pause',
        replay: 'Replay',
        loop: 'Loop preview · Outfit unchanged',
        loading: 'Loading local or remote assets…',
        failed: 'Preview unavailable; the portrait is still visible',
        noPose: 'This asset pack has no edge poses',
        retry: 'Retry loading',
        actions: {
          idle: 'Breathing and blinking',
          wave_left: 'Wave left',
          wave_right: 'Wave right',
          look_away_left: 'Look left',
          look_away_right: 'Look right',
          turn_body_left: 'Turn left',
          turn_body_right: 'Turn right',
          petting: 'Head pat'
        }
      },
      empty: 'No 2D looks are ready yet. Generate the 2D animation assets to start swapping outfits.',
      policyLabel: 'Companion-driven outfits',
      policyDesc: 'When enabled, the companion may choose a ready look or design a new one overnight.',
      policyStatusLocked: 'Locked',
      policyStatusUnlocked: 'Allowed',
      policyToggleAria: 'Allow companion-driven outfit changes',
      wearing: 'Wearing',
      statusLabels: {
        draft: 'Draft',
        splitting: 'Splitting…',
        failed: 'Split failed',
        expired: 'Expired'
      } as Record<string, string>,
      autoWearAfterSplit: 'Auto-wear once split completes',
      actions: {
        continueDesign: 'Continue design',
        continueDesignTitle: 'Continue designing this draft',
        wear: 'Wear',
        wearTitle: 'Wear this look',
        retry: 'Retry',
        delete: 'Delete',
        deleteTitle: 'Delete this look'
      },
      imageAlt: 'Outfit portrait',
      previewPlaceholderDesigning: 'Describe an outfit and the preview will appear here',
      previewPlaceholderIdle: 'Pick a look on the left to preview it, or begin a new design',
      previewHint: 'Swap clothing, hairstyle and accessories; the face and body shape stay the same.',
      generating: 'Generating…',
      startPrompt: 'Describe (or attach a reference image) to dress your companion in something new',
      startAction: 'Start a new design',
      designIntro:
        'Describe the outfit you want — e.g. "sailor uniform swapped for an off-white knit cardigan and a brown long skirt, hair tied in a low ponytail". You can also attach a reference image. One outfit per hour at most.',
      confirmAndWear: 'Confirm and wear',
      processing: 'Processing…',
      discard: 'Discard draft',
      confirmHint:
        'After confirming, the look will be 2D-split and auto-worn on completion (current look stays until then).',
      refImageAttached: 'Reference image attached',
      refImageAlt: 'Reference image',
      placeholderRefining: 'What would you like to tweak? Keep describing… (Enter to send)',
      placeholderInitial: 'Describe an outfit… (Enter to send, Shift+Enter for newline)',
      attachImage: 'Attach reference image',
      attachImageTitle: 'Attach reference image (optional, first generation only)',
      send: 'Send'
    },
    appearance: {
      renderMode: 'Render mode',
      renderModeHint:
        'Switching to 3D walks you through a wizard to generate each 3D front portrait (multi-view providers also add a back portrait); each requires a manual tap. Once confirmed, the cloud-side 3D model is generated (1–3 minutes). During generation the 2D animated version is shown (with a programmatic egg transition). Failures stay on the 2D animated version permanently; switching back to 2D takes effect immediately.',
      mode2d: '2D animated',
      mode3d: '3D',
      mesh2dFailed: '2D animation assets failed to generate',
      mesh2dMissing: '2D animation assets not yet generated',
      mesh2dRetry: 'Re-split',
      companionSize: 'Companion size',
      companionSizeHint: 'Default display scale of the sprite on the desktop.',
      scaleRange: 'Continuously adjustable from 0.3×–3×; 1× is the default.',
      scaleAria: 'Companion size'
    },
    room: {
      intro: 'Change the mood, roll back to an earlier room, or lock the room policy — all here.',
      noBackdrop: 'No active room backdrop yet',
      currentBadge: 'Current room',
      currentAltFallback: 'Active room',
      pendingOverlay: 'Setting up a new room…',
      pendingOverlayHint: 'Decorating based on your companion and daily vibe — a moment and you can move in',
      failedOverlay: 'Room setup failed',
      failedOverlayHint: 'Generation service is busy or the network is unstable — tap to retry',
      retryButton: 'Try again',
      briefFallback: 'A warm, cozy living space',
      briefPending: 'New room being decorated…',
      subbriefReady: 'Tailored to your companion and current mood',
      subbriefPending: 'Switches over seamlessly once ready',
      generateButton: 'Generate new room',
      generatingButton: 'Generating…',
      historyTitle: 'Rollback history',
      historySubtitle: 'Last 5',
      historyEmpty: 'No rollback history yet',
      historyAltFallback: 'Previous room',
      historyCurrentLabel: 'Current',
      historyRollbackLabel: 'Use this one',
      historyCurrentAria: 'Currently active room',
      historyRollbackAria: (id: string) => `Roll back to previous room ${id}`,
      policyTitle: 'Room policy',
      policyLabel: 'Companion-driven room changes',
      policyDesc:
        'When locked the companion refuses to change rooms on their own, only responding to outfit-linked changes or your direct request. Unlocked, the companion can switch rooms whenever the mood strikes.',
      policyStatusLocked: 'Locked',
      policyStatusUnlocked: 'Allowed',
      policyToggleAria: 'Allow companion-driven room changes'
    },
    roomBackdrop: {
      failedText: 'Tidying the room…',
      pendingText: 'Setting up the room'
    },
    toasts: {
      roomSlow: 'Room generation is taking longer than expected — please retry shortly',
      roomReady: 'New room is ready!',
      roomReadyAlt: 'New room is all set!',
      roomRegenerateFailed: 'Failed to change rooms… try again in a moment',
      roomRollbackFailed: 'Rollback failed — the previous room may no longer be available',
      roomRollbackSuccess: 'Switched back to a previous room',
      roomLockFailed: "Couldn't update the lock setting",
      roomLocked: 'Locked — companion-driven room changes are disabled',
      roomUnlocked: 'Unlocked — the companion may change rooms on their own',
      roomFailedFallback: 'Room generation failed, please retry shortly'
    }
  },

  workbench: {
    title: 'Workbench',
    stationBadge: 'Work station',
    stationSettingsBadge: 'Station settings',
    stationSettingsTooltip: 'Workstation environment',
    stationEnvironment: 'Workstation',
    backToChat: 'Back to chat',
    backToChatTooltip: 'Back to workstation chat (Esc)',
    openLiving: 'Living Space',
    openLivingTooltip: 'Switch to Living Space',
    companionTitle: (brandName: string) => `${brandName} companion (hold to drag the whole Workbench, tap to interact)`,
    station: {
      tabsAria: 'Station settings navigation',
      tabs: {
        inference: 'Inference & chat',
        runner: 'Local runner',
        skills: 'Skills & tools'
      }
    },
    sessionSidebar: {
      title: 'Sessions',
      new: 'New conversation',
      searchAria: 'Search conversations',
      searchPlaceholder: 'Search conversations…',
      searchResultsHeading: 'Search results',
      searching: 'Searching…',
      noMatch: 'No matching conversations',
      badgeArchived: 'Archived',
      specialHeading: 'Professional work presets',
      loadingSpecial: 'Loading preset conversations…',
      noSpecial: 'No professional preset conversations',
      regularHeading: 'Regular conversations',
      loadingSessions: 'Loading conversations…',
      noRegular: 'No regular conversations yet — tap above to create one',
      specialStationFallback: 'Pro station',
      newSessionFallback: 'New conversation',
      messageCount: (n: number) => `${n} messages`,
      sortOptions: {
        recent: 'Sort by recent activity',
        created: 'Sort by creation time',
        messages: 'Sort by message count'
      },
      presetMeta: {
        copywriter: 'Copywriter',
        developer: 'Developer',
        language_teacher: 'Language teacher',
        product_manager: 'Product manager'
      },
      actions: {
        pin: 'Pin',
        unpin: 'Unpin',
        archive: 'Archive',
        restore: 'Restore',
        delete: 'Delete'
      },
      time: {
        now: 'Just now',
        todayPrefix: (time: string) => `Today ${time}`,
        yesterdayPrefix: (time: string) => `Yesterday ${time}`,
        dateFormat: (month: number, day: number, time: string) => `${month}/${day} ${time}`
      },
      archive: {
        loading: 'Loading…',
        empty: 'No archived conversations',
        collapse: 'Hide archived conversations',
        expand: (count: number) => `Archived conversations${count > 0 ? ` (${count})` : ''}`
      }
    },
    runRail: {
      label: 'Run rail',
      expandAria: 'Expand run rail',
      collapseAria: 'Collapse run rail',
      collapseTitle: 'Collapse',
      subtitle: (steps: number) => `${steps} steps · this round`,
      idleSubtitle: 'Idle',
      stepsUnit: 'steps',
      toolsTitle: 'Tools this round',
      toolsSubtitleIdle: 'Not yet started',
      toolsSubtitleSteps: (steps: number) => `${steps} steps`,
      preparing: 'Preparing…',
      toolsEmpty: "This round hasn't started yet",
      artifactsTitle: 'Session artifacts',
      artifactsSubtitleEmpty: 'None yet',
      artifactsSubtitleCount: (count: number) => `${count} items`,
      artifactsEmpty: 'No images or videos generated this round'
    }
  },

  whisper: {
    close: 'Close Whisper'
  }
}
