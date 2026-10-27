import type { Dictionary } from './zh'

export const dict: Dictionary = {
  life: {
    title: 'Life',
    intro: 'What your companion is continuing, and what has changed recently.',
    now: 'Right now',
    unavailable: 'No recent activity is available yet',
    ongoing: 'Still on their mind',
    noPursuit: 'No ongoing pursuits at the moment',
    overviewAction: 'Explore life',
    chatAction: 'Open chat',
    show: 'Show life area',
    hide: 'Hide life area',
    close: 'Close life area',
    pursuitsTitle: 'Things that matter',
    eventsTitle: 'Recent changes',
    statuses: { all: 'All', active: 'Ongoing', paused: 'Paused', completed: 'Completed', abandoned: 'Set aside' },
    factTypes: {
      intent: 'Intention',
      virtual_activity: 'Life scene',
      execution: 'Execution',
      result: 'Confirmed result'
    },
    stepStates: {
      planned: 'Waiting',
      running: 'In progress',
      completed: 'Completed',
      failed: 'Failed',
      cancelled: 'Cancelled',
      unknown: 'Outcome unconfirmed'
    },
    stepsTitle: 'Actions',
    changeReason: 'Reason for the change',
    readingNotes: 'Reading notes',
    sourceSummary: 'Summary',
    sourceExcerpt: 'Excerpt',
    sourceText: 'Text',
    reason: 'How it began',
    desiredResult: 'What they hope to achieve',
    progress: 'Progress so far',
    nextStep: 'Planned next',
    noProgress: 'No confirmed progress yet',
    emptyPursuits: 'No pursuits have been recorded here yet.',
    emptyEvents: 'No life changes to display yet.',
    moreEvents: 'Earlier records',
    failed: 'Life updates are unavailable. Previously loaded content is still shown.',
    loading: 'Checking in…',
    back: 'Back to life',
    previous: 'Previous',
    next: 'Next',
    details: 'Explore this pursuit',
    openVisual: 'View visual',
    openOutfit: 'View outfit',
    openScene: 'View environment',
    openAction: 'View action',
    openPosts: 'Open posts',
    openDiary: 'Open diary',
    eventLabels: {
      activity: 'Activity changed',
      started: 'Started something',
      progressed: 'Made progress',
      completed: 'Finished something',
      visual: 'Created a visual',
      post: 'Published a post',
      diary: 'Wrote a diary entry',
      paused: 'Paused a pursuit',
      resumed: 'Resumed a pursuit',
      abandoned: 'Set a pursuit aside',
      step: 'Completed a step',
      failed: 'Execution failed',
      unknown: 'Outcome unconfirmed'
    }
  },
  brand: {
    name: 'SpiritAgent',
    fullName: 'SpiritAgent Desktop'
  },

  common: {
    actionVariants: {
      title: 'Creation progress by view',
      readyWithStatus: (status: string) => `Playable · ${status}`,
      remakeFailed: 'Remake failed',
      statuses: {
        not_generated: 'Not created',
        queued: 'Waiting to start',
        processing: 'Creating',
        ready: 'Playable',
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
    companionControl: {
      title: 'Life area',
      show: 'Show life area',
      left: 'Left side',
      right: 'Right side',
      saveFailed: 'Could not save life area display settings. Try again.'
    },
    save: 'Save',
    saving: 'Saving…',
    cancel: 'Cancel',
    clear: 'Clear',
    close: 'Close',
    copied: 'Copied',
    copyFailed: 'Copy failed. Try again.',
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
    processing: 'Processing…',
    operationFailed: 'The operation could not finish. Try again.',
    saveChanges: 'Save changes',
    unsavedChanges: 'Unsaved changes',
    keepMyEdits: 'Reviewed, keep my edits',
    forget: 'Forget',
    changedElsewhere: 'These details changed on another device. Review the latest values before saving.',
    view: 'View',
    back: 'Back',
    search: 'Search',
    previous: 'Previous',
    next: 'Next',
    refresh: 'Refresh'
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
    checkResult: 'Check existing result',
    finishWaiting: 'I have checked — finish waiting',
    inspectionHint:
      'After checking the latest content, you can close this waiting reminder. This does not cancel accepted work; a delayed task or result may still appear.'
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
    regenerateHint: 'Redraws the whole image from the references and your brief'
  },

  boot: {
    desktopBootFailedWithMessage: (message: string) => `Desktop failed to start: ${message}`,
    errors: {
      desktopBootFailed: 'Desktop failed to start'
    },
    failure: {
      retry: 'Retry'
    }
  },

  onboarding: {
    steps: { meet: 'Meet your companion', confirm: 'Confirm the look', voice: 'Choose a voice' },
    progressAria: 'Getting to know your companion',
    voiceTitle: 'Choose a voice for your companion',
    nextVoice: 'Continue choosing a voice',
    nextConfirm: 'Next: confirm the look',
    next: 'Next',
    previousQuestion: 'Previous question',
    skip: 'Skip',
    questionProgress: (index: number, total: number) => `Question ${index} of ${total}`,
    lockedHint: (label: string) => `“${label}” cannot be changed after the look is confirmed. Choose carefully.`,
    avatarLabel: 'Avatar',
    zoomAria: 'Enlarge',
    feedbackExample: 'For example: blond hair, green eyes, a scar across the forehead, a mechanical eye… (optional)',
    feedbackExampleRefine:
      'What looks off? For example: shorter hair, larger eyes, a gentler expression… (leave empty to regenerate)',
    refineDisabledByReference: 'Cannot refine with a reference image. Remove the reference first.',
    refineHint: 'Adjust the current avatar and keep everything else. Describe the change first.',
    refineAction: 'Refine',
    saveFailed: 'Could not save your answers. Try again.',
    resumeFailed: 'Could not read your saved progress. Try again.',
    retrying: 'Retrying…',
    stepVoice: 'Choose a voice',
    stepCompanion: 'Start together',
    readyTitle: 'Your companion is ready',
    readyHint:
      'You can start chatting now. You can also tell your companion a little about yourself first, and add more at any time.',
    knowMore: 'Let your companion know me (optional)',
    savingProfile: 'Saving your answers…',
    portraitPreviewHint: 'Preview and confirm your avatar',
    portraitGenerating: 'Creating the avatar…',
    portraitFailed: 'The avatar step could not finish',
    portraitBack: 'Previous step',
    portraitTitle: 'Draw your companion’s avatar',
    portraitConfirm: 'I like it, prepare the full look',
    portraitIdeaTitle: 'Imagine your companion',
    portraitIdeaHint:
      'Describe the face, hair, or colors you like and AI will draw the avatar. You can skip ahead if you have no idea yet.',
    portraitDescriptionLabel: 'Avatar description (optional)',
    referenceSummary: 'Add a reference image (optional)',
    referenceHint: 'Used as inspiration while generating. It does not become the avatar itself.',
    referenceAlt: 'Generated reference image',
    addReference: 'Add reference image',
    replaceReference: 'Replace reference image',
    removeReference: 'Remove',
    previousAvatarAlt: 'Previously created avatar',
    keepAvatar: 'Keep this avatar',
    useOwnImage: 'Use your own image',
    confirmTitle: 'Confirm your companion’s avatar',
    defaultName: 'Companion',
    adjustSummary: 'Not quite right? Adjust the avatar',
    regenerate: 'Generate again',
    reload: 'Reload',
    shapeReferenceAlt: 'Appearance reference',
    shapeReferenceHint: 'Borrow the shape from this image',
    shapeReference: '+ Appearance reference',
    compositionReferenceAlt: 'Lighting and composition reference',
    compositionReferenceHint: 'Borrow the lighting and composition, keeping one visual style',
    compositionReference: '+ Lighting and composition reference',
    replaceCompositionReference: 'Replace composition reference',
    ownImageHint: 'Use a single character on a plain white background. Accepting it sets the avatar directly.',
    ownImageTitle: 'Use your own image'
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
      microphonePermission: 'Microphone permission was denied.'
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
    current: 'Current voice',
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
    designFailed: 'Voice generation failed. Try again.',
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
    uploadFailed: 'Audio upload failed. Try again.',
    micUnavailable: 'Could not use the microphone. Check your system permissions or upload audio.',
    previewFailed: 'Could not play the preview. Try again.',
    onboardingHint: 'Choose a voice and confirm. You can adjust it later in Companion › Voice.',
    describePreference: 'Describe the voice you want',
    continueText: 'Use text for now'
  },
  settings: {
    nav: {
      about: 'About',
      theme: 'Theme',
      interaction: 'Interaction',
      persona: 'Persona',
      shortcuts: 'Shortcuts',
      voice: 'Voice',
      companion: 'Companion'
    },
    companion: {
      heading: 'Size and position',
      hint: 'Adjust the companion scale and position in window mode.',
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
      intro: 'How the companion responds and when it may interrupt you.',
      voiceHeading: 'Voice interaction',
      voiceIntro: 'Choose your preferred reply format and the maximum length of a voice recording.',
      responsePreference: 'Response preference',
      responsePreferenceText: 'Prefer text',
      responsePreferenceVoice: 'Prefer voice',
      responsePreferenceDesc:
        'Tell your companion which format you usually prefer. They choose based on the situation.',
      recording: 'Recording length cap',
      recordingDesc: 'Maximum length of a single voice recording — auto-stops and sends at the limit.',
      recordingSecondsSuffix: 's',
      recordingSaveFailed: 'Failed to save recording duration',
      recordingLoadFailed: "Couldn't load the recording limit. Try again.",
      tierHeading: 'How would you like to spend time together?',
      tierHint: "Only constrains the companion's proactive behaviour — your actions are never restricted.",
      tierAriaLabel: 'Disturbance tier',
      tiers: {
        still: { label: 'Only reply to me', hint: 'Quiet company until you start a conversation' },
        normal: { label: 'Occasional greetings', hint: 'A gentle text greeting while staying in place' },
        autonomous: { label: 'Free to interact', hint: 'May speak and interact naturally' }
      },
      smartHeading: 'Everyday life',
      smartHint: 'Let your companion record its life independently of the companionship intensity.',
      postsEnabled: 'Post creation',
      postsEnabledDesc:
        'Let your companion create text, image, video or audio posts at any time, including while offline. All types share the posting limit.'
    },
    socialAccounts: {
      title: 'Content sources',
      hint: 'Connect your platform accounts so your companion can find shared interests overnight and share when it fits. Clearing a connection stops future searches; content already collected may still be shared.',
      loadFailed: "Couldn't load content sources. Try again.",
      retry: 'Reload',
      states: { unconfigured: 'Not configured', configured: 'Configured', login_required: 'Sign in again' },
      cookieLabel: 'Login Cookie',
      cookieHint:
        'Paste a Cookie request header or browser Cookie JSON export containing auth_token and ct0. Credentials are used only to read content.',
      cookiePlaceholder: 'Paste login Cookies; leaving this empty keeps your saved credentials',
      save: 'Save credentials',
      saving: 'Saving…',
      clear: 'Clear connection',
      clearing: 'Clearing…',
      login: 'Sign in with QR code',
      refreshing: 'Getting QR code…',
      refreshQr: 'Get a new QR code',
      qrAlt: (platform: string) => `${platform} login QR code`,
      qrHint: (platform: string) =>
        `Scan with the ${platform} app and confirm. An ongoing login can finish after you close this page.`,
      qrResumed: 'A login is in progress. Check its status or get a new QR code.',
      expiresIn: (seconds: number) => `The QR code and verification session expire in ${seconds} seconds`,
      check: 'Check login status',
      cancel: 'Cancel this login',
      canceling: 'Canceling…',
      codeLabel: 'SMS verification code',
      codeHint: 'Enter the 6-digit SMS code for this QR login.',
      submitCode: 'Submit code',
      submittingCode: 'Submitting…',
      invalidCode: 'The code is incorrect or expired. Check it and try again.',
      loginStates: {
        awaiting_scan: 'Waiting for scan',
        sms_required: 'SMS verification required',
        verifying: 'Verifying',
        verification_required: 'Complete other security checks in the platform app, then scan a new QR code',
        logged_in: 'Signed in',
        expired: 'Login session expired. Scan a new QR code.'
      },
      pollingStopped: 'Automatic checks paused. Check the login status or get a new QR code.',
      operationFailed: "Couldn't complete the action. Try again."
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
        conflict: 'The character card was updated elsewhere. Your changes are preserved. Review before merging.',
        loadFailed: 'Could not load character card. Try again.',
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
      categoriesAria: 'Companion categories',
      memoriesTitle: 'Memories',
      memoryIntro: 'Each companion and assistant remembers its own conversations.',
      sections: {
        profile: 'Profile',
        voice: 'Voice',
        memory: 'Memories',
        wardrobe: 'Wardrobe',
        environment: 'Environments',
        actions: 'Actions',
        visuals: 'Visuals'
      },
      memoryScope: 'Whose memories',
      memoryScopeAria: 'Memory type',
      memoryKindRecall: 'Remembered things',
      memorySearchAria: 'Search memories',
      memorySearchPlaceholder: 'Search remembered things',
      memoryNoMatch: 'No matching memories.',
      memoryEdit: 'Edit memory',
      memoryCollapse: 'Collapse',
      memorySaveHint: 'Saving makes this memory available again.',
      memorySavedActive: 'Saved. This memory is in use again.',
      memorySaveAgain: 'Save and remember again',
      memoryLatestContent: 'This memory changed on another device. Latest content:',
      memoryForgetTitle: 'Forget this memory?',
      memoryForgetAction: 'Forget this memory',
      memoryTotal: (count: number) => `${count} in total`,
      profileForgetTitle: 'Forget these details?',
      profileForgetOneTitle: 'Forget this detail?',
      profileForgetAndSave: 'Save and forget',
      profileForgetOneAction: 'Forget this detail',
      profileAdditionalDetail: 'Additional detail',
      profileLatestContent: 'Latest content:',
      profileConflictHint: 'These details changed on another device. Review the latest values:',
      personalityRequired: 'Enter a personality.',
      fullbodyReference: {
        title: 'Your companion’s full appearance',
        hint: 'AI uses the portrait you just confirmed to create the full appearance. When it looks right, continue to choose a voice.',
        confirmHint: 'Confirming saves this look in the wardrobe as a styling material for visuals.',
        candidateHint:
          'This full-body image is awaiting acceptance. Body proportions may change; accepting it updates the body reference for future generations. Saved looks remain in the wardrobe; future creations use the newly accepted body reference.',
        acceptCandidate: 'Accept new full-body image',
        retryCandidateAnalysis: 'Retry body analysis',
        acceptCandidateFailed: 'Could not accept this image. Try again.',
        retryCandidateAnalysisFailed: 'Analysis failed. Try again.',
        adoptHint:
          'Adopting confirms this full-body image and starts character analysis; the saved look stays in the wardrobe.',
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
        restoreFailed: 'Could not restore this version. Try again.',
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
      hintSaveFailed: 'Save failed, try again.'
    },
    theme: {
      heading: 'Interface theme',
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
    },

    application: {
      title: 'App settings',
      hint: 'Choose the interface language.',
      generalTitle: 'General',
      languageLabel: 'Language',
      languageSaveFailed: 'Language could not be saved. Try again.',
      shortcutsTitle: 'Keyboard shortcuts',
      displayHint: 'Choose the display mode, screen, and the companion size and position in window mode.',
      advancedHint: 'The recommended defaults work for everyday use. Adjust these options only when you need to.',
      conversationTitle: 'Conversation parameters',
      conversationHint: 'Reasoning, randomness, and long conversations.',
      executionTitle: 'Computer execution settings',
      executionHint: 'Remote terminals, network access, and sensitive information.',
      categoriesAria: 'Settings categories',
      categoryAria: 'Settings category',
      settingsLabel: 'Settings',
      sections: {
        companionship: 'Companionship',
        voice: 'Voice interaction',
        sources: 'News sources',
        theme: 'Interface theme',
        display: 'Companion display',
        devices: 'Mobile access',
        capabilities: 'Capabilities',
        application: 'App settings',
        advanced: 'Advanced settings'
      }
    },

    presentation: {
      title: 'Window and desktop',
      hint: 'Choose window or desktop mode and the screen your companion uses.',
      displayModeLabel: 'Display mode',
      displayLabel: 'Display',
      displayAria: 'Interactive display',
      switchFailed: 'Could not save the display settings. Try again.',
      modeDescription:
        'Window mode uses a main window and floating companion. Desktop mode places your companion in the desktop background.',
      modeDescriptionUnsupported: 'Desktop mode is currently unavailable on this computer.',
      switchToWindowMode: 'Switch to window mode',
      switchToDesktopMode: 'Switch to desktop mode',
      displayDescription:
        'Use this display while it is connected; otherwise use the current display. Other displays keep their existing desktop.'
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
    loadFailedDesc: 'Try again.; if it keeps failing, reinstall the app to restore the built-in skills.',
    noToolsetsTitle: 'No toolsets found',
    noToolsetsDesc: 'Try a broader search term.',
    noDescription: 'No description available.',
    toolsetsEnabled: (enabled: number, total: number) => `${enabled}/${total} toolsets enabled`,
    toolsetsLoadFailed: "Couldn't load toolsets",
    toolsetsLoadFailedDesc: 'Try again.',
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
    nav: {
      showConversations: 'Show conversations',
      hideConversations: 'Hide conversations',
      companion: 'Companion chat',
      assistant: 'Assistant chat',
      readOnly: 'Task conversation · read-only',
      execution: 'Execution details',
      closeExecution: 'Close execution details',
      pinned: 'Pinned',
      recent: 'Recent conversations',
      running: 'Running',
      loadFailed: 'Conversation could not load. Try again. Your messages and draft have been retained.'
    },
    replaceReply: 'Try another answer',
    replacingReply: 'Preparing another answer…',
    topic: {
      open: 'View original',
      unavailable: 'Original link unavailable',
      like: 'More like this',
      less: 'Less like this',
      undo: 'Click again to undo feedback',
      failed: 'Feedback unavailable. Click to retry.'
    },
    defaultSessionTitle: 'Daily chat',
    inputPlaceholder: 'Say something, or drag a file over',
    typing: 'Typing…',
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
      loadFailed: "Couldn't load media"
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
      retry: 'Voice unavailable',
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
      busy: 'The voice service is busy. Try again.',
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

      saveFailed: 'Could not save session parameters. Try again.',
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
      fetchFailed: 'Failed to load presets — try again.',
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
      activate: 'Activate / sign in',
      openMainWindow: 'Open main window'
    },
    egg: {
      preparing: 'Loading your companion’s look…',
      failed: 'Could not load this look',
      unavailable: 'No visual is available yet',
      retry: 'Retry',
      openVisuals: 'View visuals',
      ariaLabel: 'Companion egg'
    }
  },

  living: {
    rail: {
      chat: 'Companion',
      posts: 'Posts',
      diary: 'Diary',
      life: 'Life',
      unread: 'Unread content',
      wardrobe: 'Wardrobe',
      remote: 'Remote',
      scene: 'Scene',
      settings: 'Settings',
      companionFallback: 'Companion',
      mainNavAria: 'Main navigation',
      avatarMood: (name: string) => `${name}'s mood`
    },
    posts: {
      intro: 'Explore the moments your companion shares and leave a comment.',
      expand: 'Read more',
      collapse: 'Show less',
      videoRecoveryHeading: 'Video posts awaiting check',
      videoRecoveryHint:
        'Check the original task when its result is unknown. Videos are published after passing their checks.',
      videoRecoveryLoadFailed: 'Could not load video posts awaiting check. Refresh the posts page.',
      videoRecoveryStatuses: {
        pending: 'The original video task is still pending',
        ready: 'The video is ready and will be published automatically',
        failed: 'The original video task failed',
        unknown: 'The original video result is unconfirmed',
        discarded: 'Discarded'
      },
      videoQuery: 'Check original task',
      videoRefresh: 'Refresh',
      videoDiscard: 'Discard',
      videoCleanupPending: 'Publication was discarded. Some local assets still need cleanup.',
      videoRetryCleanup: 'Retry cleanup',
      videoQueryFailed: 'Could not check the original video task. Try again.',
      videoDiscardFailed: 'Discard did not complete. Try again.',
      videoDiscardTitle: 'Discard this video post?',
      videoDiscardDescription:
        'This post will not be published. Its exclusive local video will be removed. The provider task may still be running, and costs already incurred will not be refunded.',
      loadMore: 'Load more',
      replyPending: 'Replying…',
      replyRetry: 'Reply failed · Retry',
      replyRetryFailed: 'Retry failed. Try again.',

      loading: 'Flipping through the album…',
      empty: 'No posts captured yet.',
      loadFailed: "Couldn't load posts",
      noTitle: 'Untitled',
      commentsLoad: 'View comments',
      commentsMore: 'More comments',
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
      backToWardrobe: 'Back to wardrobe',
      saved: 'Saved',
      empty: 'No outfits yet. Design your first one to get started.',
      loading: 'Loading outfits…',
      search: 'Search names or descriptions',
      emptySearch: 'No outfits match this search.',
      noDescription: 'No outfit description yet',
      openDetails: 'View outfit details',
      zoomOutfit: (name: string) => `View ${name} full size`,
      policyLabel: 'Let the companion add outfit materials on its own',
      policyDesc: 'When enabled, your companion may add new outfit materials by itself.',
      policyStatusLocked: 'Off',
      policyStatusUnlocked: 'On',
      policyToggleAria: 'Let the companion add outfit materials on its own',
      policyFailed: "Couldn't save the outfit material setting",
      deleteFailed: "Couldn't delete the outfit",
      confirmFailed: "Couldn't complete the operation. Try again.",
      retryDescription: 'Complete description',
      descriptionRetryFailed: 'Could not complete the outfit description. Try again.',
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
        'Confirming saves this look in the wardrobe. Once the image is ready it becomes a styling material for visuals.',
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
        generateFailed: "Couldn't generate the outfit. Try again.",
        resumeReady: 'Keep refining this draft, or confirm to add it to the wardrobe.',
        resumePreviewMissing:
          "The draft preview hasn't loaded yet. Keep describing to refine it, or reopen it from the wardrobe later.",
        promptRequiresInput:
          'Describe the outfit or attach a reference image in the design area before fetching a prompt.'
      }
    },
    scene: {
      analyzing: 'Checking the image and scene description…',
      checking: 'Checking the generation result…',
      checkStatus: 'Refresh creation status',
      inspectLibrary: 'Inspect the scene library',
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
      noScene: 'No environment material yet',
      pendingOverlay: 'Preparing scene…',
      pendingOverlayHint: 'The image and description are saved to your environment library.',
      waitingUploadOverlay: 'Waiting for a scene image',
      waitingUploadHint:
        'The source file is kept and fitted to the environment size. Saving makes the material available.',
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
      imageRegenerating: 'Regenerating the environment image. The current material stays available.',
      imageRegenerationFailed: 'Regeneration failed. The previous image is still saved.',
      historyAltFallback: 'Scene image',
      viewOriginal: 'View full image',
      historyDeleteLabel: 'Delete',
      historyDeleteConfirmTitle: 'Delete this scene?',
      historyDeleteConfirmDescription: 'The image and scene information will be permanently removed from your library.',
      policyLabel: 'Let the companion add environment materials on its own',
      policyDesc: 'When enabled, your companion may add new environment materials by itself.',
      policyStatusLocked: 'Off',
      policyStatusUnlocked: 'On',
      policyToggleAria: 'Let the companion add environment materials on its own',
      savedHint: 'Saved first. Use it any time later.',
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
      untitled: 'Untitled environment',
      needsDescription: 'Description pending.',
      statuses: {
        pending: 'Preparing',
        ready: 'Saved',
        description_failed: 'Description needed',
        failed: 'Creation failed',
        cancelled: 'Cancelled'
      }
    },
    toasts: {
      sceneReady: 'Saved to environment library',
      sceneImageRegenerated: 'Environment image updated',
      sceneRegenerateFailed: 'Scene operation failed. Check the details and retry.',
      sceneStatusRefreshFailed: 'Could not refresh the scene status. Try again.',
      sceneDeleteFailed: 'Delete failed — please retry shortly',
      sceneDeleteSuccess: 'Scene deleted',
      sceneLockFailed: "Couldn't update the lock setting",
      sceneLocked: 'Autonomous material additions disabled',
      sceneUnlocked: 'Autonomous material additions enabled'
    }
  },

  presets: {
    names: {
      companion: 'Companion',
      copywriter: 'Copywriter',
      language_teacher: 'Language teacher',
      general: 'General assistant'
    },
    descriptions: {
      copywriter:
        'Drafts, polishes and organizes copy, emails and meeting notes, keeping the original meaning and fitting the audience and occasion.',
      language_teacher:
        'Explains, corrects and practices at your level and toward your goals, with faithful, natural translations.',
      general:
        'A general conversation without a role: keeps tool use, memory and environment context, and drops the specific persona and professional guidance.'
    }
  },

  conversation: {
    sessionSidebar: {
      title: 'Sessions',
      new: 'New conversation',
      searchAria: 'Search conversations',
      searchPlaceholder: 'Search conversations…',
      loadMore: 'Load more',
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
  },

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

  desktopShell: {
    title: 'Desktop',
    desktopMode: 'Desktop mode',
    switchToDesktopMode: 'Switch to desktop mode',
    switchFailed: 'Could not save the display mode. Try again.',
    needsAttention: 'Needs attention',
    primaryDisplaySuffix: ' (Primary)',
    chooseDisplay: 'Choose a display',
    chat: 'Chat',
    companion: 'Companion',
    showConversations: 'Show conversations',
    hideConversations: 'Hide conversations',
    openUnread: 'Open unread conversation',
    avatarChatHint: 'Left-click to open chat, right-click to switch account',
    avatarUnreadHint: 'Left-click to open unread chat, right-click to switch account',
    posts: 'Posts',
    diary: 'Diary',
    life: 'Life',
    remote: 'Remote',
    settings: 'Settings',
    windowMode: 'Return to window mode',
    display: 'Interactive display',
    displayHint: 'Use only this display. Other displays keep their existing desktop.',
    quit: 'Quit SpiritAgent',
    dock: 'App Dock',
    pinnedApps: 'Pinned apps',
    runningApps: 'Running apps',
    tasks: 'Tasks',
    tasksLoading: 'Reading tasks…',
    taskLaunchFailed: 'Could not start task. Try again.',
    running: 'Running',
    pinApp: 'Pin to Dock',
    unpinApp: 'Unpin from Dock',
    cannotPin: 'Launch target unavailable; select the app from the app list',
    minimizedWindow: 'Minimized',
    closeWindow: 'Close window',
    closeAllWindows: 'Close all windows',
    runningUnavailable: 'Running apps are temporarily unavailable. Try again.',
    accessibilityTitle: 'Accessibility permission',
    accessibilityRequest: 'Allow app window management',
    accessibilityDescription:
      'Allow SpiritAgent in System Settings → Privacy & Security → Accessibility to view, close, and move running windows between displays. Pinned apps and the app catalog can still launch apps.',
    accessibilityGranted: 'Viewing and managing running windows is allowed.',
    allApps: 'All apps',
    appLaunchFailed: 'Could not start app. Try again.',
    repairApp: 'Choose app again',
    moveLeft: 'Move left',
    moveRight: 'Move right',
    missing: 'App unavailable. Choose it again.',
    dockHint: 'Browse and launch apps, or drop an app or shortcut onto the Dock to pin it',
    pickRepair: 'Choose app again',
    pickSearch: 'Search app names or details',
    pickLoading: 'Reading apps…',
    pickLoadFailed: 'Could not read apps. Rescan to retry.',
    pickEmpty: 'No matching app',
    pickBrowse: 'Browse files…',
    pickBrowsePin: 'Browse files and pin…',
    pickPinned: 'Pinned',
    pickRescan: 'Rescan',
    pickSources: {
      'start-user': 'Start menu',
      'start-all': 'All users start menu',
      'desktop-user': 'Desktop',
      'desktop-onedrive': 'OneDrive desktop',
      'desktop-public': 'Public desktop',
      'app-paths': 'Registered apps',
      'apps-folder': 'Windows apps',
      'mac-applications': 'Applications',
      'mac-system': 'System apps',
      'mac-user': 'User apps'
    },
    pickSourceFailed: 'Could not read',
    minimize: 'Minimize',
    maximize: 'Maximize',
    restore: 'Restore',
    close: 'Close',
    resize: 'Resize window',
    loading: 'Connecting…',
    connected: 'Connected',
    disconnected: 'Disconnected',
    desktopSettings: 'Desktop settings',
    disturbance: 'Companionship',
    still: 'Still',
    normal: 'Normal',
    autonomous: 'Autonomous',
    waiting: 'Preparing desktop…'
  },

  actions: {
    inLibrary: 'In library',
    notInLibrary: 'Not in library',
    emptyUnlisted: 'No actions awaiting inclusion.',
    builtin: 'Built-in',
    custom: 'Custom',
    copy: 'Create a copy',
    retry: 'Retry review',
    retryFailed: 'Could not retry the review',
    loadMore: 'Load more actions',
    title: 'Actions',
    intro:
      'Actions in your library can be used to create visuals. Check reviews, edit or retry actions not yet included.',
    searchAria: 'Search actions',
    searchPlaceholder: 'Search names or descriptions',
    loading: 'Loading actions…',
    empty: 'No actions in the library yet. Custom actions appear here after approval.',
    emptySearch: 'No actions match this search.',
    create: 'New action',
    edit: 'Edit action',
    editingLocked: 'This action is under review and cannot be edited yet.',
    deleteLabel: 'Delete',
    deleteTitle: 'Delete this action?',
    nameLabel: 'Name',
    motionLabel: 'Motion',
    useWhenLabel: 'Use when',
    avoidWhenLabel: 'Avoid when',
    conditionsHint: 'One condition per line, up to 8 per field and 120 characters each. Leave empty for no limit.',
    clipLabel: 'Action type',
    clipOnce: 'Short action',
    clipLoop: 'Looping action',
    durationLabel: 'Duration',
    durationSuffix: ' s',
    reviewReasonLabel: 'Review note',
    save: 'Save',
    cancel: 'Cancel',
    saveFailed: "Couldn't save the action",
    deleteFailed: "Couldn't delete the action",
    loadFailed: "Couldn't load actions",
    statusLabels: {
      pending: 'Pending review',
      reviewing: 'Under review',
      approved: 'Approved',
      deferred: 'Needs information',
      rejected: 'Not approved',
      failed: 'Review failed'
    }
  },

  visuals: {
    title: 'Visuals',
    intro:
      'Image and video results composed from wardrobe, environment and action materials, together with their creation status.',
    searchAria: 'Search visuals',
    searchPlaceholder: 'Search names or descriptions',
    mediaKindLabel: 'Media type',
    mediaKindAll: 'All',
    mediaKindImage: 'Images',
    mediaKindVideo: 'Videos',
    sourceLabel: 'Source material',
    sourcePlaceholder: 'Filter by wardrobe, environment or action name',
    sourceOutfit: 'Wardrobe',
    sourceScene: 'Environments',
    sourceAction: 'Actions',
    materialReady: (outfits: number, scenes: number, actions: number) =>
      `Materials ready: wardrobe ${outfits} · environments ${scenes} · actions ${actions}`,
    emptyTitle: 'No visuals yet',
    emptyHint: 'Once wardrobe, environment and action materials are ready, ask your companion to compose one.',
    emptySearch: 'No visuals match this search.',
    contactCompanion: 'Ask the companion to create one',
    loading: 'Loading visuals…',
    loadFailed: "Couldn't load visuals",
    detailLoadFailed: "Couldn't load the visual details",
    jobsTitle: 'Creations in progress and failed',
    jobImage: 'Image creation',
    jobVideo: 'Video creation',
    jobState: {
      queued: 'Queued',
      running: 'Creating',
      paused: 'Paused',
      succeeded: 'Completed',
      failed: 'Failed',
      cancelled: 'Cancelled',
      unknown: 'Unknown status'
    } as Record<string, string>,
    jobPause: 'Pause',
    jobResume: 'Resume',
    jobRetry: 'Retry',
    jobCancel: 'Cancel',
    jobCommandFailed: "Couldn't run the creation command",
    cancelTitle: 'Cancel this creation?',
    currentMark: 'Currently shown',
    backToList: 'Back to visuals',
    sourceTitle: 'Composed from',
    frozenDeleted: 'The material was deleted; the frozen source from creation is kept.',
    sizeLabel: 'Size',
    durationLabel: 'Duration',
    statusLabel: 'Creation status',
    seconds: (value: number) => `${value} s`,
    coverAlt: (name: string) => `${name} cover`,
    previewNote: 'Previewing here is not recorded as playback.',
    redoHint: 'The current result stays on display while a redo is created.',
    feedbackLabel: 'Change requests',
    videoFeedbackLabel: 'Video change requests',
    redoSubmit: 'Redo',
    redoFailed: "Couldn't submit the redo",
    prefsTitle: 'Presentation preferences',
    autonomousLabel: 'Let the companion present visuals on its own',
    autonomousDesc: 'When enabled, your companion may play or switch visuals at fitting moments.',
    prefsFailed: "Couldn't save the presentation preference",
    paused: 'Playback paused',
    prev: 'Previous',
    next: 'Next',
    total: (count: number) => `${count} total`
  }
}
