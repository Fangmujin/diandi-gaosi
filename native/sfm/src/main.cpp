// libiii_sfm —— 点点高斯 SfM 引擎入口
//
// 用法:
//   libiii_sfm.so --images <dir> --out <colmapDir> [--max-size 1600] [--overlap 5]
//                  [--features 2048] [--exhaustive] [--txt] [--verbose]
//
// 约定(UI 侧唯一依赖):
//   1) stdout 每行一个 JSON 进度
//      {"stage":"features|matching|mapping","current":N,"total":N,"registered":N,"points":N}
//   2) 产物 <colmapDir>/cameras.bin, images.bin, points3D.bin (COLMAP 二进制, 无需转换)
#include <dirent.h>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <opencv2/core.hpp>

#include "colmap_io.h"
#include "feature_extract.h"
#include "incremental.h"
#include "matching.h"
#include "model.h"
#include "progress.h"

namespace {

void emitProgress(const char* stage, int current, int total, int registered, int points) {
  std::printf("{\"stage\":\"%s\",\"current\":%d,\"total\":%d,\"registered\":%d,\"points\":%d}\n",
              stage, current, total, registered, points);
  std::fflush(stdout);
}

void emitEvent(const char* key, const std::string& value) {
  std::printf("{\"stage\":\"%s\",\"message\":\"%s\"}\n", key, value.c_str());
  std::fflush(stdout);
}

void usage() {
  std::fprintf(stderr,
               "libiii_sfm - incremental SfM for 点点高斯\n"
               "  --images <dir>     输入抽帧图片目录 (必填)\n"
               "  --out <dir>        输出 COLMAP 目录 (必填)\n"
               "  --max-size <px>    长边降采样上限, 默认 1600\n"
               "  --overlap <n>      序列匹配窗口, 默认 5\n"
               "  --features <n>     每帧最多特征数, 默认 2048\n"
               "  --exhaustive       帧数很少时使用全匹配\n"
               "  --txt              额外输出 cameras.txt/images.txt/points3D.txt\n"
               "  --verbose          把调试日志打到 stderr\n");
}

bool hasImageExt(const std::string& name) {
  const size_t dot = name.find_last_of('.');
  if (dot == std::string::npos) return false;
  std::string ext = name.substr(dot + 1);
  std::transform(ext.begin(), ext.end(), ext.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return ext == "jpg" || ext == "jpeg" || ext == "png" || ext == "bmp" || ext == "webp";
}

bool listImages(const std::string& dir, std::vector<std::string>* out, std::string* err) {
  DIR* d = opendir(dir.c_str());
  if (!d) {
    if (err) *err = "cannot open image dir: " + dir;
    return false;
  }
  while (struct dirent* e = readdir(d)) {
    const std::string name(e->d_name);
    if (name == "." || name == "..") continue;
    if (!hasImageExt(name)) continue;
    out->push_back(dir + "/" + name);
  }
  closedir(d);
  std::sort(out->begin(), out->end());
  return !out->empty();
}

}  // namespace

int main(int argc, char** argv) {
  std::string images_dir, out_dir;
  int max_size = 1600;
  int overlap = 5;
  int max_features = 2048;
  bool exhaustive = false;
  bool write_txt = false;
  bool verbose = false;

  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    auto next = [&](void) -> std::string {
      if (i + 1 >= argc) return "";
      return argv[++i];
    };
    if (a == "--images") {
      images_dir = next();
    } else if (a == "--out") {
      out_dir = next();
    } else if (a == "--max-size") {
      max_size = std::atoi(next().c_str());
    } else if (a == "--overlap") {
      overlap = std::atoi(next().c_str());
    } else if (a == "--features") {
      max_features = std::atoi(next().c_str());
    } else if (a == "--exhaustive") {
      exhaustive = true;
    } else if (a == "--txt") {
      write_txt = true;
    } else if (a == "--verbose") {
      verbose = true;
    } else if (a == "-h" || a == "--help") {
      usage();
      return 0;
    } else {
      std::fprintf(stderr, "unknown arg: %s\n", a.c_str());
      usage();
      return 2;
    }
  }
  if (images_dir.empty() || out_dir.empty()) {
    usage();
    return 2;
  }

