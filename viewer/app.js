"use strict";

const INT32_MIN = -(2 ** 31);
const INT32_MAX = 2 ** 31 - 1;
const MAX_VALUES = 256;
const MAX_VISIBLE_PES = 16;

const elements = {
  form: document.getElementById("trace-form"),
  values: document.getElementById("values-input"),
  peCount: document.getElementById("pe-input"),
  run: document.getElementById("run-button"),
  error: document.getElementById("form-error"),
  previous: document.getElementById("previous-button"),
  next: document.getElementById("next-button"),
  stepCount: document.getElementById("step-count"),
  phaseTitle: document.getElementById("phase-title"),
  track: document.getElementById("fabric-track"),
  viewport: document.getElementById("fabric-viewport"),
  detail: document.getElementById("step-detail"),
  finalOutput: document.getElementById("final-output"),
};

const state = {
  trace: null,
  steps: [],
  index: 0,
};

function parseValues(raw) {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return [];
  }

  const pieces = trimmed.split(",");
  if (pieces.length > MAX_VALUES) {
    throw new Error(`Values 最多 ${MAX_VALUES} 个；请缩短输入后再生成 trace。`);
  }

  return pieces.map((piece, index) => {
    const token = piece.trim();
    if (!/^-?\d+$/.test(token)) {
      throw new Error(`第 ${index + 1} 个 value 需要是整数，例如 -4。`);
    }
    const value = Number(token);
    if (!Number.isSafeInteger(value) || value < INT32_MIN || value > INT32_MAX) {
      throw new Error(`第 ${index + 1} 个 value 超出 int32 范围。`);
    }
    return value;
  });
}

function parsePeCount(raw) {
  if (!/^\d+$/.test(raw.trim())) {
    throw new Error("逻辑 PE 需要是 1 到 16 的整数。");
  }
  const count = Number(raw);
  if (!Number.isInteger(count) || count < 1 || count > MAX_VISIBLE_PES) {
    throw new Error("逻辑 PE 需要在 1 到 16 之间。");
  }
  return count;
}

function buildSteps(trace) {
  if (trace.input.length === 0) {
    return [
      {
        kind: "partition",
        title: "空输入：没有需要分配的元素",
        detail: "CPU model 返回空结果。未来 WSE host 会走 empty-input fast path，不启动 device copy。",
      },
      {
        kind: "verify",
        title: "验证空结果",
        detail: "C++ serial oracle 与 logical-PE model 都返回空数组。",
      },
    ];
  }

  const steps = [
    {
      kind: "partition",
      title: "把输入切成连续分块",
      detail: "每个 PE 只看到自己的有效范围；较早的 PE 在不能整除时多拿一个元素。",
    },
    {
      kind: "local",
      title: "各 PE 完成局部 inclusive scan",
      detail: "这些 local prefix 来自 C++ trace，可并行形成；此时还没有加入西侧 carry。",
    },
  ];

  trace.pes.forEach((pe, index) => {
    const outgoing = pe.output.length > 0
      ? pe.output[pe.output.length - 1]
      : pe.incoming_carry;
    const empty = pe.begin === pe.end;
    const next = index + 1 < trace.pes.length ? `，再把 ${outgoing} 传给 PE ${index + 1}` : "";
    steps.push({
      kind: "carry",
      peIndex: index,
      title: empty ? `PE ${index} 转发 carry` : `PE ${index} 应用 carry`,
      detail: empty
        ? `PE ${index} 没有有效元素，所以输出仍为空，并把 carry ${outgoing} 原样向东转发${next}。`
        : `PE ${index} 收到 ${pe.incoming_carry}，得到 global prefix [${pe.output.join(", ")}]${next}。`,
    });
  });

  steps.push({
    kind: "verify",
    title: "与 C++ serial oracle 完整对照",
    detail: `全部 ${trace.output.length} 个 global prefix 已逐项通过 CPU reference 自检。`,
  });
  return steps;
}

function makeTextElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function renderCells(values, visible) {
  const cells = document.createElement("div");
  cells.className = "cells";
  if (!visible) {
    cells.append(makeTextElement("span", "cell cell--muted", "···"));
    return cells;
  }
  if (values.length === 0) {
    cells.append(makeTextElement("span", "cell cell--muted", "∅"));
    return cells;
  }
  values.forEach((value) => {
    cells.append(makeTextElement("span", "cell", String(value)));
  });
  return cells;
}

function renderValueRow(label, values, visible) {
  const row = document.createElement("div");
  row.className = "value-row";
  row.append(makeTextElement("span", "value-row__label", label));
  row.append(renderCells(values, visible));
  return row;
}

function peVisualState(step, peIndex) {
  if (step.kind === "carry") {
    if (peIndex < step.peIndex) return "done";
    if (peIndex === step.peIndex) return "active";
  }
  if (step.kind === "verify") return "done";
  return "waiting";
}

function peStateLabel(step, peIndex) {
  const visualState = peVisualState(step, peIndex);
  if (visualState === "active") return "ACTIVE";
  if (visualState === "done") return "DONE";
  if (step.kind === "local") return "LOCAL READY";
  return "WAITING";
}

function outputIsVisible(step, peIndex) {
  return step.kind === "verify" || (step.kind === "carry" && peIndex <= step.peIndex);
}

