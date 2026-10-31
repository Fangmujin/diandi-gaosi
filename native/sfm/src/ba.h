#pragma once

#include "model.h"

namespace iii_sfm {

struct BAOptions {
  int max_iterations = 25;
  double huber_delta_px = 5.0;   // Huber 损失阈值(像素, 处理分辨率下)
  bool refine_intrinsics = true; // 是否优化 f/cx/cy/k1
  bool fix_first_camera = true;  // 固定第一台相机消除 6 自由度规范自由度
  double lambda_init = 1e-3;
  bool verbose = false;
};

struct BAResult {
  double cost_before = 0.0;
  double cost_after = 0.0;
  double mean_reproj_px = 0.0;
  int iterations = 0;
  int num_residuals = 0;
};

// 全局光束法平差 (Bundle Adjustment)
//   * 参数: 每张已注册图像的位姿 (左乘扰动, 6DoF) + 每台相机的内参 (4)
//   * 结构: 点采用 Schur 消元 (3x3 块对角), 相机侧为稠密 (6N+4C)
//   * 损失: Huber (按观测的 2 维残差范数加权)
BAResult bundleAdjust(Scene& scene, const BAOptions& opt);

// 计算所有已注册观测的重投影误差统计。
double meanReprojectionError(const Scene& scene, double* p90 = nullptr);

// 仅优化一个新注册图像的位姿(固定点与内参), 用于 PnP 后的快速细化。
bool refineSinglePose(const Scene& scene, int image_id, const BAOptions& opt,
                      Eigen::Matrix3d* R, Eigen::Vector3d* t);

}  // namespace iii_sfm
