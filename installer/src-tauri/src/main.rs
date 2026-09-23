// 进程入口；逻辑在 lib.rs 以便以库形式单测。
// windows_subsystem 必须写在二进制 crate，写在 lib 不会作用到链接期。
// debug 构建保留控制台便于 `cargo tauri dev` 查看 tracing。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    spiritagent_bootstrap_lib::run()
}
