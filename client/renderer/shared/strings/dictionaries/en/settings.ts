import type { Dictionary } from '../zh'
export const settings: Pick<Dictionary, 'settings' | 'skills' | 'toolsets'> = {
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
        'Let your companion create text, image, video or audio posts at any time, including while offline. All types share the posting limit.',
      motionHeading: 'Motion',
      motionHint:
        'Controls how much the visual figure moves, independent of proactive contact; temporary quiet does not stop the muted idle loop.',
      motionAriaLabel: 'Motion level',
      motions: {
        off: { label: 'No motion', hint: 'Show a static picture only' },
        subtle: { label: 'Subtle motion', hint: 'Mostly a quiet idle loop with fewer random actions' },
        standard: { label: 'Standard motion', hint: 'Full ambient motion and interaction performances' }
      }
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
      title: 'About your companion',
      intro: 'Review the name, personality, and your relationship. Save your edits or cancel to discard them.',
      sectionTitle: 'Persona',
      categoriesAria: 'Companion categories',
      memoriesTitle: 'Memories',
      memoryIntro:
        'Each companion and assistant retains enduring information, preferences and relationship understanding from its own conversations. Review, edit or forget these memories; find life history and diaries on their own pages.',
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
        acceptCandidate: 'Use this candidate',
        acceptCandidateFailed: 'Could not accept this image. Try again.',
        adoptHint:
          'Adopting confirms this full-body image and prepares transparent styling materials in the background. Choose the initial environment in the following step.',
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
      ageLabel: 'Age (optional)',
      agePlaceholder: 'Describe freely, or leave blank',
      occupationLabel: 'Occupation or role (optional)',
      occupationPlaceholder: 'Describe freely, or leave blank',
      backgroundLabel: 'Background (optional)',
      backgroundPlaceholder: 'Personal history, world, or other character settings…',
      backgroundTitle: 'Character background',
      backgroundHint:
        'These settings inform personality and daily life without changing confirmed appearance. Leave them blank or add them later.',
      skipBackground: 'Skip for now',
      portraitLabel: 'Confirmed portrait',
      fullbodyLabel: 'Confirmed full-body appearance',
      appearanceTitle: 'Confirmed appearance',
      preparationWorking: 'Preparing transparent appearance and the initial outfit. You can continue other activities.',
      preparationRetry: 'Retry preparation',
      nameLabel: 'Name',
      speciesLabel: 'Species',
      genderLabel: 'Gender',
      namePlaceholder: 'Name your companion',
      relationshipLabel: 'Relationship',
      relationshipPlaceholder: 'Or describe freely…',
      personalityLabel: 'Personality',
      personalityPlaceholder: 'Describe freely…',
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
      sections: {
        companionship: 'Companionship',
        appearance: 'Appearance and display',
        sources: 'News sources',
        devices: 'Mobile access',
        capabilities: 'Capabilities',
        advanced: 'Application and advanced'
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
  }
}
