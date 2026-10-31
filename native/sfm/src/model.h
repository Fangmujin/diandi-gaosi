// 点点高斯 SfM 引擎 —— 核心数据结构与相机模型
//
// 设计要点:
//  * 相机模型为 COLMAP SIMPLE_RADIAL (model_id = 2), 参数 (f, cx, cy, k1)。
//  * 位姿采用 COLMAP 约定: qvec/tvec 把世界点变换到相机坐标系 (world-to-camera)。
//  * 图像内部所有 2D 点都保存在「处理分辨率」(降采样后) 坐标系里,
//    写出 cameras.bin/images.bin 时统一按 scale 还原到「原始分辨率」,
//    保证 COLMAP 目录里的 width/height 与磁盘上的图片尺寸一致。
#pragma once

#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

#include <Eigen/Dense>

namespace iii_sfm {

// ---------------------------------------------------------------------------
// 相机内参 (COLMAP SIMPLE_RADIAL)
// ---------------------------------------------------------------------------
struct Camera {
  int camera_id = 1;
  // 重建过程中使用「处理分辨率」下的尺寸与内参
  int width = 0;
  int height = 0;
  double f = 0.0;
  double cx = 0.0;
  double cy = 0.0;
  double k1 = 0.0;  // 径向畸变
  // 写出时还原到「原始分辨率」
  int out_width = 0;
  int out_height = 0;
  double out_scale = 1.0;  // 原始 / 处理
};

// ---------------------------------------------------------------------------
// 图像 + 二维观测
// ---------------------------------------------------------------------------
struct Image {
  int image_id = 0;  // COLMAP 1-based
  std::string name;
  int camera_id = 1;

  int orig_width = 0;   // 磁盘原图尺寸
  int orig_height = 0;
  int width = 0;        // 处理分辨率
  int height = 0;
  double scale = 1.0;   // orig / processed

  bool registered = false;
  Eigen::Matrix3d R = Eigen::Matrix3d::Identity();  // world-to-camera
  Eigen::Vector3d t = Eigen::Vector3d::Zero();

  std::vector<Eigen::Vector2d> points2D;  // 处理分辨率下的坐标
  std::vector<long long> point3D_ids;     // -1 表示未关联
};

// ---------------------------------------------------------------------------
// 三维点 (COLMAP points3D)
// ---------------------------------------------------------------------------
struct Point3D {
  long long point3D_id = 0;
  Eigen::Vector3d xyz = Eigen::Vector3d::Zero();
  unsigned char rgb[3] = {255, 255, 255};
  double error = 0.0;                             // 平均重投影误差(像素)
  std::vector<std::pair<int, int>> track;         // (image_id, point2D_idx)
};

// ---------------------------------------------------------------------------
// 稀疏重建场景
// ---------------------------------------------------------------------------
struct Scene {
  std::vector<Camera> cameras;   // cameras[i].camera_id == i + 1
  std::vector<Image> images;     // images[i].image_id == i + 1
  std::vector<Point3D> points3D;
  long long next_point3D_id = 1;

  Image& image(int image_id) { return images[image_id - 1]; }
  const Image& image(int image_id) const { return images[image_id - 1]; }
};

// ---------------------------------------------------------------------------
// 相机模型: 世界点 -> 像素
//   Xc = R * X + t
//   x  = Xc.x / Xc.z, y = Xc.y / Xc.z
//   r2 = x^2 + y^2,  d = 1 + k1 * r2
//   u  = f * d * x + cx,  v = f * d * y + cy
// 返回 false 表示点在相机后方或退化。
// ---------------------------------------------------------------------------
inline bool projectPoint(const Camera& cam, const Eigen::Matrix3d& R,
                         const Eigen::Vector3d& t, const Eigen::Vector3d& X,
                         Eigen::Vector2d* out_uv, Eigen::Vector3d* out_Xc = nullptr) {
  const Eigen::Vector3d Xc = R * X + t;
  if (out_Xc) *out_Xc = Xc;
  if (Xc.z() <= 1e-9) return false;
  const double invz = 1.0 / Xc.z();
  const double x = Xc.x() * invz;
  const double y = Xc.y() * invz;
  const double r2 = x * x + y * y;
  const double d = 1.0 + cam.k1 * r2;
  if (out_uv) {
    (*out_uv)[0] = cam.f * d * x + cam.cx;
    (*out_uv)[1] = cam.f * d * y + cam.cy;
  }
  return true;
}

// 去畸变: 像素坐标(处理分辨率) -> 归一化相机坐标 (k1 径向模型)
// 采用牛顿迭代反解 r*(1 + k1*r^2) = r_d
inline Eigen::Vector2d undistortNormalized(const Camera& cam, const Eigen::Vector2d& px) {
  const double xd = (px.x() - cam.cx) / cam.f;
  const double yd = (px.y() - cam.cy) / cam.f;
  const double rd = std::sqrt(xd * xd + yd * yd);
  double r = rd;
  if (std::fabs(cam.k1) > 1e-12 && rd > 1e-12) {
    for (int it = 0; it < 8; ++it) {
      const double f_r = r * (1.0 + cam.k1 * r * r) - rd;
      const double df = 1.0 + 3.0 * cam.k1 * r * r;
      if (std::fabs(df) < 1e-12) break;
      const double step = f_r / df;
      r -= step;
      if (std::fabs(step) < 1e-12) break;
    }
    if (!(r > 0.0) || !std::isfinite(r)) r = rd;
  }
  const double s = rd > 1e-12 ? r / rd : 1.0;
  return Eigen::Vector2d(xd * s, yd * s);
}

// 相机中心 (世界坐标): C = -R^T * t
inline Eigen::Vector3d cameraCenter(const Eigen::Matrix3d& R, const Eigen::Vector3d& t) {
  return -R.transpose() * t;
}

// ---------------------------------------------------------------------------
// SO(3) / 四元数工具
// ---------------------------------------------------------------------------

// COLMAP 四元数顺序为 (w, x, y, z);Eigen::Quaterniond 内部为 (x, y, z, w)。
inline Eigen::Vector4d qvecFromMatrix(const Eigen::Matrix3d& R) {
  const Eigen::Quaterniond q(R);
  return Eigen::Vector4d(q.w(), q.x(), q.y(), q.z());
}

inline Eigen::Matrix3d matrixFromQvec(const Eigen::Vector4d& qvec) {
  const Eigen::Quaterniond q(qvec[0], qvec[1], qvec[2], qvec[3]);
  return q.normalized().toRotationMatrix();
}

// 罗德里格斯指数映射: 旋转向量 -> 旋转矩阵
inline Eigen::Matrix3d expMapRot(const Eigen::Vector3d& v) {
  const double theta2 = v.squaredNorm();
  const Eigen::Matrix3d skew =
      (Eigen::Matrix3d() << 0.0, -v.z(), v.y(), v.z(), 0.0, -v.x(), -v.y(), v.x(), 0.0)
          .finished();
  if (theta2 < 1e-12) {
    return Eigen::Matrix3d::Identity() + skew;
  }
  const double theta = std::sqrt(theta2);
  return Eigen::Matrix3d::Identity() + (std::sin(theta) / theta) * skew +
         ((1.0 - std::cos(theta)) / theta2) * (skew * skew);
}

// 旋转矩阵 -> 旋转向量
inline Eigen::Vector3d logMapRot(const Eigen::Matrix3d& R) {
  const Eigen::AngleAxisd aa(R);
  return aa.axis() * aa.angle();
}

}  // namespace iii_sfm
