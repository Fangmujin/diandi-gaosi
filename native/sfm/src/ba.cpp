// 光束法平差 (Bundle Adjustment)
//
// 参数化与雅可比:
//   相机外参使用「左乘扰动」: R <- Exp(dR) * R, t <- t + dt
//   于是 Xc = R*X + t 有
//       d Xc / d dR = -[Xc]x        (3x3)
//       d Xc / d dt =  I            (3x3)
//   投影链 (SIMPLE_RADIAL):
//       x = Xc.x/z, y = Xc.y/z, r2 = x^2+y^2, d = 1 + k1*r2
//       u = f*d*x + cx,  v = f*d*y + cy
//       d(u,v)/d(x,y) = [[ f*(d + 2*k1*x^2), f*(2*k1*x*y) ],
//                        [ f*(2*k1*x*y),     f*(d + 2*k1*y^2) ]]
//       d(x,y)/dXc     = [[ 1/z, 0, -x/z ], [ 0, 1/z, -y/z ]]
//       d(u,v)/dX      = d(u,v)/d(x,y) * d(x,y)/dXc * R
//       d(u,v)/d(f,cx,cy,k1) = [[ d*x, 1, 0, f*r2*x ],
//                                [ d*y, 0, 1, f*r2*y ]]
//
// 求解: 点参数通过 Schur 消元 (3x3 块对角) 消去,
//       相机侧得到 (6N + 4C) 维稠密系统 (80 张图 -> 484x484), Cholesky 直接分解。
#include "ba.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <tuple>
#include <utility>
#include <vector>

#include <Eigen/Cholesky>
#include <Eigen/Dense>

