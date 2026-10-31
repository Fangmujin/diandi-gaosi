#!/bin/bash
# 交叉编译 libiii_sfm.so (PIE 可执行文件)
#
# 用法: bash build_android.sh [arm64-v8a|x86_64]
# 前置: 已经用 build_opencv.sh 编译并安装好对应 ABI 的 OpenCV 静态库
set -e

NDK="C:/Users/28928/AppData/Local/Android/Sdk/ndk/27.2.12479018"
CMAKE="C:/Users/28928/.workbuddy/binaries/python/versions/3.13.12/Scripts/cmake.exe"
NINJA="C:/Users/28928/.workbuddy/binaries/python/versions/3.13.12/Scripts/ninja.exe"

ROOT="$(cd "$(dirname "$0")" && pwd)"
ABI="${1:-arm64-v8a}"
BUILD="$ROOT/build-$ABI"
INSTALL="$ROOT/../opencv-install/$ABI"
PREBUILT="$ROOT/../prebuilt/$ABI"

# OpenCV Android 静态库安装布局:
#   sdk/native/staticlibs/<abi>/libopencv_*.a
#   sdk/native/3rdparty/libs/<abi>/liblib*.a
#   sdk/native/jni/include/opencv2/...
LIBDIR="$INSTALL/sdk/native/staticlibs/$ABI"
THIRDDIR="$INSTALL/sdk/native/3rdparty/libs/$ABI"
INCDIR="$INSTALL/sdk/native/jni/include"

if [ ! -f "$LIBDIR/libopencv_core.a" ]; then
  echo "OpenCV static libs not found: $LIBDIR"
  exit 1
fi
if [ ! -f "$INCDIR/opencv2/core.hpp" ]; then
  echo "OpenCV headers not found: $INCDIR"
  exit 1
fi

# 3rdparty 静态库复制到主库目录, 方便统一用 --start-group 解决循环依赖
mkdir -p "$LIBDIR"
if [ -d "$THIRDDIR" ]; then
  cp -n "$THIRDDIR"/*.a "$LIBDIR"/ 2>/dev/null || true
fi

echo "== iii_sfm $ABI =="
echo "opencv lib dir : $LIBDIR"
echo "opencv include : $INCDIR"

mkdir -p "$BUILD" "$PREBUILT"
OCV_LIB_DIR="$LIBDIR" OCV_INCLUDE_DIR="$INCDIR" "$CMAKE" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI="$ABI" \
  -DANDROID_PLATFORM=android-24 \
  -DANDROID_STL=c++_static \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -S "$ROOT" -B "$BUILD"

"$CMAKE" --build "$BUILD" --parallel 16

OUT="$BUILD/libiii_sfm.so"
if [ ! -f "$OUT" ]; then
  echo "build output missing: $OUT"
  exit 1
fi
cp "$OUT" "$PREBUILT/libiii_sfm.so"
STRIP="C:/Users/28928/AppData/Local/Android/Sdk/ndk/27.2.12479018/toolchains/llvm/prebuilt/windows-x86_64/bin/llvm-strip.exe"
"$STRIP" --strip-all "$PREBUILT/libiii_sfm.so"
echo "== done =="
ls -la "$PREBUILT/libiii_sfm.so"
