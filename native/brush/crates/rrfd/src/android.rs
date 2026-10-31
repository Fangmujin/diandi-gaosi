use jni::JNIEnv;
use jni::objects::{GlobalRef, JClass, JObject, JStaticMethodID, JValue};
use jni::signature::Primitive;
use jni::sys::jint;
use lazy_static::lazy_static;
use std::os::fd::FromRawFd;
use std::os::raw::c_void;
use std::sync::Arc;
use std::sync::RwLock;
use std::sync::atomic::{AtomicPtr, Ordering};
use tokio::fs::File;
use tokio::sync::mpsc::Sender;

lazy_static! {
    static ref CHANNEL: RwLock<Option<Sender<Option<File>>>> = RwLock::new(None);
    static ref START_FILE_PICKER: RwLock<Option<JStaticMethodID>> = RwLock::new(None);
    static ref FILE_PICKER_CLASS: RwLock<Option<GlobalRef>> = RwLock::new(None);
    // Auto-load path driven by the launching Intent extra ("marvis_auto_path").
    // Used by the emulator self-check mode to bypass the (broken) touch input.
    static ref AUTO_PATH: RwLock<Option<String>> = RwLock::new(None);
}

/// Returns the auto-load path configured through the Intent extra, if any.
#[allow(unused)]
pub fn auto_path() -> Option<String> {
    AUTO_PATH.read().expect("AUTO_PATH poisoned").clone()
}

static VM: AtomicPtr<jni::sys::JavaVM> = AtomicPtr::new(std::ptr::null_mut());

#[allow(unused)]
pub fn jni_initialize(raw_vm: *mut jni::sys::JavaVM, raw_activity: *mut c_void) {
    if raw_vm.is_null() {
        eprintln!("RRFD_JNI_INIT_SKIP null vm");
        return;
    }
    // Store the VM first so pick_file can always attach, even if class lookup
    // fails below.
    VM.store(raw_vm, Ordering::Release);
    let vm = match unsafe { jni::JavaVM::from_raw(raw_vm) } {
        Ok(v) => v,
        Err(e) => {
            eprintln!("RRFD_JNI_INIT_ERR vm: {:?}", e);
            return;
        }
    };
    let mut env = match vm.get_env() {
        Ok(e) => e,
        Err(e) => {
            eprintln!("RRFD_JNI_INIT_ERR env: {:?}", e);
            return;
        }
    };
    // Clear any pre-existing pending exception to avoid polluting later JNI
    // calls (a stale pending exception aborts the render thread).
    let _ = env.exception_clear();

    // Resolve FilePicker through the app ClassLoader obtained from the
    // Application object. FindClass() uses the thread-context class loader
    // (not wired up this early), and the NativeActivity object itself is a
    // framework class whose getClassLoader() returns the boot loader; the
    // Application instance (com.splats.app.App) is a dex class loaded by the
    // app class loader, so that is the one we must use.
    if raw_activity.is_null() {
        eprintln!("RRFD_JNI_INIT_ERR activity null");
        return;
    }
    let activity = unsafe { JObject::from_raw(raw_activity as jni::sys::jobject) };
    let application = match env.call_method(&activity, "getApplication", "()Landroid/app/Application;", &[]) {
        Ok(v) => v.l().unwrap_or_else(|_| JObject::null()),
        Err(e) => {
            eprintln!("RRFD_JNI_INIT_ERR getApplication: {:?}", e);
            let _ = env.exception_clear();
            return;
        }
    };
    if application.is_null() {
        eprintln!("RRFD_JNI_INIT_ERR application null");
        return;
    }
    let class_loader = match env.call_method(&application, "getClassLoader", "()Ljava/lang/ClassLoader;", &[]) {
        Ok(v) => v.l().unwrap_or_else(|_| JObject::null()),
        Err(e) => {
            eprintln!("RRFD_JNI_INIT_ERR getClassLoader: {:?}", e);
            let _ = env.exception_clear();
            return;
        }
    };
    let name = match env.new_string("com/splats/app/FilePicker") {
        Ok(s) => s,
        Err(e) => {
            eprintln!("RRFD_JNI_INIT_ERR new_string: {:?}", e);
            return;
        }
    };
    let class = match env.call_method(
        &class_loader,
        "loadClass",
        "(Ljava/lang/String;)Ljava/lang/Class;",
        &[JValue::Object(&name)],
    ) {
        Ok(v) => v.l().unwrap_or_else(|_| JObject::null()),
        Err(e) => {
            eprintln!("RRFD_JNI_INIT_ERR loadClass: {:?}", e);
            let _ = env.exception_clear();
            return;
        }
    };
    if class.is_null() {
        eprintln!("RRFD_JNI_INIT_ERR class null");
        return;
    }
    let class_ref = env.new_global_ref(&class).unwrap();
    let method = match env.get_static_method_id(&JClass::from(class), "startFilePicker", "()V") {
        Ok(m) => m,
        Err(e) => {
            eprintln!("RRFD_JNI_INIT_ERR method: {:?}", e);
            let _ = env.exception_clear();
            return;
        }
    };
    *FILE_PICKER_CLASS
        .write()
        .expect("Failed to write JNI data.") = Some(class_ref);
    *START_FILE_PICKER
        .write()
        .expect("Failed to write JNI data.") = Some(method);

    // Self-check hook: read "marvis_auto_path" from the launching Intent so
    // the app can auto-load a dataset without touch input (emulator).
    {
        let key: JObject = match env.new_string("marvis_auto_path") {
            Ok(s) => unsafe { JObject::from_raw(s.into_raw()) },
            Err(_) => {
                let _ = env.exception_clear();
                JObject::null()
            }
        };
        let extra = if key.is_null() {
            None
        } else {
            match env.call_method(
                &activity,
                "getIntent",
                "()Landroid/content/Intent;",
                &[],
            ) {
                Ok(v) => {
                    let intent = v.l().unwrap_or_else(|_| JObject::null());
                    if intent.is_null() {
                        None
                    } else {
                        match env.call_method(
                            &intent,
                            "getStringExtra",
                            "(Ljava/lang/String;)Ljava/lang/String;",
                            &[JValue::Object(&key)],
                        ) {
                            Ok(extra) => match extra.l() {
                                Ok(s) if !s.is_null() => {
                                    // Convert the returned JObject into a
                                    // JString (same handle) to read it.
                                    let jstr = unsafe { jni::objects::JString::from_raw(s.into_raw()) };
                                    env.get_string(&jstr)
                                        .ok()
                                        .map(|js| js.to_str().unwrap_or_default().to_owned())
                                }
                                _ => None,
                            },
                            Err(_) => {
                                let _ = env.exception_clear();
                                None
                            }
                        }
                    }
                }
                Err(_) => {
                    let _ = env.exception_clear();
                    None
                }
            }
        };
        if let Some(path) = extra.as_ref() {
            *AUTO_PATH
                .write()
                .expect("Failed to write AUTO_PATH") = Some(path.clone());
            eprintln!("RRFD_AUTO_PATH_SET {}", path);
        }
    }

    eprintln!(
        "RRFD_JNI_INIT_DONE vm_set={}",
        !VM.load(Ordering::Acquire).is_null()
    );
}

