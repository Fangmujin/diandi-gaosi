#![recursion_limit = "256"]

//! Headless 3DGS trainer.
//!
//! Runs a brush training session with no window, no viewer and no rerun.
//! Progress is reported on stdout as one JSON object per line; everything else
//! (logs, panics) goes to stderr so stdout stays machine parseable.

use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::Context;
use brush_process::config::ProcessArgs;
use brush_process::control;
use brush_process::message::ProcessMessage;
use brush_process::process::process_stream;
use brush_vfs::DataSource;
use clap::Parser;
use tokio_stream::StreamExt;

#[derive(Parser, Debug)]
#[command(
    name = "brush_headless",
    about = "Brush headless 3D gaussian splat trainer",
    version
)]
struct HeadlessArgs {
    /// Directory holding the COLMAP dataset (images/ + sparse/).
    #[arg(long, value_name = "DIR")]
    path: String,

    /// Ply file to write. Intermediate checkpoints overwrite the same file.
    #[arg(long, value_name = "PLY")]
    export_path: String,

    /// Directory watched for `stop` / `pause` files. Defaults to the directory
    /// containing --export-path.
    #[arg(long, value_name = "DIR")]
    control_dir: Option<String>,

    #[arg(long, help_heading = "Training options", default_value_t = 30000)]
    total_steps: u32,

    #[arg(long, help_heading = "Dataset Options", default_value_t = 1920)]
    max_resolution: u32,

    #[arg(long, help_heading = "Refine options", default_value_t = 5_000_000)]
    max_splats: u32,

    #[arg(long, help_heading = "Model Options", default_value_t = 3)]
    sh_degree: u32,

    #[arg(long, help_heading = "Refine options", default_value_t = 200)]
    refine_every: u32,

    #[arg(long, help_heading = "Refine options", default_value_t = 15000)]
    growth_stop_iter: u32,

    /// Weight of the opacity regularizer (pushes logit opacity down).
    #[arg(long, help_heading = "Training options", default_value_t = 1e-9)]
    opac_loss_weight: f32,

    /// Weight of the scale regularizer (sum of exp'd scales, L1-like, penalizes
    /// oversized splats). Divided by bounds median size inside the trainer.
    #[arg(long, help_heading = "Training options", default_value_t = 1e-8)]
    scale_loss_weight: f32,

    /// How long the aux (opacity/scale) losses are applied, as a fraction of
    /// total steps. Also scales them: weight ramps from `aux_loss_time` down to
    /// 0 linearly over that fraction.
    #[arg(long, help_heading = "Training options", default_value_t = 0.9)]
    aux_loss_time: f32,

    #[arg(long, help_heading = "Process options", default_value_t = 500)]
    export_every: u32,

    #[arg(long, help_heading = "Process options", default_value_t = 42)]
    seed: u64,

    /// Max nr. of frames of the dataset to load.
    #[arg(long, help_heading = "Dataset Options")]
    max_frames: Option<usize>,

    /// Hold out every nth image as an eval split.
    #[arg(long, help_heading = "Dataset Options")]
    eval_split_every: Option<usize>,
}

/// One line of stdout. Field order matches the contract with the Android app.
#[derive(serde::Serialize)]
struct Progress {
    iter: u32,
    total: u32,
    splats: u32,
    loss: f32,
    elapsed_ms: u64,
    eta_sec: f32,
    stage: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

impl Progress {
    fn new(total: u32) -> Self {
        Self {
            iter: 0,
            total,
            splats: 0,
            loss: 0.0,
            elapsed_ms: 0,
            eta_sec: 0.0,
            stage: "training",
            error: None,
        }
    }

    fn eta_sec(iter: u32, total: u32, elapsed_ms: u64) -> f32 {
        if iter == 0 || iter >= total {
            return 0.0;
        }
        let per_iter = elapsed_ms as f64 / iter as f64;
        (per_iter * (total.saturating_sub(iter)) as f64 / 1000.0) as f32
    }
}

fn emit(progress: &Progress) {
    match serde_json::to_string(progress) {
        Ok(line) => {
            println!("{line}");
            // Rust's stdout is line buffered, but flush explicitly so a
            // ProcessBuilder consumer that reads while we train never stalls.
            let _ = std::io::stdout().flush();
        }
        Err(e) => log::error!("Failed to serialize progress: {e}"),
    }
}

/// JSON has no NaN/Infinity, so clamp before serializing.
fn finite(v: f32) -> f32 {
    if v.is_finite() {
        v
    } else {
        0.0
    }
}

fn split_export_path(export_path: &str) -> (PathBuf, String) {
    let path = PathBuf::from(export_path);
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "export_{iter}.ply".to_owned());
    (parent, name)
}

