# std_dataset_11 —— 标准验证数据集（SfM 真产物，可直接喂 brush）

给 brush-headless 做「真 GPU 能否真的跑出 PLY」验证用的最小数据集。
里面的 `sparse/0/` 三件套**不是手搓的假数据**，是 `libiii_sfm.so` 在 Android x86_64 模拟器上
对真实视频抽帧跑完 SfM 之后写出来的真实结果。

## 目录布局（COLMAP 约定）

```
std_dataset_11/
  images/                     <- 11 张 JPEG 原帧
    frame_0001.jpg ... frame_0041.jpg
  sparse/0/
    cameras.bin               <- 64 B
    images.bin                <- 447,326 B
    points3D.bin              <- 160,515 B
```

brush 的 colmap loader（`brush-dataset/src/formats/colmap.rs`）行为：
先在 VFS 里找 `cameras.bin`（找不到才退回 `cameras.txt`），用它的父目录当 sparse 目录；
然后按 `images.bin` 里每张图的 `name` 字段，用 `files_ending_in(name)` 在**整个数据集目录**
里找对应图片。所以 `images/<name>` 的文件名必须和 `images.bin` 里的 `name` **逐字节一致**。

## 关键数字

| 项 | 值 |
|---|---|
| `images/` 里的文件数 | 11 |
| `images.bin` 里注册（有 pose）的图 | **10** |
| 未被引用的一张 | `frame_0025.jpg`（SfM 没通过 PnP 内点门槛，所以没写进 images.bin；留着无害，brush 会忽略） |
| 每帧分辨率 | **640 × 1136**（11 张全部一致，竖屏） |
| `images/` 总字节 | 904,461 B |
| name 字段格式样例 | `frame_0001.jpg`（`frame_%04d.jpg`，无路径前缀，NUL 结尾，二进制里**不带长度前缀**） |
| name 完整性 | images.bin 引用的 10 个名字在 `images/` 里**全部命中，0 缺失** |

`images.bin` 里引用的 10 个名字：

```
frame_0001.jpg  frame_0005.jpg  frame_0009.jpg  frame_0013.jpg  frame_0017.jpg
frame_0021.jpg  frame_0029.jpg  frame_0033.jpg  frame_0037.jpg  frame_0041.jpg
```

## 相机与点云（用来判断训练结果的尺度对不对）

- 相机模型：**SIMPLE_RADIAL**（model_id = 2），`params = (f, cx, cy, k1)`
- 解出的内参（**原始分辨率 640×1136 下**）：
  `f = 1362.967 px`，`cx = 320.555`，`cy = 568.341`，`k1 = 0.0396`
  → FOV 约 `2·atan(640/2/1362.97) ≈ 26.4°`（水平方向）
- 稀疏点云（points3D.bin）：**2153 个点**，track 长度 min 2 / mean 2.94 / max 9
- **预期 point bbox**（世界坐标， CAM 坐标系由第一帧固定）：
  `x[-0.44, 1.73]  y[-1.22, 0.52]  z[2.70, 3.98]`
  → 点云整体尺度在 **1~4 个单位**量级，深度 z 为正、约 2.7–4.0。
  如果训练出来的 splat bbox 跟这个差一个数量级以上（比如 1e2 或 1e-2），
  说明坐标系/尺子有问题，先查相机，不要急着查训练。
- 相机轨迹：10 个光心，相邻步长 min 0.3554 / mean 0.9715 / max 1.6923，包围盒跨度 2.6754
  （即相机绕着物体走了一圈，不是原地不动，也不是退化成一条线）
- `points3D.bin` 里存的重投影误差：mean 0.4496 px，p90 0.8958 px

## 数据来源

- 源视频：`ooosplat_input/54734.mp4`（640×1136，8.70 s，29.99 fps）
- 抽帧：ffmpeg `select='not(mod(n\,6))'` → 44 帧；本数据集取其等间隔 11 帧子集
- SfM 命令：
  `./libiii_sfm.so --images <frames> --out <colmap> --max-size 1024 --overlap 8`
- 运行环境：Android x86_64 模拟器（API 36.1），`libiii_sfm.so` x86_64，OpenCV 4.10.0

## tools/inspect_colmap.py 输出（PASS）

```
$ python diandi-gaosi/native/sfm/tools/inspect_colmap.py \
      diandi-gaosi/native/testdata/std_dataset_11/sparse/0

cameras : 1
   id=1 SIMPLE_RADIAL 640x1136 params=1362.967, 320.555, 568.458, 0.040
images  : 10 registered
points  : 2153
observations (from images) : 6333
observations (from tracks) : 6338
dangling point3D refs      : 0
camera center step dist    : min 0.3554  max 1.6923  mean 0.9715
camera center span (bbox)  : 2.6754
mean reproj error (stored) : 0.4496 px
p90  reproj error (stored) : 0.8958 px
track length               : min 2 max 9 mean 2.94
point bbox                 : x[-0.44,1.73] y[-1.22,0.52] z[2.70,3.98]
VERDICT: PASS
```

（`observations (from images)` 6333 与 `(from tracks)` 6338 差 5 条，是少数 2D 点在后续
filter 阶段被改挂到别的 point3D 所致；COLMAP 自身也允许这种情况。`dangling refs = 0`。）

另外还用 **COLMAP 3.8 官方 `scripts/python/read_write_model.py`**（第三方独立实现）
解析过同一份数据，10 images / 2153 points3D / 名字全部正常读出，无错。

## 喂给 brush 的方式

```bash
# headless（PIE，exec 不是 dlopen）
./libbrushheadless.so \
  --path <abs>/std_dataset_11 \
  --export-path /tmp/out.ply \
  --total-steps 30 --max-resolution 128 --max-splats 50000 \
  --sh-degree 0 --refine-every 10 --growth-stop-iter 25 \
  --export-every 10 --seed 42
```

`RUST_LOG=info` 下应该看到的日志（这是"COLMAP 被正确消费"的判据）：

```
Loading colmap dataset with 10 images
Loaded COLMAP image at path "images/frame_0001.jpg"      (×10，不应出现 Image not found)
Starting from colmap points 2153
Created init splat from points
Loaded 10 training views
```

### 已知坑：brush-headless 会 bail（不是数据问题）

当前 `brush-headless` 只要数据集里存在 `points3D.bin` 就会失败：
`brush-process/src/train_stream.rs:81` emit `ProcessMessage::ViewSplats`，
而 `brush-headless/src/main.rs:247` 把它当致命错误：

```
Error: <path> contains only ply files; a COLMAP dataset directory is required
EXIT=1
```

**验证训练本身时，先把 `sparse/0/points3D.bin` 挪走**（随机初始化也能跑），
或者让 headless 改成像 `brush-cli` 一样忽略 `ViewSplats`。
已经准备好一份去掉 points3D 的副本：`../ds_11_noinit/`。
