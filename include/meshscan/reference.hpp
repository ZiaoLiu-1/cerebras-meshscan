#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace meshscan {

using Value = std::int32_t;
inline constexpr std::size_t kMaxReferencePeCount = 4096;

struct PeTrace {
  std::size_t pe_index{};
  std::size_t begin{};
  std::size_t end{};
  Value incoming_carry{};
  Value local_total{};
  std::vector<Value> local_prefix;
  std::vector<Value> output;
};

struct SimulationResult {
  std::vector<Value> output;
  std::vector<PeTrace> traces;
};

std::vector<Value> serial_inclusive_scan(const std::vector<Value>& values);

SimulationResult simulate_linear_mesh(const std::vector<Value>& values,
                                      std::size_t pe_count);

std::string to_json(const std::vector<Value>& input,
                    const SimulationResult& result);

}  // namespace meshscan