fn clear_control_file(dir: &Path, name: &str) {
    let file = dir.join(name);
    if file.is_file() {
        log::warn!("Removing stale control file {}", file.display());
        let _ = std::fs::remove_file(file);
    }
}

async fn run(cli: HeadlessArgs) -> anyhow::Result<()> {
    let (export_dir, export_name) = split_export_path(&cli.export_path);

    let control_dir = cli
        .control_dir
        .clone()
        .map(PathBuf::from)
        .unwrap_or_else(|| export_dir.clone());
    // A fresh process owns its control files: never let a leftover 'stop'
    // from a previous run kill this one before it starts.
    clear_control_file(&control_dir, "stop");
    clear_control_file(&control_dir, "pause");
    control::set_control_dir(&control_dir);
    log::info!("Control directory: {}", control_dir.display());

    let mut args = ProcessArgs::default();
    args.train_config.total_steps = cli.total_steps;
    args.train_config.max_splats = cli.max_splats;
    args.train_config.refine_every = cli.refine_every;
    args.train_config.growth_stop_iter = cli.growth_stop_iter;
    args.train_config.opac_loss_weight = cli.opac_loss_weight;
    args.train_config.scale_loss_weight = cli.scale_loss_weight;
    args.train_config.aux_loss_time = cli.aux_loss_time;
    args.model_config.sh_degree = cli.sh_degree;
    args.load_config.max_resolution = cli.max_resolution;
    args.load_config.max_frames = cli.max_frames;
    args.load_config.eval_split_every = cli.eval_split_every;
    args.process_config.seed = cli.seed;
    args.process_config.export_every = cli.export_every;
    args.process_config.export_path = export_dir.to_string_lossy().into_owned();
    args.process_config.export_name = export_name;
    args.process_config.eval_save_to_disk = false;
    args.rerun_config.rerun_enabled = false;

    let mut progress = Progress::new(cli.total_steps);
    emit(&progress);

    log::info!("Initializing GPU device");
    let device = brush_render::burn_init_setup().await;

    let (sender, args_receiver) = tokio::sync::oneshot::channel();
    sender
        .send(args)
        .map_err(|_| anyhow::anyhow!("Failed to send process args"))?;

    log::info!("Starting training from {}", cli.path);
    let stream = process_stream(DataSource::Path(cli.path.clone()), args_receiver, device);
    let mut stream = std::pin::pin!(stream);

    while let Some(msg) = stream.next().await {
        let msg = match msg {
            Ok(msg) => msg,
            Err(e) => {
                let err = format!("{e:#}");
                progress.stage = "failed";
                progress.error = Some(err);
                emit(&progress);
                return Err(e);
            }
        };

        match msg {
            ProcessMessage::TrainStep {
                splats,
                stats,
                iter,
                total_elapsed,
            } => {
                let stats = *stats;
                progress.iter = iter;
                progress.splats = splats.num_splats();
                progress.loss = finite(stats.loss.into_scalar_async().await);
                progress.elapsed_ms = total_elapsed.as_millis() as u64;
                progress.eta_sec = Progress::eta_sec(iter, progress.total, progress.elapsed_ms);
                progress.stage = "training";
                emit(&progress);
            }
            ProcessMessage::RefineStep {
                cur_splat_count, ..
            } => {
                progress.splats = cur_splat_count;
            }
            ProcessMessage::ExportStep { iter } => {
                progress.iter = iter;
                progress.stage = "exporting";
                emit(&progress);
            }
            ProcessMessage::Dataset { dataset } => {
                log::info!(
                    "Loaded {} training views",
                    dataset.train.views.len()
                );
            }
            ProcessMessage::Warning { error } => {
                log::warn!("{error:#}");
            }
            // A COLMAP dataset with points3D seeds the initial splats from those
            // points, which is exactly what we want. The message only carries
            // viewer state, so ignore it (brush-cli does the same).
            ProcessMessage::ViewSplats { .. } => {
                log::info!("Dataset provides initial splats, using them");
            }
            _ => {}
        }
    }

    progress.stage = "done";
    progress.error = None;
    emit(&progress);
    Ok(())
}

fn main() -> anyhow::Result<()> {
    let cli = HeadlessArgs::parse();

    // Never let logs pollute stdout: it carries the JSON progress stream.
    env_logger::builder()
        .target(env_logger::Target::Stderr)
        .init();

    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .context("Failed to initialize tokio runtime")?
        .block_on(run(cli))
}
