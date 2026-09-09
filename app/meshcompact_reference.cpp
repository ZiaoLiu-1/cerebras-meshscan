#include "meshscan/compaction.hpp"

#include <charconv>
#include <exception>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

template <typename T>
T parse_integer(std::string_view text, std::string_view flag) {
  T value{};
  const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
  if (text.empty() || error != std::errc{} || end != text.data() + text.size()) {
    throw std::invalid_argument(std::string(flag) + " expects a decimal integer in range");
  }
  return value;
}

std::vector<meshscan::compaction::Value> parse_values(std::string_view text) {
  std::vector<meshscan::compaction::Value> values;
  if (text.empty()) {
    return values;
  }
  std::size_t begin = 0;
  while (begin <= text.size()) {
    const std::size_t comma = text.find(',', begin);
    const std::size_t end = comma == std::string_view::npos ? text.size() : comma;
    values.push_back(parse_integer<meshscan::compaction::Value>(text.substr(begin, end - begin), "--values"));
    if (values.size() > meshscan::compaction::kMaxPeCount * meshscan::compaction::kMaxChunkSize) {
      throw std::invalid_argument("input exceeds maximum 512-value capacity");
    }
    if (comma == std::string_view::npos) {
      break;
    }
    begin = comma + 1;
  }
  return values;
}

void usage(std::ostream& stream) {
  stream << "Usage: meshcompact-reference --pes P --chunk-size C --threshold T --values A,B,C\n"
            "P: 1..8; C: 1..64; T and values: int32; empty --values is supported.\n";
}

}  // namespace

int main(int argc, char* argv[]) {
  try {
    if (argc == 2 && std::string_view(argv[1]) == "--help") {
      usage(std::cout);
      return 0;
    }
    std::size_t width{};
    std::size_t chunk_size{};
    meshscan::compaction::Value threshold{};
    std::vector<meshscan::compaction::Value> values;
    std::set<std::string_view> seen;
    for (int index = 1; index < argc; ++index) {
      const std::string_view flag = argv[index];
      if (flag != "--pes" && flag != "--chunk-size" && flag != "--threshold" && flag != "--values") {
        throw std::invalid_argument("unknown argument: " + std::string(flag));
      }
      if (!seen.insert(flag).second) {
        throw std::invalid_argument("duplicate argument: " + std::string(flag));
      }
      if (++index >= argc) {
        throw std::invalid_argument(std::string(flag) + " requires a value");
      }
      const std::string_view text = argv[index];
      if (flag == "--pes") {
        width = parse_integer<std::size_t>(text, flag);
      } else if (flag == "--chunk-size") {
        chunk_size = parse_integer<std::size_t>(text, flag);
      } else if (flag == "--threshold") {
        threshold = parse_integer<meshscan::compaction::Value>(text, flag);
      } else {
        values = parse_values(text);
      }
    }
    if (seen.size() != 4) {
      throw std::invalid_argument("--pes, --chunk-size, --threshold and --values are required");
    }
    const auto result = meshscan::compaction::simulate(values, width, chunk_size, threshold);
    std::cout << meshscan::compaction::to_json(values, result) << '\n';
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "meshcompact-reference: " << error.what() << '\n';
    usage(std::cerr);
    return 2;
  }
}
