// 特征匹配:
//   1) 序列(overlap 窗口)或全匹配, 复杂度 O(N*overlap) 而非 O(N^2)
//   2) BFMatcher k=2 + Lowe 比值测试 + 互相最近邻
//   3) 基础矩阵 F 的 RANSAC 几何校验(对像素坐标, 不依赖内参)
#include "matching.h"

#include <algorithm>
#include <cmath>
#include <vector>

#include <opencv2/calib3d.hpp>
#include <opencv2/core.hpp>
#include <opencv2/features2d.hpp>

namespace iii_sfm {

namespace {

// 比值测试 + 可选的互相最近邻。
// 注意: cv::BFMatcher 的 crossCheck 与 knnMatch(K=2) 互斥 —— OpenCV 的 batchDistance
// 在 crosscheck 分支里有 CV_Assert(K == 1), 所以互相最近邻只能自己实现。
void rawMatch(const cv::Mat& da, const cv::Mat& db, double ratio, bool cross_check,
              std::vector<std::pair<int, int>>* out) {
  out->clear();
  if (da.empty() || db.empty() || da.rows < 2 || db.rows < 2) return;

  cv::BFMatcher matcher(cv::NORM_L2, false);
  std::vector<std::vector<cv::DMatch>> knn_ab;
  matcher.knnMatch(da, db, knn_ab, 2);

  std::vector<std::pair<int, int>> ab;
  ab.reserve(knn_ab.size());
  for (const auto& v : knn_ab) {
    if (v.size() < 2) continue;
    if (v[0].distance > ratio * v[1].distance) continue;
    ab.emplace_back(v[0].queryIdx, v[0].trainIdx);
  }

  if (!cross_check) {
    *out = std::move(ab);
    return;
  }

  // 反过来再查一次, 只保留双向一致的匹配
  std::vector<std::vector<cv::DMatch>> knn_ba;
  matcher.knnMatch(db, da, knn_ba, 2);
  std::vector<int> best_b(db.rows, -1);
  for (const auto& v : knn_ba) {
    if (v.empty()) continue;
    if (v.size() >= 2 && v[0].distance > ratio * v[1].distance) continue;
    best_b[v[0].queryIdx] = v[0].trainIdx;
  }
  for (const auto& m : ab) {
    if (best_b[m.second] == m.first) out->push_back(m);
  }
}

}  // namespace

bool matchImages(const Scene& scene, const std::vector<cv::Mat>& descriptors,
                 const MatchOptions& opt, std::vector<ImagePair>* pairs, const ProgressFn& progress,
                 std::string* err) {
  pairs->clear();
  const int n = static_cast<int>(scene.images.size());
  if (n < 2) {
    if (err) *err = "need at least 2 images";
    return false;
  }

  // ---- 枚举候选对 ----
  std::vector<std::pair<int, int>> candidates;
  if (opt.exhaustive || n <= 8) {
    for (int i = 0; i < n; ++i)
      for (int j = i + 1; j < n; ++j) candidates.emplace_back(i, j);
  } else {
    const int ov = std::max(1, opt.overlap);
    for (int i = 0; i < n; ++i) {
      for (int d = 1; d <= ov; ++d) {
        const int j = i + d;
        if (j < n) candidates.emplace_back(i, j);
      }
      // 大跨度的回环对, 帮助闭合环绕物体的轨迹
      for (int d = ov * 2; d <= ov * 4; d += ov) {
        if (i + d < n) candidates.emplace_back(i, i + d);
      }
    }
  }
  // 去重
  std::sort(candidates.begin(), candidates.end());
  candidates.erase(std::unique(candidates.begin(), candidates.end()), candidates.end());

  const int total = static_cast<int>(candidates.size());
  for (int c = 0; c < total; ++c) {
    const int i = candidates[c].first;
    const int j = candidates[c].second;
    if (progress) progress("matching", c + 1, total, 0, 0);

    std::vector<std::pair<int, int>> raw;
    rawMatch(descriptors[i], descriptors[j], opt.ratio_test, opt.cross_check, &raw);
    if (static_cast<int>(raw.size()) < opt.min_matches) continue;

    // 按距离排序没必要, 这里按索引稳定截断
    if (static_cast<int>(raw.size()) > opt.max_matches_kept)
      raw.resize(opt.max_matches_kept);

    // ---- F 矩阵 RANSAC ----
    std::vector<cv::Point2f> pa, pb;
    pa.reserve(raw.size());
    pb.reserve(raw.size());
    for (const auto& m : raw) {
      pa.emplace_back(static_cast<float>(scene.images[i].points2D[m.first].x()),
                      static_cast<float>(scene.images[i].points2D[m.first].y()));
      pb.emplace_back(static_cast<float>(scene.images[j].points2D[m.second].x()),
                      static_cast<float>(scene.images[j].points2D[m.second].y()));
    }
    cv::Mat inlier_mask;
    cv::Mat F = cv::findFundamentalMat(pa, pb, cv::FM_RANSAC, opt.ransac_pixel_thresh, 0.999,
                                       20000, inlier_mask);
    if (F.empty() || inlier_mask.empty()) continue;

    ImagePair pair;
    pair.i = i;
    pair.j = j;
    pair.num_raw = static_cast<int>(raw.size());
    const unsigned char* mask = inlier_mask.ptr<unsigned char>();
    for (size_t k = 0; k < raw.size(); ++k) {
      if (!mask[k]) continue;
      pair.matches.push_back(raw[k]);
    }
    pair.num_inliers = static_cast<int>(pair.matches.size());
    if (pair.num_inliers < opt.min_matches) continue;
    pairs->push_back(std::move(pair));
  }
  return true;
}

}  // namespace iii_sfm
