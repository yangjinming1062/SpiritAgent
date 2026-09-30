import type { Dictionary } from './zh'

export const dict: Dictionary = {
  brand: {
    name: 'SpiritAgent',
    fullName: 'SpiritAgent Desktop'
  },

  common: {
    companionControl: {
      title: 'Companion',
      show: 'Show companion',
      left: 'Left side',
      right: 'Right side',
      temporaryEdge: 'Not enough room beside the window; companion is temporarily hidden',
      temporaryMaximized: 'Companion is temporarily hidden while maximized',
      saveFailed: 'Could not save companion display settings. Please try again.',
      dragTitle: (name: string) => `Drag ${name} to move the whole window`
    },
    save: 'Save',
    saving: 'Saving…',
    cancel: 'Cancel',
    clear: 'Clear',
    close: 'Close',
    copied: 'Copied',
    copyFailed: 'Copy failed',
    loading: 'Loading…',
    maximize: 'Maximize',
    minimize: 'Minimize',
    remove: 'Remove',
    restore: 'Restore',
    retry: 'Retry',
    pickDate: 'Pick a date',
    datePicker: { year: 'Year', month: 'Month', day: 'Day', unset: 'Not set' },
    imagePick: {
      tooLarge: 'This image is too large. Choose a smaller one.',
      readFailed: "Couldn't read the selected image. Try again."
    },
    processing: 'Processing…'
  },

  selfSource: {
    guidanceAction: 'Need to create an image? Get the prompt and references',
    open: 'Use your own image',
    openTitle: 'Upload a finished image, or get a prompt to create one elsewhere',
    hint: 'Upload a finished image directly, or expand the prompt and references to create it elsewhere. Check the identity, visual style and framing before adopting.',
    referenceTitle: 'Reference images (provide with the prompt)',
    referenceHint:
      'Hand the reference image(s) and prompt to your image tool; use thumbnail actions to copy or save. Click to enlarge.',
    referenceMissing:
      'No reference image is available right now. The prompt is anchored to the avatar — confirm the avatar in Settings → Character & Memory first, or retry later.',
    referenceZoom: 'Reference preview',
    refs: {
      avatarSeed: 'Avatar',
      fullbodySeed: 'Full body'
    },
    promptLoading: 'Preparing the prompt…',
    promptFailed: 'Failed to fetch the prompt. Please retry.',
    retryPrompt: 'Refetch prompt',
    copyPrompt: 'Copy prompt',
    copied: 'Copied',
    copyRefImage: 'Copy image',
    copiedRefImage: 'Image copied',
    saveRefImage: 'Save image',
    saveRefImageFailed: 'Failed to save. Please retry.',
    copyRefImageFailed: 'Failed to copy. Please retry.',
    pickTitle: 'Pick the finished image',
    pickImage: 'Pick image',
    replaceImage: 'Replace',
    adopt: 'Use this image',
    adopting: 'Saving…',
    adoptFailed: 'Failed to save. Please retry.',
    useAi: 'Use AI generation'
  },

  generationActions: {
    aiTitle: 'Generate with AI',
    edit: 'Refine',
    regenerate: 'Regenerate',
    generate: 'Generate',
    editHint: 'Changes only what you describe; everything else stays',
    editRequiresFeedback: 'Describe the changes first to refine',
    regenerateHint: 'Redraws the whole image from the references and your brief',
    groupHint: 'Refine changes only what you describe; Regenerate redraws the whole image from the references.',
    selfTitle: 'Use your own image',
    selfHint:
      'Upload a prepared image directly, or get a prompt and references to create one in an external tool first.'
  },

  boot: {
    desktopBootFailedWithMessage: (message: string) => `Desktop failed to start: ${message}`,
    errors: {
      desktopBootFailed: 'Desktop failed to start',
      bridgeUnavailable: 'Desktop components are unavailable. Restart the app.',
      desktopReconnectFailed: 'Lost connection to the backend. The app is retrying in the background.'
    },
    failure: {
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
      methodNotAllowed: (brandFullName: string) =>
        `The server rejected this request (405 Method Not Allowed). Check whether ${brandFullName} needs an update.`,
      microphonePermission: 'Microphone permission was denied.',
      openaiRejectedApiKey: 'OpenAI rejected the API key.',
      openaiRejectedApiKeyWithStatus: (status: number | string) =>
        `OpenAI rejected the API key (${status} invalid_api_key).`
    },
    voice: {
      invalidTitle: 'Voice no longer available',
      invalidMessage: (name: string) =>
        `The voice "${name}" you previously selected is no longer in the catalogue, so the default voice is being used for now. Choose another in Settings › Voice.`,
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
      channelPeerRequest: (label: string, name: string) =>
        `${name || 'A new contact'} on ${label} wants to chat with the companion — approve it in Chat channels`
    }
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
  },

  settings: {
    nav: {
      about: 'About',
      theme: 'Theme',
      interaction: 'Interaction',
      navAriaLabel: 'Settings section navigation',
      persona: 'Persona & memory',
      shortcuts: 'Shortcuts',
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
      recordingAria: 'Recording shortcut',
      editAria: 'Click to edit shortcut',
      cancelHint: 'Esc to cancel',
      registeredOk: 'Global hotkey registered',
      registerFailed: 'Hotkey registration failed',
      clearAria: 'Clear shortcut',
      clearTitle: 'Disable / clear shortcut',
      resetAll: 'Restore all defaults',
      resetAria: 'Restore default shortcut',
      resetTitle: (value: string) => `Restore default (${value})`,
      defaultLabel: 'Default',
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
        loginTimeout: 'Login timed out. Get a new QR code.',
        qrAlt: 'WeChat login QR code',
        connectedAs: (name: string) => `Connected${name ? `: ${name}` : ''}`
      },
      peers: {
        title: 'Peer approvals',
        intro:
          'Unknown peers get a pairing prompt the first time they message. Once approved, they can chat with the companion and have it perform actions on this computer. Messages from blocked peers are silently ignored.',
        empty: 'No peer records yet',
        approve: 'Approve',
        block: 'Block',
        remove: 'Remove',
        pendingLabel: 'Pending',
        allowedLabel: 'Approved',
        blockedLabel: 'Blocked',
        actionFailed: 'Action failed',
        loadFailed: "Couldn't load peers"
      }
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
      downloadUpdate: 'Download update',
      retryDownload: 'Download again',
      downloading: (percent: number) => `Downloading update ${percent}%`,
      preparing: 'Preparing to install…',
      updateDownloaded: (value: number | string) => `v${value} is ready; restart to finish installing`,
      restartNow: 'Restart now',
      checkError: (value: string) => `Update check failed: ${value}`,
      downloadError: (value: string) => `Update download failed: ${value}`,
      installError: (value: string) => `Update install failed: ${value}`
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
      envLocal: 'Local',
      ssh: 'SSH connection',
      sshHost: 'Host',
      sshPort: 'Port',
      sshUser: 'Username',
      sshPassword: 'Password',
      sshKey: 'Private key path',
      security: 'Security',
      securityRedactSecrets: 'Redact sensitive output',
      browser: 'Browser settings',
      browserAllowPrivateUrls: 'Allow intranet access'
    },
    skills: {
      title: 'Skills',
      intro:
        'Each entry is a group of installed local skills. Changes apply immediately; the model only sees skills that are enabled and available.',
      loading: 'Loading skills…',
      loadError: 'Could not read the skill list from disk.',
      saveError: 'Could not save the skill toggle.',
      refreshError: "Saved, but the current conversation hasn't picked up the change yet — toggle again.",
      emptyTitle: 'No skills installed',
      emptyDesc: (brandName: string) => `Reinstall ${brandName} to restore the built-in skills.`,
      hiddenByPlatformTitle: 'No skills available for this OS',
      hiddenByPlatformDesc: (brandName: string) =>
        `The skills bundled with this ${brandName} build target other operating systems. Reinstall ${brandName} on a supported OS to enable them.`
    },
    inference: {
      heading: 'Inference & chat',
      intro:
        'Configure defaults for ordinary conversations only. Each fixed conversation has scenario defaults and is configured within its own conversation window.',
      loading: 'Loading…',
      saveFailed: 'Could not save inference & chat settings.',
      saved: 'Inference & chat settings saved.',
      loadFailed: "Couldn't load inference & chat settings",
      agentDefaults: {
        heading: 'Agent defaults',
        intro: 'Applies to ordinary conversations without session overrides. Does not affect fixed conversations.',
        reasoningEffort: 'Reasoning depth',
        reasoningEffortDesc:
          "How hard the model reasons each turn. Off disables reasoning; Minimal through Ultra go progressively deeper. Above the provider's ceiling, its highest supported level is used.",
        backgroundReview: 'Background memory consolidation',
        backgroundReviewDesc:
          'Extracts memories from ordinary conversations without changing memory consolidation in fixed conversations.',
        reasoningOptions: {
          none: 'Off',
          minimal: 'Minimal',
          low: 'Low',
          medium: 'Medium',
          high: 'High',
          xhigh: 'X-High',
          max: 'Max',
          ultra: 'Ultra'
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
          'Temperature for summarising long conversation histories. Recommended to keep at 0 for factual fidelity.'
      }
    },
    interaction: {
      title: 'Interaction',
      intro: 'How the companion responds and when it may interrupt you.',
      voiceHeading: 'Chat & voice',
      responsePreference: 'Response preference',
      responsePreferenceText: 'Prefer text',
      responsePreferenceVoice: 'Prefer voice',
      responsePreferenceDesc:
        'Tell your companion which format you usually prefer. They choose based on the situation. Tap voice messages to listen.',
      recording: 'Recording length cap',
      recordingDesc: 'Maximum length of a single voice recording — auto-stops and sends at the limit.',
      recordingSecondsSuffix: 's',
      recordingSaveFailed: 'Failed to save recording duration',
      recordingLoadFailed: "Couldn't load the recording limit. Try again later.",
      tierHeading: 'Disturbance tier',
      tierHint: "Only constrains the companion's proactive behaviour — your actions are never restricted.",
      tierAriaLabel: 'Disturbance tier',
      tiers: {
        still: { label: 'Still', hint: 'Never initiates anything; only responds to you' },
        normal: { label: 'Normal', hint: 'Light in-place interactions such as text greetings' },
        autonomous: { label: 'Autonomous', hint: 'Moves freely and speaks; all abilities enabled' }
      },
      smartHeading: 'Smart reactions & autonomy',
      smartHint: 'Enables smarter reasoning and decision-making; disable to reduce LLM calls.',
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
        'Let the companion create an image or short video overnight and save it to Living Space moments.',
      autonomousVoice: 'Overnight voice surprises',
      autonomousVoiceAria: 'Overnight voice surprises',
      autonomousVoiceDesc:
        'Let the companion use its current voice for an audio keepsake or add narration to a surprise image or video.'
    },
    persona: {
      characterCard: {
        edit: 'Edit',
        title: 'Character card',
        description:
          'Supporting descriptions from the confirmed portrait and full-body image. The images remain the source for appearance. Outfits manage clothes, hairstyle, hair color, makeup and accessories.',
        portrait: 'Portrait',
        body: 'Full-body image',
        portraitFeatures: 'Face and head',
        bodyFeatures: 'Body features',
        restore: 'Use extracted value',
        edited: 'Edited',
        unknown: 'Not observed or not applicable',
        analyzing:
          'Analyzing fixed features from the portrait and full-body image. No field-by-field confirmation needed.',
        failed: 'Analysis failed. Your confirmed images and published character details are preserved. You can retry.',
        retry: 'Retry analysis',
        extract: 'Extract again',
        reload: 'Reload',
        saved: 'Descriptions saved. Future generation keeps the confirmed appearance.',
        conflict: 'The character card was updated elsewhere. Your changes are preserved. Review before merging.',
        merge: 'Keep changes with latest version',
        loadFailed: 'Could not load character card. Please retry.',
        operationFailed: 'The operation failed. Your changes are preserved.',
        fields: {
          head_shape: 'Head shape',
          facial_features: 'Facial features',
          facial_surface: 'Facial surface',
          head_identifiers: 'Distinctive head features',
          body_shape: 'Body shape',
          proportions: 'Proportions',
          limbs_and_appendages: 'Limbs and natural appendages',
          body_surface: 'Body surface and markings'
        }
      },
      title: 'Persona & memory',
      intro: 'Edit the persona and review what the companion remembers — all in one place.',
      sectionTitle: 'Persona',
      sectionMemory: 'Long-term memory',
      mediaReviewTitle: 'Action videos to confirm',
      mediaReviewLoadError: 'Could not load actions to confirm. Refresh to try again.',
      mediaReviewEmpty: 'No actions need confirmation right now.',
      mediaReviewRefresh: 'Refresh',
      fullbodyReference: {
        title: 'Full-body image',
        hint: 'Consistent visual style and a stable idle pose suited to this character’s anatomy and personality. Used for the default video, outfits and scenes.',
        confirmHint:
          'Confirmation starts character analysis, followed by the default video and scene. You can continue onboarding.',
        candidateHint:
          'This full-body image is awaiting acceptance. Body proportions may change; accepting it updates the body reference for future generations. The old video stays visible until you create a new one.',
        acceptCandidate: 'Accept new full-body image',
        retryCandidateAnalysis: 'Retry body analysis',
        acceptCandidateFailed: 'Could not accept this image. Try again.',
        retryCandidateAnalysisFailed: 'Analysis failed. Try again.',
        adoptHint:
          'Adopting confirms this full-body image and starts character analysis, followed by the default video and scene.',
        adoptPreviewFailed: 'The image was saved, but its preview is not ready. Reload before confirming.',
        empty: 'No full-body image yet. Generate one from the current portrait and persona.',
        loading: 'Preparing the full-body image…',
        sourceChoiceTitle: 'Choose how to get the full-body image',
        sourceChoiceHint:
          'Set the full-body look first — it feeds the default video, outfits and scenes. Draw with AI, or upload an image you already prepared.',
        sourceAi: 'AI generate',
        sourceAiArrow: 'AI draw →',
        sourceAiHint:
          'Generate from the confirmed portrait. Add body and outfit details or a reference image, then preview, refine or regenerate.',
        sourceSelf: 'Direct upload',
        sourceSelfArrow: 'Choose image →',
        sourceSelfHint:
          'Upload an image you prepared as the full-body look, or fetch a prompt and draw it externally first.',
        backToSourceChoice: 'Choose another way',
        referenceLabel: 'User reference image',
        refLabel: 'Reference image (optional)',
        refHint:
          'Optionally attach a reference so the result matches your intent. It adds physique, clothing and pose cues and does not become this step’s result. The confirmed portrait determines the face and species.',
        chooseReference: 'Choose reference image',
        replaceReference: 'Replace reference image',
        removeReference: 'Remove reference image',
        pickError: 'Could not select the image. Try a smaller PNG, JPEG, WebP or GIF file.',
        descriptionLabel: 'Full-body description (optional)',
        descriptionPlaceholder: 'For example: slender build, mechanical arm, dark blue robe, natural standing pose…',
        descriptionHint:
          'The confirmed portrait defines the face and species. This description applies only to this generation.',
        feedbackLabel: 'What should change? (optional)',
        feedbackPlaceholder: 'Describe any changes to the body or pose, or leave blank to generate.',
        generate: 'Generate full-body image',
        regenerate: 'Regenerate full-body image',
        editDisabledByReference: 'Refine is unavailable while a reference image is attached — remove it first',
        reload: 'Reload',
        enlarge: 'Enlarge full-body image',
        historyTitle: 'Previous images',
        historyVersion: 'Version',
        historyCurrent: 'Current',
        historyCurrentUnavailable: 'The current preview is unavailable. Choose a previous version below.',
        historySelectedHint:
          'An earlier version is selected. Confirming restores it first; to keep refining, set it as the current version.',
        restoreVersion: 'Set as current version',
        restoreFailed: 'Could not restore this version. Please try again.',
        back: 'Back',
        continue: 'Confirm and continue',
        errors: {
          load: 'Could not load the full-body image. Please reload.',
          generate:
            'Generation did not finish. The previous preview is preserved. Reload to check the result before generating again.',
          preview: 'The image was saved, but its preview could not load. Please reload.'
        }
      },
      editAction: 'Edit',
      editHeading: 'Edit persona',
      defaultName: 'Companion',
      noPersonality: 'No personality set yet',
      nameLabel: 'Name',
      namePlaceholder: 'Name your companion',
      relationshipLabel: 'Relationship',
      relationshipPlaceholder: 'Or describe freely…',
      personalityLabel: 'Personality',
      personalityPlaceholder: 'Describe freely…',
      detailSeparator: ':',
      speakingStyleLabel: 'Speaking style',
      speakingStylePlaceholder: 'Describe the tone and expression style you want',
      hintEmptyName: 'Please enter a name',
      hintEmptySpeakingStyle: 'Please enter a speaking style',
      hintSaveFailed: 'Save failed, please try again',
      hintHydrateFailed: 'Saved, but the local refresh failed — please try again'
    },
    voice: {
      title: 'Voice',
      intro: 'Pick a voice for the current system language, or design a custom one.',
      noTtsConfigured: 'No TTS provider is configured.',
      noVoicesForLanguage: 'The configured TTS providers have no voices for the current system language.',
      loadFailed: "Couldn't load the voice list",
      sampleLine: (name: string) =>
        name ? `Hi, I'm ${name}. This is my voice.` : "Hi, I'm your companion. This is my voice.",
      genderFilterAria: 'Tone filter',
      genderFilters: {
        '': 'All',
        female: 'Female',
        male: 'Male',
        neutral: 'Neutral'
      },
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
      heading: 'Theme',
      hint: "Themes apply to both the Living Space and the Workbench windows; the companion's appearance is unaffected.",
      themesHeading: 'Theme presets',
      themesAriaLabel: 'Theme presets',
      activeBadge: 'Active',
      themes: {
        night: { label: 'Night', description: 'Deep and calm — made for nighttime use' },
        day: { label: 'Day', description: 'Warm and bright, full of energy' },
        'night-clear': {
          label: 'Night clear',
          description: 'Dark translucent surface — the scene shows through the shell'
        },
        'day-clear': {
          label: 'Day clear',
          description: 'Light translucent surface — the scene shows through the shell'
        }
      },
      materialHeading: 'Material effects',
      materialTransparent: 'Clear mode (transparent)',
      materialTransparentDesc:
        'Enables liquid glass and frosted translucency — the scene image flows with light, and the desktop companion feels airy.',
      materialTransparentDayNight: 'Day transparent / Night transparent',
      materialSolid: 'Solid mode (classic)',
      materialSolidDesc:
        'Opaque surfaces in graphite and warm paper, with strong contrast — fits busier desktop backgrounds.',
      materialSolidDayNight: 'Day / Night',
      reduceTransparency: 'Reduce transparency',
      reduceTransparencyDesc:
        'Turns off glass blur and solidifies panels to cut compositing cost; true transparency outside the window is unaffected.'
    },
    memory: {
      tabAriaLabel: 'Memory type',
      presetLabel: 'Current preset',
      tabActive: (count: string | number) => `Active · ${count}`,
      tabCandidate: (count: string | number) => `Candidates · ${count}`,
      tabInvalidated: (count: string | number) => `Invalidated · ${count}`,
      tabExpired: (count: string | number) => `Expired · ${count}`,
      userProfileHint: (count: string | number) => `${count} explicitly configured profile entries`,
      profile: {
        title: 'User profile',
        hint: 'Personal details collected during onboarding; your companion keeps them in mind. Edit or fill them in anytime — after deleting, the entry is forgotten but can be added back later.',
        set: 'Set',
        unset: 'Not set',
        add: 'Add',
        fields: {
          user_call_name: 'Call name',
          user_gender: 'Gender',
          user_birthday: 'Birthday',
          user_hobbies: 'Hobbies',
          user_freeform: 'Notes'
        }
      },
      maintenanceHint:
        'Your companion reviews evidence and maintains memory autonomously. Only active, unexpired memories inform interactions.',
      basis: {
        explicit: 'Explicit statement',
        inferred: 'Inference',
        observed: 'Observation',
        system: 'System record'
      },
      usage: { contextual: 'Contextual', background: 'Enduring context' },
      stance: { supports: 'Supporting evidence', opposes: 'Counterevidence' },
      evidence: 'View evidence',
      expires: 'Valid until',
      updated: 'Updated',
      loading: 'Loading…',
      loadFailedHint: 'Failed to load',
      loadFailedToast: 'Failed to load long-term memory',
      saveFailedHint: 'Save failed, rolled back',
      saveFailedToast: 'Failed to save memory',
      deleteFailedHint: 'Delete failed, rolled back',
      deleteFailedToast: 'Failed to delete memory',
      charCount: (count: number) => `${count} character${count === 1 ? '' : 's'}`,
      empty: 'No memories here. Ordinary conversation does not need to become long-term memory.',
      saved: 'Saved',
      saving: 'Saving…',
      delete: 'Delete'
    }
  },

  skills: {
    tabSkills: 'Skills',
    tabToolsets: 'Toolsets',
    all: 'All',
    searchSkills: 'Search skills…',
    searchToolsets: 'Search toolsets…',
    loading: 'Loading capabilities…',
    noSkillsTitle: 'No skills found',
    noSkillsDesc: 'Try a broader search or another category.',
    loadFailedTitle: 'Failed to load skill list',
    loadFailedDesc: 'Try again later; if it keeps failing, reinstall the app to restore the built-in skills.',
    noToolsetsTitle: 'No toolsets found',
    noToolsetsDesc: 'Try a broader search term.',
    noDescription: 'No description available.',
    toolsetsEnabled: (enabled: number, total: number) => `${enabled}/${total} toolsets enabled`,
    toolsetsLoadFailed: "Couldn't load toolsets",
    toolsetsLoadFailedDesc: 'Please try again later.',
    toolsetsSaveFailed: "Couldn't save the toolset toggle"
  },

  toolsets: {
    browser_automation: {
      label: 'Browser automation',
      description: 'Multi-backend browser capabilities: navigation, clicks, snapshots, cookies/CDP.'
    },
    file_operations: { label: 'File operations', description: 'Read/write, patches, directory and file search.' },
    terminal: { label: 'Terminal', description: 'Run shell commands locally or on an SSH host.' },
    code_execution: { label: 'Code execution', description: 'Sandboxed Python execution with restricted calls.' },
    process_management: { label: 'Process management', description: 'Background process startup and tracking.' },
    skills_system: { label: 'Skill system', description: 'List, view and manage Skill contents.' },
    memory: { label: 'Memory', description: 'Write, retrieve and delete long-term memories.' },
    web_tools: { label: 'Web tools', description: 'Web search and page content extraction.' },
    image_generation: { label: 'Image generation', description: 'Generate images via cloud models.' },
    messaging: { label: 'Messaging', description: 'Send messages via Webhook.' },
    scheduled_tasks: { label: 'Scheduled tasks', description: 'Cron triggers and periodic scheduling.' },
    agent_delegation: { label: 'Sub-agent delegation', description: 'Spawn sub-sessions and sub-agents.' },
    computer_use: {
      label: 'Desktop control',
      description: 'Operate desktop apps with screenshots, mouse and keyboard.'
    },
    media_analysis: { label: 'Media analysis', description: 'Image analysis.' },
    system_awareness: {
      label: 'System awareness',
      description: 'OS state sensing: idle time, screen lock, focused window, fullscreen and cursor position.'
    }
  },

  errors: {
    boundaryTitle: 'Something went wrong',
    boundaryDesc: 'This view hit an unexpected error. Your conversations and settings are safe.',
    reloadWindow: 'Reload window'
  },

  ui: {
    lightbox: {
      backdropAria: 'Click backdrop to close',
      closePreview: 'Close preview',
      zoomIn: 'Zoom in',
      zoomOut: 'Zoom out',
      zoomReset: 'Reset',
      zoomPercent: (n: number) => `${n}%`,
      zoomHint: 'Scroll to zoom · drag to pan · double-click to zoom in/out'
    },
    search: {
      clear: 'Clear search'
    }
  },

  chat: {
    defaultSessionTitle: 'Daily chat',
    inputPlaceholder: 'Say something, or drag a file over',
    typing: 'Typing...',
    queued: 'Received, waiting to be processed',
    openMainSessionFailed: 'Could not open daily chat',

    filesReceived: (count: number) => `${count} file${count === 1 ? '' : 's'} received`,
    attachmentsAdded: (count: number) => `${count} attachment${count === 1 ? '' : 's'} added`,
    filesHandoffFailed: "Couldn't hand over the files. Drop them again.",
    sendFailed: 'Send failed',

    media: {
      generating: 'Generating video…',
      generationFailed: 'Generation failed',
      resultUnknown: 'Generation result is not yet confirmed',
      imageLoading: 'Loading image…',
      loadFailed: "Couldn't load media",
      reviewHint: 'Check the character’s appearance. This media is only a preview for now.',
      reviewAccept: 'Confirm and accept',
      reviewReject: 'Reject',
      reviewRejected: 'This media was not accepted.',
      reviewLoadError: 'Could not load review status. Try again later.',
      reviewAcceptError: 'Could not accept this media. Try again later.'
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

    edit: {
      label: 'Edit last message',
      cancel: 'Cancel editing (Esc)',
      send: 'Send changes and regenerate reply',
      hint: 'Regenerate the reply and keep the original attachments',
      stale: 'The conversation has changed. Wait for the reply or select the last message again.',
      failed: 'Could not edit the message. Your changes have been kept.'
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
      selectFolder: 'Select folder',
      openFailed: "Couldn't open the file picker"
    },

    voice: {
      retry: 'Voice unavailable. Tap to retry',
      play: 'Play voice',
      stop: 'Stop voice playback',
      collapse: 'Collapse',
      showTranscript: 'Show transcript'
    },

    voiceInput: {
      busy: 'The voice service is busy. Try again shortly.',
      notRecognized: "Couldn't recognise any speech. Try again or type instead.",
      micUnavailable: "Couldn't record from the microphone"
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

    emptyHint: 'Say something, or send a file, image or video',

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
      completed: (count: number) => `Completed ${count} step${count === 1 ? '' : 's'}`,
      genericName: 'Tool'
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
        minimal: 'Minimal',
        low: 'Low',
        medium: 'Medium',
        high: 'High',
        xhigh: 'X-High',
        max: 'Max',
        ultra: 'Ultra'
      },
      reasoningHints: {
        high: 'Deep reasoning — suited for complex plans and proofs.',
        low: 'Quick thinking — best for simple Q&A.',
        medium: 'Standard reasoning — good for development and debugging.',
        none: 'Direct answers — fastest response.',
        minimal: 'Barely-there thinking — just above off, for the simplest prompts.',
        xhigh: 'Extra-deep reasoning — for research-grade problems and hard proofs.',
        max: 'Near-ceiling reasoning — think as hard as the provider allows.',
        ultra: 'Highest product level; falls back to the provider’s top supported level automatically.'
      },

      contextCapsuleAria: 'View context usage and compression',
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

      manualCompressFailed: 'Failed to manually compress context',

      thresholdSliderHeading: 'Context window load',
      thresholdSliderSubheading: '/ auto-compress line',
      thresholdSliderTriggerLabel: 'Trigger point:',
      thresholdSliderAria: 'Auto-compress threshold (drag left/right to adjust)',
      thresholdSliderMin: '30% tighter compression',
      thresholdSliderHint: 'Drag the handle left/right to adjust the threshold',
      thresholdSliderMax: '100% full capacity',
      thresholdSliderDescription:
        'When context reaches the chosen ratio, older history is automatically summarised in the background to free up space and keep the conversation coherent.',

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

      statusHealthy: 'Plenty of context headroom for smooth conversation.',
      statusWarning: 'Context is filling up — nearing the auto-compress threshold; tidy anytime.',
      statusCritical: 'Context is heavy — past the auto-compress line; compress now.',

      compressing: 'Compressing earlier messages…',
      compressAction: 'Compress context now',

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
      forbidden: 'Fixed conversations cannot be renamed',
      failed: 'Rename failed, restored previous name'
    }
  },

  companion: {
    statusBusy: 'Busy',
    statusCompanion: 'With you',
    menu: {
      quietOff: (minutes: number) => `End quiet (${minutes} min left)`,
      quietOn: (minutes: number) => `Quiet for ${minutes} min`,
      resetPosition: 'Reset position',
      hide: 'Hide companion',
      activate: 'Activate / sign in'
    },
    egg: {
      preparing: 'Preparing your companion’s actions…',
      generating: 'Generating your companion’s actions…',
      failed: 'Action generation failed',
      unavailable: 'Actions are not ready yet',
      retry: 'Retry',
      openWardrobe: 'Open wardrobe',
      ariaLabel: 'Companion egg'
    }
  },

  living: {
    title: 'Living Space',
    goToWorkbench: 'Open Workbench',
    rail: {
      chat: 'Chat',
      moments: 'Moments',
      diary: 'Diary',
      wardrobe: 'Wardrobe',
      channels: 'Channels',
      scene: 'Scene',
      settings: 'Settings',
      companionFallback: 'Companion',
      avatarMood: (name: string) => `${name}'s mood`
    },
    moments: {
      loading: 'Flipping through the album…',
      empty: 'No moments captured yet.',
      loadFailed: "Couldn't load moments",
      noTitle: 'Untitled',
      commentPlaceholder: 'Write a comment…',
      commentSend: 'Send',
      commentSending: 'Sending…',
      commentDelete: 'Delete',
      commentFailed: "Couldn't post the comment",
      commentDeleteFailed: "Couldn't delete the comment",
      userLabel: 'Me',
      kindLabels: {
        emotion: 'Feeling',
        scene: 'Scene',
        together: 'Together'
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
      loadFailed: "Couldn't load the diary",
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
    outfit: {
      empty: 'No outfits yet. Design your first one to get started.',
      wardrobeHeading: 'Outfits',
      outfitCount: (count: number) => (count === 1 ? '1 outfit' : `${count} outfits`),
      zoomOutfit: (name: string) => `View ${name} full size`,
      policyLabel: 'Companion-driven outfits',
      policyDesc: 'When enabled, the companion may choose a ready look or design a new one overnight.',
      policyStatusLocked: 'Locked',
      policyStatusUnlocked: 'Allowed',
      policyToggleAria: 'Allow companion-driven outfit changes',
      policyFailed: "Couldn't save the outfit setting",
      deleteFailed: "Couldn't delete the outfit",
      confirmFailed: "Couldn't confirm the outfit. Try again.",
      wearing: 'Wearing',
      statusLabels: {
        draft: 'Draft',
        failed: 'Confirm failed',
        expired: 'Expired'
      } as Record<string, string>,
      actions: {
        continueDesign: 'Continue design',
        continueDesignTitle: 'Refine and redraw this outfit',
        retry: 'Retry',
        delete: 'Delete',
        deleteTitle: 'Delete this outfit'
      },
      imageAlt: 'Outfit portrait',
      previewPlaceholderDesigning: 'Describe an outfit and the preview will appear here',
      previewHint: 'Swap clothing, hairstyle and accessories; the face and body shape stay the same.',
      generating: 'Generating…',
      startAction: 'Design an outfit',
      designIntro:
        'Describe the outfit you want — e.g. "sailor uniform swapped for an off-white knit cardigan and a brown long skirt, hair tied in a low ponytail". You can also attach a reference image. One outfit per hour at most.',
      confirmAction: 'Confirm',
      processing: 'Processing…',
      discard: 'Discard draft',
      confirmHint: 'Once confirmed, the outfit is ready. Open its actions to generate and wear it.',
      refImageAttached: 'Reference image attached',
      refImageAlt: 'Reference image',
      zoomRefImage: 'View reference image',
      retryLast: 'Retry last generation',
      placeholderRefining: 'What would you like to tweak? Keep describing… (Enter to send)',
      placeholderInitial: 'Describe an outfit… (Enter to send, Shift+Enter for newline)',
      attachImage: 'Attach reference image',
      attachImageTitle: 'Attach reference image (optional, first generation only)',
      send: 'Send',
      design: {
        byReference: '(Designed from the reference image)',
        pickReferenceTitle: 'Choose an outfit reference image',
        drafted:
          'Draft ready — see the preview above. Keep describing to refine it, or confirm to add it to the wardrobe.',
        refined:
          'Refined as requested — see the preview above. Keep describing to refine further, or confirm to add it to the wardrobe.',
        previewFailed: 'The draft was created, but its preview failed to load. Reopen the draft from the wardrobe.',
        timedOut:
          'Timed out waiting for the result; generation may still be running. Check the draft in the wardrobe before retrying.',
        generateFailed: "Couldn't generate the outfit. Try again later.",
        resumeReady: 'Keep refining this draft, or confirm to add it to the wardrobe.',
        resumePreviewMissing:
          "The draft preview hasn't loaded yet. Keep describing to refine it, or reopen it from the wardrobe later."
      }
    },
    appearance: {
      videoReady: (version: number, clips: number) => `Action pack ready (version ${version}, ${clips} actions)`,
      actionsHeading: 'Actions',
      actionCount: (count: number) => (count === 1 ? '1 action' : `${count} actions`),
      videoActionReady: 'Ready',
      videoActionGenerating: 'Generating',
      videoActionFailed: 'Failed',
      videoActionResultUnknown: 'Outcome unknown',
      videoActionResultUnknownHint: 'Submission outcome unknown. Check the generation task before generating again.',
      videoActionReview: 'Needs review',
      videoActionReviewHint: 'Preview and check the character in Settings.',
      videoActionCompactHint: 'Select to inspect',
      videoActionOnDemandShort: 'On demand',
      videoActionSearch: 'Search actions',
      videoActionNoMatch: 'No matching actions',
      videoActionEmpty: 'Select an action to see its preview and controls.',
      videoActionCancel: 'Cancel',
      videoActionRetryWaitHint: 'Other actions are still generating. You can retry this action when the pack finishes.',
      videoOutfitNotReady: 'This outfit has no action pack yet. Generate one to preview and wear it.',
      videoPacksLoadFailed: "Couldn't load action packs",
      videoSelectOutfit: 'Return to the wardrobe and choose an outfit to see its actions.',
      videoBackToOutfits: 'Back to wardrobe',
      videoLoading: 'Loading actions for this outfit…',
      videoIdle: 'Idle',
      videoWalkLeft: 'Walk left',
      videoWalkRight: 'Walk right',
      videoDrag: 'Suspended',
      videoPeekLeft: 'Peek left',
      videoPeekRight: 'Peek right',
      videoPeekCalibrationFailed: 'Layout unavailable',
      videoPeekCalibrationFailedHint:
        'The video is ready, but its peek layout did not pass validation. Regenerate to try again.',
      videoResume: 'Resume existing work',
      videoActivate: 'Wear this outfit',
      videoIdentityReviewHint: 'Check this action pack’s appearance; the current video keeps playing.',
      videoIdentityReviewActivate: 'Confirm appearance and wear',
      videoVersions: 'Action pack versions',
      videoVersion: (version: number) => `Version ${version}`,
      videoActive: 'Active',
      videoActionOnDemand: 'System actions are filled in by the service when needed; you can also generate here.',
      videoGenMissingAction: 'Generate this action',
      videoRedoAction: 'Regenerate this action',
      videoFeedback: 'Describe the desired movement (optional)',
      videoGenAction: 'Generate action pack',
      videoRegenAction: 'Regenerate action pack',
      videoRegenTitle: 'Regenerate action pack',
      videoRegenBody:
        'The actions will be re-scripted and generated from the current outfit reference; the pack activates automatically when ready.',
      videoGenStagePose: 'Preparing the action pose…',
      videoGenStageScript: 'Writing the action script from the character’s personality…',
      videoGenStageSubmit: 'Submitting the video generation task…',
      videoGenStageGenerate: 'Generating the video, this usually takes a few minutes…',
      videoGenStageDownload: 'Downloading the generated video…',
      videoGenStageProcess: 'Removing the background, checking loops and encoding transparent clips…',
      videoGenStagePublish: 'Publishing the action pack…',
      videoGenStageDefault: 'Generating the action pack…',
      videoGenBusy: 'An action pack task is already running. Wait for it to finish.',
      videoGenRequestFailed: 'Action pack request failed. Please retry shortly.',
      videoActivateFailed: 'Could not wear this outfit. Please retry shortly.',
      videoGenHint:
        'Generates actions from the outfit reference and personality: script → video → matting, about a few minutes. Preview or regenerate each action; the current look stays until it is ready.',
      companionSize: 'Companion size',
      companionSizeHint: 'Default display scale of the sprite on the desktop.',
      scaleAria: 'Companion size'
    },
    scene: {
      intro:
        'Scenes show where your companion is and what they are doing. Scene clothing is independent of the wardrobe. Save scenes and use them again.',
      referenceHint:
        'A reference guides the environment and composition; people in it never appear in the result. To use its clothing, describe the complete outfit in the outfit description.',
      referenceLabel: 'Scene reference',
      chooseReference: 'Choose reference',
      replaceReference: 'Replace reference',
      removeReference: 'Remove reference',
      referenceError: 'Choose a valid PNG, JPEG, WebP or GIF image.',
      notesLabel: 'Environment and activity',
      notesPlaceholder: 'For example: an evening walk along the beach.',
      outfitLabel: 'Outfit description (optional)',
      outfitPlaceholder: 'For example: a white short-sleeved shirt, blue shorts, white sneakers and a straw hat.',
      outfitHint:
        'Leave blank to keep the wardrobe’s active outfit; filling this in replaces the full look for this scene — include clothing, colors, hairstyle, footwear and accessories.',
      noScene: 'No active scene yet',
      pendingOverlay: 'Preparing scene…',
      pendingOverlayHint:
        'The image and description are saved to your library. Your current background stays in place.',
      waitingUploadOverlay: 'Waiting for a scene image',
      waitingUploadHint: 'The uploaded image is saved first. Choose Activate when ready.',
      waitingUploadAction: 'Upload image',
      generateButton: 'Create scene',
      generatingButton: 'Creating…',
      historyTitle: 'Scene library',
      emptyLibrary: 'Your scene library is empty. Create or upload a scene to set up your living space.',
      emptySearch: 'No scenes match this search.',
      loading: 'Loading scene library…',
      loadFailed: 'Could not load the scene library. Try again.',
      detailLoadFailed: 'Scene details are unavailable. The scene may have been deleted.',
      openDetails: 'View scene details',
      backToLibrary: 'Back to scene library',
      newSceneTitle: 'New scene',
      searchButton: 'Search',
      imageRegenerate: 'Regenerate image',
      saveAndRegenerate: 'Save and regenerate',
      imageRegenerating: 'Regenerating the image. The current scene stays available.',
      imageRegenerationFailed: 'Regeneration failed. The previous image is still saved.',
      historyAltFallback: 'Scene image',
      historyCurrentLabel: 'Current',
      historyRollbackLabel: 'Activate',
      viewOriginal: 'View full image',
      historyDeleteLabel: 'Delete',
      historyDeleteConfirmTitle: 'Delete this scene?',
      historyDeleteConfirmDescription: 'The image and scene information will be permanently removed from your library.',
      policyLabel: 'Autonomous scene changes',
      policyDesc:
        'Locking prevents autonomous creation and switching. You can still create and switch scenes manually on the scene page. Nightly activity also follows its own policy.',
      policyStatusLocked: 'Locked',
      policyStatusUnlocked: 'Allowed',
      policyToggleAria: 'Allow autonomous scene creation and switching',
      savedHint: 'Saved first. Activate manually when ready.',
      slow: 'Still processing. Check for an update.',
      refresh: 'Check status',
      cancelTask: 'Cancel task',
      search: 'Search titles or descriptions',
      titleLabel: 'Short title',
      descriptionLabel: 'Description',
      save: 'Save',
      cancel: 'Cancel',
      edit: 'Edit details',
      retryAnalysis: 'Retry description',
      previous: 'Previous',
      next: 'Next',
      untitled: 'Untitled scene',
      needsDescription: 'An image and description are required before activation.',
      statuses: {
        pending: 'Preparing',
        ready: 'Saved',
        description_failed: 'Description needed',
        failed: 'Creation failed',
        cancelled: 'Cancelled'
      }
    },
    sceneBackdrop: {
      pendingText: 'Setting up the scene'
    },
    toasts: {
      sceneReady: 'Saved to scene library',
      sceneImageRegenerated: 'Scene image updated',
      sceneRegenerateFailed: 'Scene operation failed. Check the details and retry.',
      sceneRollbackSuccess: 'Scene activated',
      sceneDeleteFailed: 'Delete failed — please retry shortly',
      sceneDeleteSuccess: 'Scene deleted',
      sceneLockFailed: "Couldn't update the lock setting",
      sceneLocked: 'Autonomous scene changes locked',
      sceneUnlocked: 'Autonomous scene changes allowed'
    }
  },

  presets: {
    names: {
      companion: 'Companion',
      developer: 'Engineer',
      product_manager: 'Product manager',
      copywriter: 'Copywriter',
      language_teacher: 'Language teacher'
    },
    descriptions: {
      developer:
        'Technical explanations, design, coding, debugging and code review, with attention to project constraints and verification.',
      product_manager:
        'Clarifies user problems and goals, weighs options, and turns them into actionable, verifiable product requirements.',
      copywriter:
        'Drafts, polishes and organizes copy, emails and meeting notes, keeping the original meaning and fitting the audience and occasion.',
      language_teacher:
        'Explains, corrects and practices at your level and toward your goals, with faithful, natural translations.'
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
      label: 'Run trace',
      expandAria: 'Expand run trace',
      collapseAria: 'Collapse run trace',
      collapseTitle: 'Collapse',
      subtitle: (steps: number) => `${steps} steps · this round`,
      idleSubtitle: 'Idle',
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
