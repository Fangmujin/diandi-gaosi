#!/bin/bash
# 为 Android 交叉编译 OpenCV 静态库 (只需要 core/imgproc/features2d/calib3d/imgcodecs/flann)
#
# 用法: bash build_opencv.sh [arm64-v8a|x86_64]
set -e

NDK="C:/Users/28928/AppData/Local/Android/Sdk/ndk/27.2.12479018"
CMAKE="C:/Users/28928/.workbuddy/binaries/python/versions/3.13.12/Scripts/cmake.exe"
NINJA="C:/Users/28928/.workbuddy/binaries/python/versions/3.13.12/Scripts/ninja.exe"

ROOT="$(cd "$(dirname "$0")" && pwd)"
DEPS="C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/_deps"
SRC="$DEPS/opencv-4.10.0"
ABI="${1:-arm64-v8a}"

BUILD="$DEPS/opencv-build-$ABI"
INSTALL="$ROOT/../opencv-install/$ABI"

echo "== OpenCV $ABI =="
echo "src     : $SRC"
echo "build   : $BUILD"
echo "install : $INSTALL"

mkdir -p "$BUILD" "$INSTALL"

"$CMAKE" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI="$ABI" \
  -DANDROID_PLATFORM=android-24 \
  -DANDROID_STL=c++_static \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY \
  -DCMAKE_INSTALL_PREFIX="$INSTALL" \
  -DBUILD_LIST=core,imgproc,features2d,calib3d,imgcodecs,flann \
  -DBUILD_SHARED_LIBS=OFF \
  -DENABLE_PIC=ON \
  -DBUILD_ANDROID_PROJECTS=OFF \
  -DBUILD_ANDROID_EXAMPLES=OFF \
  -DINSTALL_ANDROID_EXAMPLES=OFF \
  -DBUILD_JAVA=OFF \
  -DBUILD_FAT_JAVA_LIB=OFF \
  -DBUILD_opencv_java=OFF \
  -DBUILD_EXAMPLES=OFF \
  -DBUILD_TESTS=OFF \
  -DBUILD_PERF_TESTS=OFF \
  -DBUILD_DOCS=OFF \
  -DBUILD_opencv_apps=OFF \
  -DWITH_IPP=OFF \
  -DWITH_OPENCL=OFF \
  -DWITH_CUDA=OFF \
  -DWITH_PROTOBUF=OFF \
  -DWITH_ADE=OFF \
  -DWITH_QUIRC=OFF \
  -DWITH_FFMPEG=OFF \
  -DWITH_GTK=OFF \
  -DWITH_ITT=OFF \
  -DWITH_OPENEXR=OFF \
  -DWITH_EIGEN=OFF \
  -DWITH_TBB=OFF \
  -S "$SRC" -B "$BUILD"

"$CMAKE" --build "$BUILD" --parallel "$NUMBER_OF_PROCESSORS"
"$CMAKE" --install "$BUILD"

echo "== done: $ABI =="
find "$INSTALL" -name "*.a" | head -40
