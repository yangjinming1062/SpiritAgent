// 由 scripts/generate_protocol.py 从服务端公开契约生成。

export interface ApiSchemas {
  "AIConfigPublic": { "providers": Array<ApiSchemas["ProviderCardPublic"]>; "capabilities": ApiSchemas["CapabilityChains"]; "tasks": { [key: string]: ApiSchemas["TaskConfig"] } }
  "AIConfigUpdate": { "providers": Array<ApiSchemas["ProviderCardUpdate"]>; "capabilities": ApiSchemas["CapabilityChainsInput"]; "tasks"?: { [key: string]: ApiSchemas["TaskConfigInput"] } }
  "ActionBudgetStatus": { "budget_date": string; "window_start": string; "window_end": string; "autonomous_create_used": number; "autonomous_create_limit": number; "user_requested_create_used": number; "user_requested_create_limit": number }
  "ActionCatalogEvent": { "packId": number; "catalogVersion": number; "appearanceEpoch": number }
  "ActionCatalogResponse": { "pack_id": number; "catalog_version": number; "appearance_epoch": number; "manifest_url": (string | null) }
  "ActionDesignRequest": { "name": string; "motion_description": string; "use_when"?: Array<string>; "avoid_when"?: Array<string>; "reason": string; "duration_seconds": number; "clip_kind": string; "expected_pack_id"?: (number | null) }
  "ActionDesignResult": { "outcome": string; "proposal_id": (number | null); "action_id": (number | null); "message": string }
  "ActionJobEvent": { "packId": number; "outfitId": (number | null); "actionId"?: (number | null); "action": string; "stage": string }
  "ActionPlayCommand": { "play_id": string; "pack_id": number; "appearance_epoch": number; "action_id": number; "asset_revision_id"?: (number | null); "repeat_count"?: number; "expires_at"?: (string | null); "source"?: string }
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
  "Body_create_version_api_admin_updates_versions_post": { "file": string; "release_notes"?: string }
  "Body_import_user_backup_api_admin_users__user_id__import_post": { "file": string }
  "Body_speech_to_text_api_media_stt_post": { "audio_file"?: (string | null) }
  "Body_upload_action_api_companion_desktop_videos_actions__action_id__upload_post": { "file": string; "expected_action_version": number; "expected_set_id"?: (number | null); "expected_draft_id"?: (string | null); "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "aspect_policy"?: "ask" | "keep" | "expand" }
  "Body_upload_chat_audio_api_media_audios_post": { "file": string; "session_id": string }
  "Body_upload_chat_video_api_media_videos_post": { "file"?: (string | null); "session_id"?: string }
  "Body_upload_image_api_companion_desktop_videos_actions__action_id__image_upload_post": { "file": string; "expected_action_version": number; "expected_set_id"?: (number | null); "expected_draft_id"?: (string | null); "expected_base_version"?: (number | null); "expected_resolution_revision"?: (number | null); "aspect_policy"?: "ask" | "keep" | "expand" }
  "Body_upload_voice_reference_api_media_voice_samples_post": { "file": string }
  "CacheUsage": { "request_count": number; "reported_request_count": number; "input_tokens": (number | null); "cached_input_tokens": (number | null); "cache_hit_rate": (number | null) }
  "CallCancelRequest": { "req_id"?: (string | number | null) }
  "CallGetRequest": { "call_id": string }
  "CapabilityCard": { "id": string; "provider_id": string; "model_name": string; "context_window": (number | null); "max_output_tokens": (number | null); "input_modalities": Array<ApiSchemas["ModelModality"]>; "output_modalities": Array<ApiSchemas["ModelModality"]> }
  "CapabilityCardInput": { "id": string; "provider_id": string; "model_name": string; "context_window"?: (number | null); "max_output_tokens"?: (number | null); "input_modalities": Array<ApiSchemas["ModelModalityInput"]>; "output_modalities": Array<ApiSchemas["ModelModalityInput"]> }
  "CapabilityChains": { "llm": Array<ApiSchemas["CapabilityCard"]>; "stt": Array<ApiSchemas["CapabilityCard"]>; "tts": Array<ApiSchemas["CapabilityCard"]>; "image_gen": Array<ApiSchemas["CapabilityCard"]>; "video_gen": Array<ApiSchemas["CapabilityCard"]>; "embedding": Array<ApiSchemas["CapabilityCard"]> }
  "CapabilityChainsInput": { "llm"?: Array<ApiSchemas["CapabilityCardInput"]>; "stt"?: Array<ApiSchemas["CapabilityCardInput"]>; "tts"?: Array<ApiSchemas["CapabilityCardInput"]>; "image_gen"?: Array<ApiSchemas["CapabilityCardInput"]>; "video_gen"?: Array<ApiSchemas["CapabilityCardInput"]>; "embedding"?: Array<ApiSchemas["CapabilityCardInput"]> }
  "ChainPolicy": { "chain": Array<string> }
  "ChainPolicyInput": { "chain"?: Array<string> }
  "CharacterCardEvent": { "avatar_id": number; "revision": number; "status": "pending" | "running" | "ready" | "failed" }
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
  "CommandDispatchRequest": { "session_id": string; "command": string; "args"?: (Array<(string | number | number)> | string | null); "confirmed"?: boolean }
  "CommandDispatchResult": { "command": string; "result": ApiSchemas["CommandResult"] }
  "CommandListResponse": { "commands": Array<ApiSchemas["CommandSummary"]> }
  "CommandResult": { "status": "ok" | "error"; "message": string; "payload"?: ({ [key: string]: ApiSchemas["JsonValue"] } | null); "hydrate"?: boolean }
  "CommandSummary": { "name": string; "aliases": Array<string>; "description": string; "requires_confirmation": boolean }
  "CompanionMessageEvent": { "session_id": string; "message_id": number; "bubbles": Array<ApiSchemas["ReplyBubbleView"]> }
  "CompanionOperationResponse": { "ok": boolean }
  "CompanionSessionMessageView": { "id": number; "role": "assistant"; "content": (string | null); "subtype": string; "reasoning": (string | null); "timestamp": number; "can_replace_reply": boolean; "media": Array<ApiSchemas["ClientMediaEntry"]>; "tool_calls": ApiSchemas["JsonValue"]; "tool_call_id": (string | null); "tool_name": string; "content_type": "companion_reply"; "bubbles": Array<ApiSchemas["ReplyBubbleView"]> }
  "CompanionSignal": { "available": boolean; "presentation_mode": "desktop" | "window"; "event"?: ("desktop_available" | "context_changed" | null) }
  "CompanionSignalResult": { "queued": boolean }
  "CompressionEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "session_id"?: (string | null); "subtype": string; "text": string; "message_id"?: (number | null) }
  "CompressionUsage": { "total_tokens": number; "context_window": number }
  "ConfigReplaceRequest": { "config": { [key: string]: ApiSchemas["JsonValue"] } }
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
  "DesktopVideoUpdatedEvent": { "version": number }
  "DeviceListResponse": { "items": Array<ApiSchemas["DeviceResponse"]> }
  "DeviceResponse": { "id": number; "name": string; "expires_at": string; "last_seen_at": string; "created_at": string }
  "DiaryDeletedEvent": { "diary_id": string; "entry_date": string }
  "DiaryEntryResponse": { "id": string; "entry_date": string; "title": string; "body": string; "post_ids": Array<string>; "created_at": (string | null); "updated_at": (string | null) }
  "DiaryListResponse": { "entries": Array<ApiSchemas["DiaryEntryResponse"]>; "unread_diary_ids": Array<string> }
  "DiaryReadRequest": { "diary_ids": Array<string> }
  "DiaryUnreadResponse": { "has_unread": boolean }
  "EmptyRequest": Record<string, never>
  "EmptyResult": { [key: string]: unknown }
  "ExchangeRequest": { "token": string; "device_name"?: string }
  "FullbodyAdoptRequest": { "image": string; "content_type"?: (string | null) }
  "FullbodyCandidateResponse": { "id": number; "avatar_id": number; "image_url": string; "status": "pending" | "ready" | "failed" | "accepted" | "rejected"; "error": (string | null) }
  "FullbodyConfirmRequest": { "expected_url": string }
  "FullbodyPromptRequest": { "feedback"?: (string | null) }
  "FullbodyReferenceGenerateRequest": { "feedback"?: (string | null); "image"?: (string | null); "content_type"?: (string | null); "mode": "edit" | "regenerate"; "candidate_id"?: (number | null) }
  "GatewayErrorEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "message": string; "retry_message_id"?: (number | null) }
  "GatewaySessionResumeResult": { "session_id": string; "message_count": number; "messages": Array<ApiSchemas["SessionMessageView"]>; "info": ApiSchemas["SessionRuntimeInfo"]; "resumed": boolean; "replayed_count": number; "current_seq": number; "stream_id": string; "active_turn": (ApiSchemas["ActiveTurnView"] | null); "last_submission": (ApiSchemas["LastSubmissionSnapshot"] | null); "truncated": boolean; "next_cursor": (string | null); "incremental": boolean; "history_revision": number }
  "GatewayTicketResponse": { "ticket": string; "expires_in": number }
  "IdleExpressionRequest": { "trigger"?: "periodic" | "user_return"; "idle_seconds"?: (number | null); "local_hour"?: (number | null) }
  "IdleExpressionResult": { "expressed": boolean; "action_id"?: (number | null); "reason"?: string }
  "ImageClipUpload": { "media_type": "image"; "action": string; "data": string; "content_type"?: (string | null) }
  "ImagePromptResponse": { "prompt": string }
  "InteractionStatsRequest": { "kind": "chat_turn"; "hour": number }
  "InteractionStatsResult": { "recorded": "chat_turn"; "threshold_met": boolean; "peak_hour": (number | null) }
  "JsonValue": unknown
  "JsonValueInput": unknown
  "LastSubmissionSnapshot": { "request_id": string; "status": string; "error": (string | null); "message_ids": Array<number>; "retry_message_id": (number | null); "replace_reply_message_id"?: (number | null); "result_message_id"?: (number | null) }
  "LinkBubble": { "type": "link"; "topic_id": number; "title": string; "platform": "x" | "xiaohongshu" | "douyin"; "url": string; "published_at": string }
  "MediaReviewResponse": { "id": number; "status": "pending" | "accepted" | "rejected"; "reason": string; "media_type": "image" | "video"; "media_url": string; "title": string; "system_slot": string }
  "MemoryChangedEvent": { "system_preset_id": string; "memory_id"?: (number | null) }
  "MemoryDeleteResponse": { "deleted": boolean }
  "MemoryEvidence": { "fingerprint": string; "message_id"?: (number | null); "session_id"?: (number | null); "quote"?: (string | null); "stance"?: ("supports" | "opposes" | null); "created_at"?: (string | null); "source_unavailable"?: (boolean | null) }
  "MemoryListItem": { "id": number; "content_version": number; "content": string; "context": (string | null); "tags": (string | null); "basis": "explicit" | "inferred" | "observed" | "system"; "status": "active" | "candidate" | "invalidated" | "forgotten"; "usage": "contextual" | "background"; "reason": string; "source_kind": string; "updated_at": string; "evidence": Array<ApiSchemas["MemoryEvidence"]>; "expires_at": (string | null); "reviewed_at": (string | null); "system_preset_id": string; "importance": number; "created_at": string }
  "MemoryListResponse": { "memories": Array<ApiSchemas["MemoryListItem"]>; "counts": { [key: string]: number }; "total": number; "system_preset_id": string }
  "MemoryProfileChange": { "field": "user_call_name" | "user_gender" | "user_birthday" | "user_hobbies" | "user_freeform"; "value": (string | null); "expected_version": number }
  "MemoryProfileRequest": { "system_preset_id": "companion"; "changes": Array<ApiSchemas["MemoryProfileChange"]> }
  "MemoryProfileResponse": { "memories": Array<ApiSchemas["MemoryListItem"]>; "system_preset_id": "companion" }
  "MemoryUpdateRequest": { "system_preset_id": string; "content": string; "expected_version": number }
  "MessageAudioEvent": { "session_id": string; "message_id": number; "audio_url": string; "duration"?: (number | null); "transcript"?: (string | null) }
  "MessageBubbleEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "message_id": number; "bubble_index": number; "bubble": ApiSchemas["ReplyBubbleView"] }
  "MessageCompleteEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "text"?: (string | null); "bubbles"?: (Array<ApiSchemas["ReplyBubbleView"]> | null); "reasoning"?: (string | null); "media"?: (Array<ApiSchemas["ClientMediaEntry"]> | null); "usage"?: (ApiSchemas["TokenUsage"] | null); "cache_usage"?: (ApiSchemas["CacheUsage"] | null); "message_id"?: (number | null); "can_replace_reply": boolean }
  "MessageDeletedEvent": { "session_id": string; "messages": Array<ApiSchemas["SessionMessageView"]>; "deleted_count": number; "anchor": { [key: string]: ApiSchemas["JsonValue"] } }
  "MessageDeltaEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "text": string }
  "MessageEditedEvent": { "session_id": string; "messages": Array<ApiSchemas["SessionMessageView"]> }
  "MessageMediaEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "message_id": number; "bubble_index": number; "bubble": ApiSchemas["ClientMediaBubble"]; "session_id": string; "media_id": string }
  "MessagePersistedEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "role": string; "message_ids": Array<number>; "messages": Array<ApiSchemas["SessionMessageView"]> }
  "MessageReplacedEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "session_id": string; "replaced_message_id": number; "message": ApiSchemas["SessionMessageView"]; "history_revision": number }
  "MessageResponse": { "message": string }
  "MessageVoiceEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "message_id": number; "bubble_index": number; "bubble": ApiSchemas["VoiceBubbleView"]; "session_id": string }
  "ModelDiscoveryRequest": { "provider": ApiSchemas["ProviderCardUpdate"]; "user_id"?: (number | null); "service": "llm" | "stt" | "tts" | "image_gen" | "video_gen" | "embedding"; "model_name"?: (string | null); "catalog_id"?: (string | null); "refresh"?: boolean }
  "ModelDiscoveryResponse": { "models": Array<ApiSchemas["ModelMetadata"]>; "candidates": { [key: string]: Array<ApiSchemas["ModelMetadata"]> }; "warnings": Array<string>; "unsupported": boolean; "catalog_updated_at": (string | null) }
  "ModelMetadata": { "model_name": string; "display_name": string; "services": Array<string>; "context_window": (number | null); "max_output_tokens": (number | null); "input_modalities": (Array<ApiSchemas["ModelModality"]> | null); "output_modalities": (Array<ApiSchemas["ModelModality"]> | null); "catalog_id": (string | null); "canonical_slug": (string | null); "catalog_url": (string | null); "field_sources": { [key: string]: string } }
  "ModelModality": "text" | "image" | "audio" | "video" | "embeddings"
  "ModelModalityInput": "text" | "image" | "audio" | "video" | "embeddings"
  "MoodEvent": { "mood": string }
  "NightlyActivityLogItem": { "id": number; "user_id": number; "target_date": string; "status": string; "summary": (string | null); "payload": ({ [key: string]: unknown } | null); "created_at": (string | null); "updated_at": (string | null) }
  "NightlyActivityLogListResponse": { "items": Array<ApiSchemas["NightlyActivityLogItem"]> }
  "OnboardingPatchRequest": { "field": string; "value"?: (string | null) }
  "OnboardingStateResponse": { "answers": { [key: string]: string }; "next_field": (string | null); "complete": boolean }
  "OutfitAdoptRequest": { "source": "import"; "description"?: (string | null); "image": string; "content_type"?: (string | null) }
  "OutfitChangedEvent": { "outfit_id": number; "worn": boolean }
  "OutfitConfirmRequest": Record<string, never>
  "OutfitCreateRequest": { "source": "generate"; "description"?: (string | null); "image"?: (string | null); "content_type"?: (string | null) }
  "OutfitImageImportRequest": { "image": string; "content_type"?: (string | null); "source": "import" }
  "OutfitListResponse": { "outfits": Array<ApiSchemas["OutfitResponse"]>; "policy": "locked" | "llm_may_replace" }
  "OutfitPolicyRequest": { "policy": "locked" | "llm_may_replace" }
  "OutfitPolicyResponse": { "policy": "locked" | "llm_may_replace" }
  "OutfitPromptRequest": { "outfit_id"?: (number | null); "feedback"?: (string | null); "description"?: (string | null); "image"?: (string | null); "content_type"?: (string | null) }
  "OutfitRegenerateRequest": { "source": "generate"; "feedback"?: (string | null); "mode": "edit" | "regenerate" }
  "OutfitResponse": { "initial_video_error": (string | null); "description_status": "pending" | "processing" | "ready" | "failed"; "description_error": (string | null); "id": number; "name": string; "description": (string | null); "fullbody_url": string; "status": string; "active": boolean }
  "PackEvent": { "packId": number; "outfitId": (number | null) }
  "PackFailedEvent": { "packId": number; "outfitId": (number | null); "reason": string }
  "PackProgressEvent": { "packId": number; "outfitId": (number | null); "stage": string; "action"?: (string | null); "video_queue"?: (ApiSchemas["VideoQueueInfo"] | null) }
  "PairingResponse": { "id": number; "url"?: (string | null); "expires_at": string; "state": "pending" | "paired" | "cancelled" | "expired" }
  "PeekGeometry": { "side": "left" | "right"; "cut_x": number; "focus_rect": [number, number, number, number] }
  "PendingActionReview": { "id": number; "media_type": "image" | "video"; "media_url": string }
  "PersonaResponse": { "definition_json": string; "is_complete": boolean; "personality_tags": Array<string>; "current_mood": (string | null) }
  "PersonaUpdate": { "definition_json": string; "expected_fields"?: ({ [key: string]: (string | null) } | null) }
  "PlainSessionMessageView": { "id": number; "role": "assistant" | "system" | "tool" | "user"; "content": (string | null); "subtype": string; "reasoning": (string | null); "timestamp": number; "can_replace_reply": boolean; "media": Array<ApiSchemas["ClientMediaEntry"]>; "tool_calls": ApiSchemas["JsonValue"]; "tool_call_id": (string | null); "tool_name": string; "content_type": "multimodal_v1" | "text" }
  "PortraitConfirmRequest": { "expected_avatar_id": number }
  "PostCommentCreateRequest": { "content": string }
  "PostCommentDeletedEvent": { "post_id": string; "comment_id": string }
  "PostCommentEvent": { "post_id": string; "comment": ApiSchemas["PostCommentResponse"] }
  "PostCommentResponse": { "id": string; "post_id": string; "role": ApiSchemas["PostCommentRole"]; "content": string; "created_at": string; "updated_at": string; "reply_to_comment_id": (string | null); "reply_status": "none" | "pending" | "running" | "completed" | "failed"; "reply_error": (string | null) }
  "PostCommentRole": "user" | "companion"
  "PostContentType": "text" | "image" | "video" | "audio"
  "PostListResponse": { "posts": Array<ApiSchemas["PostResponse"]>; "next_cursor": (string | null); "unread_post_ids": Array<string> }
  "PostPublicationRecovery": { "publication_id": string; "status": "queued" | "running" | "published" | "partial" | "failed" | "blocked" | "result_unknown" | "declined" | "discarded"; "post_id": (string | null); "error": (string | null); "title": string; "video_status": "pending" | "ready" | "failed" | "unknown" | "discarded"; "media_url": (string | null); "can_adopt": boolean; "can_discard": boolean }
  "PostPublicationRecoveryList": { "items": Array<ApiSchemas["PostPublicationRecovery"]>; "next_offset": (number | null) }
  "PostPublicationResult": { "publication_id": string; "status": "queued" | "running" | "published" | "partial" | "failed" | "blocked" | "result_unknown" | "declined" | "discarded"; "post_id": (string | null); "error": (string | null) }
  "PostReadRequest": { "post_ids": Array<string> }
  "PostResponse": { "id": string; "published_at": string; "content_type": ApiSchemas["PostContentType"]; "title": string; "body": string; "media_url": (string | null); "audio_url": (string | null); "comments": Array<ApiSchemas["PostCommentResponse"]> }
  "PostUnreadResponse": { "has_unread": boolean }
  "PromptBatchMessage": { "text": string; "attachments"?: (Array<ApiSchemas["ChatAttachment"]> | null) }
  "PromptPresetListResponse": { "presets": Array<ApiSchemas["PromptPresetSummary"]> }
  "PromptPresetSummary": { "id": string; "name": string; "description": string; "icon_key": string }
  "PromptSubmitRequest": { "session_id": string; "request_id": string; "text"?: (string | null); "attachments"?: (Array<ApiSchemas["ChatAttachment"]> | null); "batch"?: (Array<ApiSchemas["PromptBatchMessage"]> | null); "edit_message_id"?: (number | null); "retry_message_id"?: (number | null); "replace_reply_message_id"?: (number | null); "response_preference"?: ("text" | "voice" | null) }
  "PromptSubmitResult": { "queued": boolean; "request_id": string; "status": "accepted" | "running" | "completed" | "cancelled" | "interrupted" | "failed"; "error"?: (string | null); "message_ids"?: Array<number>; "messages"?: Array<ApiSchemas["SessionMessageView"]>; "replace_reply_message_id"?: (number | null); "result_message_id"?: (number | null) }
  "ProviderCardPublic": { "id": string; "name": string; "provider_type": string; "api_key": string; "base_url": string; "api_key_set": boolean }
  "ProviderCardUpdate": { "id": string; "name": string; "provider_type": string; "api_key"?: string; "base_url"?: string; "api_key_set"?: boolean; "clear_api_key"?: boolean }
  "ProviderDefaults": { "base_url": string; "requires_api_key": boolean; "supports_voice_design": boolean; "voice_design_model": (string | null); "supports_voice_clone": boolean; "voice_clone_model": (string | null); "input_modalities": Array<ApiSchemas["ModelModality"]> }
  "RefreshRequest": { "client_version"?: string; "client_context"?: (ApiSchemas["ChatRequestClientContext"] | null) }
  "RegenerateActivationRequest": { "base_url"?: (string | null) }
  "ReleaseManifestFileItem": { "url": string; "sha512": string; "size": number }
  "ReleaseManifestResponse": { "version": string; "releaseDate": string; "releaseNotes": string; "path": string; "sha512": string; "files": Array<ApiSchemas["ReleaseManifestFileItem"]> }
  "RemoteSessionResponse": { "user": ApiSchemas["RemoteUserResponse"]; "device": ApiSchemas["DeviceResponse"]; "csrf_token": string }
  "RemoteUserResponse": { "id": number; "username": string }
  "ReplyAudio": { "url": string; "duration": number }
  "ReplyBubbleView": (ApiSchemas["TextBubble"] | ApiSchemas["VoiceBubbleView"] | ApiSchemas["ClientMediaBubble"] | ApiSchemas["LinkBubble"])
  "ResourceDecision": { "decision": "accept" | "reject" }
  "RpcActionCatalogEvent": { "packId": number; "catalogVersion": number; "appearanceEpoch": number }
  "RpcActionJobEvent": { "packId": number; "outfitId": (number | null); "actionId"?: (number | null); "action": string; "stage": string }
  "RpcActionPlayCommand": { "play_id": string; "pack_id": number; "appearance_epoch": number; "action_id": number; "asset_revision_id"?: (number | null); "repeat_count"?: number; "expires_at"?: (string | null); "source"?: string }
  "RpcActiveTurnView": { "request_id": string; "origin_kind": string; "message_ids": Array<number>; "assistant_message_id": (number | null); "messages": Array<ApiSchemas["RpcSessionMessageView"]>; "text": string; "reasoning": string; "bubbles": Array<ApiSchemas["RpcReplyBubbleView"]>; "tools": Array<ApiSchemas["RpcToolSnapshot"]>; "running": boolean; "replace_reply_message_id": (number | null) }
  "RpcCacheUsage": { "request_count": number; "reported_request_count": number; "input_tokens": (number | null); "cached_input_tokens": (number | null); "cache_hit_rate": (number | null) }
  "RpcCharacterCardEvent": { "avatar_id": number; "revision": number; "status": "pending" | "running" | "ready" | "failed" }
  "RpcChatAttachment": { "type": "image" | "video" | "audio"; "file_url": string }
  "RpcClientMediaBubble": { "type": "image" | "video"; "media_id": string; "status": "pending" | "ready" | "failed" | "result_unknown"; "url": (string | null); "error": (string | null); "video_queue"?: (ApiSchemas["RpcVideoQueueInfo"] | null) }
  "RpcClientMediaEntry": { "type": "image" | "video" | "audio"; "url": string; "audio_url"?: (string | null); "duration"?: (number | null); "transcript"?: (string | null); "review_id"?: (string | null) }
  "RpcCommandDispatchRequest": { "session_id": string; "command": string; "args"?: (Array<(string | number | number)> | string | null); "confirmed"?: boolean }
  "RpcCommandDispatchResult": { "command": string; "result": ApiSchemas["RpcCommandResult"] }
  "RpcCommandResult": { "status": "ok" | "error"; "message": string; "payload"?: ({ [key: string]: ApiSchemas["RpcJsonValue"] } | null); "hydrate"?: boolean }
  "RpcCompanionMessageEvent": { "session_id": string; "message_id": number; "bubbles": Array<ApiSchemas["RpcReplyBubbleView"]> }
  "RpcCompanionSessionMessageView": { "id"?: number; "role": "assistant"; "content": (string | null); "subtype"?: string; "reasoning"?: (string | null); "timestamp"?: number; "can_replace_reply"?: boolean; "media"?: Array<ApiSchemas["RpcClientMediaEntry"]>; "tool_calls"?: ApiSchemas["RpcJsonValue"]; "tool_call_id"?: (string | null); "tool_name"?: string; "content_type": "companion_reply"; "bubbles": Array<ApiSchemas["RpcReplyBubbleView"]> }
  "RpcCompanionSignal": { "available": boolean; "presentation_mode": "desktop" | "window"; "event"?: ("desktop_available" | "context_changed" | null) }
  "RpcCompanionSignalResult": { "queued": boolean }
  "RpcCompressionEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "session_id"?: (string | null); "subtype": string; "text": string; "message_id"?: (number | null) }
  "RpcCompressionUsage": { "total_tokens": number; "context_window": number }
  "RpcDesktopVideoPlayCommand": { "play_id": string; "set_id": number; "action_id": number; "set_epoch": number; "kind": "loop" | "once"; "expires_at": string; "video_url": string; "poster_url": string; "geometry_mode": "target" | "keep"; "version": number; "transition"?: "base" | "fade"; "continuity": "independent" | "base"; "base_version"?: (number | null) }
  "RpcDesktopVideoUpdatedEvent": { "version": number }
  "RpcDiaryDeletedEvent": { "diary_id": string; "entry_date": string }
  "RpcDiaryEntryResponse": { "id": string; "entry_date": string; "title": string; "body": string; "post_ids"?: Array<string>; "created_at"?: (string | null); "updated_at"?: (string | null) }
  "RpcEmptyRequest": Record<string, never>
  "RpcEmptyResult": { [key: string]: unknown }
  "RpcGatewayErrorEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "message": string; "retry_message_id"?: (number | null) }
  "RpcGatewaySessionResumeResult": { "session_id": string; "message_count": number; "messages": Array<ApiSchemas["RpcSessionMessageView"]>; "info": ApiSchemas["RpcSessionRuntimeInfo"]; "resumed": boolean; "replayed_count": number; "current_seq": number; "stream_id": string; "active_turn": (ApiSchemas["RpcActiveTurnView"] | null); "last_submission": (ApiSchemas["RpcLastSubmissionSnapshot"] | null); "truncated": boolean; "next_cursor": (string | null); "incremental": boolean; "history_revision": number }
  "RpcIdleExpressionRequest": { "trigger"?: "periodic" | "user_return"; "idle_seconds"?: (number | null); "local_hour"?: (number | null) }
  "RpcIdleExpressionResult": { "expressed": boolean; "action_id"?: (number | null); "reason"?: string }
  "RpcInteractionStatsRequest": { "kind": "chat_turn"; "hour": number }
  "RpcInteractionStatsResult": { "recorded": "chat_turn"; "threshold_met": boolean; "peak_hour": (number | null) }
  "RpcJsonValue": unknown
  "RpcLastSubmissionSnapshot": { "request_id": string; "status": string; "error": (string | null); "message_ids": Array<number>; "retry_message_id": (number | null); "replace_reply_message_id"?: (number | null); "result_message_id"?: (number | null) }
  "RpcLinkBubble": { "type": "link"; "topic_id": number; "title": string; "platform": "x" | "xiaohongshu" | "douyin"; "url": string; "published_at": string }
  "RpcMemoryChangedEvent": { "system_preset_id": string; "memory_id"?: (number | null) }
  "RpcMessageAudioEvent": { "session_id": string; "message_id": number; "audio_url": string; "duration"?: (number | null); "transcript"?: (string | null) }
  "RpcMessageBubbleEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "message_id": number; "bubble_index": number; "bubble": ApiSchemas["RpcReplyBubbleView"] }
  "RpcMessageCompleteEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "text"?: (string | null); "bubbles"?: (Array<ApiSchemas["RpcReplyBubbleView"]> | null); "reasoning"?: (string | null); "media"?: (Array<ApiSchemas["RpcClientMediaEntry"]> | null); "usage"?: (ApiSchemas["RpcTokenUsage"] | null); "cache_usage"?: (ApiSchemas["RpcCacheUsage"] | null); "message_id"?: (number | null); "can_replace_reply": boolean }
  "RpcMessageDeletedEvent": { "session_id": string; "messages": Array<ApiSchemas["RpcSessionMessageView"]>; "deleted_count": number; "anchor": { [key: string]: ApiSchemas["RpcJsonValue"] } }
  "RpcMessageDeltaEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "text": string }
  "RpcMessageEditedEvent": { "session_id": string; "messages": Array<ApiSchemas["RpcSessionMessageView"]> }
  "RpcMessageMediaEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "message_id": number; "bubble_index": number; "bubble": ApiSchemas["RpcClientMediaBubble"]; "session_id": string; "media_id": string }
  "RpcMessagePersistedEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "role": string; "message_ids": Array<number>; "messages": Array<ApiSchemas["RpcSessionMessageView"]> }
  "RpcMessageReplacedEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "session_id": string; "replaced_message_id": number; "message": ApiSchemas["RpcSessionMessageView"]; "history_revision": number }
  "RpcMessageVoiceEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "message_id": number; "bubble_index": number; "bubble": ApiSchemas["RpcVoiceBubbleView"]; "session_id": string }
  "RpcMoodEvent": { "mood": string }
  "RpcOutfitChangedEvent": { "outfit_id": number; "worn": boolean }
  "RpcPackEvent": { "packId": number; "outfitId": (number | null) }
  "RpcPackFailedEvent": { "packId": number; "outfitId": (number | null); "reason": string }
  "RpcPackProgressEvent": { "packId": number; "outfitId": (number | null); "stage": string; "action"?: (string | null); "video_queue"?: (ApiSchemas["RpcVideoQueueInfo"] | null) }
  "RpcPlainSessionMessageView": { "id"?: number; "role": "assistant" | "system" | "tool" | "user"; "content": (string | null); "subtype"?: string; "reasoning"?: (string | null); "timestamp"?: number; "can_replace_reply"?: boolean; "media"?: Array<ApiSchemas["RpcClientMediaEntry"]>; "tool_calls"?: ApiSchemas["RpcJsonValue"]; "tool_call_id"?: (string | null); "tool_name"?: string; "content_type": "multimodal_v1" | "text" }
  "RpcPostCommentDeletedEvent": { "post_id": string; "comment_id": string }
  "RpcPostCommentEvent": { "post_id": string; "comment": ApiSchemas["RpcPostCommentResponse"] }
  "RpcPostCommentResponse": { "id": string; "post_id": string; "role": ApiSchemas["RpcPostCommentRole"]; "content": string; "created_at": string; "updated_at": string; "reply_to_comment_id": (string | null); "reply_status": "none" | "pending" | "running" | "completed" | "failed"; "reply_error": (string | null) }
  "RpcPostCommentRole": "user" | "companion"
  "RpcPostContentType": "text" | "image" | "video" | "audio"
  "RpcPostResponse": { "id": string; "published_at": string; "content_type": ApiSchemas["RpcPostContentType"]; "title": string; "body": string; "media_url": (string | null); "audio_url": (string | null); "comments": Array<ApiSchemas["RpcPostCommentResponse"]> }
  "RpcPromptBatchMessage": { "text": string; "attachments"?: (Array<ApiSchemas["RpcChatAttachment"]> | null) }
  "RpcPromptSubmitRequest": { "session_id": string; "request_id": string; "text"?: (string | null); "attachments"?: (Array<ApiSchemas["RpcChatAttachment"]> | null); "batch"?: (Array<ApiSchemas["RpcPromptBatchMessage"]> | null); "edit_message_id"?: (number | null); "retry_message_id"?: (number | null); "replace_reply_message_id"?: (number | null); "response_preference"?: ("text" | "voice" | null) }
  "RpcPromptSubmitResult": { "queued": boolean; "request_id": string; "status": "accepted" | "running" | "completed" | "cancelled" | "interrupted" | "failed"; "error"?: (string | null); "message_ids"?: Array<number>; "messages"?: Array<ApiSchemas["RpcSessionMessageView"]>; "replace_reply_message_id"?: (number | null); "result_message_id"?: (number | null) }
  "RpcReplyAudio": { "url": string; "duration": number }
  "RpcReplyBubbleView": (ApiSchemas["RpcTextBubble"] | ApiSchemas["RpcVoiceBubbleView"] | ApiSchemas["RpcClientMediaBubble"] | ApiSchemas["RpcLinkBubble"])
  "RpcRunnerToolSchema": { "name": string; "description"?: string; "parameters"?: { [key: string]: ApiSchemas["RpcJsonValue"] }; [key: string]: unknown }
  "RpcSceneChangedEvent": { "scene_id"?: (number | null); "version": number; "switch_version": number }
  "RpcSessionAckRequest": { "seq": number }
  "RpcSessionAckResult": { "acked": number; "pruned": number }
  "RpcSessionCompressionResult": { "session_id": string; "compressed": boolean; "reason"?: (string | null); "usage": ApiSchemas["RpcCompressionUsage"]; "replaced_count"?: (number | null); "summary"?: (string | null); "messages"?: (Array<ApiSchemas["RpcSessionMessageView"]> | null) }
  "RpcSessionListChangedEvent": { "session_id": string; "deleted"?: boolean }
  "RpcSessionMessageView": (ApiSchemas["RpcPlainSessionMessageView"] | ApiSchemas["RpcCompanionSessionMessageView"])
  "RpcSessionRequest": { "session_id": string }
  "RpcSessionResumeRequest": { "session_id": string; "last_seq"?: (number | null); "after_id"?: (number | null); "history_revision"?: (number | null); "stream_id"?: (string | null) }
  "RpcSessionRuntimeInfo": { "model": (string | null); "provider": string; "running": boolean; "system_preset_id": string; "settings"?: { [key: string]: unknown }; "context_window"?: (number | null); "kind"?: string; "is_automation"?: boolean }
  "RpcSessionStateEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "messages"?: (Array<ApiSchemas["RpcSessionMessageView"]> | null); "running": boolean; "status": string; "error": (string | null); "result_message_id"?: (number | null) }
  "RpcSessionUndoRequest": { "session_id": string; "source_message_id": number; "confirmed"?: boolean }
  "RpcSessionUndoResult": { "session_id": string; "deleted_count": number; "anchor": ApiSchemas["RpcUndoAnchor"]; "messages": Array<ApiSchemas["RpcSessionMessageView"]> }
  "RpcShouldActRequest": { "kind"?: "periodic_provision"; "idle_seconds"?: (number | null); "local_hour"?: (number | null); "focused_category"?: (string | null); "fullscreen"?: boolean; "screen_locked"?: boolean; "seconds_since_last_action"?: (number | null) }
  "RpcShouldActResult": { "should_act"?: boolean; "action"?: (string | null); "params"?: ({ [key: string]: unknown } | null); "reason"?: string }
  "RpcSystemNotificationEvent": { "kind": "error" | "success"; "title": string; "message": string; "session_id": string }
  "RpcTextBubble": { "type": "text"; "text": string }
  "RpcTokenUsage": { "prompt_tokens"?: (number | null); "completion_tokens"?: (number | null); "total_tokens"?: (number | null); "input_tokens"?: (number | null); "output_tokens"?: (number | null); "reasoning_tokens"?: (number | null); "context_window"?: (number | null) }
  "RpcToolCallEvent": { "call_id": string; "name": string; "args": { [key: string]: ApiSchemas["RpcJsonValue"] }; "session_id": (string | null); "headless": boolean; "skill_scope": (ApiSchemas["RpcToolScope"] | null) }
  "RpcToolCancelEvent": { "call_id": string }
  "RpcToolProgressEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "name": (string | null); "call_id": (string | null); "status": "running" | "complete" }
  "RpcToolResultRequest": { "call_id": string; "result": ApiSchemas["RpcJsonValue"] }
  "RpcToolScope": { "user_id": number; "system_preset_id": string }
  "RpcToolSnapshot": { "name": (string | null); "call_id": (string | null); "status": string }
  "RpcToolsSyncRequest": { "skill_scope_version": 1; "tools": Array<ApiSchemas["RpcRunnerToolSchema"]> }
  "RpcToolsSyncResult": { "count": number }
  "RpcTopicFeedbackSnapshot": { "topic_id": number; "value": ("like" | "less" | null); "updated_at": (string | null) }
  "RpcTurnEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null) }
  "RpcUndoAnchor": { "text": string; "attachments": Array<ApiSchemas["RpcUndoAttachment"]> }
  "RpcUndoAttachment": { "type": "image"; "url": string }
  "RpcVideoCompletedEvent": { "task_id": string; "session_id": (string | null); "url": string; "media": Array<ApiSchemas["RpcClientMediaEntry"]>; "warning"?: (string | null) }
  "RpcVideoFailedEvent": { "task_id": string; "session_id": (string | null); "error": (string | null); "status_message_id": (number | null); "status_text": (string | null) }
  "RpcVideoQueueEvent": { "task_id": string; "session_id": (string | null); "media_id": (string | null); "video_queue": (ApiSchemas["RpcVideoQueueInfo"] | null) }
  "RpcVideoQueueInfo": { "state": "waiting" | "rate_limited" | "blocked"; "next_retry_at"?: (string | null) }
  "RpcVoiceBubbleView": { "type": "voice"; "text": string; "audio": (ApiSchemas["RpcReplyAudio"] | null) }
  "RpcVoiceUpdatedEvent": { "voice_id": string; "version": number }
  "RunnerCallCancelRequest": { "req_id"?: (string | number | null) }
  "RunnerCallGetRequest": { "call_id": string }
  "RunnerCallResult": { "call_id": string; "status": "claimed" | "completed" | "failed" | "unknown" | "not_found"; "result"?: ApiSchemas["JsonValue"]; "error"?: (string | null); "claimed_at"?: (number | number | null); "finished_at"?: (number | number | null) }
  "RunnerCancelResult": { "ok": boolean; "error"?: ("no_in_flight" | null) }
  "RunnerCapabilitiesChangedPayload": { "run_generation": string; "capabilities": ApiSchemas["RunnerCapabilitiesPayload"]; "probe_failed": boolean }
  "RunnerCapabilitiesPayload": { "microphone"?: (boolean | null); "screen_capture"?: (boolean | null); "system_activity"?: (boolean | null); "platform"?: (string | null); "python"?: (string | null) }
  "RunnerCapabilityHealth": { "available": boolean; "reason"?: (string | null) }
  "RunnerConfigReplaceRequest": { "config": { [key: string]: ApiSchemas["RunnerJsonValue"] } }
  "RunnerConfigResult": { "ok"?: true }
  "RunnerEmptyRequest": Record<string, never>
  "RunnerInfoResult": { "version": string; "run_generation": string; "pid": number; "capabilities": ApiSchemas["RunnerCapabilitiesPayload"]; "capabilities_health": { [key: string]: ApiSchemas["RunnerCapabilityHealth"] }; "probe_failed": boolean; "reconnect_streak": number; "started_at": number; "uptime_seconds": number; "reconnect_count": number; "tool_count": number; "network_reachable": boolean; "disk_free_bytes": (number | null); "system": ApiSchemas["RunnerSystemInfo"] }
  "RunnerJsonValue": unknown
  "RunnerReadyPayload": { "version": string; "run_generation": string; "pid": number; "capabilities": ApiSchemas["RunnerCapabilitiesPayload"]; "capabilities_health": { [key: string]: ApiSchemas["RunnerCapabilityHealth"] }; "probe_failed": boolean; "reconnect_streak": number }
  "RunnerRunnerCallResult": { "call_id": string; "status": "claimed" | "completed" | "failed" | "unknown" | "not_found"; "result"?: ApiSchemas["RunnerJsonValue"]; "error"?: (string | null); "claimed_at"?: (number | number | null); "finished_at"?: (number | number | null) }
  "RunnerRunnerCancelResult": { "ok": boolean; "error"?: ("no_in_flight" | null) }
  "RunnerRunnerCapabilitiesChangedPayload": { "run_generation": string; "capabilities": ApiSchemas["RunnerRunnerCapabilitiesPayload"]; "probe_failed": boolean }
  "RunnerRunnerCapabilitiesPayload": { "microphone"?: (boolean | null); "screen_capture"?: (boolean | null); "system_activity"?: (boolean | null); "platform"?: (string | null); "python"?: (string | null) }
  "RunnerRunnerCapabilityHealth": { "available": boolean; "reason"?: (string | null) }
  "RunnerRunnerConfigResult": { "ok"?: true }
  "RunnerRunnerEmptyRequest": Record<string, never>
  "RunnerRunnerInfoResult": { "version": string; "run_generation": string; "pid": number; "capabilities": ApiSchemas["RunnerRunnerCapabilitiesPayload"]; "capabilities_health": { [key: string]: ApiSchemas["RunnerRunnerCapabilityHealth"] }; "probe_failed": boolean; "reconnect_streak": number; "started_at": number; "uptime_seconds": number; "reconnect_count": number; "tool_count": number; "network_reachable": boolean; "disk_free_bytes": (number | null); "system": ApiSchemas["RunnerRunnerSystemInfo"] }
  "RunnerRunnerReadyPayload": { "version": string; "run_generation": string; "pid": number; "capabilities": ApiSchemas["RunnerRunnerCapabilitiesPayload"]; "capabilities_health": { [key: string]: ApiSchemas["RunnerRunnerCapabilityHealth"] }; "probe_failed": boolean; "reconnect_streak": number }
  "RunnerRunnerSystemInfo": { "platform": string; "python": string; "release": string; "machine": string }
  "RunnerRunnerToolResult": ApiSchemas["RunnerJsonValue"]
  "RunnerRunnerToolSchema": { "name": string; "description"?: string; "parameters"?: { [key: string]: ApiSchemas["RunnerJsonValue"] }; [key: string]: unknown }
  "RunnerRunnerToolsResult": { "tools": Array<ApiSchemas["RunnerRunnerToolSchema"]> }
  "RunnerSkillScopeRequest": { "user_id": number; "system_preset_id": "companion" | "copywriter" | "language_teacher" }
  "RunnerSystemInfo": { "platform": string; "python": string; "release": string; "machine": string }
  "RunnerToolExecuteRequest": { "name": string; "args"?: { [key: string]: ApiSchemas["RunnerJsonValue"] }; "call_id"?: (string | null); "skill_scope"?: (ApiSchemas["RunnerSkillScopeRequest"] | null) }
  "RunnerToolResult": ApiSchemas["JsonValue"]
  "RunnerToolSchema": { "name": string; "description"?: string; "parameters"?: { [key: string]: ApiSchemas["JsonValue"] }; [key: string]: unknown }
  "RunnerToolsResult": { "tools": Array<ApiSchemas["RunnerToolSchema"]> }
  "SceneActivateRequest": { "scene_id": number }
  "SceneChangedEvent": { "scene_id"?: (number | null); "version": number; "switch_version": number }
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
  "SceneResponse": { "id": number; "status": ApiSchemas["SceneStatus"]; "stage": string; "origin": ApiSchemas["SceneOrigin"]; "source": ApiSchemas["SceneSource"]; "target_size": (ApiSchemas["SceneImageSize"] | null); "source_size": (ApiSchemas["SceneImageDimensions"] | null); "image_size": (ApiSchemas["SceneImageDimensions"] | null); "title": string; "description": string; "requirements": string; "prompt": string; "url": string; "auto_activate": boolean; "switch_version": number; "error": (string | null); "attempt_count": number; "requested_at": (string | null); "ready_at": (string | null); "activated_at": (string | null); "regeneration": (ApiSchemas["SceneRegenerationResponse"] | null) }
  "SceneSource": "generated" | "user_upload"
  "SceneStateResponse": { "active": (ApiSchemas["SceneResponse"] | null); "policy": ApiSchemas["ScenePolicy"]; "pending": (ApiSchemas["SceneResponse"] | null); "regenerating": (ApiSchemas["SceneResponse"] | null); "version": number; "switch_version": number }
  "SceneStatus": "pending" | "ready" | "description_failed" | "failed" | "cancelled"
  "SessionAckRequest": { "seq": number }
  "SessionAckResult": { "acked": number; "pruned": number }
  "SessionCompressionResult": { "session_id": string; "compressed": boolean; "reason"?: (string | null); "usage": ApiSchemas["CompressionUsage"]; "replaced_count"?: (number | null); "summary"?: (string | null); "messages"?: (Array<ApiSchemas["SessionMessageView"]> | null) }
  "SessionCreateRequest": { "system_preset_id": string }
  "SessionCreateResult": { "session_id": string; "info": ApiSchemas["SessionRuntimeInfo"] }
  "SessionForkRequest": { "source_message_id": number }
  "SessionHistoryResponse": { "messages": Array<ApiSchemas["SessionMessageView"]>; "next_cursor": (number | null); "has_more": boolean }
  "SessionListChangedEvent": { "session_id": string; "deleted"?: boolean }
  "SessionMessageView": (ApiSchemas["PlainSessionMessageView"] | ApiSchemas["CompanionSessionMessageView"])
  "SessionRequest": { "session_id": string }
  "SessionResumeRequest": { "session_id": string; "last_seq"?: (number | null); "after_id"?: (number | null); "history_revision"?: (number | null); "stream_id"?: (string | null) }
  "SessionRuntimeInfo": { "model": (string | null); "provider": string; "running": boolean; "system_preset_id": string; "settings": { [key: string]: unknown }; "context_window": (number | null); "kind": string; "is_automation": boolean }
  "SessionSettingsPatch": { "temperature"?: (number | null); "context_compression_threshold"?: (number | null); "reasoning_effort"?: ("none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra" | null) }
  "SessionStateEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "messages"?: (Array<ApiSchemas["SessionMessageView"]> | null); "running": boolean; "status": string; "error": (string | null); "result_message_id"?: (number | null) }
  "SessionUndoRequest": { "session_id": string; "source_message_id": number; "confirmed"?: boolean }
  "SessionUndoResult": { "session_id": string; "deleted_count": number; "anchor": ApiSchemas["UndoAnchor"]; "messages": Array<ApiSchemas["SessionMessageView"]> }
  "ShouldActRequest": { "kind"?: "periodic_provision"; "idle_seconds"?: (number | null); "local_hour"?: (number | null); "focused_category"?: (string | null); "fullscreen"?: boolean; "screen_locked"?: boolean; "seconds_since_last_action"?: (number | null) }
  "ShouldActResult": { "should_act"?: boolean; "action"?: (string | null); "params"?: ({ [key: string]: unknown } | null); "reason"?: string }
  "SkillScopeRequest": { "user_id": number; "system_preset_id": "companion" | "copywriter" | "language_teacher" }
  "SpeechToTextResponse": { "text": string }
  "SystemNotificationEvent": { "kind": "error" | "success"; "title": string; "message": string; "session_id": string }
  "TaskConfig": { "chains": { [key: string]: ApiSchemas["ChainPolicy"] } }
  "TaskConfigInput": { "chains"?: { [key: string]: ApiSchemas["ChainPolicyInput"] } }
  "TextBubble": { "type": "text"; "text": string }
  "TokenResponse": { "access_token": string; "expires_in": number; "user": ApiSchemas["UserInfo"] }
  "TokenUsage": { "prompt_tokens"?: (number | null); "completion_tokens"?: (number | null); "total_tokens"?: (number | null); "input_tokens"?: (number | null); "output_tokens"?: (number | null); "reasoning_tokens"?: (number | null); "context_window"?: (number | null) }
  "ToolCallEvent": { "call_id": string; "name": string; "args": { [key: string]: ApiSchemas["JsonValue"] }; "session_id": (string | null); "headless": boolean; "skill_scope": (ApiSchemas["ToolScope"] | null) }
  "ToolCancelEvent": { "call_id": string }
  "ToolExecuteRequest": { "name": string; "args"?: { [key: string]: ApiSchemas["JsonValue"] }; "call_id"?: (string | null); "skill_scope"?: (ApiSchemas["SkillScopeRequest"] | null) }
  "ToolProgressEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null); "name": (string | null); "call_id": (string | null); "status": "running" | "complete" }
  "ToolResultRequest": { "call_id": string; "result": ApiSchemas["JsonValue"] }
  "ToolScope": { "user_id": number; "system_preset_id": string }
  "ToolSnapshot": { "name": (string | null); "call_id": (string | null); "status": string }
  "ToolsSyncRequest": { "skill_scope_version": 1; "tools": Array<ApiSchemas["RunnerToolSchema"]> }
  "ToolsSyncResult": { "count": number }
  "TopicFeedbackRequest": { "value": ("like" | "less" | null) }
  "TopicFeedbackSnapshot": { "topic_id": number; "value": ("like" | "less" | null); "updated_at": (string | null) }
  "TtsRequest": { "text"?: string; "voice"?: string; "language"?: string }
  "TurnEvent": { "request_id"?: (string | null); "origin_kind"?: (string | null); "replace_reply_message_id"?: (number | null) }
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
  "VideoCompletedEvent": { "task_id": string; "session_id": (string | null); "url": string; "media": Array<ApiSchemas["ClientMediaEntry"]>; "warning"?: (string | null) }
  "VideoFailedEvent": { "task_id": string; "session_id": (string | null); "error": (string | null); "status_message_id": (number | null); "status_text": (string | null) }
  "VideoPackCreateRequest": { "source": "import"; "outfit_id": number; "clips": Array<(ApiSchemas["ImageClipUpload"] | ApiSchemas["VideoClipUpload"])>; "canvas_width"?: number; "canvas_height"?: number }
  "VideoPackEnsureRequest": { "action": "peek_left" | "peek_right"; "command": "ensure" }
  "VideoPackGenerateRequest": { "source": "generate"; "outfit_id"?: (number | null); "force"?: boolean; "source_pack_id"?: (number | null); "action"?: (string | null); "feedback"?: string }
  "VideoPackListResponse": { "packs": Array<ApiSchemas["VideoPackResponse"]> }
  "VideoPackResponse": { "id": number; "outfit_id": (number | null); "status": string; "active": boolean; "identity_review": "none" | "pass" | "review" | "accepted"; "identity_review_reason": string; "content_hash": (string | null); "manifest_url": (string | null); "can_retry": boolean; "can_regenerate": boolean; "can_regenerate_pack": boolean; "actions": Array<(ApiSchemas["VideoActionImageResponse"] | ApiSchemas["VideoActionVideoResponse"])>; "error": (string | null) }
  "VideoPackRetryRequest": { "command": "retry" }
  "VideoPackSelectionRequest": { "pack_id": number }
  "VideoQueueAttachTask": { "expected_revision": number; "note": string; "action": "attach_task"; "task_id": string }
  "VideoQueueConfirmFinished": { "expected_revision": number; "note": string; "action": "confirm_finished"; "task_id"?: null }
  "VideoQueueEvent": { "task_id": string; "session_id": (string | null); "media_id": (string | null); "video_queue": (ApiSchemas["VideoQueueInfo"] | null) }
  "VideoQueueInfo": { "state": "waiting" | "rate_limited" | "blocked"; "next_retry_at": (string | null) }
  "VideoQueueKind": "media" | "action" | "desktop"
  "VideoQueueLaneView": { "id": string; "provider": string; "provider_names": Array<string>; "next_retry_at": (string | null); "requests": Array<ApiSchemas["VideoQueueRequestView"]> }
  "VideoQueueList": { "queues": Array<ApiSchemas["VideoQueueLaneView"]> }
  "VideoQueueRequestView": { "id": string; "revision": number; "kind": ApiSchemas["VideoQueueKind"]; "entity_id": (number | null); "user_id": (number | null); "model": string; "state": string; "provider_task_id": (string | null); "created_at": string; "updated_at": string; "error_message": (string | null) }
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
  "VoiceUpdatedEvent": { "voice_id": string; "version": number }
  "VoicesListResponse": { "providers": Array<string>; "voices": Array<ApiSchemas["VoiceEntry"]>; "supports_voice_design": boolean; "voice_design_guide": string; "supports_voice_prompt_generation": boolean; "supports_voice_clone": boolean; "custom_voices": Array<ApiSchemas["CustomVoiceSummary"]> }
  "XhsLoginQrCode": { "is_logged_in": boolean; "image": (string | null); "expires_at": (string | null); "session_id": (string | null) }
  "XhsLoginVerification": { "session_id": string; "code": string }
  "XiaohongshuAdminLoginStatus": { "is_logged_in": boolean; "username": (string | null); "user_id": (string | null); "session_id": (string | null); "state": ("awaiting_scan" | "sms_required" | "verifying" | "verification_required" | "logged_in" | "expired" | null); "expires_at": (string | null); "verification_error": ("invalid_code" | null); "configured": boolean }
}

export interface ApiOperations {
  "GET /health": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: { [key: string]: string } }
  "GET /api/admin/video-queue": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["VideoQueueList"] }
  "POST /api/admin/video-queue/{request_id}/resolve": { path: { "request_id": string }; query: Record<string, never>; body: (ApiSchemas["VideoQueueAttachTask"] | ApiSchemas["VideoQueueConfirmFinished"]); response: ApiSchemas["VideoQueueRequestView"] }
  "POST /api/admin/ai/models/discover": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ModelDiscoveryRequest"]; response: ApiSchemas["ModelDiscoveryResponse"] }
  "GET /api/admin/users": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["UserListResponse"] }
  "POST /api/admin/users": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["UserCreate"]; response: ApiSchemas["UserResponse"] }
  "PATCH /api/admin/users/{user_id}": { path: { "user_id": number }; query: Record<string, never>; body: ApiSchemas["UserUpdate"]; response: ApiSchemas["UserResponse"] }
  "DELETE /api/admin/users/{user_id}": { path: { "user_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["MessageResponse"] }
  "POST /api/admin/users/{user_id}/activation": { path: { "user_id": number }; query: Record<string, never>; body: ApiSchemas["RegenerateActivationRequest"]; response: ApiSchemas["UserResponse"] }
  "GET /api/admin/model-configs": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["UserModelConfigListResponse"] }
  "GET /api/admin/runtime-info": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: { [key: string]: string } }
  "GET /api/admin/system-settings": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: { [key: string]: ApiSchemas["JsonValue"] } }
  "PUT /api/admin/system-settings": { path: Record<string, never>; query: Record<string, never>; body: { [key: string]: ApiSchemas["JsonValueInput"] }; response: { [key: string]: ApiSchemas["JsonValue"] } }
  "GET /api/admin/nightly-activity-logs": { path: Record<string, never>; query: { "user_id"?: (number | null); "target_date"?: (string | null) }; body: undefined; response: ApiSchemas["NightlyActivityLogListResponse"] }
  "GET /api/admin/social/xiaohongshu/login/status": { path: Record<string, never>; query: { "session_id"?: (string | null) }; body: undefined; response: ApiSchemas["XiaohongshuAdminLoginStatus"] }
  "POST /api/admin/social/xiaohongshu/login/qrcode": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["XhsLoginQrCode"] }
  "POST /api/admin/social/xiaohongshu/login/verification": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["XhsLoginVerification"]; response: ApiSchemas["XiaohongshuAdminLoginStatus"] }
  "DELETE /api/admin/social/xiaohongshu/login": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["MessageResponse"] }
  "PUT /api/admin/users/{user_id}/model-config": { path: { "user_id": number }; query: Record<string, never>; body: ApiSchemas["UserModelConfigRequest"]; response: ApiSchemas["MessageResponse"] }
  "DELETE /api/admin/users/{user_id}/model-config": { path: { "user_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["MessageResponse"] }
  "GET /api/admin/users/{user_id}/export": { path: { "user_id": number }; query: Record<string, never>; body: undefined; response: unknown }
  "POST /api/admin/users/{user_id}/import": { path: { "user_id": number }; query: { "mode"?: "overwrite" | "merge"; "sections"?: (Array<string> | null) }; body: FormData; response: ApiSchemas["UserBackupImportResponse"] }
  "POST /api/gateway/ticket": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["GatewayTicketResponse"] }
  "GET /api/companion/media-reviews": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: Array<ApiSchemas["MediaReviewResponse"]> }
  "GET /api/companion/media-reviews/{review_id}": { path: { "review_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["MediaReviewResponse"] }
  "POST /api/companion/media-reviews/{review_id}/decision": { path: { "review_id": number }; query: Record<string, never>; body: ApiSchemas["ResourceDecision"]; response: ApiSchemas["MediaReviewResponse"] }
  "GET /api/companion/character-card": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: (ApiSchemas["CharacterCardResponse"] | null) }
  "PATCH /api/companion/character-card": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["CharacterCardUpdate"]; response: ApiSchemas["CharacterCardResponse"] }
  "POST /api/companion/character-card/extract": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["CharacterCardExtract"]; response: ApiSchemas["CharacterCardResponse"] }
  "GET /api/companion/persona": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["PersonaResponse"] }
  "PUT /api/companion/persona": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["PersonaUpdate"]; response: ApiSchemas["PersonaResponse"] }
  "POST /api/companion/portrait/confirm": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["PortraitConfirmRequest"]; response: ApiSchemas["CompanionOperationResponse"] }
  "GET /api/companion/avatar": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["AvatarAssetResponse"] }
  "POST /api/companion/avatar": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["AvatarGenerateRequest"] | ApiSchemas["AvatarFromImageRequest"]); response: ApiSchemas["AvatarAssetResponse"] }
  "POST /api/companion/avatar/prompt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["AvatarPromptRequest"]; response: ApiSchemas["ImagePromptResponse"] }
  "POST /api/companion/avatar/adopt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["FullbodyAdoptRequest"]; response: ApiSchemas["AvatarAssetResponse"] }
  "GET /api/companion/avatar/history": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["AvatarHistoryResponse"] }
  "PUT /api/companion/avatar/{avatar_id}/select": { path: { "avatar_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["AvatarAssetResponse"] }
  "POST /api/companion/avatar/{avatar_id}/fullbody/reference": { path: { "avatar_id": number }; query: Record<string, never>; body: ApiSchemas["FullbodyReferenceGenerateRequest"]; response: (ApiSchemas["AvatarAssetResponse"] | ApiSchemas["FullbodyCandidateResponse"]) }
  "GET /api/companion/avatar/{avatar_id}/fullbody/candidate": { path: { "avatar_id": number }; query: Record<string, never>; body: undefined; response: (ApiSchemas["FullbodyCandidateResponse"] | null) }
  "POST /api/companion/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/analyze": { path: { "avatar_id": number; "candidate_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["FullbodyCandidateResponse"] }
  "POST /api/companion/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/accept": { path: { "avatar_id": number; "candidate_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["AvatarAssetResponse"] }
  "POST /api/companion/avatar/{avatar_id}/fullbody/confirm": { path: { "avatar_id": number }; query: Record<string, never>; body: ApiSchemas["FullbodyConfirmRequest"]; response: ApiSchemas["AvatarAssetResponse"] }
  "POST /api/companion/avatar/{avatar_id}/fullbody/reference/prompt": { path: { "avatar_id": number }; query: Record<string, never>; body: ApiSchemas["FullbodyPromptRequest"]; response: ApiSchemas["ImagePromptResponse"] }
  "POST /api/companion/avatar/{avatar_id}/fullbody/reference/adopt": { path: { "avatar_id": number }; query: Record<string, never>; body: ApiSchemas["FullbodyAdoptRequest"]; response: (ApiSchemas["AvatarAssetResponse"] | ApiSchemas["FullbodyCandidateResponse"]) }
  "GET /api/companion/outfits": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["OutfitListResponse"] }
  "POST /api/companion/outfits": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["OutfitCreateRequest"] | ApiSchemas["OutfitAdoptRequest"]); response: ApiSchemas["OutfitResponse"] }
  "PATCH /api/companion/outfits/policy": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["OutfitPolicyRequest"]; response: ApiSchemas["OutfitPolicyResponse"] }
  "POST /api/companion/outfits/prompt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["OutfitPromptRequest"]; response: ApiSchemas["ImagePromptResponse"] }
  "POST /api/companion/outfits/{outfit_id}/image": { path: { "outfit_id": number }; query: Record<string, never>; body: (ApiSchemas["OutfitRegenerateRequest"] | ApiSchemas["OutfitImageImportRequest"]); response: ApiSchemas["OutfitResponse"] }
  "POST /api/companion/outfits/{outfit_id}/confirm": { path: { "outfit_id": number }; query: Record<string, never>; body: ApiSchemas["OutfitConfirmRequest"]; response: ApiSchemas["OutfitResponse"] }
  "POST /api/companion/outfits/{outfit_id}/description/retry": { path: { "outfit_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["OutfitResponse"] }
  "DELETE /api/companion/outfits/{outfit_id}": { path: { "outfit_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["CompanionOperationResponse"] }
  "GET /api/companion/video-packs": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["VideoPackListResponse"] }
  "POST /api/companion/video-packs": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["VideoPackCreateRequest"] | ApiSchemas["VideoPackGenerateRequest"]); response: ApiSchemas["VideoPackResponse"] }
  "POST /api/companion/video-packs/{pack_id}/production": { path: { "pack_id": number }; query: Record<string, never>; body: (ApiSchemas["VideoPackEnsureRequest"] | ApiSchemas["VideoPackRetryRequest"]); response: ApiSchemas["VideoPackResponse"] }
  "PUT /api/companion/video-packs/current": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["VideoPackSelectionRequest"]; response: ApiSchemas["VideoPackResponse"] }
  "DELETE /api/companion/video-packs/{pack_id}": { path: { "pack_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["CompanionOperationResponse"] }
  "GET /api/companion/asset/{user_id}/{asset_path}": { path: { "user_id": number; "asset_path": string }; query: { "expires"?: (number | null); "sig"?: (string | null) }; body: undefined; response: unknown }
  "GET /api/companion/actions/catalog": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["ActionCatalogResponse"] }
  "POST /api/companion/actions/design": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ActionDesignRequest"]; response: ApiSchemas["ActionDesignResult"] }
  "POST /api/companion/actions/playback/{play_id}/receipt": { path: { "play_id": string }; query: Record<string, never>; body: ApiSchemas["ActionPlaybackReceipt"]; response: ApiSchemas["CompanionOperationResponse"] }
  "GET /api/companion/actions/budget": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["ActionBudgetStatus"] }
  "PATCH /api/companion/actions/{action_id}": { path: { "action_id": number }; query: Record<string, never>; body: ApiSchemas["ActionUpdateRequest"]; response: { [key: string]: boolean } }
  "DELETE /api/companion/actions/{action_id}": { path: { "action_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["CompanionOperationResponse"] }
  "GET /api/companion/desktop-videos/state": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["DesktopVideoStateResponse"] }
  "GET /api/companion/desktop-videos/sets": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["DesktopVideoListResponse"] }
  "POST /api/companion/desktop-videos/sets": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopVideoCreateSetRequest"]; response: ApiSchemas["DesktopVideoSetResponse"] }
  "GET /api/companion/desktop-videos/actions/{action_id}": { path: { "action_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["DesktopVideoActionResponse"] }
  "POST /api/companion/desktop-videos/ensure-current": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["DesktopVideoEnsureRequest"] | null); response: ApiSchemas["DesktopVideoStateResponse"] }
  "POST /api/companion/desktop-videos/actions/{action_id}/refine-video/preview": { path: { "action_id": number }; query: Record<string, never>; body: ApiSchemas["DesktopVideoRefineRequest"]; response: ApiSchemas["DesktopVideoRefinePreviewResponse"] }
  "POST /api/companion/desktop-videos/actions/{action_id}/external-prompt": { path: { "action_id": number }; query: Record<string, never>; body: (ApiSchemas["DesktopVideoExternalPromptRequest"] | null); response: ApiSchemas["DesktopVideoExternalPromptResponse"] }
  "POST /api/companion/desktop-videos/actions/{action_id}/upload": { path: { "action_id": number }; query: Record<string, never>; body: FormData; response: ApiSchemas["DesktopVideoActionResponse"] }
  "POST /api/companion/desktop-videos/design": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopVideoDesignRequest"]; response: ApiSchemas["DesktopVideoProposalResponse"] }
  "POST /api/companion/desktop-videos/play": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopVideoPlayRequest"]; response: ApiSchemas["DesktopVideoPlayCommand"] }
  "PUT /api/companion/desktop-videos/preferences": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopVideoPreferences"]; response: ApiSchemas["DesktopVideoStateResponse"] }
  "POST /api/companion/desktop-videos/plays/{play_id}/claim": { path: { "play_id": string }; query: Record<string, never>; body: ApiSchemas["DesktopVideoClaimRequest"]; response: { [key: string]: boolean } }
  "POST /api/companion/desktop-videos/plays/{play_id}/receipt": { path: { "play_id": string }; query: Record<string, never>; body: ApiSchemas["DesktopVideoReceipt"]; response: ApiSchemas["CompanionOperationResponse"] }
  "PATCH /api/companion/desktop-videos/sets/{set_id}": { path: { "set_id": number }; query: Record<string, never>; body: ApiSchemas["DesktopVideoRenameSetRequest"]; response: ApiSchemas["DesktopVideoSetResponse"] }
  "DELETE /api/companion/desktop-videos/sets/{set_id}": { path: { "set_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["DesktopVideoStateResponse"] }
  "POST /api/companion/desktop-videos/actions/{action_id}/image-upload": { path: { "action_id": number }; query: Record<string, never>; body: FormData; response: ApiSchemas["DesktopVideoActionResponse"] }
  "POST /api/companion/desktop-videos/actions/{action_id}/draft/commands": { path: { "action_id": number }; query: Record<string, never>; body: (ApiSchemas["DesktopVideoGenerateCommand"] | ApiSchemas["DesktopVideoImageCommand"] | ApiSchemas["DesktopVideoRefineCommand"] | ApiSchemas["DesktopVideoConfirmImageCommand"] | ApiSchemas["DesktopVideoCancelCommand"] | ApiSchemas["DesktopVideoPauseCommand"] | ApiSchemas["DesktopVideoResumeCommand"]); response: ApiSchemas["DesktopVideoActionResponse"] }
  "POST /api/companion/desktop-videos/actions/{action_id}/draft/decision": { path: { "action_id": number }; query: Record<string, never>; body: ApiSchemas["DesktopVideoDecisionRequest"]; response: ApiSchemas["DesktopVideoActionResponse"] }
  "PUT /api/companion/desktop-videos/current": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopVideoSelectionRequest"]; response: ApiSchemas["DesktopVideoStateResponse"] }
  "GET /api/companion/diary": { path: Record<string, never>; query: { "from"?: (string | null); "to"?: (string | null); "limit"?: number }; body: undefined; response: ApiSchemas["DiaryListResponse"] }
  "GET /api/companion/diary/read-state": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["DiaryUnreadResponse"] }
  "PATCH /api/companion/diary/read-state": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DiaryReadRequest"]; response: ApiSchemas["DiaryUnreadResponse"] }
  "GET /api/companion/posts": { path: Record<string, never>; query: { "cursor"?: (string | null); "limit"?: number }; body: undefined; response: ApiSchemas["PostListResponse"] }
  "GET /api/companion/posts/read-state": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["PostUnreadResponse"] }
  "PATCH /api/companion/posts/read-state": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["PostReadRequest"]; response: ApiSchemas["PostUnreadResponse"] }
  "GET /api/companion/posts/publications": { path: Record<string, never>; query: { "state": "recoverable"; "limit"?: number; "offset"?: number }; body: undefined; response: ApiSchemas["PostPublicationRecoveryList"] }
  "GET /api/companion/posts/publications/{publication_id}": { path: { "publication_id": string }; query: Record<string, never>; body: undefined; response: ApiSchemas["PostPublicationRecovery"] }
  "POST /api/companion/posts/publications/{publication_id}/query": { path: { "publication_id": string }; query: Record<string, never>; body: undefined; response: ApiSchemas["PostPublicationRecovery"] }
  "POST /api/companion/posts/publications/{publication_id}/decision": { path: { "publication_id": string }; query: Record<string, never>; body: ApiSchemas["ResourceDecision"]; response: ApiSchemas["PostPublicationResult"] }
  "GET /api/companion/posts/{post_id}": { path: { "post_id": string }; query: Record<string, never>; body: undefined; response: ApiSchemas["PostResponse"] }
  "POST /api/companion/posts/{post_id}/comments": { path: { "post_id": string }; query: Record<string, never>; body: ApiSchemas["PostCommentCreateRequest"]; response: ApiSchemas["PostCommentResponse"] }
  "DELETE /api/companion/posts/{post_id}/comments/{comment_id}": { path: { "post_id": string; "comment_id": string }; query: Record<string, never>; body: undefined; response: undefined }
  "POST /api/companion/posts/{post_id}/comments/{comment_id}/reply/retry": { path: { "post_id": string; "comment_id": string }; query: Record<string, never>; body: undefined; response: ApiSchemas["PostCommentResponse"] }
  "GET /api/companion/scenes/state": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["SceneStateResponse"] }
  "POST /api/companion/scenes": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["SceneGenerateRequest"] | ApiSchemas["SceneImportRequest"]); response: ApiSchemas["SceneResponse"] }
  "GET /api/companion/scenes": { path: Record<string, never>; query: { "q"?: string; "offset"?: number; "limit"?: number }; body: undefined; response: ApiSchemas["SceneListResponse"] }
  "POST /api/companion/scenes/prompt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ScenePromptRequest"]; response: ApiSchemas["SceneResponse"] }
  "POST /api/companion/scenes/{scene_id}/discard": { path: { "scene_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["SceneResponse"] }
  "POST /api/companion/scenes/{scene_id}/regenerate": { path: { "scene_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["SceneResponse"] }
  "DELETE /api/companion/scenes/{scene_id}": { path: { "scene_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["CompanionOperationResponse"] }
  "GET /api/companion/scenes/{scene_id}": { path: { "scene_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["SceneResponse"] }
  "PATCH /api/companion/scenes/{scene_id}": { path: { "scene_id": number }; query: Record<string, never>; body: ApiSchemas["SceneDescriptionRequest"]; response: ApiSchemas["SceneResponse"] }
  "PUT /api/companion/scenes/current": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["SceneActivateRequest"]; response: ApiSchemas["SceneResponse"] }
  "PATCH /api/companion/scenes/policy": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ScenePolicyRequest"]; response: ApiSchemas["ScenePolicyResponse"] }
  "POST /api/companion/scenes/{scene_id}/analyze": { path: { "scene_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["SceneResponse"] }
  "GET /api/companion/topics/feedback": { path: Record<string, never>; query: { "topic_ids"?: Array<number> }; body: undefined; response: Array<ApiSchemas["TopicFeedbackSnapshot"]> }
  "PUT /api/companion/topics/{topic_id}/feedback": { path: { "topic_id": number }; query: Record<string, never>; body: ApiSchemas["TopicFeedbackRequest"]; response: ApiSchemas["TopicFeedbackSnapshot"] }
  "GET /api/presets": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["PromptPresetListResponse"] }
  "GET /api/commands": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["CommandListResponse"] }
  "GET /api/memories": { path: Record<string, never>; query: { "system_preset_id": string; "kind"?: (string | null); "status"?: "active" | "candidate" | "invalidated" | "expired"; "tag"?: (string | null); "q"?: (string | null); "limit"?: number; "offset"?: number }; body: undefined; response: ApiSchemas["MemoryListResponse"] }
  "PATCH /api/memories/profile": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["MemoryProfileRequest"]; response: ApiSchemas["MemoryProfileResponse"] }
  "PATCH /api/memories/{memory_id}": { path: { "memory_id": number }; query: Record<string, never>; body: ApiSchemas["MemoryUpdateRequest"]; response: ApiSchemas["MemoryListItem"] }
  "DELETE /api/memories/{memory_id}": { path: { "memory_id": number }; query: { "system_preset_id": string }; body: undefined; response: ApiSchemas["MemoryDeleteResponse"] }
  "GET /api/voices": { path: Record<string, never>; query: { "language"?: (string | null) }; body: undefined; response: ApiSchemas["VoicesListResponse"] }
  "POST /api/voices/match": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["VoiceMatchRequest"]; response: ApiSchemas["VoiceMatchResponse"] }
  "POST /api/voices/candidates": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["VoiceDesignRequest"] | ApiSchemas["VoiceCloneRequest"]); response: ApiSchemas["VoicePreviewResponse"] }
  "POST /api/voices/prompt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["VoicePromptRequest"]; response: ApiSchemas["VoicePromptResponse"] }
  "POST /api/voices/candidates/{candidate_id}/confirmation": { path: { "candidate_id": string }; query: Record<string, never>; body: ApiSchemas["VoiceConfirmRequest"]; response: ApiSchemas["VoiceConfirmResponse"] }
  "DELETE /api/voices/operations/{operation_id}": { path: { "operation_id": string }; query: Record<string, never>; body: undefined; response: ApiSchemas["VoiceCancelResponse"] }
  "GET /api/companion/onboarding": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["OnboardingStateResponse"] }
  "PATCH /api/companion/onboarding": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["OnboardingPatchRequest"]; response: ApiSchemas["OnboardingStateResponse"] }
  "GET /api/config": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["DesktopConfigResponse"] }
  "PUT /api/config": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DesktopConfigPutRequest"]; response: ApiSchemas["DesktopConfigResponse"] }
  "POST /api/media/voice-samples": { path: Record<string, never>; query: Record<string, never>; body: FormData; response: ApiSchemas["VoiceSampleResponse"] }
  "GET /api/media/files/{file_id}": { path: { "file_id": string }; query: Record<string, never>; body: undefined; response: unknown }
  "GET /api/media/videos/{session_id}/{file_id}": { path: { "session_id": string; "file_id": string }; query: Record<string, never>; body: undefined; response: unknown }
  "GET /api/media/audios/{session_id}/{file_id}": { path: { "session_id": string; "file_id": string }; query: Record<string, never>; body: undefined; response: unknown }
  "POST /api/media/audios": { path: Record<string, never>; query: Record<string, never>; body: FormData; response: ApiSchemas["ChatAudioUploadResponse"] }
  "POST /api/media/videos": { path: Record<string, never>; query: Record<string, never>; body: FormData; response: ApiSchemas["ChatVideoUploadResponse"] }
  "POST /api/media/stt": { path: Record<string, never>; query: { "language"?: string }; body: FormData; response: ApiSchemas["SpeechToTextResponse"] }
  "POST /api/media/tts": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["TtsRequest"]; response: unknown }
  "POST /admin/login": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["AdminLoginRequest"]; response: ApiSchemas["AdminTokenResponse"] }
  "GET /admin/": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: unknown }
  "POST /api/remote/pairings": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["PairingResponse"] }
  "GET /api/remote/pairings/{pairing_id}": { path: { "pairing_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["PairingResponse"] }
  "DELETE /api/remote/pairings/{pairing_id}": { path: { "pairing_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["MessageResponse"] }
  "POST /api/remote/session/exchange": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ExchangeRequest"]; response: ApiSchemas["RemoteSessionResponse"] }
  "GET /api/remote/session": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["RemoteSessionResponse"] }
  "DELETE /api/remote/session": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["MessageResponse"] }
  "GET /api/remote/devices": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["DeviceListResponse"] }
  "DELETE /api/remote/devices": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["MessageResponse"] }
  "DELETE /api/remote/devices/{device_id}": { path: { "device_id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["MessageResponse"] }
  "POST /api/sessions/messages/{message_id}/voice/{bubble_index}": { path: { "message_id": number; "bubble_index": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["VoiceBubbleView"] }
  "GET /api/sessions": { path: Record<string, never>; query: { "limit"?: number; "offset"?: number; "min_messages"?: number; "archived"?: "only" | "exclude" | "include"; "order"?: "recent" | "created" | "messages"; "include_subagents"?: boolean; "q"?: (string | null) }; body: undefined; response: ApiSchemas["DesktopSessionListResponse"] }
  "POST /api/sessions": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["SessionCreateRequest"]; response: ApiSchemas["SessionCreateResult"] }
  "GET /api/sessions/main": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["SessionCreateResult"] }
  "GET /api/sessions/{session_id}": { path: { "session_id": string }; query: Record<string, never>; body: undefined; response: ApiSchemas["SessionCreateResult"] }
  "PATCH /api/sessions/{session_id}": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["DesktopSessionPatchRequest"]; response: ApiSchemas["SessionCreateResult"] }
  "DELETE /api/sessions/{session_id}": { path: { "session_id": string }; query: Record<string, never>; body: undefined; response: ApiSchemas["DesktopSessionOperationResponse"] }
  "POST /api/sessions/{session_id}/forks": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["SessionForkRequest"]; response: ApiSchemas["SessionCreateResult"] }
  "GET /api/sessions/{session_id}/history": { path: { "session_id": string }; query: { "before_id"?: (number | null); "limit"?: number }; body: undefined; response: ApiSchemas["SessionHistoryResponse"] }
  "GET /api/update/latest.yml": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["ReleaseManifestResponse"] }
  "GET /api/update/latest-mac.yml": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["ReleaseManifestResponse"] }
  "GET /api/update/latest-runner.yml": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: undefined }
  "GET /api/update/{filename}": { path: { "filename": string }; query: Record<string, never>; body: undefined; response: undefined }
  "GET /api/admin/updates/versions": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["UpdateVersionListResponse"] }
  "POST /api/admin/updates/versions": { path: Record<string, never>; query: Record<string, never>; body: FormData; response: ApiSchemas["UpdateVersionItem"] }
  "PATCH /api/admin/updates/versions/{id}": { path: { "id": number }; query: Record<string, never>; body: ApiSchemas["UpdateVersionUpdate"]; response: ApiSchemas["UpdateVersionItem"] }
  "DELETE /api/admin/updates/versions/{id}": { path: { "id": number }; query: Record<string, never>; body: undefined; response: ApiSchemas["MessageResponse"] }
  "POST /api/user/activate": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ActivateRequest"]; response: ApiSchemas["TokenResponse"] }
  "POST /api/user/refresh": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["RefreshRequest"]; response: ApiSchemas["TokenResponse"] }
  "POST /api/user/logout": { path: Record<string, never>; query: Record<string, never>; body: undefined; response: ApiSchemas["MessageResponse"] }
}

