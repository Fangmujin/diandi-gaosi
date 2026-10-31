#include "colmap_io.h"

#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>

namespace iii_sfm {
namespace {

// ---------------------------------------------------------------------------
// 小端二进制写入器
// ---------------------------------------------------------------------------
class BinaryWriter {
 public:
  explicit BinaryWriter(const std::string& path) : f_(path, std::ios::binary) {}
  bool ok() const { return f_.good(); }

  void writeU64(uint64_t v) { raw(&v, 8); }
  void writeI64(int64_t v) { raw(&v, 8); }
  void writeI32(int32_t v) { raw(&v, 4); }
  void writeF64(double v) { raw(&v, 8); }
  void writeU8(uint8_t v) { raw(&v, 1); }
  void writeBytes(const void* p, size_t n) { f_.write(reinterpret_cast<const char*>(p), n); }

 private:
  void raw(const void* p, size_t n) {
    f_.write(reinterpret_cast<const char*>(p), static_cast<std::streamsize>(n));
  }
  std::ofstream f_;
};

class BinaryReader {
 public:
  explicit BinaryReader(const std::string& path) : f_(path, std::ios::binary) {}
  bool ok() const { return f_.good(); }
  bool readU64(uint64_t* v) { return raw(v, 8); }
  bool readI64(int64_t* v) { return raw(v, 8); }
  bool readI32(int32_t* v) { return raw(v, 4); }
  bool readF64(double* v) { return raw(v, 8); }
  bool readU8(uint8_t* v) { return raw(v, 1); }
  void skip(size_t n) { f_.seekg(static_cast<std::streamoff>(n), std::ios::cur); }
  // 读到 NUL 为止(含 NUL), 与 COLMAP 的 NUL 结尾字符串布局一致。容量上限防御:
  // 名字异常长时直接放弃, 避免被坏数据带着分配巨量内存。
  bool readCString(size_t max_len = 4096) {
    name_.clear();
    for (size_t i = 0; i < max_len; ++i) {
      char c = 0;
      if (!raw(&c, 1)) return false;
      if (c == '\0') return true;
      name_.push_back(c);
    }
    return false;
  }
  const std::string& lastName() const { return name_; }

