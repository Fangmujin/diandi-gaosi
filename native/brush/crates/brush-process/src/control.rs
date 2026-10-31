//! Control file protocol for headless runs.
//!
//! The host app (an Android APK spawning this binary as a subprocess) steers a
//! running training session by dropping plain files into a control directory.
//! No sockets or signals are involved, which keeps the contract trivially
//! implementable from Java/Kotlin:
//!
//! * `<dir>/stop`  -> finish the current step, export the current splats, exit.
//! * `<dir>/pause` -> stop stepping until the file disappears (or `stop` shows up).
//!
//! When no control directory is set every predicate is false, so the training
//! loop behaves exactly like upstream brush.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

static CONTROL_DIR: OnceLock<PathBuf> = OnceLock::new();

/// Set the directory that is watched for `stop` / `pause` files.
///
/// Only honored once; later calls are ignored.
pub fn set_control_dir(dir: impl AsRef<Path>) {
    let _ = CONTROL_DIR.set(dir.as_ref().to_path_buf());
}

fn flag_file_present(name: &str) -> bool {
    CONTROL_DIR
        .get()
        .is_some_and(|dir| dir.join(name).is_file())
}

/// True when the host asked the run to finish early.
pub fn should_stop() -> bool {
    flag_file_present("stop")
}

/// True when the host asked the run to hold until resumed.
pub fn should_pause() -> bool {
    flag_file_present("pause")
}
