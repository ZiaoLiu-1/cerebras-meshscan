#include "meshscan/reference.hpp"

#include <cstdint>
#include <exception>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void expect(bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

template <typename Exception, typename Function>
void expect_throws(Function function, const std::string& message) {
  try {
    function();
  } catch (const Exception&) {
    return;
  }
  throw std::runtime_error(message);
}

void expect_matches_oracle(const std::vector<meshscan::Value>& values,
                           std::size_t pe_count) {
  const auto expected = meshscan::serial_inclusive_scan(values);
  const auto actual = meshscan::simulate_linear_mesh(values, pe_count);
  expect(actual.output == expected, "mesh output differs from serial oracle");
  expect(actual.traces.size() == pe_count, "trace count differs from PE count");
}

void test_known_cases() {
  expect_matches_oracle({}, 4);
  expect_matches_oracle({8}, 1);
  expect_matches_oracle({3, -2, 5, 7, -4, 1}, 4);
  expect_matches_oracle({1, 2, 3}, 8);
  expect_matches_oracle({0, 0, 0, 0}, 3);

  const auto result =
      meshscan::simulate_linear_mesh({3, -2, 5, 7, -4, 1}, 4);
  expect(result.output == std::vector<meshscan::Value>({3, 1, 6, 13, 9, 10}),
         "known result is incorrect");
  expect(result.traces[0].begin == 0 && result.traces[0].end == 2,
         "first partition bounds are incorrect");
  expect(result.traces[2].incoming_carry == 13,
         "carry into third PE is incorrect");
  expect(result.traces[3].output == std::vector<meshscan::Value>({10}),
         "last PE output is incorrect");
}

void test_randomized_cases() {
  std::mt19937_64 generator(0xCE4EB2A5ULL);
  std::uniform_int_distribution<int> length_distribution(0, 128);
  std::uniform_int_distribution<int> value_distribution(-1000, 1000);
  const std::vector<std::size_t> pe_counts{1, 2, 3, 4, 8, 16};

  for (int trial = 0; trial < 300; ++trial) {
    const int length = length_distribution(generator);
    std::vector<meshscan::Value> values;
    values.reserve(static_cast<std::size_t>(length));
    for (int index = 0; index < length; ++index) {
      values.push_back(value_distribution(generator));
    }
    for (const std::size_t pe_count : pe_counts) {
      expect_matches_oracle(values, pe_count);
    }
  }
}

void test_invalid_and_overflow_cases() {
  expect_throws<std::invalid_argument>(
      [] { (void)meshscan::simulate_linear_mesh({1, 2, 3}, 0); },
      "zero PE count did not fail");
  expect_throws<std::invalid_argument>(
      [] {
        (void)meshscan::simulate_linear_mesh(
            {1, 2, 3}, meshscan::kMaxReferencePeCount + 1);
      },
      "unbounded PE count did not fail");

  expect_throws<std::overflow_error>(
      [] {
        (void)meshscan::serial_inclusive_scan(
            {std::numeric_limits<meshscan::Value>::max(), 1});
      },
      "serial positive overflow did not fail");
  expect_throws<std::overflow_error>(
      [] {
        (void)meshscan::simulate_linear_mesh(
            {std::numeric_limits<meshscan::Value>::max(), 1}, 2);
      },
      "mesh positive overflow did not fail");

  expect_throws<std::overflow_error>(
      [] {
        (void)meshscan::serial_inclusive_scan(
            {std::numeric_limits<meshscan::Value>::min(), -1});
      },
      "serial negative overflow did not fail");

  // The MVP uses checked 32-bit arithmetic inside each partition. A local
  // prefix may therefore be unsupported even when every global prefix fits.
  const meshscan::Value maximum = std::numeric_limits<meshscan::Value>::max();
  const std::vector<meshscan::Value> reassociation_case{-1, 0, maximum, 1};
  expect(meshscan::serial_inclusive_scan(reassociation_case).back() == maximum,
         "reassociation fixture should be globally representable");
  expect_throws<std::overflow_error>(
      [&reassociation_case] {
        (void)meshscan::simulate_linear_mesh(reassociation_case, 2);
      },
      "layout-dependent local overflow did not fail");
}

}  // namespace

int main() {
  try {
    test_known_cases();
    test_randomized_cases();
    test_invalid_and_overflow_cases();
    std::cout << "PASS: deterministic, randomized, and overflow tests\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL: " << error.what() << '\n';
    return 1;
  }
}