export interface RpcOperations {
  "command.dispatch": { request: ApiSchemas["RpcCommandDispatchRequest"]; response: ApiSchemas["RpcCommandDispatchResult"] }
  "companion.idle_expression": { request: ApiSchemas["RpcIdleExpressionRequest"]; response: ApiSchemas["RpcIdleExpressionResult"] }
  "companion.record_interaction_stats": { request: ApiSchemas["RpcInteractionStatsRequest"]; response: ApiSchemas["RpcInteractionStatsResult"] }
  "companion.should_act": { request: ApiSchemas["RpcShouldActRequest"]; response: ApiSchemas["RpcShouldActResult"] }
  "companion.signal": { request: ApiSchemas["RpcCompanionSignal"]; response: ApiSchemas["RpcCompanionSignalResult"] }
  "prompt.submit": { request: ApiSchemas["RpcPromptSubmitRequest"]; response: ApiSchemas["RpcPromptSubmitResult"] }
  "session.ack": { request: ApiSchemas["RpcSessionAckRequest"]; response: ApiSchemas["RpcSessionAckResult"] }
  "session.compress_context": { request: ApiSchemas["RpcSessionRequest"]; response: ApiSchemas["RpcSessionCompressionResult"] }
  "session.interrupt": { request: ApiSchemas["RpcSessionRequest"]; response: ApiSchemas["RpcEmptyResult"] }
  "session.ping": { request: ApiSchemas["RpcEmptyRequest"]; response: ApiSchemas["RpcEmptyResult"] }
  "session.resume": { request: ApiSchemas["RpcSessionResumeRequest"]; response: ApiSchemas["RpcGatewaySessionResumeResult"] }
  "session.undo_to_message": { request: ApiSchemas["RpcSessionUndoRequest"]; response: ApiSchemas["RpcSessionUndoResult"] }
  "tool.result": { request: ApiSchemas["RpcToolResultRequest"]; response: ApiSchemas["RpcEmptyResult"] }
  "tools.sync": { request: ApiSchemas["RpcToolsSyncRequest"]; response: ApiSchemas["RpcToolsSyncResult"] }
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
  "calls.cancel": { request: ApiSchemas["RunnerCallCancelRequest"]; response: ApiSchemas["RunnerRunnerCancelResult"] }
  "calls.get": { request: ApiSchemas["RunnerCallGetRequest"]; response: ApiSchemas["RunnerRunnerCallResult"] }
  "config.replace": { request: ApiSchemas["RunnerConfigReplaceRequest"]; response: ApiSchemas["RunnerRunnerConfigResult"] }
  "runner.info": { request: ApiSchemas["RunnerRunnerEmptyRequest"]; response: ApiSchemas["RunnerRunnerInfoResult"] }
  "tools.execute": { request: ApiSchemas["RunnerToolExecuteRequest"]; response: ApiSchemas["RunnerRunnerToolResult"] }
  "tools.list": { request: ApiSchemas["RunnerRunnerEmptyRequest"]; response: ApiSchemas["RunnerRunnerToolsResult"] }
}