  const auto t_start = std::chrono::steady_clock::now();

  // ---- 列举图片 ----
  std::vector<std::string> paths;
  std::string err;
  if (!listImages(images_dir, &paths, &err)) {
    emitEvent("error", err.empty() ? "no images found" : err);
    std::fprintf(stderr, "ERROR: %s\n", err.c_str());
    return 3;
  }
  const int n = static_cast<int>(paths.size());
  if (verbose) std::fprintf(stderr, "[main] %d images\n", n);

  // ---- 建场景(先占位, 逐帧填特征) ----
  iii_sfm::Scene scene;
  scene.images.resize(n);
  for (int i = 0; i < n; ++i) {
    iii_sfm::Image& im = scene.images[i];
    im.image_id = i + 1;
    im.camera_id = 1;
    const size_t slash = paths[i].find_last_of("/\\");
    im.name = (slash == std::string::npos) ? paths[i] : paths[i].substr(slash + 1);
  }

  // ---- 特征提取 ----
  iii_sfm::FeatureOptions fopt;
  fopt.max_image_size = max_size > 0 ? max_size : 1600;
  fopt.max_features = max_features > 0 ? max_features : 2048;
  std::vector<iii_sfm::FrameFeatures> feats(n);
  int features_ok = 0;
  for (int i = 0; i < n; ++i) {
    std::string e;
    if (!iii_sfm::extractFeatures(paths[i], fopt, scene.images[i], feats[i], &e)) {
      if (verbose) std::fprintf(stderr, "[main] skip %s: %s\n", paths[i].c_str(), e.c_str());
      continue;
    }
    ++features_ok;
    emitProgress("features", i + 1, n, 0, 0);
  }
  if (features_ok < 2) {
    emitEvent("error", "fewer than 2 images with valid features");
    return 4;
  }

  // ---- 建立相机(按原始分辨率分组; 视频帧一般是同一台相机) ----
  {
    std::vector<std::pair<int, int>> sizes;  // (orig_w, orig_h)
    for (int i = 0; i < n; ++i) {
      if (scene.images[i].points2D.empty()) continue;
      bool found = false;
      for (const auto& s : sizes) {
        if (s.first == scene.images[i].orig_width && s.second == scene.images[i].orig_height) {
          found = true;
          break;
        }
      }
      if (!found) sizes.emplace_back(scene.images[i].orig_width, scene.images[i].orig_height);
    }
    for (size_t c = 0; c < sizes.size(); ++c) {
      iii_sfm::Camera cam;
      cam.camera_id = static_cast<int>(c) + 1;
      cam.out_width = sizes[c].first;
      cam.out_height = sizes[c].second;
      // 用该组第一张图的处理分辨率作为内参基准
      int pw = sizes[c].first, ph = sizes[c].second;
      for (int i = 0; i < n; ++i) {
        if (scene.images[i].orig_width == sizes[c].first &&
            scene.images[i].orig_height == sizes[c].second &&
            !scene.images[i].points2D.empty()) {
          pw = scene.images[i].width;
          ph = scene.images[i].height;
          break;
        }
      }
      cam.width = pw;
      cam.height = ph;
      cam.out_scale = static_cast<double>(cam.out_width) / static_cast<double>(cam.width);
      // COLMAP 的缺省焦距先验: 1.2 * max(width, height)
      cam.f = 1.2 * static_cast<double>(std::max(pw, ph));
      cam.cx = 0.5 * static_cast<double>(pw) + 0.5;
      cam.cy = 0.5 * static_cast<double>(ph) + 0.5;
      cam.k1 = 0.0;
      scene.cameras.push_back(cam);
    }
    for (int i = 0; i < n; ++i) {
      for (const iii_sfm::Camera& c : scene.cameras) {
        if (c.out_width == scene.images[i].orig_width && c.out_height == scene.images[i].orig_height) {
          scene.images[i].camera_id = c.camera_id;
          break;
        }
      }
    }
  }

