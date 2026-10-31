#pragma once

#include <string>
#include <vector>

#include <opencv2/core.hpp>

#include "model.h"

namespace iii_sfm {

struct FeatureOptions {
  int max_image_size = 1600;      // 长边降采样上限(像素)
  int max_features = 2048;        // 每帧保留的特征数
  double contrast_threshold = 0.02;
  double edge_threshold = 12.0;
  int octave_layers = 3;
  double sigma = 1.6;
  bool clahe = false;             // 低对比度视频可开启
};

// 每帧的特征与缓存
struct FrameFeatures {
  cv::Mat descriptors;   // CV_32F, 每行一个 128 维 SIFT 描述子
  cv::Mat thumb;         // 彩色缩略图, 用于给三维点取色
  double thumb_scale = 1.0;
};

// 读图 -> 降采样 -> SIFT 检测与描述。
// 成功时填充 img.points2D / img.point3D_ids / feat.descriptors。
bool extractFeatures(const std::string& path, const FeatureOptions& opt, Image& img,
                     FrameFeatures& feat, std::string* err);

// 从缩略图取某个(处理分辨率)坐标处的颜色
bool sampleColor(const FrameFeatures& feat, double x, double y, unsigned char rgb[3]);

}  // namespace iii_sfm
