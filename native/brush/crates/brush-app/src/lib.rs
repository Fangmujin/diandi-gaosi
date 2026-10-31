#![recursion_limit = "256"]
#![cfg(target_os = "android")]

use brush_ui::UiMode;
use brush_ui::app::App;
use brush_ui::ui_process::UiProcess;
use jni::sys::{JNI_VERSION_1_6, jint};
use std::os::raw::c_void;
use std::sync::Arc;

// Bundled CJK font (SimHei): the Android runtime provides no usable system
// CJK fallback for egui here, so embed the font and register it with egui.
const CJK_FONT: &[u8] = include_bytes!("../assets/fonts/simhei.ttf");

#[allow(non_snake_case)]
#[unsafe(no_mangle)]
pub extern "system" fn JNI_OnLoad(_: jni::JavaVM, _: *mut c_void) -> jint {
    // Do NOT initialize the picker bridge here: JNI_OnLoad can run on a
    // background thread before the app ClassLoader is ready, and a panic here
    // aborts the whole process. android_main performs the real initialization
    // once the activity is up.
    JNI_VERSION_1_6
}

#[unsafe(no_mangle)]
fn android_main(app: winit::platform::android::activity::AndroidApp) {
    // Emulator gfxstream Vulkan (both SwiftShader and host NVIDIA paths) crashes
    // inside on_vkQueueSubmit, so use the GL backend. With host GPU passthrough
    // the emulator exposes GLES 3.1 (SSBO supported), which makes cubecl/burn
    // work. On a real phone GLES 3.1/3.2 also supports SSBO, so this stays valid.
    unsafe { std::env::set_var("WGPU_BACKEND", "gl"); }

    // NativeActivity does NOT always call JNI_OnLoad; grab the JavaVM and the
    // activity object ourselves so the file picker JNI bridge is always
    // initialized with the correct (app) ClassLoader.
    rrfd::android::jni_initialize(app.vm_as_ptr() as *mut jni::sys::JavaVM, app.activity_as_ptr());

    let context = Arc::new(UiProcess::new());

    let wgpu_options = brush_ui::create_egui_options();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .unwrap();

    runtime.block_on(async {
        android_logger::init_once(
            android_logger::Config::default().with_max_level(log::LevelFilter::Info),
        );
        eframe::run_native(
            "Brush",
            eframe::NativeOptions {
                // Build app display.
                viewport: egui::ViewportBuilder::default(),
                android_app: Some(app),
                wgpu_options,
                ..Default::default()
            },
            Box::new(|cc| {
                let mut fonts = egui::FontDefinitions::default();
                fonts
                    .font_data
                    .insert("cjk".into(), egui::FontData::from_owned(CJK_FONT.to_vec()).into());
                fonts
                    .families
                    .entry(egui::FontFamily::Proportional)
                    .or_default()
                    .insert(0, "cjk".into());
                fonts
                    .families
                    .entry(egui::FontFamily::Monospace)
                    .or_default()
                    .push("cjk".into());
                cc.egui_ctx.set_fonts(fonts);
                Ok(Box::new(App::new(cc, context)))
            }),
        )
        .unwrap();
    });
}