export interface RunnerEventPayloads {
  "runner.capabilities_changed": ApiSchemas["RunnerRunnerCapabilitiesChangedPayload"]
  "runner.ready": ApiSchemas["RunnerRunnerReadyPayload"]
}

export const API_OPERATIONS = [
  {
    "key": "GET /health",
    "path": "/health",
    "method": "GET",
    "response": "json",
    "identities": [
      "public"
    ],
    "upload": "json",
    "wait_for_model": false
  },
  {
    "key": "GET /api/admin/video-queue",
    "path": "/api/admin/video-queue",
    "method": "GET",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/video-queue"
  },
  {
    "key": "POST /api/admin/video-queue/{request_id}/resolve",
    "path": "/api/admin/video-queue/{request_id}/resolve",
    "method": "POST",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/video-queue/{request_id}/resolve"
  },
  {
    "key": "POST /api/admin/ai/models/discover",
    "path": "/api/admin/ai/models/discover",
    "method": "POST",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/ai/models/discover"
  },
  {
    "key": "GET /api/admin/users",
    "path": "/api/admin/users",
    "method": "GET",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/users"
  },
  {
    "key": "POST /api/admin/users",
    "path": "/api/admin/users",
    "method": "POST",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/users"
  },
  {
    "key": "PATCH /api/admin/users/{user_id}",
    "path": "/api/admin/users/{user_id}",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/users/{user_id}"
  },
  {
    "key": "DELETE /api/admin/users/{user_id}",
    "path": "/api/admin/users/{user_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/users/{user_id}"
  },
  {
    "key": "POST /api/admin/users/{user_id}/activation",
    "path": "/api/admin/users/{user_id}/activation",
    "method": "POST",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/users/{user_id}/activation"
  },
  {
    "key": "GET /api/admin/model-configs",
    "path": "/api/admin/model-configs",
    "method": "GET",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/model-configs"
  },
  {
    "key": "GET /api/admin/runtime-info",
    "path": "/api/admin/runtime-info",
    "method": "GET",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/runtime-info"
  },
  {
    "key": "GET /api/admin/system-settings",
    "path": "/api/admin/system-settings",
    "method": "GET",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/system-settings"
  },
  {
    "key": "PUT /api/admin/system-settings",
    "path": "/api/admin/system-settings",
    "method": "PUT",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/system-settings"
  },
  {
    "key": "GET /api/admin/nightly-activity-logs",
    "path": "/api/admin/nightly-activity-logs",
    "method": "GET",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/nightly-activity-logs"
  },
  {
    "key": "GET /api/admin/social/xiaohongshu/login/status",
    "path": "/api/admin/social/xiaohongshu/login/status",
    "method": "GET",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/social/xiaohongshu/login/status"
  },
  {
    "key": "POST /api/admin/social/xiaohongshu/login/qrcode",
    "path": "/api/admin/social/xiaohongshu/login/qrcode",
    "method": "POST",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/social/xiaohongshu/login/qrcode"
  },
  {
    "key": "POST /api/admin/social/xiaohongshu/login/verification",
    "path": "/api/admin/social/xiaohongshu/login/verification",
    "method": "POST",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/social/xiaohongshu/login/verification"
  },
  {
    "key": "DELETE /api/admin/social/xiaohongshu/login",
    "path": "/api/admin/social/xiaohongshu/login",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/social/xiaohongshu/login"
  },
  {
    "key": "PUT /api/admin/users/{user_id}/model-config",
    "path": "/api/admin/users/{user_id}/model-config",
    "method": "PUT",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/users/{user_id}/model-config"
  },
  {
    "key": "DELETE /api/admin/users/{user_id}/model-config",
    "path": "/api/admin/users/{user_id}/model-config",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/users/{user_id}/model-config"
  },
  {
    "key": "GET /api/admin/users/{user_id}/export",
    "path": "/api/admin/users/{user_id}/export",
    "method": "GET",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/users/{user_id}/export"
  },
  {
    "key": "POST /api/admin/users/{user_id}/import",
    "path": "/api/admin/users/{user_id}/import",
    "method": "POST",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "backup",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/users/{user_id}/import"
  },
  {
    "key": "POST /api/gateway/ticket",
    "path": "/api/gateway/ticket",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/gateway/ticket"
  },
  {
    "key": "GET /api/companion/media-reviews",
    "path": "/api/companion/media-reviews",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/media-reviews"
  },
  {
    "key": "GET /api/companion/media-reviews/{review_id}",
    "path": "/api/companion/media-reviews/{review_id}",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/media-reviews/{review_id}"
  },
  {
    "key": "POST /api/companion/media-reviews/{review_id}/decision",
    "path": "/api/companion/media-reviews/{review_id}/decision",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/media-reviews/{review_id}/decision"
  },
  {
    "key": "GET /api/companion/character-card",
    "path": "/api/companion/character-card",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/character-card"
  },
  {
    "key": "PATCH /api/companion/character-card",
    "path": "/api/companion/character-card",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/character-card"
  },
  {
    "key": "POST /api/companion/character-card/extract",
    "path": "/api/companion/character-card/extract",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/character-card/extract"
  },
  {
    "key": "GET /api/companion/persona",
    "path": "/api/companion/persona",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/persona"
  },
  {
    "key": "PUT /api/companion/persona",
    "path": "/api/companion/persona",
    "method": "PUT",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/persona"
  },
  {
    "key": "POST /api/companion/portrait/confirm",
    "path": "/api/companion/portrait/confirm",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/portrait/confirm"
  },
  {
    "key": "GET /api/companion/avatar",
    "path": "/api/companion/avatar",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/avatar"
  },
  {
    "key": "POST /api/companion/avatar",
    "path": "/api/companion/avatar",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "image",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/avatar"
  },
  {
    "key": "POST /api/companion/avatar/prompt",
    "path": "/api/companion/avatar/prompt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/avatar/prompt"
  },
  {
    "key": "POST /api/companion/avatar/adopt",
    "path": "/api/companion/avatar/adopt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "image",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/avatar/adopt"
  },
  {
    "key": "GET /api/companion/avatar/history",
    "path": "/api/companion/avatar/history",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/avatar/history"
  },
  {
    "key": "PUT /api/companion/avatar/{avatar_id}/select",
    "path": "/api/companion/avatar/{avatar_id}/select",
    "method": "PUT",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/avatar/{avatar_id}/select"
  },
  {
    "key": "POST /api/companion/avatar/{avatar_id}/fullbody/reference",
    "path": "/api/companion/avatar/{avatar_id}/fullbody/reference",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "image",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/avatar/{avatar_id}/fullbody/reference"
  },
  {
    "key": "GET /api/companion/avatar/{avatar_id}/fullbody/candidate",
    "path": "/api/companion/avatar/{avatar_id}/fullbody/candidate",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/avatar/{avatar_id}/fullbody/candidate"
  },
  {
    "key": "POST /api/companion/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/analyze",
    "path": "/api/companion/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/analyze",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/analyze"
  },
  {
    "key": "POST /api/companion/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/accept",
    "path": "/api/companion/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/accept",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/avatar/{avatar_id}/fullbody/candidate/{candidate_id}/accept"
  },
  {
    "key": "POST /api/companion/avatar/{avatar_id}/fullbody/confirm",
    "path": "/api/companion/avatar/{avatar_id}/fullbody/confirm",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/avatar/{avatar_id}/fullbody/confirm"
  },
  {
    "key": "POST /api/companion/avatar/{avatar_id}/fullbody/reference/prompt",
    "path": "/api/companion/avatar/{avatar_id}/fullbody/reference/prompt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/avatar/{avatar_id}/fullbody/reference/prompt"
  },
  {
    "key": "POST /api/companion/avatar/{avatar_id}/fullbody/reference/adopt",
    "path": "/api/companion/avatar/{avatar_id}/fullbody/reference/adopt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "image",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/avatar/{avatar_id}/fullbody/reference/adopt"
  },
  {
    "key": "GET /api/companion/outfits",
    "path": "/api/companion/outfits",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/outfits"
  },
  {
    "key": "POST /api/companion/outfits",
    "path": "/api/companion/outfits",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "image",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/outfits"
  },
  {
    "key": "PATCH /api/companion/outfits/policy",
    "path": "/api/companion/outfits/policy",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/outfits/policy"
  },
  {
    "key": "POST /api/companion/outfits/prompt",
    "path": "/api/companion/outfits/prompt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "image",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/outfits/prompt"
  },
  {
    "key": "POST /api/companion/outfits/{outfit_id}/image",
    "path": "/api/companion/outfits/{outfit_id}/image",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "image",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/outfits/{outfit_id}/image"
  },
  {
    "key": "POST /api/companion/outfits/{outfit_id}/confirm",
    "path": "/api/companion/outfits/{outfit_id}/confirm",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/outfits/{outfit_id}/confirm"
  },
  {
    "key": "POST /api/companion/outfits/{outfit_id}/description/retry",
    "path": "/api/companion/outfits/{outfit_id}/description/retry",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/outfits/{outfit_id}/description/retry"
  },
  {
    "key": "DELETE /api/companion/outfits/{outfit_id}",
    "path": "/api/companion/outfits/{outfit_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/outfits/{outfit_id}"
  },
  {
    "key": "GET /api/companion/video-packs",
    "path": "/api/companion/video-packs",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/video-packs"
  },
  {
    "key": "POST /api/companion/video-packs",
    "path": "/api/companion/video-packs",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "action_pack",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/video-packs"
  },
  {
    "key": "POST /api/companion/video-packs/{pack_id}/production",
    "path": "/api/companion/video-packs/{pack_id}/production",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/video-packs/{pack_id}/production"
  },
  {
    "key": "PUT /api/companion/video-packs/current",
    "path": "/api/companion/video-packs/current",
    "method": "PUT",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/video-packs/current"
  },
  {
    "key": "DELETE /api/companion/video-packs/{pack_id}",
    "path": "/api/companion/video-packs/{pack_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/video-packs/{pack_id}"
  },
  {
    "key": "GET /api/companion/asset/{user_id}/{asset_path}",
    "path": "/api/companion/asset/{user_id}/{asset_path}",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote",
      "public"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/asset/{user_id}/{asset_path:path}"
  },
  {
    "key": "GET /api/companion/actions/catalog",
    "path": "/api/companion/actions/catalog",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/actions/catalog"
  },
  {
    "key": "POST /api/companion/actions/design",
    "path": "/api/companion/actions/design",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/actions/design"
  },
  {
    "key": "POST /api/companion/actions/playback/{play_id}/receipt",
    "path": "/api/companion/actions/playback/{play_id}/receipt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/actions/playback/{play_id}/receipt"
  },
  {
    "key": "GET /api/companion/actions/budget",
    "path": "/api/companion/actions/budget",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/actions/budget"
  },
  {
    "key": "PATCH /api/companion/actions/{action_id}",
    "path": "/api/companion/actions/{action_id}",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/actions/{action_id}"
  },
  {
    "key": "DELETE /api/companion/actions/{action_id}",
    "path": "/api/companion/actions/{action_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/actions/{action_id}"
  },
  {
    "key": "GET /api/companion/desktop-videos/state",
    "path": "/api/companion/desktop-videos/state",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/state"
  },
  {
    "key": "GET /api/companion/desktop-videos/sets",
    "path": "/api/companion/desktop-videos/sets",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/sets"
  },
  {
    "key": "POST /api/companion/desktop-videos/sets",
    "path": "/api/companion/desktop-videos/sets",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/sets"
  },
  {
    "key": "GET /api/companion/desktop-videos/actions/{action_id}",
    "path": "/api/companion/desktop-videos/actions/{action_id}",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/actions/{action_id}"
  },
  {
    "key": "POST /api/companion/desktop-videos/ensure-current",
    "path": "/api/companion/desktop-videos/ensure-current",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/ensure-current"
  },
  {
    "key": "POST /api/companion/desktop-videos/actions/{action_id}/refine-video/preview",
    "path": "/api/companion/desktop-videos/actions/{action_id}/refine-video/preview",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/actions/{action_id}/refine-video/preview"
  },
  {
    "key": "POST /api/companion/desktop-videos/actions/{action_id}/external-prompt",
    "path": "/api/companion/desktop-videos/actions/{action_id}/external-prompt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/actions/{action_id}/external-prompt"
  },
  {
    "key": "POST /api/companion/desktop-videos/actions/{action_id}/upload",
    "path": "/api/companion/desktop-videos/actions/{action_id}/upload",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "video",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/actions/{action_id}/upload"
  },
  {
    "key": "POST /api/companion/desktop-videos/design",
    "path": "/api/companion/desktop-videos/design",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/design"
  },
  {
    "key": "POST /api/companion/desktop-videos/play",
    "path": "/api/companion/desktop-videos/play",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/play"
  },
  {
    "key": "PUT /api/companion/desktop-videos/preferences",
    "path": "/api/companion/desktop-videos/preferences",
    "method": "PUT",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/preferences"
  },
  {
    "key": "POST /api/companion/desktop-videos/plays/{play_id}/claim",
    "path": "/api/companion/desktop-videos/plays/{play_id}/claim",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/plays/{play_id}/claim"
  },
  {
    "key": "POST /api/companion/desktop-videos/plays/{play_id}/receipt",
    "path": "/api/companion/desktop-videos/plays/{play_id}/receipt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/plays/{play_id}/receipt"
  },
  {
    "key": "PATCH /api/companion/desktop-videos/sets/{set_id}",
    "path": "/api/companion/desktop-videos/sets/{set_id}",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/sets/{set_id}"
  },
  {
    "key": "DELETE /api/companion/desktop-videos/sets/{set_id}",
    "path": "/api/companion/desktop-videos/sets/{set_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/sets/{set_id}"
  },
  {
    "key": "POST /api/companion/desktop-videos/actions/{action_id}/image-upload",
    "path": "/api/companion/desktop-videos/actions/{action_id}/image-upload",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "image_file",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/actions/{action_id}/image-upload"
  },
  {
    "key": "POST /api/companion/desktop-videos/actions/{action_id}/draft/commands",
    "path": "/api/companion/desktop-videos/actions/{action_id}/draft/commands",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/actions/{action_id}/draft/commands"
  },
  {
    "key": "POST /api/companion/desktop-videos/actions/{action_id}/draft/decision",
    "path": "/api/companion/desktop-videos/actions/{action_id}/draft/decision",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/actions/{action_id}/draft/decision"
  },
  {
    "key": "PUT /api/companion/desktop-videos/current",
    "path": "/api/companion/desktop-videos/current",
    "method": "PUT",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/desktop-videos/current"
  },
  {
    "key": "GET /api/companion/diary",
    "path": "/api/companion/diary",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/diary"
  },
  {
    "key": "GET /api/companion/diary/read-state",
    "path": "/api/companion/diary/read-state",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/diary/read-state"
  },
  {
    "key": "PATCH /api/companion/diary/read-state",
    "path": "/api/companion/diary/read-state",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/diary/read-state"
  },
  {
    "key": "GET /api/companion/posts",
    "path": "/api/companion/posts",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/posts"
  },
  {
    "key": "GET /api/companion/posts/read-state",
    "path": "/api/companion/posts/read-state",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/posts/read-state"
  },
  {
    "key": "PATCH /api/companion/posts/read-state",
    "path": "/api/companion/posts/read-state",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/posts/read-state"
  },
  {
    "key": "GET /api/companion/posts/publications",
    "path": "/api/companion/posts/publications",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/posts/publications"
  },
  {
    "key": "GET /api/companion/posts/publications/{publication_id}",
    "path": "/api/companion/posts/publications/{publication_id}",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/posts/publications/{publication_id}"
  },
  {
    "key": "POST /api/companion/posts/publications/{publication_id}/query",
    "path": "/api/companion/posts/publications/{publication_id}/query",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/companion/posts/publications/{publication_id}/query"
  },
  {
    "key": "POST /api/companion/posts/publications/{publication_id}/decision",
    "path": "/api/companion/posts/publications/{publication_id}/decision",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/posts/publications/{publication_id}/decision"
  },
  {
    "key": "GET /api/companion/posts/{post_id}",
    "path": "/api/companion/posts/{post_id}",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/posts/{post_id}"
  },
  {
    "key": "POST /api/companion/posts/{post_id}/comments",
    "path": "/api/companion/posts/{post_id}/comments",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/posts/{post_id}/comments"
  },
  {
    "key": "DELETE /api/companion/posts/{post_id}/comments/{comment_id}",
    "path": "/api/companion/posts/{post_id}/comments/{comment_id}",
    "method": "DELETE",
    "response": "empty",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/posts/{post_id}/comments/{comment_id}"
  },
  {
    "key": "POST /api/companion/posts/{post_id}/comments/{comment_id}/reply/retry",
    "path": "/api/companion/posts/{post_id}/comments/{comment_id}/reply/retry",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/posts/{post_id}/comments/{comment_id}/reply/retry"
  },
  {
    "key": "GET /api/companion/scenes/state",
    "path": "/api/companion/scenes/state",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes/state"
  },
  {
    "key": "POST /api/companion/scenes",
    "path": "/api/companion/scenes",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "image",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes"
  },
  {
    "key": "GET /api/companion/scenes",
    "path": "/api/companion/scenes",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes"
  },
  {
    "key": "POST /api/companion/scenes/prompt",
    "path": "/api/companion/scenes/prompt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes/prompt"
  },
  {
    "key": "POST /api/companion/scenes/{scene_id}/discard",
    "path": "/api/companion/scenes/{scene_id}/discard",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes/{scene_id}/discard"
  },
  {
    "key": "POST /api/companion/scenes/{scene_id}/regenerate",
    "path": "/api/companion/scenes/{scene_id}/regenerate",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes/{scene_id}/regenerate"
  },
  {
    "key": "DELETE /api/companion/scenes/{scene_id}",
    "path": "/api/companion/scenes/{scene_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes/{scene_id}"
  },
  {
    "key": "GET /api/companion/scenes/{scene_id}",
    "path": "/api/companion/scenes/{scene_id}",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes/{scene_id}"
  },
  {
    "key": "PATCH /api/companion/scenes/{scene_id}",
    "path": "/api/companion/scenes/{scene_id}",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes/{scene_id}"
  },
  {
    "key": "PUT /api/companion/scenes/current",
    "path": "/api/companion/scenes/current",
    "method": "PUT",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes/current"
  },
  {
    "key": "PATCH /api/companion/scenes/policy",
    "path": "/api/companion/scenes/policy",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes/policy"
  },
  {
    "key": "POST /api/companion/scenes/{scene_id}/analyze",
    "path": "/api/companion/scenes/{scene_id}/analyze",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/scenes/{scene_id}/analyze"
  },
  {
    "key": "GET /api/companion/topics/feedback",
    "path": "/api/companion/topics/feedback",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/topics/feedback"
  },
  {
    "key": "PUT /api/companion/topics/{topic_id}/feedback",
    "path": "/api/companion/topics/{topic_id}/feedback",
    "method": "PUT",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/topics/{topic_id}/feedback"
  },
  {
    "key": "GET /api/presets",
    "path": "/api/presets",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/presets"
  },
  {
    "key": "GET /api/commands",
    "path": "/api/commands",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/commands"
  },
  {
    "key": "GET /api/memories",
    "path": "/api/memories",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/memories"
  },
  {
    "key": "PATCH /api/memories/profile",
    "path": "/api/memories/profile",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/memories/profile"
  },
  {
    "key": "PATCH /api/memories/{memory_id}",
    "path": "/api/memories/{memory_id}",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/memories/{memory_id}"
  },
  {
    "key": "DELETE /api/memories/{memory_id}",
    "path": "/api/memories/{memory_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/memories/{memory_id}"
  },
  {
    "key": "GET /api/voices",
    "path": "/api/voices",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/voices"
  },
  {
    "key": "POST /api/voices/match",
    "path": "/api/voices/match",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/voices/match"
  },
  {
    "key": "POST /api/voices/candidates",
    "path": "/api/voices/candidates",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/voices/candidates"
  },
  {
    "key": "POST /api/voices/prompt",
    "path": "/api/voices/prompt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/voices/prompt"
  },
  {
    "key": "POST /api/voices/candidates/{candidate_id}/confirmation",
    "path": "/api/voices/candidates/{candidate_id}/confirmation",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/voices/candidates/{candidate_id}/confirmation"
  },
  {
    "key": "DELETE /api/voices/operations/{operation_id}",
    "path": "/api/voices/operations/{operation_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/voices/operations/{operation_id}"
  },
  {
    "key": "GET /api/companion/onboarding",
    "path": "/api/companion/onboarding",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/onboarding"
  },
  {
    "key": "PATCH /api/companion/onboarding",
    "path": "/api/companion/onboarding",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/companion/onboarding"
  },
  {
    "key": "GET /api/config",
    "path": "/api/config",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/config"
  },
  {
    "key": "PUT /api/config",
    "path": "/api/config",
    "method": "PUT",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/config"
  },
  {
    "key": "POST /api/media/voice-samples",
    "path": "/api/media/voice-samples",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "audio",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/media/voice-samples"
  },
  {
    "key": "GET /api/media/files/{file_id}",
    "path": "/api/media/files/{file_id}",
    "method": "GET",
    "response": "json",
    "identities": [
      "public"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/media/files/{file_id}"
  },
  {
    "key": "GET /api/media/videos/{session_id}/{file_id}",
    "path": "/api/media/videos/{session_id}/{file_id}",
    "method": "GET",
    "response": "json",
    "identities": [
      "public"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/media/videos/{session_id}/{file_id}"
  },
  {
    "key": "GET /api/media/audios/{session_id}/{file_id}",
    "path": "/api/media/audios/{session_id}/{file_id}",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/media/audios/{session_id}/{file_id}"
  },
  {
    "key": "POST /api/media/audios",
    "path": "/api/media/audios",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "audio",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/media/audios"
  },
  {
    "key": "POST /api/media/videos",
    "path": "/api/media/videos",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "video",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/media/videos"
  },
  {
    "key": "POST /api/media/stt",
    "path": "/api/media/stt",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "audio",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/media/stt"
  },
  {
    "key": "POST /api/media/tts",
    "path": "/api/media/tts",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/media/tts"
  },
  {
    "key": "POST /admin/login",
    "path": "/admin/login",
    "method": "POST",
    "response": "json",
    "identities": [
      "public"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/admin/login"
  },
  {
    "key": "GET /admin/",
    "path": "/admin/",
    "method": "GET",
    "response": "json",
    "identities": [
      "public"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/admin/"
  },
  {
    "key": "POST /api/remote/pairings",
    "path": "/api/remote/pairings",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/remote/pairings"
  },
  {
    "key": "GET /api/remote/pairings/{pairing_id}",
    "path": "/api/remote/pairings/{pairing_id}",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/remote/pairings/{pairing_id}"
  },
  {
    "key": "DELETE /api/remote/pairings/{pairing_id}",
    "path": "/api/remote/pairings/{pairing_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/remote/pairings/{pairing_id}"
  },
  {
    "key": "POST /api/remote/session/exchange",
    "path": "/api/remote/session/exchange",
    "method": "POST",
    "response": "json",
    "identities": [
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/remote/session/exchange"
  },
  {
    "key": "GET /api/remote/session",
    "path": "/api/remote/session",
    "method": "GET",
    "response": "json",
    "identities": [
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/remote/session"
  },
  {
    "key": "DELETE /api/remote/session",
    "path": "/api/remote/session",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/remote/session"
  },
  {
    "key": "GET /api/remote/devices",
    "path": "/api/remote/devices",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/remote/devices"
  },
  {
    "key": "DELETE /api/remote/devices",
    "path": "/api/remote/devices",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/remote/devices"
  },
  {
    "key": "DELETE /api/remote/devices/{device_id}",
    "path": "/api/remote/devices/{device_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/remote/devices/{device_id}"
  },
  {
    "key": "POST /api/sessions/messages/{message_id}/voice/{bubble_index}",
    "path": "/api/sessions/messages/{message_id}/voice/{bubble_index}",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": true,
    "ipc": true,
    "route_path": "/api/sessions/messages/{message_id}/voice/{bubble_index}"
  },
  {
    "key": "GET /api/sessions",
    "path": "/api/sessions",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/sessions"
  },
  {
    "key": "POST /api/sessions",
    "path": "/api/sessions",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/sessions"
  },
  {
    "key": "GET /api/sessions/main",
    "path": "/api/sessions/main",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/sessions/main"
  },
  {
    "key": "GET /api/sessions/{session_id}",
    "path": "/api/sessions/{session_id}",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/sessions/{session_id}"
  },
  {
    "key": "PATCH /api/sessions/{session_id}",
    "path": "/api/sessions/{session_id}",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/sessions/{session_id}"
  },
  {
    "key": "DELETE /api/sessions/{session_id}",
    "path": "/api/sessions/{session_id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/sessions/{session_id}"
  },
  {
    "key": "POST /api/sessions/{session_id}/forks",
    "path": "/api/sessions/{session_id}/forks",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/sessions/{session_id}/forks"
  },
  {
    "key": "GET /api/sessions/{session_id}/history",
    "path": "/api/sessions/{session_id}/history",
    "method": "GET",
    "response": "json",
    "identities": [
      "desktop",
      "remote"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": true,
    "route_path": "/api/sessions/{session_id}/history"
  },
  {
    "key": "GET /api/update/latest.yml",
    "path": "/api/update/latest.yml",
    "method": "GET",
    "response": "json",
    "identities": [
      "public"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/update/latest.yml"
  },
  {
    "key": "GET /api/update/latest-mac.yml",
    "path": "/api/update/latest-mac.yml",
    "method": "GET",
    "response": "json",
    "identities": [
      "public"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/update/latest-mac.yml"
  },
  {
    "key": "GET /api/update/latest-runner.yml",
    "path": "/api/update/latest-runner.yml",
    "method": "GET",
    "response": "binary",
    "identities": [
      "public"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/update/latest-runner.yml"
  },
  {
    "key": "GET /api/update/{filename}",
    "path": "/api/update/{filename}",
    "method": "GET",
    "response": "binary",
    "identities": [
      "public"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/update/{filename:path}"
  },
  {
    "key": "GET /api/admin/updates/versions",
    "path": "/api/admin/updates/versions",
    "method": "GET",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/updates/versions"
  },
  {
    "key": "POST /api/admin/updates/versions",
    "path": "/api/admin/updates/versions",
    "method": "POST",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "release",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/updates/versions"
  },
  {
    "key": "PATCH /api/admin/updates/versions/{id}",
    "path": "/api/admin/updates/versions/{id}",
    "method": "PATCH",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/updates/versions/{id}"
  },
  {
    "key": "DELETE /api/admin/updates/versions/{id}",
    "path": "/api/admin/updates/versions/{id}",
    "method": "DELETE",
    "response": "json",
    "identities": [
      "admin"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/admin/updates/versions/{id}"
  },
  {
    "key": "POST /api/user/activate",
    "path": "/api/user/activate",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/user/activate"
  },
  {
    "key": "POST /api/user/refresh",
    "path": "/api/user/refresh",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/user/refresh"
  },
  {
    "key": "POST /api/user/logout",
    "path": "/api/user/logout",
    "method": "POST",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "upload": "json",
    "wait_for_model": false,
    "ipc": false,
    "route_path": "/api/user/logout"
  }
] as const
export const RPC_POLICIES = {
  "command.dispatch": {
    "remote": true,
    "concurrent": true,
    "wait_for_model": true
  },
  "companion.idle_expression": {
    "remote": false,
    "concurrent": true,
    "wait_for_model": true
  },
  "companion.record_interaction_stats": {
    "remote": false,
    "concurrent": false,
    "wait_for_model": false
  },
  "companion.should_act": {
    "remote": false,
    "concurrent": true,
    "wait_for_model": true
  },
  "companion.signal": {
    "remote": false,
    "concurrent": false,
    "wait_for_model": false
  },
  "prompt.submit": {
    "remote": true,
    "concurrent": false,
    "wait_for_model": false
  },
  "session.ack": {
    "remote": true,
    "concurrent": true,
    "wait_for_model": false
  },
  "session.compress_context": {
    "remote": true,
    "concurrent": true,
    "wait_for_model": true
  },
  "session.interrupt": {
    "remote": true,
    "concurrent": true,
    "wait_for_model": false
  },
  "session.ping": {
    "remote": true,
    "concurrent": true,
    "wait_for_model": false
  },
  "session.resume": {
    "remote": true,
    "concurrent": false,
    "wait_for_model": false
  },
  "session.undo_to_message": {
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
