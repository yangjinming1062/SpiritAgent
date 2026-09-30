// 逻辑在 lib.rs 以便库单测；windows_subsystem 只对二进制 crate 生效，debug 保留控制台。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    spiritagent_bootstrap_lib::run()
}
