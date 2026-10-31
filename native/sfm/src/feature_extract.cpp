#include "feature_extract.h"

#include <algorithm>
#include <cmath>

#include <opencv2/features2d.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

namespace iii_sfm {

bool extractFeatures(const std::string& path, const FeatureOptions& opt, Image& img,
                     FrameFeatures& feat, std::string* err) {
  cv::Mat bgr = cv::imread(path, cv::IMREAD_COLOR);
  if (bgr.empty()) {
    if (err) *err = "imread failed: " + path;
    return false;
  }
  img.orig_width = bgr.cols;
  img.orig_height = bgr.rows;

  // ---- 降采样 ----
  cv::Mat work = bgr;
  const int long_edge = std::max(bgr.cols, bgr.rows);
  double s = 1.0;
  if (opt.max_image_size > 0 && long_edge > opt.max_image_size) {
    s = static_cast<double>(opt.max_image_size) / static_cast<double>(long_edge);
    cv::resize(bgr, work, cv::Size(), s, s, cv::INTER_AREA);
  }
  img.width = work.cols;
  img.height = work.rows;
  img.scale = static_cast<double>(img.orig_width) / static_cast<double>(img.width);

  // ---- 灰度 ----
  cv::Mat gray;
  cv::cvtColor(work, gray, cv::COLOR_BGR2GRAY);
  if (opt.clahe) {
    cv::Ptr<cv::CLAHE> clahe = cv::createCLAHE(2.0, cv::Size(8, 8));
    clahe->apply(gray, gray);
  }

  // ---- SIFT ----
  cv::Ptr<cv::SIFT> sift = cv::SIFT::create(opt.max_features, opt.octave_layers,
                                            opt.contrast_threshold, opt.edge_threshold, opt.sigma);
  std::vector<cv::KeyPoint> kps;
  cv::Mat desc;
  sift->detectAndCompute(gray, cv::noArray(), kps, desc);

  img.points2D.clear();
  img.point3D_ids.clear();
  img.points2D.reserve(kps.size());
  img.point3D_ids.reserve(kps.size());
  for (const cv::KeyPoint& k : kps) {
    img.points2D.emplace_back(static_cast<double>(k.pt.x), static_cast<double>(k.pt.y));
    img.point3D_ids.push_back(-1);
  }

  // ---- 缩略图(仅用于给三维点取色) ----
  const int tw = std::max(1, img.width / 8);
  const int th = std::max(1, img.height / 8);
  cv::resize(work, feat.thumb, cv::Size(tw, th), 0, 0, cv::INTER_AREA);
  feat.thumb_scale = 1.0 / 8.0;

  feat.descriptors = desc;
  return true;
}

bool sampleColor(const FrameFeatures& feat, double x, double y, unsigned char rgb[3]) {
  if (feat.thumb.empty()) return false;
  const int tx = static_cast<int>(x * feat.thumb_scale);
  const int ty = static_cast<int>(y * feat.thumb_scale);
  if (tx < 0 || ty < 0 || tx >= feat.thumb.cols || ty >= feat.thumb.rows) return false;
  const cv::Vec3b p = feat.thumb.at<cv::Vec3b>(ty, tx);
  rgb[0] = p[2];  // BGR -> RGB
  rgb[1] = p[1];
  rgb[2] = p[0];
  return true;
}

}  // namespace iii_sfm
