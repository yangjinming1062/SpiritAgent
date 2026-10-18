// 由 scripts/generate_protocol.py 从服务端公开契约生成。

export interface ApiSchemas {
  "AIConfigPublic": { "providers": Array<ApiSchemas["ProviderCardPublic"]>; "capabilities": ApiSchemas["CapabilityChains"]; "tasks": { [key: string]: ApiSchemas["TaskConfig"] } }
  "AIConfigUpdate": { "providers": Array<ApiSchemas["ProviderCardUpdate"]>; "capabilities": ApiSchemas["CapabilityChainsInput"]; "tasks"?: { [key: string]: ApiSchemas["TaskConfigInput"] } }
  "ActionBudgetStatus": { "budget_date": string; "window_start": string; "window_end": string; "autonomous_create_used": number; "autonomous_create_limit": number; "user_requested_create_used": number; "user_requested_create_limit": number }
  "ActionCatalogResponse": { "pack_id": number; "catalog_version": number; "appearance_epoch": number; "manifest_url": (string | null) }
  "ActionDesignRequest": { "name": string; "motion_description": string; "use_when"?: Array<string>; "avoid_when"?: Array<string>; "reason": string; "duration_seconds": number; "clip_kind": string; "expected_pack_id"?: (number | null) }
  "ActionDesignResult": { "outcome": string; "proposal_id": (number | null); "action_id": (number | null); "message": string }
  "ActionPlaybackReceipt": { "play_id": string; "status": string; "visible_duration_ms"?: number; "error"?: (string | null) }
  "ActionUpdateRequest": { "enabled": boolean }
  "ActivateRequest": { "code": string; "client_version"?: string; "client_context"?: (ApiSchemas["ChatRequestClientContext"] | null) }
  "ActiveTurnView": { "request_id": string; "origin_kind": string; "message_ids": Array<number>; "assistant_message_id": (number | null); "messages": Array<ApiSchemas["SessionMessageView"]>; "text": string; "reasoning": string; "bubbles": Array<ApiSchemas["ReplyBubbleView"]>; "tools": Array<ApiSchemas["ToolSnapshot"]>; "running": boolean; "replace_reply_message_id": (number | null) }
  "AdminLoginRequest": { "username": string; "password": string }
  "AdminTokenResponse": { "access_token": string; "expires_in": number }
  "AiTaskDefinition": { "id": ApiSchemas["AiTaskKey"]; "group": string; "label": string; "services": Array<string>; "service_usage": { [key: string]: Array<string> } }
  "AiTaskKey": "companion_chat" | "work_chat" | "scheduled_task" | "proactive" | "memory_review" | "memory_embedding" | "nightly" | "posts" | "appearance" | "outfit" | "scene" | "window_action" | "desktop_life" | "speech_input" | "companion_voice" | "speech_preview" | "voice_design" | "voice_clone"
  "AiTaskKeyInput": "companion_chat" | "work_chat" | "scheduled_task" | "proactive" | "memory_review" | "memory_embedding" | "nightly" | "posts" | "appearance" | "outfit" | "scene" | "window_action" | "desktop_life" | "speech_input" | "companion_voice" | "speech_preview" | "voice_design" | "voice_clone"
  "ApiError": { "code": string; "message": string; "details": ApiSchemas["JsonValue"]; "request_id": string }
  "ApiErrorResponse": { "error": ApiSchemas["ApiError"] }
  "AvatarAssetResponse": { "id": number; "asset_url": string; "seed_fullbody_url": string; "is_fullbody_confirmed": boolean; "prompt": string; "status": "succeeded" }
  "AvatarFromImageRequest": { "source": "reference"; "image": string; "content_type"?: (string | null); "description"?: (string | null); "presentation_image"?: (string | null); "presentation_content_type"?: (string | null) }
  "AvatarGenerateRequest": { "source": "generate"; "mode"?: ("edit" | "regenerate" | null); "feedback"?: (string | null) }
  "AvatarHistoryResponse": { "history": Array<ApiSchemas["AvatarAssetResponse"]> }
  "AvatarPromptRequest": { "feedback"?: (string | null); "has_reference"?: boolean }
  "CapabilityCard": { "id": string; "provider_id": string; "model_name": string; "context_window": (number | null); "max_output_tokens": (number | null); "input_modalities": Array<ApiSchemas["ModelModality"]>; "output_modalities": Array<ApiSchemas["ModelModality"]> }
  "CapabilityCardInput": { "id": string; "provider_id": string; "model_name": string; "context_window"?: (number | null); "max_output_tokens"?: (number | null); "input_modalities": Array<ApiSchemas["ModelModalityInput"]>; "output_modalities": Array<ApiSchemas["ModelModalityInput"]> }
  "CapabilityChains": { "llm": Array<ApiSchemas["CapabilityCard"]>; "stt": Array<ApiSchemas["CapabilityCard"]>; "tts": Array<ApiSchemas["CapabilityCard"]>; "image_gen": Array<ApiSchemas["CapabilityCard"]>; "video_gen": Array<ApiSchemas["CapabilityCard"]>; "embedding": Array<ApiSchemas["CapabilityCard"]> }
  "CapabilityChainsInput": { "llm"?: Array<ApiSchemas["CapabilityCardInput"]>; "stt"?: Array<ApiSchemas["CapabilityCardInput"]>; "tts"?: Array<ApiSchemas["CapabilityCardInput"]>; "image_gen"?: Array<ApiSchemas["CapabilityCardInput"]>; "video_gen"?: Array<ApiSchemas["CapabilityCardInput"]>; "embedding"?: Array<ApiSchemas["CapabilityCardInput"]> }
  "ChainPolicy": { "chain": Array<string> }
  "ChainPolicyInput": { "chain"?: Array<string> }
  "CharacterCardExtract": { "expected_avatar_id": number; "expected_revision": number }
  "CharacterCardResponse": { "avatar_id": number; "revision": number; "features": ApiSchemas["CharacterFeatures"]; "overrides": ApiSchemas["CharacterOverrides"]; "automatic": ApiSchemas["CharacterFeatures"]; "status": "pending" | "running" | "ready" | "failed"; "portrait_status": "pending" | "running" | "ready" | "failed"; "body_status": "pending" | "running" | "ready" | "failed"; "error": (string | null) }
  "CharacterCardUpdate": { "expected_avatar_id": number; "expected_revision": number; "changes": ApiSchemas["CharacterOverridesInput"] }
  "CharacterFeatures": { "body_shape": string; "proportions": string; "limbs_and_appendages": string; "body_surface": string; "head_shape": string; "facial_features": string; "facial_surface": string; "head_identifiers": string }
  "CharacterOverrides": { "head_shape": (string | null); "facial_features": (string | null); "facial_surface": (string | null); "head_identifiers": (string | null); "body_shape": (string | null); "proportions": (string | null); "limbs_and_appendages": (string | null); "body_surface": (string | null) }
  "CharacterOverridesInput": { "head_shape"?: (string | null); "facial_features"?: (string | null); "facial_surface"?: (string | null); "head_identifiers"?: (string | null); "body_shape"?: (string | null); "proportions"?: (string | null); "limbs_and_appendages"?: (string | null); "body_surface"?: (string | null) }
  "ChatAttachment": { "type": "image" | "video" | "audio"; "file_url": string }
  "ChatAudioUploadResponse": { "url": string; "mime_type": string; "duration": number }
  "ChatRequestClientContext": { "environment_hints"?: (string | null); "platform_hints"?: (string | null) }
  "ChatVideoUploadResponse": { "url": string }
  "ClientMediaBubble": { "type": "image" | "video"; "media_id": string; "status": "pending" | "ready" | "failed" | "result_unknown"; "url": (string | null); "error": (string | null); "video_queue": (ApiSchemas["VideoQueueInfo"] | null) }
  "ClientMediaEntry": { "type": "image" | "video" | "audio"; "url": string; "audio_url": (string | null); "duration": (number | null); "transcript": (string | null); "review_id": (string | null) }
  "CommandDispatchRequest": { "command": string; "args"?: (Array<(string | number | number)> | string | null); "confirmed"?: boolean }
  "CommandDispatchResult": { "command": string; "result": ApiSchemas["CommandResult"] }
  "CommandListResponse": { "commands": Array<ApiSchemas["CommandSummary"]> }
  "CommandResult": { "status": "ok" | "error"; "message": string; "payload": (ApiSchemas["SessionClearResult"] | ApiSchemas["SessionCompressionResult"] | ApiSchemas["RememberCommandResult"] | null); "hydrate": boolean }
  "CommandSummary": { "name": string; "aliases": Array<string>; "description": string; "requires_confirmation": boolean }
  "CompanionOperationResponse": { "ok": boolean }
  "CompanionSessionMessageView": { "id": number; "role": "assistant"; "content": (string | null); "subtype": string; "reasoning": (string | null); "timestamp": number; "can_replace_reply": boolean; "media": Array<ApiSchemas["ClientMediaEntry"]>; "tool_calls": ApiSchemas["JsonValue"]; "tool_call_id": (string | null); "tool_name": string; "content_type": "companion_reply"; "bubbles": Array<ApiSchemas["ReplyBubbleView"]> }
  "CompressionUsage": { "total_tokens": number; "context_window": number }
  "CustomVoiceSummary": { "candidate_id": string; "kind": "design" | "clone"; "voice_id": string; "prompt": string; "preview_audio_url": string; "reference_audio_url": (string | null); "provider": string; "model": string; "version": number; "available": boolean }
  "DesktopConfigPutRequest": { "config": { [key: string]: ApiSchemas["JsonValueInput"] } }
  "DesktopConfigResponse": { "config": { [key: string]: ApiSchemas["JsonValue"] } }
  "DesktopSessionInfo": { "id": string; "kind": string; "title": (string | null); "started_at": number; "last_active": number; "message_count": number; "input_tokens": number; "output_tokens": number; "tool_call_count": number; "preview": (string | null); "pinned": boolean; "archived": boolean; "system_preset_id": (string | null); "system_preset_icon_key": (string | null) }
  "DesktopSessionListResponse": { "limit": number; "offset": number; "total": number; "sessions": Array<ApiSchemas["DesktopSessionInfo"]> }
  "DesktopSessionOperationResponse": { "ok": boolean }
  "DesktopSessionPatchRequest": { "title"?: (string | null); "pinned"?: (boolean | null); "archived"?: (boolean | null); "settings"?: (ApiSchemas["SessionSettingsPatch"] | null) }
  "DesktopVideoActionResponse": { "id": number; "set_id": number; "current": (ApiSchemas["DesktopVideoCurrentResponse"] | null); "draft": (ApiSchemas["DesktopVideoDraftResponse"] | null); "playable": boolean; "unavailable_reason": (string | null); "needs_update": boolean; "key": string; "name": string; "description": string; "kind": "loop" | "once"; "duration_seconds": number; "target_duration_seconds": number; "status": "queued" | "processing" | "ready" | "failed" | "review_pending"; "stage": string; "error": (string | null); "video_queue": (ApiSchemas["VideoQueueInfo"] | null); "enabled": boolean; "preset": boolean; "use_when": Array<string>; "avoid_when": Array<string>; "allowed_triggers": Array<"periodic" | "user_return" | "chat" | "manual">; "continuity": "independent" | "base"; "transition": "base" | "fade"; "base_version": (number | null); "poster_url": string; "video_url": string; "version": number; "actual_duration_ms": (number | null) }
  "DesktopVideoCancelCommand": { "draft_id"?: (string | null); "command": "cancel" }
  "DesktopVideoClaimRequest": { "client_id": string }
  "DesktopVideoConfirmImageCommand": { "draft_id": string; "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "generate_video"?: boolean; "command": "confirm-image" }
  "DesktopVideoCreateSetRequest": { "request_id": string; "name": string; "scene_id": number; "outfit_id": number; "source"?: "ai" | "image_upload" | "video_upload"; "feedback"?: string; "target_width": number; "target_height": number }
  "DesktopVideoCurrentResponse": { "video_url": string; "poster_url": string; "image_url": string; "duration_ms": number; "width": number; "height": number; "geometry_mode": "target" | "keep"; "continuity": "independent" | "base"; "base_version": (number | null); "transition": "base" | "fade" }
  "DesktopVideoDecisionRequest": { "draft_id": string; "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "decision": "accept" | "reject" }
  "DesktopVideoDesignRequest": { "name": string; "motion_description": string; "kind"?: "loop" | "once"; "continuity"?: "independent" | "base"; "allowed_triggers"?: Array<"periodic" | "user_return" | "chat" | "manual">; "duration_seconds"?: number; "use_when"?: Array<string>; "avoid_when"?: Array<string>; "reason"?: string; "expected_set_id"?: (number | null) }
  "DesktopVideoDraftResponse": { "id": string; "stage": string; "source": "user_requested" | "autonomous" | "external_upload"; "target_width": number; "target_height": number; "resolution_revision": number; "geometry_mode": "target" | "keep"; "duration_seconds": number; "video_refinement": boolean; "frame_width": (number | null); "frame_height": (number | null); "feedback": string; "submission_unknown": boolean; "image_url": string; "image_confirmed": boolean; "candidate_video_url": string; "candidate_poster_url": string; "review_reason": string; "base_version": (number | null); "error": (string | null); "video_queue": (ApiSchemas["VideoQueueInfo"] | null) }
  "DesktopVideoEnsureRequest": { "trigger"?: "entry"; "target_width": number; "target_height": number }
  "DesktopVideoExternalPromptRequest": { "expected_set_id"?: (number | null); "requirements"?: string; "expected_draft_id"?: (string | null); "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "aspect_policy"?: "ask" | "keep" }
  "DesktopVideoExternalPromptResponse": { "draft_id": (string | null); "base_version": number; "action_id": number; "set_id": number; "name": string; "description": string; "kind": "loop" | "once"; "duration_seconds": number; "prompt": string; "references": Array<ApiSchemas["DesktopVideoReference"]> }
  "DesktopVideoGenerateCommand": { "feedback"?: string; "expected_draft_id"?: (string | null); "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "aspect_policy"?: "ask" | "keep" | "expand"; "command": "generate-video" }
  "DesktopVideoImageCommand": { "feedback"?: string; "regenerate"?: boolean; "expected_draft_id"?: (string | null); "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "aspect_policy"?: "ask" | "keep" | "expand"; "command": "generate-image" }
  "DesktopVideoListResponse": { "sets": Array<ApiSchemas["DesktopVideoSetResponse"]>; "version": number }
  "DesktopVideoPauseCommand": { "draft_id": string; "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "command": "pause" }
  "DesktopVideoPlayCommand": { "play_id": string; "set_id": number; "action_id": number; "set_epoch": number; "kind": "loop" | "once"; "expires_at": string; "video_url": string; "poster_url": string; "geometry_mode": "target" | "keep"; "version": number; "transition": "base" | "fade"; "continuity": "independent" | "base"; "base_version": (number | null) }
  "DesktopVideoPlayRequest": { "action_id": number; "expected_set_id"?: (number | null); "reason"?: string }
  "DesktopVideoPreferences": { "pinned"?: (boolean | null); "autonomous_enabled"?: (boolean | null) }
  "DesktopVideoProposalResponse": { "proposal_id": number; "set_id": number; "action_id": (number | null); "status": string; "review_reason": string; "name": string; "description": string; "created_at": (string | null) }
  "DesktopVideoReceipt": { "client_id": string; "status": "started" | "completed" | "interrupted" | "failed" | "rejected"; "error"?: (string | null) }
  "DesktopVideoReference": { "label": string; "url": string }
  "DesktopVideoRefineCommand": { "source": "current" | "candidate"; "feedback"?: string; "aspect_policy"?: "keep" | "expand"; "expected_action_version": number; "expected_draft_id"?: (string | null); "expected_base_version": number; "expected_resolution_revision": number; "command": "refine-video" }
  "DesktopVideoRefinePreviewResponse": { "duration_seconds": number; "width": number; "height": number }
  "DesktopVideoRefineRequest": { "source": "current" | "candidate"; "feedback"?: string; "aspect_policy"?: "keep" | "expand"; "expected_action_version": number; "expected_draft_id"?: (string | null); "expected_base_version": number; "expected_resolution_revision": number }
  "DesktopVideoRenameSetRequest": { "name": string }
  "DesktopVideoResumeCommand": { "draft_id": string; "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "command": "resume" }
  "DesktopVideoSelectionRequest": { "set_id": number }
  "DesktopVideoSetResponse": { "id": number; "title": string; "target_width": number; "target_height": number; "resolution_revision": number; "is_default": boolean; "source": "ai" | "image_upload" | "video_upload"; "outfit_id": (number | null); "scene_id": (number | null); "status": string; "actions": Array<ApiSchemas["DesktopVideoActionResponse"]>; "proposals": Array<ApiSchemas["DesktopVideoProposalResponse"]>; "created_at": string; "is_current": boolean; "base_image_url": string; "base_version": number }
  "DesktopVideoStateResponse": { "current": (ApiSchemas["DesktopVideoSetResponse"] | null); "selected_action_id": (number | null); "loop_action_id": (number | null); "set_epoch": number; "pinned": boolean; "autonomous_enabled": boolean; "version": number; "preparation_error": (string | null) }
  "DeviceListResponse": { "items": Array<ApiSchemas["DeviceResponse"]> }
  "DeviceResponse": { "id": number; "name": string; "expires_at": string; "last_seen_at": string; "created_at": string }
  "DiaryEntryResponse": { "id": string; "entry_date": string; "title": string; "body": string; "post_ids": Array<string>; "created_at": (string | null); "updated_at": (string | null) }
  "DiaryListResponse": { "entries": Array<ApiSchemas["DiaryEntryResponse"]>; "unread_diary_ids": Array<string> }
  "DiaryReadRequest": { "diary_ids": Array<string> }
  "DiaryUnreadResponse": { "has_unread": boolean }
  "ExchangeRequest": { "token": string; "device_name"?: string }
  "FullbodyAdoptRequest": { "image": string; "content_type"?: (string | null) }
  "FullbodyCandidateResponse": { "id": number; "avatar_id": number; "image_url": string; "status": "pending" | "ready" | "failed" | "accepted" | "rejected"; "error": (string | null) }
  "FullbodyConfirmRequest": { "expected_url": string }
  "FullbodyPromptRequest": { "feedback"?: (string | null) }
  "FullbodyReferenceGenerateRequest": { "feedback"?: (string | null); "image"?: (string | null); "content_type"?: (string | null); "mode": "edit" | "regenerate"; "candidate_id"?: (number | null) }
  "GatewayTicketRequest": { "stream_id"?: (string | null) }
  "GatewayTicketResponse": { "ticket": string; "expires_in": number }
  "IdleExpressionRequest": { "trigger"?: "periodic" | "user_return"; "idle_seconds"?: (number | null); "local_hour"?: (number | null) }
  "IdleExpressionResult": { "expressed": boolean; "action_id": (number | null); "reason": string }
  "ImageClipUpload": { "media_type": "image"; "action": string; "data": string; "content_type"?: (string | null) }
  "ImagePromptResponse": { "prompt": string }
  "InteractionStatsRequest": { "kind": "chat_turn"; "hour": number }
  "InteractionStatsResult": { "recorded": "chat_turn"; "threshold_met": boolean; "peak_hour": (number | null) }
  "JsonValue": unknown
  "JsonValueInput": unknown
  "LastSubmissionSnapshot": { "request_id": string; "status": string; "error": (string | null); "message_ids": Array<number>; "retry_message_id": (number | null); "replace_reply_message_id": (number | null); "result_message_id": (number | null) }
  "LinkBubble": { "type": "link"; "topic_id": number; "title": string; "platform": "x" | "xiaohongshu" | "douyin"; "url": string; "published_at": string }
  "MediaReviewResponse": { "id": number; "status": "pending" | "accepted" | "rejected"; "reason": string; "media_type": "image" | "video"; "media_url": string; "title": string; "system_slot": string }
  "MemoryDeleteResponse": { "deleted": boolean }
  "MemoryEvidence": { "fingerprint": string; "message_id"?: (number | null); "session_id"?: (number | null); "quote"?: (string | null); "stance"?: ("supports" | "opposes" | null); "created_at"?: (string | null); "source_unavailable"?: (boolean | null) }
  "MemoryListItem": { "id": number; "content_version": number; "content": string; "context": (string | null); "tags": (string | null); "basis": "explicit" | "inferred" | "observed" | "system"; "status": "active" | "candidate" | "invalidated" | "forgotten"; "usage": "contextual" | "background"; "reason": string; "source_kind": string; "updated_at": string; "evidence": Array<ApiSchemas["MemoryEvidence"]>; "expires_at": (string | null); "reviewed_at": (string | null); "system_preset_id": string; "importance": number; "created_at": string }
  "MemoryListResponse": { "memories": Array<ApiSchemas["MemoryListItem"]>; "counts": { [key: string]: number }; "total": number; "system_preset_id": string }
  "MemoryProfileChange": { "field": "user_call_name" | "user_gender" | "user_birthday" | "user_hobbies" | "user_freeform"; "value": (string | null); "expected_version": number }
  "MemoryProfileRequest": { "system_preset_id": "companion"; "changes": Array<ApiSchemas["MemoryProfileChange"]> }
  "MemoryProfileResponse": { "memories": Array<ApiSchemas["MemoryListItem"]>; "system_preset_id": "companion" }
  "MemoryUpdateRequest": { "system_preset_id": string; "content": string; "expected_version": number }
  "MessageResponse": { "message": string }
  "ModelDiscoveryRequest": { "provider": ApiSchemas["ProviderCardUpdate"]; "user_id"?: (number | null); "service": "llm" | "stt" | "tts" | "image_gen" | "video_gen" | "embedding"; "model_name"?: (string | null); "catalog_id"?: (string | null); "refresh"?: boolean }
  "ModelDiscoveryResponse": { "models": Array<ApiSchemas["ModelMetadata"]>; "candidates": { [key: string]: Array<ApiSchemas["ModelMetadata"]> }; "warnings": Array<string>; "unsupported": boolean; "catalog_updated_at": (string | null) }
  "ModelMetadata": { "model_name": string; "display_name": string; "services": Array<string>; "context_window": (number | null); "max_output_tokens": (number | null); "input_modalities": (Array<ApiSchemas["ModelModality"]> | null); "output_modalities": (Array<ApiSchemas["ModelModality"]> | null); "catalog_id": (string | null); "canonical_slug": (string | null); "catalog_url": (string | null); "field_sources": { [key: string]: string } }
  "ModelModality": "text" | "image" | "audio" | "video" | "embeddings"
  "ModelModalityInput": "text" | "image" | "audio" | "video" | "embeddings"
  "NightlyActivityLogItem": { "id": number; "user_id": number; "target_date": string; "status": string; "summary": (string | null); "payload": ({ [key: string]: unknown } | null); "created_at": (string | null); "updated_at": (string | null) }
  "NightlyActivityLogListResponse": { "items": Array<ApiSchemas["NightlyActivityLogItem"]> }
  "OnboardingPatchRequest": { "field": string; "value"?: (string | null) }
  "OnboardingStateResponse": { "answers": { [key: string]: string }; "next_field": (string | null); "complete": boolean }
  "OutfitAdoptRequest": { "source": "import"; "description"?: (string | null); "image": string; "content_type"?: (string | null) }
  "OutfitConfirmRequest": Record<string, never>
  "OutfitCreateRequest": { "source": "generate"; "description"?: (string | null); "image"?: (string | null); "content_type"?: (string | null) }
  "OutfitImageImportRequest": { "image": string; "content_type"?: (string | null); "source": "import" }
  "OutfitListResponse": { "outfits": Array<ApiSchemas["OutfitResponse"]>; "policy": "locked" | "llm_may_replace" }
  "OutfitPolicyRequest": { "policy": "locked" | "llm_may_replace" }
  "OutfitPolicyResponse": { "policy": "locked" | "llm_may_replace" }
  "OutfitPromptRequest": { "outfit_id"?: (number | null); "feedback"?: (string | null); "description"?: (string | null); "image"?: (string | null); "content_type"?: (string | null) }
  "OutfitRegenerateRequest": { "source": "generate"; "feedback"?: (string | null); "mode": "edit" | "regenerate" }
  "OutfitResponse": { "initial_video_error": (string | null); "description_status": "pending" | "processing" | "ready" | "failed"; "description_error": (string | null); "id": number; "name": string; "description": (string | null); "fullbody_url": string; "status": string; "active": boolean }
  "PairingResponse": { "id": number; "url"?: (string | null); "expires_at": string; "state": "pending" | "paired" | "cancelled" | "expired" }
  "PeekGeometry": { "side": "left" | "right"; "cut_x": number; "focus_rect": [number, number, number, number] }
  "PendingActionReview": { "id": number; "media_type": "image" | "video"; "media_url": string }
  "PersonaResponse": { "definition_json": string; "is_complete": boolean; "personality_tags": Array<string>; "current_mood": (string | null) }
  "PersonaUpdate": { "definition_json": string; "expected_fields"?: ({ [key: string]: (string | null) } | null) }
  "PlainSessionMessageView": { "id": number; "role": "assistant" | "system" | "tool" | "user"; "content": (string | null); "subtype": string; "reasoning": (string | null); "timestamp": number; "can_replace_reply": boolean; "media": Array<ApiSchemas["ClientMediaEntry"]>; "tool_calls": ApiSchemas["JsonValue"]; "tool_call_id": (string | null); "tool_name": string; "content_type": "multimodal_v1" | "text" }
  "PortraitConfirmRequest": { "expected_avatar_id": number }
  "PostCommentCreateRequest": { "content": string }
  "PostCommentResponse": { "id": string; "post_id": string; "role": ApiSchemas["PostCommentRole"]; "content": string; "created_at": string; "updated_at": string; "reply_to_comment_id": (string | null); "reply_status": "none" | "pending" | "running" | "completed" | "failed"; "reply_error": (string | null) }
  "PostCommentRole": "user" | "companion"
  "PostContentType": "text" | "image" | "video" | "audio"
  "PostListResponse": { "posts": Array<ApiSchemas["PostResponse"]>; "next_cursor": (string | null); "unread_post_ids": Array<string> }
  "PostPublicationRecovery": { "publication_id": string; "status": "queued" | "running" | "published" | "partial" | "failed" | "blocked" | "result_unknown" | "review_pending" | "declined" | "discarded"; "post_id": (string | null); "error": (string | null); "title": string; "video_status": "pending" | "ready" | "failed" | "unknown" | "discarded"; "media_url": (string | null); "can_adopt": boolean; "can_discard": boolean }
  "PostPublicationRecoveryList": { "items": Array<ApiSchemas["PostPublicationRecovery"]>; "next_offset": (number | null) }
  "PostPublicationResult": { "publication_id": string; "status": "queued" | "running" | "published" | "partial" | "failed" | "blocked" | "result_unknown" | "review_pending" | "declined" | "discarded"; "post_id": (string | null); "error": (string | null) }
  "PostReadRequest": { "post_ids": Array<string> }
  "PostResponse": { "id": string; "published_at": string; "content_type": ApiSchemas["PostContentType"]; "title": string; "body": string; "media_url": (string | null); "audio_url": (string | null); "comments": Array<ApiSchemas["PostCommentResponse"]> }
  "PostUnreadResponse": { "has_unread": boolean }
  "PromptBatchMessage": { "text": string; "attachments"?: (Array<ApiSchemas["ChatAttachment"]> | null) }
  "PromptPresetListResponse": { "presets": Array<ApiSchemas["PromptPresetSummary"]> }
  "PromptPresetSummary": { "id": string; "name": string; "description": string; "icon_key": string }
  "PromptSubmitRequest": { "request_id": string; "text"?: (string | null); "attachments"?: (Array<ApiSchemas["ChatAttachment"]> | null); "batch"?: (Array<ApiSchemas["PromptBatchMessage"]> | null); "edit_message_id"?: (number | null); "retry_message_id"?: (number | null); "replace_reply_message_id"?: (number | null); "response_preference"?: ("text" | "voice" | null) }
  "PromptSubmitResult": { "queued": boolean; "request_id": string; "status": "accepted" | "running" | "completed" | "cancelled" | "interrupted" | "failed"; "error": (string | null); "message_ids": Array<number>; "messages": Array<ApiSchemas["SessionMessageView"]>; "replace_reply_message_id": (number | null); "result_message_id": (number | null) }
  "ProviderCardPublic": { "id": string; "name": string; "provider_type": string; "api_key": string; "base_url": string; "api_key_set": boolean }
  "ProviderCardUpdate": { "id": string; "name": string; "provider_type": string; "api_key"?: string; "base_url"?: string; "api_key_set"?: boolean; "clear_api_key"?: boolean }
  "ProviderDefaults": { "base_url": string; "requires_api_key": boolean; "supports_voice_design": boolean; "voice_design_model": (string | null); "supports_voice_clone": boolean; "voice_clone_model": (string | null); "input_modalities": Array<ApiSchemas["ModelModality"]> }
  "RefreshRequest": { "client_version"?: string; "client_context"?: (ApiSchemas["ChatRequestClientContext"] | null) }
  "RegenerateActivationRequest": { "base_url"?: (string | null) }
  "ReleaseManifestFileItem": { "url": string; "sha512": string; "size": number }
  "ReleaseManifestResponse": { "version": string; "releaseDate": string; "releaseNotes": string; "path": string; "sha512": string; "files": Array<ApiSchemas["ReleaseManifestFileItem"]> }
  "RememberCommandResult": { "memory_id": number; "content": string }
  "RemoteSessionResponse": { "user": ApiSchemas["RemoteUserResponse"]; "device": ApiSchemas["DeviceResponse"]; "csrf_token": string }
  "RemoteUserResponse": { "id": number; "username": string }
  "ReplyAudio": { "url": string; "duration": number }
  "ReplyBubbleView": (ApiSchemas["TextBubble"] | ApiSchemas["VoiceBubbleView"] | ApiSchemas["ClientMediaBubble"] | ApiSchemas["LinkBubble"])
  "ResourceDecision": { "decision": "accept" | "reject" }
  "RpcActionCatalogEvent": { "packId": number; "catalogVersion": number; "appearanceEpoch": number }
  "RpcActionJobEvent": { "packId": number; "outfitId": (number | null); "actionId": (number | null); "action": string; "stage": string }
  "RpcActionPlayCommand": { "play_id": string; "pack_id": number; "appearance_epoch": number; "action_id": number; "asset_revision_id": (number | null); "repeat_count": number; "expires_at": (string | null); "source": string }
  "RpcCacheUsage": { "request_count": number; "reported_request_count": number; "input_tokens": (number | null); "cached_input_tokens": (number | null); "cache_hit_rate": (number | null) }
  "RpcCharacterCardEvent": { "avatar_id": number; "revision": number; "status": "pending" | "running" | "ready" | "failed" }
  "RpcClientMediaBubble": { "type": "image" | "video"; "media_id": string; "status": "pending" | "ready" | "failed" | "result_unknown"; "url": (string | null); "error": (string | null); "video_queue": (ApiSchemas["RpcVideoQueueInfo"] | null) }
  "RpcClientMediaEntry": { "type": "image" | "video" | "audio"; "url": string; "audio_url": (string | null); "duration": (number | null); "transcript": (string | null); "review_id": (string | null) }
  "RpcCommandDispatchResult": { "command": string; "result": ApiSchemas["RpcCommandResult"] }
  "RpcCommandResult": { "status": "ok" | "error"; "message": string; "payload": (ApiSchemas["RpcSessionClearResult"] | ApiSchemas["RpcSessionCompressionResult"] | ApiSchemas["RpcRememberCommandResult"] | null); "hydrate": boolean }
  "RpcCompanionMessageEvent": { "session_id": string; "message_id": number; "bubbles": Array<ApiSchemas["RpcReplyBubbleView"]> }
  "RpcCompanionSessionMessageView": { "id": number; "role": "assistant"; "content": (string | null); "subtype": string; "reasoning": (string | null); "timestamp": number; "can_replace_reply": boolean; "media": Array<ApiSchemas["RpcClientMediaEntry"]>; "tool_calls": ApiSchemas["RpcJsonValue"]; "tool_call_id": (string | null); "tool_name": string; "content_type": "companion_reply"; "bubbles": Array<ApiSchemas["RpcReplyBubbleView"]> }
  "RpcCompanionSignalResult": { "queued": boolean }
  "RpcCompressionEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "session_id": (string | null); "subtype": string; "text": string; "message_id": (number | null) }
  "RpcCompressionUsage": { "total_tokens": number; "context_window": number }
  "RpcDesktopVideoPlayCommand": { "play_id": string; "set_id": number; "action_id": number; "set_epoch": number; "kind": "loop" | "once"; "expires_at": string; "video_url": string; "poster_url": string; "geometry_mode": "target" | "keep"; "version": number; "transition": "base" | "fade"; "continuity": "independent" | "base"; "base_version": (number | null) }
  "RpcDesktopVideoUpdatedEvent": { "version": number }
  "RpcDiaryDeletedEvent": { "diary_id": string; "entry_date": string }
  "RpcDiaryEntryResponse": { "id": string; "entry_date": string; "title": string; "body": string; "post_ids": Array<string>; "created_at": (string | null); "updated_at": (string | null) }
  "RpcEmptyResult": { [key: string]: unknown }
  "RpcGatewayAckResult": { "acked": number; "pruned": number }
  "RpcGatewayErrorEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "message": string; "retry_message_id": (number | null) }
  "RpcGatewayResumeResult": { "stream_id": string; "current_seq": number; "recovery": "replayed" | "snapshot_required"; "replayed_count": number; "pending_calls": Array<string> }
  "RpcInputCompanionSignal": { "available": boolean; "presentation_mode": "desktop" | "window"; "event"?: ("desktop_available" | "context_changed" | null) }
  "RpcInputEmptyRequest": Record<string, never>
  "RpcInputGatewayAckRequest": { "seq": number }
  "RpcInputGatewayResumeRequest": { "stream_id"?: (string | null); "last_seq"?: (number | null) }
  "RpcInputJsonValue": unknown
  "RpcInputRunnerToolSchema": { "name": string; "description"?: string; "parameters"?: { [key: string]: ApiSchemas["RpcInputJsonValue"] }; [key: string]: unknown }
  "RpcInputToolResultRequest": { "call_id": string; "result": ApiSchemas["RpcInputJsonValue"] }
  "RpcInputToolsSyncRequest": { "skill_scope_version": 1; "tools": Array<ApiSchemas["RpcInputRunnerToolSchema"]> }
  "RpcJsonValue": unknown
  "RpcLinkBubble": { "type": "link"; "topic_id": number; "title": string; "platform": "x" | "xiaohongshu" | "douyin"; "url": string; "published_at": string }
  "RpcMemoryChangedEvent": { "system_preset_id": string; "memory_id": (number | null) }
  "RpcMessageAudioEvent": { "session_id": string; "message_id": number; "audio_url": string; "duration": (number | null); "transcript": (string | null) }
  "RpcMessageBubbleEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "message_id": number; "bubble_index": number; "bubble": ApiSchemas["RpcReplyBubbleView"] }
  "RpcMessageCompleteEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "text": (string | null); "bubbles": (Array<ApiSchemas["RpcReplyBubbleView"]> | null); "reasoning": (string | null); "media": (Array<ApiSchemas["RpcClientMediaEntry"]> | null); "usage": (ApiSchemas["RpcTokenUsage"] | null); "cache_usage": (ApiSchemas["RpcCacheUsage"] | null); "message_id": (number | null); "can_replace_reply": boolean }
  "RpcMessageDeletedEvent": { "session_id": string; "messages": Array<ApiSchemas["RpcSessionMessageView"]>; "deleted_count": number; "anchor": ApiSchemas["RpcUndoAnchor"] }
  "RpcMessageDeltaEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "text": string }
  "RpcMessageEditedEvent": { "session_id": string; "messages": Array<ApiSchemas["RpcSessionMessageView"]> }
  "RpcMessageMediaEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "message_id": number; "bubble_index": number; "bubble": ApiSchemas["RpcClientMediaBubble"]; "session_id": string; "media_id": string }
  "RpcMessagePersistedEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "role": string; "message_ids": Array<number>; "messages": Array<ApiSchemas["RpcSessionMessageView"]> }
  "RpcMessageReplacedEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "session_id": string; "replaced_message_id": number; "message": ApiSchemas["RpcSessionMessageView"]; "history_revision": number }
  "RpcMessageVoiceEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "message_id": number; "bubble_index": number; "bubble": ApiSchemas["RpcVoiceBubbleView"]; "session_id": string }
  "RpcMoodEvent": { "mood": string }
  "RpcOutfitChangedEvent": { "outfit_id": number; "worn": boolean }
  "RpcPackEvent": { "packId": number; "outfitId": (number | null) }
  "RpcPackFailedEvent": { "packId": number; "outfitId": (number | null); "reason": string }
  "RpcPackProgressEvent": { "packId": number; "outfitId": (number | null); "stage": string; "action": (string | null); "video_queue": (ApiSchemas["RpcVideoQueueInfo"] | null) }
  "RpcPlainSessionMessageView": { "id": number; "role": "assistant" | "system" | "tool" | "user"; "content": (string | null); "subtype": string; "reasoning": (string | null); "timestamp": number; "can_replace_reply": boolean; "media": Array<ApiSchemas["RpcClientMediaEntry"]>; "tool_calls": ApiSchemas["RpcJsonValue"]; "tool_call_id": (string | null); "tool_name": string; "content_type": "multimodal_v1" | "text" }
  "RpcPostCommentDeletedEvent": { "post_id": string; "comment_id": string }
  "RpcPostCommentEvent": { "post_id": string; "comment": ApiSchemas["RpcPostCommentResponse"] }
  "RpcPostCommentResponse": { "id": string; "post_id": string; "role": ApiSchemas["RpcPostCommentRole"]; "content": string; "created_at": string; "updated_at": string; "reply_to_comment_id": (string | null); "reply_status": "none" | "pending" | "running" | "completed" | "failed"; "reply_error": (string | null) }
  "RpcPostCommentRole": "user" | "companion"
  "RpcPostContentType": "text" | "image" | "video" | "audio"
  "RpcPostResponse": { "id": string; "published_at": string; "content_type": ApiSchemas["RpcPostContentType"]; "title": string; "body": string; "media_url": (string | null); "audio_url": (string | null); "comments": Array<ApiSchemas["RpcPostCommentResponse"]> }
  "RpcRememberCommandResult": { "memory_id": number; "content": string }
  "RpcReplyAudio": { "url": string; "duration": number }
  "RpcReplyBubbleView": (ApiSchemas["RpcTextBubble"] | ApiSchemas["RpcVoiceBubbleView"] | ApiSchemas["RpcClientMediaBubble"] | ApiSchemas["RpcLinkBubble"])
  "RpcSceneChangedEvent": { "scene_id": (number | null); "version": number; "switch_version": number }
  "RpcSessionClearResult": { "session_id": string; "cleared_count": number; "messages": Array<ApiSchemas["RpcSessionMessageView"]> }
  "RpcSessionCompressionResult": { "session_id": string; "compressed": boolean; "reason": (string | null); "usage": ApiSchemas["RpcCompressionUsage"]; "replaced_count": (number | null); "summary": (string | null); "messages": (Array<ApiSchemas["RpcSessionMessageView"]> | null) }
  "RpcSessionListChangedEvent": { "session_id": string; "deleted": boolean }
  "RpcSessionMessageView": (ApiSchemas["RpcPlainSessionMessageView"] | ApiSchemas["RpcCompanionSessionMessageView"])
  "RpcSessionStateEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "messages": (Array<ApiSchemas["RpcSessionMessageView"]> | null); "running": boolean; "status": string; "error": (string | null); "result_message_id": (number | null) }
  "RpcSystemNotificationEvent": { "kind": "error" | "success"; "title": string; "message": string; "session_id": string }
  "RpcTextBubble": { "type": "text"; "text": string }
  "RpcTokenUsage": { "prompt_tokens": (number | null); "completion_tokens": (number | null); "total_tokens": (number | null); "input_tokens": (number | null); "output_tokens": (number | null); "reasoning_tokens": (number | null); "context_window": (number | null) }
  "RpcToolCallEvent": { "call_id": string; "name": string; "args": { [key: string]: ApiSchemas["RpcJsonValue"] }; "session_id": (string | null); "headless": boolean; "skill_scope": (ApiSchemas["RpcToolScope"] | null) }
  "RpcToolCancelEvent": { "call_id": string }
  "RpcToolProgressEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null); "name": (string | null); "call_id": (string | null); "status": "running" | "complete" }
  "RpcToolResultResult": { "status": "accepted" | "no_longer_pending" }
  "RpcToolScope": { "user_id": number; "system_preset_id": string }
  "RpcToolsSyncResult": { "count": number }
  "RpcTopicFeedbackSnapshot": { "topic_id": number; "value": ("like" | "less" | null); "updated_at": (string | null) }
  "RpcTurnEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (number | null) }
  "RpcUndoAnchor": { "text": string; "attachments": Array<ApiSchemas["RpcUndoAttachment"]> }
  "RpcUndoAttachment": { "type": "image"; "url": string }
  "RpcVideoCompletedEvent": { "task_id": string; "session_id": (string | null); "url": string; "media": Array<ApiSchemas["RpcClientMediaEntry"]>; "warning": (string | null) }
  "RpcVideoFailedEvent": { "task_id": string; "session_id": (string | null); "error": (string | null); "status_message_id": (number | null); "status_text": (string | null) }
  "RpcVideoQueueEvent": { "task_id": string; "session_id": (string | null); "media_id": (string | null); "video_queue": (ApiSchemas["RpcVideoQueueInfo"] | null) }
  "RpcVideoQueueInfo": { "state": "waiting" | "rate_limited" | "blocked"; "next_retry_at": (string | null) }
  "RpcVoiceBubbleView": { "type": "voice"; "text": string; "audio": (ApiSchemas["RpcReplyAudio"] | null) }
  "RpcVoiceUpdatedEvent": { "voice_id": string; "version": number }
  "RunnerInputCallCancelRequest": (ApiSchemas["RunnerInputCancelRequestTarget"] | ApiSchemas["RunnerInputCancelCallTarget"])
  "RunnerInputCallGetRequest": { "call_id": string }
  "RunnerInputCancelCallTarget": { "call_id": string }
  "RunnerInputCancelRequestTarget": { "req_id": (string | number) }
  "RunnerInputConfigReplaceRequest": { "config": { [key: string]: ApiSchemas["RunnerInputJsonValue"] } }
  "RunnerInputJsonValue": unknown
  "RunnerInputRunnerEmptyRequest": Record<string, never>
  "RunnerInputSkillScopeRequest": { "user_id": number; "system_preset_id": "companion" | "copywriter" | "language_teacher" }
  "RunnerInputToolExecuteRequest": { "name": string; "args"?: { [key: string]: ApiSchemas["RunnerInputJsonValue"] }; "call_id"?: (string | null); "skill_scope"?: (ApiSchemas["RunnerInputSkillScopeRequest"] | null) }
  "RunnerJsonValue": unknown
  "RunnerRunnerCallResult": { "call_id": string; "status": "claimed" | "completed" | "failed" | "unknown" | "not_found"; "result": ApiSchemas["RunnerJsonValue"]; "error": (string | null); "claimed_at": (number | number | null); "finished_at": (number | number | null) }
  "RunnerRunnerCancelResult": { "ok": boolean; "error": ("no_in_flight" | null) }
  "RunnerRunnerCapabilitiesChangedPayload": { "run_generation": string; "capabilities": ApiSchemas["RunnerRunnerCapabilitiesPayload"]; "probe_failed": boolean }
  "RunnerRunnerCapabilitiesPayload": { "microphone": (boolean | null); "screen_capture": (boolean | null); "system_activity": (boolean | null); "platform": (string | null); "python": (string | null) }
  "RunnerRunnerCapabilityHealth": { "available": boolean; "reason": (string | null) }
  "RunnerRunnerConfigResult": { "ok": true }
  "RunnerRunnerInfoResult": { "version": string; "run_generation": string; "pid": number; "capabilities": ApiSchemas["RunnerRunnerCapabilitiesPayload"]; "capabilities_health": { [key: string]: ApiSchemas["RunnerRunnerCapabilityHealth"] }; "probe_failed": boolean; "reconnect_streak": number; "started_at": number; "uptime_seconds": number; "reconnect_count": number; "tool_count": number; "network_reachable": boolean; "disk_free_bytes": (number | null); "system": ApiSchemas["RunnerRunnerSystemInfo"] }
  "RunnerRunnerReadyPayload": { "version": string; "run_generation": string; "pid": number; "capabilities": ApiSchemas["RunnerRunnerCapabilitiesPayload"]; "capabilities_health": { [key: string]: ApiSchemas["RunnerRunnerCapabilityHealth"] }; "probe_failed": boolean; "reconnect_streak": number }
  "RunnerRunnerSystemInfo": { "platform": string; "python": string; "release": string; "machine": string }
  "RunnerRunnerToolResult": ApiSchemas["RunnerJsonValue"]
  "RunnerRunnerToolSchema": { "name": string; "description": string; "parameters": { [key: string]: ApiSchemas["RunnerJsonValue"] }; [key: string]: unknown }
  "RunnerRunnerToolsResult": { "tools": Array<ApiSchemas["RunnerRunnerToolSchema"]> }
  "SceneActivateRequest": { "scene_id": number }
  "SceneDescriptionRequest": { "title": string; "description": string }
  "SceneGenerateRequest": { "source": "generate"; "notes"?: (string | null); "image"?: (string | null); "content_type"?: "image/png" | "image/jpeg" | "image/webp" | "image/gif" }
  "SceneImageDimensions": { "width": number; "height": number }
  "SceneImageSize": { "width": number; "height": number }
  "SceneImportRequest": { "image": string; "content_type"?: (string | null); "source": "import"; "scene_id"?: (number | null) }
  "SceneListResponse": { "scenes": Array<ApiSchemas["SceneResponse"]>; "total": number; "offset": number; "limit": number; "version": number }
  "SceneOrigin": "onboarding" | "llm" | "nightly" | "user_request"
  "ScenePolicy": "locked" | "llm_may_replace"
  "ScenePolicyInput": "locked" | "llm_may_replace"
  "ScenePolicyRequest": { "policy": ApiSchemas["ScenePolicyInput"] }
  "ScenePolicyResponse": { "policy": ApiSchemas["ScenePolicy"] }
  "ScenePromptRequest": { "notes"?: (string | null) }
  "SceneRegenerationResponse": { "task_id": string; "status": "pending" | "ready" | "failed" | "cancelled"; "stage": string; "error": (string | null) }
  "SceneResponse": { "id": number; "status": ApiSchemas["SceneStatus"]; "stage": string; "can_retry_generation": boolean; "origin": ApiSchemas["SceneOrigin"]; "source": ApiSchemas["SceneSource"]; "target_size": (ApiSchemas["SceneImageSize"] | null); "source_size": (ApiSchemas["SceneImageDimensions"] | null); "image_size": (ApiSchemas["SceneImageDimensions"] | null); "title": string; "description": string; "requirements": string; "prompt": string; "url": string; "auto_activate": boolean; "switch_version": number; "error": (string | null); "attempt_count": number; "requested_at": (string | null); "ready_at": (string | null); "activated_at": (string | null); "regeneration": (ApiSchemas["SceneRegenerationResponse"] | null) }
  "SceneSource": "generated" | "user_upload"
  "SceneStateResponse": { "active": (ApiSchemas["SceneResponse"] | null); "policy": ApiSchemas["ScenePolicy"]; "pending": (ApiSchemas["SceneResponse"] | null); "regenerating": (ApiSchemas["SceneResponse"] | null); "version": number; "switch_version": number }
  "SceneStatus": "pending" | "ready" | "description_failed" | "failed" | "cancelled"
  "SessionClearResult": { "session_id": string; "cleared_count": number; "messages": Array<ApiSchemas["SessionMessageView"]> }
  "SessionCompressionResult": { "session_id": string; "compressed": boolean; "reason": (string | null); "usage": ApiSchemas["CompressionUsage"]; "replaced_count": (number | null); "summary": (string | null); "messages": (Array<ApiSchemas["SessionMessageView"]> | null) }
  "SessionCreateRequest": { "system_preset_id": string }
  "SessionCreateResult": { "session_id": string; "info": ApiSchemas["SessionRuntimeInfo"] }
  "SessionForkRequest": { "source_message_id": number }
  "SessionHistoryResponse": { "messages": Array<ApiSchemas["SessionMessageView"]>; "next_cursor": (number | null); "has_more": boolean }
  "SessionMessageView": (ApiSchemas["PlainSessionMessageView"] | ApiSchemas["CompanionSessionMessageView"])
  "SessionRuntimeInfo": { "model": (string | null); "provider": string; "running": boolean; "system_preset_id": string; "settings": { [key: string]: unknown }; "context_window": (number | null); "kind": string; "is_automation": boolean }
  "SessionSettingsPatch": { "temperature"?: (number | null); "context_compression_threshold"?: (number | null); "reasoning_effort"?: ("none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra" | null) }
  "SessionSnapshotResponse": { "session_id": string; "message_count": number; "messages": Array<ApiSchemas["SessionMessageView"]>; "info": ApiSchemas["SessionRuntimeInfo"]; "current_seq": (number | null); "stream_id": (string | null); "active_turn": (ApiSchemas["ActiveTurnView"] | null); "last_submission": (ApiSchemas["LastSubmissionSnapshot"] | null); "truncated": boolean; "next_cursor": (string | null); "history_revision": number }
  "SessionUndoRequest": { "source_message_id": number; "confirmed"?: boolean }
  "SessionUndoResult": { "session_id": string; "deleted_count": number; "anchor": ApiSchemas["UndoAnchor"]; "messages": Array<ApiSchemas["SessionMessageView"]> }
  "ShouldActRequest": { "kind"?: "periodic_provision"; "idle_seconds"?: (number | null); "local_hour"?: (number | null); "focused_category"?: (string | null); "fullscreen"?: boolean; "screen_locked"?: boolean; "seconds_since_last_action"?: (number | null) }
  "ShouldActResult": { "should_act": boolean; "action": (string | null); "params": ({ [key: string]: unknown } | null); "reason": string }
  "SpeechToTextResponse": { "text": string }
  "TaskConfig": { "chains": { [key: string]: ApiSchemas["ChainPolicy"] } }
  "TaskConfigInput": { "chains"?: { [key: string]: ApiSchemas["ChainPolicyInput"] } }
  "TextBubble": { "type": "text"; "text": string }
  "TokenResponse": { "access_token": string; "expires_in": number; "user": ApiSchemas["UserInfo"] }
  "ToolSnapshot": { "name": (string | null); "call_id": (string | null); "status": string }
  "TopicFeedbackRequest": { "value": ("like" | "less" | null) }
  "TopicFeedbackSnapshot": { "topic_id": number; "value": ("like" | "less" | null); "updated_at": (string | null) }
  "TtsRequest": { "text"?: string; "voice"?: string; "language"?: string }
  "TurnCancelResult": { "request_id": string; "cancelled": boolean }
  "UndoAnchor": { "text": string; "attachments": Array<ApiSchemas["UndoAttachment"]> }
  "UndoAttachment": { "type": "image"; "url": string }
  "UpdateVersionItem": { "id": number; "version": string; "release_notes": string; "exe_filename": string; "exe_sha512": string; "exe_size": number; "mac_filename": (string | null); "mac_sha512": (string | null); "mac_size": (number | null); "runner_filename": (string | null); "is_active": boolean; "created_at": string; "created_by": (string | null) }
  "UpdateVersionListResponse": { "items": Array<ApiSchemas["UpdateVersionItem"]> }
  "UpdateVersionUpdate": { "release_notes"?: (string | null); "is_active"?: (boolean | null) }
  "UserBackupImportFailure": { "section": string; "count": number; "reason": string }
  "UserBackupImportResponse": { "mode": "overwrite" | "merge"; "sections": Array<string>; "imported": { [key: string]: number }; "restored_files": number; "failed": Array<ApiSchemas["UserBackupImportFailure"]> }
  "UserCreate": { "username": string; "base_url": string; "nightly_activity_enabled"?: boolean }
  "UserInfo": { "id": number; "username": string }
  "UserListResponse": { "items": Array<ApiSchemas["UserResponse"]> }
  "UserModelConfigListItem": { "user_id": number; "ai_config": ApiSchemas["AIConfigPublic"] }
  "UserModelConfigListResponse": { "items": Array<ApiSchemas["UserModelConfigListItem"]>; "ai_provider_support": { [key: string]: Array<string> }; "ai_provider_defaults": { [key: string]: { [key: string]: ApiSchemas["ProviderDefaults"] } }; "system_ai_config": ApiSchemas["AIConfigPublic"]; "ai_tasks": Array<ApiSchemas["AiTaskDefinition"]> }
  "UserModelConfigRequest": { "ai_config": ApiSchemas["AIConfigUpdate"] }
  "UserResponse": { "id": number; "username": string; "nightly_activity_enabled": boolean; "is_active": boolean; "created_at": string; "activation_code": string }
  "UserUpdate": { "nightly_activity_enabled"?: (boolean | null); "is_active"?: (boolean | null); "base_url"?: (string | null) }
  "VideoActionImageResponse": { "media_type": "image"; "action": string; "name": string; "status": string; "stage": string; "error": (string | null); "video_queue": (ApiSchemas["VideoQueueInfo"] | null); "media_url": (string | null); "current_media_url": (string | null); "pending_review": (ApiSchemas["PendingActionReview"] | null); "has_ready_asset": boolean; "can_regenerate": boolean; "feedback": string; "peek_geometry": (ApiSchemas["PeekGeometry"] | null); "content_rect": ([number, number, number, number] | null) }
  "VideoActionVideoResponse": { "media_type": "video"; "action": string; "name": string; "status": string; "stage": string; "error": (string | null); "video_queue": (ApiSchemas["VideoQueueInfo"] | null); "media_url": (string | null); "current_media_url": (string | null); "pending_review": (ApiSchemas["PendingActionReview"] | null); "has_ready_asset": boolean; "can_regenerate": boolean; "feedback": string; "peek_geometry": (ApiSchemas["PeekGeometry"] | null); "content_rect": ([number, number, number, number] | null); "motion_prompt": string }
  "VideoClipUpload": { "media_type": "video"; "action": string; "data": string; "content_type"?: (string | null); "start_seconds"?: (number | null); "end_seconds"?: (number | null) }
  "VideoPackCreateRequest": { "source": "import"; "outfit_id": number; "clips": Array<(ApiSchemas["ImageClipUpload"] | ApiSchemas["VideoClipUpload"])>; "canvas_width"?: number; "canvas_height"?: number }
  "VideoPackEnsureRequest": { "action": "peek_left" | "peek_right"; "command": "ensure" }
  "VideoPackGenerateRequest": { "source": "generate"; "outfit_id"?: (number | null); "force"?: boolean; "source_pack_id"?: (number | null); "action"?: (string | null); "feedback"?: string }
  "VideoPackListResponse": { "packs": Array<ApiSchemas["VideoPackResponse"]> }
  "VideoPackResponse": { "id": number; "outfit_id": (number | null); "status": string; "active": boolean; "identity_review": "none" | "pass" | "review" | "accepted"; "identity_review_reason": string; "content_hash": (string | null); "manifest_url": (string | null); "can_retry": boolean; "can_regenerate": boolean; "can_regenerate_pack": boolean; "actions": Array<(ApiSchemas["VideoActionImageResponse"] | ApiSchemas["VideoActionVideoResponse"])>; "error": (string | null) }
  "VideoPackRetryRequest": { "command": "retry" }
  "VideoPackSelectionRequest": { "pack_id": number }
  "VideoQueueAttachTask": { "expected_revision": number; "note": string; "action": "attach_task"; "task_id": string }
  "VideoQueueConfirmFinished": { "expected_revision": number; "note": string; "action": "confirm_finished"; "task_id"?: null }
  "VideoQueueInfo": { "state": "waiting" | "rate_limited" | "blocked"; "next_retry_at": (string | null) }
  "VideoQueueKind": "media" | "action" | "desktop"
  "VideoQueueLaneView": { "id": string; "provider": string; "provider_names": Array<string>; "next_retry_at": (string | null); "requests": Array<ApiSchemas["VideoQueueRequestView"]> }
  "VideoQueueList": { "queues": Array<ApiSchemas["VideoQueueLaneView"]> }
  "VideoQueueRequestView": { "id": string; "revision": number; "kind": ApiSchemas["VideoQueueKind"]; "entity_id": (number | null); "user_id": (number | null); "model": string; "state": string; "cancel_requested": boolean; "provider_task_id": (string | null); "created_at": string; "updated_at": string; "error_message": (string | null) }
  "VoiceBubbleView": { "type": "voice"; "text": string; "audio": (ApiSchemas["ReplyAudio"] | null) }
  "VoiceCancelResponse": { "cancelled": boolean }
  "VoiceCloneRequest": { "kind": "clone"; "operation_id": string; "sample_id": string; "preview_text": string }
  "VoiceConfirmRequest": { "expected_version": number }
  "VoiceConfirmResponse": { "voice_id": string; "version": number }
  "VoiceDesignRequest": { "kind": "design"; "operation_id": string; "prompt": string; "preview_text": string; "provider"?: string }
  "VoiceEntry": { "id": string; "provider": string; "label": string; "gender": string; "language": string; "tags": Array<string>; "description": string }
  "VoiceMatchRequest": { "preference": string; "language"?: (string | null) }
  "VoiceMatchResponse": { "voice": (ApiSchemas["VoiceEntry"] | null); "alternatives": Array<ApiSchemas["VoiceEntry"]> }
  "VoicePreviewResponse": { "candidate_id": string; "voice_id": string; "provider": string; "trial_audio_base64": string; "trial_audio_mime": string; "expected_version": number; "kind": "design" | "clone"; "prompt": string }
  "VoicePromptRequest": { "operation_id": string; "current_prompt"?: string; "feedback"?: string; "preference"?: string }
  "VoicePromptResponse": { "prompt": string; "provider": string }
  "VoiceSampleResponse": { "sample_id": string; "url": string; "duration": number; "mime_type": string }
  "VoicesListResponse": { "providers": Array<string>; "voices": Array<ApiSchemas["VoiceEntry"]>; "supports_voice_design": boolean; "voice_design_guide": string; "supports_voice_prompt_generation": boolean; "supports_voice_clone": boolean; "custom_voices": Array<ApiSchemas["CustomVoiceSummary"]> }
  "XhsLoginQrCode": { "is_logged_in": boolean; "image": (string | null); "expires_at": (string | null); "session_id": (string | null) }
  "XhsLoginVerification": { "session_id": string; "code": string }
  "XiaohongshuAdminLoginStatus": { "is_logged_in": boolean; "username": (string | null); "user_id": (string | null); "session_id": (string | null); "state": ("awaiting_scan" | "sms_required" | "verifying" | "verification_required" | "logged_in" | "expired" | null); "expires_at": (string | null); "verification_error": ("invalid_code" | null); "configured": boolean }
  "create_version": { "file": string; "release_notes"?: string }
  "import_user_backup": { "file": string }
  "speech_to_text": { "audio_file"?: (string | null) }
  "upload_action": { "file": string; "expected_action_version": number; "expected_set_id"?: (number | null); "expected_draft_id"?: (string | null); "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "aspect_policy"?: "ask" | "keep" | "expand" }
  "upload_chat_audio": { "file": string; "session_id": string }
  "upload_chat_video": { "file"?: (string | null); "session_id"?: string }
  "upload_image": { "file": string; "expected_action_version": number; "expected_set_id"?: (number | null); "expected_draft_id"?: (string | null); "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "aspect_policy"?: "ask" | "keep" | "expand" }
  "upload_voice_reference": { "file": string }
}

export interface ApiOperations {
  "system.health": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: { [key: string]: string } }
  "admin.video_queue": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["VideoQueueList"] }
  "admin.reconcile_video_queue": { path: { "request_id": string }; query: Record<string, never>; body: (ApiSchemas["VideoQueueAttachTask"] | ApiSchemas["VideoQueueConfirmFinished"]); input: { path: { "request_id": string }; query?: never; body: (ApiSchemas["VideoQueueAttachTask"] | ApiSchemas["VideoQueueConfirmFinished"]) }; response: ApiSchemas["VideoQueueRequestView"] }
  "admin.cancel_video_queue_request": { path: { "request_id": string }; query: Record<string, never>; body: undefined; input: { path: { "request_id": string }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "admin.retry_video_queue_lane": { path: { "lane_id": string }; query: Record<string, never>; body: undefined; input: { path: { "lane_id": string }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "admin.discover_ai_models": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ModelDiscoveryRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ModelDiscoveryRequest"] }; response: ApiSchemas["ModelDiscoveryResponse"] }
  "admin.list_users": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["UserListResponse"] }
  "admin.create_user": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["UserCreate"]; input: { path?: never; query?: never; body: ApiSchemas["UserCreate"] }; response: ApiSchemas["UserResponse"] }
  "admin.update_user": { path: { "user_id": number }; query: Record<string, never>; body: ApiSchemas["UserUpdate"]; input: { path: { "user_id": number }; query?: never; body: ApiSchemas["UserUpdate"] }; response: ApiSchemas["UserResponse"] }
  "admin.delete_user": { path: { "user_id": number }; query: Record<string, never>; body: undefined; input: { path: { "user_id": number }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "admin.regenerate_activation": { path: { "user_id": number }; query: Record<string, never>; body: ApiSchemas["RegenerateActivationRequest"]; input: { path: { "user_id": number }; query?: never; body: ApiSchemas["RegenerateActivationRequest"] }; response: ApiSchemas["UserResponse"] }
  "admin.list_model_configs": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["UserModelConfigListResponse"] }
  "admin.runtime_info": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: { [key: string]: string } }
  "admin.get_system_settings": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: { [key: string]: ApiSchemas["JsonValue"] } }
  "admin.update_system_settings": { path: Record<string, never>; query: Record<string, never>; body: { [key: string]: ApiSchemas["JsonValueInput"] }; input: { path?: never; query?: never; body: { [key: string]: ApiSchemas["JsonValueInput"] } }; response: { [key: string]: ApiSchemas["JsonValue"] } }
  "admin.list_nightly_activity_logs": { path: Record<string, never>; query: { "user_id"?: (number | null); "target_date"?: (string | null) }; body: undefined; input: { path?: never; query?: { "user_id"?: (number | null); "target_date"?: (string | null) }; body?: never }; response: ApiSchemas["NightlyActivityLogListResponse"] }
  "admin.xiaohongshu_login_status": { path: Record<string, never>; query: { "session_id"?: (string | null) }; body: undefined; input: { path?: never; query?: { "session_id"?: (string | null) }; body?: never }; response: ApiSchemas["XiaohongshuAdminLoginStatus"] }
  "admin.refresh_xiaohongshu_login_qrcode": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["XhsLoginQrCode"] }
  "admin.submit_xiaohongshu_login_verification": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["XhsLoginVerification"]; input: { path?: never; query?: never; body: ApiSchemas["XhsLoginVerification"] }; response: ApiSchemas["XiaohongshuAdminLoginStatus"] }
  "admin.clear_xiaohongshu_saved_login": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "admin.upsert_model_config": { path: { "user_id": number }; query: Record<string, never>; body: ApiSchemas["UserModelConfigRequest"]; input: { path: { "user_id": number }; query?: never; body: ApiSchemas["UserModelConfigRequest"] }; response: ApiSchemas["MessageResponse"] }
  "admin.delete_model_config": { path: { "user_id": number }; query: Record<string, never>; body: undefined; input: { path: { "user_id": number }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "admin.export_user_backup": { path: { "user_id": number }; query: Record<string, never>; body: undefined; input: { path: { "user_id": number }; query?: never; body?: never }; response: Uint8Array }
  "admin.import_user_backup": { path: { "user_id": number }; query: { "mode"?: "overwrite" | "merge"; "sections"?: (Array<string> | null) }; body: FormData; input: { path: { "user_id": number }; query?: { "mode"?: "overwrite" | "merge"; "sections"?: (Array<string> | null) }; body: FormData }; response: ApiSchemas["UserBackupImportResponse"] }
  "gateway.gateway_ticket": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["GatewayTicketRequest"] | null); input: { path?: never; query?: never; body?: (ApiSchemas["GatewayTicketRequest"] | null) }; response: ApiSchemas["GatewayTicketResponse"] }
  "companion.record_interaction": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["InteractionStatsRequest"]; input: { path?: never; query?: never; body: ApiSchemas["InteractionStatsRequest"] }; response: ApiSchemas["InteractionStatsResult"] }
  "companion.idle_expression": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["IdleExpressionRequest"]; input: { path?: never; query?: never; body: ApiSchemas["IdleExpressionRequest"] }; response: ApiSchemas["IdleExpressionResult"] }
  "companion.movement_decision": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ShouldActRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ShouldActRequest"] }; response: ApiSchemas["ShouldActResult"] }
  "companion.get_pending_visual_media_reviews": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: Array<ApiSchemas["MediaReviewResponse"]> }
  "companion.get_visual_media_review": { path: { "review_id": number }; query: Record<string, never>; body: undefined; input: { path: { "review_id": number }; query?: never; body?: never }; response: ApiSchemas["MediaReviewResponse"] }
  "companion.decide_visual_media_review": { path: { "review_id": number }; query: Record<string, never>; body: ApiSchemas["ResourceDecision"]; input: { path: { "review_id": number }; query?: never; body: ApiSchemas["ResourceDecision"] }; response: ApiSchemas["MediaReviewResponse"] }
  "companion.get_character_card_route": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: (ApiSchemas["CharacterCardResponse"] | null) }
  "companion.patch_character_card_route": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["CharacterCardUpdate"]; input: { path?: never; query?: never; body: ApiSchemas["CharacterCardUpdate"] }; response: ApiSchemas["CharacterCardResponse"] }
  "companion.extract_character_card_route": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["CharacterCardExtract"]; input: { path?: never; query?: never; body: ApiSchemas["CharacterCardExtract"] }; response: ApiSchemas["CharacterCardResponse"] }
  "companion.get_persona": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["PersonaResponse"] }
  "companion.put_persona": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["PersonaUpdate"]; input: { path?: never; query?: never; body: ApiSchemas["PersonaUpdate"] }; response: ApiSchemas["PersonaResponse"] }
  "companion.post_portrait_confirm": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["PortraitConfirmRequest"]; input: { path?: never; query?: never; body: ApiSchemas["PortraitConfirmRequest"] }; response: ApiSchemas["CompanionOperationResponse"] }
  "companion.get_avatar": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["AvatarAssetResponse"] }
  "companion.post_avatar": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["AvatarGenerateRequest"] | ApiSchemas["AvatarFromImageRequest"]); input: { path?: never; query?: never; body: (ApiSchemas["AvatarGenerateRequest"] | ApiSchemas["AvatarFromImageRequest"]) }; response: ApiSchemas["AvatarAssetResponse"] }
  "companion.post_avatar_prompt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["AvatarPromptRequest"]; input: { path?: never; query?: never; body: ApiSchemas["AvatarPromptRequest"] }; response: ApiSchemas["ImagePromptResponse"] }
  "companion.post_avatar_adopt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["FullbodyAdoptRequest"]; input: { path?: never; query?: never; body: ApiSchemas["FullbodyAdoptRequest"] }; response: ApiSchemas["AvatarAssetResponse"] }
  "companion.get_avatar_history": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["AvatarHistoryResponse"] }
  "companion.put_avatar_select": { path: { "avatar_id": number }; query: Record<string, never>; body: undefined; input: { path: { "avatar_id": number }; query?: never; body?: never }; response: ApiSchemas["AvatarAssetResponse"] }
  "companion.post_fullbody_reference": { path: { "avatar_id": number }; query: Record<string, never>; body: ApiSchemas["FullbodyReferenceGenerateRequest"]; input: { path: { "avatar_id": number }; query?: never; body: ApiSchemas["FullbodyReferenceGenerateRequest"] }; response: (ApiSchemas["AvatarAssetResponse"] | ApiSchemas["FullbodyCandidateResponse"]) }
  "companion.get_fullbody_candidate": { path: { "avatar_id": number }; query: Record<string, never>; body: undefined; input: { path: { "avatar_id": number }; query?: never; body?: never }; response: (ApiSchemas["FullbodyCandidateResponse"] | null) }
  "companion.post_fullbody_candidate_analyze": { path: { "avatar_id": number; "candidate_id": number }; query: Record<string, never>; body: undefined; input: { path: { "avatar_id": number; "candidate_id": number }; query?: never; body?: never }; response: ApiSchemas["FullbodyCandidateResponse"] }
  "companion.post_fullbody_candidate_accept": { path: { "avatar_id": number; "candidate_id": number }; query: Record<string, never>; body: undefined; input: { path: { "avatar_id": number; "candidate_id": number }; query?: never; body?: never }; response: ApiSchemas["AvatarAssetResponse"] }
  "companion.post_fullbody_confirm": { path: { "avatar_id": number }; query: Record<string, never>; body: ApiSchemas["FullbodyConfirmRequest"]; input: { path: { "avatar_id": number }; query?: never; body: ApiSchemas["FullbodyConfirmRequest"] }; response: ApiSchemas["AvatarAssetResponse"] }
  "companion.post_fullbody_prompt": { path: { "avatar_id": number }; query: Record<string, never>; body: ApiSchemas["FullbodyPromptRequest"]; input: { path: { "avatar_id": number }; query?: never; body?: ApiSchemas["FullbodyPromptRequest"] }; response: ApiSchemas["ImagePromptResponse"] }
  "companion.post_fullbody_adopt": { path: { "avatar_id": number }; query: Record<string, never>; body: ApiSchemas["FullbodyAdoptRequest"]; input: { path: { "avatar_id": number }; query?: never; body: ApiSchemas["FullbodyAdoptRequest"] }; response: (ApiSchemas["AvatarAssetResponse"] | ApiSchemas["FullbodyCandidateResponse"]) }
  "companion.get_outfits": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["OutfitListResponse"] }
  "companion.post_outfit": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["OutfitCreateRequest"] | ApiSchemas["OutfitAdoptRequest"]); input: { path?: never; query?: never; body: (ApiSchemas["OutfitCreateRequest"] | ApiSchemas["OutfitAdoptRequest"]) }; response: ApiSchemas["OutfitResponse"] }
  "companion.patch_outfit_policy": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["OutfitPolicyRequest"]; input: { path?: never; query?: never; body: ApiSchemas["OutfitPolicyRequest"] }; response: ApiSchemas["OutfitPolicyResponse"] }
  "companion.post_outfit_prompt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["OutfitPromptRequest"]; input: { path?: never; query?: never; body: ApiSchemas["OutfitPromptRequest"] }; response: ApiSchemas["ImagePromptResponse"] }
  "companion.post_outfit_regenerate": { path: { "outfit_id": number }; query: Record<string, never>; body: (ApiSchemas["OutfitRegenerateRequest"] | ApiSchemas["OutfitImageImportRequest"]); input: { path: { "outfit_id": number }; query?: never; body: (ApiSchemas["OutfitRegenerateRequest"] | ApiSchemas["OutfitImageImportRequest"]) }; response: ApiSchemas["OutfitResponse"] }
  "companion.post_outfit_confirm": { path: { "outfit_id": number }; query: Record<string, never>; body: ApiSchemas["OutfitConfirmRequest"]; input: { path: { "outfit_id": number }; query?: never; body?: ApiSchemas["OutfitConfirmRequest"] }; response: ApiSchemas["OutfitResponse"] }
  "companion.post_retry_outfit_description": { path: { "outfit_id": number }; query: Record<string, never>; body: undefined; input: { path: { "outfit_id": number }; query?: never; body?: never }; response: ApiSchemas["OutfitResponse"] }
  "companion.delete_outfit_route": { path: { "outfit_id": number }; query: Record<string, never>; body: undefined; input: { path: { "outfit_id": number }; query?: never; body?: never }; response: ApiSchemas["CompanionOperationResponse"] }
  "companion.get_video_packs": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["VideoPackListResponse"] }
  "companion.post_video_pack": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["VideoPackCreateRequest"] | ApiSchemas["VideoPackGenerateRequest"]); input: { path?: never; query?: never; body: (ApiSchemas["VideoPackCreateRequest"] | ApiSchemas["VideoPackGenerateRequest"]) }; response: ApiSchemas["VideoPackResponse"] }
  "companion.produce_video_pack": { path: { "pack_id": number }; query: Record<string, never>; body: (ApiSchemas["VideoPackEnsureRequest"] | ApiSchemas["VideoPackRetryRequest"]); input: { path: { "pack_id": number }; query?: never; body: (ApiSchemas["VideoPackEnsureRequest"] | ApiSchemas["VideoPackRetryRequest"]) }; response: ApiSchemas["VideoPackResponse"] }
  "companion.put_video_pack_selection": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["VideoPackSelectionRequest"]; input: { path?: never; query?: never; body: ApiSchemas["VideoPackSelectionRequest"] }; response: ApiSchemas["VideoPackResponse"] }
  "companion.delete_video_pack_route": { path: { "pack_id": number }; query: Record<string, never>; body: undefined; input: { path: { "pack_id": number }; query?: never; body?: never }; response: ApiSchemas["CompanionOperationResponse"] }
  "companion.serve_companion_asset": { path: { "user_id": number; "asset_path": string }; query: { "expires"?: (number | null); "sig"?: (string | null) }; body: undefined; input: { path: { "user_id": number; "asset_path": string }; query?: { "expires"?: (number | null); "sig"?: (string | null) }; body?: never }; response: Uint8Array }
  "companion_actions.get_catalog": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["ActionCatalogResponse"] }
  "companion_actions.design_action": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ActionDesignRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ActionDesignRequest"] }; response: ApiSchemas["ActionDesignResult"] }
  "companion_actions.submit_playback_receipt": { path: { "play_id": string }; query: Record<string, never>; body: ApiSchemas["ActionPlaybackReceipt"]; input: { path: { "play_id": string }; query?: never; body: ApiSchemas["ActionPlaybackReceipt"] }; response: ApiSchemas["CompanionOperationResponse"] }
  "companion_actions.get_budget_status": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["ActionBudgetStatus"] }
  "companion_actions.set_action_enabled": { path: { "action_id": number }; query: Record<string, never>; body: ApiSchemas["ActionUpdateRequest"]; input: { path: { "action_id": number }; query?: never; body: ApiSchemas["ActionUpdateRequest"] }; response: { [key: string]: boolean } }
  "companion_actions.delete_action": { path: { "action_id": number }; query: Record<string, never>; body: undefined; input: { path: { "action_id": number }; query?: never; body?: never }; response: ApiSchemas["CompanionOperationResponse"] }
  "companion_desktop_videos.read_state": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["DesktopVideoStateResponse"] }
  "companion_desktop_videos.read_sets": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["DesktopVideoListResponse"] }
  "companion_desktop_videos.create_set": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopVideoCreateSetRequest"]; input: { path?: never; query?: never; body: ApiSchemas["DesktopVideoCreateSetRequest"] }; response: ApiSchemas["DesktopVideoSetResponse"] }
  "companion_desktop_videos.read_action": { path: { "action_id": number }; query: Record<string, never>; body: undefined; input: { path: { "action_id": number }; query?: never; body?: never }; response: ApiSchemas["DesktopVideoActionResponse"] }
  "companion_desktop_videos.prepare_current": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["DesktopVideoEnsureRequest"] | null); input: { path?: never; query?: never; body?: (ApiSchemas["DesktopVideoEnsureRequest"] | null) }; response: ApiSchemas["DesktopVideoStateResponse"] }
  "companion_desktop_videos.preview_video_refinement": { path: { "action_id": number }; query: Record<string, never>; body: ApiSchemas["DesktopVideoRefineRequest"]; input: { path: { "action_id": number }; query?: never; body: ApiSchemas["DesktopVideoRefineRequest"] }; response: ApiSchemas["DesktopVideoRefinePreviewResponse"] }
  "companion_desktop_videos.external_prompt": { path: { "action_id": number }; query: Record<string, never>; body: (ApiSchemas["DesktopVideoExternalPromptRequest"] | null); input: { path: { "action_id": number }; query?: never; body?: (ApiSchemas["DesktopVideoExternalPromptRequest"] | null) }; response: ApiSchemas["DesktopVideoExternalPromptResponse"] }
  "companion_desktop_videos.upload_action": { path: { "action_id": number }; query: Record<string, never>; body: FormData; input: { path: { "action_id": number }; query?: never; body: FormData }; response: ApiSchemas["DesktopVideoActionResponse"] }
  "companion_desktop_videos.design_action": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopVideoDesignRequest"]; input: { path?: never; query?: never; body: ApiSchemas["DesktopVideoDesignRequest"] }; response: ApiSchemas["DesktopVideoProposalResponse"] }
  "companion_desktop_videos.play_action": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopVideoPlayRequest"]; input: { path?: never; query?: never; body: ApiSchemas["DesktopVideoPlayRequest"] }; response: ApiSchemas["DesktopVideoPlayCommand"] }
  "companion_desktop_videos.update_preferences": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopVideoPreferences"]; input: { path?: never; query?: never; body: ApiSchemas["DesktopVideoPreferences"] }; response: ApiSchemas["DesktopVideoStateResponse"] }
  "companion_desktop_videos.claim_play": { path: { "play_id": string }; query: Record<string, never>; body: ApiSchemas["DesktopVideoClaimRequest"]; input: { path: { "play_id": string }; query?: never; body: ApiSchemas["DesktopVideoClaimRequest"] }; response: { [key: string]: boolean } }
  "companion_desktop_videos.receipt_play": { path: { "play_id": string }; query: Record<string, never>; body: ApiSchemas["DesktopVideoReceipt"]; input: { path: { "play_id": string }; query?: never; body: ApiSchemas["DesktopVideoReceipt"] }; response: ApiSchemas["CompanionOperationResponse"] }
  "companion_desktop_videos.rename_set": { path: { "set_id": number }; query: Record<string, never>; body: ApiSchemas["DesktopVideoRenameSetRequest"]; input: { path: { "set_id": number }; query?: never; body: ApiSchemas["DesktopVideoRenameSetRequest"] }; response: ApiSchemas["DesktopVideoSetResponse"] }
  "companion_desktop_videos.delete_set": { path: { "set_id": number }; query: Record<string, never>; body: undefined; input: { path: { "set_id": number }; query?: never; body?: never }; response: ApiSchemas["DesktopVideoStateResponse"] }
  "companion_desktop_videos.upload_image": { path: { "action_id": number }; query: Record<string, never>; body: FormData; input: { path: { "action_id": number }; query?: never; body: FormData }; response: ApiSchemas["DesktopVideoActionResponse"] }
  "companion_desktop_videos.command_draft": { path: { "action_id": number }; query: Record<string, never>; body: (ApiSchemas["DesktopVideoGenerateCommand"] | ApiSchemas["DesktopVideoImageCommand"] | ApiSchemas["DesktopVideoRefineCommand"] | ApiSchemas["DesktopVideoConfirmImageCommand"] | ApiSchemas["DesktopVideoCancelCommand"] | ApiSchemas["DesktopVideoPauseCommand"] | ApiSchemas["DesktopVideoResumeCommand"]); input: { path: { "action_id": number }; query?: never; body: (ApiSchemas["DesktopVideoGenerateCommand"] | ApiSchemas["DesktopVideoImageCommand"] | ApiSchemas["DesktopVideoRefineCommand"] | ApiSchemas["DesktopVideoConfirmImageCommand"] | ApiSchemas["DesktopVideoCancelCommand"] | ApiSchemas["DesktopVideoPauseCommand"] | ApiSchemas["DesktopVideoResumeCommand"]) }; response: ApiSchemas["DesktopVideoActionResponse"] }
  "companion_desktop_videos.decide_draft": { path: { "action_id": number }; query: Record<string, never>; body: ApiSchemas["DesktopVideoDecisionRequest"]; input: { path: { "action_id": number }; query?: never; body: ApiSchemas["DesktopVideoDecisionRequest"] }; response: ApiSchemas["DesktopVideoActionResponse"] }
  "companion_desktop_videos.select_current_set": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopVideoSelectionRequest"]; input: { path?: never; query?: never; body: ApiSchemas["DesktopVideoSelectionRequest"] }; response: ApiSchemas["DesktopVideoStateResponse"] }
  "companion_journal.get_diary": { path: Record<string, never>; query: { "from"?: (string | null); "to"?: (string | null); "limit"?: number }; body: undefined; input: { path?: never; query?: { "from"?: (string | null); "to"?: (string | null); "limit"?: number }; body?: never }; response: ApiSchemas["DiaryListResponse"] }
  "companion_journal.get_diary_unread": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["DiaryUnreadResponse"] }
  "companion_journal.read_diary": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DiaryReadRequest"]; input: { path?: never; query?: never; body: ApiSchemas["DiaryReadRequest"] }; response: ApiSchemas["DiaryUnreadResponse"] }
  "companion_posts.get_posts": { path: Record<string, never>; query: { "cursor"?: (string | null); "limit"?: number }; body: undefined; input: { path?: never; query?: { "cursor"?: (string | null); "limit"?: number }; body?: never }; response: ApiSchemas["PostListResponse"] }
  "companion_posts.get_posts_unread": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["PostUnreadResponse"] }
  "companion_posts.read_posts": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["PostReadRequest"]; input: { path?: never; query?: never; body: ApiSchemas["PostReadRequest"] }; response: ApiSchemas["PostUnreadResponse"] }
  "companion_posts.get_publication_recoveries": { path: Record<string, never>; query: { "state": "recoverable"; "limit"?: number; "offset"?: number }; body: undefined; input: { path?: never; query: { "state": "recoverable"; "limit"?: number; "offset"?: number }; body?: never }; response: ApiSchemas["PostPublicationRecoveryList"] }
  "companion_posts.get_publication_recovery": { path: { "publication_id": string }; query: Record<string, never>; body: undefined; input: { path: { "publication_id": string }; query?: never; body?: never }; response: ApiSchemas["PostPublicationRecovery"] }
  "companion_posts.query_publication_recovery": { path: { "publication_id": string }; query: Record<string, never>; body: undefined; input: { path: { "publication_id": string }; query?: never; body?: never }; response: ApiSchemas["PostPublicationRecovery"] }
  "companion_posts.decide_publication": { path: { "publication_id": string }; query: Record<string, never>; body: ApiSchemas["ResourceDecision"]; input: { path: { "publication_id": string }; query?: never; body: ApiSchemas["ResourceDecision"] }; response: ApiSchemas["PostPublicationResult"] }
  "companion_posts.get_post_detail": { path: { "post_id": string }; query: Record<string, never>; body: undefined; input: { path: { "post_id": string }; query?: never; body?: never }; response: ApiSchemas["PostResponse"] }
  "companion_posts.post_comment": { path: { "post_id": string }; query: Record<string, never>; body: ApiSchemas["PostCommentCreateRequest"]; input: { path: { "post_id": string }; query?: never; body: ApiSchemas["PostCommentCreateRequest"] }; response: ApiSchemas["PostCommentResponse"] }
  "companion_posts.remove_comment": { path: { "post_id": string; "comment_id": string }; query: Record<string, never>; body: undefined; input: { path: { "post_id": string; "comment_id": string }; query?: never; body?: never }; response: undefined }
  "companion_posts.retry_comment_reply": { path: { "post_id": string; "comment_id": string }; query: Record<string, never>; body: undefined; input: { path: { "post_id": string; "comment_id": string }; query?: never; body?: never }; response: ApiSchemas["PostCommentResponse"] }
  "companion_scenes.read_scene_state": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["SceneStateResponse"] }
  "companion_scenes.create_scene": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["SceneGenerateRequest"] | ApiSchemas["SceneImportRequest"]); input: { path?: never; query?: never; body: (ApiSchemas["SceneGenerateRequest"] | ApiSchemas["SceneImportRequest"]) }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.scene_list_route": { path: Record<string, never>; query: { "q"?: string; "offset"?: number; "limit"?: number }; body: undefined; input: { path?: never; query?: { "q"?: string; "offset"?: number; "limit"?: number }; body?: never }; response: ApiSchemas["SceneListResponse"] }
  "companion_scenes.post_scene_prompt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ScenePromptRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ScenePromptRequest"] }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.post_scene_discard": { path: { "scene_id": number }; query: Record<string, never>; body: undefined; input: { path: { "scene_id": number }; query?: never; body?: never }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.post_scene_regenerate": { path: { "scene_id": number }; query: Record<string, never>; body: undefined; input: { path: { "scene_id": number }; query?: never; body?: never }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.delete_scene_route": { path: { "scene_id": number }; query: Record<string, never>; body: undefined; input: { path: { "scene_id": number }; query?: never; body?: never }; response: ApiSchemas["CompanionOperationResponse"] }
  "companion_scenes.scene_detail_route": { path: { "scene_id": number }; query: Record<string, never>; body: undefined; input: { path: { "scene_id": number }; query?: never; body?: never }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.scene_edit_route": { path: { "scene_id": number }; query: Record<string, never>; body: ApiSchemas["SceneDescriptionRequest"]; input: { path: { "scene_id": number }; query?: never; body: ApiSchemas["SceneDescriptionRequest"] }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.post_scene_activate": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["SceneActivateRequest"]; input: { path?: never; query?: never; body: ApiSchemas["SceneActivateRequest"] }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.patch_scene_policy": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ScenePolicyRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ScenePolicyRequest"] }; response: ApiSchemas["ScenePolicyResponse"] }
  "companion_scenes.scene_analyze_route": { path: { "scene_id": number }; query: Record<string, never>; body: undefined; input: { path: { "scene_id": number }; query?: never; body?: never }; response: ApiSchemas["SceneResponse"] }
  "companion_topics.read_feedback": { path: Record<string, never>; query: { "topic_ids"?: Array<number> }; body: undefined; input: { path?: never; query?: { "topic_ids"?: Array<number> }; body?: never }; response: Array<ApiSchemas["TopicFeedbackSnapshot"]> }
  "companion_topics.update_feedback": { path: { "topic_id": number }; query: Record<string, never>; body: ApiSchemas["TopicFeedbackRequest"]; input: { path: { "topic_id": number }; query?: never; body: ApiSchemas["TopicFeedbackRequest"] }; response: ApiSchemas["TopicFeedbackSnapshot"] }
  "resource_catalog.get_presets": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["PromptPresetListResponse"] }
  "resource_catalog.get_commands": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["CommandListResponse"] }
  "resource_catalog.get_memories": { path: Record<string, never>; query: { "system_preset_id": string; "kind"?: (string | null); "status"?: "active" | "candidate" | "invalidated" | "expired"; "tag"?: (string | null); "q"?: (string | null); "limit"?: number; "offset"?: number }; body: undefined; input: { path?: never; query: { "system_preset_id": string; "kind"?: (string | null); "status"?: "active" | "candidate" | "invalidated" | "expired"; "tag"?: (string | null); "q"?: (string | null); "limit"?: number; "offset"?: number }; body?: never }; response: ApiSchemas["MemoryListResponse"] }
  "resource_catalog.patch_memory_profile": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["MemoryProfileRequest"]; input: { path?: never; query?: never; body: ApiSchemas["MemoryProfileRequest"] }; response: ApiSchemas["MemoryProfileResponse"] }
  "resource_catalog.patch_memory": { path: { "memory_id": number }; query: Record<string, never>; body: ApiSchemas["MemoryUpdateRequest"]; input: { path: { "memory_id": number }; query?: never; body: ApiSchemas["MemoryUpdateRequest"] }; response: ApiSchemas["MemoryListItem"] }
  "resource_catalog.delete_memory": { path: { "memory_id": number }; query: { "system_preset_id": string }; body: undefined; input: { path: { "memory_id": number }; query: { "system_preset_id": string }; body?: never }; response: ApiSchemas["MemoryDeleteResponse"] }
  "voices.list_voices": { path: Record<string, never>; query: { "language"?: (string | null) }; body: undefined; input: { path?: never; query?: { "language"?: (string | null) }; body?: never }; response: ApiSchemas["VoicesListResponse"] }
  "voices.post_voice_match": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["VoiceMatchRequest"]; input: { path?: never; query?: never; body: ApiSchemas["VoiceMatchRequest"] }; response: ApiSchemas["VoiceMatchResponse"] }
  "voices.post_voice_candidate": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["VoiceDesignRequest"] | ApiSchemas["VoiceCloneRequest"]); input: { path?: never; query?: never; body: (ApiSchemas["VoiceDesignRequest"] | ApiSchemas["VoiceCloneRequest"]) }; response: ApiSchemas["VoicePreviewResponse"] }
  "voices.post_voice_prompt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["VoicePromptRequest"]; input: { path?: never; query?: never; body: ApiSchemas["VoicePromptRequest"] }; response: ApiSchemas["VoicePromptResponse"] }
  "voices.post_voice_confirmation": { path: { "candidate_id": string }; query: Record<string, never>; body: ApiSchemas["VoiceConfirmRequest"]; input: { path: { "candidate_id": string }; query?: never; body: ApiSchemas["VoiceConfirmRequest"] }; response: ApiSchemas["VoiceConfirmResponse"] }
  "voices.delete_voice_operation": { path: { "operation_id": string }; query: Record<string, never>; body: undefined; input: { path: { "operation_id": string }; query?: never; body?: never }; response: ApiSchemas["VoiceCancelResponse"] }
  "onboarding.get_onboarding": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["OnboardingStateResponse"] }
  "onboarding.patch_onboarding": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["OnboardingPatchRequest"]; input: { path?: never; query?: never; body: ApiSchemas["OnboardingPatchRequest"] }; response: ApiSchemas["OnboardingStateResponse"] }
  "config.get_config": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["DesktopConfigResponse"] }
  "config.put_config": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopConfigPutRequest"]; input: { path?: never; query?: never; body: ApiSchemas["DesktopConfigPutRequest"] }; response: ApiSchemas["DesktopConfigResponse"] }
  "media.upload_voice_reference": { path: Record<string, never>; query: Record<string, never>; body: FormData; input: { path?: never; query?: never; body: FormData }; response: ApiSchemas["VoiceSampleResponse"] }
  "media.serve_file": { path: { "file_id": string }; query: Record<string, never>; body: undefined; input: { path: { "file_id": string }; query?: never; body?: never }; response: Uint8Array }
  "media.serve_session_video": { path: { "session_id": string; "file_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string; "file_id": string }; query?: never; body?: never }; response: Uint8Array }
  "media.serve_session_audio": { path: { "session_id": string; "file_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string; "file_id": string }; query?: never; body?: never }; response: Uint8Array }
  "media.upload_chat_audio": { path: Record<string, never>; query: Record<string, never>; body: FormData; input: { path?: never; query?: never; body: FormData }; response: ApiSchemas["ChatAudioUploadResponse"] }
  "media.upload_chat_video": { path: Record<string, never>; query: Record<string, never>; body: FormData; input: { path?: never; query?: never; body?: FormData }; response: ApiSchemas["ChatVideoUploadResponse"] }
  "media.speech_to_text": { path: Record<string, never>; query: { "language"?: string }; body: FormData; input: { path?: never; query?: { "language"?: string }; body?: FormData }; response: ApiSchemas["SpeechToTextResponse"] }
  "media.text_to_speech": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["TtsRequest"]; input: { path?: never; query?: never; body: ApiSchemas["TtsRequest"] }; response: Uint8Array }
  "page.admin_login": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["AdminLoginRequest"]; input: { path?: never; query?: never; body: ApiSchemas["AdminLoginRequest"] }; response: ApiSchemas["AdminTokenResponse"] }
  "remote.create_pairing": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["PairingResponse"] }
  "remote.pairing_status": { path: { "pairing_id": number }; query: Record<string, never>; body: undefined; input: { path: { "pairing_id": number }; query?: never; body?: never }; response: ApiSchemas["PairingResponse"] }
  "remote.cancel_pairing": { path: { "pairing_id": number }; query: Record<string, never>; body: undefined; input: { path: { "pairing_id": number }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "remote.exchange_pairing": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ExchangeRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ExchangeRequest"] }; response: ApiSchemas["RemoteSessionResponse"] }
  "remote.current_remote_session": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["RemoteSessionResponse"] }
  "remote.logout_remote_session": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "remote.list_remote_devices": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["DeviceListResponse"] }
  "remote.revoke_all_remote_devices": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "remote.revoke_remote_device": { path: { "device_id": number }; query: Record<string, never>; body: undefined; input: { path: { "device_id": number }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "sessions.submit_turn": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["PromptSubmitRequest"]; input: { path: { "session_id": string }; query?: never; body: ApiSchemas["PromptSubmitRequest"] }; response: ApiSchemas["PromptSubmitResult"] }
  "sessions.get_turn": { path: { "session_id": string; "request_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string; "request_id": string }; query?: never; body?: never }; response: ApiSchemas["PromptSubmitResult"] }
  "sessions.cancel_turn": { path: { "session_id": string; "request_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string; "request_id": string }; query?: never; body?: never }; response: ApiSchemas["TurnCancelResult"] }
  "sessions.snapshot": { path: { "session_id": string }; query: { "stream_id"?: (string | null) }; body: undefined; input: { path: { "session_id": string }; query?: { "stream_id"?: (string | null) }; body?: never }; response: ApiSchemas["SessionSnapshotResponse"] }
  "sessions.undo": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["SessionUndoRequest"]; input: { path: { "session_id": string }; query?: never; body: ApiSchemas["SessionUndoRequest"] }; response: ApiSchemas["SessionUndoResult"] }
  "sessions.compress": { path: { "session_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string }; query?: never; body?: never }; response: ApiSchemas["SessionCompressionResult"] }
  "sessions.command": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["CommandDispatchRequest"]; input: { path: { "session_id": string }; query?: never; body: ApiSchemas["CommandDispatchRequest"] }; response: ApiSchemas["CommandDispatchResult"] }
  "sessions.retry_voice_bubble": { path: { "message_id": number; "bubble_index": number }; query: Record<string, never>; body: undefined; input: { path: { "message_id": number; "bubble_index": number }; query?: never; body?: never }; response: ApiSchemas["VoiceBubbleView"] }
  "sessions.list_sessions": { path: Record<string, never>; query: { "limit"?: number; "offset"?: number; "min_messages"?: number; "archived"?: "only" | "exclude" | "include"; "order"?: "recent" | "created" | "messages"; "include_subagents"?: boolean; "q"?: (string | null) }; body: undefined; input: { path?: never; query?: { "limit"?: number; "offset"?: number; "min_messages"?: number; "archived"?: "only" | "exclude" | "include"; "order"?: "recent" | "created" | "messages"; "include_subagents"?: boolean; "q"?: (string | null) }; body?: never }; response: ApiSchemas["DesktopSessionListResponse"] }
  "sessions.create_session": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["SessionCreateRequest"]; input: { path?: never; query?: never; body: ApiSchemas["SessionCreateRequest"] }; response: ApiSchemas["SessionCreateResult"] }
  "sessions.get_main_session": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["SessionCreateResult"] }
  "sessions.get_session": { path: { "session_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string }; query?: never; body?: never }; response: ApiSchemas["SessionCreateResult"] }
  "sessions.patch_session": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["DesktopSessionPatchRequest"]; input: { path: { "session_id": string }; query?: never; body: ApiSchemas["DesktopSessionPatchRequest"] }; response: ApiSchemas["SessionCreateResult"] }
  "sessions.delete_session": { path: { "session_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string }; query?: never; body?: never }; response: ApiSchemas["DesktopSessionOperationResponse"] }
  "sessions.fork_session": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["SessionForkRequest"]; input: { path: { "session_id": string }; query?: never; body: ApiSchemas["SessionForkRequest"] }; response: ApiSchemas["SessionCreateResult"] }
  "sessions.get_session_history": { path: { "session_id": string }; query: { "before_id"?: (number | null); "limit"?: number }; body: undefined; input: { path: { "session_id": string }; query?: { "before_id"?: (number | null); "limit"?: number }; body?: never }; response: ApiSchemas["SessionHistoryResponse"] }
  "update.get_latest_yml": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["ReleaseManifestResponse"] }
  "update.get_latest_mac_yml": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["ReleaseManifestResponse"] }
  "update.get_latest_runner_yml": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: Uint8Array }
  "update.get_latest_file": { path: { "filename": string }; query: Record<string, never>; body: undefined; input: { path: { "filename": string }; query?: never; body?: never }; response: Uint8Array }
  "update.list_versions": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["UpdateVersionListResponse"] }
  "update.create_version": { path: Record<string, never>; query: Record<string, never>; body: FormData; input: { path?: never; query?: never; body: FormData }; response: ApiSchemas["UpdateVersionItem"] }
  "update.update_version": { path: { "id": number }; query: Record<string, never>; body: ApiSchemas["UpdateVersionUpdate"]; input: { path: { "id": number }; query?: never; body: ApiSchemas["UpdateVersionUpdate"] }; response: ApiSchemas["UpdateVersionItem"] }
  "update.delete_version": { path: { "id": number }; query: Record<string, never>; body: undefined; input: { path: { "id": number }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "user.activate": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ActivateRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ActivateRequest"] }; response: ApiSchemas["TokenResponse"] }
  "user.refresh_session": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["RefreshRequest"]; input: { path?: never; query?: never; body: ApiSchemas["RefreshRequest"] }; response: ApiSchemas["TokenResponse"] }
  "user.logout": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
}

export interface RpcOperations {
  "companion.signal": { request: ApiSchemas["RpcInputCompanionSignal"]; response: ApiSchemas["RpcCompanionSignalResult"] }
  "gateway.ack": { request: ApiSchemas["RpcInputGatewayAckRequest"]; response: ApiSchemas["RpcGatewayAckResult"] }
  "gateway.ping": { request: ApiSchemas["RpcInputEmptyRequest"]; response: ApiSchemas["RpcEmptyResult"] }
  "gateway.resume": { request: ApiSchemas["RpcInputGatewayResumeRequest"]; response: ApiSchemas["RpcGatewayResumeResult"] }
  "tool.result": { request: ApiSchemas["RpcInputToolResultRequest"]; response: ApiSchemas["RpcToolResultResult"] }
  "tools.sync": { request: ApiSchemas["RpcInputToolsSyncRequest"]; response: ApiSchemas["RpcToolsSyncResult"] }
}

export interface GatewayEventPayloads {
  "command.result": ApiSchemas["RpcCommandDispatchResult"]
  "companion.action.catalog_changed": ApiSchemas["RpcActionCatalogEvent"]
  "companion.action.job_updated": ApiSchemas["RpcActionJobEvent"]
  "companion.action.play_requested": ApiSchemas["RpcActionPlayCommand"]
  "companion.character_card.updated": ApiSchemas["RpcCharacterCardEvent"]
  "companion.desktop_video.play_requested": ApiSchemas["RpcDesktopVideoPlayCommand"]
  "companion.desktop_video.updated": ApiSchemas["RpcDesktopVideoUpdatedEvent"]
  "companion.diary.created": ApiSchemas["RpcDiaryEntryResponse"]
  "companion.diary.deleted": ApiSchemas["RpcDiaryDeletedEvent"]
  "companion.diary.read": ApiSchemas["RpcEmptyResult"]
  "companion.message": ApiSchemas["RpcCompanionMessageEvent"]
  "companion.mood": ApiSchemas["RpcMoodEvent"]
  "companion.outfit.updated": ApiSchemas["RpcOutfitChangedEvent"]
  "companion.post.comment": ApiSchemas["RpcPostCommentEvent"]
  "companion.post.comment.deleted": ApiSchemas["RpcPostCommentDeletedEvent"]
  "companion.post.created": ApiSchemas["RpcPostResponse"]
  "companion.posts.read": ApiSchemas["RpcEmptyResult"]
  "companion.scene.activated": ApiSchemas["RpcSceneChangedEvent"]
  "companion.scene.updated": ApiSchemas["RpcSceneChangedEvent"]
  "companion.topic_feedback.updated": ApiSchemas["RpcTopicFeedbackSnapshot"]
  "companion.video.activated": ApiSchemas["RpcPackEvent"]
  "companion.video.failed": ApiSchemas["RpcPackFailedEvent"]
  "companion.video.progress": ApiSchemas["RpcPackProgressEvent"]
  "companion.video.ready": ApiSchemas["RpcPackEvent"]
  "companion.voice.updated": ApiSchemas["RpcVoiceUpdatedEvent"]
  "compress.completed": ApiSchemas["RpcCompressionEvent"]
  "error": ApiSchemas["RpcGatewayErrorEvent"]
  "memory.changed": ApiSchemas["RpcMemoryChangedEvent"]
  "message.audio": ApiSchemas["RpcMessageAudioEvent"]
  "message.break": ApiSchemas["RpcTurnEvent"]
  "message.bubble": ApiSchemas["RpcMessageBubbleEvent"]
  "message.complete": ApiSchemas["RpcMessageCompleteEvent"]
  "message.deleted": ApiSchemas["RpcMessageDeletedEvent"]
  "message.delta": ApiSchemas["RpcMessageDeltaEvent"]
  "message.edited": ApiSchemas["RpcMessageEditedEvent"]
  "message.media": ApiSchemas["RpcMessageMediaEvent"]
  "message.persisted": ApiSchemas["RpcMessagePersistedEvent"]
  "message.reasoning.delta": ApiSchemas["RpcMessageDeltaEvent"]
  "message.replaced": ApiSchemas["RpcMessageReplacedEvent"]
  "message.start": ApiSchemas["RpcTurnEvent"]
  "message.voice": ApiSchemas["RpcMessageVoiceEvent"]
  "session.list_changed": ApiSchemas["RpcSessionListChangedEvent"]
  "session.state": ApiSchemas["RpcSessionStateEvent"]
  "system.notification": ApiSchemas["RpcSystemNotificationEvent"]
  "tool.call": ApiSchemas["RpcToolCallEvent"]
  "tool.cancel": ApiSchemas["RpcToolCancelEvent"]
  "tool.complete": ApiSchemas["RpcToolProgressEvent"]
  "tool.start": ApiSchemas["RpcToolProgressEvent"]
  "video_gen.completed": ApiSchemas["RpcVideoCompletedEvent"]
  "video_gen.failed": ApiSchemas["RpcVideoFailedEvent"]
  "video_gen.queue": ApiSchemas["RpcVideoQueueEvent"]
}

export interface RunnerOperations {
  "calls.cancel": { request: ApiSchemas["RunnerInputCallCancelRequest"]; response: ApiSchemas["RunnerRunnerCancelResult"] }
  "calls.get": { request: ApiSchemas["RunnerInputCallGetRequest"]; response: ApiSchemas["RunnerRunnerCallResult"] }
  "config.replace": { request: ApiSchemas["RunnerInputConfigReplaceRequest"]; response: ApiSchemas["RunnerRunnerConfigResult"] }
  "runner.info": { request: ApiSchemas["RunnerInputRunnerEmptyRequest"]; response: ApiSchemas["RunnerRunnerInfoResult"] }
  "tools.execute": { request: ApiSchemas["RunnerInputToolExecuteRequest"]; response: ApiSchemas["RunnerRunnerToolResult"] }
  "tools.list": { request: ApiSchemas["RunnerInputRunnerEmptyRequest"]; response: ApiSchemas["RunnerRunnerToolsResult"] }
}

export interface RunnerEventPayloads {
  "runner.capabilities_changed": ApiSchemas["RunnerRunnerCapabilitiesChangedPayload"]
  "runner.ready": ApiSchemas["RunnerRunnerReadyPayload"]
}

export const API_OPERATIONS = {
  "system.health": {
    "path": "/health",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "public"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.video_queue": {
    "path": "/api/admin/video-queue",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.reconcile_video_queue": {
    "path": "/api/admin/video-queue/{request_id}/resolve",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.cancel_video_queue_request": {
    "path": "/api/admin/video-queue/{request_id}/cancel",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.retry_video_queue_lane": {
    "path": "/api/admin/video-queue/lanes/{lane_id}/retry",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.discover_ai_models": {
    "path": "/api/admin/ai/models/discover",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.list_users": {
    "path": "/api/admin/users",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.create_user": {
    "path": "/api/admin/users",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.update_user": {
    "path": "/api/admin/users/{user_id}",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.delete_user": {
    "path": "/api/admin/users/{user_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.regenerate_activation": {
    "path": "/api/admin/users/{user_id}/activation",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.list_model_configs": {
    "path": "/api/admin/model-configs",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.runtime_info": {
    "path": "/api/admin/runtime-info",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.get_system_settings": {
    "path": "/api/admin/system-settings",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.update_system_settings": {
    "path": "/api/admin/system-settings",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.list_nightly_activity_logs": {
    "path": "/api/admin/nightly-activity-logs",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.xiaohongshu_login_status": {
    "path": "/api/admin/social/xiaohongshu/login/status",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.refresh_xiaohongshu_login_qrcode": {
    "path": "/api/admin/social/xiaohongshu/login/qrcode",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.submit_xiaohongshu_login_verification": {
    "path": "/api/admin/social/xiaohongshu/login/verification",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.clear_xiaohongshu_saved_login": {
    "path": "/api/admin/social/xiaohongshu/login",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.upsert_model_config": {
    "path": "/api/admin/users/{user_id}/model-config",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.delete_model_config": {
    "path": "/api/admin/users/{user_id}/model-config",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.export_user_backup": {
    "path": "/api/admin/users/{user_id}/export",
    "method": "GET",
    "encoding": "json",
    "response": "binary",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "admin.import_user_backup": {
    "path": "/api/admin/users/{user_id}/import",
    "method": "POST",
    "encoding": "multipart",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "gateway.gateway_ticket": {
    "path": "/api/gateway/ticket",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "companion.record_interaction": {
    "path": "/api/companion/interactions",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.idle_expression": {
    "path": "/api/companion/idle-expressions",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.movement_decision": {
    "path": "/api/companion/movement-decisions",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.get_pending_visual_media_reviews": {
    "path": "/api/companion/media-reviews",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.get_visual_media_review": {
    "path": "/api/companion/media-reviews/{review_id}",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.decide_visual_media_review": {
    "path": "/api/companion/media-reviews/{review_id}/decision",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.get_character_card_route": {
    "path": "/api/companion/character-card",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.patch_character_card_route": {
    "path": "/api/companion/character-card",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.extract_character_card_route": {
    "path": "/api/companion/character-card/extract",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.get_persona": {
    "path": "/api/companion/persona",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.put_persona": {
    "path": "/api/companion/persona",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.post_portrait_confirm": {
    "path": "/api/companion/portrait/confirm",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.get_avatar": {
    "path": "/api/companion/avatar",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.post_avatar": {
    "path": "/api/companion/avatar",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.post_avatar_prompt": {
    "path": "/api/companion/avatar/prompt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.post_avatar_adopt": {
    "path": "/api/companion/avatar/adopt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.get_avatar_history": {
    "path": "/api/companion/avatar/history",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.put_avatar_select": {
    "path": "/api/companion/avatar/{avatar_id}/select",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.post_fullbody_reference": {
    "path": "/api/companion/avatar/{avatar_id}/fullbody/reference",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.get_fullbody_candidate": {
    "path": "/api/companion/avatar/{avatar_id}/fullbody/candidate",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.post_fullbody_candidate_analyze": {
    "path": "/api/companion/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/analyze",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.post_fullbody_candidate_accept": {
    "path": "/api/companion/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/accept",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.post_fullbody_confirm": {
    "path": "/api/companion/avatar/{avatar_id}/fullbody/confirm",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.post_fullbody_prompt": {
    "path": "/api/companion/avatar/{avatar_id}/fullbody/reference/prompt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.post_fullbody_adopt": {
    "path": "/api/companion/avatar/{avatar_id}/fullbody/reference/adopt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.get_outfits": {
    "path": "/api/companion/outfits",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.post_outfit": {
    "path": "/api/companion/outfits",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.patch_outfit_policy": {
    "path": "/api/companion/outfits/policy",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.post_outfit_prompt": {
    "path": "/api/companion/outfits/prompt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.post_outfit_regenerate": {
    "path": "/api/companion/outfits/{outfit_id}/image",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.post_outfit_confirm": {
    "path": "/api/companion/outfits/{outfit_id}/confirm",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.post_retry_outfit_description": {
    "path": "/api/companion/outfits/{outfit_id}/description/retry",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion.delete_outfit_route": {
    "path": "/api/companion/outfits/{outfit_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.get_video_packs": {
    "path": "/api/companion/video-packs",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.post_video_pack": {
    "path": "/api/companion/video-packs",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.produce_video_pack": {
    "path": "/api/companion/video-packs/{pack_id}/production",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.put_video_pack_selection": {
    "path": "/api/companion/video-packs/current",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.delete_video_pack_route": {
    "path": "/api/companion/video-packs/{pack_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion.serve_companion_asset": {
    "path": "/api/companion/asset/{user_id}/{asset_path}",
    "method": "GET",
    "encoding": "json",
    "response": "binary",
    "identities": [
      "desktop",
      "remote",
      "public"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_actions.get_catalog": {
    "path": "/api/companion/actions/catalog",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_actions.design_action": {
    "path": "/api/companion/actions/design",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion_actions.submit_playback_receipt": {
    "path": "/api/companion/actions/playback/{play_id}/receipt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_actions.get_budget_status": {
    "path": "/api/companion/actions/budget",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_actions.set_action_enabled": {
    "path": "/api/companion/actions/{action_id}",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_actions.delete_action": {
    "path": "/api/companion/actions/{action_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.read_state": {
    "path": "/api/companion/desktop-videos/state",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.read_sets": {
    "path": "/api/companion/desktop-videos/sets",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.create_set": {
    "path": "/api/companion/desktop-videos/sets",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.read_action": {
    "path": "/api/companion/desktop-videos/actions/{action_id}",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.prepare_current": {
    "path": "/api/companion/desktop-videos/ensure-current",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.preview_video_refinement": {
    "path": "/api/companion/desktop-videos/actions/{action_id}/refine-video/preview",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.external_prompt": {
    "path": "/api/companion/desktop-videos/actions/{action_id}/external-prompt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion_desktop_videos.upload_action": {
    "path": "/api/companion/desktop-videos/actions/{action_id}/upload",
    "method": "POST",
    "encoding": "multipart",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "companion_desktop_videos.design_action": {
    "path": "/api/companion/desktop-videos/design",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion_desktop_videos.play_action": {
    "path": "/api/companion/desktop-videos/play",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.update_preferences": {
    "path": "/api/companion/desktop-videos/preferences",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.claim_play": {
    "path": "/api/companion/desktop-videos/plays/{play_id}/claim",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.receipt_play": {
    "path": "/api/companion/desktop-videos/plays/{play_id}/receipt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.rename_set": {
    "path": "/api/companion/desktop-videos/sets/{set_id}",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.delete_set": {
    "path": "/api/companion/desktop-videos/sets/{set_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.upload_image": {
    "path": "/api/companion/desktop-videos/actions/{action_id}/image-upload",
    "method": "POST",
    "encoding": "multipart",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "companion_desktop_videos.command_draft": {
    "path": "/api/companion/desktop-videos/actions/{action_id}/draft/commands",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.decide_draft": {
    "path": "/api/companion/desktop-videos/actions/{action_id}/draft/decision",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_desktop_videos.select_current_set": {
    "path": "/api/companion/desktop-videos/current",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_journal.get_diary": {
    "path": "/api/companion/diary",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_journal.get_diary_unread": {
    "path": "/api/companion/diary/read-state",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_journal.read_diary": {
    "path": "/api/companion/diary/read-state",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_posts.get_posts": {
    "path": "/api/companion/posts",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_posts.get_posts_unread": {
    "path": "/api/companion/posts/read-state",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_posts.read_posts": {
    "path": "/api/companion/posts/read-state",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_posts.get_publication_recoveries": {
    "path": "/api/companion/posts/publications",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_posts.get_publication_recovery": {
    "path": "/api/companion/posts/publications/{publication_id}",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_posts.query_publication_recovery": {
    "path": "/api/companion/posts/publications/{publication_id}/query",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "companion_posts.decide_publication": {
    "path": "/api/companion/posts/publications/{publication_id}/decision",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_posts.get_post_detail": {
    "path": "/api/companion/posts/{post_id}",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_posts.post_comment": {
    "path": "/api/companion/posts/{post_id}/comments",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_posts.remove_comment": {
    "path": "/api/companion/posts/{post_id}/comments/{comment_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "empty",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_posts.retry_comment_reply": {
    "path": "/api/companion/posts/{post_id}/comments/{comment_id}/reply/retry",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.read_scene_state": {
    "path": "/api/companion/scenes/state",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.create_scene": {
    "path": "/api/companion/scenes",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.scene_list_route": {
    "path": "/api/companion/scenes",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.post_scene_prompt": {
    "path": "/api/companion/scenes/prompt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.post_scene_discard": {
    "path": "/api/companion/scenes/{scene_id}/discard",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.post_scene_regenerate": {
    "path": "/api/companion/scenes/{scene_id}/regenerate",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.delete_scene_route": {
    "path": "/api/companion/scenes/{scene_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.scene_detail_route": {
    "path": "/api/companion/scenes/{scene_id}",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.scene_edit_route": {
    "path": "/api/companion/scenes/{scene_id}",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.post_scene_activate": {
    "path": "/api/companion/scenes/current",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.patch_scene_policy": {
    "path": "/api/companion/scenes/policy",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_scenes.scene_analyze_route": {
    "path": "/api/companion/scenes/{scene_id}/analyze",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_topics.read_feedback": {
    "path": "/api/companion/topics/feedback",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_topics.update_feedback": {
    "path": "/api/companion/topics/{topic_id}/feedback",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "resource_catalog.get_presets": {
    "path": "/api/presets",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "resource_catalog.get_commands": {
    "path": "/api/commands",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "resource_catalog.get_memories": {
    "path": "/api/memories",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "resource_catalog.patch_memory_profile": {
    "path": "/api/memories/profile",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "resource_catalog.patch_memory": {
    "path": "/api/memories/{memory_id}",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "resource_catalog.delete_memory": {
    "path": "/api/memories/{memory_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "voices.list_voices": {
    "path": "/api/voices",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "voices.post_voice_match": {
    "path": "/api/voices/match",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "voices.post_voice_candidate": {
    "path": "/api/voices/candidates",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "voices.post_voice_prompt": {
    "path": "/api/voices/prompt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "voices.post_voice_confirmation": {
    "path": "/api/voices/candidates/{candidate_id}/confirmation",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "voices.delete_voice_operation": {
    "path": "/api/voices/operations/{operation_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "onboarding.get_onboarding": {
    "path": "/api/companion/onboarding",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "onboarding.patch_onboarding": {
    "path": "/api/companion/onboarding",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "config.get_config": {
    "path": "/api/config",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "config.put_config": {
    "path": "/api/config",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "media.upload_voice_reference": {
    "path": "/api/media/voice-samples",
    "method": "POST",
    "encoding": "multipart",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "media.serve_file": {
    "path": "/api/media/files/{file_id}",
    "method": "GET",
    "encoding": "json",
    "response": "binary",
    "identities": [
      "public"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "media.serve_session_video": {
    "path": "/api/media/videos/{session_id}/{file_id}",
    "method": "GET",
    "encoding": "json",
    "response": "binary",
    "identities": [
      "public"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "media.serve_session_audio": {
    "path": "/api/media/audios/{session_id}/{file_id}",
    "method": "GET",
    "encoding": "json",
    "response": "binary",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "media.upload_chat_audio": {
    "path": "/api/media/audios",
    "method": "POST",
    "encoding": "multipart",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "media.upload_chat_video": {
    "path": "/api/media/videos",
    "method": "POST",
    "encoding": "multipart",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "media.speech_to_text": {
    "path": "/api/media/stt",
    "method": "POST",
    "encoding": "multipart",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": false
  },
  "media.text_to_speech": {
    "path": "/api/media/tts",
    "method": "POST",
    "encoding": "json",
    "response": "binary",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "page.admin_login": {
    "path": "/admin/login",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "public"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "remote.create_pairing": {
    "path": "/api/remote/pairings",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "remote.pairing_status": {
    "path": "/api/remote/pairings/{pairing_id}",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "remote.cancel_pairing": {
    "path": "/api/remote/pairings/{pairing_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "remote.exchange_pairing": {
    "path": "/api/remote/session/exchange",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "remote"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "remote.current_remote_session": {
    "path": "/api/remote/session",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "remote"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "remote.logout_remote_session": {
    "path": "/api/remote/session",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "remote"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "remote.list_remote_devices": {
    "path": "/api/remote/devices",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "remote.revoke_all_remote_devices": {
    "path": "/api/remote/devices",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "remote.revoke_remote_device": {
    "path": "/api/remote/devices/{device_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.submit_turn": {
    "path": "/api/sessions/{session_id}/turns",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.get_turn": {
    "path": "/api/sessions/{session_id}/turns/{request_id}",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.cancel_turn": {
    "path": "/api/sessions/{session_id}/turns/{request_id}/cancel",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.snapshot": {
    "path": "/api/sessions/{session_id}/snapshot",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.undo": {
    "path": "/api/sessions/{session_id}/history/undo",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.compress": {
    "path": "/api/sessions/{session_id}/history/compression",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "sessions.command": {
    "path": "/api/sessions/{session_id}/commands",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "sessions.retry_voice_bubble": {
    "path": "/api/sessions/messages/{message_id}/voice/{bubble_index}",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": true,
    "ipc": true
  },
  "sessions.list_sessions": {
    "path": "/api/sessions",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.create_session": {
    "path": "/api/sessions",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.get_main_session": {
    "path": "/api/sessions/main",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.get_session": {
    "path": "/api/sessions/{session_id}",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.patch_session": {
    "path": "/api/sessions/{session_id}",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.delete_session": {
    "path": "/api/sessions/{session_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.fork_session": {
    "path": "/api/sessions/{session_id}/forks",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "sessions.get_session_history": {
    "path": "/api/sessions/{session_id}/history",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "update.get_latest_yml": {
    "path": "/api/update/latest.yml",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "public"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.get_latest_mac_yml": {
    "path": "/api/update/latest-mac.yml",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "public"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.get_latest_runner_yml": {
    "path": "/api/update/latest-runner.yml",
    "method": "GET",
    "encoding": "json",
    "response": "binary",
    "identities": [
      "public"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.get_latest_file": {
    "path": "/api/update/{filename}",
    "method": "GET",
    "encoding": "json",
    "response": "binary",
    "identities": [
      "public"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.list_versions": {
    "path": "/api/admin/updates/versions",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.create_version": {
    "path": "/api/admin/updates/versions",
    "method": "POST",
    "encoding": "multipart",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.update_version": {
    "path": "/api/admin/updates/versions/{id}",
    "method": "PATCH",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.delete_version": {
    "path": "/api/admin/updates/versions/{id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "user.activate": {
    "path": "/api/user/activate",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "user.refresh_session": {
    "path": "/api/user/refresh",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "user.logout": {
    "path": "/api/user/logout",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": false
  }
} as const
export const RPC_POLICIES = {
  "companion.signal": {
    "remote": false,
    "concurrent": false,
    "wait_for_model": false
  },
  "gateway.ack": {
    "remote": true,
    "concurrent": true,
    "wait_for_model": false
  },
  "gateway.ping": {
    "remote": true,
    "concurrent": true,
    "wait_for_model": false
  },
  "gateway.resume": {
    "remote": true,
    "concurrent": false,
    "wait_for_model": false
  },
  "tool.result": {
    "remote": false,
    "concurrent": true,
    "wait_for_model": false
  },
  "tools.sync": {
    "remote": false,
    "concurrent": true,
    "wait_for_model": false
  }
} as const
export const EVENT_POLICIES = {
  "command.result": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "companion.action.catalog_changed": {
    "audience": "desktop",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.action.job_updated": {
    "audience": "desktop",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.action.play_requested": {
    "audience": "desktop",
    "snapshot": false,
    "recovery": "transient",
    "baseline": false
  },
  "companion.character_card.updated": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.desktop_video.play_requested": {
    "audience": "desktop",
    "snapshot": false,
    "recovery": "transient",
    "baseline": false
  },
  "companion.desktop_video.updated": {
    "audience": "desktop",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.diary.created": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.diary.deleted": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.diary.read": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.message": {
    "audience": "all",
    "snapshot": false,
    "recovery": "transient",
    "baseline": false
  },
  "companion.mood": {
    "audience": "all",
    "snapshot": false,
    "recovery": "transient",
    "baseline": false
  },
  "companion.outfit.updated": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.post.comment": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.post.comment.deleted": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.post.created": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.posts.read": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.scene.activated": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.scene.updated": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.topic_feedback.updated": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.video.activated": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.video.failed": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.video.progress": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.video.ready": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.voice.updated": {
    "audience": "desktop",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "compress.completed": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "error": {
    "audience": "all",
    "snapshot": false,
    "recovery": "transient",
    "baseline": false
  },
  "memory.changed": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "message.audio": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "message.break": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": true
  },
  "message.bubble": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": true
  },
  "message.complete": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "message.deleted": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "message.delta": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": true
  },
  "message.edited": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "message.media": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "message.persisted": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "message.reasoning.delta": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": true
  },
  "message.replaced": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "message.start": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": true
  },
  "message.voice": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "session.list_changed": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "session.state": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": false
  },
  "system.notification": {
    "audience": "all",
    "snapshot": false,
    "recovery": "transient",
    "baseline": false
  },
  "tool.call": {
    "audience": "desktop",
    "snapshot": false,
    "recovery": "device",
    "baseline": false
  },
  "tool.cancel": {
    "audience": "desktop",
    "snapshot": false,
    "recovery": "device",
    "baseline": false
  },
  "tool.complete": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": true
  },
  "tool.start": {
    "audience": "all",
    "snapshot": true,
    "recovery": "session",
    "baseline": true
  },
  "video_gen.completed": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "video_gen.failed": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "video_gen.queue": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  }
} as const
