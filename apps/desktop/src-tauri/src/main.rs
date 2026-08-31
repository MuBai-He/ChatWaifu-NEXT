#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if let Some(exit_code) = chatwaifu_desktop_host::runtime_supervisor_exit_code() {
        std::process::exit(exit_code);
    }
    chatwaifu_desktop_host::run();
}
