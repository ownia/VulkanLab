#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

namespace vklab {

struct Timing {
    double median;
    double minimum;
    double maximum;
};

inline Timing summarize(std::vector<double> samples) {
    if (samples.empty()) throw std::invalid_argument("Timing samples must not be empty");
    std::sort(samples.begin(), samples.end());
    size_t middle = samples.size() / 2;
    double median = (samples[(samples.size() - 1) / 2] + samples[middle]) * 0.5;
    return {median, samples.front(), samples.back()};
}

inline double timestampMilliseconds(uint64_t start, uint64_t end, uint32_t validBits, float nanosecondsPerTick) {
    if (!validBits || validBits > 64 || !std::isfinite(nanosecondsPerTick) || nanosecondsPerTick <= 0)
        throw std::invalid_argument("Invalid timestamp properties");
    uint64_t mask = validBits == 64 ? std::numeric_limits<uint64_t>::max() : (uint64_t(1) << validBits) - 1;
    uint64_t elapsed = (end - start) & mask;
    return static_cast<double>(elapsed) * nanosecondsPerTick / 1e6;
}

}