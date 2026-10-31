# 架构选型文档 - 点点高斯 v0.1

> 日期:2026-10-14 | 状态:待用户确认
> 架构师:高见远 | 目标真机:VIVO X300 Pro(骁龙 8 Elite / Adreno 830 / Vulkan 1.3 / 12-16GB)
> 开发环境:Windows PC + Android 模拟器(x86_64, API 36, AVD: Medium_Phone_API_36.1)

---

## 1. 端侧真实重建链(核心,区别于上轮伪点云)

```
视频/照片 → 抽帧(MediaCodec,已有) → SfM 重建(COLMAP 精简交叉编译,新增)
→ Brush 轻量训练(brush-headless 新 crate,Vulkan) → final.ply
→ GLB 转换(splat-transform,已有) → PlayCanvas GSplat 渲染(已有)
```

## 2. 三大关键选型(ADR 摘要,全文见 docs/decisions/ADR-00*.md)

### ADR-001: SfM = COLMAP 精简交叉编译(arm64/x86_64) + global_mapper
- COLMAP 4.0 已合并 GLOMAP 为 `global_mapper`,全局 SfM 比增量 mapper 快 10-50x,CPU 友好
- 仅编 3 个命令为单一可执行文件:feature_extractor + sequential_matcher + global_mapper(视频序列用 sequential 匹配,O(N²)→O(N))
- 输出原生 COLMAP 格式(cameras.bin/images.bin/points3D.bin),与 Brush dataset loader **零转换直连**
- 构建配置:`-DCUDA_ENABLED=OFF -DGUI_ENABLED=OFF -DTESTS_ENABLED=OFF`,Ceres MINIGLOG,SQLite amalgamation;FreeImage 为中风险依赖(有安卓移植先例)
- 不走 vcpkg(arm64-android port 曾失败 #33359);**退路**:3 天内 FreeImage/Boost 无解 → 切 OpenMVG + 自写 COLMAP 格式转换器(字段级 golden 比对防沉默错误)

### ADR-002: 训练引擎 = Brush v0.3(wgpu/Vulkan)+ 新增 brush-headless crate
- Brush 安卓训练是官方支持路径(wgpu vulkan + burn/cubecl),arm64/x86_64 APK 上轮已编译成功(62MB)
- 在 brush workspace 新增 `brush-headless` bin crate:依赖 brush-process/brush-dataset,**不拉 egui/winit**;JSONL 进度流(iter/耗时/高斯数/预估剩余)+ 控制文件暂停/恢复/停止 + final.ply 落盘
- **必改项**:`brush-dataset/src/scene_loader.rs:23` 的 `MAX_CACHE_MB` 硬编码 6GB → 移动端默认 1.5GB(不修则真机必 OOM/LMK)
- 断点续训:export_every 产出 ply + 数据集放 init.ply + `--start-iter`("准续训",optimizer 动量重置为固有限制,文档明示)

### ADR-003: 应用架构 = WebView 壳 + 原生 CLI 子进程调度(与桌面 OOOSplat 同构)
- Kotlin 壳(复用上轮已验证构建链)+ 本地 HTTP 服务 + JS 桥(已有)
- PipelineManager(新写核心组件)以 ProcessBuilder 调度 jniLibs 内原生二进制(colmap / brush-headless 以 lib*.so 形式随 APK 分发,保证可执行位)
- 训练运行于 `foregroundServiceType="mediaProcessing"` 前台服务 + ThermalGuard(温度过热自动分段暂停)
- 抽帧保留 MediaCodec(已验证),**不引入 FFmpeg**(ffmpeg-kit 已停止维护,省 20-40MB/ABI + LGPL 合规负担)
- 子进程崩溃不带崩 UI;模拟器(SwiftShader)只验证链路正确性,性能标定必须真机

## 3. 双档参数表(Spec 默认值)

| 参数 | 快档 | 精档 |
|------|------|------|
| 输入帧数 | 32-48 | 60-80 |
| SfM 输入分辨率 | ≤960px | ≤1600px |
| 匹配策略 | sequential, overlap=8-10 | sequential, overlap=10-15 |
| Mapper | global_mapper | global_mapper(失败回退 point_triangulator+增量) |
| 训练 max_resolution | 960 | 1280(内存余量足可 1600) |
| total_steps | 6000 | 12000-15000 |
| max_splats | 1,500,000 | 4,000,000 |
| sh_degree | 1 | 3 |
| refine_every / growth_stop_iter | 100 / 4000 | 200 / 10000 |
| export_every | 2000 | 3000 |

## 4. 端到端耗时预估(VIVO X300 Pro)

| 阶段 | 快档 | 精档 | 置信度 |
|------|------|------|--------|
| 视频抽帧 | 1-2 min | 2-4 min | 高 |
| SfM 全阶段 | 5-15 min | 12-30 min | 中 |
| 训练 | 10-35 min | 60-150 min | **低-中(最不确定)** |
| 转换+落盘 | <1 min | 1-2 min | 高 |
| **端到端** | **20-45 min** | **1.5-3 h** | — |

**ETA 校准方案(Spec 硬约束)**:训练启动后先跑 200 步实测 it/s,`剩余时间=(total_steps-iter)/实测it/s` 动态更新;SfM 按各阶段实时进度折算。模拟器数据不得用于性能预估。

## 5. APK 体积

ABI 拆分,arm64 为主。预估 100-160MB(colmap 侧 20-40MB + brush-headless 30-50MB + 渲染/转换资产);超 200MB 再议按需下发。

## 6. 里程碑 1 端到端验证(开发门禁)

1. **桌面黄金链路**:PC 真 COLMAP 处理 60 帧样例 → brush 桌面 CLI 训练 500 步 → final.ply → GLB 渲染,全绿后该 COLMAP 数据集为 golden 输入
2. **模拟器链路(正确性)**:golden 输入缩到 16帧/512px → 模拟器跑 colmap x86_64 + brush-headless x86_64(200 步)→ 与桌面产物比对 ply 点数/相机数
3. **真机标定(性能)**:VIVO X300 Pro 跑快档全流程,记录各阶段耗时/it/s/内存峰值/温度 → 收窄耗时区间,锁定 UI 预估公式
4. **错误流**:弱纹理素材 → SfM 注册帧数 <50% 的提示路径

## 7. 已验证可继承资产(上轮马威斯任务)

WebView 壳构建链(aapt2+javac+d8+apksigner)、PLY→GLB 转换(1.15MB/246ms)、PlayCanvas GSplat 渲染(5000 高斯 48-74fps)、视频抽帧(8帧/14s)、Brush v0.3 arm64/x86_64 交叉编译、相册/拍照/导入/导出四桥接入口、`marvis_auto_path` 自动化测试钩子。

## 8. 技术栈锁定

| 层 | 技术 | 版本 |
|----|------|------|
| 壳 | Kotlin + WebView(手工构建链) | minSdk 24 |
| SfM | COLMAP 精简(headless,无 CUDA/GUI) | 锁 4.1/4.2 tag(golden 用例防漂移) |
| 训练 | Brush fork + brush-headless crate | v0.3.0 |
| 转换 | splat-transform(ESM,纯 CPU) | 3.9.0 |
| 渲染 | PlayCanvas GSplat(WebGL2) | 2.23.0 |
| 图标 | lucide-static(全项目唯一图标库) | 最新 |
| Rust 工具链 | rustc/cargo(本机已装) | 1.99.0 |
| NDK | r27c / SDK ndk 27.2.12479018(已装) | 27.x |
