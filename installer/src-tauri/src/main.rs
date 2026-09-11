// 进程入口；逻辑均在 lib.rs 中以便以库形式单测。
// windows_subsystem 必须置于二进制 crate——之前写在 lib 上导致 release 版残留 cmd 窗口。
// debug_assertions 仅在 release 构建中剥离控制台分配，便于 `cargo tauri dev` 时看到 tracing 输出。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    spiritagent_bootstrap_lib::run()
}
