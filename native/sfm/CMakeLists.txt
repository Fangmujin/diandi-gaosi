cmake_minimum_required(VERSION 3.22)
project(iii_sfm CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# ---------------------------------------------------------------------------
# Eigen (header-only, vendored)
# ---------------------------------------------------------------------------
set(EIGEN_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/third_party/eigen")

# ---------------------------------------------------------------------------
# OpenCV (交叉编译出来的 Android 静态库)
# 优先 find_package(OpenCV); 找不到时退回 OCV_INCLUDE_DIR / OCV_LIB_DIR
# ---------------------------------------------------------------------------
set(OCV_LIBS "")
set(OCV_INC "")
if(OpenCV_DIR OR DEFINED ENV{OpenCV_DIR})
  find_package(OpenCV QUIET COMPONENTS core imgproc features2d calib3d imgcodecs flann)
endif()
if(OpenCV_FOUND)
  set(OCV_INC ${OpenCV_INCLUDE_DIRS})
  set(OCV_LIBS ${OpenCV_LIBS})
  message(STATUS "OpenCV via find_package: ${OpenCV_VERSION} libs=${OpenCV_LIBS}")
else()
  set(OCV_INC "$ENV{OCV_INCLUDE_DIR}")
  if(NOT OCV_INC)
    set(OCV_INC "${CMAKE_CURRENT_SOURCE_DIR}/../opencv-install/include")
  endif()
  set(OCV_LIB_DIR "$ENV{OCV_LIB_DIR}")
  file(GLOB OCV_CORE_LIBS "${OCV_LIB_DIR}/libopencv_*.a")
  file(GLOB OCV_3P_LIBS "${OCV_LIB_DIR}/liblib*.a" "${OCV_LIB_DIR}/libzlib.a"
       "${OCV_LIB_DIR}/libtegra_hal.a" "${OCV_LIB_DIR}/libcpufeatures.a"
       "${OCV_LIB_DIR}/libittnotify.a" "${OCV_LIB_DIR}/libippiw.a"
       "${OCV_LIB_DIR}/libippicv.a" "${OCV_LIB_DIR}/libade.a"
       "${OCV_LIB_DIR}/libquirc.a" "${OCV_LIB_DIR}/libIlmImf.a"
       "${OCV_LIB_DIR}/libtbb.a")
  set(OCV_LIBS ${OCV_CORE_LIBS} ${OCV_3P_LIBS})
  message(STATUS "OpenCV via OCV_LIB_DIR=${OCV_LIB_DIR}")
  message(STATUS "OpenCV libs: ${OCV_LIBS}")
endif()
if(NOT OCV_LIBS)
  message(FATAL_ERROR "OpenCV static libraries not found. Set OCV_LIB_DIR / OpenCV_DIR.")
endif()

# ---------------------------------------------------------------------------
# 目标: 一个 PIE 可执行文件, 文件名按契约命名为 libiii_sfm.so
# (Android 上通过 exec 调用, 与 UI 侧的二进制契约保持一致)
# ---------------------------------------------------------------------------
add_executable(iii_sfm
  src/main.cpp
  src/colmap_io.cpp
  src/feature_extract.cpp
  src/matching.cpp
  src/incremental.cpp
  src/ba.cpp
)

target_include_directories(iii_sfm PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${EIGEN_ROOT}
  ${OCV_INC}
)

# 静态库之间有循环依赖, 用 --start-group/--end-group 解决
set(OCV_GROUP "")
if(NOT OpenCV_FOUND)
  set(OCV_GROUP -Wl,--start-group ${OCV_LIBS} -Wl,--end-group)
else()
  set(OCV_GROUP ${OCV_LIBS})
endif()

# z: OpenCV 的 persistence.cpp / libpng 需要 zlib (gzopen 等), Android NDK 自带
target_link_libraries(iii_sfm PRIVATE ${OCV_GROUP} z ${CMAKE_DL_LIBS} m log)

# Android 15+ 的 16KB 页要求: ELF LOAD 段必须按 16KB 对齐, 否则在启用 16KB 页的
# 机器上加载会失败。默认链接脚本给的是 4KB(0x1000), 这里显式改成 16KB(0x4000)。
# 只影响段布局, 不改变任何代码生成参数。
target_link_options(iii_sfm PRIVATE
    "-Wl,-z,max-page-size=16384"
    "-Wl,-z,common-page-size=16384")

target_compile_options(iii_sfm PRIVATE -ffast-math -fomit-frame-pointer)
if(ANDROID_ABI STREQUAL "arm64-v8a")
  # arm64 = the real-phone deliverable. Keep -O3 exactly as it was when the SfM
  # numerics were validated (44/44 registered, 0.3822px). Do not change this
  # without re-running that validation.
  target_compile_options(iii_sfm PRIVATE -O3 -march=armv8-a)
else()
  # x86_64 = emulator only. -O3 inlines aggressively and blows .text up to
  # 19.4MB (vs 5.1MB on arm64), which pushes the emulator APK over the 45MB
  # budget. -Os trades speed for size; -ffast-math is unchanged above.
  target_compile_options(iii_sfm PRIVATE -Os)
endif()

set_target_properties(iii_sfm PROPERTIES
  PREFIX ""
  OUTPUT_NAME "libiii_sfm"
  SUFFIX ".so"
)
