"use strict";

// This is a fixed teaching trace, not a second prefix-scan implementation.
const DEMO = Object.freeze({
  input: Object.freeze([3, -1, 4, 2, 5, -2, 1, 3]),
  pe0Input: Object.freeze([3, -1, 4, 2]),
  pe1Input: Object.freeze([5, -2, 1, 3]),
  pe0Local: Object.freeze([3, 2, 6, 8]),
  pe1Local: Object.freeze([5, 3, 4, 7]),
  pe0Global: Object.freeze([3, 2, 6, 8]),
  pe1Global: Object.freeze([13, 11, 12, 15]),
});

const PENDING = Object.freeze([null, null, null, null]);

const STEPS = Object.freeze([
  {
    time: "00:00",
    short: "目标",
    title: "先看我们要算什么",
    detail: "每个位置都保留“到我为止”的总和。最后应该得到 [3, 2, 6, 8, 13, 11, 12, 15]。",
    note: "先沿着输入从左到右读一遍；当前位置也包含在当前总和里。",
    pe0State: "idle",
    pe1State: "idle",
    pe0Status: "等待分块",
    pe1Status: "等待分块",
    pe0Input: PENDING,
    pe1Input: PENDING,
    pe0Local: PENDING,
    pe1Local: PENDING,
    pe0Global: PENDING,
    pe1Global: PENDING,
    pe0CarryOut: "—",
    pe1CarryIn: "—",
    pe1CarryOut: "—",
    routeState: "quiet",
    routeLabel: "eastbound route",
    routeValue: "—",
  },
  {
    time: "02:00",
    short: "分块",
    title: "先按连续位置切成两半",
    detail: "PE 0 拿 indices 0–3；PE 1 拿 indices 4–7。连续分块让每个 PE 都清楚自己负责哪一段。",
    note: "分块没有改变输入顺序：西边 PE 管较早的位置，东边 PE 管较晚的位置。",
    pe0State: "working",
    pe1State: "working",
    pe0Status: "持有前四个数",
    pe1Status: "持有后四个数",
    pe0Input: DEMO.pe0Input,
    pe1Input: DEMO.pe1Input,
    pe0Local: PENDING,
    pe1Local: PENDING,
    pe0Global: PENDING,
    pe1Global: PENDING,
    pe0CarryOut: "—",
    pe1CarryIn: "—",
    pe1CarryOut: "—",
    routeState: "quiet",
    routeLabel: "route reserved",
    routeValue: "—",
  },
  {
    time: "04:00",
    short: "局部 scan",
    title: "两个 PE 同时先算自己的局部前缀",
    detail: "PE 0 得到 [3, 2, 6, 8]；PE 1 得到 [5, 3, 4, 7]。PE 1 的这排数只从自己的 5 开始，还不是全局答案。",
    note: "并行发生在这里：两个 local scan 不必互相等待。等待发生在把 PE 1 的结果校正为 global prefix 时。",
    pe0State: "working",
    pe1State: "waiting",
    pe0Status: "local total = 8",
    pe1Status: "local ready · 等 carry",
    pe0Input: DEMO.pe0Input,
    pe1Input: DEMO.pe1Input,
    pe0Local: DEMO.pe0Local,
    pe1Local: DEMO.pe1Local,
    pe0Global: PENDING,
    pe1Global: PENDING,
    pe0CarryOut: "准备 8",
    pe1CarryIn: "等待",
    pe1CarryOut: "—",
    routeState: "ready",
    routeLabel: "PE 1 global wait",
    routeValue: "8 ready",
  },
  {
    time: "06:00",
    short: "传 carry",
    title: "PE 0 把自己的总和 8 向东发送",
    detail: "PE 0 前面没有别的 PE，所以它的 local prefix 已经是 global prefix。它的最后一个值 8，就是 PE 1 缺少的左侧总和。",
    note: "carry 不是额外猜出来的数字；它等于 PE 0 所负责全部输入的总和。通信方向是 west → east。",
    pe0State: "done",
    pe1State: "waiting",
    pe0Status: "global ready",
    pe1Status: "等待 global 校正",
    pe0Input: DEMO.pe0Input,
    pe1Input: DEMO.pe1Input,
    pe0Local: DEMO.pe0Local,
    pe1Local: DEMO.pe1Local,
    pe0Global: DEMO.pe0Global,
    pe1Global: PENDING,
    pe0CarryOut: "8",
    pe1CarryIn: "传输中",
    pe1CarryOut: "—",
    routeState: "moving",
    routeLabel: "carry moves east",
    routeValue: "8 →",
  },
  {
    time: "08:00",
    short: "变成全局",
    title: "PE 1 给每个 local prefix 都加上 8",
    detail: "[5, 3, 4, 7] + 8 变成 [13, 11, 12, 15]。现在 PE 1 的输出终于包含了左边和本地的全部历史。",
    note: "这就是 PE 1 必须等 carry 的原因：没有 8，它只能证明局部正确，不能证明 indices 4–7 的全局结果正确。",
    pe0State: "done",
    pe1State: "working",
    pe0Status: "global ready",
    pe1Status: "应用 carry = 8",
    pe0Input: DEMO.pe0Input,
    pe1Input: DEMO.pe1Input,
    pe0Local: DEMO.pe0Local,
    pe1Local: DEMO.pe1Local,
    pe0Global: DEMO.pe0Global,
    pe1Global: DEMO.pe1Global,
    pe0CarryOut: "8",
    pe1CarryIn: "8",
    pe1CarryOut: "15",
    routeState: "arrived",
    routeLabel: "carry applied",
    routeValue: "8",
  },
  {
    time: "10:00",
    short: "证明与瓶颈",
    title: "用 invariant 收尾，再看它哪里会慢",
    detail: "全部八个 global prefix 与串行答案逐项一致，最后 carry 15 等于全部输入之和。正确性成立，但 carry 的先后依赖仍然存在。",
    note: "扩展到更多 PE 时，每站都要等西边 carry。最东端的等待会随线性链增长；之后可以研究 tree-style scan 来缩短依赖深度。",
    pe0State: "done",
    pe1State: "done",
    pe0Status: "invariant holds",
    pe1Status: "invariant holds",
    pe0Input: DEMO.pe0Input,
    pe1Input: DEMO.pe1Input,
    pe0Local: DEMO.pe0Local,
    pe1Local: DEMO.pe1Local,
    pe0Global: DEMO.pe0Global,
    pe1Global: DEMO.pe1Global,
    pe0CarryOut: "8",
    pe1CarryIn: "8",
    pe1CarryOut: "15",
    routeState: "arrived",
    routeLabel: "verified carry",
    routeValue: "8",
  },
]);

