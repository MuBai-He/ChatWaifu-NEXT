#![cfg(target_os = "windows")]

use chatwaifu_desktop_host::windows_current_process_image_path;

#[test]
fn query_full_process_image_name_returns_the_real_test_process_image() {
    let queried = windows_current_process_image_path()
        .expect("QueryFullProcessImageNameW should locate the current process image");
    let standard = std::env::current_exe().expect("std::env::current_exe should locate the test");

    assert!(queried.is_absolute(), "queried path: {}", queried.display());
    assert!(queried.is_file(), "queried path: {}", queried.display());
    assert_eq!(
        queried
            .canonicalize()
            .expect("canonicalize queried image path"),
        standard
            .canonicalize()
            .expect("canonicalize standard image path")
    );
}
