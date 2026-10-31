#pragma once

#include <string>
#include <vector>

#include "model.h"

namespace iii_sfm {

// 按 COLMAP 官方二进制布局写出 cameras.bin / images.bin / points3D.bin。
// 字段级对齐 scripts/python/read_write_model.py:
//
//  cameras.bin:
//    uint64 num_cameras
//    { int32 camera_id, int32 model_id, uint64 width, uint64 height,
//      double[num_params] params } * num_cameras
//  images.bin:
//    uint64 num_reg_images
//    { int32 image_id, double[4] qvec(w,x,y,z), double[3] tvec, int32 camera_id,
//      uint64 name_len, char[name_len] name(含 '\0'),
//      uint64 num_points2D, { double x, double y, int64 point3D_id } * n } * num_reg
//  points3D.bin:
//    uint64 num_points3D
//    { int64 point3D_id, double[3] xyz, uint8[3] rgb, double error,
//      uint64 track_len, { int32 image_id, int32 point2D_idx } * track_len } * n
//
// 所有数值为小端(little endian)。
bool writeColmapBinary(const std::string& out_dir, const Scene& scene, std::string* err);

// 可选: 额外写出人类可读的 txt 版本,便于调试。
bool writeColmapText(const std::string& out_dir, const Scene& scene, std::string* err);

// 回读校验: 解析刚写出的三个 bin,统计相机/图像/三维点数量。
struct ModelStats {
  size_t num_cameras = 0;
  size_t num_images = 0;
  size_t num_points3D = 0;
  size_t num_observations = 0;
  double mean_reproj_error = 0.0;
};
bool readColmapStats(const std::string& out_dir, ModelStats* stats, std::string* err);

}  // namespace iii_sfm
