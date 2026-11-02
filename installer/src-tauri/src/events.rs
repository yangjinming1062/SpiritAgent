//! Rust → React 的事件流；Tauri 通道 `bootstrap`，由 `type` 字段派发。

use serde::{Deserialize, Serialize};

/// 由 `install 脚本 -Manifest` 报告的阶段定义；`title` 直接用于 UI 展示。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StageInfo {
    pub name: String,
    pub title: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Manifest {
    pub stages: Vec<StageInfo>,
    pub protocol_version: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum StageState {
    Running,
    Succeeded,
    Skipped,
    Failed,
}

// 标识来源管道；uv/pip 等会向 stderr 写正常进度，不可误标为错误。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum LogStream {
    Stdout,
    Stderr,
}

/// 统一经 `bootstrap` 通道发送；由 `type` 标签派发。
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum BootstrapEvent {
    /// 启动时一次，附带完整阶段列表。
    Manifest { stages: Vec<StageInfo> },
    /// 阶段状态切换；`durationMs` 仅终态填充。
    Stage {
        name: String,
        state: StageState,
        #[serde(rename = "durationMs", skip_serializing_if = "Option::is_none")]
        duration_ms: Option<u64>,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },
    /// 脚本 stdout/stderr 原始行。
    Log { line: String, stream: LogStream },
    /// 全部阶段成功后一次。
    Complete,
    /// 流程中止时一次。
    Failed { error: String },
}
