#include "meshscan/reference.hpp"

#include <charconv>
#include <cstddef>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

std::size_t parse_pe_count(std::string_view text) {
  std::size_t value = 0;
  const auto [pointer, error] =
      std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || pointer != text.data() + text.size() || value == 0) {
    throw std::invalid_argument("--pes expects a positive integer");
  }
  return value;
}

meshscan::Value parse_value(std::string_view text) {
  if (text.empty()) {
    throw std::invalid_argument("--values contains an empty item");
  }

  meshscan::Value value = 0;
  const auto [pointer, error] =
      std::from_chars(text.data(), text.data() + text.size(), value);
  if (error != std::errc{} || pointer != text.data() + text.size()) {
    throw std::invalid_argument("--values expects comma-separated signed integers");
  }
  return value;
}

std::vector<meshscan::Value> parse_values(std::string_view text) {
  std::vector<meshscan::Value> values;
  if (text.empty()) {
    return values;
  }

  std::size_t begin = 0;
  while (begin <= text.size()) {
    const std::size_t comma = text.find(',', begin);
    const std::size_t end =
        comma == std::string_view::npos ? text.size() : comma;
    values.push_back(parse_value(text.substr(begin, end - begin)));
    if (comma == std::string_view::npos) {
      break;
    }
    begin = comma + 1;
  }
  return values;
}

void print_usage(std::ostream& stream) {
  stream << "Usage: meshscan-reference [--pes N] [--values A,B,C]\n";
}

}  // namespace

int main(int argc, char* argv[]) {
  try {
    std::size_t pe_count = 4;
    std::vector<meshscan::Value> values{3, -2, 5, 7, -4, 1};

    for (int index = 1; index < argc; ++index) {
      const std::string_view argument = argv[index];
      if (argument == "--help") {
        print_usage(std::cout);
        return 0;
      }
      if ((argument == "--pes" || argument == "--values") && index + 1 >= argc) {
        throw std::invalid_argument(std::string(argument) + " requires a value");
      }
      if (argument == "--pes") {
        pe_count = parse_pe_count(argv[++index]);
      } else if (argument == "--values") {
        values = parse_values(argv[++index]);
      } else {
        throw std::invalid_argument("unknown argument: " + std::string(argument));
      }
    }

    const meshscan::SimulationResult result =
        meshscan::simulate_linear_mesh(values, pe_count);
    if (result.output != meshscan::serial_inclusive_scan(values)) {
      throw std::runtime_error("logical PE model disagrees with serial oracle");
    }
    std::cout << meshscan::to_json(values, result) << '\n';
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "meshscan-reference: " << error.what() << '\n';
    print_usage(std::cerr);
    return 2;
  }
}