#[allow(unused)]
pub(crate) async fn pick_file() -> std::io::Result<File> {
    let raw = VM.load(Ordering::Acquire);
    eprintln!(
        "RRFD_PICK vm_none={} channel_set={}",
        raw.is_null(),
        CHANNEL.read().unwrap().is_some()
    );
    let (sender, mut receiver) = tokio::sync::mpsc::channel(1);
    {
        let channel = CHANNEL.write();
        if let Ok(mut channel) = channel {
            *channel = Some(sender);
        } else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::Other,
                "Failed to initialize file picker channel",
            ));
        }
    }

    // Call method. Be sure this is scoped so we drop all guards before waiting.
    {
        let java_vm = unsafe { jni::JavaVM::from_raw(raw) }.map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::Other, format!("JNI error: {:?}", e))
        })?;
        let mut env = java_vm.attach_current_thread().map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::Other, format!("JNI error: {:?}", e))
        })?;

        let class = FILE_PICKER_CLASS
            .read()
            .expect("Failed to initialize FilePicker class");
        let class_ref = match class.as_ref() {
            Some(c) => c,
            None => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Other,
                    "FilePicker class not initialized",
                ))
            }
        };
        let method = START_FILE_PICKER
            .read()
            .expect("Failed to initialize FilePicker method");
        let method_ref = match method.as_ref() {
            Some(m) => m,
            None => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Other,
                    "startFilePicker method not initialized",
                ))
            }
        };

        // SAFETY: This is safe as long as we cached the method in the right way, and
        // this matches the Java side. Not much more we can do here.
        let _ = unsafe {
            env.call_static_method_unchecked(
                class_ref,
                method_ref,
                jni::signature::ReturnType::Primitive(Primitive::Void),
                &[],
            )
        }
        .map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::Other, format!("JNI error: {:?}", e))
        })?;
    }
    let file = receiver.recv().await.ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::Other,
            "Failed to receive file picker result",
        )
    });

    match file {
        Ok(Some(file)) => Ok(file),
        Ok(None) => Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "No file selected",
        )),
        Err(e) => Err(e),
    }
}

#[unsafe(no_mangle)]
extern "system" fn Java_com_splats_app_FilePicker_onFilePickerResult<'local>(
    _env: JNIEnv<'local>,
    _class: JClass<'local>,
    fd: jint,
) {
    let file = if fd < 0 {
        None
    } else {
        // Convert the raw file descriptor into a Rust File
        // SAFETY: Pray that JNI gets us a valid file. It will be open
        // when passed to us.
        Some(unsafe { tokio::fs::File::from_raw_fd(fd) })
    };

    // Channel can be gone before the callback if other parts of pick_file fail.
    if let Ok(ch) = CHANNEL.read() {
        if let Some(ch) = ch.as_ref() {
            ch.try_send(file)
                .expect("Failed to send file picking result");
        }
    }
}
