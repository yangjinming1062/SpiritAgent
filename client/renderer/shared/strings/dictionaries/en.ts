import type { Dictionary } from './zh'

export const dict: Dictionary = {
  brand: {
    name: 'SpiritAgent',
    fullName: 'SpiritAgent Desktop'
  },

  common: {
    actionVariants: {
      title: 'Creation progress by view',
      readyWithStatus: (status: string) => `Playable · ${status}`,
      remakeFailed: 'Remake failed',
      kinds: { window: 'Outfit', desktop: 'Desktop background' },
      statuses: {
        not_generated: 'Not created',
        queued: 'Waiting to start',
        processing: 'Creating',
        ready: 'Playable',
        review_pending: 'Awaiting review',
        failed: 'Creation failed',
        blocked: 'Creation unavailable',
        disabled: 'Disabled',
        excluded: 'Removed'
      }
    },
    videoQueue: {
      waiting: 'Video queued',
      rate_limited: 'Service busy, waiting to retry',
      blocked: 'Waiting for generation results to be verified'
    },
    done: 'Done',
    moreActions: 'More actions',
    creationDetails: 'Creation details',
    useForDesktopLife: 'Use in this desktop background',
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
    pageRestoreFailed: 'Could not restore the page',
    pickDate: 'Pick a date',
    datePicker: { year: 'Year', month: 'Month', day: 'Day' },
    imagePick: {
      tooLarge: 'This image is too large. Choose a smaller one.',
      readFailed: "Couldn't read the selected image. Try again."
    },
    processing: 'Processing…'
  },

  creation: {
    working: 'Creating',
    review: 'Created, awaiting confirmation',
    success: 'In use',
    paused: 'Creation paused',
    cancelled: 'Creation cancelled',
    unknown: 'Verifying the creation result',
    slow: 'This is taking longer. We are still waiting for the result.',
    preserveCurrent: 'Your current content remains available until you confirm the replacement.',
    canLeave: 'You can leave this page and return later to check progress.',
    inputLocked: 'These inputs cannot be changed during creation. You can still read them.',
    retryLoad: 'Reload preview',
    previewFailed: 'The preview could not load. Your result is still saved.',
    imageLoadFailed: 'Image failed to load',
    checkResult: 'Check existing result'
  },

  selfSource: {
    guidanceAction: 'Need to create an image? Get the prompt and references',
    open: 'Use your own image',
    openTitle: 'Use your own image',
    hint: 'Upload a finished image directly, or expand the prompt and references to create it elsewhere. Check the identity, visual style and framing before adopting.',
    referenceTitle: 'Reference images (provide with the prompt)',
    referenceHint:
      'Hand the reference image(s) and prompt to your image tool; use thumbnail actions to copy or save. Click to enlarge.',
    referenceMissing:
      'No reference image is available yet. Complete your companion appearance, then return here to continue.',
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
    aiTitle: 'Adjust image',
    edit: 'Refine',
    regenerate: 'Regenerate',
    generate: 'Generate',
    editHint: 'Changes only what you describe; everything else stays',
    editRequiresFeedback: 'Describe the changes first to refine',
    regenerateHint: 'Redraws the whole image from the references and your brief',
    groupHint: 'Refine changes only what you describe; Regenerate redraws the whole image from the references.',
    selfTitle: 'More options',
    selfHint: 'Upload your own image, or view creation references.'
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
      serviceUnavailable: 'AI is temporarily unavailable. Try again later. Your saved content is still available.'
    },
    voice: {
      invalidTitle: 'Voice no longer available',
      invalidMessage: (name: string) =>
        `The voice "${name}" you previously selected is unavailable, so the default voice is being used for now. Choose another in Companion › Voice.`,
      invalidAction: 'Choose a voice'
    },
    system: {
      view: 'View',
      scheduledTask: 'Scheduled task',
      imageReady: 'Image ready — click to view',
      videoReady: 'Video ready — click to view'
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

  voice: {
    title: 'Voice',
    intro: 'Choose a preset voice, or design or clone a voice for your companion.',
    noTtsConfigured: 'Voice is temporarily unavailable. You can keep chatting with text and choose a voice later.',
    noVoicesForLanguage: 'No voice is currently available for this language. You can keep chatting with text.',
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
    designTab: 'Design a voice',
    designPlaceholder: 'Describe the voice you want…',
    designGenerate: 'Generate preview',
    designFailed: 'Voice generation failed. Please try again.',
    methodAria: 'Voice source',
    fixedHeading: 'Choose a voice',
    cloneHeading: 'Clone a voice',
    useThisVoice: 'Use this voice',
    saving: 'Saving…',
    showRecommended: 'Show recommended voices',
    showAll: 'Browse all voices',
    designIntro: 'Let AI design a voice from your companion’s profile, or write and edit the description yourself.',
    autoDesign: 'AI design',
    designingDescription: 'Designing the voice description',
    generatingTrial: 'Generating the voice preview',
    trialReady: 'Preview ready, awaiting your choice',
    trialReadyHint: 'Listen again and use it when you are happy, or keep adjusting the voice.',
    promptLabel: 'Voice description',
    designSteps: ['Design description', 'Generate preview', 'Listen and use'],
    adjustToggle: 'Adjust voice',
    adjustAndPreview: 'Adjust and preview',
    sampleReady: 'Reference audio ready',
    sampleReadyHint: 'Play the original to check it, then clone and preview.',
    cancelling: 'Cancelling this creation',
    cancelFailed: 'Cancellation has not been confirmed. Try cancelling again. This operation will not be resubmitted.',
    retryCancel: 'Retry cancellation',
    cancelUnknown: 'Stop and check this creation',
    generationUnknown: 'The creation result has not arrived. Stop this operation before starting another.',
    confirmUnknown: 'Saving has not been confirmed. Check the current voice first.',
    checkCurrent: 'Check current voice',
    retryConfirm: 'Retry confirming this voice',
    saveFailed: 'Could not save the voice. Your preview is still available.',
    playingPreview: 'Preview playing',
    stopPreview: 'Stop preview',
    designHint: 'Generate a preview after editing. Your current voice changes only when you confirm.',
    autoDesignUnavailable: 'AI design is not configured. You can still write a description and preview it.',
    designUnavailable: 'Voice design is not configured. Choose a preset or clone a voice.',
    designGuide: 'Voice description tips',
    feedbackLabel: 'Feedback',
    feedbackPlaceholder: 'What would you change? For example: softer, while keeping the mature tone…',
    savedVoice: 'Saved voice',
    savedUnavailable: 'This voice is currently unavailable. Check your voice configuration or choose another voice.',
    cloneIntro:
      'Record or upload clear, natural speech from one speaker. Aim for 5–30 seconds; maximum 60 seconds and 24 MiB.',
    cloneUnavailable: 'Voice cloning is not configured. Choose a preset or design a voice.',
    startRecording: 'Start recording',
    stopRecording: 'Stop recording',
    rerecord: 'Record again',
    uploadAudio: 'Upload audio',
    recordingHint: 'Recording… Stops automatically after 60 seconds.',
    uploadingAudio: 'Processing audio…',
    sampleDuration: (seconds: number) => `Reference audio · ${seconds}s`,
    previewOriginal: 'Play original',
    clonePreview: 'Clone and preview',
    emptyAudio: 'The audio is empty. Record or upload another sample.',
    audioTooLarge: 'Audio files must be no larger than 24 MiB.',
    uploadFailed: 'Audio upload failed. Please try again.',
    micUnavailable: 'Could not use the microphone. Check your system permissions or upload audio.',
    previewFailed: 'Could not play the preview. Please try again.',
    onboardingHint: 'Choose a voice and confirm. You can adjust it later in Companion › Voice.',
    describePreference: 'Describe the voice you want',
    continueText: 'Use text for now'
  },
  settings: {
    nav: {
      about: 'About',
      theme: 'Theme',
      interaction: 'Interaction',
      navAriaLabel: 'Settings section navigation',
      persona: 'Persona',
      shortcuts: 'Shortcuts',
      voice: 'Voice',
      companion: 'Companion'
    },
    companion: {
      heading: 'Companion display',
      hint: 'Adjust the companion scale and position.',
      companionSize: 'Companion size',
      companionSizeHint: 'Default display scale of your companion.',
      scaleAria: 'Companion size',
      resetPosition: 'Reset companion position',
      resetPositionDesc: 'Move your companion back to its default position on the current display.'
    },
    shortcuts: {
      heading: 'Global shortcuts',
      intro:
        'Summon or hide the companion from anywhere on your system. Click a key field to record a new combination.',
      toggleVisibility: 'Hide / show companion',
      toggleVisibilityDesc: 'Quickly show or hide the companion window in window mode.',
      openMain: 'Show / hide main window',
      openMainDesc: 'Open or hide the main window; open or raise the main conversation in desktop mode.',
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
    remote: {
      heading: 'Mobile access',
      intro:
        'Scan with your phone to open your personal page. Chat, read updates and manage your companion. Local tools are available while the computer is online.',
      connect: 'Connect phone',
      refreshCode: 'New QR code',
      cancelCode: 'Cancel QR code',
      copyLink: 'Copy link',
      copied: 'Copied',
      copyFailed: 'Could not copy the link',
      connectFailed: 'Could not create a QR code',
      cancelFailed: 'Could not cancel the QR code',
      qrPrompt: 'Scan with your phone camera and open in the browser',
      expiry: (seconds: number) =>
        `QR code expires in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`,
      expired: 'QR code expired. Create a new one.',
      paired: 'Phone connected',
      cancelled: 'QR code cancelled',
      pollFailed: 'Connection status unavailable. Please retry.',
      devices: 'Authorized devices',
      devicesIntro: 'Authorization lasts 30 days. Revoking it signs the device out immediately.',
      empty: 'No phones connected yet',
      refresh: 'Refresh',
      loadFailed: 'Could not load devices',
      lastSeen: 'Last seen',
      expires: 'Expires',
      revoke: 'Revoke access',
      revokeAll: 'Revoke all access',
      revokeTitle: 'Revoke device access?',
      revokeAllTitle: 'Revoke access for all devices?',
      revokeDescription:
        'Phones will be signed out and their active conversations and local calls stopped. Completed actions will remain.',
      revokeFailed: 'Could not revoke access'
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
      intro: 'Configure the local runner. Changes are saved automatically and take effect after the runner restarts.',
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
        'Configure default parameters, reasoning depth, and context compression for all conversations without their own settings. Changes are saved automatically.',
      loading: 'Loading…',
      saveFailed: 'Could not save inference & chat settings.',
      saved: 'Inference & chat settings saved.',
      loadFailed: "Couldn't load inference & chat settings",
      agentDefaults: {
        heading: 'Agent defaults',
        intro: 'Applies to all conversations without their own parameter overrides.',
        reasoningEffort: 'Reasoning depth',
        reasoningEffortDesc:
          "How hard the model reasons each turn. Off disables reasoning; Minimal through Ultra go progressively deeper. Above the provider's ceiling, its highest supported level is used.",
        backgroundReview: 'Background memory consolidation',
        backgroundReviewDesc: 'Extracts memories from conversation history in both system and ordinary conversations.',
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
          'Default generation temperature for all conversations without their own override. Lower values are more deterministic, higher values more creative.',
        titleTemperature: 'Title generation temperature',
        titleTemperatureDesc:
          'Temperature for auto-generating session titles from the first turn. Keep low for accurate summarisation.',
        compressionTemperature: 'Context compression temperature',
        compressionTemperatureDesc:
          'Temperature for summarising long conversation histories. Recommended to keep at 0 for factual fidelity.'
      }
    },
    interaction: {
      title: 'Companionship',
      moreOptions: 'More companionship options',
      intro: 'How the companion responds and when it may interrupt you.',
      voiceHeading: 'Responses & recording',
      responsePreference: 'Response preference',
      responsePreferenceText: 'Prefer text',
      responsePreferenceVoice: 'Prefer voice',
      responsePreferenceDesc:
        'Tell your companion which format you usually prefer. They choose based on the situation.',
      recording: 'Recording length cap',
      recordingDesc: 'Maximum length of a single voice recording — auto-stops and sends at the limit.',
      recordingSecondsSuffix: 's',
      recordingSaveFailed: 'Failed to save recording duration',
      recordingLoadFailed: "Couldn't load the recording limit. Try again later.",
      tierHeading: 'How would you like to spend time together?',
      tierHint: "Only constrains the companion's proactive behaviour — your actions are never restricted.",
      tierAriaLabel: 'Disturbance tier',
      tiers: {
        still: { label: 'Only reply to me', hint: 'Quiet company until you start a conversation' },
        normal: { label: 'Occasional greetings', hint: 'A gentle text greeting while staying in place' },
        autonomous: { label: 'Free to explore', hint: 'May speak, move around, and interact naturally' }
      },
      smartHeading: 'Everyday life',
      smartHint: 'Let your companion share daily life and discover things you both enjoy.',
      idleAffect: 'Expressions and activities while idle',
      idleAffectAria: 'Idle situational mood',
      idleAffectDesc:
        'When free to explore, the companion beside your window can react naturally after a long idle period.',
      autonomy: 'Choose where to move',
      autonomyAria: 'Autonomous space decisions',
      autonomyDesc: 'When free to explore, the companion beside your window can choose places to stay and move to.',
      postsEnabled: 'Post creation',
      informationDiscovery: 'Discover new content',
      informationDiscoveryDesc:
        'Let your companion explore recent content related to your interests overnight and share when it fits.',
      postsEnabledDesc:
        'Let your companion create text, image, video or audio posts at any time, including while offline. All types share the posting limit.'
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
      title: 'About your companion',
      intro: 'Review the name, personality, and your relationship. Save your edits or cancel to discard them.',
      sectionTitle: 'Persona',
      sectionMemory: 'Long-term memory',
      mediaReviewTitle: 'Action videos to confirm',
      mediaReviewLoadError: 'Could not load actions to confirm. Refresh to try again.',
      mediaReviewEmpty: 'No actions need confirmation right now.',
      mediaReviewRefresh: 'Refresh',
      fullbodyReference: {
        title: 'Your companion’s full appearance',
        hint: 'AI uses the portrait you just confirmed to create the full appearance. When it looks right, continue to choose a voice.',
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
      hintSaved: 'Saved',
      hintSaveFailed: 'Save failed, please try again'
    },
    theme: {
      heading: 'Theme',
      hint: "Themes apply to the main window, whisper and desktop mode interfaces; the companion's appearance is unaffected.",
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
        'Enables liquid glass and frosted translucency — the scene flows with light, and the companion stays airy in window mode.',
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
      presetLabel: 'Whose memories would you like to see?',
      tabActive: (count: string | number) => `In use · ${count}`,
      tabCandidate: (count: string | number) => `To organize · ${count}`,
      tabInvalidated: (count: string | number) => `Disabled · ${count}`,
      tabExpired: (count: string | number) => `Expired · ${count}`,
      profile: {
        title: 'My details',
        hint: 'Personal details collected during onboarding; your companion keeps them in mind. Edit or fill them in anytime — after deleting, the entry is forgotten but can be added back later.',
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
      evidence: 'Details and evidence',
      expires: 'Valid until',
      updated: 'Updated',
      loading: 'Loading…',
      loadFailedHint: 'Failed to load',
      loadFailedToast: 'Failed to load long-term memory',
      saveFailedHint: 'Save failed. Try again.',
      saveFailedToast: 'Failed to save memory',
      deleteFailedHint: 'Could not forget this memory. Please retry.',
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
    replaceReply: 'Try another answer',
    replacingReply: 'Preparing another answer…',
    topic: {
      platforms: { x: 'X', xiaohongshu: 'Xiaohongshu', douyin: 'Douyin' },
      open: 'View original',
      unavailable: 'Original link unavailable',
      like: 'More like this',
      less: 'Less like this',
      undo: 'Click again to undo feedback',
      failed: 'Feedback unavailable. Click to retry.'
    },
    defaultSessionTitle: 'Daily chat',
    inputPlaceholder: 'Say something, or drag a file over',
    typing: 'Typing...',
    openMainSessionFailed: 'Could not open daily chat',

    filesReceived: (count: number) => `${count} file${count === 1 ? '' : 's'} received`,
    attachmentsAdded: (count: number) => `${count} attachment${count === 1 ? '' : 's'} added`,
    filesHandoffFailed: "Couldn't hand over the files. Drop them again.",
    connectionInterrupted: 'Connection interrupted. Replies will sync after reconnecting.',
    sendFailed: 'Send failed',

    media: {
      generating: 'Generating video…',
      generationFailed: 'Generation failed',
      resultUnknown: 'Generation result is not yet confirmed',
      imageLoading: 'Loading image…',
      loadFailed: "Couldn't load media",
      reviewHint: 'Preview this media and decide whether to accept it. It is only a preview for now.',
      reviewAccept: 'Confirm and accept',
      reviewReject: 'Reject',
      reviewRejected: 'This media was not accepted.',
      reviewLoadError: 'Could not load review status. Try again later.',
      reviewAcceptError: 'Could not accept this media. Try again later.'
    },

    copy: {
      label: 'Copy message',
      failed: 'Failed to copy message'
    },

    contextMenu: {
      label: 'Message actions',
      copySelection: 'Copy selected text'
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
      confirmTitle: 'Undo this message?',
      confirmDescription: 'This will also delete every message after it.',
      confirmLabel: 'Undo',
      extraImagesNotRestored: (count: number) =>
        `Only one image can be attached at a time; ${count} other image${count === 1 ? ' was' : 's were'} not restored`
    },

    edit: {
      label: 'Edit message',
      cancel: 'Cancel editing (Esc)',
      send: 'Send changes and regenerate reply',
      hint: 'Regenerate the reply and keep the original attachments',
      retainedImage: 'Original image, kept after sending',
      retainedVideo: 'Original video, kept after sending',
      confirmTitle: 'Continue and send the changes?',
      confirmDescription: 'This is not the last message. Sending the changes will delete every message after it.',
      confirmLabel: 'Send changes',
      stale: 'The conversation has changed. Wait for the reply or select this message again.',
      failed: 'Could not edit the message. Your changes have been kept.'
    },

    attachment: {
      pendingImageAlt: 'Image pending to send',
      loading: 'Loading…',
      sendingImage: 'Sending image…',
      addedImage: 'Image attached',
      removeImage: 'Remove attached image',
      uploading: 'Uploading…',
      uploadInterrupted: 'Upload interrupted. Please retry.',
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
      confirmTitle: (name: string) => `Run /${name}?`,
      confirmDescription: 'This will affect the message history.',
      confirmLabel: 'Run',
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
      retryShort: 'Retry',
      play: 'Play voice',
      pause: 'Pause playback',
      resume: 'Resume playback',
      unheard: 'Unheard',
      listened: 'Listened',
      paused: 'Paused',
      playing: 'Playing',
      loading: 'Loading',
      failed: 'Playback failed',
      progress: 'Playback progress',
      saveFailed: 'Could not save voice playback progress',
      controls: 'Voice controls',
      autoplay: 'Automatically play voice messages',
      restart: 'Play from beginning',
      retryPlayback: 'Retry playback',
      hideTranscript: 'Hide transcript',
      showTranscript: 'Show transcript'
    },

    voiceInput: {
      busy: 'The voice service is busy. Try again shortly.',
      notRecognized: "Couldn't recognise any speech. Try again or type instead.",
      micUnavailable: "Couldn't record from the microphone"
    },

    input: {
      assistantPlaceholder: 'Type a command, ask a question, or drag a file in…',
      readOnlyHint: 'Task conversation · read-only',
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
      stopPending: 'Stopping, awaiting confirmation',
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
      openPanel: 'Advanced conversation settings',
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
        medium: 'Standard reasoning — good for writing and language learning.',
        none: 'Direct answers — fastest response.',
        minimal: 'Barely-there thinking — just above off, for the simplest prompts.',
        xhigh: 'Extra-deep reasoning — for research-grade problems and hard proofs.',
        max: 'Near-ceiling reasoning — think as hard as the provider allows.',
        ultra: 'Highest product level; falls back to the provider’s top supported level automatically.'
      },
      contextUnknown: 'Not configured',
      reasoningOff: 'Off',

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
      resetConfirm: 'This conversation now follows global settings',
      compressSuccessMore: (count: number) => `Successfully tidied and compressed ${count} earlier messages`,
      compressNotNeeded: 'Not much history to compress yet',

      sessionScopeBadge: 'Current session',
      sessionScopeBadgeTitle: 'Overrides apply to this conversation. Reset to defaults to follow global settings.',
      closePanel: 'Close panel',

      statsUsed: 'Used',
      statsMax: 'Max capacity',
      statsPercent: 'Usage',
      statsTokens: 'Tokens',
      statsNodeAt: (pct: number) => `Threshold ${pct}%`,

      cacheUsageTitle: 'Last completed turn · cache hit rate',
      cacheUsagePending: 'No turn statistics in this run yet',
      cacheUsageUnavailable: 'Not provided by the provider',
      cacheUsagePartial: 'Partial data',
      cacheUsageTokens: (cached: string, input: string) => `Cached / input: ${cached} / ${input} tokens`,
      cacheUsageRequests: (reported: number, total: number) => `Reported requests: ${reported} / ${total}`,

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

      scopeNote: 'Overrides this conversation. Reset to follow global settings.',
      scopeNoteTitle: 'Overrides apply to this conversation. Reset to defaults to follow global settings.',
      resetButtonTitle: 'Clear this conversation’s overrides and follow global settings',
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
      forbidden: 'System conversations cannot be renamed',
      failed: 'Rename failed, restored previous name'
    }
  },

  companion: {
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
    rail: {
      chat: 'Chat',
      posts: 'Posts',
      diary: 'Diary',
      unread: 'Unread content',
      wardrobe: 'Wardrobe',
      remote: 'Remote',
      scene: 'Scene',
      settings: 'Settings',
      companionFallback: 'Companion',
      avatarMood: (name: string) => `${name}'s mood`
    },
    posts: {
      intro: 'Explore the moments your companion shares and leave a comment.',
      expand: 'Read more',
      collapse: 'Show less',
      videoRecoveryHeading: 'Video posts awaiting review',
      videoRecoveryHint:
        'The original video result or identity needs confirmation. These posts were not recreated or published. Review the video, then adopt or discard it. Adoption does not create narration.',
      videoIdentityReviewPending: 'Identity needs confirmation; not published automatically',
      videoRecoveryLoadFailed: 'Could not load videos awaiting review. Refresh the posts page.',
      videoRecoveryStatuses: {
        pending: 'The original video task is still pending',
        ready: 'A video is available; review it before adopting',
        failed: 'The original video task failed',
        unknown: 'The original video result is unconfirmed',
        discarded: 'Discarded'
      },
      videoQuery: 'Check original task',
      videoRefresh: 'Refresh',
      videoAdopt: 'Adopt and publish',
      videoDiscard: 'Discard',
      videoCleanupPending: 'Publication was discarded. Some local assets still need cleanup.',
      videoRetryCleanup: 'Retry cleanup',
      videoQueryFailed: 'Could not check the original video task. Try again later.',
      videoAdoptFailed: 'Cannot adopt this video now. Check the current appearance, post settings, and quota.',
      videoDiscardFailed: 'Discard did not complete. Try again later.',
      videoDiscardTitle: 'Discard this video post?',
      videoDiscardDescription:
        'This post will not be published. Its exclusive local video will be removed. The provider task may still be running, and costs already incurred will not be refunded.',
      loadMore: 'Load more',
      replyPending: 'Replying…',
      replyRetry: 'Reply failed · Retry',
      replyRetryFailed: 'Retry failed. Please try again later.',

      loading: 'Flipping through the album…',
      empty: 'No posts captured yet.',
      loadFailed: "Couldn't load posts",
      noTitle: 'Untitled',
      commentPlaceholder: 'Write a comment…',
      commentSend: 'Send',
      commentSending: 'Sending…',
      commentDelete: 'Delete',
      commentFailed: "Couldn't post the comment",
      commentDeleteFailed: "Couldn't delete the comment",
      userLabel: 'Me',
      contentTypeLabels: { text: 'Text', image: 'Image', video: 'Video', audio: 'Voice' } as Record<string, string>,
      contentTypeFallback: 'Post'
    },
    diary: {
      intro: 'Browse your companion’s reflections by date.',
      previousMonth: 'Previous month',
      nextMonth: 'Next month',
      loading: 'Opening the journal…',
      todayBadge: 'Today',
      signature: (name: string) => `— ${name}'s diary`,
      emptyTitle: 'No diary entry for this day',
      emptyHintToday: 'Your companion may leave an entry after the day ends.',
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
      policyLabel: 'Allow autonomous outfit changes',
      policyDesc: 'When enabled, your companion may choose a ready outfit or design a new one at night.',
      policyStatusLocked: 'Off',
      policyStatusUnlocked: 'On',
      policyToggleAria: 'Allow companion-driven outfit changes',
      policyFailed: "Couldn't save the outfit setting",
      deleteFailed: "Couldn't delete the outfit",
      confirmFailed: "Couldn't confirm the outfit. Try again.",
      retryDescription: 'Complete description',
      descriptionRetryFailed: 'Could not complete the outfit description. Please try again.',
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
        'Describe the outfit you want — e.g. "sailor uniform swapped for an off-white knit cardigan and a brown long skirt, hair tied in a low ponytail". You can also attach a reference image.',
      confirmAction: 'Confirm',
      processing: 'Processing…',
      discard: 'Continue later',
      confirmHint:
        'Confirming prepares idle and drag actions automatically. Choose Wear on its actions page when ready.',
      refImageAttached: 'Reference image attached',
      refImageAlt: 'Reference image',
      zoomRefImage: 'View reference image',
      retryLast: 'Retry last generation',
      placeholderRefining: 'What would you like to tweak? Keep describing… (Enter to send)',
      placeholderInitial: 'Describe an outfit… (Enter to send, Shift+Enter for newline)',
      attachImage: 'Attach reference image',
      attachImageTitle: 'Attach reference image (optional, first generation only)',
      send: 'Create outfit',
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
          "The draft preview hasn't loaded yet. Keep describing to refine it, or reopen it from the wardrobe later.",
        promptRequiresInput:
          'Describe the outfit or attach a reference image in the design area before fetching a prompt.'
      }
    },
    appearance: {
      videoReady: (clips: number) => `Actions ready (${clips} actions)`,
      actionsHeading: 'Actions',
      actionCount: (count: number) => (count === 1 ? '1 action' : `${count} actions`),
      videoActionReady: 'Ready',
      videoActionGenerating: 'Generating',
      videoActionFailed: 'Failed',
      videoActionResultUnknown: 'Outcome unknown',
      videoActionResultUnknownHint:
        'Check the generation task. This action cannot be regenerated until its outcome is known.',
      videoActionReview: 'Needs review',
      videoActionReviewHint: 'Preview the pending action and decide whether to accept it.',
      videoActionCurrent: 'Accepted action',
      videoActionPending: 'Pending action',
      videoActionPendingHint: 'Accept to replace the current action; reject to keep it.',
      videoActionCompactHint: 'Select to inspect',
      videoActionOnDemandShort: 'On demand',
      videoActionSearch: 'Search actions',
      videoActionNoMatch: 'No matching actions',
      videoActionEmpty: 'Select an action to see its preview and controls.',
      videoActionCancel: 'Cancel',
      videoOutfitNotReady: 'This outfit’s basic actions are not ready yet.',
      videoPacksLoadFailed: "Couldn't load action packs",
      videoSelectOutfit: 'Return to the wardrobe and choose an outfit to see its actions.',
      videoBackToOutfits: 'Back',
      videoLoading: 'Loading actions for this outfit…',
      videoIdle: 'Idle',
      videoWalkLeft: 'Walk left',
      videoWalkRight: 'Walk right',
      videoDrag: 'Suspended',
      videoPeekLeft: 'Peek left',
      videoPeekRight: 'Peek right',
      videoPeekCalibrationFailed: 'Layout unavailable',
      videoPeekCalibrationFailedHint:
        'The media is ready, but its peek layout did not pass validation. Regenerate to try again.',
      videoResume: 'Resume existing work',
      videoResumeAction: 'Continue production',
      videoResumeActionHint:
        'Reuse the existing video and saved progress to finish production without generating a new video.',
      videoActivate: 'Wear this outfit',
      videoIdentityReviewHint: 'Check this action pack’s appearance; the current look stays available.',
      videoIdentityReviewActivate: 'Confirm appearance and wear',
      videoActive: 'Active',
      videoActionOnDemand: 'System actions are filled in by the service when needed; you can also generate here.',
      videoGenMissingAction: 'Generate this action',
      videoRedoAction: 'Regenerate',
      videoRedoActionHint: 'Create new action media. This may incur new generation charges.',
      videoFeedback: 'Describe the desired pose or movement (optional)',
      videoGenAction: 'Prepare basic actions',
      videoRegenAction: 'Recreate basic actions',
      videoRegenTitle: 'Regenerate action pack',
      videoRegenBody:
        'An idle video and a drag image will be regenerated from this outfit reference. Your current outfit stays on; choose Wear when the actions are ready.',
      videoGenStagePose: 'Preparing the action pose…',
      videoGenStageScript: 'Designing the pose and movement from the character’s personality…',
      videoGenStageSubmit: 'Submitting the generation task…',
      videoGenStageGenerate: 'Generating action media, this usually takes a few minutes…',
      videoGenStageDownload: 'Downloading the generated media…',
      videoGenStageProcess: 'Processing transparent media and checking quality…',
      videoGenStagePublish: 'Publishing the action pack…',
      videoGenStageDefault: 'Generating the action pack…',
      videoGenBusy:
        'The requested action is still generating or awaiting confirmation. Wait until its outcome is known.',
      videoPackHasReadyActions: 'This outfit already has ready actions. Regenerate individual actions as needed.',
      videoGenRequestFailed: 'Action pack request failed. Please retry shortly.',
      videoActivateFailed: 'Could not wear this outfit. Please retry shortly.',
      videoGenHint:
        'Confirming an outfit in the wardrobe automatically prepares its idle video and drag image; other actions are made on demand. Your current outfit stays on. Choose Wear once the basic actions are ready.'
    },
    scene: {
      analyzing: 'Checking the image and scene description…',
      checking: 'Checking the generation result…',
      checkStatus: 'Refresh creation status',
      inspectLibrary: 'Inspect the scene library',
      finishWaiting: 'I have checked — finish waiting',
      inspectionHint:
        'After checking the latest content, you can close this waiting reminder. This does not cancel accepted work; a delayed task or result may still appear.',
      submissionUnknown:
        'The creation request has not been confirmed. Refresh its status and check the background library first. No duplicate request will be submitted while it is unresolved.',
      intro:
        'Describe a place you would enjoy. AI creates a background for your chat window, and you choose when to use it.',
      referenceHint:
        'A reference guides the environment and composition. Your companion’s own appearance is not repeated in the scene image.',
      referenceLabel: 'Scene reference',
      chooseReference: 'Choose reference',
      replaceReference: 'Replace reference',
      removeReference: 'Remove reference',
      referenceError: 'Choose a valid PNG, JPEG, WebP or GIF image.',
      notesLabel: 'Environment description',
      notesPlaceholder: 'For example: an evening beach with soft sunset light across the sand and sea.',
      targetSize: (width: number, height: number) => `Scene size: ${width} × ${height}`,
      regenerationTargetSize: (width: number, height: number) => `Regeneration size: ${width} × ${height}`,
      generationTargetSize: (width: number, height: number) => `Generation target: ${width} × ${height}`,
      sourceSize: (width: number, height: number) => `Original image: ${width} × ${height}`,
      imageSize: (width: number, height: number) => `Image size: ${width} × ${height}`,
      uploadHint:
        'Choose a background without a duplicate of your companion. The source file is kept; the display image is cropped proportionally to fit the scene size.',
      noScene: 'No active scene yet',
      pendingOverlay: 'Preparing scene…',
      pendingOverlayHint:
        'The image and description are saved to your library. Your current background stays in place.',
      waitingUploadOverlay: 'Waiting for a scene image',
      waitingUploadHint: 'The source file is kept and fitted to the scene size. Activate the saved scene when ready.',
      waitingUploadAction: 'Upload image',
      generateButton: 'Create background',
      generatingButton: 'Creating…',
      emptyLibrary: 'Your scene library is empty. Create or upload a scene to set up your living space.',
      emptySearch: 'No scenes match this search.',
      loading: 'Loading scene library…',
      loadFailed: 'Could not load the scene library. Try again.',
      detailLoadFailed: 'Scene details are unavailable. The scene may have been deleted.',
      openDetails: 'View scene details',
      backToLibrary: 'Back',
      newSceneTitle: 'Create background',
      searchButton: 'Search',
      imageRegenerate: 'Regenerate image',
      saveAndRegenerate: 'Save and regenerate',
      imageRegenerating: 'Regenerating the image. The current scene stays available.',
      imageRegenerationFailed: 'Regeneration failed. The previous image is still saved.',
      historyAltFallback: 'Scene image',
      historyCurrentLabel: 'Current',
      historyRollbackLabel: 'Use this background',
      viewOriginal: 'View full image',
      historyDeleteLabel: 'Delete',
      historyDeleteConfirmTitle: 'Delete this scene?',
      historyDeleteConfirmDescription: 'The image and scene information will be permanently removed from your library.',
      policyLabel: 'Allow autonomous background changes',
      policyDesc:
        'When enabled, your companion may create or switch backgrounds. At night, your nightly activity settings also apply.',
      policyStatusLocked: 'Off',
      policyStatusUnlocked: 'On',
      policyToggleAria: 'Allow autonomous scene creation and switching',
      savedHint: 'Saved first. Activate manually when ready.',
      slow: 'Still generating. This view will update when it finishes.',
      imageLoadFailed: 'The scene image could not be displayed. Try loading it again.',
      retryImage: 'Reload image',
      cancelTask: 'Cancel task',
      search: 'Search titles or descriptions',
      titleLabel: 'Short title',
      descriptionLabel: 'Environment description',
      save: 'Save',
      cancel: 'Cancel',
      edit: 'Edit details',
      retryAnalysis: 'Retry image and description check',
      retryGeneration: 'Retry generation',
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
      sceneStatusRefreshFailed: 'Could not refresh the scene status. Try again shortly.',
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
      copywriter: 'Copywriter',
      language_teacher: 'Language teacher'
    },
    descriptions: {
      copywriter:
        'Drafts, polishes and organizes copy, emails and meeting notes, keeping the original meaning and fitting the audience and occasion.',
      language_teacher:
        'Explains, corrects and practices at your level and toward your goals, with faithful, natural translations.'
    }
  },

  conversation: {
    sessionSidebar: {
      title: 'Sessions',
      new: 'New conversation',
      searchAria: 'Search conversations',
      searchPlaceholder: 'Search conversations…',
      searchResultsHeading: 'Search results',
      searching: 'Searching…',
      noMatch: 'No matching conversations',
      badgeArchived: 'Archived',
      systemHeading: 'System conversations',
      loadingSystem: 'Loading system conversations…',
      noSystem: 'No system conversations',
      ordinaryHeading: 'Ordinary conversations',
      loadingSessions: 'Loading conversations…',
      noOrdinary: 'No ordinary conversations yet — tap above to create one',
      systemSessionFallback: 'My companion',
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
