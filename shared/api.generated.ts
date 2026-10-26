// 由 scripts/generate_protocol.py 从服务端公开契约生成。

export interface ApiSchemas {
  "AIConfigPublic": { "providers": Array<ApiSchemas["ProviderCardPublic"]>; "capabilities": ApiSchemas["CapabilityChains"]; "tasks": { [key: string]: ApiSchemas["TaskConfig"] } }
  "AIConfigUpdate": { "providers": Array<ApiSchemas["ProviderCardUpdate"]>; "capabilities": ApiSchemas["CapabilityChainsInput"]; "tasks"?: { [key: string]: ApiSchemas["TaskConfigInput"] } }
  "ActionAssetDesign": { "name": string; "motion_description": string; "use_when"?: Array<string>; "avoid_when"?: Array<string>; "allowed_triggers"?: Array<string>; "clip_kind"?: "once" | "loop"; "duration_seconds"?: number; "reason"?: string }
  "ActionAssetUpdate": { "name": string; "motion_description": string; "use_when"?: Array<string>; "avoid_when"?: Array<string>; "allowed_triggers"?: Array<string>; "clip_kind"?: "once" | "loop"; "duration_seconds"?: number; "reason"?: string; "expected_updated_at": string }
  "ActionAssetView": { "id": string; "name": string; "motion_description": string; "use_when": Array<string>; "avoid_when": Array<string>; "allowed_triggers": Array<string>; "clip_kind": "once" | "loop"; "duration_seconds": number; "reason": string; "source": "user_requested" | "autonomous" | "preset"; "status": "pending" | "reviewing" | "approved" | "deferred" | "rejected"; "review_reason": string; "created_at": string; "updated_at": string }
  "ActionListResponse": { "actions": Array<ApiSchemas["ActionAssetView"]>; "total": (number | null); "has_more": boolean; "next_cursor": (string | null); "limit": number }
  "ActionMaterialRef": { "action_id": (string | null); "name": string; "motion_description": string; "clip_kind": "once" | "loop"; "duration_seconds": number; "use_when": Array<string>; "avoid_when": Array<string>; "allowed_triggers": Array<string>; "revision": (string | null) }
  "ActivateRequest": { "code": string; "client_version"?: string; "client_context"?: (ApiSchemas["ChatRequestClientContext"] | null) }
  "ActiveTurnView": { "request_id": string; "origin_kind": string; "message_ids": Array<string>; "assistant_message_id": (string | null); "messages": Array<ApiSchemas["SessionMessageView"]>; "text": string; "reasoning": string; "bubbles": Array<ApiSchemas["ReplyBubbleView"]>; "tools": Array<ApiSchemas["ToolSnapshot"]>; "running": boolean; "replace_reply_message_id": (string | null) }
  "ActualPresentationSnapshot": { "visual_id": (string | null); "media_kind": ("image" | "video" | null); "visual_version": (number | null); "visual_description": string; "visual_path": (string | null); "outfit_path": (string | null); "outfit_hash": (string | null); "outfit_description": string; "scene_path": (string | null); "scene_hash": (string | null); "scene_description": string; "identity_path": (string | null); "identity_hash": (string | null); "identity_reference_role": "portrait" | "fullbody"; "ready": boolean }
  "AdminLoginRequest": { "username": string; "password": string }
  "AdminTokenResponse": { "access_token": string; "expires_in": number }
  "AiTaskDefinition": { "id": ApiSchemas["AiTaskKey"]; "group": string; "label": string; "services": Array<string>; "service_usage": { [key: string]: Array<string> } }
  "AiTaskKey": "companion_chat" | "work_chat" | "scheduled_task" | "proactive" | "memory_review" | "memory_embedding" | "nightly" | "posts" | "appearance" | "outfit" | "scene" | "visuals" | "speech_input" | "companion_voice" | "speech_preview" | "voice_design" | "voice_clone"
  "AiTaskKeyInput": "companion_chat" | "work_chat" | "scheduled_task" | "proactive" | "memory_review" | "memory_embedding" | "nightly" | "posts" | "appearance" | "outfit" | "scene" | "visuals" | "speech_input" | "companion_voice" | "speech_preview" | "voice_design" | "voice_clone"
  "ApiError": { "code": string; "message": string; "details": ApiSchemas["JsonValue"]; "request_id": string }
  "ApiErrorResponse": { "error": ApiSchemas["ApiError"] }
  "AvatarAssetResponse": { "id": string; "asset_url": string; "seed_fullbody_url": string; "is_fullbody_confirmed": boolean; "prompt": string; "status": "succeeded" }
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
  "CharacterCardExtract": { "expected_avatar_id": string; "expected_revision": number }
  "CharacterCardResponse": { "avatar_id": string; "revision": number; "features": ApiSchemas["CharacterFeatures"]; "overrides": ApiSchemas["CharacterOverrides"]; "automatic": ApiSchemas["CharacterFeatures"]; "status": "pending" | "running" | "ready" | "failed"; "portrait_status": "pending" | "running" | "ready" | "failed"; "body_status": "pending" | "running" | "ready" | "failed"; "error": (string | null) }
  "CharacterCardSnapshot": { "avatar_id": (string | null); "revision": number; "features": ApiSchemas["CharacterFeatures"]; "overrides": ApiSchemas["CharacterOverrides"] }
  "CharacterCardUpdate": { "expected_avatar_id": string; "expected_revision": number; "changes": ApiSchemas["CharacterOverridesInput"] }
  "CharacterFeatures": { "body_shape": string; "proportions": string; "limbs_and_appendages": string; "body_surface": string; "head_shape": string; "facial_features": string; "facial_surface": string; "head_identifiers": string }
  "CharacterOverrides": { "head_shape": (string | null); "facial_features": (string | null); "facial_surface": (string | null); "head_identifiers": (string | null); "body_shape": (string | null); "proportions": (string | null); "limbs_and_appendages": (string | null); "body_surface": (string | null) }
  "CharacterOverridesInput": { "head_shape"?: (string | null); "facial_features"?: (string | null); "facial_surface"?: (string | null); "head_identifiers"?: (string | null); "body_shape"?: (string | null); "proportions"?: (string | null); "limbs_and_appendages"?: (string | null); "body_surface"?: (string | null) }
  "ChatAttachment": { "type": "image" | "video" | "audio"; "file_url": string }
  "ChatAudioUploadResponse": { "url": string; "mime_type": string; "duration": number }
  "ChatRequestClientContext": { "environment_hints"?: (string | null); "platform_hints"?: (string | null) }
  "ChatVideoUploadResponse": { "url": string }
  "ClientMediaBubble": { "type": "image" | "video"; "media_id": string; "status": "pending" | "ready" | "failed" | "result_unknown"; "url": (string | null); "error": (string | null); "video_queue": (ApiSchemas["VideoQueueInfo"] | null) }
  "ClientMediaEntry": { "type": "image" | "video" | "audio"; "url": string; "audio_url": (string | null); "duration": (number | null); "transcript": (string | null) }
  "ComboMaterialRef": { "material_id": (string | null); "name": string; "path": string; "content_hash": string; "description": string; "revision": (string | null) }
  "ComboSnapshot": { "identity": ApiSchemas["CharacterCardSnapshot"]; "identity_path": string; "identity_hash": string; "portrait_path": string; "portrait_hash": string; "identity_reference_role": "portrait" | "fullbody"; "outfit": ApiSchemas["ComboMaterialRef"]; "scene": ApiSchemas["ComboMaterialRef"]; "action": ApiSchemas["ActionMaterialRef"]; "plan": (ApiSchemas["VisualCreationPlan"] | null); "target_width": number; "target_height": number; "requirements": string; "feedback": string; "video_feedback": string; "persona": { [key: string]: string } }
  "CommandDispatchRequest": { "command": string; "args"?: (Array<(string | number | number)> | string | null); "confirmed"?: boolean }
  "CommandDispatchResult": { "command": string; "result": ApiSchemas["CommandResult"] }
  "CommandListResponse": { "commands": Array<ApiSchemas["CommandSummary"]> }
  "CommandResult": { "status": "ok" | "error"; "message": string; "payload": (ApiSchemas["SessionClearResult"] | ApiSchemas["SessionCompressionResult"] | ApiSchemas["RememberCommandResult"] | null); "hydrate": boolean }
  "CommandSummary": { "name": string; "aliases": Array<string>; "description": string; "requires_confirmation": boolean }
  "CompanionOperationResponse": { "ok": boolean }
  "CompanionSessionMessageView": { "id": string; "role": "assistant"; "content": (string | null); "subtype": string; "reasoning": (string | null); "timestamp": number; "can_replace_reply": boolean; "media": Array<ApiSchemas["ClientMediaEntry"]>; "tool_calls": ApiSchemas["JsonValue"]; "tool_call_id": (string | null); "tool_name": string; "content_type": "companion_reply"; "bubbles": Array<ApiSchemas["ReplyBubbleView"]> }
  "CompressionUsage": { "total_tokens": number; "context_window": number }
  "CurrentVoiceRequest": { "voice_id": string }
  "CurrentVoiceResponse": { "voice_id": string }
  "CustomVoiceSummary": { "candidate_id": string; "kind": "design" | "clone"; "voice_id": string; "prompt": string; "preview_audio_url": string; "reference_audio_url": (string | null); "provider": string; "model": string; "version": number; "available": boolean }
  "DesignAction": { "capability": "action.design"; "name": string; "motion_description": string; "reason": string; "use_when": Array<string>; "avoid_when": Array<string>; "duration_seconds": number; "clip_kind": "once" | "loop" }
  "DesktopConfigPutRequest": { "config": { [key: string]: ApiSchemas["JsonValueInput"] } }
  "DesktopConfigResponse": { "config": { [key: string]: ApiSchemas["JsonValue"] } }
  "DesktopSessionInfo": { "id": string; "kind": string; "title": (string | null); "started_at": number; "last_active": number; "message_count": number; "input_tokens": number; "output_tokens": number; "tool_call_count": number; "preview": (string | null); "pinned": boolean; "archived": boolean; "system_preset_id": (string | null); "system_preset_icon_key": (string | null) }
  "DesktopSessionListResponse": { "limit": number; "total": (number | null); "has_more": boolean; "next_cursor": (string | null); "sessions": Array<ApiSchemas["DesktopSessionInfo"]> }
  "DesktopSessionOperationResponse": { "ok": boolean }
  "DesktopSessionPatchRequest": { "title"?: (string | null); "pinned"?: (boolean | null); "archived"?: (boolean | null); "settings"?: (ApiSchemas["SessionSettingsPatch"] | null) }
  "DeviceListResponse": { "items": Array<ApiSchemas["DeviceResponse"]> }
  "DeviceResponse": { "id": string; "name": string; "expires_at": string; "last_seen_at": string; "created_at": string }
  "DiaryEntryResponse": { "id": string; "entry_date": string; "title": string; "body": string; "post_ids": Array<string>; "created_at": (string | null); "updated_at": (string | null) }
  "DiaryListResponse": { "entries": Array<ApiSchemas["DiaryEntryResponse"]>; "unread_diary_ids": Array<string> }
  "DiaryReadRequest": { "diary_ids": Array<string> }
  "DiaryUnreadResponse": { "has_unread": boolean }
  "ExchangeRequest": { "token": string; "device_name"?: string }
  "FullbodyAdoptRequest": { "image": string; "content_type"?: (string | null) }
  "FullbodyCandidateResponse": { "id": string; "avatar_id": string; "image_url": string; "status": "pending" | "ready" | "failed" | "accepted" | "rejected"; "error": (string | null) }
  "FullbodyConfirmRequest": { "expected_url": string }
  "FullbodyPromptRequest": { "feedback"?: (string | null) }
  "FullbodyReferenceGenerateRequest": { "feedback"?: (string | null); "image"?: (string | null); "content_type"?: (string | null); "mode": "edit" | "regenerate"; "candidate_id"?: (string | null) }
  "GatewayTicketRequest": { "stream_id"?: (string | null) }
  "GatewayTicketResponse": { "ticket": string; "expires_in": number }
  "IdleExpressionRequest": { "trigger"?: "periodic" | "user_return"; "idle_seconds"?: (number | null); "local_hour"?: (number | null) }
  "IdleExpressionResult": { "expressed": boolean; "action_id": (string | null); "reason": string }
  "ImagePromptResponse": { "prompt": string }
  "InteractionStatsRequest": { "kind": "chat_turn"; "hour": number }
  "InteractionStatsResult": { "recorded": "chat_turn"; "threshold_met": boolean; "peak_hour": (number | null) }
  "JsonValue": unknown
  "JsonValueInput": unknown
  "LastSubmissionSnapshot": { "request_id": string; "status": string; "error": (string | null); "message_ids": Array<string>; "retry_message_id": (string | null); "replace_reply_message_id": (string | null); "result_message_id": (string | null) }
  "LifeEventListResponse": { "items": Array<ApiSchemas["LifeEventView"]>; "total": (number | null); "has_more": boolean; "next_cursor": (string | null) }
  "LifeEventView": { "id": string; "event_key": string; "kind": string; "source": string; "subject": string; "fact_type": "intent" | "virtual_activity" | "execution" | "result"; "fact": { [key: string]: unknown }; "occurred_at": string; "pursuit_id": (string | null); "step_id": (string | null); "result_ref": { [key: string]: unknown }; "is_meaningful": boolean }
  "LifePursuitDetail": { "id": string; "title": string; "reason": string; "desired_result": string; "progress": string; "next_step": string; "status": "active" | "paused" | "completed" | "abandoned"; "status_reason": string; "status_source": string; "status_changed_at": (string | null); "resume_after": (string | null); "previous_pursuit_id": (string | null); "started_at": string; "completed_at": (string | null); "result_ref": { [key: string]: unknown }; "updated_at": string; "steps": Array<ApiSchemas["LifeStepView"]> }
  "LifePursuitListResponse": { "items": Array<ApiSchemas["LifePursuitView"]>; "total": (number | null); "has_more": boolean; "next_cursor": (string | null) }
  "LifePursuitView": { "id": string; "title": string; "reason": string; "desired_result": string; "progress": string; "next_step": string; "status": "active" | "paused" | "completed" | "abandoned"; "status_reason": string; "status_source": string; "status_changed_at": (string | null); "resume_after": (string | null); "previous_pursuit_id": (string | null); "started_at": string; "completed_at": (string | null); "result_ref": { [key: string]: unknown }; "updated_at": string }
  "LifeStateView": { "user_id": string; "current_activity": string; "activity_kind": "intent" | "virtual_activity" | "execution" | "result"; "activity_started_at": (string | null); "activity_step_id": (string | null); "presentation_revision": number; "current_scene": { [key: string]: unknown }; "mood": (string | null); "active_pursuit_id": (string | null); "next_review_at": (string | null); "revision": number; "scene_revision": number; "updated_at": string }
  "LifeStepView": { "id": string; "pursuit_id": (string | null); "idempotency_key": string; "kind": string; "description": string; "state": "planned" | "running" | "completed" | "failed" | "cancelled" | "unknown"; "source": string; "action": ((ApiSchemas["VisualAction"] | ApiSchemas["SceneAction"] | ApiSchemas["OutfitAction"] | ApiSchemas["DesignAction"] | ApiSchemas["ReadingAction"]) | null); "disposition": string; "wait_reason": string; "not_before": (string | null); "reading_result": (ApiSchemas["ReadingResultView"] | null); "business_ref": { [key: string]: unknown }; "result_ref": { [key: string]: unknown }; "error": (string | null); "started_at": (string | null); "finished_at": (string | null) }
  "LinkBubble": { "type": "link"; "topic_id": string; "title": string; "platform": "x" | "xiaohongshu"; "url": string; "published_at": string }
  "MemoryDeleteResponse": { "deleted": boolean }
  "MemoryDetail": { "id": string; "content_version": number; "content": string; "context": string; "kind": string; "slot_key": (string | null); "category": (string | null); "tags": Array<string>; "basis": "explicit" | "inferred" | "observed" | "system"; "status": "active" | "candidate" | "invalidated" | "forgotten"; "usage": "contextual" | "background"; "reason": string; "source_kind": string; "updated_at": string; "expires_at": (string | null); "reviewed_at": (string | null); "system_preset_id": string; "importance": number; "created_at": string; "evidence": Array<ApiSchemas["MemoryEvidence"]>; "revisions": Array<{ [key: string]: unknown }> }
  "MemoryEvidence": { "fingerprint": string; "message_id"?: (string | null); "quote"?: (string | null); "stance"?: ("supports" | "opposes" | null); "created_at"?: (string | null); "source_unavailable"?: (boolean | null) }
  "MemoryListItem": { "id": string; "content_version": number; "content": string; "context": string; "kind": string; "slot_key": (string | null); "category": (string | null); "tags": Array<string>; "basis": "explicit" | "inferred" | "observed" | "system"; "status": "active" | "candidate" | "invalidated" | "forgotten"; "usage": "contextual" | "background"; "reason": string; "source_kind": string; "updated_at": string; "expires_at": (string | null); "reviewed_at": (string | null); "system_preset_id": string; "importance": number; "created_at": string }
  "MemoryListResponse": { "memories": Array<ApiSchemas["MemoryListItem"]>; "counts": { [key: string]: number }; "total": (number | null); "has_more": boolean; "next_cursor": (string | null); "system_preset_id": string }
  "MemoryProfileChange": { "field": "user_call_name" | "user_gender" | "user_birthday" | "user_hobbies" | "user_freeform"; "value": (string | null); "expected_version": number }
  "MemoryProfileRequest": { "system_preset_id": "companion"; "changes": Array<ApiSchemas["MemoryProfileChange"]> }
  "MemoryProfileResponse": { "memories": Array<ApiSchemas["MemoryListItem"]>; "system_preset_id": "companion" }
  "MemoryUpdateRequest": { "system_preset_id": string; "content": string; "expected_version": number }
  "MessageResponse": { "message": string }
  "ModelDiscoveryRequest": { "provider": ApiSchemas["ProviderCardUpdate"]; "user_id"?: (string | null); "service": "llm" | "stt" | "tts" | "image_gen" | "video_gen" | "embedding"; "model_name"?: (string | null); "catalog_id"?: (string | null); "refresh"?: boolean }
  "ModelDiscoveryResponse": { "models": Array<ApiSchemas["ModelMetadata"]>; "candidates": { [key: string]: Array<ApiSchemas["ModelMetadata"]> }; "warnings": Array<string>; "unsupported": boolean; "catalog_updated_at": (string | null) }
  "ModelMetadata": { "model_name": string; "display_name": string; "services": Array<string>; "context_window": (number | null); "max_output_tokens": (number | null); "input_modalities": (Array<ApiSchemas["ModelModality"]> | null); "output_modalities": (Array<ApiSchemas["ModelModality"]> | null); "catalog_id": (string | null); "canonical_slug": (string | null); "catalog_url": (string | null); "field_sources": { [key: string]: string } }
  "ModelModality": "text" | "image" | "audio" | "video" | "embeddings"
  "ModelModalityInput": "text" | "image" | "audio" | "video" | "embeddings"
  "NightlyActivityLogItem": { "id": string; "user_id": string; "target_date": string; "status": string; "summary": (string | null); "payload": ({ [key: string]: unknown } | null); "created_at": (string | null); "updated_at": (string | null) }
  "NightlyActivityLogListResponse": { "items": Array<ApiSchemas["NightlyActivityLogItem"]> }
  "OnboardingPatchRequest": { "field": string; "value"?: (string | null) }
  "OnboardingStateResponse": { "answers": { [key: string]: string }; "next_field": (string | null); "complete": boolean }
  "OutfitAction": { "capability": "outfit.create"; "description": string }
  "OutfitAdoptRequest": { "source": "import"; "description"?: (string | null); "image": string; "content_type"?: (string | null) }
  "OutfitCreateRequest": { "source": "generate"; "description"?: (string | null); "image"?: (string | null); "content_type"?: (string | null) }
  "OutfitImageImportRequest": { "image": string; "content_type"?: (string | null); "source": "import" }
  "OutfitListResponse": { "outfits": Array<ApiSchemas["OutfitResponse"]>; "policy": "locked" | "llm_may_replace" }
  "OutfitPolicyRequest": { "policy": "locked" | "llm_may_replace" }
  "OutfitPolicyResponse": { "policy": "locked" | "llm_may_replace" }
  "OutfitPromptRequest": { "outfit_id"?: (string | null); "feedback"?: (string | null); "description"?: (string | null); "image"?: (string | null); "content_type"?: (string | null) }
  "OutfitRegenerateRequest": { "source": "generate"; "feedback"?: (string | null); "mode": "edit" | "regenerate" }
  "OutfitResponse": { "description_status": "pending" | "processing" | "ready" | "failed"; "description_error": (string | null); "id": string; "name": string; "description": (string | null); "fullbody_url": string; "status": string }
  "PairingResponse": { "id": string; "url"?: (string | null); "expires_at": string; "state": "pending" | "paired" | "cancelled" | "expired" }
  "PersonaResponse": { "definition_json": { [key: string]: string }; "is_complete": boolean; "personality_tags": Array<string>; "current_mood": (string | null) }
  "PersonaUpdate": { "definition_json": { [key: string]: (string | null) }; "expected_fields"?: ({ [key: string]: (string | null) } | null) }
  "PlainSessionMessageView": { "id": string; "role": "assistant" | "system" | "tool" | "user"; "content": (string | null); "subtype": string; "reasoning": (string | null); "timestamp": number; "can_replace_reply": boolean; "media": Array<ApiSchemas["ClientMediaEntry"]>; "tool_calls": ApiSchemas["JsonValue"]; "tool_call_id": (string | null); "tool_name": string; "content_type": "multimodal_v1" | "text" }
  "PortraitConfirmRequest": { "expected_avatar_id": string }
  "PostCommentCreateRequest": { "content": string }
  "PostCommentListResponse": { "comments": Array<ApiSchemas["PostCommentResponse"]>; "next_cursor": (string | null) }
  "PostCommentResponse": { "id": string; "post_id": string; "role": ApiSchemas["PostCommentRole"]; "content": string; "created_at": string; "updated_at": string; "reply_to_comment_id": (string | null); "reply_status": "none" | "pending" | "running" | "completed" | "failed"; "reply_error": (string | null) }
  "PostCommentRole": "user" | "companion"
  "PostContentType": "text" | "image" | "video" | "audio"
  "PostListResponse": { "posts": Array<ApiSchemas["PostResponse"]>; "next_cursor": (string | null); "unread_post_ids": Array<string> }
  "PostPublicationRecovery": { "publication_id": string; "status": "queued" | "running" | "published" | "partial" | "failed" | "blocked" | "result_unknown" | "declined" | "discarded"; "post_id": (string | null); "error": (string | null); "title": string; "video_status": "pending" | "ready" | "failed" | "unknown" | "discarded"; "media_url": (string | null); "can_discard": boolean }
  "PostPublicationRecoveryList": { "items": Array<ApiSchemas["PostPublicationRecovery"]>; "next_cursor": (string | null); "has_more": boolean }
  "PostPublicationResult": { "publication_id": string; "status": "queued" | "running" | "published" | "partial" | "failed" | "blocked" | "result_unknown" | "declined" | "discarded"; "post_id": (string | null); "error": (string | null) }
  "PostReadRequest": { "post_ids": Array<string> }
  "PostResponse": { "id": string; "published_at": string; "content_type": ApiSchemas["PostContentType"]; "title": string; "body": string; "media_url": (string | null); "audio_url": (string | null); "comments": Array<ApiSchemas["PostCommentResponse"]>; "comment_count": number; "comments_next_cursor": (string | null); "comments_loaded": boolean }
  "PostUnreadResponse": { "has_unread": boolean }
  "PromptBatchMessage": { "text": string; "attachments"?: (Array<ApiSchemas["ChatAttachment"]> | null) }
  "PromptPresetListResponse": { "presets": Array<ApiSchemas["PromptPresetSummary"]> }
  "PromptPresetSummary": { "id": string; "name": string; "description": string; "icon_key": string }
  "PromptSubmitRequest": { "request_id": string; "text"?: (string | null); "attachments"?: (Array<ApiSchemas["ChatAttachment"]> | null); "batch"?: (Array<ApiSchemas["PromptBatchMessage"]> | null); "edit_message_id"?: (string | null); "retry_message_id"?: (string | null); "replace_reply_message_id"?: (string | null); "response_preference"?: ("text" | "voice" | null) }
  "PromptSubmitResult": { "queued": boolean; "request_id": string; "status": "accepted" | "running" | "completed" | "cancelled" | "interrupted" | "failed"; "error": (string | null); "message_ids": Array<string>; "messages": Array<ApiSchemas["SessionMessageView"]>; "replace_reply_message_id": (string | null); "result_message_id": (string | null) }
  "ProviderCardPublic": { "id": string; "name": string; "provider_type": string; "api_key": string; "base_url": string; "api_key_set": boolean }
  "ProviderCardUpdate": { "id": string; "name": string; "provider_type": string; "api_key"?: string; "base_url"?: string; "api_key_set"?: boolean; "clear_api_key"?: boolean }
  "ProviderDefaults": { "base_url": string; "requires_api_key": boolean; "supports_voice_design": boolean; "voice_design_model": (string | null); "supports_voice_clone": boolean; "voice_clone_model": (string | null); "input_modalities": Array<ApiSchemas["ModelModality"]> }
  "ReadingAction": { "capability": "content.read"; "purpose": string; "query": string; "urls": Array<string>; "topic_ids": Array<string> }
  "ReadingResultView": { "sources": Array<ApiSchemas["ReadingSourceView"]>; "notes": string }
  "ReadingSourceView": { "url": string; "title": string; "retrieved_at": string; "content_kind": "extracted_text" | "summary"; "excerpted": boolean }
  "RefreshRequest": { "client_version"?: string; "client_context"?: (ApiSchemas["ChatRequestClientContext"] | null) }
  "RegenerateActivationRequest": { "base_url"?: (string | null) }
  "ReleaseManifestFileItem": { "url": string; "sha512": string; "size": number }
  "ReleaseManifestResponse": { "version": string; "releaseDate": string; "releaseNotes": string; "path": string; "sha512": string; "files": Array<ApiSchemas["ReleaseManifestFileItem"]> }
  "RememberCommandResult": { "memory_id": string; "content": string }
  "RemoteSessionResponse": { "user": ApiSchemas["RemoteUserResponse"]; "device": ApiSchemas["DeviceResponse"]; "csrf_token": string }
  "RemoteUserResponse": { "id": string; "username": string }
  "ReplyAudio": { "url": string; "duration": number }
  "ReplyBubbleView": (ApiSchemas["TextBubble"] | ApiSchemas["VoiceBubbleView"] | ApiSchemas["ClientMediaBubble"] | ApiSchemas["LinkBubble"])
  "RpcCacheUsage": { "request_count": number; "reported_request_count": number; "input_tokens": (number | null); "cached_input_tokens": (number | null); "cache_hit_rate": (number | null) }
  "RpcCharacterCardEvent": { "avatar_id": string; "revision": number; "status": "pending" | "running" | "ready" | "failed" }
  "RpcClientMediaBubble": { "type": "image" | "video"; "media_id": string; "status": "pending" | "ready" | "failed" | "result_unknown"; "url": (string | null); "error": (string | null); "video_queue": (ApiSchemas["RpcVideoQueueInfo"] | null) }
  "RpcClientMediaEntry": { "type": "image" | "video" | "audio"; "url": string; "audio_url": (string | null); "duration": (number | null); "transcript": (string | null) }
  "RpcComboMaterialRef": { "material_id": (string | null); "name": string; "path": string; "content_hash": string; "description": string; "revision": (string | null) }
  "RpcCommandDispatchResult": { "command": string; "result": ApiSchemas["RpcCommandResult"] }
  "RpcCommandResult": { "status": "ok" | "error"; "message": string; "payload": (ApiSchemas["RpcSessionClearResult"] | ApiSchemas["RpcSessionCompressionResult"] | ApiSchemas["RpcRememberCommandResult"] | null); "hydrate": boolean }
  "RpcCompanionMessageEvent": { "session_id": string; "message_id": string; "bubbles": Array<ApiSchemas["RpcReplyBubbleView"]> }
  "RpcCompanionSessionMessageView": { "id": string; "role": "assistant"; "content": (string | null); "subtype": string; "reasoning": (string | null); "timestamp": number; "can_replace_reply": boolean; "media": Array<ApiSchemas["RpcClientMediaEntry"]>; "tool_calls": ApiSchemas["RpcJsonValue"]; "tool_call_id": (string | null); "tool_name": string; "content_type": "companion_reply"; "bubbles": Array<ApiSchemas["RpcReplyBubbleView"]> }
  "RpcCompanionSignalResult": { "queued": boolean }
  "RpcCompressionEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "session_id": (string | null); "subtype": string; "text": string; "message_id": (string | null) }
  "RpcCompressionUsage": { "total_tokens": number; "context_window": number }
  "RpcDiaryDeletedEvent": { "diary_id": string; "entry_date": string }
  "RpcDiaryEntryResponse": { "id": string; "entry_date": string; "title": string; "body": string; "post_ids": Array<string>; "created_at": (string | null); "updated_at": (string | null) }
  "RpcEmptyResult": { [key: string]: unknown }
  "RpcGatewayAckResult": { "acked": number; "pruned": number }
  "RpcGatewayErrorEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "message": string; "retry_message_id": (string | null) }
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
  "RpcLifeUpdatedEvent": { "revision": (number | null); "pursuit_id": (string | null); "step_id": (string | null); "event_key": (string | null) }
  "RpcLinkBubble": { "type": "link"; "topic_id": string; "title": string; "platform": "x" | "xiaohongshu"; "url": string; "published_at": string }
  "RpcMemoryChangedEvent": { "system_preset_id": string; "memory_id": (string | null) }
  "RpcMessageAudioEvent": { "session_id": string; "message_id": string; "audio_url": string; "duration": (number | null); "transcript": (string | null) }
  "RpcMessageBubbleEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "message_id": string; "bubble_index": number; "bubble": ApiSchemas["RpcReplyBubbleView"] }
  "RpcMessageCompleteEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "text": (string | null); "bubbles": (Array<ApiSchemas["RpcReplyBubbleView"]> | null); "reasoning": (string | null); "media": (Array<ApiSchemas["RpcClientMediaEntry"]> | null); "usage": (ApiSchemas["RpcTokenUsage"] | null); "cache_usage": (ApiSchemas["RpcCacheUsage"] | null); "message_id": (string | null); "can_replace_reply": boolean }
  "RpcMessageDeletedEvent": { "session_id": string; "messages": Array<ApiSchemas["RpcSessionMessageView"]>; "deleted_count": number; "anchor": ApiSchemas["RpcUndoAnchor"] }
  "RpcMessageDeltaEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "text": string }
  "RpcMessageEditedEvent": { "session_id": string; "messages": Array<ApiSchemas["RpcSessionMessageView"]> }
  "RpcMessageMediaEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "message_id": string; "bubble_index": number; "bubble": ApiSchemas["RpcClientMediaBubble"]; "session_id": string; "media_id": string }
  "RpcMessagePersistedEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "role": string; "message_ids": Array<string>; "messages": Array<ApiSchemas["RpcSessionMessageView"]> }
  "RpcMessageReplacedEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "session_id": string; "replaced_message_id": string; "message": ApiSchemas["RpcSessionMessageView"]; "history_revision": number }
  "RpcMessageVoiceEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "message_id": string; "bubble_index": number; "bubble": ApiSchemas["RpcVoiceBubbleView"]; "session_id": string }
  "RpcMoodEvent": { "mood": string }
  "RpcOutfitChangedEvent": { "outfit_id": string }
  "RpcPlainSessionMessageView": { "id": string; "role": "assistant" | "system" | "tool" | "user"; "content": (string | null); "subtype": string; "reasoning": (string | null); "timestamp": number; "can_replace_reply": boolean; "media": Array<ApiSchemas["RpcClientMediaEntry"]>; "tool_calls": ApiSchemas["RpcJsonValue"]; "tool_call_id": (string | null); "tool_name": string; "content_type": "multimodal_v1" | "text" }
  "RpcPostCommentDeletedEvent": { "post_id": string; "comment_id": string }
  "RpcPostCommentEvent": { "post_id": string; "comment": ApiSchemas["RpcPostCommentResponse"] }
  "RpcPostCommentResponse": { "id": string; "post_id": string; "role": ApiSchemas["RpcPostCommentRole"]; "content": string; "created_at": string; "updated_at": string; "reply_to_comment_id": (string | null); "reply_status": "none" | "pending" | "running" | "completed" | "failed"; "reply_error": (string | null) }
  "RpcPostCommentRole": "user" | "companion"
  "RpcPostContentType": "text" | "image" | "video" | "audio"
  "RpcPostResponse": { "id": string; "published_at": string; "content_type": ApiSchemas["RpcPostContentType"]; "title": string; "body": string; "media_url": (string | null); "audio_url": (string | null); "comments": Array<ApiSchemas["RpcPostCommentResponse"]>; "comment_count": number; "comments_next_cursor": (string | null); "comments_loaded": boolean }
  "RpcRememberCommandResult": { "memory_id": string; "content": string }
  "RpcReplyAudio": { "url": string; "duration": number }
  "RpcReplyBubbleView": (ApiSchemas["RpcTextBubble"] | ApiSchemas["RpcVoiceBubbleView"] | ApiSchemas["RpcClientMediaBubble"] | ApiSchemas["RpcLinkBubble"])
  "RpcSceneChangedEvent": { "scene_id": (string | null) }
  "RpcSessionClearResult": { "session_id": string; "cleared_count": number; "messages": Array<ApiSchemas["RpcSessionMessageView"]> }
  "RpcSessionCompressionResult": { "session_id": string; "compressed": boolean; "reason": (string | null); "usage": ApiSchemas["RpcCompressionUsage"]; "replaced_count": (number | null); "summary": (string | null); "messages": (Array<ApiSchemas["RpcSessionMessageView"]> | null) }
  "RpcSessionListChangedEvent": { "session_id": string; "deleted": boolean }
  "RpcSessionMessageView": (ApiSchemas["RpcPlainSessionMessageView"] | ApiSchemas["RpcCompanionSessionMessageView"])
  "RpcSessionStateEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "messages": (Array<ApiSchemas["RpcSessionMessageView"]> | null); "running": boolean; "status": string; "error": (string | null); "result_message_id": (string | null) }
  "RpcSystemNotificationEvent": { "kind": "error" | "success"; "title": string; "message": string; "session_id": string }
  "RpcTextBubble": { "type": "text"; "text": string }
  "RpcTokenUsage": { "prompt_tokens": (number | null); "completion_tokens": (number | null); "total_tokens": (number | null); "input_tokens": (number | null); "output_tokens": (number | null); "reasoning_tokens": (number | null); "context_window": (number | null) }
  "RpcToolCallEvent": { "call_id": string; "name": string; "args": { [key: string]: ApiSchemas["RpcJsonValue"] }; "session_id": (string | null); "headless": boolean; "skill_scope": (ApiSchemas["RpcToolScope"] | null) }
  "RpcToolCancelEvent": { "call_id": string }
  "RpcToolProgressEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null); "name": (string | null); "call_id": (string | null); "status": "running" | "complete" }
  "RpcToolResultResult": { "status": "accepted" | "no_longer_pending" }
  "RpcToolScope": { "user_id": string; "system_preset_id": string }
  "RpcToolsSyncResult": { "count": number }
  "RpcTopicFeedbackSnapshot": { "topic_id": string; "value": ("like" | "less" | null); "updated_at": (string | null) }
  "RpcTurnEvent": { "request_id": (string | null); "origin_kind": (string | null); "replace_reply_message_id": (string | null) }
  "RpcUndoAnchor": { "text": string; "attachments": Array<ApiSchemas["RpcUndoAttachment"]> }
  "RpcUndoAttachment": { "type": "image"; "url": string }
  "RpcVideoCompletedEvent": { "task_id": string; "session_id": (string | null); "url": string; "media": Array<ApiSchemas["RpcClientMediaEntry"]>; "warning": (string | null) }
  "RpcVideoFailedEvent": { "task_id": string; "session_id": (string | null); "error": (string | null); "status_message_id": (string | null); "status_text": (string | null) }
  "RpcVideoQueueEvent": { "task_id": string; "session_id": (string | null); "media_id": (string | null); "video_queue": (ApiSchemas["RpcVideoQueueInfo"] | null) }
  "RpcVideoQueueInfo": { "state": "waiting" | "rate_limited" | "blocked"; "next_retry_at": (string | null) }
  "RpcVisualPlayCommand": { "play_id": string; "status": "queued"; "visual_id": string; "visual_version": number; "media_kind": "image" | "video"; "media_path": string; "poster_path": (string | null); "input_image_hash": (string | null); "clip_kind": "once" | "loop"; "loop": boolean; "transition": "natural" | "fade"; "presentation_epoch": number; "state_revision": number; "source": "manual" | "autonomous" | "restore" | "remote"; "expires_at": string; "outfit": ApiSchemas["RpcComboMaterialRef"]; "scene": ApiSchemas["RpcComboMaterialRef"] }
  "RpcVisualPlaybackEvent": { "play_id": string; "status": "queued" | "claimed" | "started" | "finished" | "failed" | "expired"; "receipt_kind": ("image" | "video" | "window_static" | null) }
  "RpcVisualUpdatedEvent": { "version": number }
  "RpcVoiceBubbleView": { "type": "voice"; "text": string; "audio": (ApiSchemas["RpcReplyAudio"] | null) }
  "RpcVoiceUpdatedEvent": { "voice_id": string; "version": number }
  "RunnerInputCallCancelRequest": (ApiSchemas["RunnerInputCancelRequestTarget"] | ApiSchemas["RunnerInputCancelCallTarget"])
  "RunnerInputCallGetRequest": { "call_id": string }
  "RunnerInputCancelCallTarget": { "call_id": string }
  "RunnerInputCancelRequestTarget": { "req_id": (string | number) }
  "RunnerInputConfigReplaceRequest": { "config": { [key: string]: ApiSchemas["RunnerInputJsonValue"] } }
  "RunnerInputJsonValue": unknown
  "RunnerInputRunnerEmptyRequest": Record<string, never>
  "RunnerInputSkillScopeRequest": { "user_id": string; "system_preset_id": "companion" | "copywriter" | "language_teacher" | "general" }
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
  "SceneAction": { "capability": "scene.create"; "notes": string }
  "SceneDescriptionRequest": { "title": string; "description": string }
  "SceneGenerateRequest": { "source": "generate"; "notes"?: (string | null); "image"?: (string | null); "content_type"?: "image/png" | "image/jpeg" | "image/webp" | "image/gif" }
  "SceneImageDimensions": { "width": number; "height": number }
  "SceneImageSize": { "width": number; "height": number }
  "SceneImportRequest": { "image": string; "content_type"?: (string | null); "source": "import"; "scene_id"?: (string | null) }
  "SceneListResponse": { "scenes": Array<ApiSchemas["SceneResponse"]>; "total": (number | null); "has_more": boolean; "next_cursor": (string | null); "limit": number }
  "SceneOrigin": "onboarding" | "llm" | "nightly" | "user_request"
  "ScenePolicy": "locked" | "llm_may_replace"
  "ScenePolicyInput": "locked" | "llm_may_replace"
  "ScenePolicyRequest": { "policy": ApiSchemas["ScenePolicyInput"] }
  "ScenePolicyResponse": { "policy": ApiSchemas["ScenePolicy"] }
  "ScenePromptRequest": { "notes"?: (string | null) }
  "SceneRegenerationResponse": { "task_id": string; "status": "pending" | "ready" | "failed" | "cancelled"; "stage": string; "error": (string | null) }
  "SceneResponse": { "id": string; "status": ApiSchemas["SceneStatus"]; "stage": string; "can_retry_generation": boolean; "origin": ApiSchemas["SceneOrigin"]; "source": ApiSchemas["SceneSource"]; "target_size": (ApiSchemas["SceneImageSize"] | null); "source_size": (ApiSchemas["SceneImageDimensions"] | null); "image_size": (ApiSchemas["SceneImageDimensions"] | null); "title": string; "description": string; "requirements": string; "prompt": string; "url": string; "error": (string | null); "attempt_count": number; "requested_at": (string | null); "ready_at": (string | null); "regeneration": (ApiSchemas["SceneRegenerationResponse"] | null) }
  "SceneSource": "generated" | "user_upload"
  "SceneStateResponse": { "policy": ApiSchemas["ScenePolicy"]; "materials": Array<ApiSchemas["SceneResponse"]>; "generation_pending": boolean }
  "SceneStatus": "pending" | "ready" | "description_failed" | "failed" | "cancelled"
  "ServiceCheckResultView": { "repo": ApiSchemas["ServiceRepoView"]; "incoming": Array<ApiSchemas["ServiceCommitInfo"]>; "issues": Array<ApiSchemas["ServicePreflightIssue"]> }
  "ServiceCommitInfo": { "sha": string; "short_sha": string; "subject": string; "author": string; "committed_at": (string | null) }
  "ServicePreflightIssue": { "check": "git" | "ssh" | "docker" | "compose" | "repository" | "upstream" | "worktree" | "divergence"; "message": string }
  "ServiceRepoView": { "branch": string; "upstream": string; "current_commit": string; "current_commit_subject": string; "ahead": number; "behind": number; "dirty": boolean; "diverged": boolean; "remote_commit": string }
  "ServiceRuntimeView": { "name": string; "state": string; "health": string; "image_id": string; "running_commit": string }
  "ServiceUpdateApplyRequest": { "target_commit": string; "request_id"?: string }
  "ServiceUpdateCheckRequest": { "request_id"?: string }
  "ServiceUpdateLogChunk": { "task_id": string; "offset": number; "total_bytes": number; "truncated": boolean; "lines": Array<string> }
  "ServiceUpdateTaskAccepted": { "task_id": string; "existing": boolean }
  "ServiceUpdateTaskError": { "stage": ("preflight" | "fetch" | "fast_forward" | "build" | "restart" | "verify" | "done" | "failed" | "interrupted" | null); "reason": string; "message": string }
  "ServiceUpdateTaskList": { "tasks": Array<ApiSchemas["ServiceUpdateTaskView"]> }
  "ServiceUpdateTaskView": { "task_id": string; "request_id": string; "kind": "check" | "update"; "state": "running" | "done" | "failed" | "interrupted"; "stage": "preflight" | "fetch" | "fast_forward" | "build" | "restart" | "verify" | "done" | "failed" | "interrupted"; "target_commit": string; "created_at": string; "updated_at": string; "error": (ApiSchemas["ServiceUpdateTaskError"] | null); "log_offset": number; "log_truncated": boolean; "check": (ApiSchemas["ServiceCheckResultView"] | null) }
  "ServiceUpdaterStatus": { "enabled": boolean; "updater_ready": boolean; "version": string; "repo": ApiSchemas["ServiceRepoView"]; "runtime": (ApiSchemas["ServiceRuntimeView"] | null); "preflight_issues": Array<ApiSchemas["ServicePreflightIssue"]>; "active_task": (ApiSchemas["ServiceUpdateTaskView"] | null); "latest_task": (ApiSchemas["ServiceUpdateTaskView"] | null) }
  "SessionClearResult": { "session_id": string; "cleared_count": number; "messages": Array<ApiSchemas["SessionMessageView"]> }
  "SessionCompressionResult": { "session_id": string; "compressed": boolean; "reason": (string | null); "usage": ApiSchemas["CompressionUsage"]; "replaced_count": (number | null); "summary": (string | null); "messages": (Array<ApiSchemas["SessionMessageView"]> | null) }
  "SessionCreateRequest": { "system_preset_id": string }
  "SessionCreateResult": { "session_id": string; "info": ApiSchemas["SessionRuntimeInfo"] }
  "SessionForkRequest": { "source_message_id": string }
  "SessionHistoryResponse": { "messages": Array<ApiSchemas["SessionMessageView"]>; "next_cursor": (string | null); "has_more": boolean }
  "SessionMessageView": (ApiSchemas["PlainSessionMessageView"] | ApiSchemas["CompanionSessionMessageView"])
  "SessionRuntimeInfo": { "model": (string | null); "provider": string; "running": boolean; "system_preset_id": string; "settings": { [key: string]: unknown }; "context_window": (number | null); "kind": string; "is_automation": boolean }
  "SessionSettingsPatch": { "temperature"?: (number | null); "context_compression_threshold"?: (number | null); "reasoning_effort"?: ("none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max" | "ultra" | null) }
  "SessionSnapshotResponse": { "session_id": string; "message_count": number; "messages": Array<ApiSchemas["SessionMessageView"]>; "info": ApiSchemas["SessionRuntimeInfo"]; "current_seq": (number | null); "stream_id": (string | null); "active_turn": (ApiSchemas["ActiveTurnView"] | null); "last_submission": (ApiSchemas["LastSubmissionSnapshot"] | null); "truncated": boolean; "next_cursor": (string | null); "history_revision": number }
  "SessionUndoRequest": { "source_message_id": string; "confirmed"?: boolean }
  "SessionUndoResult": { "session_id": string; "deleted_count": number; "anchor": ApiSchemas["UndoAnchor"]; "messages": Array<ApiSchemas["SessionMessageView"]> }
  "SocialAccountList": { "accounts": Array<ApiSchemas["SocialAccountView"]> }
  "SocialAccountView": { "platform": "x" | "xiaohongshu"; "auth_kind": "cookie" | "qr"; "configured": boolean; "state": "unconfigured" | "configured" | "login_required"; "login": (ApiSchemas["SocialLoginStatus"] | null) }
  "SocialCredentialsRequest": { "credentials": string }
  "SocialLoginQrCode": { "image": string; "expires_at": string; "session_id": string }
  "SocialLoginStatus": { "session_id": string; "state": "awaiting_scan" | "sms_required" | "verifying" | "verification_required" | "logged_in" | "expired"; "expires_at": string; "verification_error": ("invalid_code" | null) }
  "SocialVerificationRequest": { "code": string }
  "SpeechToTextResponse": { "text": string }
  "StaticPresentation": { "request_id": string; "presentation_epoch": number; "state_revision": number; "life_revision": number; "outfit": (ApiSchemas["ComboMaterialRef"] | null); "scene": (ApiSchemas["ComboMaterialRef"] | null); "identity_path": (string | null); "identity_hash": (string | null); "receipt_kind": ("window_static" | "desktop_static" | null); "error": (string | null); "retry_at": (string | null) }
  "StaticReceipt": { "request_id": string; "presentation_epoch": number; "state_revision": number; "receipt_kind": "window_static" | "desktop_static"; "status": "started" | "failed"; "error"?: (string | null) }
  "TaskConfig": { "chains": { [key: string]: ApiSchemas["ChainPolicy"] } }
  "TaskConfigInput": { "chains"?: { [key: string]: ApiSchemas["ChainPolicyInput"] } }
  "TextBubble": { "type": "text"; "text": string }
  "TokenResponse": { "access_token": string; "expires_in": number; "user": ApiSchemas["UserInfo"] }
  "ToolSnapshot": { "name": (string | null); "call_id": (string | null); "status": string }
  "TopicFeedbackRequest": { "value": ("like" | "less" | null) }
  "TopicFeedbackSnapshot": { "topic_id": string; "value": ("like" | "less" | null); "updated_at": (string | null) }
  "TtsRequest": { "text"?: string; "voice"?: string; "language"?: string }
  "TurnCancelResult": { "request_id": string; "cancelled": boolean }
  "UndoAnchor": { "text": string; "attachments": Array<ApiSchemas["UndoAttachment"]> }
  "UndoAttachment": { "type": "image"; "url": string }
  "UpdateVersionItem": { "id": string; "version": string; "release_notes": string; "exe_filename": string; "exe_sha512": string; "exe_size": number; "mac_filename": (string | null); "mac_sha512": (string | null); "mac_size": (number | null); "runner_filename": (string | null); "is_active": boolean; "created_at": string; "created_by": (string | null) }
  "UpdateVersionListResponse": { "items": Array<ApiSchemas["UpdateVersionItem"]> }
  "UpdateVersionUpdate": { "release_notes"?: (string | null); "is_active"?: (boolean | null) }
  "UserBackupImportFailure": { "section": string; "count": number; "reason": string }
  "UserBackupImportResponse": { "mode": "overwrite" | "merge"; "sections": Array<string>; "imported": { [key: string]: number }; "restored_files": number; "failed": Array<ApiSchemas["UserBackupImportFailure"]> }
  "UserCreate": { "username": string; "base_url": string; "nightly_activity_enabled"?: boolean }
  "UserInfo": { "id": string; "username": string }
  "UserListResponse": { "items": Array<ApiSchemas["UserResponse"]> }
  "UserModelConfigListItem": { "user_id": string; "ai_config": ApiSchemas["AIConfigPublic"] }
  "UserModelConfigListResponse": { "items": Array<ApiSchemas["UserModelConfigListItem"]>; "ai_provider_support": { [key: string]: Array<string> }; "ai_provider_defaults": { [key: string]: { [key: string]: ApiSchemas["ProviderDefaults"] } }; "system_ai_config": ApiSchemas["AIConfigPublic"]; "ai_tasks": Array<ApiSchemas["AiTaskDefinition"]> }
  "UserModelConfigRequest": { "ai_config": ApiSchemas["AIConfigUpdate"] }
  "UserResponse": { "id": string; "username": string; "nightly_activity_enabled": boolean; "is_active": boolean; "created_at": string; "activation_code": string }
  "UserUpdate": { "nightly_activity_enabled"?: (boolean | null); "is_active"?: (boolean | null); "base_url"?: (string | null) }
  "VideoQueueAttachTask": { "expected_revision": number; "note": string; "action": "attach_task"; "task_id": string }
  "VideoQueueConfirmFinished": { "expected_revision": number; "note": string; "action": "confirm_finished"; "task_id"?: null }
  "VideoQueueInfo": { "state": "waiting" | "rate_limited" | "blocked"; "next_retry_at": (string | null) }
  "VideoQueueKind": "media" | "visuals"
  "VideoQueueLaneView": { "id": string; "provider": string; "provider_names": Array<string>; "next_retry_at": (string | null); "requests": Array<ApiSchemas["VideoQueueRequestView"]> }
  "VideoQueueList": { "queues": Array<ApiSchemas["VideoQueueLaneView"]> }
  "VideoQueueRequestView": { "id": string; "revision": number; "kind": ApiSchemas["VideoQueueKind"]; "job_id": (string | null); "user_id": (string | null); "model": string; "state": string; "cancel_requested": boolean; "provider_task_id": (string | null); "created_at": string; "updated_at": string; "error_message": (string | null) }
  "VisualAction": { "capability": "visual.generate"; "outfit_id": string; "scene_id": string; "action_id": string; "media_kind": "image" | "video"; "requirements": string }
  "VisualClaimRequest": { "client_id": string }
  "VisualClaimResponse": { "claimed": boolean }
  "VisualCreationPlan": { "activity": string; "opening_description": string; "required_props": Array<string>; "motion_requirements": string }
  "VisualDetailView": { "id": string; "media_kind": "image" | "video"; "clip_kind": "once" | "loop"; "title": string; "status": string; "media_url": string; "poster_url": (string | null); "width": number; "height": number; "duration_ms": (number | null); "version": number; "description": string; "outfit_id": (string | null); "outfit_name": string; "scene_id": (string | null); "scene_title": string; "action_id": (string | null); "action_name": (string | null); "input_image_id": (string | null); "input_image_hash": (string | null); "created_at": string; "updated_at": string; "snapshot": ApiSchemas["ComboSnapshot"]; "replaced_at_version": (number | null); "jobs": Array<ApiSchemas["VisualJobView"]> }
  "VisualItemView": { "id": string; "media_kind": "image" | "video"; "clip_kind": "once" | "loop"; "title": string; "status": string; "media_url": string; "poster_url": (string | null); "width": number; "height": number; "duration_ms": (number | null); "version": number; "description": string; "outfit_id": (string | null); "outfit_name": string; "scene_id": (string | null); "scene_title": string; "action_id": (string | null); "action_name": (string | null); "input_image_id": (string | null); "input_image_hash": (string | null); "created_at": string; "updated_at": string }
  "VisualJobCommandRequest": { "command": "pause" | "resume" | "cancel" | "retry" }
  "VisualJobListResponse": { "jobs": Array<ApiSchemas["VisualJobView"]>; "total": (number | null); "has_more": boolean; "next_cursor": (string | null); "limit": number }
  "VisualJobView": { "id": string; "purpose": string; "target_kind": "image" | "video"; "target_visual_id": (string | null); "expected_version": (number | null); "state": "queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled" | "unknown"; "stage": string; "feedback": string; "video_feedback": string; "repair_count": number; "repair_limit": number; "error": (string | null); "result_visual_id": (string | null); "result_image_id": (string | null); "video_queue": (ApiSchemas["VideoQueueInfo"] | null); "created_at": string; "updated_at": string }
  "VisualListResponse": { "visuals": Array<ApiSchemas["VisualItemView"]>; "total": (number | null); "has_more": boolean; "next_cursor": (string | null); "limit": number }
  "VisualPlayCommand": { "play_id": string; "status": "queued"; "visual_id": string; "visual_version": number; "media_kind": "image" | "video"; "media_path": string; "poster_path": (string | null); "input_image_hash": (string | null); "clip_kind": "once" | "loop"; "loop": boolean; "transition": "natural" | "fade"; "presentation_epoch": number; "state_revision": number; "source": "manual" | "autonomous" | "restore" | "remote"; "expires_at": string; "outfit": ApiSchemas["ComboMaterialRef"]; "scene": ApiSchemas["ComboMaterialRef"] }
  "VisualPreferencesRequest": { "autonomous_enabled"?: (boolean | null); "target_width"?: (number | null); "target_height"?: (number | null) }
  "VisualPresentationResponse": { "current_visual": (ApiSchemas["VisualItemView"] | null); "loop_visual": (ApiSchemas["VisualItemView"] | null); "autonomous_enabled": boolean; "target_width": (number | null); "target_height": (number | null); "presentation_epoch": number; "state_revision": number; "pending_play": (ApiSchemas["VisualPlayCommand"] | null); "static_presentation": (ApiSchemas["StaticPresentation"] | null); "actual": ApiSchemas["ActualPresentationSnapshot"] }
  "VisualReceiptRequest": { "status": "started" | "finished" | "failed" | "interrupted"; "client_id": string; "receipt_kind"?: ("image" | "video" | "window_static" | null); "error"?: (string | null) }
  "VisualReceiptResponse": { "ok": boolean }
  "VisualRegenerateRequest": { "expected_version": number; "feedback"?: (string | null); "video_feedback"?: (string | null) }
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
  "create_version": { "file": string; "release_notes"?: string }
  "import_user_backup": { "file": string }
  "speech_to_text": { "audio_file"?: (string | null) }
  "upload_chat_audio": { "file": string; "session_id": string }
  "upload_chat_video": { "file"?: (string | null); "session_id"?: string }
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
  "admin.update_user": { path: { "user_id": string }; query: Record<string, never>; body: ApiSchemas["UserUpdate"]; input: { path: { "user_id": string }; query?: never; body: ApiSchemas["UserUpdate"] }; response: ApiSchemas["UserResponse"] }
  "admin.delete_user": { path: { "user_id": string }; query: Record<string, never>; body: undefined; input: { path: { "user_id": string }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "admin.regenerate_activation": { path: { "user_id": string }; query: Record<string, never>; body: ApiSchemas["RegenerateActivationRequest"]; input: { path: { "user_id": string }; query?: never; body: ApiSchemas["RegenerateActivationRequest"] }; response: ApiSchemas["UserResponse"] }
  "admin.list_model_configs": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["UserModelConfigListResponse"] }
  "admin.runtime_info": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: { [key: string]: string } }
  "admin.get_system_settings": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: { [key: string]: ApiSchemas["JsonValue"] } }
  "admin.update_system_settings": { path: Record<string, never>; query: Record<string, never>; body: { [key: string]: ApiSchemas["JsonValueInput"] }; input: { path?: never; query?: never; body: { [key: string]: ApiSchemas["JsonValueInput"] } }; response: { [key: string]: ApiSchemas["JsonValue"] } }
  "admin.list_nightly_activity_logs": { path: Record<string, never>; query: { "user_id"?: (string | null); "target_date"?: (string | null) }; body: undefined; input: { path?: never; query?: { "user_id"?: (string | null); "target_date"?: (string | null) }; body?: never }; response: ApiSchemas["NightlyActivityLogListResponse"] }
  "admin.upsert_model_config": { path: { "user_id": string }; query: Record<string, never>; body: ApiSchemas["UserModelConfigRequest"]; input: { path: { "user_id": string }; query?: never; body: ApiSchemas["UserModelConfigRequest"] }; response: ApiSchemas["MessageResponse"] }
  "admin.delete_model_config": { path: { "user_id": string }; query: Record<string, never>; body: undefined; input: { path: { "user_id": string }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "admin.export_user_backup": { path: { "user_id": string }; query: Record<string, never>; body: undefined; input: { path: { "user_id": string }; query?: never; body?: never }; response: Uint8Array }
  "admin.import_user_backup": { path: { "user_id": string }; query: { "mode"?: "overwrite" | "merge"; "sections"?: (Array<string> | null) }; body: FormData; input: { path: { "user_id": string }; query?: { "mode"?: "overwrite" | "merge"; "sections"?: (Array<string> | null) }; body: FormData }; response: ApiSchemas["UserBackupImportResponse"] }
  "gateway.gateway_ticket": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["GatewayTicketRequest"] | null); input: { path?: never; query?: never; body?: (ApiSchemas["GatewayTicketRequest"] | null) }; response: ApiSchemas["GatewayTicketResponse"] }
  "companion.record_interaction": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["InteractionStatsRequest"]; input: { path?: never; query?: never; body: ApiSchemas["InteractionStatsRequest"] }; response: ApiSchemas["InteractionStatsResult"] }
  "companion.idle_expression": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["IdleExpressionRequest"]; input: { path?: never; query?: never; body: ApiSchemas["IdleExpressionRequest"] }; response: ApiSchemas["IdleExpressionResult"] }
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
  "companion.put_avatar_select": { path: { "avatar_id": string }; query: Record<string, never>; body: undefined; input: { path: { "avatar_id": string }; query?: never; body?: never }; response: ApiSchemas["AvatarAssetResponse"] }
  "companion.post_fullbody_reference": { path: { "avatar_id": string }; query: Record<string, never>; body: ApiSchemas["FullbodyReferenceGenerateRequest"]; input: { path: { "avatar_id": string }; query?: never; body: ApiSchemas["FullbodyReferenceGenerateRequest"] }; response: (ApiSchemas["AvatarAssetResponse"] | ApiSchemas["FullbodyCandidateResponse"]) }
  "companion.get_fullbody_candidate": { path: { "avatar_id": string }; query: Record<string, never>; body: undefined; input: { path: { "avatar_id": string }; query?: never; body?: never }; response: (ApiSchemas["FullbodyCandidateResponse"] | null) }
  "companion.post_fullbody_candidate_analyze": { path: { "avatar_id": string; "candidate_id": string }; query: Record<string, never>; body: undefined; input: { path: { "avatar_id": string; "candidate_id": string }; query?: never; body?: never }; response: ApiSchemas["FullbodyCandidateResponse"] }
  "companion.post_fullbody_candidate_accept": { path: { "avatar_id": string; "candidate_id": string }; query: Record<string, never>; body: undefined; input: { path: { "avatar_id": string; "candidate_id": string }; query?: never; body?: never }; response: ApiSchemas["AvatarAssetResponse"] }
  "companion.post_fullbody_confirm": { path: { "avatar_id": string }; query: Record<string, never>; body: ApiSchemas["FullbodyConfirmRequest"]; input: { path: { "avatar_id": string }; query?: never; body: ApiSchemas["FullbodyConfirmRequest"] }; response: ApiSchemas["AvatarAssetResponse"] }
  "companion.post_fullbody_prompt": { path: { "avatar_id": string }; query: Record<string, never>; body: ApiSchemas["FullbodyPromptRequest"]; input: { path: { "avatar_id": string }; query?: never; body?: ApiSchemas["FullbodyPromptRequest"] }; response: ApiSchemas["ImagePromptResponse"] }
  "companion.post_fullbody_adopt": { path: { "avatar_id": string }; query: Record<string, never>; body: ApiSchemas["FullbodyAdoptRequest"]; input: { path: { "avatar_id": string }; query?: never; body: ApiSchemas["FullbodyAdoptRequest"] }; response: (ApiSchemas["AvatarAssetResponse"] | ApiSchemas["FullbodyCandidateResponse"]) }
  "companion.get_outfits": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["OutfitListResponse"] }
  "companion.post_outfit": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["OutfitCreateRequest"] | ApiSchemas["OutfitAdoptRequest"]); input: { path?: never; query?: never; body: (ApiSchemas["OutfitCreateRequest"] | ApiSchemas["OutfitAdoptRequest"]) }; response: ApiSchemas["OutfitResponse"] }
  "companion.patch_outfit_policy": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["OutfitPolicyRequest"]; input: { path?: never; query?: never; body: ApiSchemas["OutfitPolicyRequest"] }; response: ApiSchemas["OutfitPolicyResponse"] }
  "companion.post_outfit_prompt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["OutfitPromptRequest"]; input: { path?: never; query?: never; body: ApiSchemas["OutfitPromptRequest"] }; response: ApiSchemas["ImagePromptResponse"] }
  "companion.post_outfit_regenerate": { path: { "outfit_id": string }; query: Record<string, never>; body: (ApiSchemas["OutfitRegenerateRequest"] | ApiSchemas["OutfitImageImportRequest"]); input: { path: { "outfit_id": string }; query?: never; body: (ApiSchemas["OutfitRegenerateRequest"] | ApiSchemas["OutfitImageImportRequest"]) }; response: ApiSchemas["OutfitResponse"] }
  "companion.post_outfit_confirm": { path: { "outfit_id": string }; query: Record<string, never>; body: undefined; input: { path: { "outfit_id": string }; query?: never; body?: never }; response: ApiSchemas["OutfitResponse"] }
  "companion.post_retry_outfit_description": { path: { "outfit_id": string }; query: Record<string, never>; body: undefined; input: { path: { "outfit_id": string }; query?: never; body?: never }; response: ApiSchemas["OutfitResponse"] }
  "companion.delete_outfit_route": { path: { "outfit_id": string }; query: Record<string, never>; body: undefined; input: { path: { "outfit_id": string }; query?: never; body?: never }; response: ApiSchemas["CompanionOperationResponse"] }
  "companion.serve_companion_asset": { path: { "user_id": string; "asset_path": string }; query: { "expires"?: (number | null); "sig"?: (string | null) }; body: undefined; input: { path: { "user_id": string; "asset_path": string }; query?: { "expires"?: (number | null); "sig"?: (string | null) }; body?: never }; response: Uint8Array }
  "companion_actions.list_actions": { path: Record<string, never>; query: { "query"?: (string | null); "clip_kind"?: ("once" | "loop" | null); "status"?: (string | null); "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body: undefined; input: { path?: never; query?: { "query"?: (string | null); "clip_kind"?: ("once" | "loop" | null); "status"?: (string | null); "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body?: never }; response: ApiSchemas["ActionListResponse"] }
  "companion_actions.create_action": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ActionAssetDesign"]; input: { path?: never; query?: never; body: ApiSchemas["ActionAssetDesign"] }; response: ApiSchemas["ActionAssetView"] }
  "companion_actions.get_action": { path: { "action_id": string }; query: Record<string, never>; body: undefined; input: { path: { "action_id": string }; query?: never; body?: never }; response: ApiSchemas["ActionAssetView"] }
  "companion_actions.update_action": { path: { "action_id": string }; query: Record<string, never>; body: ApiSchemas["ActionAssetUpdate"]; input: { path: { "action_id": string }; query?: never; body: ApiSchemas["ActionAssetUpdate"] }; response: ApiSchemas["ActionAssetView"] }
  "companion_actions.delete_action": { path: { "action_id": string }; query: Record<string, never>; body: undefined; input: { path: { "action_id": string }; query?: never; body?: never }; response: undefined }
  "companion_visuals.list_visuals": { path: Record<string, never>; query: { "query"?: (string | null); "media_kind"?: ("image" | "video" | null); "outfit_id"?: (string | null); "scene_id"?: (string | null); "action_id"?: (string | null); "source"?: (string | null); "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body: undefined; input: { path?: never; query?: { "query"?: (string | null); "media_kind"?: ("image" | "video" | null); "outfit_id"?: (string | null); "scene_id"?: (string | null); "action_id"?: (string | null); "source"?: (string | null); "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body?: never }; response: ApiSchemas["VisualListResponse"] }
  "companion_visuals.list_visual_jobs": { path: Record<string, never>; query: { "state"?: ("queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled" | "unknown" | null); "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body: undefined; input: { path?: never; query?: { "state"?: ("queued" | "running" | "paused" | "succeeded" | "failed" | "cancelled" | "unknown" | null); "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body?: never }; response: ApiSchemas["VisualJobListResponse"] }
  "companion_visuals.get_presentation": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["VisualPresentationResponse"] }
  "companion_visuals.put_presentation": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["VisualPreferencesRequest"]; input: { path?: never; query?: never; body: ApiSchemas["VisualPreferencesRequest"] }; response: ApiSchemas["VisualPresentationResponse"] }
  "companion_visuals.static_receipt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["StaticReceipt"]; input: { path?: never; query?: never; body: ApiSchemas["StaticReceipt"] }; response: ApiSchemas["VisualReceiptResponse"] }
  "companion_visuals.restore_presentation": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: (ApiSchemas["VisualPlayCommand"] | null) }
  "companion_visuals.get_visual": { path: { "visual_id": string }; query: Record<string, never>; body: undefined; input: { path: { "visual_id": string }; query?: never; body?: never }; response: ApiSchemas["VisualDetailView"] }
  "companion_visuals.regenerate_visual": { path: { "visual_id": string }; query: Record<string, never>; body: ApiSchemas["VisualRegenerateRequest"]; input: { path: { "visual_id": string }; query?: never; body: ApiSchemas["VisualRegenerateRequest"] }; response: ApiSchemas["VisualJobView"] }
  "companion_visuals.command_visual_job": { path: { "job_id": string }; query: Record<string, never>; body: ApiSchemas["VisualJobCommandRequest"]; input: { path: { "job_id": string }; query?: never; body: ApiSchemas["VisualJobCommandRequest"] }; response: ApiSchemas["VisualJobView"] }
  "companion_visuals.claim_play": { path: { "play_id": string }; query: Record<string, never>; body: ApiSchemas["VisualClaimRequest"]; input: { path: { "play_id": string }; query?: never; body: ApiSchemas["VisualClaimRequest"] }; response: ApiSchemas["VisualClaimResponse"] }
  "companion_visuals.receipt_play": { path: { "play_id": string }; query: Record<string, never>; body: ApiSchemas["VisualReceiptRequest"]; input: { path: { "play_id": string }; query?: never; body: ApiSchemas["VisualReceiptRequest"] }; response: ApiSchemas["VisualReceiptResponse"] }
  "companion_journal.get_diary": { path: Record<string, never>; query: { "from"?: (string | null); "to"?: (string | null); "limit"?: number }; body: undefined; input: { path?: never; query?: { "from"?: (string | null); "to"?: (string | null); "limit"?: number }; body?: never }; response: ApiSchemas["DiaryListResponse"] }
  "companion_journal.get_diary_unread": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["DiaryUnreadResponse"] }
  "companion_journal.read_diary": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["DiaryReadRequest"]; input: { path?: never; query?: never; body: ApiSchemas["DiaryReadRequest"] }; response: ApiSchemas["DiaryUnreadResponse"] }
  "companion_life.get_life": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["LifeStateView"] }
  "companion_life.get_life_pursuits": { path: Record<string, never>; query: { "status"?: (string | null); "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body: undefined; input: { path?: never; query?: { "status"?: (string | null); "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body?: never }; response: ApiSchemas["LifePursuitListResponse"] }
  "companion_life.get_life_pursuit": { path: { "pursuit_id": string }; query: Record<string, never>; body: undefined; input: { path: { "pursuit_id": string }; query?: never; body?: never }; response: ApiSchemas["LifePursuitDetail"] }
  "companion_life.get_life_step": { path: { "step_id": string }; query: Record<string, never>; body: undefined; input: { path: { "step_id": string }; query?: never; body?: never }; response: ApiSchemas["LifeStepView"] }
  "companion_life.get_life_events": { path: Record<string, never>; query: { "pursuit_id"?: (string | null); "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body: undefined; input: { path?: never; query?: { "pursuit_id"?: (string | null); "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body?: never }; response: ApiSchemas["LifeEventListResponse"] }
  "companion_posts.get_posts": { path: Record<string, never>; query: { "cursor"?: (string | null); "limit"?: number }; body: undefined; input: { path?: never; query?: { "cursor"?: (string | null); "limit"?: number }; body?: never }; response: ApiSchemas["PostListResponse"] }
  "companion_posts.get_posts_unread": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["PostUnreadResponse"] }
  "companion_posts.read_posts": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["PostReadRequest"]; input: { path?: never; query?: never; body: ApiSchemas["PostReadRequest"] }; response: ApiSchemas["PostUnreadResponse"] }
  "companion_posts.get_publication_recoveries": { path: Record<string, never>; query: { "state": "recoverable"; "limit"?: number; "cursor"?: (string | null) }; body: undefined; input: { path?: never; query: { "state": "recoverable"; "limit"?: number; "cursor"?: (string | null) }; body?: never }; response: ApiSchemas["PostPublicationRecoveryList"] }
  "companion_posts.get_publication_recovery": { path: { "publication_id": string }; query: Record<string, never>; body: undefined; input: { path: { "publication_id": string }; query?: never; body?: never }; response: ApiSchemas["PostPublicationRecovery"] }
  "companion_posts.query_publication_recovery": { path: { "publication_id": string }; query: Record<string, never>; body: undefined; input: { path: { "publication_id": string }; query?: never; body?: never }; response: ApiSchemas["PostPublicationRecovery"] }
  "companion_posts.discard_publication": { path: { "publication_id": string }; query: Record<string, never>; body: undefined; input: { path: { "publication_id": string }; query?: never; body?: never }; response: ApiSchemas["PostPublicationResult"] }
  "companion_posts.get_post_detail": { path: { "post_id": string }; query: Record<string, never>; body: undefined; input: { path: { "post_id": string }; query?: never; body?: never }; response: ApiSchemas["PostResponse"] }
  "companion_posts.post_comment": { path: { "post_id": string }; query: Record<string, never>; body: ApiSchemas["PostCommentCreateRequest"]; input: { path: { "post_id": string }; query?: never; body: ApiSchemas["PostCommentCreateRequest"] }; response: ApiSchemas["PostCommentResponse"] }
  "companion_posts.get_comments": { path: { "post_id": string }; query: { "cursor"?: (string | null); "limit"?: number }; body: undefined; input: { path: { "post_id": string }; query?: { "cursor"?: (string | null); "limit"?: number }; body?: never }; response: ApiSchemas["PostCommentListResponse"] }
  "companion_posts.remove_comment": { path: { "post_id": string; "comment_id": string }; query: Record<string, never>; body: undefined; input: { path: { "post_id": string; "comment_id": string }; query?: never; body?: never }; response: undefined }
  "companion_posts.retry_comment_reply": { path: { "post_id": string; "comment_id": string }; query: Record<string, never>; body: undefined; input: { path: { "post_id": string; "comment_id": string }; query?: never; body?: never }; response: ApiSchemas["PostCommentResponse"] }
  "companion_scenes.read_scene_state": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["SceneStateResponse"] }
  "companion_scenes.create_scene": { path: Record<string, never>; query: Record<string, never>; body: (ApiSchemas["SceneGenerateRequest"] | ApiSchemas["SceneImportRequest"]); input: { path?: never; query?: never; body: (ApiSchemas["SceneGenerateRequest"] | ApiSchemas["SceneImportRequest"]) }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.scene_list_route": { path: Record<string, never>; query: { "q"?: string; "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body: undefined; input: { path?: never; query?: { "q"?: string; "cursor"?: (string | null); "include_total"?: boolean; "limit"?: number }; body?: never }; response: ApiSchemas["SceneListResponse"] }
  "companion_scenes.post_scene_prompt": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ScenePromptRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ScenePromptRequest"] }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.post_scene_discard": { path: { "scene_id": string }; query: Record<string, never>; body: undefined; input: { path: { "scene_id": string }; query?: never; body?: never }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.post_scene_regenerate": { path: { "scene_id": string }; query: Record<string, never>; body: undefined; input: { path: { "scene_id": string }; query?: never; body?: never }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.delete_scene_route": { path: { "scene_id": string }; query: Record<string, never>; body: undefined; input: { path: { "scene_id": string }; query?: never; body?: never }; response: ApiSchemas["CompanionOperationResponse"] }
  "companion_scenes.scene_detail_route": { path: { "scene_id": string }; query: Record<string, never>; body: undefined; input: { path: { "scene_id": string }; query?: never; body?: never }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.scene_edit_route": { path: { "scene_id": string }; query: Record<string, never>; body: ApiSchemas["SceneDescriptionRequest"]; input: { path: { "scene_id": string }; query?: never; body: ApiSchemas["SceneDescriptionRequest"] }; response: ApiSchemas["SceneResponse"] }
  "companion_scenes.patch_scene_policy": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ScenePolicyRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ScenePolicyRequest"] }; response: ApiSchemas["ScenePolicyResponse"] }
  "companion_scenes.scene_analyze_route": { path: { "scene_id": string }; query: Record<string, never>; body: undefined; input: { path: { "scene_id": string }; query?: never; body?: never }; response: ApiSchemas["SceneResponse"] }
  "companion_topics.read_feedback": { path: Record<string, never>; query: { "topic_ids"?: Array<string> }; body: undefined; input: { path?: never; query?: { "topic_ids"?: Array<string> }; body?: never }; response: Array<ApiSchemas["TopicFeedbackSnapshot"]> }
  "companion_topics.update_feedback": { path: { "topic_id": string }; query: Record<string, never>; body: ApiSchemas["TopicFeedbackRequest"]; input: { path: { "topic_id": string }; query?: never; body: ApiSchemas["TopicFeedbackRequest"] }; response: ApiSchemas["TopicFeedbackSnapshot"] }
  "resource_catalog.get_presets": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["PromptPresetListResponse"] }
  "resource_catalog.get_commands": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["CommandListResponse"] }
  "resource_catalog.get_memories": { path: Record<string, never>; query: { "system_preset_id": string; "kind"?: (string | null); "status"?: "active" | "candidate" | "invalidated" | "expired"; "tag"?: (string | null); "q"?: (string | null); "limit"?: number; "cursor"?: (string | null); "include_total"?: boolean }; body: undefined; input: { path?: never; query: { "system_preset_id": string; "kind"?: (string | null); "status"?: "active" | "candidate" | "invalidated" | "expired"; "tag"?: (string | null); "q"?: (string | null); "limit"?: number; "cursor"?: (string | null); "include_total"?: boolean }; body?: never }; response: ApiSchemas["MemoryListResponse"] }
  "resource_catalog.patch_memory_profile": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["MemoryProfileRequest"]; input: { path?: never; query?: never; body: ApiSchemas["MemoryProfileRequest"] }; response: ApiSchemas["MemoryProfileResponse"] }
  "resource_catalog.get_memory": { path: { "memory_id": string }; query: { "system_preset_id": string }; body: undefined; input: { path: { "memory_id": string }; query: { "system_preset_id": string }; body?: never }; response: ApiSchemas["MemoryDetail"] }
  "resource_catalog.patch_memory": { path: { "memory_id": string }; query: Record<string, never>; body: ApiSchemas["MemoryUpdateRequest"]; input: { path: { "memory_id": string }; query?: never; body: ApiSchemas["MemoryUpdateRequest"] }; response: ApiSchemas["MemoryListItem"] }
  "resource_catalog.delete_memory": { path: { "memory_id": string }; query: { "system_preset_id": string }; body: undefined; input: { path: { "memory_id": string }; query: { "system_preset_id": string }; body?: never }; response: ApiSchemas["MemoryDeleteResponse"] }
  "voices.list_voices": { path: Record<string, never>; query: { "language"?: (string | null) }; body: undefined; input: { path?: never; query?: { "language"?: (string | null) }; body?: never }; response: ApiSchemas["VoicesListResponse"] }
  "voices.get_current_voice": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["CurrentVoiceResponse"] }
  "voices.put_current_voice": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["CurrentVoiceRequest"]; input: { path?: never; query?: never; body: ApiSchemas["CurrentVoiceRequest"] }; response: ApiSchemas["CurrentVoiceResponse"] }
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
  "remote.pairing_status": { path: { "pairing_id": string }; query: Record<string, never>; body: undefined; input: { path: { "pairing_id": string }; query?: never; body?: never }; response: ApiSchemas["PairingResponse"] }
  "remote.cancel_pairing": { path: { "pairing_id": string }; query: Record<string, never>; body: undefined; input: { path: { "pairing_id": string }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "remote.exchange_pairing": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ExchangeRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ExchangeRequest"] }; response: ApiSchemas["RemoteSessionResponse"] }
  "remote.current_remote_session": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["RemoteSessionResponse"] }
  "remote.logout_remote_session": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "remote.list_remote_devices": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["DeviceListResponse"] }
  "remote.revoke_all_remote_devices": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "remote.revoke_remote_device": { path: { "device_id": string }; query: Record<string, never>; body: undefined; input: { path: { "device_id": string }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "sessions.submit_turn": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["PromptSubmitRequest"]; input: { path: { "session_id": string }; query?: never; body: ApiSchemas["PromptSubmitRequest"] }; response: ApiSchemas["PromptSubmitResult"] }
  "sessions.get_turn": { path: { "session_id": string; "request_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string; "request_id": string }; query?: never; body?: never }; response: ApiSchemas["PromptSubmitResult"] }
  "sessions.cancel_turn": { path: { "session_id": string; "request_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string; "request_id": string }; query?: never; body?: never }; response: ApiSchemas["TurnCancelResult"] }
  "sessions.snapshot": { path: { "session_id": string }; query: { "stream_id"?: (string | null) }; body: undefined; input: { path: { "session_id": string }; query?: { "stream_id"?: (string | null) }; body?: never }; response: ApiSchemas["SessionSnapshotResponse"] }
  "sessions.undo": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["SessionUndoRequest"]; input: { path: { "session_id": string }; query?: never; body: ApiSchemas["SessionUndoRequest"] }; response: ApiSchemas["SessionUndoResult"] }
  "sessions.compress": { path: { "session_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string }; query?: never; body?: never }; response: ApiSchemas["SessionCompressionResult"] }
  "sessions.command": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["CommandDispatchRequest"]; input: { path: { "session_id": string }; query?: never; body: ApiSchemas["CommandDispatchRequest"] }; response: ApiSchemas["CommandDispatchResult"] }
  "sessions.retry_voice_bubble": { path: { "message_id": string; "bubble_index": number }; query: Record<string, never>; body: undefined; input: { path: { "message_id": string; "bubble_index": number }; query?: never; body?: never }; response: ApiSchemas["VoiceBubbleView"] }
  "sessions.list_sessions": { path: Record<string, never>; query: { "limit"?: number; "cursor"?: (string | null); "include_total"?: boolean; "min_messages"?: number; "archived"?: "only" | "exclude" | "include"; "order"?: "recent" | "created" | "messages"; "include_subagents"?: boolean; "q"?: (string | null) }; body: undefined; input: { path?: never; query?: { "limit"?: number; "cursor"?: (string | null); "include_total"?: boolean; "min_messages"?: number; "archived"?: "only" | "exclude" | "include"; "order"?: "recent" | "created" | "messages"; "include_subagents"?: boolean; "q"?: (string | null) }; body?: never }; response: ApiSchemas["DesktopSessionListResponse"] }
  "sessions.create_session": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["SessionCreateRequest"]; input: { path?: never; query?: never; body: ApiSchemas["SessionCreateRequest"] }; response: ApiSchemas["SessionCreateResult"] }
  "sessions.get_main_session": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["SessionCreateResult"] }
  "sessions.get_session": { path: { "session_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string }; query?: never; body?: never }; response: ApiSchemas["SessionCreateResult"] }
  "sessions.patch_session": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["DesktopSessionPatchRequest"]; input: { path: { "session_id": string }; query?: never; body: ApiSchemas["DesktopSessionPatchRequest"] }; response: ApiSchemas["SessionCreateResult"] }
  "sessions.delete_session": { path: { "session_id": string }; query: Record<string, never>; body: undefined; input: { path: { "session_id": string }; query?: never; body?: never }; response: ApiSchemas["DesktopSessionOperationResponse"] }
  "sessions.fork_session": { path: { "session_id": string }; query: Record<string, never>; body: ApiSchemas["SessionForkRequest"]; input: { path: { "session_id": string }; query?: never; body: ApiSchemas["SessionForkRequest"] }; response: ApiSchemas["SessionCreateResult"] }
  "sessions.get_session_history": { path: { "session_id": string }; query: { "before_id"?: (string | null); "limit"?: number }; body: undefined; input: { path: { "session_id": string }; query?: { "before_id"?: (string | null); "limit"?: number }; body?: never }; response: ApiSchemas["SessionHistoryResponse"] }
  "social_accounts.list_social_accounts": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["SocialAccountList"] }
  "social_accounts.put_social_credentials": { path: { "platform": "x" | "xiaohongshu" }; query: Record<string, never>; body: ApiSchemas["SocialCredentialsRequest"]; input: { path: { "platform": "x" | "xiaohongshu" }; query?: never; body: ApiSchemas["SocialCredentialsRequest"] }; response: ApiSchemas["SocialAccountView"] }
  "social_accounts.clear_social_account": { path: { "platform": "x" | "xiaohongshu" }; query: Record<string, never>; body: undefined; input: { path: { "platform": "x" | "xiaohongshu" }; query?: never; body?: never }; response: ApiSchemas["SocialAccountView"] }
  "social_accounts.start_social_login": { path: { "platform": "x" | "xiaohongshu" }; query: Record<string, never>; body: undefined; input: { path: { "platform": "x" | "xiaohongshu" }; query?: never; body?: never }; response: ApiSchemas["SocialLoginQrCode"] }
  "social_accounts.get_social_login": { path: { "platform": "x" | "xiaohongshu"; "session_id": string }; query: Record<string, never>; body: undefined; input: { path: { "platform": "x" | "xiaohongshu"; "session_id": string }; query?: never; body?: never }; response: ApiSchemas["SocialLoginStatus"] }
  "social_accounts.cancel_social_login": { path: { "platform": "x" | "xiaohongshu"; "session_id": string }; query: Record<string, never>; body: undefined; input: { path: { "platform": "x" | "xiaohongshu"; "session_id": string }; query?: never; body?: never }; response: ApiSchemas["SocialAccountView"] }
  "social_accounts.verify_social_login": { path: { "platform": "x" | "xiaohongshu"; "session_id": string }; query: Record<string, never>; body: ApiSchemas["SocialVerificationRequest"]; input: { path: { "platform": "x" | "xiaohongshu"; "session_id": string }; query?: never; body: ApiSchemas["SocialVerificationRequest"] }; response: ApiSchemas["SocialLoginStatus"] }
  "update.get_latest_yml": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["ReleaseManifestResponse"] }
  "update.get_latest_mac_yml": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["ReleaseManifestResponse"] }
  "update.get_latest_runner_yml": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: Uint8Array }
  "update.get_latest_file": { path: { "filename": string }; query: Record<string, never>; body: undefined; input: { path: { "filename": string }; query?: never; body?: never }; response: Uint8Array }
  "update.list_versions": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["UpdateVersionListResponse"] }
  "update.create_version": { path: Record<string, never>; query: Record<string, never>; body: FormData; input: { path?: never; query?: never; body: FormData }; response: ApiSchemas["UpdateVersionItem"] }
  "update.update_version": { path: { "id": string }; query: Record<string, never>; body: ApiSchemas["UpdateVersionUpdate"]; input: { path: { "id": string }; query?: never; body: ApiSchemas["UpdateVersionUpdate"] }; response: ApiSchemas["UpdateVersionItem"] }
  "update.delete_version": { path: { "id": string }; query: Record<string, never>; body: undefined; input: { path: { "id": string }; query?: never; body?: never }; response: ApiSchemas["MessageResponse"] }
  "update.get_service_updater_status": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["ServiceUpdaterStatus"] }
  "update.start_service_update_check": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ServiceUpdateCheckRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ServiceUpdateCheckRequest"] }; response: ApiSchemas["ServiceUpdateTaskAccepted"] }
  "update.apply_service_update": { path: Record<string, never>; query: Record<string, never>; body: ApiSchemas["ServiceUpdateApplyRequest"]; input: { path?: never; query?: never; body: ApiSchemas["ServiceUpdateApplyRequest"] }; response: ApiSchemas["ServiceUpdateTaskAccepted"] }
  "update.list_service_update_tasks": { path: Record<string, never>; query: Record<string, never>; body: undefined; input: { path?: never; query?: never; body?: never }; response: ApiSchemas["ServiceUpdateTaskList"] }
  "update.get_service_update_task": { path: { "task_id": string }; query: Record<string, never>; body: undefined; input: { path: { "task_id": string }; query?: never; body?: never }; response: ApiSchemas["ServiceUpdateTaskView"] }
  "update.get_service_update_task_log": { path: { "task_id": string }; query: { "offset"?: number }; body: undefined; input: { path: { "task_id": string }; query?: { "offset"?: number }; body?: never }; response: ApiSchemas["ServiceUpdateLogChunk"] }
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
  "companion.action.updated": ApiSchemas["RpcEmptyResult"]
  "companion.character_card.updated": ApiSchemas["RpcCharacterCardEvent"]
  "companion.diary.created": ApiSchemas["RpcDiaryEntryResponse"]
  "companion.diary.deleted": ApiSchemas["RpcDiaryDeletedEvent"]
  "companion.diary.read": ApiSchemas["RpcEmptyResult"]
  "companion.life.updated": ApiSchemas["RpcLifeUpdatedEvent"]
  "companion.message": ApiSchemas["RpcCompanionMessageEvent"]
  "companion.mood": ApiSchemas["RpcMoodEvent"]
  "companion.outfit.updated": ApiSchemas["RpcOutfitChangedEvent"]
  "companion.post.comment": ApiSchemas["RpcPostCommentEvent"]
  "companion.post.comment.deleted": ApiSchemas["RpcPostCommentDeletedEvent"]
  "companion.post.created": ApiSchemas["RpcPostResponse"]
  "companion.posts.read": ApiSchemas["RpcEmptyResult"]
  "companion.scene.updated": ApiSchemas["RpcSceneChangedEvent"]
  "companion.topic_feedback.updated": ApiSchemas["RpcTopicFeedbackSnapshot"]
  "companion.visual.initial_requested": ApiSchemas["RpcEmptyResult"]
  "companion.visual.play": ApiSchemas["RpcVisualPlayCommand"]
  "companion.visual.playback": ApiSchemas["RpcVisualPlaybackEvent"]
  "companion.visual.updated": ApiSchemas["RpcVisualUpdatedEvent"]
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
  "companion_actions.list_actions": {
    "path": "/api/companion/actions",
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
  "companion_actions.create_action": {
    "path": "/api/companion/actions",
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
  "companion_actions.get_action": {
    "path": "/api/companion/actions/{action_id}",
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
  "companion_actions.update_action": {
    "path": "/api/companion/actions/{action_id}",
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
  "companion_actions.delete_action": {
    "path": "/api/companion/actions/{action_id}",
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
  "companion_visuals.list_visuals": {
    "path": "/api/companion/visuals",
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
  "companion_visuals.list_visual_jobs": {
    "path": "/api/companion/visuals/jobs",
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
  "companion_visuals.get_presentation": {
    "path": "/api/companion/visuals/presentation",
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
  "companion_visuals.put_presentation": {
    "path": "/api/companion/visuals/presentation",
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
  "companion_visuals.static_receipt": {
    "path": "/api/companion/visuals/presentation/static-receipt",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_visuals.restore_presentation": {
    "path": "/api/companion/visuals/presentation/restore",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_visuals.get_visual": {
    "path": "/api/companion/visuals/{visual_id}",
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
  "companion_visuals.regenerate_visual": {
    "path": "/api/companion/visuals/{visual_id}/regenerate",
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
  "companion_visuals.command_visual_job": {
    "path": "/api/companion/visuals/jobs/{job_id}/commands",
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
  "companion_visuals.claim_play": {
    "path": "/api/companion/visuals/plays/{play_id}/claim",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "companion_visuals.receipt_play": {
    "path": "/api/companion/visuals/plays/{play_id}/receipt",
    "method": "POST",
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
  "companion_life.get_life": {
    "path": "/api/companion/life",
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
  "companion_life.get_life_pursuits": {
    "path": "/api/companion/life/pursuits",
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
  "companion_life.get_life_pursuit": {
    "path": "/api/companion/life/pursuits/{pursuit_id}",
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
  "companion_life.get_life_step": {
    "path": "/api/companion/life/steps/{step_id}",
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
  "companion_life.get_life_events": {
    "path": "/api/companion/life/events",
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
  "companion_posts.discard_publication": {
    "path": "/api/companion/posts/publications/{publication_id}/discard",
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
  "companion_posts.get_comments": {
    "path": "/api/companion/posts/{post_id}/comments",
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
  "resource_catalog.get_memory": {
    "path": "/api/memories/{memory_id}",
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
      "desktop",
      "remote"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "voices.get_current_voice": {
    "path": "/api/voices/current",
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
  "voices.put_current_voice": {
    "path": "/api/voices/current",
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
  "voices.post_voice_match": {
    "path": "/api/voices/match",
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
  "voices.post_voice_candidate": {
    "path": "/api/voices/candidates",
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
  "voices.post_voice_prompt": {
    "path": "/api/voices/prompt",
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
  "voices.post_voice_confirmation": {
    "path": "/api/voices/candidates/{candidate_id}/confirmation",
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
  "voices.delete_voice_operation": {
    "path": "/api/voices/operations/{operation_id}",
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
      "desktop",
      "remote"
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
  "social_accounts.list_social_accounts": {
    "path": "/api/social-accounts",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "social_accounts.put_social_credentials": {
    "path": "/api/social-accounts/{platform}/credentials",
    "method": "PUT",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "social_accounts.clear_social_account": {
    "path": "/api/social-accounts/{platform}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "social_accounts.start_social_login": {
    "path": "/api/social-accounts/{platform}/login",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "social_accounts.get_social_login": {
    "path": "/api/social-accounts/{platform}/login/{session_id}",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "social_accounts.cancel_social_login": {
    "path": "/api/social-accounts/{platform}/login/{session_id}",
    "method": "DELETE",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
    ],
    "wait_for_model": false,
    "ipc": true
  },
  "social_accounts.verify_social_login": {
    "path": "/api/social-accounts/{platform}/login/{session_id}/verification",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "desktop"
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
  "update.get_service_updater_status": {
    "path": "/api/admin/updates/service/status",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.start_service_update_check": {
    "path": "/api/admin/updates/service/check",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.apply_service_update": {
    "path": "/api/admin/updates/service/apply",
    "method": "POST",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.list_service_update_tasks": {
    "path": "/api/admin/updates/service/tasks",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.get_service_update_task": {
    "path": "/api/admin/updates/service/tasks/{task_id}",
    "method": "GET",
    "encoding": "json",
    "response": "json",
    "identities": [
      "admin"
    ],
    "wait_for_model": false,
    "ipc": false
  },
  "update.get_service_update_task_log": {
    "path": "/api/admin/updates/service/tasks/{task_id}/log",
    "method": "GET",
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
  "companion.action.updated": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.character_card.updated": {
    "audience": "all",
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
  "companion.life.updated": {
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
  "companion.visual.initial_requested": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.visual.play": {
    "audience": "desktop",
    "snapshot": false,
    "recovery": "transient",
    "baseline": false
  },
  "companion.visual.playback": {
    "audience": "all",
    "snapshot": false,
    "recovery": "resource",
    "baseline": false
  },
  "companion.visual.updated": {
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
export const SYSTEM_PRESET_IDS = ["companion", "copywriter", "language_teacher", "general"] as const
export const SOCIAL_PLATFORMS = {
  "x": {
    "auth_kind": "cookie",
    "hosts": [
      "x.com",
      "www.x.com",
      "twitter.com",
      "www.twitter.com"
    ],
    "labels": {
      "zh": "X",
      "en": "X"
    }
  },
  "xiaohongshu": {
    "auth_kind": "qr",
    "hosts": [
      "xiaohongshu.com",
      "www.xiaohongshu.com"
    ],
    "labels": {
      "zh": "小红书",
      "en": "Xiaohongshu"
    }
  }
} as const