  // ---- 匹配 ----
  iii_sfm::MatchOptions mopt;
  mopt.overlap = overlap > 0 ? overlap : 5;
  mopt.exhaustive = exhaustive;
  std::vector<iii_sfm::ImagePair> pairs;
  std::vector<cv::Mat> descs(n);
  for (int i = 0; i < n; ++i) descs[i] = feats[i].descriptors;
  if (!iii_sfm::matchImages(scene, descs, mopt, &pairs, emitProgress, &err)) {
    emitEvent("error", err);
    return 5;
  }
  if (verbose) std::fprintf(stderr, "[main] %zu verified pairs\n", pairs.size());
  if (pairs.empty()) {
    emitEvent("error", "no geometrically verified image pairs");
    return 6;
  }
  descs.clear();
  descs.shrink_to_fit();

  // ---- 增量重建 ----
  iii_sfm::SfmOptions sopt;
  sopt.verbose = verbose;
  iii_sfm::SfmResult result;
  if (!iii_sfm::runIncrementalSfM(scene, pairs, sopt, emitProgress, &result, &err)) {
    emitEvent("error", err.empty() ? "reconstruction failed" : err);
    std::fprintf(stderr, "ERROR: %s\n", err.c_str());
    return 7;
  }

  // ---- 给三维点取色 ----
  for (iii_sfm::Point3D& p : scene.points3D) {
    unsigned int acc[3] = {0, 0, 0};
    int cnt = 0;
    for (const auto& e : p.track) {
      const iii_sfm::Image& im = scene.image(e.first);
      if (!im.registered) continue;
      unsigned char rgb[3];
      if (iii_sfm::sampleColor(feats[e.first - 1], im.points2D[e.second].x(),
                               im.points2D[e.second].y(), rgb)) {
        acc[0] += rgb[0];
        acc[1] += rgb[1];
        acc[2] += rgb[2];
        ++cnt;
      }
    }
    if (cnt > 0) {
      p.rgb[0] = static_cast<unsigned char>(acc[0] / cnt);
      p.rgb[1] = static_cast<unsigned char>(acc[1] / cnt);
      p.rgb[2] = static_cast<unsigned char>(acc[2] / cnt);
    }
  }

  // ---- 写出 ----
  if (!iii_sfm::writeColmapBinary(out_dir, scene, &err)) {
    emitEvent("error", err);
    return 8;
  }
  if (write_txt) iii_sfm::writeColmapText(out_dir, scene, &err);

  // ---- 回读自检 ----
  iii_sfm::ModelStats stats;
  std::string rerr;
  const bool verified = iii_sfm::readColmapStats(out_dir, &stats, &rerr);

  const auto t_end = std::chrono::steady_clock::now();
  const double elapsed_ms =
      std::chrono::duration<double, std::milli>(t_end - t_start).count();

  emitProgress("mapping", result.num_registered, n, result.num_registered, result.num_points3D);
  std::printf(
      "{\"stage\":\"done\",\"current\":%d,\"total\":%d,\"registered\":%d,\"points\":%d,"
      "\"observations\":%d,\"mean_reproj_px\":%.3f,\"init_tri_angle_deg\":%.2f,"
      "\"focal\":%.3f,\"k1\":%.5f,\"elapsed_ms\":%.0f,\"verified_bin\":%s,"
      "\"cameras_read\":%zu,\"images_read\":%zu,\"points_read\":%zu,"
      "\"obs_read\":%zu,\"mean_error_read\":%.3f}\n",
      result.num_registered, n, result.num_registered, result.num_points3D,
      result.num_observations, result.mean_reproj_error_px, result.init_tri_angle_deg,
      scene.cameras.empty() ? 0.0 : scene.cameras[0].f * scene.cameras[0].out_scale,
      scene.cameras.empty() ? 0.0 : scene.cameras[0].k1, elapsed_ms,
      verified ? "true" : "false", stats.num_cameras, stats.num_images, stats.num_points3D,
      stats.num_observations, stats.mean_reproj_error);
  std::fflush(stdout);
  return 0;
}
