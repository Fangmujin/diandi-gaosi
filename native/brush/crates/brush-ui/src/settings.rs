use crate::{UiMode, panels::AppPane, ui_process::UiProcess};
use brush_process::config::ProcessArgs;
use brush_vfs::DataSource;
use egui::{Align2, Slider, Ui};
use tokio::sync::oneshot::Sender;

pub struct SettingsPanel {
    args: ProcessArgs,
    url: String,
    send_args: Option<Sender<ProcessArgs>>,
    show_url_dialog: bool,
    picker_feedback: Option<String>,
}

impl SettingsPanel {
    pub(crate) fn new() -> Self {
        Self {
            args: ProcessArgs::default(),
            url: "splat.com/example.ply".to_owned(),
            send_args: None,
            show_url_dialog: false,
            picker_feedback: None,
        }
    }

    fn ui_window(&mut self, ui: &egui::Ui, process: &UiProcess) {
        // Check if the receiver is closed and set send_args to None if it is
        if let Some(sender) = &self.send_args
            && sender.is_closed()
        {
            self.send_args = None;
        }

        if self.send_args.is_none() {
            return;
        }

        egui::Window::new("训练设置")
            .resizable(true)
            .collapsible(false)
            .default_pos(ui.ctx().screen_rect().center())
            .pivot(Align2::CENTER_CENTER)
            .show(ui.ctx(), |ui| {
                // 训练
                ui.heading("训练");
                slider(ui, &mut self.args.train_config.total_steps, 1..=50000, " 步", false);

                ui.label("最大高斯数");
                ui.add(Slider::new(&mut self.args.train_config.max_splats, 1000000..=10000000)
                    .custom_formatter(|n, _| format!("{:.0}k", n as f32 / 1000.0))
                    .clamping(egui::SliderClamping::Never));

                ui.collapsing("学习率", |ui| {
                    let tc = &mut self.args.train_config;
                    slider(ui, &mut tc.lr_mean, 1e-7..=1e-4, "均值学习率（起始）", true);
                    slider(ui, &mut tc.lr_mean, 1e-7..=1e-4, "均值学习率（结束）", true);
                    slider(ui, &mut tc.mean_noise_weight, 1e3..=1e5, "均值噪声权重", true);
                    slider(ui, &mut tc.lr_coeffs_dc, 1e-4..=1e-2, "球谐 DC 系数", true);
                    slider(ui, &mut tc.lr_coeffs_sh_scale, 1.0..=50.0, "高阶球谐除数", false);
                    slider(ui, &mut tc.lr_opac, 1e-3..=1e-1, "不透明度", true);
                    slider(ui, &mut tc.lr_scale, 1e-3..=1e-1, "缩放", true);
                    slider(ui, &mut tc.lr_scale_end, 1e-4..=1e-2, "缩放（结束）", true);
                    slider(ui, &mut tc.lr_rotation, 1e-4..=1e-2, "旋转", true);
                });

                ui.collapsing("生长与细化", |ui| {
                    let tc = &mut self.args.train_config;
                    slider(ui, &mut tc.refine_every, 50..=300, "细化频率", false);
                    slider(ui, &mut tc.growth_grad_threshold, 0.0001..=0.001, "生长梯度阈值", true);
                    slider(ui, &mut tc.growth_select_fraction, 0.01..=0.2, "生长选择比例", false);
                    slider(ui, &mut tc.growth_stop_iter, 5000..=20000, "生长停止迭代", false);
                });

                ui.collapsing("损失函数", |ui| {
                    let tc = &mut self.args.train_config;
                    slider(ui, &mut tc.ssim_weight, 0.0..=1.0, "SSIM 权重", false);
                    slider(ui, &mut tc.opac_loss_weight, 1e-9..=1e-7, "高斯不透明度损失权重", true);
                    slider(ui, &mut tc.match_alpha_weight, 0.01..=1.0, "Alpha 匹配权重", false);
                });

                ui.add_space(15.0);

                // 模型
                ui.heading("模型");
                ui.label("球谐阶数:");
                ui.add(Slider::new(&mut self.args.model_config.sh_degree, 0..=4));

                ui.add_space(15.0);

                // 数据集
                ui.heading("数据集");
                ui.label("最大图像分辨率");
                slider(ui, &mut self.args.load_config.max_resolution, 32..=2048, "", false);

                let mut limit_frames = self.args.load_config.max_frames.is_some();
                if ui.checkbox(&mut limit_frames, "限制最大帧数").clicked() {
                    self.args.load_config.max_frames = if limit_frames { Some(32) } else { None };
                }
                if let Some(max_frames) = self.args.load_config.max_frames.as_mut() {
                    slider(ui, max_frames, 1..=256, "", false);
                }

                let mut use_eval_split = self.args.load_config.eval_split_every.is_some();
                if ui.checkbox(&mut use_eval_split, "划分评估集").clicked() {
                    self.args.load_config.eval_split_every = if use_eval_split { Some(8) } else { None };
                }
                if let Some(eval_split) = self.args.load_config.eval_split_every.as_mut() {
                    ui.add(Slider::new(eval_split, 2..=32).clamping(egui::SliderClamping::Never)
                        .prefix("每 ").suffix(" 帧取 1"));
                }

                let mut subsample_frames = self.args.load_config.subsample_frames.is_some();
                if ui.checkbox(&mut subsample_frames, "帧抽样").clicked() {
                    self.args.load_config.subsample_frames = if subsample_frames { Some(2) } else { None };
                }
                if let Some(subsample) = self.args.load_config.subsample_frames.as_mut() {
                    ui.add(Slider::new(subsample, 2..=20).clamping(egui::SliderClamping::Never)
                        .prefix("每 1/").suffix(" 帧加载"));
                }

                let mut subsample_points = self.args.load_config.subsample_points.is_some();
                if ui.checkbox(&mut subsample_points, "点云抽样").clicked() {
                    self.args.load_config.subsample_points = if subsample_points { Some(2) } else { None };
                }
                if let Some(subsample) = self.args.load_config.subsample_points.as_mut() {
                    ui.add(Slider::new(subsample, 2..=20).clamping(egui::SliderClamping::Never)
                        .prefix("每 1/").suffix(" 点加载"));
                }

                ui.add_space(15.0);

                // 处理
                ui.heading("处理");
                ui.label("随机种子:");
                let mut seed_str = self.args.process_config.seed.to_string();
                if ui.text_edit_singleline(&mut seed_str).changed()
                    && let Ok(seed) = seed_str.parse::<u64>() {
                        self.args.process_config.seed = seed;
                    }

                ui.label("起始迭代:");
                slider(ui, &mut self.args.process_config.start_iter, 0..=10000, "", false);

                #[cfg(not(target_family = "wasm"))]
                ui.collapsing("导出", |ui| {
                    let pc = &mut self.args.process_config;
                    ui.add(Slider::new(&mut pc.export_every, 1..=15000)
                        .clamping(egui::SliderClamping::Never).prefix("每 ").suffix(" 步"));
                    text_input(ui, "导出路径:", &mut pc.export_path);
                    text_input(ui, "导出文件名:", &mut pc.export_name);
                });

                ui.collapsing("评估", |ui| {
                    let pc = &mut self.args.process_config;
                    ui.add(Slider::new(&mut pc.eval_every, 1..=5000)
                        .clamping(egui::SliderClamping::Never).prefix("每 ").suffix(" 步"));
                    ui.checkbox(&mut pc.eval_save_to_disk, "保存评估图像到磁盘");
                });

                ui.add_space(15.0);

                // Rerun
                #[cfg(all(not(target_family = "wasm"), not(target_os = "android")))]
                {
                    ui.add(egui::Hyperlink::from_label_and_url(
                        egui::RichText::new("Rerun.io").heading(), "https://rerun.io"));

                    let rc = &mut self.args.rerun_config;
                    ui.checkbox(&mut rc.rerun_enabled, "启用 Rerun");

                    if rc.rerun_enabled {
                        ui.label("在 rerun 查看器中打开 brush_blueprint.rbl 以获得默认布局。");

                        ui.label("记录训练统计");
                        ui.add(Slider::new(&mut rc.rerun_log_train_stats_every, 1..=1000)
                            .clamping(egui::SliderClamping::Never).prefix("每 ").suffix(" 步"));

                        let mut visualize_splats = rc.rerun_log_splats_every.is_some();
                        ui.checkbox(&mut visualize_splats, "可视化高斯");
                        if visualize_splats != rc.rerun_log_splats_every.is_some() {
                            rc.rerun_log_splats_every = if visualize_splats { Some(500) } else { None };
                        }
                        if let Some(every) = rc.rerun_log_splats_every.as_mut() {
                            slider(ui, every, 1..=5000, "可视化高斯间隔", false);
                        }

                        ui.label("最大图像日志尺寸");
                        ui.add(Slider::new(&mut rc.rerun_max_img_size, 128..=2048)
                            .clamping(egui::SliderClamping::Never).suffix(" px"));
                    }

                    ui.add_space(15.0);
                }

                // 开始按钮
                ui.add_space(10.0);
                ui.vertical_centered_justified(|ui| {
                    if ui.add(egui::Button::new("开始训练")
                        .min_size(egui::vec2(160.0, 48.0))
                        .fill(egui::Color32::from_rgb(70, 130, 180))
                        .corner_radius(6.0)).clicked() {
                        process.set_total_steps(self.args.train_config.total_steps);
                        self.send_args.take().expect("Must be some").send(self.args.clone()).ok();
                    }
                });
            });
    }
}