namespace iii_sfm {
namespace {

inline Eigen::Matrix3d skew(const Eigen::Vector3d& v) {
  Eigen::Matrix3d m;
  m << 0.0, -v.z(), v.y(), v.z(), 0.0, -v.x(), -v.y(), v.x(), 0.0;
  return m;
}

struct ObsEntry {
  int img_index;  // scene.images 下标
  int img_rank;   // 已注册图像序号(参数块偏移)
  int cam_slot;   // scene.cameras 下标
  Eigen::Vector2d uv;
};

struct PointBlock {
  int pt_index;  // scene.points3D 下标
  std::vector<ObsEntry> obs;
};

// 收集所有可平差观测(至少被两张已注册图像看到)
void collectObservations(const Scene& scene, const std::vector<int>& rank_of,
                         std::vector<PointBlock>* out) {
  out->clear();
  for (size_t pi = 0; pi < scene.points3D.size(); ++pi) {
    const Point3D& p = scene.points3D[pi];
    std::vector<ObsEntry> obs;
    for (const auto& e : p.track) {
      const int img_id = e.first;
      if (img_id < 1 || img_id > static_cast<int>(scene.images.size())) continue;
      const int idx = e.second;
      const Image& im = scene.images[img_id - 1];
      if (!im.registered) continue;
      if (idx < 0 || idx >= static_cast<int>(im.points2D.size())) continue;
      const int rank = rank_of[img_id - 1];
      if (rank < 0) continue;
      obs.push_back(ObsEntry{img_id - 1, rank, im.camera_id - 1, im.points2D[idx]});
    }
    if (obs.size() >= 2) out->push_back(PointBlock{static_cast<int>(pi), std::move(obs)});
  }
}

// 计算单个观测的残差与雅可比; 返回 false 表示点在该相机后方。
bool residualAndJacobian(const Camera& cam, const Eigen::Matrix3d& R, const Eigen::Vector3d& t,
                         const Eigen::Vector3d& X, const Eigen::Vector2d& uv,
                         Eigen::Vector2d* res, Eigen::Matrix<double, 2, 3>* dPose,
                         Eigen::Matrix<double, 2, 3>* dTrans, Eigen::Matrix<double, 2, 3>* dPoint,
                         Eigen::Matrix<double, 2, 4>* dIntr) {
  const Eigen::Vector3d Xc = R * X + t;
  if (Xc.z() <= 1e-9) return false;
  const double z = Xc.z();
  const double invz = 1.0 / z;
  const double x = Xc.x() * invz;
  const double y = Xc.y() * invz;
  const double r2 = x * x + y * y;
  const double d = 1.0 + cam.k1 * r2;

  (*res)[0] = cam.f * d * x + cam.cx - uv.x();
  (*res)[1] = cam.f * d * y + cam.cy - uv.y();

  Eigen::Matrix<double, 2, 2> Dxy;
  Dxy(0, 0) = cam.f * (d + 2.0 * cam.k1 * x * x);
  Dxy(0, 1) = cam.f * (2.0 * cam.k1 * x * y);
  Dxy(1, 0) = Dxy(0, 1);
  Dxy(1, 1) = cam.f * (d + 2.0 * cam.k1 * y * y);

  Eigen::Matrix<double, 2, 3> Pxy;
  Pxy << invz, 0.0, -x * invz, 0.0, invz, -y * invz;

  const Eigen::Matrix<double, 2, 3> dXc = Dxy * Pxy;  // d(u,v)/dXc
  if (dPose) *dPose = -dXc * skew(Xc);                // d(u,v)/d dR
  if (dTrans) *dTrans = dXc;                          // d(u,v)/d dt
  if (dPoint) *dPoint = dXc * R;                      // d(u,v)/dX
  if (dIntr) {
    (*dIntr)(0, 0) = d * x;
    (*dIntr)(0, 1) = 1.0;
    (*dIntr)(0, 2) = 0.0;
    (*dIntr)(0, 3) = cam.f * r2 * x;
    (*dIntr)(1, 0) = d * y;
    (*dIntr)(1, 1) = 0.0;
    (*dIntr)(1, 2) = 1.0;
    (*dIntr)(1, 3) = cam.f * r2 * y;
  }
  return true;
}

inline double huberCost(double rnorm, double delta) {
  if (rnorm <= delta) return 0.5 * rnorm * rnorm;
  return delta * (rnorm - 0.5 * delta);
}

double totalCost(const Scene& scene, const std::vector<PointBlock>& blocks, double delta) {
  double cost = 0.0;
  for (const PointBlock& pb : blocks) {
    const Eigen::Vector3d& X = scene.points3D[pb.pt_index].xyz;
    for (const ObsEntry& o : pb.obs) {
      const Image& im = scene.images[o.img_index];
      const Camera& cam = scene.cameras[o.cam_slot];
      Eigen::Vector2d res;
      if (!residualAndJacobian(cam, im.R, im.t, X, o.uv, &res, nullptr, nullptr, nullptr, nullptr))
        continue;
      cost += huberCost(res.norm(), delta);
    }
  }
  return cost;
}

}  // namespace

// ---------------------------------------------------------------------------
BAResult bundleAdjust(Scene& scene, const BAOptions& opt) {
  BAResult out;

  // ---- 建立索引 ---------------------------------------------------------
  std::vector<int> rank_of(scene.images.size(), -1);
  std::vector<int> reg_list;  // scene.images 下标
  for (size_t i = 0; i < scene.images.size(); ++i) {
    if (scene.images[i].registered) {
      rank_of[i] = static_cast<int>(reg_list.size());
      reg_list.push_back(static_cast<int>(i));
    }
  }
  const int N = static_cast<int>(reg_list.size());
  const int C = static_cast<int>(scene.cameras.size());
  if (N < 2) return out;
  const int C0 = 6 * N;
  const int P = 6 * N + 4 * C;
  if (P <= 0) return out;

  std::vector<PointBlock> blocks;
  collectObservations(scene, rank_of, &blocks);
  if (blocks.empty()) return out;
  for (const PointBlock& b : blocks) out.num_residuals += static_cast<int>(b.obs.size()) * 2;

  struct PointCache {
    Eigen::Matrix3d Vinv = Eigen::Matrix3d::Zero();
    Eigen::Vector3d gx = Eigen::Vector3d::Zero();
    std::vector<std::pair<int, Eigen::Matrix<double, 6, 3>>> wPose;  // (img_rank, 6x3)
    std::vector<std::pair<int, Eigen::Matrix<double, 4, 3>>> wIntr;  // (cam_slot, 4x3)
  };
  std::vector<PointCache> cache(blocks.size());

  Eigen::MatrixXd U = Eigen::MatrixXd::Zero(P, P);
  Eigen::VectorXd gc = Eigen::VectorXd::Zero(P);

  struct Snapshot {
    std::vector<Eigen::Matrix3d> Rs;
    std::vector<Eigen::Vector3d> ts;
    std::vector<Camera> cams;
    std::vector<Eigen::Vector3d> Xs;
  };
  auto snapshot = [&]() {
    Snapshot s;
    s.Rs.resize(N);
    s.ts.resize(N);
    for (int i = 0; i < N; ++i) {
      s.Rs[i] = scene.images[reg_list[i]].R;
      s.ts[i] = scene.images[reg_list[i]].t;
    }
    s.cams = scene.cameras;
    s.Xs.resize(blocks.size());
    for (size_t i = 0; i < blocks.size(); ++i) s.Xs[i] = scene.points3D[blocks[i].pt_index].xyz;
    return s;
  };
  auto rollback = [&](const Snapshot& s) {
    for (int i = 0; i < N; ++i) {
      scene.images[reg_list[i]].R = s.Rs[i];
      scene.images[reg_list[i]].t = s.ts[i];
    }
    scene.cameras = s.cams;
    for (size_t i = 0; i < blocks.size(); ++i) scene.points3D[blocks[i].pt_index].xyz = s.Xs[i];
  };

  double cost = totalCost(scene, blocks, opt.huber_delta_px);
  out.cost_before = cost;
  if (opt.verbose) std::fprintf(stderr, "[ba] init cost %.6f\n", cost);

  double lambda = opt.lambda_init;
  for (int iter = 0; iter < opt.max_iterations; ++iter) {
    U.setZero();
    gc.setZero();

    // ---- 组装法方程 -----------------------------------------------------
    for (size_t bi = 0; bi < blocks.size(); ++bi) {
      const PointBlock& pb = blocks[bi];
      const Eigen::Vector3d& X = scene.points3D[pb.pt_index].xyz;
      Eigen::Matrix3d V = Eigen::Matrix3d::Zero();
      Eigen::Vector3d gx = Eigen::Vector3d::Zero();
      PointCache& pc = cache[bi];
      pc.wPose.clear();
      pc.wIntr.clear();

      std::vector<int> poseSlots, intrSlots;
      for (const ObsEntry& o : pb.obs) {
        const Image& im = scene.images[o.img_index];
        const Camera& cam = scene.cameras[o.cam_slot];
        Eigen::Vector2d res;
        Eigen::Matrix<double, 2, 3> dPose, dTrans, dPoint;
        Eigen::Matrix<double, 2, 4> dIntr;
        if (!residualAndJacobian(cam, im.R, im.t, X, o.uv, &res, &dPose, &dTrans, &dPoint, &dIntr))
          continue;

        const double rn = res.norm();
        const double w = rn <= opt.huber_delta_px ? 1.0 : opt.huber_delta_px / rn;
        const int po = 6 * o.img_rank;
        const int co = C0 + 4 * o.cam_slot;

        // --- 相机-相机块 (外参) ---
        Eigen::Matrix<double, 6, 6> bb = Eigen::Matrix<double, 6, 6>::Zero();
        bb.block<3, 3>(0, 0) = dPose.transpose() * dPose;
        bb.block<3, 3>(0, 3) = dPose.transpose() * dTrans;
        bb.block<3, 3>(3, 0) = bb.block<3, 3>(0, 3).transpose();
        bb.block<3, 3>(3, 3) = dTrans.transpose() * dTrans;
        U.block<6, 6>(po, po) += w * bb;
        gc.block<3, 1>(po, 0) += w * (dPose.transpose() * res);
        gc.block<3, 1>(po + 3, 0) += w * (dTrans.transpose() * res);

        if (opt.refine_intrinsics) {
          U.block<4, 4>(co, co) += w * (dIntr.transpose() * dIntr);
          gc.block<4, 1>(co, 0) += w * (dIntr.transpose() * res);
          U.block<3, 4>(po, co) += w * (dPose.transpose() * dIntr);
          U.block<3, 4>(po + 3, co) += w * (dTrans.transpose() * dIntr);
          U.block<4, 3>(co, po) += w * (dIntr.transpose() * dPose);
          U.block<4, 3>(co, po + 3) += w * (dIntr.transpose() * dTrans);
        }

        // --- 点块 ---
        V += w * (dPoint.transpose() * dPoint);
        gx += w * (dPoint.transpose() * res);

        // --- 相机 x 点 ---
        {
          Eigen::Matrix<double, 6, 3> wp = Eigen::Matrix<double, 6, 3>::Zero();
          wp.block<3, 3>(0, 0) = dPose.transpose() * dPoint;
          wp.block<3, 3>(3, 0) = dTrans.transpose() * dPoint;
          auto it = std::find(poseSlots.begin(), poseSlots.end(), o.img_rank);
          if (it == poseSlots.end()) {
            poseSlots.push_back(o.img_rank);
            pc.wPose.emplace_back(o.img_rank, w * wp);
          } else {
            pc.wPose[std::distance(poseSlots.begin(), it)].second += w * wp;
          }
        }
        if (opt.refine_intrinsics) {
          Eigen::Matrix<double, 4, 3> wi = dIntr.transpose() * dPoint;
          auto it = std::find(intrSlots.begin(), intrSlots.end(), o.cam_slot);
          if (it == intrSlots.end()) {
            intrSlots.push_back(o.cam_slot);
            pc.wIntr.emplace_back(o.cam_slot, w * wi);
          } else {
            pc.wIntr[std::distance(intrSlots.begin(), it)].second += w * wi;
          }
        }
      }

      const Eigen::Matrix3d Vd = V + (lambda * 1e-6 + 1e-8) * Eigen::Matrix3d::Identity();
      if (std::fabs(Vd.determinant()) < 1e-18) {
        pc.Vinv = Eigen::Matrix3d::Zero();
      } else {
        pc.Vinv = Vd.inverse();
      }
      pc.gx = gx;
    }

    // ---- Schur 消元: Ured = U - W V^{-1} W^T ----
    Eigen::MatrixXd Ured = U;
    Eigen::VectorXd gred = gc;
    for (size_t bi = 0; bi < blocks.size(); ++bi) {
      const PointCache& pc = cache[bi];
      const Eigen::Matrix3d& Vi = pc.Vinv;
      for (size_t a = 0; a < pc.wPose.size(); ++a) {
        const int ra = pc.wPose[a].first;
        const int oa = 6 * ra;
        gred.block<6, 1>(oa, 0) -= pc.wPose[a].second * Vi * pc.gx;
        for (size_t b = 0; b < pc.wPose.size(); ++b) {
          const int rb = pc.wPose[b].first;
          const int ob = 6 * rb;
          if (rb < ra) continue;
          const Eigen::Matrix<double, 6, 6> blk = pc.wPose[a].second * Vi *
                                                  pc.wPose[b].second.transpose();
          if (rb == ra) {
            Ured.block<6, 6>(oa, oa) -= blk;
          } else {
            Ured.block<6, 6>(oa, ob) -= blk;
            Ured.block<6, 6>(ob, oa) -= blk.transpose();
          }
        }
      }
      for (size_t a = 0; a < pc.wIntr.size(); ++a) {
        const int ca = pc.wIntr[a].first;
        const int oa = C0 + 4 * ca;
        gred.block<4, 1>(oa, 0) -= pc.wIntr[a].second * Vi * pc.gx;
        for (size_t b = 0; b < pc.wIntr.size(); ++b) {
          const int cb = pc.wIntr[b].first;
          if (cb < ca) continue;
          const int ob = C0 + 4 * cb;
          const Eigen::Matrix<double, 4, 4> blk = pc.wIntr[a].second * Vi *
                                                  pc.wIntr[b].second.transpose();
          if (cb == ca) {
            Ured.block<4, 4>(oa, oa) -= blk;
          } else {
            Ured.block<4, 4>(oa, ob) -= blk;
            Ured.block<4, 4>(ob, oa) -= blk.transpose();
          }
        }
        for (const auto& wp : pc.wPose) {
          const int op = 6 * wp.first;
          const Eigen::Matrix<double, 6, 4> blk = wp.second * Vi * pc.wIntr[a].second.transpose();
          Ured.block<6, 4>(op, oa) -= blk;
          Ured.block<4, 6>(oa, op) -= blk.transpose();
        }
      }
    }

    // ---- 规范自由度固定 + LM 阻尼 ------------------------------------------
    if (opt.fix_first_camera) {
      for (int k = 0; k < 6; ++k) {
        Ured.row(k).setZero();
        Ured.col(k).setZero();
        Ured(k, k) = 1.0;
        gred(k) = 0.0;
      }
    }
    double max_diag = Ured.diagonal().maxCoeff();
    if (!(max_diag > 0.0)) max_diag = 1.0;
    Ured += lambda * max_diag * Eigen::MatrixXd::Identity(P, P);

    Eigen::VectorXd delta = Eigen::VectorXd::Zero(P);
    {
      Eigen::LLT<Eigen::MatrixXd> llt(Ured);
      if (llt.info() == Eigen::Success) {
        delta = llt.solve(-gred);
      } else {
        Eigen::LDLT<Eigen::MatrixXd> ldlt(Ured);
        if (ldlt.info() != Eigen::Success) {
          lambda *= 10.0;
          continue;
        }
        delta = ldlt.solve(-gred);
      }
    }
    if (!delta.allFinite()) {
      lambda *= 10.0;
      continue;
    }

    // ---- 回代求点更新 -----------------------------------------------------
    std::vector<Eigen::Vector3d> dx(blocks.size(), Eigen::Vector3d::Zero());
    for (size_t bi = 0; bi < blocks.size(); ++bi) {
      const PointCache& pc = cache[bi];
      Eigen::Vector3d rhs = -pc.gx;
      for (const auto& wp : pc.wPose)
        rhs -= wp.second.transpose() * delta.block<6, 1>(6 * wp.first, 0);
      for (const auto& wi : pc.wIntr)
        rhs -= wi.second.transpose() * delta.block<4, 1>(C0 + 4 * wi.first, 0);
      dx[bi] = pc.Vinv * rhs;
    }

    // ---- 试探更新 ---------------------------------------------------------
    const Snapshot snap = snapshot();
    for (int i = 0; i < N; ++i) {
      if (opt.fix_first_camera && i == 0) continue;
      Image& im = scene.images[reg_list[i]];
      im.R = expMapRot(Eigen::Vector3d(delta[6 * i + 0], delta[6 * i + 1], delta[6 * i + 2])) * im.R;
      im.t += Eigen::Vector3d(delta[6 * i + 3], delta[6 * i + 4], delta[6 * i + 5]);
    }
    if (opt.refine_intrinsics) {
      for (int c = 0; c < C; ++c) {
        Camera& cam = scene.cameras[c];
        cam.f = std::max(1.0, cam.f + delta[C0 + 4 * c + 0]);
        cam.cx += delta[C0 + 4 * c + 1];
        cam.cy += delta[C0 + 4 * c + 2];
        cam.k1 = std::min(0.5, std::max(-0.5, cam.k1 + delta[C0 + 4 * c + 3]));
      }
    }
    for (size_t bi = 0; bi < blocks.size(); ++bi)
      scene.points3D[blocks[bi].pt_index].xyz += dx[bi];

    const double new_cost = totalCost(scene, blocks, opt.huber_delta_px);
    out.iterations = iter + 1;
    if (new_cost < cost) {
      const double rel = (cost - new_cost) / std::max(1e-12, cost);
      cost = new_cost;
      lambda = std::max(lambda * 0.3, 1e-9);
      if (opt.verbose)
        std::fprintf(stderr, "[ba] iter %2d cost %.6f lambda %.1e rel %.2e\n", iter, cost, lambda,
                     rel);
      if (rel < 1e-5) break;
    } else {
      rollback(snap);
      lambda *= 8.0;
      if (opt.verbose)
        std::fprintf(stderr, "[ba] iter %2d reject %.6f -> %.6f lambda %.1e\n", iter, cost,
                     new_cost, lambda);
      if (lambda > 1e10) break;
    }
  }

  out.cost_after = cost;
  double e = 0.0;
  int cnt = 0;
  for (const PointBlock& pb : blocks) {
    const Eigen::Vector3d& X = scene.points3D[pb.pt_index].xyz;
    for (const ObsEntry& o : pb.obs) {
      const Image& im = scene.images[o.img_index];
      const Camera& cam = scene.cameras[o.cam_slot];
      Eigen::Vector2d res;
      if (!residualAndJacobian(cam, im.R, im.t, X, o.uv, &res, nullptr, nullptr, nullptr, nullptr))
        continue;
      e += res.norm();
      ++cnt;
    }
  }
  out.mean_reproj_px = cnt > 0 ? e / cnt : 0.0;
  return out;
}

double meanReprojectionError(const Scene& scene, double* p90) {
  std::vector<double> errs;
  errs.reserve(scene.points3D.size() * 3);
  for (const Point3D& p : scene.points3D) {
    for (const auto& e : p.track) {
      if (e.first < 1 || e.first > static_cast<int>(scene.images.size())) continue;
      const Image& im = scene.images[e.first - 1];
      if (!im.registered) continue;
      if (e.second < 0 || e.second >= static_cast<int>(im.points2D.size())) continue;
      const Camera& cam = scene.cameras[im.camera_id - 1];
      const Eigen::Vector3d Xc = im.R * p.xyz + im.t;
      if (Xc.z() <= 1e-9) continue;
      const double x = Xc.x() / Xc.z();
      const double y = Xc.y() / Xc.z();
      const double r2 = x * x + y * y;
      const double d = 1.0 + cam.k1 * r2;
      const double u = cam.f * d * x + cam.cx;
      const double v = cam.f * d * y + cam.cy;
      const double dx = u - im.points2D[e.second].x();
      const double dy = v - im.points2D[e.second].y();
      errs.push_back(std::sqrt(dx * dx + dy * dy));
    }
  }
  if (errs.empty()) {
    if (p90) *p90 = 0.0;
    return 0.0;
  }
  double s = 0.0;
  for (double v : errs) s += v;
  const double mean = s / static_cast<double>(errs.size());
  if (p90) {
    std::sort(errs.begin(), errs.end());
    *p90 = errs[std::min(errs.size() - 1, static_cast<size_t>(errs.size() * 0.9))];
  }
  return mean;
}

// 固定所有点与内参, 只优化一张图像的位姿 (PnP 之后的局部细化)
bool refineSinglePose(const Scene& scene, int image_id, const BAOptions& opt, Eigen::Matrix3d* R,
                      Eigen::Vector3d* t) {
  if (image_id < 1 || image_id > static_cast<int>(scene.images.size())) return false;
  const Image& im = scene.images[image_id - 1];
  const Camera& cam = scene.cameras[im.camera_id - 1];

  // 收集该图像上所有已三角化的 2D-3D 对应 (points3D 按 id 递增, 二分查找)
  std::vector<std::pair<Eigen::Vector3d, Eigen::Vector2d>> corrs;
  corrs.reserve(im.point3D_ids.size());
  for (size_t i = 0; i < im.point3D_ids.size() && i < im.points2D.size(); ++i) {
    const long long pid = im.point3D_ids[i];
    if (pid < 0) continue;
    int lo = 0, hi = static_cast<int>(scene.points3D.size());
    while (lo < hi) {
      const int mid = (lo + hi) / 2;
      if (scene.points3D[mid].point3D_id < pid) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    if (lo >= static_cast<int>(scene.points3D.size()) || scene.points3D[lo].point3D_id != pid)
      continue;
    corrs.emplace_back(scene.points3D[lo].xyz, im.points2D[i]);
  }
  if (corrs.size() < 10) return false;

  auto costOf = [&](const Eigen::Matrix3d& Rr, const Eigen::Vector3d& tt) {
    double c = 0.0;
    for (const auto& c2 : corrs) {
      Eigen::Vector2d res;
      if (!residualAndJacobian(cam, Rr, tt, c2.first, c2.second, &res, nullptr, nullptr, nullptr,
                               nullptr))
        continue;
      c += huberCost(res.norm(), opt.huber_delta_px);
    }
    return c;
  };

  Eigen::Matrix3d Rc = *R;
  Eigen::Vector3d tc = *t;
  double lambda = opt.lambda_init;
  double cost = costOf(Rc, tc);
  for (int iter = 0; iter < 15; ++iter) {
    Eigen::Matrix<double, 6, 6> H = Eigen::Matrix<double, 6, 6>::Zero();
    Eigen::Matrix<double, 6, 1> g = Eigen::Matrix<double, 6, 1>::Zero();
    for (const auto& c2 : corrs) {
      Eigen::Vector2d res;
      Eigen::Matrix<double, 2, 3> dPose, dTrans;
      if (!residualAndJacobian(cam, Rc, tc, c2.first, c2.second, &res, &dPose, &dTrans, nullptr,
                               nullptr))
        continue;
      const double rn = res.norm();
      const double w = rn <= opt.huber_delta_px ? 1.0 : opt.huber_delta_px / rn;
      Eigen::Matrix<double, 6, 2> J = Eigen::Matrix<double, 6, 2>::Zero();
      J.block<3, 2>(0, 0) = dPose.transpose();
      J.block<3, 2>(3, 0) = dTrans.transpose();
      H += w * (J * J.transpose());
      g += w * (J * res);
    }
    double md = H.diagonal().maxCoeff();
    if (!(md > 0.0)) md = 1.0;
    H += lambda * md * Eigen::Matrix<double, 6, 6>::Identity();
    Eigen::Matrix<double, 6, 1> d = -H.ldlt().solve(g);
    if (!d.allFinite()) break;
    const Eigen::Matrix3d Rn = expMapRot(Eigen::Vector3d(d[0], d[1], d[2])) * Rc;
    const Eigen::Vector3d tn = tc + Eigen::Vector3d(d[3], d[4], d[5]);
    const double nc = costOf(Rn, tn);
    if (nc < cost) {
      cost = nc;
      Rc = Rn;
      tc = tn;
      lambda = std::max(lambda * 0.3, 1e-9);
    } else {
      lambda *= 8.0;
      if (lambda > 1e8) break;
    }
  }
  *R = Rc;
  *t = tc;
  return true;
}

}  // namespace iii_sfm
