#pragma once

#include <string>
#include <vector>

#include <opencv2/core.hpp>

#include "model.h"
#include "progress.h"

namespace iii_sfm {

struct MatchOptions {
  int overlap = 5;              // 序列匹配窗口: 第 i 帧与 i+1 .. i+overlap 匹配
  bool exhaustive = false;      // 帧数很少时可全匹配
  double ratio_test = 0.8;      // Lowe 比值
  bool cross_check = true;      // 互相最近邻
  int min_matches = 20;         // 低于该数的匹配对直接丢弃
  double ransac_pixel_thresh = 1.5;  // F 矩阵 RANSAC 阈值(像素)
  int max_matches_kept = 1200;  // 每个匹配对最多保留的匹配数
};

struct ImagePair {
  int i = 0;  // 0-based 图像下标
  int j = 0;
  std::vector<std::pair<int, int>> matches;  // (keypoint_i, keypoint_j), 已经几何校验
  int num_raw = 0;
  int num_inliers = 0;
};

// 对一组帧做序列(或全)匹配 + 比值测试 + F 矩阵 RANSAC 几何校验。
bool matchImages(const Scene& scene, const std::vector<cv::Mat>& descriptors,
                 const MatchOptions& opt, std::vector<ImagePair>* pairs, const ProgressFn& progress,
                 std::string* err);

}  // namespace iii_sfm
