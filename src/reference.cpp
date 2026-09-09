#include "meshscan/reference.hpp"

#include <limits>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace meshscan {
namespace {

Value checked_add(Value left, Value right) {
  if ((right > 0 && left > std::numeric_limits<Value>::max() - right) ||
      (right < 0 && left < std::numeric_limits<Value>::min() - right)) {
    throw std::overflow_error("prefix sum exceeds signed 32-bit range");
  }
  return left + right;
}

void append_values(std::ostringstream& stream,
                   const std::vector<Value>& values) {
  stream << '[';
  for (std::size_t index = 0; index < values.size(); ++index) {
    if (index != 0) {
      stream << ',';
    }
    stream << values[index];
  }
  stream << ']';
}

}  // namespace

std::vector<Value> serial_inclusive_scan(const std::vector<Value>& values) {
  std::vector<Value> output;
  output.reserve(values.size());

  Value running = 0;
  for (const Value value : values) {
    running = checked_add(running, value);
    output.push_back(running);
  }
  return output;
}

SimulationResult simulate_linear_mesh(const std::vector<Value>& values,
                                      std::size_t pe_count) {
  if (pe_count == 0) {
    throw std::invalid_argument("pe_count must be at least one");
  }
  if (pe_count > kMaxReferencePeCount) {
    throw std::invalid_argument(
        "pe_count exceeds the CPU reference safety limit of 4096");
  }

  SimulationResult result;
  result.output.resize(values.size());
  result.traces.reserve(pe_count);

  const std::size_t base_size = values.size() / pe_count;
  const std::size_t extra = values.size() % pe_count;
  std::size_t begin = 0;
  Value carry = 0;

  for (std::size_t pe = 0; pe < pe_count; ++pe) {
    const std::size_t local_size = base_size + (pe < extra ? 1U : 0U);
    const std::size_t end = begin + local_size;

    PeTrace trace;
    trace.pe_index = pe;
    trace.begin = begin;
    trace.end = end;
    trace.incoming_carry = carry;
    trace.local_prefix.reserve(local_size);
    trace.output.reserve(local_size);

    Value local_running = 0;
    for (std::size_t index = begin; index < end; ++index) {
      local_running = checked_add(local_running, values[index]);
      trace.local_prefix.push_back(local_running);

      const Value global_value = checked_add(carry, local_running);
      trace.output.push_back(global_value);
      result.output[index] = global_value;
    }

    trace.local_total = local_running;
    carry = checked_add(carry, local_running);
    result.traces.push_back(std::move(trace));
    begin = end;
  }

  return result;
}

std::string to_json(const std::vector<Value>& input,
                    const SimulationResult& result) {
  std::ostringstream stream;
  stream << "{\"model\":\"cpu-logical-pe\",\"input\":";
  append_values(stream, input);
  stream << ",\"output\":";
  append_values(stream, result.output);
  stream << ",\"pes\":[";

  for (std::size_t index = 0; index < result.traces.size(); ++index) {
    if (index != 0) {
      stream << ',';
    }
    const PeTrace& trace = result.traces[index];
    stream << "{\"pe\":" << trace.pe_index << ",\"begin\":" << trace.begin
           << ",\"end\":" << trace.end
           << ",\"incoming_carry\":" << trace.incoming_carry
           << ",\"local_total\":" << trace.local_total
           << ",\"local_prefix\":";
    append_values(stream, trace.local_prefix);
    stream << ",\"output\":";
    append_values(stream, trace.output);
    stream << '}';
  }

  stream << "]}";
  return stream.str();
}

}  // namespace meshscan
