# Spec - 点点高斯 v0.1(规格契约)

> 生成日期:2026-10-14 | 基于:PRD v0.1 + ARCH v0.1 + DESIGN v0.1 | 状态:已由用户批准开始开发

---

## 1. 产品定义
安卓端侧(全离线)3DGS 高斯泼溅重建 APP,把视频/照片重建成可交互查看的高斯模型,UI 对标桌面版 OOOSplat 极简形态。

## 2. MVP 范围(锁定)
| 优先级 | 功能 | 验收摘要 |
|--------|------|---------|
| P0 | 导入素材 | 相册选视频/照片 + 拍摄;≤60s 视频 |
| P0 | 运行 | 快/精双档;运行前预估总耗时;阶段进度实时占比;动态 ETA |
| P0 | 查看 | 手势旋转/缩放/平移;≥24fps(真机快档) |
| P0 | 图库池 | 历史项目列表,3s 内打开 |
| P1 | 错误提示 | SfM 注册率 <50% 给人话提示 |

## 3. 明确不做
账号/订阅/云同步、导出 PLY-GLB 分享(Backlog)、AR 采集引导、模型裁剪、水印。

## 4. 技术架构(锁定)
| 层 | 技术 | 版本 |
|----|------|------|
| 壳 | Kotlin + WebView(手工构建:aapt2/javac/d8/apksigner) | minSdk 24 / target 36 |
| SfM | 端侧 SfM 引擎(见 §4.1),输出 COLMAP 格式 | — |
| 训练 | brush-headless(Rust,wgpu/Vulkan) | brush v0.3.0 fork |
| 转换 | splat-transform ESM(纯 CPU)PLY→GLB | 3.9.0 |
| 渲染 | PlayCanvas GSplat(WebGL2) | 2.23.0 |
| 图标 | lucide-static(唯一) | 最新 |
| 工具链 | Rust 1.99 / NDK 27.2.12479018 / SDK android-36 | 本机已装 |

### 4.1 SfM 引擎决策(按优先级,逐个尝试,失败即降级)
1. **首选**:COLMAP headless 精简交叉编译(feature_extractor + sequential_matcher + global_mapper,CUDA/GUI/TESTS OFF)
2. **退路 1**:OpenCV Android SDK(features2d SIFT + calib3d)+ 自写最小增量 SfM → 输出 COLMAP bin 三件套
3. **退路 2**:OpenMVG + 自写 COLMAP 格式转换器
硬要求:**输出必须是 COLMAP 格式 cameras.bin/images.bin/points3D.bin**,作为 brush dataset 输入。

## 5. 原生二进制契约(跨 worker 唯一接口)
统一 IPC 约定,三个 worker 必须遵守:

### 5.1 brush-headless
```
调用:ProcessBuilder exec <nativeLibDir>/libbrushheadless.so \
  --path <colmapDatasetDir> --export-path <out.ply> \
  --total-steps <n> --max-resolution <n> --max-splats <n> --sh-degree <n> \
  --refine-every <n> --growth-stop-iter <n> --export-every <n>
进度:stdout 每行一个 JSON:{"iter":N,"total":N,"splats":N,"loss":F,"elapsed_ms":N,"eta_sec":F}
控制:工作目录出现 stop → 立即退出;出现 pause → 暂停直到 pause 消失
产物:final.ply 落 --export-path
必改:MAX_CACHE_MB 安卓默认 1536(非 6GB)
```

### 5.2 SfM 引擎
```
调用:exec <nativeLibDir>/libiii_sfm.so --images <dir> --out <colmapDir> --max-size <n> --overlap <n>
进度:stdout 每行 JSON:{"stage":"features|matching|mapping","current":N,"total":N,"registered":N}
产物:<colmapDir>/{cameras.bin,images.bin,points3D.bin}
```
(命令名由最终选定引擎实现确定,格式须对齐)

## 6. 双档参数(Spec 默认值)
| 参数 | 快档 | 精档 |
|------|------|------|
| 帧数/分辨率 | 32-48 / ≤960 | 60-80 / ≤1600 |
| 匹配 overlap | 8-10 | 10-15 |
| max_resolution(训练) | 960 | 1280 |
| total_steps | 6000 | 12000-15000 |
| max_splats | 1.5M | 4M |
| sh_degree | 1 | 3 |
| refine_every / growth_stop | 100 / 4000 | 200 / 10000 |
| export_every | 2000 | 3000 |

## 7. 页面清单(锁定)
| 页面 | 内容 |
|------|------|
| P1 导入页 | 拍摄/相册双入口、素材缩略图条、快/精分段控件(快≈20-45分钟/精≈1.5-3小时)、开始重建 |
| P2 运行进度页 | 阶段清单(抽帧→特征→匹配→重建→训练→导出)、mono 百分比、预估剩余耗时(动态)、折叠日志、错误重试 |
| P3 查看页 | 全屏画布 + 手势 + 重置/切换按钮 + 模型信息条 |
| P4 图库池 | 2 列网格、状态徽章、长按 ActionSheet、空状态引导 |

## 8. 设计 Token(锁定)
底色 #0D1117 / 卡片 #161F2A / 前景 #E6EDF3 / muted #8B949E / accent #34D399 / border #2A3441;
语义 成功#3FB950 警告#D29922 错误#F85149;字体 Noto Sans SC + JetBrains Mono;图标 Lucide(16/20/24px);
圆角 8/12/16/pill;间距 4/8/12/16/20/24/32;触摸 ≥44×44;缓动 cubic-bezier(0.2,0,0,1);零 emoji 图标、零硬编码色、无紫粉渐变。

## 9. 端到端验证(交付门禁)
```
1. 打包 APK(x86_64 + arm64),apksigner 自签校验通过
2. 模拟器安装:adb install -r <apk>; am start 启动无崩溃
3. 内置样例素材跑完整管道到 final.ply(极小参数:8-16帧/512px/300-1000步)
4. 查看页渲染出图,LP AI/Ally 非空白、非伪点云(有相机位姿、注册率)
5. 图库池可见该项目并可重入查看
6. 错误流:弱纹理素材 → 给出明确失败提示,不静默崩溃
```
