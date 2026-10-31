# libiii_sfm —— 点点高斯 SfM 引擎

真实的运动恢复结构 (Structure-from-Motion) 引擎。输入一组抽帧图片, 输出 **COLMAP 格式的
`cameras.bin` / `images.bin` / `points3D.bin`**, 训练器 (brush) 直接读取, 零转换。

## 二进制契约

调用方式(文件名固定为 `libiii_sfm.so`, 但它是 **PIE 可执行文件**, 用 `exec` 调用, 不是 `dlopen`):

```
exec <nativeLibDir>/libiii_sfm.so \
     --images <dir>       输入抽帧图片目录(必填)
     --out <colmapDir>    输出目录(必填, 需已存在)
     [--max-size 1600]    长边降采样上限(像素)
     [--overlap 5]        序列匹配窗口, 第 i 帧与 i+1..i+overlap 匹配
     [--features 2048]    每帧最多 SIFT 特征数
     [--exhaustive]       帧数很少时用全匹配(O(N^2))
     [--txt]              额外输出 cameras.txt/images.txt/points3D.txt
     [--verbose]          调试日志打到 stderr
```

进度: **stdout 每行一个 JSON**(JSON Lines), 行缓冲, 可实时读取:

```
{"stage":"features","current":12,"total":44,"registered":0,"points":0}
{"stage":"matching","current":30,"total":120,"registered":0,"points":0}
{"stage":"mapping","current":17,"total":44,"registered":17,"points":5231}
{"stage":"done","current":44,"total":44,"registered":44,"points":18302, ...}
{"stage":"error","message":"..."}       // 失败时输出, 进程退出码非 0
```

产物: `<colmapDir>/{cameras.bin,images.bin,points3D.bin}`, 相机模型为 `SIMPLE_RADIAL`
(model_id = 2), 参数为 `(f, cx, cy, k1)`。

退出码: `0` 成功; `2` 参数错误; `3` 读不到图片; `4` 有效特征不足两张;
`5/6` 匹配失败; `7` 重建失败; `8` 写文件失败。

## 算法流程

1. **特征提取** —— OpenCV SIFT, 每帧最多 2048 个特征, 长边降采样到 `--max-size`
2. **匹配** —— 序列窗口匹配 O(N*overlap); BF + Lowe 比值测试 (0.8) + 互相最近邻;
   基础矩阵 F 的 RANSAC 几何校验
3. **Track 构建** —— 并查集把跨帧的匹配点归并成 track
4. **初始像对** —— 本质矩阵 E 的 RANSAC + `recoverPose` + 三角化 + 视差角评估,
   优先选择中位视差角大的像对
5. **增量注册** —— 按 2D-3D 对应数选下一帧; PnP RANSAC (EPnP + 迭代精化);
   内点数与内点比例双阈值
6. **三角化** —— DLT + 前方交会符号检查 + 重投影误差 + 最小视差角
7. **光束法平差** —— 自写稀疏 LM (Schur 消元 + Huber 损失), 优化所有位姿 + 共享内参
   `(f, cx, cy, k1)`; 固定第一帧消除规范自由度
8. **输出** —— COLMAP 二进制三件套, 并回读自检

所有几何计算都在**去畸变后的归一化相机坐标**下进行 (牛顿迭代反解 `r*(1+k1*r^2)`),
避免内参与畸变耦合导致的偏差。

## 构建

```bash
# 1) 准备 OpenCV 静态库
#    x86_64: 直接用官方预编译 SDK, 不需要编译
#      opencv-4.10.0-android-sdk.zip 里的 sdk/native/staticlibs/x86_64、
#      sdk/native/3rdparty/libs/x86_64、sdk/native/jni/include 按 arm64 那份的
#      布局放到 native/opencv-install/x86_64/ 下即可
#    arm64-v8a: 源码交叉编译 (约 20-40 分钟)
bash build_opencv.sh arm64-v8a

# 2) 编译 libiii_sfm.so
bash build_android.sh arm64-v8a
bash build_android.sh x86_64
```

产物落到 `diandi-gaosi/native/prebuilt/<abi>/libiii_sfm.so`，自动 `llvm-strip --strip-all`。

## 验证

```bash
# 推到设备/模拟器上跑一遍 (本机 adb 不稳定, 脚本内部会自己拉起 daemon 并轮询等设备上線)
bash tools/run_device.sh x86_64 <frames_dir> <host_out_dir> --max-size 1024 --overlap 8

# 图片多、耗时长的场景用后台版: 设备端 nohup 执行并写日志, 本机轮询
bash tools/run_device_bg.sh x86_64 <frames_dir> <host_out_dir> --max-size 800 --overlap 5

# 用独立实现的 Python COLMAP 读取器交叉校验产物
python tools/inspect_colmap.py <colmap_dir>
```

实测数据见 `docs/artifacts/native-sfm.md` 第 8 节。

## 依赖

- Android NDK 27.2.12479018 (LLVM/Clang, `c++_static`)
- CMake + Ninja
- OpenCV 4.10.0 静态库 (`core,imgproc,features2d,calib3d,imgcodecs,flann`)
- Eigen 3.4 (header-only, 已 vendor 到 `third_party/eigen`)

详见 `diandi-gaosi/docs/artifacts/native-sfm.md`。
