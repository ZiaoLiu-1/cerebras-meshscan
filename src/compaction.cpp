#include "meshscan/compaction.hpp"

#include <algorithm>
#include <sstream>
#include <stdexcept>
#include <utility>
#include <vector>

namespace meshscan::compaction {
namespace {

struct Record {
  Value value{};
  std::size_t index{};
  std::size_t rank{};
  std::size_t source{};
};

void require(bool condition, const char* message) {
  if (!condition) {
    throw std::logic_error(message);
  }
}

template <typename T>
void array_json(std::ostringstream& stream, const std::vector<T>& values) {
  stream << '[';
  for (std::size_t i = 0; i < values.size(); ++i) {
    if (i != 0) {
      stream << ',';
    }
    stream << values[i];
  }
  stream << ']';
}

}  // namespace

FilterResult serial_filter(const std::vector<Value>& values, Value threshold) {
  FilterResult result;
  for (std::size_t index = 0; index < values.size(); ++index) {
    if (values[index] >= threshold) {
      result.output.push_back(values[index]);
      result.indices.push_back(index);
    }
  }
  return result;
}

Result simulate(const std::vector<Value>& values, std::size_t width,
                std::size_t chunk_size, Value threshold) {
  if (width == 0 || width > kMaxPeCount) {
    throw std::invalid_argument("PE count must be in [1, 8]");
  }
  if (chunk_size == 0 || chunk_size > kMaxChunkSize) {
    throw std::invalid_argument("chunk size must be in [1, 64]");
  }
  if (values.size() > width * chunk_size) {
    throw std::invalid_argument("input exceeds width * chunk_size capacity");
  }

  Result result;
  result.width = width;
  result.chunk_size = chunk_size;
  result.threshold = threshold;
  result.pes.resize(width);
  std::vector<std::vector<Record>> local_records(width);
  const std::size_t base = values.size() / width;
  const std::size_t extra = values.size() % width;
  std::size_t start = 0;
  std::size_t prefix = 0;

  // Counts advance east. Each selected local item acquires one global rank.
  for (std::size_t pe = 0; pe < width; ++pe) {
    PeTrace& trace = result.pes[pe];
    trace.pe = pe;
    trace.input_start = start;
    trace.valid_count = base + (pe < extra ? 1 : 0);
    trace.offset = prefix;
    require(trace.valid_count <= chunk_size, "balanced chunk exceeds capacity");
    for (std::size_t slot = 0; slot < trace.valid_count; ++slot) {
      const std::size_t index = start + slot;
      if (values[index] >= threshold) {
        const std::size_t rank = prefix + local_records[pe].size();
        require(rank <= index, "stable selected rank exceeds input index");
        require(rank / chunk_size <= pe, "compaction requires an eastward move");
        local_records[pe].push_back({values[index], index, rank, pe});
      }
    }
    trace.local_count = local_records[pe].size();
    prefix += trace.local_count;
    trace.prefix_count = prefix;
    start += trace.valid_count;
  }
  require(start == values.size(), "balanced input partition has a gap");
  result.selected_count = prefix;

  // Consume the stream from the east after local records. Records are routed
  // hop by hop, not placed directly into a host-concatenated output vector.
  std::vector<Record> incoming;
  std::size_t total_received = 0;
  std::size_t total_forwarded = 0;
  for (std::size_t remaining = width; remaining > 0; --remaining) {
    const std::size_t pe = remaining - 1;
    PeTrace& trace = result.pes[pe];
    trace.received_count = incoming.size();
    total_received += trace.received_count;
    std::vector<Record> records = local_records[pe];
    records.insert(records.end(), incoming.begin(), incoming.end());
    std::vector<Record> outgoing;
    std::vector<Record> slots(chunk_size);
    std::vector<bool> occupied(chunk_size, false);
    for (std::size_t i = 0; i < records.size(); ++i) {
      const Record& record = records[i];
      if (i != 0) {
        require(records[i - 1].rank < record.rank, "packet rank order is unstable");
        require(records[i - 1].index < record.index, "packet index order is unstable");
      }
      require(record.source >= pe, "packet moved east");
      require(record.rank < result.selected_count, "packet rank exceeds selected count");
      require(record.index < values.size() && values[record.index] == record.value,
              "packet lost its input provenance");
      const std::size_t owner = record.rank / chunk_size;
      require(owner <= pe, "packet overshot its output owner");
      if (owner < pe) {
        outgoing.push_back(record);
      } else {
        const std::size_t slot = record.rank % chunk_size;
        require(!occupied[slot], "two packets target the same output slot");
        slots[slot] = record;
        occupied[slot] = true;
        ++trace.output_count;
      }
    }
    trace.forwarded_count = outgoing.size();
    total_forwarded += trace.forwarded_count;
    require(trace.local_count + trace.received_count ==
                trace.output_count + trace.forwarded_count,
            "record conservation failed at a PE");
    const std::size_t output_start = pe * chunk_size;
    const std::size_t expected_count = output_start >= result.selected_count
        ? 0 : std::min(chunk_size, result.selected_count - output_start);
    require(trace.output_count == expected_count, "output partition count is incorrect");
    for (std::size_t slot = 0; slot < chunk_size; ++slot) {
      require(occupied[slot] == (slot < expected_count), "packed output contains a hole");
      if (occupied[slot]) {
        trace.output_values.push_back(slots[slot].value);
        trace.output_indices.push_back(slots[slot].index);
      }
    }
    incoming = std::move(outgoing);
  }
  require(incoming.empty(), "records escaped the west edge");
  require(total_received == total_forwarded, "link record accounting is inconsistent");
  for (const PeTrace& trace : result.pes) {
    result.output.insert(result.output.end(), trace.output_values.begin(), trace.output_values.end());
    result.indices.insert(result.indices.end(), trace.output_indices.begin(), trace.output_indices.end());
  }
  const FilterResult serial = serial_filter(values, threshold);
  require(result.output == serial.output && result.indices == serial.indices,
          "distributed compaction disagrees with independent serial filter");
  require(result.output.size() == result.selected_count, "selected count is inconsistent");
  return result;
}

std::string to_json(const std::vector<Value>& input, const Result& result) {
  std::ostringstream stream;
  stream << "{\"schema\":\"meshcompact-oracle-v1\",\"width\":" << result.width
         << ",\"chunk_size\":" << result.chunk_size
         << ",\"threshold\":" << result.threshold << ",\"input\":";
  array_json(stream, input);
  stream << ",\"output\":";
  array_json(stream, result.output);
  stream << ",\"indices\":";
  array_json(stream, result.indices);
  stream << ",\"selected_count\":" << result.selected_count << ",\"pes\":[";
  for (std::size_t i = 0; i < result.pes.size(); ++i) {
    const PeTrace& trace = result.pes[i];
    if (i != 0) {
      stream << ',';
    }
    stream << "{\"pe\":" << trace.pe
           << ",\"input_start\":" << trace.input_start
           << ",\"valid_count\":" << trace.valid_count
           << ",\"local_count\":" << trace.local_count
           << ",\"offset\":" << trace.offset
           << ",\"prefix_count\":" << trace.prefix_count
           << ",\"output_count\":" << trace.output_count
           << ",\"received_count\":" << trace.received_count
           << ",\"forwarded_count\":" << trace.forwarded_count
           << ",\"output_values\":";
    array_json(stream, trace.output_values);
    stream << ",\"output_indices\":";
    array_json(stream, trace.output_indices);
    stream << '}';
  }
  stream << "]}";
  return stream.str();
}

}  // namespace meshscan::compaction
