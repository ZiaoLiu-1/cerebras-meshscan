#include "meshscan/compaction.hpp"

#include <algorithm>
#include <exception>
#include <iostream>
#include <iterator>
#include <limits>
#include <numeric>
#include <random>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using meshscan::compaction::Value;
std::size_t verified_cases = 0;
std::size_t rejected_cases = 0;

void expect(bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void verify(const std::vector<Value>& values, std::size_t width,
            std::size_t chunk_size, Value threshold) {
  const auto actual = meshscan::compaction::simulate(values, width, chunk_size, threshold);
  std::vector<Value> expected_values;
  std::copy_if(values.begin(), values.end(), std::back_inserter(expected_values),
               [threshold](Value value) { return value >= threshold; });
  std::vector<std::size_t> all_indices(values.size());
  std::iota(all_indices.begin(), all_indices.end(), std::size_t{0});
  std::vector<std::size_t> expected_indices;
  std::copy_if(all_indices.begin(), all_indices.end(), std::back_inserter(expected_indices),
               [&values, threshold](std::size_t index) { return values[index] >= threshold; });
  expect(actual.output == expected_values, "std::copy_if values disagree");
  expect(actual.indices == expected_indices, "std::copy_if indices disagree");
  expect(actual.selected_count == expected_values.size(), "wrong selected count");
  expect(actual.width == width && actual.chunk_size == chunk_size && actual.threshold == threshold,
         "configuration changed");
  expect(actual.pes.size() == width, "missing PE trace");
  const auto serial = meshscan::compaction::serial_filter(values, threshold);
  expect(serial.output == expected_values && serial.indices == expected_indices,
         "serial filter disagrees with standard-library filter");

  std::vector<std::size_t> source(values.size());
  std::size_t start = 0;
  std::size_t prefix = 0;
  for (std::size_t pe = 0; pe < width; ++pe) {
    const auto& trace = actual.pes[pe];
    const std::size_t count = values.size() / width + (pe < values.size() % width ? 1 : 0);
    const auto begin = values.begin() + static_cast<std::ptrdiff_t>(start);
    const auto end = begin + static_cast<std::ptrdiff_t>(count);
    const std::size_t selected = static_cast<std::size_t>(
        std::count_if(begin, end, [threshold](Value value) { return value >= threshold; }));
    expect(trace.pe == pe && trace.input_start == start && trace.valid_count == count,
           "wrong balanced partition");
    expect(trace.offset == prefix && trace.local_count == selected, "wrong count scan");
    prefix += selected;
    expect(trace.prefix_count == prefix, "wrong inclusive count prefix");
    for (std::size_t index = start; index < start + count; ++index) {
      source[index] = pe;
    }
    start += count;
  }

  // Independent crossing formula, not the oracle's packet-hop implementation.
  for (std::size_t pe = 0; pe < width; ++pe) {
    const auto& trace = actual.pes[pe];
    std::size_t received = 0;
    std::size_t forwarded = 0;
    std::vector<Value> owned_values;
    std::vector<std::size_t> owned_indices;
    for (std::size_t rank = 0; rank < expected_indices.size(); ++rank) {
      const std::size_t index = expected_indices[rank];
      const std::size_t owner = rank / chunk_size;
      expect(owner <= source[index], "fixture requires an unsupported eastward move");
      received += source[index] > pe && owner <= pe ? 1 : 0;
      forwarded += source[index] >= pe && owner < pe ? 1 : 0;
      if (owner == pe) {
        owned_values.push_back(values[index]);
        owned_indices.push_back(index);
      }
    }
    expect(trace.received_count == received, "wrong east-link received count");
    expect(trace.forwarded_count == forwarded, "wrong west-link forwarded count");
    expect(trace.output_count == owned_values.size(), "wrong output count");
    expect(trace.output_values == owned_values && trace.output_indices == owned_indices,
           "wrong packed physical output ownership");
    expect(trace.local_count + received == trace.output_count + forwarded,
           "record conservation failed");
  }
  ++verified_cases;
}

void test_known_cases() {
  const Value minimum = std::numeric_limits<Value>::min();
  const Value maximum = std::numeric_limits<Value>::max();
  const std::vector<std::pair<std::vector<Value>, Value>> cases{
      {{}, 0}, {{0}, 0}, {{-1}, 0}, {{1}, 0}, {{7, 7, 7, 7}, 7},
      {{7, 7, 7, 7}, 8}, {{1, 2, 3}, minimum}, {{minimum, maximum}, maximum},
      {{minimum, minimum, maximum, maximum}, minimum},
      {{maximum, maximum, maximum, maximum}, 0},
      {{2, 8, 1, 9, 3, 7, 4, 6, 5}, 5},
      {{-4, 9, -4, 9, 0, 9, -4, 9}, 9}, {{0, 0, 0, 0, 5, 6, 7, 8}, 1}};
  for (std::size_t width = 1; width <= 8; ++width) {
    for (const auto& [values, threshold] : cases) {
      verify(values, width, 64, threshold);
    }
  }
  const auto moved = meshscan::compaction::simulate({0, 0, 0, 0, 5, 6, 7, 8}, 4, 2, 1);
  expect(moved.output == std::vector<Value>({5, 6, 7, 8}), "wrong hand-worked result");
  expect(moved.indices == std::vector<std::size_t>({4, 5, 6, 7}), "wrong hand-worked indices");
  const std::vector<std::size_t> receives{2, 4, 2, 0};
  const std::vector<std::size_t> forwards{0, 2, 4, 2};
  for (std::size_t pe = 0; pe < 4; ++pe) {
    expect(moved.pes[pe].received_count == receives[pe], "wrong hand-worked receives");
    expect(moved.pes[pe].forwarded_count == forwards[pe], "wrong hand-worked forwards");
  }
  verify({0, 0, 0, 0, 5, 6, 7, 8}, 4, 2, 1);
}

void test_capacity_boundaries() {
  for (std::size_t width = 1; width <= 8; ++width) {
    for (const std::size_t chunk : {1U, 2U, 3U, 16U, 64U}) {
      const std::size_t capacity = width * chunk;
      const std::set<std::size_t> lengths{0, 1, width - 1, width, capacity - 1, capacity};
      for (const std::size_t length : lengths) {
        std::vector<Value> values(length);
        for (std::size_t i = 0; i < length; ++i) {
          values[i] = static_cast<Value>(i % 7) - 3;
        }
        verify(values, width, chunk, 0);
        verify(values, width, chunk, -4);
        verify(values, width, chunk, 4);
      }
    }
  }
}

void test_randomized() {
  std::mt19937_64 random(0xC04FAC7ULL);
  std::uniform_int_distribution<Value> value_dist(std::numeric_limits<Value>::min(),
                                                  std::numeric_limits<Value>::max());
  std::uniform_int_distribution<std::size_t> width_dist(1, 8);
  std::uniform_int_distribution<std::size_t> chunk_dist(1, 64);
  for (int trial = 0; trial < 400; ++trial) {
    const std::size_t width = width_dist(random);
    const std::size_t chunk = chunk_dist(random);
    std::uniform_int_distribution<std::size_t> length_dist(0, width * chunk);
    std::vector<Value> values(length_dist(random));
    std::generate(values.begin(), values.end(), [&] { return value_dist(random); });
    verify(values, width, chunk, value_dist(random));
  }
}

void test_invalid_configurations() {
  const auto rejected = [](const std::vector<Value>& values, std::size_t width, std::size_t chunk) {
    try {
      (void)meshscan::compaction::simulate(values, width, chunk, 0);
    } catch (const std::invalid_argument&) {
      ++rejected_cases;
      return;
    }
    throw std::runtime_error("invalid configuration accepted");
  };
  rejected({}, 0, 1);
  rejected({}, 9, 1);
  rejected({}, std::numeric_limits<std::size_t>::max(), 1);
  rejected({}, 1, 0);
  rejected({}, 1, 65);
  rejected({}, 1, std::numeric_limits<std::size_t>::max());
  rejected({1, 2, 3}, 1, 2);
  rejected(std::vector<Value>(513, 0), 8, 64);
}

}  // namespace

int main() {
  try {
    test_known_cases();
    test_capacity_boundaries();
    test_randomized();
    test_invalid_configurations();
    std::cout << "PASS: " << verified_cases << " compaction cases (including 400 fixed-seed random), "
              << rejected_cases << " rejected configurations\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL: " << error.what() << '\n';
    return 1;
  }
}