 private:
  bool raw(void* p, size_t n) {
    f_.read(reinterpret_cast<char*>(p), static_cast<std::streamsize>(n));
    return static_cast<size_t>(f_.gcount()) == n;
  }
  std::ifstream f_;
  std::string name_;
};

constexpr int kSimpleRadialModelId = 2;  // COLMAP SimpleRadialCameraModel

}  // namespace

bool writeColmapBinary(const std::string& out_dir, const Scene& scene, std::string* err) {
  // ---------------- cameras.bin ----------------
  {
    BinaryWriter w(out_dir + "/cameras.bin");
    if (!w.ok()) {
      if (err) *err = "cannot write cameras.bin";
      return false;
    }
    // 写出的相机尺寸 = 原始分辨率;内参按 out_scale 还原到原始分辨率。
    w.writeU64(scene.cameras.size());
    for (const Camera& c : scene.cameras) {
      w.writeI32(c.camera_id);
      w.writeI32(kSimpleRadialModelId);
      w.writeU64(static_cast<uint64_t>(c.out_width > 0 ? c.out_width : c.width));
      w.writeU64(static_cast<uint64_t>(c.out_height > 0 ? c.out_height : c.height));
      w.writeF64(c.f * c.out_scale);
      w.writeF64(c.cx * c.out_scale);
      w.writeF64(c.cy * c.out_scale);
      w.writeF64(c.k1);
    }
  }

  // ---------------- images.bin ----------------
  {
    BinaryWriter w(out_dir + "/images.bin");
    if (!w.ok()) {
      if (err) *err = "cannot write images.bin";
      return false;
    }
    size_t num_reg = 0;
    for (const Image& im : scene.images) {
      if (im.registered) ++num_reg;
    }
    w.writeU64(num_reg);
    for (const Image& im : scene.images) {
      if (!im.registered) continue;
      w.writeI32(im.image_id);
      const Eigen::Vector4d q = qvecFromMatrix(im.R);
      w.writeF64(q[0]);
      w.writeF64(q[1]);
      w.writeF64(q[2]);
      w.writeF64(q[3]);
      w.writeF64(im.t.x());
      w.writeF64(im.t.y());
      w.writeF64(im.t.z());
      w.writeI32(im.camera_id);

      // COLMAP 二进制 images.bin 里图像名是 **NUL 结尾、不带长度前缀** 的字符串。
      // (官方 scripts/python/read_write_model.py::read_images_binary 是逐字节读到 '\0'
      //  为止; brush 的 colmap-reader 也用 read_until(b'\0')。写 u64 name_len 会让
      //  下游把长度字段当成名字的一部分, 再把名字头几个字节当成 num_points2D,
      //  结果 num_points2D 变成天文数字 -> Vec::with_capacity 容量溢出 panic。)
      const std::string& name = im.name;
      w.writeBytes(name.data(), name.size());
      const char zero = '\0';
      w.writeBytes(&zero, 1);

      w.writeU64(im.points2D.size());
      // 2D 点坐标还原到原始分辨率
      for (size_t i = 0; i < im.points2D.size(); ++i) {
        w.writeF64(im.points2D[i].x() * im.scale);
        w.writeF64(im.points2D[i].y() * im.scale);
        w.writeI64(im.point3D_ids[i]);
      }
    }
  }

  // ---------------- points3D.bin ----------------
  {
    BinaryWriter w(out_dir + "/points3D.bin");
    if (!w.ok()) {
      if (err) *err = "cannot write points3D.bin";
      return false;
    }
    w.writeU64(scene.points3D.size());
    for (const Point3D& p : scene.points3D) {
      w.writeI64(p.point3D_id);
      w.writeF64(p.xyz.x());
      w.writeF64(p.xyz.y());
      w.writeF64(p.xyz.z());
      w.writeU8(p.rgb[0]);
      w.writeU8(p.rgb[1]);
      w.writeU8(p.rgb[2]);
      w.writeF64(p.error);
      w.writeU64(p.track.size());
      for (const auto& e : p.track) {
        w.writeI32(e.first);
        w.writeI32(e.second);
      }
    }
  }
  return true;
}

bool writeColmapText(const std::string& out_dir, const Scene& scene, std::string* err) {
  {
    std::ofstream f(out_dir + "/cameras.txt");
    if (!f) {
      if (err) *err = "cannot write cameras.txt";
      return false;
    }
    f << "# Camera list with one line of data per camera:\n"
      << "#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n";
    for (const Camera& c : scene.cameras) {
      f << c.camera_id << " SIMPLE_RADIAL " << c.out_width << " " << c.out_height << " "
        << (c.f * c.out_scale) << " " << (c.cx * c.out_scale) << " " << (c.cy * c.out_scale) << " "
        << c.k1 << "\n";
    }
  }
  {
    std::ofstream f(out_dir + "/images.txt");
    if (!f) {
      if (err) *err = "cannot write images.txt";
      return false;
    }
    f << "# Image list with two lines of data per image:\n"
      << "#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n"
      << "#   POINTS2D[] as (X, Y, POINT3D_ID)\n";
    for (const Image& im : scene.images) {
      if (!im.registered) continue;
      const Eigen::Vector4d q = qvecFromMatrix(im.R);
      f << im.image_id << " " << q[0] << " " << q[1] << " " << q[2] << " " << q[3] << " "
        << im.t.x() << " " << im.t.y() << " " << im.t.z() << " " << im.camera_id << " "
        << im.name << "\n";
      for (size_t i = 0; i < im.points2D.size(); ++i) {
        f << (im.points2D[i].x() * im.scale) << " " << (im.points2D[i].y() * im.scale) << " "
          << im.point3D_ids[i];
        if (i + 1 < im.points2D.size()) f << " ";
      }
      f << "\n";
    }
  }
  {
    std::ofstream f(out_dir + "/points3D.txt");
    if (!f) {
      if (err) *err = "cannot write points3D.txt";
      return false;
    }
    f << "# 3D point list with one line of data per point:\n"
      << "#   POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n";
    for (const Point3D& p : scene.points3D) {
      f << p.point3D_id << " " << p.xyz.x() << " " << p.xyz.y() << " " << p.xyz.z() << " "
        << static_cast<int>(p.rgb[0]) << " " << static_cast<int>(p.rgb[1]) << " "
        << static_cast<int>(p.rgb[2]) << " " << p.error;
      for (const auto& e : p.track) f << " " << e.first << " " << e.second;
      f << "\n";
    }
  }
  return true;
}

bool readColmapStats(const std::string& out_dir, ModelStats* stats, std::string* err) {
  {
    BinaryReader r(out_dir + "/cameras.bin");
    if (!r.ok()) {
      if (err) *err = "cannot reopen cameras.bin";
      return false;
    }
    uint64_t n = 0;
    if (!r.readU64(&n)) return false;
    stats->num_cameras = n;
    for (uint64_t i = 0; i < n; ++i) {
      int32_t cid, mid;
      uint64_t w, h;
      double p[4] = {0, 0, 0, 0};
      r.readI32(&cid);
      r.readI32(&mid);
      r.readU64(&w);
      r.readU64(&h);
      for (int k = 0; k < 4; ++k) r.readF64(&p[k]);
      if (mid != kSimpleRadialModelId) {
        if (err) *err = "unexpected camera model id in cameras.bin";
        return false;
      }
      if (!(p[0] > 0) || !(w > 0) || !(h > 0)) {
        if (err) *err = "degenerate camera in cameras.bin";
        return false;
      }
    }
  }
  {
    BinaryReader r(out_dir + "/images.bin");
    if (!r.ok()) {
      if (err) *err = "cannot reopen images.bin";
      return false;
    }
    uint64_t n = 0;
    if (!r.readU64(&n)) return false;
    stats->num_images = n;
    for (uint64_t i = 0; i < n; ++i) {
      int32_t iid, cid;
      double q[4], t[3];
      uint64_t npts = 0;
      r.readI32(&iid);
      for (int k = 0; k < 4; ++k) r.readF64(&q[k]);
      for (int k = 0; k < 3; ++k) r.readF64(&t[k]);
      r.readI32(&cid);
      if (!r.readCString()) {
        if (err) *err = "images.bin: malformed image name";
        return false;
      }
      r.readU64(&npts);
      // 防御: 单张图的 2D 点数不应该超过几百万, 超了说明字节流错位
      if (npts > 20u * 1000u * 1000u) {
        if (err) *err = "images.bin: implausible num_points2D (stream misaligned?)";
        return false;
      }
      for (uint64_t k = 0; k < npts; ++k) {
        double x, y;
        int64_t pid;
        r.readF64(&x);
        r.readF64(&y);
        r.readI64(&pid);
      }
      const double qn = std::sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]);
      if (qn < 0.99 || qn > 1.01) {
        if (err) *err = "non-unit quaternion in images.bin";
        return false;
      }
    }
  }
  {
    BinaryReader r(out_dir + "/points3D.bin");
    if (!r.ok()) {
      if (err) *err = "cannot reopen points3D.bin";
      return false;
    }
    uint64_t n = 0;
    if (!r.readU64(&n)) return false;
    stats->num_points3D = n;
    double err_sum = 0.0;
    for (uint64_t i = 0; i < n; ++i) {
      int64_t pid;
      double xyz[3], e;
      uint8_t rgb[3];
      uint64_t tlen = 0;
      r.readI64(&pid);
      for (int k = 0; k < 3; ++k) r.readF64(&xyz[k]);
      for (int k = 0; k < 3; ++k) r.readU8(&rgb[k]);
      r.readF64(&e);
      r.readU64(&tlen);
      for (uint64_t k = 0; k < tlen; ++k) {
        int32_t a, b;
        r.readI32(&a);
        r.readI32(&b);
      }
      stats->num_observations += tlen;
      err_sum += e;
    }
    stats->mean_reproj_error = n > 0 ? err_sum / static_cast<double>(n) : 0.0;
  }
  return true;
}

}  // namespace iii_sfm
