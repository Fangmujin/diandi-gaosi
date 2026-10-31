#pragma once

#include <string>
#include <vector>

#include "matching.h"
#include "model.h"
#include "progress.h"

namespace iii_sfm {

struct SfmOptions {
  // 初始像对
  int min_init_matches = 60;        // 初始像对至少需要的几何内点
  double init_min_tri_angle = 8.0;  // 初始像对最小三角化角(度)
  int init_candidates = 12;         // 最多尝试的初始像对数量
  // 增量注册
  int min_pnp_corrs = 20;           // 参与 PnP 的最小 2D-3D 对应数
  int min_pnp_inliers = 18;         // PnP RANSAC 至少需要的内点数
  double pnp_pixel_thresh = 3.0;    // PnP RANSAC 阈值(像素)
  // 三角化
  double min_tri_angle_deg = 1.5;
  double max_reproj_error_px = 3.0;  // 三角化/过滤的重投影误差上限(像素)
  int max_track_views = 12;          // 单个 track 参与三角化的最大视角数
  // 平差
  int ba_every = 2;                  // 每注册多少张图做一次全局 BA
  int ba_max_iterations = 25;
  double ba_huber_px = 5.0;
  bool refine_intrinsics = true;
  int ba_final_iterations = 40;
  bool verbose = false;
};

struct SfmResult {
  int num_registered = 0;
  int num_points3D = 0;
  int num_observations = 0;
  double mean_reproj_error_px = 0.0;
  double init_tri_angle_deg = 0.0;
  double ba_cost_before = 0.0;
  double ba_cost_after = 0.0;
  int init_pair[2] = {-1, -1};
};

// 增量式 SfM: 初始像对 -> PnP 逐帧注册 -> 三角化 -> 全局 BA -> 过滤。
bool runIncrementalSfM(Scene& scene, const std::vector<ImagePair>& pairs, const SfmOptions& opt,
                       const ProgressFn& progress, SfmResult* result, std::string* err);

}  // namespace iii_sfm