impl AppPane for SettingsPanel {
    fn title(&self) -> String {
        "设置".to_owned()
    }

    fn is_visible(&self, process: &UiProcess) -> bool {
        process.ui_mode() == UiMode::Default
    }

    fn ui(&mut self, ui: &mut egui::Ui, process: &UiProcess) {
        egui::ScrollArea::vertical().show(ui, |ui| {
            ui.add_space(20.0);

            // 创建加载选项 UI
            let mut load_option = None;

            ui.label(
                egui::RichText::new("加载数据:")
                    .heading()
                    .color(egui::Color32::from_rgb(70, 130, 180)),
            );
            ui.add_space(8.0);

            ui.horizontal(|ui| {
                ui.spacing_mut().item_spacing.x = 6.0;

                // Larger touch targets on mobile.
                let btn_size = if cfg!(target_os = "android") {
                    egui::vec2(160.0, 56.0)
                } else {
                    egui::vec2(88.0, 48.0)
                };

                if ui
                    .add(
                        egui::Button::new("文件")
                            .min_size(btn_size)
                            .fill(egui::Color32::from_rgb(70, 130, 180))
                            .stroke(egui::Stroke::NONE),
                    )
                    .clicked()
                {
                    load_option = Some(DataSource::PickFile);
                    self.picker_feedback = Some("正在打开文件选择器，请选择 PLY / SPZ 文件…".to_owned());
                }

                let can_pick_dir = !cfg!(target_os = "android");
                if can_pick_dir
                    && ui
                        .add(
                            egui::Button::new("文件夹")
                                .min_size(btn_size)
                                .fill(egui::Color32::from_rgb(70, 130, 180))
                                .stroke(egui::Stroke::NONE),
                        )
                        .clicked()
                {
                    load_option = Some(DataSource::PickDirectory);
                    self.picker_feedback = Some("正在打开文件夹选择器…".to_owned());
                }

                let can_url = !cfg!(target_os = "android");
                if can_url
                    && ui
                        .add(
                            egui::Button::new("网址")
                                .min_size(btn_size)
                                .fill(egui::Color32::from_rgb(70, 130, 180))
                                .stroke(egui::Stroke::NONE),
                        )
                        .clicked()
                {
                    self.show_url_dialog = true;
                    self.picker_feedback = Some("正在打开网址输入框…".to_owned());
                }
            });

            // 点击反馈 / 错误提示
            if let Some(feedback) = &self.picker_feedback {
                ui.add_space(8.0);
                ui.label(
                    egui::RichText::new(feedback)
                        .size(13.0)
                        .color(egui::Color32::from_rgb(120, 170, 220)),
                );
            }

            ui.add_space(15.0);

            // URL dialog window
            if self.show_url_dialog {
                egui::Window::new("从网址加载")
                    .resizable(false)
                    .collapsible(false)
                    .default_pos(ui.ctx().screen_rect().center())
                    .pivot(Align2::CENTER_CENTER)
                    .show(ui.ctx(), |ui| {
                        ui.vertical(|ui| {
                            ui.label("输入网址:");
                            ui.add_space(5.0);

                            let url_response = ui.add(
                                egui::TextEdit::singleline(&mut self.url)
                                    .desired_width(280.0)
                                    .hint_text("例如：splat.com/example.ply"),
                            );

                            ui.add_space(10.0);

                            ui.horizontal(|ui| {
                                if ui.button("加载").clicked() && !self.url.trim().is_empty() {
                                    load_option = Some(DataSource::Url(self.url.clone()));
                                    self.show_url_dialog = false;
                                }
                                if ui.button("取消").clicked() {
                                    self.show_url_dialog = false;
                                }
                            });

                            if url_response.lost_focus()
                                && ui.input(|i| i.key_pressed(egui::Key::Enter))
                                && !self.url.trim().is_empty()
                            {
                                load_option = Some(DataSource::Url(self.url.clone()));
                                self.show_url_dialog = false;
                            }
                        });
                    });
            }

            if let Some(source) = load_option {
                let (sender, receiver) = tokio::sync::oneshot::channel();
                self.send_args = Some(sender);
                process.start_new_process(source, receiver);
            }
        });

        // 加载流程开始后清除选择器提示
        if process.is_loading() || process.last_error().is_some() {
            self.picker_feedback = None;
        }

        // 加载数据 / 选择器过程中显示错误
        if let Some(err) = process.last_error() {
            ui.add_space(8.0);
            ui.label(
                egui::RichText::new(format!("加载失败：{err}"))
                    .size(13.0)
                    .color(egui::Color32::from_rgb(220, 80, 80)),
            );
        }

        // Draw settings window if we're loading something (if loading a ply
        // this wont' do anything, only if process args are needed).
        if process.is_loading() {
            self.ui_window(ui, process);
        }
    }
}

// Helper functions to reduce repetition
fn slider<T>(
    ui: &mut Ui,
    value: &mut T,
    range: std::ops::RangeInclusive<T>,
    text: &str,
    logarithmic: bool,
) where
    T: egui::emath::Numeric,
{
    let mut s = Slider::new(value, range).clamping(egui::SliderClamping::Never);
    if logarithmic {
        s = s.logarithmic(true);
    }
    if !text.is_empty() {
        s = s.text(text);
    }
    ui.add(s);
}

#[allow(unused)]
fn text_input(ui: &mut Ui, label: &str, text: &mut String) {
    let label = ui.label(label);
    ui.text_edit_singleline(text).labelled_by(label.id);
}
