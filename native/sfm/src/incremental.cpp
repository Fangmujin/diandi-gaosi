// 增量式 Structure-from-Motion
//
//   1) 由几何校验过的匹配构建 track (并查集)
//   2) 选取初始像对: F/E 矩阵 RANSAC + recoverPose + 三角化 + 视差角评估
//   3) 逐帧注册: 2D-3D 对应 -> PnP RANSAC (归一化坐标) -> 位姿细化
//   4) 三角化新 track (DLT + 前方交会的正负号/重投影/视差角检查)
//   5) 周期性全局光束法平差 + 外点过滤
#include "incremental.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <numeric>
#include <vector>

#include <Eigen/Dense>
#include <Eigen/SVD>

#include <opencv2/calib3d.hpp>
#include <opencv2/core.hpp>

#include "ba.h"

namespace iii_sfm {
namespace {

// points3D 按 id 递增保存, 用二分查找加速
int lowerBoundPoint(const Scene& s, long long id) {
  int lo = 0, hi = static_cast<int>(s.points3D.size());
  while (lo < hi) {
    const int mid = (lo + hi) / 2;
    if (s.points3D[mid].point3D_id < id) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

const Point3D* findPoint(const Scene& s, long long id) {
  const int i = lowerBoundPoint(s, id);
  if (i >= static_cast<int>(s.points3D.size()) || s.points3D[i].point3D_id != id) return nullptr;
  return &s.points3D[i];
}

// DLT 三角化: 输入归一化坐标与相机矩阵 [R|t]
bool triangulateDLT(const std::vector<Eigen::Matrix<double, 3, 4>>& Ps,
                    const std::vector<Eigen::Vector2d>& pts, Eigen::Vector3d* X) {
  const int n = static_cast<int>(Ps.size());
  if (n < 2) return false;
  Eigen::MatrixXd A(2 * n, 4);
  for (int i = 0; i < n; ++i) {
    A.row(2 * i) = pts[i].x() * Ps[i].row(2) - Ps[i].row(0);
    A.row(2 * i + 1) = pts[i].y() * Ps[i].row(2) - Ps[i].row(1);
  }
  Eigen::JacobiSVD<Eigen::MatrixXd> svd(A, Eigen::ComputeFullV);
  const Eigen::Vector4d v = svd.matrixV().col(3);
  if (std::fabs(v[3]) < 1e-12) return false;
  *X = Eigen::Vector3d(v[0], v[1], v[2]) / v[3];
  return X->allFinite();
}

inline Eigen::Matrix<double, 3, 4> poseMatrix(const Eigen::Matrix3d& R, const Eigen::Vector3d& t) {
  Eigen::Matrix<double, 3, 4> P;
  P.block<3, 3>(0, 0) = R;
  P.block<3, 1>(0, 3) = t;
  return P;
}

class UnionFind {
 public:
  explicit UnionFind(size_t n) : parent_(n) { std::iota(parent_.begin(), parent_.end(), 0); }
  size_t find(size_t x) {
    while (parent_[x] != x) {
      parent_[x] = parent_[parent_[x]];
      x = parent_[x];
    }
    return x;
  }
  void unite(size_t a, size_t b) {
    const size_t ra = find(a), rb = find(b);
    if (ra != rb) parent_[ra] = rb;
  }

 private:
  std::vector<size_t> parent_;
};

}  // namespace

// ---------------------------------------------------------------------------
namespace {

class Mapper {
 public:
  Mapper(Scene& scene, const std::vector<ImagePair>& pairs, const SfmOptions& opt,
         const ProgressFn& prog)
      : scene_(scene), pairs_(pairs), opt_(opt), prog_(prog) {
    n_ = static_cast<int>(scene_.images.size());
    registered_.assign(n_, 0);
    failed_.assign(n_, 0);
    obs_track_.resize(n_);
  }

  bool run(SfmResult* res, std::string* err);

 private:
  void buildTracks();
  bool findInitialPair(int* out_i, int* out_j, Eigen::Matrix3d* R, Eigen::Vector3d* t,
                       double* angle);
  std::vector<int> collectDirtyTracks(int img_index);
  bool triangulateTrack(int track_id, Eigen::Vector3d* X, double* max_angle);
  void createPoint(int track_id, const Eigen::Vector3d& X, double err_px);
  void filterPoints();
  bool registerNextImage(int img_index);
  void runGlobalBA(int iterations);
  void refreshPointIds();

  Scene& scene_;
  const std::vector<ImagePair>& pairs_;
  const SfmOptions& opt_;
  const ProgressFn& prog_;
  int n_ = 0;
  std::vector<char> registered_;
  std::vector<char> failed_;
  std::vector<std::vector<int>> obs_track_;   // img -> kp -> track id
  std::vector<std::vector<std::pair<int, int>>> tracks_;  // track -> (image_id, kp)
  std::vector<long long> track_point3d_;
  std::vector<std::vector<int>> pair_of_img_;
  int num_registered_ = 0;
};

// ---------------------------------------------------------------------------
void Mapper::buildTracks() {
  size_t total = 0;
  std::vector<size_t> base(n_);
  for (int i = 0; i < n_; ++i) {
    base[i] = total;
    total += scene_.images[i].points2D.size();
  }
  UnionFind uf(total);
  for (const ImagePair& p : pairs_) {
    for (const auto& m : p.matches) {
      uf.unite(base[p.i] + m.first, base[p.j] + m.second);
    }
  }
  // 根 -> track id
  std::vector<int> root_track(total, -1);
  for (int i = 0; i < n_; ++i) {
    obs_track_[i].assign(scene_.images[i].points2D.size(), -1);
  }
  tracks_.clear();
  for (int i = 0; i < n_; ++i) {
    for (size_t k = 0; k < scene_.images[i].points2D.size(); ++k) {
      const size_t r = uf.find(base[i] + k);
      int t = root_track[r];
      if (t < 0) {
        t = static_cast<int>(tracks_.size());
        root_track[r] = t;
        tracks_.emplace_back();
      }
      obs_track_[i][k] = t;
      tracks_[t].emplace_back(i + 1, static_cast<int>(k));
    }
  }
  // 只保留跨越多张图的 track(其余置空以省内存)
  for (int i = 0; i < n_; ++i) {
    for (size_t k = 0; k < obs_track_[i].size(); ++k) {
      const int t = obs_track_[i][k];
      if (t < 0) continue;
      if (tracks_[t].size() < 2) obs_track_[i][k] = -1;
    }
  }
  track_point3d_.assign(tracks_.size(), -1);

  pair_of_img_.assign(n_, {});
  for (size_t pi = 0; pi < pairs_.size(); ++pi) {
    pair_of_img_[pairs_[pi].i].push_back(static_cast<int>(pi));
    pair_of_img_[pairs_[pi].j].push_back(static_cast<int>(pi));
  }
}

// ---------------------------------------------------------------------------
bool Mapper::findInitialPair(int* out_i, int* out_j, Eigen::Matrix3d* R, Eigen::Vector3d* t,
                             double* angle) {
  std::vector<int> order(pairs_.size());
  std::iota(order.begin(), order.end(), 0);
  std::sort(order.begin(), order.end(), [&](int a, int b) {
    return pairs_[a].num_inliers > pairs_[b].num_inliers;
  });

  const cv::Mat K = cv::Mat::eye(3, 3, CV_64F);
  double best_score = -1.0;
  bool strict_ok = false;

  for (int pass = 0; pass < 2 && !strict_ok; ++pass) {
    const int limit = std::min(static_cast<int>(order.size()),
                               pass == 0 ? opt_.init_candidates
                                         : std::min(60, static_cast<int>(order.size())));
    for (int oi = 0; oi < limit; ++oi) {
      const ImagePair& p = pairs_[order[oi]];
      if (static_cast<int>(p.matches.size()) < opt_.min_init_matches) break;
      const Camera& cam = scene_.cameras[scene_.images[p.i].camera_id - 1];
      const double norm_thresh =
          std::min(5e-3, std::max(1e-4, opt_.pnp_pixel_thresh / std::max(1.0, cam.f)));

      std::vector<cv::Point2d> n1, n2;
      n1.reserve(p.matches.size());
      n2.reserve(p.matches.size());
      for (const auto& m : p.matches) {
        const Eigen::Vector2d a =
            undistortNormalized(cam, scene_.images[p.i].points2D[m.first]);
        const Eigen::Vector2d b =
            undistortNormalized(cam, scene_.images[p.j].points2D[m.second]);
        n1.emplace_back(a.x(), a.y());
        n2.emplace_back(b.x(), b.y());
      }

      cv::Mat maskE;
      cv::Mat E = cv::findEssentialMat(n1, n2, K, cv::RANSAC, 0.999, norm_thresh, maskE);
      if (E.empty()) continue;

      std::vector<cv::Point2d> i1, i2;
      std::vector<size_t> i1_src;  // i1 中第 k 项对应 p.matches 的下标
      const unsigned char* me = maskE.ptr<unsigned char>();
      for (size_t k = 0; k < n1.size(); ++k) {
        if (!me[k]) continue;
        i1.push_back(n1[k]);
        i2.push_back(n2[k]);
        i1_src.push_back(k);
      }
      if (i1.size() < static_cast<size_t>(opt_.min_init_matches)) continue;

      cv::Mat Rcv, tcv, maskP;
      const int inl = cv::recoverPose(E, i1, i2, K, Rcv, tcv, maskP);
      if (Rcv.empty() || tcv.empty()) continue;

      Eigen::Matrix3d Re;
      Eigen::Vector3d te;
      for (int r = 0; r < 3; ++r) {
        for (int c = 0; c < 3; ++c) Re(r, c) = Rcv.at<double>(r, c);
        te[r] = tcv.at<double>(r, 0);
      }
      if (!Re.allFinite() || !te.allFinite()) continue;
      if (te.norm() < 1e-12) continue;

      // ---- 三角化 + 前方交会检查 ----
      const std::vector<Eigen::Matrix<double, 3, 4>> Ps = {poseMatrix(Eigen::Matrix3d::Identity(),
                                                                      Eigen::Vector3d::Zero()),
                                                           poseMatrix(Re, te)};
      const unsigned char* mp = maskP.empty() ? nullptr : maskP.ptr<unsigned char>();
      int good = 0;
      std::vector<double> angles;
      angles.reserve(i1.size());
      for (size_t k = 0; k < i1.size(); ++k) {
        if (mp && !mp[k]) continue;
        Eigen::Vector3d X;
        if (!triangulateDLT(Ps, {Eigen::Vector2d(i1[k].x, i1[k].y), Eigen::Vector2d(i2[k].x, i2[k].y)},
                            &X))
          continue;
        const Eigen::Vector3d Xc2 = Re * X + te;
        if (X.z() <= 0 || Xc2.z() <= 0) continue;
        Eigen::Vector2d uv1, uv2;
        if (!projectPoint(cam, Eigen::Matrix3d::Identity(), Eigen::Vector3d::Zero(), X, &uv1))
          continue;
        if (!projectPoint(cam, Re, te, X, &uv2)) continue;
        const size_t src = i1_src[k];
        const double e1 = (uv1 - scene_.images[p.i].points2D[p.matches[src].first]).norm();
        const double e2 = (uv2 - scene_.images[p.j].points2D[p.matches[src].second]).norm();
        if (e1 > 2.0 * opt_.max_reproj_error_px || e2 > 2.0 * opt_.max_reproj_error_px) continue;
        // 视差角
        const Eigen::Vector3d r1 = X.normalized();
        const Eigen::Vector3d r2 = (X - (-Re.transpose() * te)).normalized();
        const double a = std::acos(std::min(1.0, std::max(-1.0, r1.dot(r2)))) * 180.0 / M_PI;
        angles.push_back(a);
        ++good;
      }
      if (good < 40 || angles.empty()) continue;
      std::sort(angles.begin(), angles.end());
      const double med_angle = angles[angles.size() / 2];
      if (opt_.verbose)
        std::fprintf(stderr, "[init] pair (%d,%d) inliers %d good %d med_angle %.2f\n", p.i, p.j,
                     static_cast<int>(p.matches.size()), good, med_angle);
      if (pass == 0 && med_angle < opt_.init_min_tri_angle) continue;

      const double score = static_cast<double>(good) * std::min(med_angle, 30.0);
      if (score > best_score) {
        best_score = score;
        *out_i = p.i;
        *out_j = p.j;
        *R = Re;
        *t = te;
        *angle = med_angle;
        if (pass == 0 && med_angle >= opt_.init_min_tri_angle) strict_ok = true;
      }
      if (strict_ok) break;
    }
  }
  return best_score > 0;
}

// ---------------------------------------------------------------------------
std::vector<int> Mapper::collectDirtyTracks(int img_index) {
  std::vector<int> out;
  for (int t : obs_track_[img_index]) {
    if (t < 0) continue;
    if (track_point3d_[t] >= 0) continue;
    out.push_back(t);
  }
  std::sort(out.begin(), out.end());
  out.erase(std::unique(out.begin(), out.end()), out.end());
  return out;
}

// ---------------------------------------------------------------------------
bool Mapper::triangulateTrack(int track_id, Eigen::Vector3d* X, double* max_angle) {
  const auto& tk = tracks_[track_id];
  std::vector<int> views;  // 0-based 图像下标
  std::vector<int> kps;    // 对应的关键点下标
  std::vector<Eigen::Vector2d> pts;
  for (const auto& e : tk) {
    const int idx = e.first - 1;
    if (!registered_[idx]) continue;
    views.push_back(idx);
    kps.push_back(e.second);
    const Camera& cam = scene_.cameras[scene_.images[idx].camera_id - 1];
    pts.push_back(undistortNormalized(cam, scene_.images[idx].points2D[e.second]));
  }
  if (views.size() < 2) return false;
  if (static_cast<int>(views.size()) > opt_.max_track_views) {
    views.resize(opt_.max_track_views);
    kps.resize(opt_.max_track_views);
    pts.resize(opt_.max_track_views);
  }

  std::vector<Eigen::Matrix<double, 3, 4>> Ps;
  Ps.reserve(views.size());
  for (int v : views) Ps.push_back(poseMatrix(scene_.images[v].R, scene_.images[v].t));

  if (!triangulateDLT(Ps, pts, X)) return false;
  if (!X->allFinite()) return false;

  // 前方交会 + 重投影检查(所有参与三角化的视角)
  double err_sum = 0.0;
  int err_cnt = 0;
  std::vector<Eigen::Vector3d> centers;
  centers.reserve(views.size());
  for (int v : views) centers.push_back(cameraCenter(scene_.images[v].R, scene_.images[v].t));
  for (size_t a = 0; a < views.size(); ++a) {
    const Image& im = scene_.images[views[a]];
    const Camera& cam = scene_.cameras[im.camera_id - 1];
    const Eigen::Vector3d Xc = im.R * (*X) + im.t;
    if (Xc.z() <= 1e-6) return false;
    Eigen::Vector2d uv;
    if (!projectPoint(cam, im.R, im.t, *X, &uv)) return false;
    const double e = (uv - im.points2D[kps[a]]).norm();
    err_sum += e;
    ++err_cnt;
    if (e > 2.0 * opt_.max_reproj_error_px) return false;
  }
  // 视差角: 取最大基线视角对
  double best = 0.0;
  for (size_t a = 0; a < views.size(); ++a) {
    for (size_t b = a + 1; b < views.size(); ++b) {
      const Eigen::Vector3d ra = (*X - centers[a]).normalized();
      const Eigen::Vector3d rb = (*X - centers[b]).normalized();
      const double ang = std::acos(std::min(1.0, std::max(-1.0, ra.dot(rb)))) * 180.0 / M_PI;
      if (ang > best) best = ang;
    }
  }
  if (best < opt_.min_tri_angle_deg) return false;
  if (max_angle) *max_angle = best;
  (void)err_sum;
  (void)err_cnt;
  return true;
}

// ---------------------------------------------------------------------------
void Mapper::createPoint(int track_id, const Eigen::Vector3d& X, double err_px) {
  Point3D p;
  p.point3D_id = scene_.next_point3D_id++;
  p.xyz = X;
  p.error = err_px;
  p.track = tracks_[track_id];
  scene_.points3D.push_back(p);
  track_point3d_[track_id] = p.point3D_id;
  for (const auto& e : p.track) {
    Image& im = scene_.image(e.first);
    if (e.second < static_cast<int>(im.point3D_ids.size())) im.point3D_ids[e.second] = p.point3D_id;
  }
}

// ---------------------------------------------------------------------------
void Mapper::refreshPointIds() {
  for (int i = 0; i < n_; ++i) {
    std::fill(scene_.images[i].point3D_ids.begin(), scene_.images[i].point3D_ids.end(), -1LL);
  }
  for (const Point3D& p : scene_.points3D) {
    for (const auto& e : p.track) {
      Image& im = scene_.image(e.first);
      if (e.second < static_cast<int>(im.point3D_ids.size())) im.point3D_ids[e.second] = p.point3D_id;
    }
  }
}

void Mapper::filterPoints() {
  const int before = static_cast<int>(scene_.points3D.size());
  std::vector<Point3D> kept;
  kept.reserve(scene_.points3D.size());
  for (Point3D& p : scene_.points3D) {
    // 只保留被 >=2 张已注册图像观测、且重投影误差可接受的点
    int visible = 0;
    double err_sum = 0.0;
    int err_cnt = 0;
    bool ok = true;
    for (const auto& e : p.track) {
      const int idx = e.first - 1;
      if (!registered_[idx]) continue;
      const Image& im = scene_.images[idx];
      const Camera& cam = scene_.cameras[im.camera_id - 1];
      const Eigen::Vector3d Xc = im.R * p.xyz + im.t;
      if (Xc.z() <= 1e-6) {
        ok = false;
        break;
      }
      Eigen::Vector2d uv;
      if (!projectPoint(cam, im.R, im.t, p.xyz, &uv)) {
        ok = false;
        break;
      }
      const double rerr = (uv - im.points2D[e.second]).norm();
      err_sum += rerr;
      ++err_cnt;
      if (rerr > 3.0 * opt_.max_reproj_error_px) {
        ok = false;
        break;
      }
      ++visible;
    }
    if (!ok || visible < 2) continue;
    p.error = err_cnt > 0 ? err_sum / err_cnt : 0.0;
    kept.push_back(std::move(p));
  }
  scene_.points3D.swap(kept);

  // 释放被删除点占用的 track
  std::vector<long long> kept_ids;
  kept_ids.reserve(scene_.points3D.size());
  for (const Point3D& p : scene_.points3D) kept_ids.push_back(p.point3D_id);
  std::sort(kept_ids.begin(), kept_ids.end());
  for (size_t t = 0; t < track_point3d_.size(); ++t) {
    if (track_point3d_[t] < 0) continue;
    if (!std::binary_search(kept_ids.begin(), kept_ids.end(), track_point3d_[t]))
      track_point3d_[t] = -1;
  }
  refreshPointIds();
  if (opt_.verbose)
    std::fprintf(stderr, "[filter] %d -> %d points\n", before,
                 static_cast<int>(scene_.points3D.size()));
}

// ---------------------------------------------------------------------------
void Mapper::runGlobalBA(int iterations) {
  BAOptions bo;
  bo.max_iterations = iterations;
  bo.huber_delta_px = opt_.ba_huber_px;
  bo.refine_intrinsics = opt_.refine_intrinsics;
  bo.verbose = opt_.verbose;
  bundleAdjust(scene_, bo);
}

// ---------------------------------------------------------------------------
bool Mapper::registerNextImage(int img_index) {
  Image& img = scene_.images[img_index];
  const Camera& cam = scene_.cameras[img.camera_id - 1];

  // ---- 收集 2D-3D 对应 ----
  std::vector<std::pair<Eigen::Vector3d, Eigen::Vector2d>> corrs;
  std::vector<int> corr_tracks;
  for (int pi : pair_of_img_[img_index]) {
    const ImagePair& p = pairs_[pi];
    const bool forward = (p.i == img_index);
    const int other = forward ? p.j : p.i;
    if (!registered_[other]) continue;
    for (const auto& m : p.matches) {
      const int kp_self = forward ? m.first : m.second;
      const int kp_other = forward ? m.second : m.first;
      if (kp_self >= static_cast<int>(obs_track_[img_index].size())) continue;
      const int t = obs_track_[img_index][kp_self];
      if (t < 0 || track_point3d_[t] < 0) continue;
      const Point3D* pt = findPoint(scene_, track_point3d_[t]);
      if (!pt) continue;
      (void)kp_other;
      corrs.emplace_back(pt->xyz, undistortNormalized(cam, img.points2D[kp_self]));
      corr_tracks.push_back(t);
    }
  }
  if (static_cast<int>(corrs.size()) < opt_.min_pnp_corrs) return false;

  cv::Mat obj(static_cast<int>(corrs.size()), 3, CV_64F);
  cv::Mat img_pts(static_cast<int>(corrs.size()), 2, CV_64F);
  for (size_t k = 0; k < corrs.size(); ++k) {
    obj.at<double>(static_cast<int>(k), 0) = corrs[k].first.x();
    obj.at<double>(static_cast<int>(k), 1) = corrs[k].first.y();
    obj.at<double>(static_cast<int>(k), 2) = corrs[k].first.z();
    img_pts.at<double>(static_cast<int>(k), 0) = corrs[k].second.x();
    img_pts.at<double>(static_cast<int>(k), 1) = corrs[k].second.y();
  }

  const cv::Mat K = cv::Mat::eye(3, 3, CV_64F);
  const double norm_thresh = opt_.pnp_pixel_thresh / std::max(1.0, cam.f);
  cv::Mat rvec, tvec, inliers;
  bool ok = cv::solvePnPRansac(obj, img_pts, K, cv::noArray(), rvec, tvec, false, 200,
                               norm_thresh, 0.999, inliers, cv::SOLVEPNP_EPNP);
  if (!ok && inliers.empty()) return false;
  int num_inl = inliers.empty() ? 0 : inliers.rows;
  if (num_inl < opt_.min_pnp_inliers) return false;

  // 用内点做一次迭代式精化
  cv::Mat obj_inl, pts_inl;
  if (num_inl > 0 && num_inl < static_cast<int>(corrs.size())) {
    obj_inl.create(num_inl, 3, CV_64F);
    pts_inl.create(num_inl, 2, CV_64F);
    for (int k = 0; k < num_inl; ++k) {
      const int r = inliers.at<int>(k, 0);
      for (int c = 0; c < 3; ++c) obj_inl.at<double>(k, c) = obj.at<double>(r, c);
      for (int c = 0; c < 2; ++c) pts_inl.at<double>(k, c) = img_pts.at<double>(r, c);
    }
    cv::solvePnP(obj_inl, pts_inl, K, cv::noArray(), rvec, tvec, true, cv::SOLVEPNP_ITERATIVE);
  }

  Eigen::Vector3d rv(rvec.at<double>(0), rvec.at<double>(1), rvec.at<double>(2));
  Eigen::Matrix3d R = expMapRot(rv);
  Eigen::Vector3d t(tvec.at<double>(0), tvec.at<double>(1), tvec.at<double>(2));
  if (!R.allFinite() || !t.allFinite()) return false;

  // 内点比例检查
  const double ratio = static_cast<double>(num_inl) / static_cast<double>(corrs.size());
  if (ratio < 0.15) return false;

  // 位姿细化(固定点与内参)
  img.registered = true;
  img.R = R;
  img.t = t;
  {
    BAOptions bo;
    bo.huber_delta_px = opt_.ba_huber_px;
    refineSinglePose(scene_, img_index + 1, bo, &img.R, &img.t);
  }
  return true;
}

// ---------------------------------------------------------------------------
bool Mapper::run(SfmResult* res, std::string* err) {
  buildTracks();
  if (opt_.verbose)
    std::fprintf(stderr, "[sfm] tracks %zu\n", tracks_.size());

  int i0 = -1, j0 = -1;
  Eigen::Matrix3d R0 = Eigen::Matrix3d::Identity();
  Eigen::Vector3d t0 = Eigen::Vector3d::Zero();
  double angle0 = 0.0;
  if (!findInitialPair(&i0, &j0, &R0, &t0, &angle0)) {
    if (err) *err = "no valid initial image pair (need more parallax / more frames)";
    return false;
  }
  scene_.images[i0].registered = true;
  scene_.images[i0].R = Eigen::Matrix3d::Identity();
  scene_.images[i0].t = Eigen::Vector3d::Zero();
  scene_.images[j0].registered = true;
  scene_.images[j0].R = R0;
  scene_.images[j0].t = t0;
  registered_[i0] = 1;
  registered_[j0] = 1;
  num_registered_ = 2;
  res->init_pair[0] = i0;
  res->init_pair[1] = j0;
  res->init_tri_angle_deg = angle0;

  // ---- 初始三角化 ----
  std::vector<int> dirty = collectDirtyTracks(i0);
  {
    auto d2 = collectDirtyTracks(j0);
    dirty.insert(dirty.end(), d2.begin(), d2.end());
    std::sort(dirty.begin(), dirty.end());
    dirty.erase(std::unique(dirty.begin(), dirty.end()), dirty.end());
  }
  for (int t : dirty) {
    Eigen::Vector3d X;
    double ang = 0.0;
    if (triangulateTrack(t, &X, &ang)) createPoint(t, X, 0.0);
  }
  if (opt_.verbose)
    std::fprintf(stderr, "[sfm] init pair (%d,%d) angle %.2f deg, points %zu\n", i0, j0, angle0,
                 scene_.points3D.size());
  runGlobalBA(opt_.ba_final_iterations);
  filterPoints();

  if (prog_) prog_("mapping", num_registered_, n_, num_registered_,
                   static_cast<int>(scene_.points3D.size()));

  // ---- 增量注册 ----
  const int ba_every = std::max(1, opt_.ba_every);
  int since_ba = 0;
  while (true) {
    // 选下一个待注册图像: 2D-3D 对应最多者
    int best_img = -1;
    int best_score = 0;
    for (int c = 0; c < n_; ++c) {
      if (registered_[c] || failed_[c]) continue;
      int score = 0;
      for (int pi : pair_of_img_[c]) {
        const ImagePair& p = pairs_[pi];
        const bool forward = (p.i == c);
        const int other = forward ? p.j : p.i;
        if (!registered_[other]) continue;
        for (const auto& m : p.matches) {
          const int kp_self = forward ? m.first : m.second;
          if (kp_self >= static_cast<int>(obs_track_[c].size())) continue;
          const int t = obs_track_[c][kp_self];
          if (t < 0 || track_point3d_[t] < 0) continue;
          ++score;
        }
      }
      if (score > best_score) {
        best_score = score;
        best_img = c;
      }
    }
    if (best_img < 0 || best_score < opt_.min_pnp_corrs) break;

    if (!registerNextImage(best_img)) {
      failed_[best_img] = 1;
      continue;
    }
    registered_[best_img] = 1;
    ++num_registered_;
    if (opt_.verbose)
      std::fprintf(stderr, "[sfm] registered image %d (%d/%d), points %zu\n", best_img,
                   num_registered_, n_, scene_.points3D.size());

    // ---- 三角化新 track ----
    std::vector<int> to_tri = collectDirtyTracks(best_img);
    int added = 0;
    for (int t : to_tri) {
      if (track_point3d_[t] >= 0) continue;
      Eigen::Vector3d X;
      double ang = 0.0;
      if (triangulateTrack(t, &X, &ang)) {
        createPoint(t, X, 0.0);
        ++added;
      }
    }
    if (opt_.verbose && added > 0)
      std::fprintf(stderr, "[sfm]   +%d new points\n", added);

    ++since_ba;
    if (since_ba >= ba_every || num_registered_ == n_) {
      runGlobalBA(ba_every > 1 ? opt_.ba_max_iterations : opt_.ba_max_iterations);
      filterPoints();
      since_ba = 0;
    }
    if (prog_)
      prog_("mapping", num_registered_, n_, num_registered_,
            static_cast<int>(scene_.points3D.size()));
  }

  // ---- 最终全局 BA + 过滤 ----
  runGlobalBA(opt_.ba_final_iterations);
  filterPoints();

  res->num_registered = num_registered_;
  res->num_points3D = static_cast<int>(scene_.points3D.size());
  res->num_observations = 0;
  for (const Point3D& p : scene_.points3D) res->num_observations += static_cast<int>(p.track.size());
  res->mean_reproj_error_px = meanReprojectionError(scene_);
  return num_registered_ >= 2;
}

}  // namespace

// ---------------------------------------------------------------------------
bool runIncrementalSfM(Scene& scene, const std::vector<ImagePair>& pairs, const SfmOptions& opt,
                       const ProgressFn& progress, SfmResult* result, std::string* err) {
  Mapper m(scene, pairs, opt, progress);
  return m.run(result, err);
}

}  // namespace iii_sfm