const elements = {
  lessonReference: document.getElementById("lesson-reference"),
  tabs: Array.from(document.querySelectorAll('[role="tab"][data-step]')),
  tabList: document.getElementById("step-tabs"),
  panel: document.getElementById("stage-panel"),
  kicker: document.getElementById("stage-kicker"),
  title: document.getElementById("stage-title"),
  detail: document.getElementById("stage-detail"),
  note: document.querySelector("#bench-note p"),
  progressFill: document.getElementById("progress-fill"),
  inputRow: document.getElementById("input-row"),
  pe0: document.getElementById("pe-0"),
  pe1: document.getElementById("pe-1"),
  pe0Status: document.getElementById("pe-0-status"),
  pe1Status: document.getElementById("pe-1-status"),
  pe0Input: document.getElementById("pe-0-input"),
  pe1Input: document.getElementById("pe-1-input"),
  pe0Local: document.getElementById("pe-0-local"),
  pe1Local: document.getElementById("pe-1-local"),
  pe0Global: document.getElementById("pe-0-global"),
  pe1Global: document.getElementById("pe-1-global"),
  pe0CarryOut: document.getElementById("pe-0-carry-out"),
  pe1CarryIn: document.getElementById("pe-1-carry-in"),
  pe1CarryOut: document.getElementById("pe-1-carry-out"),
  carryRoute: document.getElementById("carry-route"),
  carryLabel: document.getElementById("carry-label"),
  carryValue: document.getElementById("carry-value"),
  proof: document.getElementById("proof-strip"),
  previous: document.getElementById("previous-step"),
  next: document.getElementById("next-step"),
  stepPosition: document.getElementById("step-position"),
  selfExplain: document.getElementById("self-explain"),
  referenceToggle: document.getElementById("reference-toggle"),
  answers: Array.from(document.querySelectorAll("#m0-form textarea")),
  answerProgress: document.getElementById("answer-progress"),
  copyButton: document.getElementById("copy-answers"),
  copyStatus: document.getElementById("copy-status"),
};

