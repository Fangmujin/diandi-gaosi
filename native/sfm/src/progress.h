#pragma once

#include <functional>

namespace iii_sfm {

// stdout JSONL 进度回调:
//   {"stage":"features|matching|mapping","current":N,"total":N,"registered":N,"points":N}
using ProgressFn = std::function<void(const char* stage, int current, int total, int registered,
                                      int points)>;

}  // namespace iii_sfm
