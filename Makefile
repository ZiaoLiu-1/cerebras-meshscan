CXX ?= c++
PYTHON ?= python3
CPPFLAGS := -Iinclude
COMMON_FLAGS := -std=c++20 -Wall -Wextra -Wpedantic -Werror
CXXFLAGS ?= -O2
BUILD_DIR := build

REFERENCE_SOURCES := src/reference.cpp
CLI_SOURCES := app/meshscan_reference.cpp $(REFERENCE_SOURCES)
TEST_SOURCES := tests/reference_tests.cpp $(REFERENCE_SOURCES)
COMPACTION_SOURCES := src/compaction.cpp
COMPACTION_HEADER := include/meshscan/compaction.hpp

.PHONY: all reference compaction test test-compaction simulator simulator-compaction trace-scan trace-compaction tutorial viewer sanitize clean

all: reference compaction

reference: $(BUILD_DIR)/meshscan-reference

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(BUILD_DIR)/meshscan-reference: $(CLI_SOURCES) include/meshscan/reference.hpp | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(COMMON_FLAGS) $(CXXFLAGS) $(CLI_SOURCES) -o $@

$(BUILD_DIR)/reference-tests: $(TEST_SOURCES) include/meshscan/reference.hpp | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(COMMON_FLAGS) $(CXXFLAGS) $(TEST_SOURCES) -o $@

compaction: $(BUILD_DIR)/meshcompact-reference

$(BUILD_DIR)/meshcompact-reference: app/meshcompact_reference.cpp $(COMPACTION_SOURCES) $(COMPACTION_HEADER) | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(COMMON_FLAGS) $(CXXFLAGS) app/meshcompact_reference.cpp $(COMPACTION_SOURCES) -o $@

$(BUILD_DIR)/compaction-tests: tests/compaction_tests.cpp $(COMPACTION_SOURCES) $(COMPACTION_HEADER) | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(COMMON_FLAGS) $(CXXFLAGS) tests/compaction_tests.cpp $(COMPACTION_SOURCES) -o $@

test-compaction: $(BUILD_DIR)/meshcompact-reference $(BUILD_DIR)/compaction-tests
	./$(BUILD_DIR)/compaction-tests
	$(PYTHON) -B tests/test_compaction_cli.py --binary $(BUILD_DIR)/meshcompact-reference
	$(PYTHON) -B tests/test_compaction_contract.py
	$(PYTHON) -B tests/test_compaction_runner.py
	$(PYTHON) -B tests/test_compaction_tutorial.py --binary $(BUILD_DIR)/meshcompact-reference

test: $(BUILD_DIR)/reference-tests $(BUILD_DIR)/meshscan-reference test-compaction
	./$(BUILD_DIR)/reference-tests
	$(PYTHON) -B tests/test_cli.py --binary $(BUILD_DIR)/meshscan-reference
	$(PYTHON) -B tests/test_validation_cases.py --binary $(BUILD_DIR)/meshscan-reference
	$(PYTHON) -B tests/test_simulator_harness.py --binary $(BUILD_DIR)/meshscan-reference
	$(PYTHON) -B tests/test_scan_runner.py
	$(PYTHON) -B tests/test_tutorial.py
	$(PYTHON) -B tests/test_viewer.py --binary $(BUILD_DIR)/meshscan-reference
	@if command -v node >/dev/null 2>&1; then \
		node --check tutorial/app.js && node --check viewer/app.js; \
	else echo "SKIP: node unavailable; browser JavaScript syntax not checked"; fi

tutorial:
	$(PYTHON) -B -m http.server 8766 --bind 127.0.0.1 --directory tutorial

# Start the existing licensed SDK VM with `limactl start cs_sdk` first.
# SDK binaries, generated artifacts and run records remain outside the repo.
simulator: $(BUILD_DIR)/meshscan-reference
	$(PYTHON) -B tools/run_simulator.py --binary $(BUILD_DIR)/meshscan-reference

simulator-compaction: $(BUILD_DIR)/meshcompact-reference
	$(PYTHON) -B tools/run_compaction.py --binary $(BUILD_DIR)/meshcompact-reference

# One small real SDK launch, retaining private traces for the official GUI.
trace-scan: $(BUILD_DIR)/meshscan-reference
	$(PYTHON) -B tools/run_visualization.py --algorithm scan

trace-compaction: $(BUILD_DIR)/meshcompact-reference
	$(PYTHON) -B tools/run_visualization.py --algorithm compaction

viewer: $(BUILD_DIR)/meshscan-reference
	$(PYTHON) -B viewer/server.py --binary $(BUILD_DIR)/meshscan-reference

sanitize: | $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(COMMON_FLAGS) -O1 -g -fsanitize=address,undefined \
		-fno-omit-frame-pointer $(TEST_SOURCES) -o $(BUILD_DIR)/reference-tests-sanitize
	./$(BUILD_DIR)/reference-tests-sanitize
	$(CXX) $(CPPFLAGS) $(COMMON_FLAGS) -O1 -g -fsanitize=address,undefined \
		-fno-omit-frame-pointer tests/compaction_tests.cpp $(COMPACTION_SOURCES) -o $(BUILD_DIR)/compaction-tests-sanitize
	./$(BUILD_DIR)/compaction-tests-sanitize

clean:
	rm -rf $(BUILD_DIR)