let activeStep = 0;
let referenceHidden = false;
let answeredCount = 0;

function renderCells(container, values, signal = false) {
  const fragment = document.createDocumentFragment();
  values.forEach((value, index) => {
    const cell = document.createElement("span");
    cell.className = "number-cell";
    cell.dataset.index = String(index);
    if (value === null) {
      cell.classList.add("number-cell--pending");
      cell.textContent = "·";
    } else {
      if (signal) cell.classList.add("number-cell--signal");
      cell.textContent = String(value);
    }
    fragment.append(cell);
  });
  container.replaceChildren(fragment);
  const spokenValues = values.map((value) => value ?? "待计算").join(", ");
  container.setAttribute("aria-label", spokenValues);
}

function renderStep(index, options = {}) {
  activeStep = Math.max(0, Math.min(STEPS.length - 1, index));
  const step = STEPS[activeStep];

  elements.kicker.textContent = `STEP ${activeStep + 1} / ${STEPS.length} · ${step.time}`;
  elements.title.textContent = step.title;
  elements.detail.textContent = step.detail;
  elements.note.textContent = step.note;
  elements.progressFill.style.transform = `scaleX(${(activeStep + 1) / STEPS.length})`;

  elements.pe0.dataset.state = step.pe0State;
  elements.pe1.dataset.state = step.pe1State;
  elements.pe0Status.textContent = step.pe0Status;
  elements.pe1Status.textContent = step.pe1Status;

  renderCells(elements.pe0Input, step.pe0Input);
  renderCells(elements.pe1Input, step.pe1Input);
  renderCells(elements.pe0Local, step.pe0Local);
  renderCells(elements.pe1Local, step.pe1Local);
  renderCells(elements.pe0Global, step.pe0Global, activeStep >= 4);
  renderCells(elements.pe1Global, step.pe1Global, activeStep >= 4);

  elements.pe0CarryOut.textContent = step.pe0CarryOut;
  elements.pe1CarryIn.textContent = step.pe1CarryIn;
  elements.pe1CarryOut.textContent = step.pe1CarryOut;
  elements.carryRoute.dataset.state = step.routeState;
  elements.carryLabel.textContent = step.routeLabel;
  elements.carryValue.textContent = step.routeValue;

  elements.tabs.forEach((tab, tabIndex) => {
    const selected = tabIndex === activeStep;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  elements.panel.setAttribute("aria-labelledby", `step-tab-${activeStep}`);

  elements.previous.disabled = activeStep === 0;
  elements.next.textContent = activeStep === STEPS.length - 1
    ? "开始写我的解释 ↓"
    : `下一步：${STEPS[activeStep + 1].short} →`;
  elements.stepPosition.textContent = `${activeStep + 1} / ${STEPS.length}`;
  elements.proof.hidden = activeStep !== STEPS.length - 1;

  if (options.focusTab) {
    elements.tabs[activeStep].focus();
  }
  if (options.focusTab || options.scrollTab) {
    elements.tabs[activeStep].scrollIntoView({ block: "nearest", inline: "center" });
  }
}

function showOrHideReference(shouldHide) {
  referenceHidden = shouldHide;
  elements.lessonReference.hidden = shouldHide;
  elements.referenceToggle.setAttribute("aria-pressed", String(shouldHide));
  elements.referenceToggle.textContent = shouldHide
    ? "回看教学实验"
    : "隐藏参考，开始自述";

  if (shouldHide) {
    elements.answers[0].focus();
  } else {
    document.getElementById("workbench").scrollIntoView({ block: "start" });
  }
}

function moveStep(delta) {
  renderStep(activeStep + delta, { scrollTab: true });
}

function beginSelfExplanation() {
  elements.selfExplain.scrollIntoView({ block: "start" });
  showOrHideReference(true);
}

function updateAnswerProgress() {
  const count = elements.answers.filter((field) => field.value.trim() !== "").length;
  if (count === answeredCount) return;
  answeredCount = count;
  elements.answerProgress.textContent = `${count} / 4 已写`;
  elements.copyButton.disabled = count !== elements.answers.length;
  elements.copyButton.textContent = count === elements.answers.length
    ? "复制我的四条解释"
    : "写完四格后复制我的解释";
  elements.copyStatus.textContent = "";
}

function buildSelfExplanation() {
  const labels = [
    "1. 两个 PE 的分块方式",
    "2. PE 1 等待 carry 的原因",
    "3. 完成后的正确性 invariant",
    "4. 线性 carry chain 的可能瓶颈",
  ];
  const lines = [
    "MeshScan self-explanation notes",
    "说明：以下是我的练习笔记，网页未自动验证理解。",
    "",
  ];
  elements.answers.forEach((field, index) => {
    lines.push(labels[index], field.value.trim(), "");
  });
  return lines.join("\n").trim();
}

async function writeToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const helper = document.createElement("textarea");
  helper.value = text;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.opacity = "0";
  document.body.append(helper);
  helper.select();
  const copied = document.execCommand("copy");
  helper.remove();
  if (!copied) throw new Error("copy command was unavailable");
}

async function copyAnswers() {
  updateAnswerProgress();
  if (answeredCount !== elements.answers.length) return;
  elements.copyButton.disabled = true;
  elements.copyButton.textContent = "正在复制…";
  try {
    await writeToClipboard(buildSelfExplanation());
    elements.copyStatus.textContent = "已复制你的四条解释，可以粘贴到笔记中保存。";
  } catch (_error) {
    elements.copyStatus.textContent = "浏览器没有允许自动复制。请直接选中四个文本框中的内容。";
  } finally {
    elements.copyButton.disabled = false;
    elements.copyButton.textContent = "再次复制我的四条解释";
  }
}

elements.tabs.forEach((tab) => {
  tab.addEventListener("click", () => renderStep(Number(tab.dataset.step)));
});

elements.tabList.addEventListener("keydown", (event) => {
  let nextIndex = activeStep;
  if (event.key === "ArrowLeft") nextIndex = Math.max(0, activeStep - 1);
  else if (event.key === "ArrowRight") nextIndex = Math.min(STEPS.length - 1, activeStep + 1);
  else if (event.key === "Home") nextIndex = 0;
  else if (event.key === "End") nextIndex = STEPS.length - 1;
  else return;

  event.preventDefault();
  renderStep(nextIndex, { focusTab: true });
});

elements.previous.addEventListener("click", () => moveStep(-1));
elements.next.addEventListener("click", () => {
  if (activeStep === STEPS.length - 1) beginSelfExplanation();
  else moveStep(1);
});

elements.referenceToggle.addEventListener("click", () => {
  showOrHideReference(!referenceHidden);
});

elements.answers.forEach((field) => {
  field.addEventListener("input", updateAnswerProgress);
  field.addEventListener("change", updateAnswerProgress);
});

elements.copyButton.addEventListener("click", () => {
  void copyAnswers();
});

renderCells(elements.inputRow, DEMO.input);
renderStep(0);
updateAnswerProgress();
