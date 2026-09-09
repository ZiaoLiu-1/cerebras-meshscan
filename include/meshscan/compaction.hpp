#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace meshscan::compaction {

using Value = std::int32_t;
inline constexpr std::size_t kMaxPeCount = 8;
inline constexpr std::size_t kMaxChunkSize = 64;

struct FilterResult {
  std::vector<Value> output;
  std::vector<std::size_t> indices;
};

struct PeTrace {
  std::size_t pe{};
  std::size_t input_start{};
  std::size_t valid_count{};
  std::size_t local_count{};
  std::size_t offset{};
  std::size_t prefix_count{};
  std::size_t output_count{};
  std::size_t received_count{};
  std::size_t forwarded_count{};
  std::vector<Value> output_values;
  std::vector<std::size_t> output_indices;
};

struct Result {
  std::size_t width{};
  std::size_t chunk_size{};
  Value threshold{};
  std::vector<Value> output;
  std::vector<std::size_t> indices;
  std::size_t selected_count{};
  std::vector<PeTrace> pes;
};

// This oracle never adds input values: every int32 value is supported.
FilterResult serial_filter(const std::vector<Value>& values, Value threshold);

// Balanced local filtering, eastbound count prefixes, then westbound records.
// Checks packet ownership, occupied slots, stability, and serial equivalence.
Result simulate(const std::vector<Value>& values, std::size_t width,
                std::size_t chunk_size, Value threshold);

std::string to_json(const std::vector<Value>& input, const Result& result);

}  // namespace meshscan::compaction
