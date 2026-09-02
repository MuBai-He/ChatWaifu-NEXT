fn main() {
    let placeholders = [
        "../../../dist/macos/runtime-sidecar",
        "../../../dist/windows/runtime-sidecar",
        "../../../build/windows-installer/resources",
    ];
    for path in placeholders {
        let dir = std::path::Path::new(path);
        if !dir.exists() {
            let _ = std::fs::create_dir_all(dir);
        }
    }
    tauri_build::build();
}
