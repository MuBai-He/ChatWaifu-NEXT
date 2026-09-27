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
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        cc::Build::new()
            .file("src/apple_eventkit.m")
            .flag("-fobjc-arc")
            .flag("-fblocks")
            .compile("cw_apple");
        println!("cargo:rustc-link-lib=framework=EventKit");
        println!("cargo:rustc-link-lib=framework=Foundation");
        println!("cargo:rustc-link-lib=framework=AppKit");
        println!("cargo:rerun-if-changed=src/apple_eventkit.m");
    }
    tauri_build::build();
}