function localIsVisible(step) {
  return step.kind !== "partition";
}

function renderPe(trace, pe, peIndex, step) {
  const section = document.createElement("section");
  section.className = "pe";
  section.dataset.state = peVisualState(step, peIndex);
  section.dataset.pe = String(peIndex);
  section.setAttribute("aria-label", `PE ${peIndex}, input index ${pe.begin} to ${pe.end}`);

  const head = document.createElement("div");
  head.className = "pe__head";
  head.append(makeTextElement("span", "", `PE ${String(peIndex).padStart(2, "0")}`));
  head.append(makeTextElement("span", "pe__state", peStateLabel(step, peIndex)));

  const body = document.createElement("div");
  body.className = "pe__body";
  body.append(renderValueRow(`input [${pe.begin}:${pe.end})`, trace.input.slice(pe.begin, pe.end), true));
  body.append(renderValueRow("local prefix", pe.local_prefix, localIsVisible(step)));
  body.append(renderValueRow("global prefix", pe.output, outputIsVisible(step, peIndex)));

  const outgoing = pe.output.length > 0
    ? pe.output[pe.output.length - 1]
    : pe.incoming_carry;
  const carry = document.createElement("div");
  carry.className = "pe__carry";
  carry.append(makeTextElement("span", "", `IN ${pe.incoming_carry}`));
  carry.append(makeTextElement("span", "", "→"));
  carry.append(makeTextElement("span", "", `OUT ${outgoing}`));

  section.append(head, body, carry);
  return section;
}

function renderCarryLink(pe, peIndex, step) {
  const link = document.createElement("div");
  link.className = "carry-link";
  const active = step.kind === "carry" && step.peIndex === peIndex;
  link.dataset.state = active ? "active" : "waiting";
  link.setAttribute("aria-hidden", "true");
  const outgoing = pe.output.length > 0
    ? pe.output[pe.output.length - 1]
    : pe.incoming_carry;
  link.append(makeTextElement("span", "carry-link__value", active ? String(outgoing) : "carry"));
  return link;
}

function render() {
  if (!state.trace || state.steps.length === 0) return;
  const step = state.steps[state.index];
  const fragment = document.createDocumentFragment();

  state.trace.pes.forEach((pe, index) => {
    fragment.append(renderPe(state.trace, pe, index, step));
    if (index + 1 < state.trace.pes.length) {
      fragment.append(renderCarryLink(pe, index, step));
    }
  });
  elements.track.replaceChildren(fragment);

  elements.stepCount.textContent = `STEP ${state.index + 1} / ${state.steps.length}`;
  elements.phaseTitle.textContent = step.title;
  elements.detail.textContent = step.detail;
  elements.previous.disabled = state.index === 0;
  elements.next.disabled = state.index === state.steps.length - 1;
  elements.finalOutput.textContent = step.kind === "verify"
    ? `[${state.trace.output.join(", ")}] · VERIFIED`
    : "—";

  if (step.kind === "carry") {
    const activePe = elements.track.querySelector(`[data-pe="${step.peIndex}"]`);
    activePe?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  } else {
    elements.viewport.scrollTo({ left: 0, behavior: "smooth" });
  }
}

function showError(message) {
  elements.error.textContent = message;
  elements.values.setAttribute("aria-invalid", "true");
  elements.peCount.setAttribute("aria-invalid", "true");
}

function clearError() {
  elements.error.textContent = "";
  elements.values.removeAttribute("aria-invalid");
  elements.peCount.removeAttribute("aria-invalid");
}

async function requestTrace(values, peCount) {
  const response = await fetch("/api/trace", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values, pe_count: peCount }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "本地 model 没有返回可用 trace。请检查 server 终端。");
  }
  return payload.trace;
}

async function runModel() {
  clearError();
  let values;
  let peCount;
  try {
    values = parseValues(elements.values.value);
    peCount = parsePeCount(elements.peCount.value);
  } catch (error) {
    showError(error.message);
    return;
  }

  elements.run.disabled = true;
  elements.run.setAttribute("aria-busy", "true");
  elements.run.textContent = "正在读取 C++ trace…";
  try {
    state.trace = await requestTrace(values, peCount);
    state.steps = buildSteps(state.trace);
    state.index = 0;
    render();
  } catch (error) {
    showError(error.message);
  } finally {
    elements.run.disabled = false;
    elements.run.removeAttribute("aria-busy");
    elements.run.textContent = "生成 CPU trace";
  }
}

function moveStep(delta) {
  const nextIndex = Math.min(
    state.steps.length - 1,
    Math.max(0, state.index + delta),
  );
  if (nextIndex === state.index) return;
  state.index = nextIndex;
  render();
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  void runModel();
});
elements.previous.addEventListener("click", () => moveStep(-1));
elements.next.addEventListener("click", () => moveStep(1));

document.addEventListener("keydown", (event) => {
  const tag = event.target instanceof HTMLElement ? event.target.tagName : "";
  if (tag === "INPUT" || tag === "BUTTON") return;
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    moveStep(-1);
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    moveStep(1);
  } else if (event.key === "Home" && state.steps.length > 0) {
    event.preventDefault();
    state.index = 0;
    render();
  } else if (event.key === "End" && state.steps.length > 0) {
    event.preventDefault();
    state.index = state.steps.length - 1;
    render();
  }
});

void runModel();
