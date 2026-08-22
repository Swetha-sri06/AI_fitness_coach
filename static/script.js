// =========================================================
// FitMate AI — frontend logic (vanilla JS, no build step)
// =========================================================

const state = {
  selectedEquipment: new Set(),
  thread_id: null,
  lastData: null,
  logCompleted: true,
};

// ---------------------------------------------------------
// Theme (dark / light)
// The initial value is already applied by the inline script in
// <head> (before paint). This block just wires up the toggle and
// keeps localStorage in sync so the choice survives refreshes and
// navigation to other pages.
// ---------------------------------------------------------
const THEME_KEY = "fitmate-theme";

function getCurrentTheme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const toggle = document.getElementById("themeToggle");
  if (toggle) {
    const switchesTo = theme === "light" ? "dark" : "light";
    toggle.setAttribute("aria-pressed", theme === "light" ? "true" : "false");
    toggle.setAttribute("aria-label", `Switch to ${switchesTo} theme`);
  }
}

// Sync the toggle's aria state with whatever the anti-flash script
// already applied to <html> on load.
applyTheme(getCurrentTheme());

document.getElementById("themeToggle").addEventListener("click", () => {
  const next = getCurrentTheme() === "light" ? "dark" : "light";
  applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch (e) {
    // localStorage unavailable (e.g. private mode) — theme still
    // applies for this session, it just won't persist.
  }
});

// ---------------------------------------------------------
// Tabs (Coach / Progress Log)
// ---------------------------------------------------------
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => {
      b.classList.remove("active");
      b.setAttribute("aria-selected", "false");
    });
    btn.classList.add("active");
    btn.setAttribute("aria-selected", "true");

    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    document.getElementById(btn.dataset.view).classList.add("active");
  });
});

// ---------------------------------------------------------
// Preset chips
// ---------------------------------------------------------
const PRESETS = {
  loss: {
    goal: "weight loss",
    level: "beginner",
    days: 5,
    duration: "45 minutes",
    equipment: ["dumbbell"],
    message: "I want to lose weight. I'm a beginner and can train 45 minutes a day, 5 days a week.",
  },
  muscle: {
    goal: "muscle gain",
    level: "intermediate",
    days: 4,
    duration: "50 minutes",
    equipment: ["dumbbell", "barbell"],
    message: "I want to build muscle. I'm intermediate and train 4 days a week for about 50 minutes.",
  },
  home: {
    goal: "general fitness",
    level: "beginner",
    days: 3,
    duration: "30 minutes",
    equipment: ["none"],
    message: "I'm a complete beginner and want a home workout routine, no equipment, 3 days a week, 30 minutes each.",
  },
  none: {
    goal: "general fitness",
    level: "beginner",
    days: 4,
    duration: "25 minutes",
    equipment: ["none"],
    message: "I have no equipment at all. Give me an effective bodyweight-only routine.",
  },
};

document.querySelectorAll(".preset-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const preset = PRESETS[chip.dataset.preset];
    if (!preset) return;

    document.getElementById("fGoal").value = preset.goal;
    document.getElementById("fLevel").value = preset.level;
    document.getElementById("fDays").value = preset.days;
    document.getElementById("fDuration").value = preset.duration;
    document.getElementById("fMessage").value = preset.message;

    state.selectedEquipment = new Set(preset.equipment);
    document.querySelectorAll(".option-chip").forEach((c) => {
      c.classList.toggle("active", state.selectedEquipment.has(c.dataset.value));
    });
  });
});

// ---------------------------------------------------------
// Equipment multi-select chips
// ---------------------------------------------------------
document.querySelectorAll("#equipmentChips .option-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const value = chip.dataset.value;
    if (state.selectedEquipment.has(value)) {
      state.selectedEquipment.delete(value);
      chip.classList.remove("active");
    } else {
      state.selectedEquipment.add(value);
      chip.classList.add("active");
    }
  });
});

// ---------------------------------------------------------
// Fitness-plan renderer: markdown -> structured, styled HTML
// Never leaves raw markdown (tables, ---, **, <br>, etc.) on
// screen. Parses the AI's markdown into blocks, then applies a
// few fitness-specific layouts on top:
//   - "Fitness Profile Summary" -> a label/value grid, sourced
//     from the structured fitness_profile object when available
//   - "Weekly Workout Plan" -> one card per day (from a Day
//     table, or from "Day N" sub-headings)
//   - everything else -> clean headings/paragraphs/lists/tables
//   - inline "4x8-10" / "rest: 90 sec" style fragments -> badges
// ---------------------------------------------------------
function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Text passed in here is already HTML-escaped -- only markdown
// inline syntax (never raw HTML) is turned into tags.
function inlineFormat(text) {
  return text
    .replace(/`([^`]+?)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__(.+?)__/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/(?:^|\s)_(.+?)_(?=\s|$)/g, " <em>$1</em>");
}

// Pulls "4 x 8-10", "3 sets of 10 reps", "rest: 90 sec" style
// fragments out of exercise lines and turns them into badges.
function decorateExercise(text) {
  return text
    .replace(/(\d+)\s*sets?\s+of\s+(\d+(?:[-–]\d+)?)\s*reps?/gi, '<span class="badge badge-sets">$1 &times; $2</span>')
    .replace(/(\d+)\s*(?:sets?)?\s*[x×]\s*(\d+(?:[-–]\d+)?)(?:\s*reps?)?/gi, '<span class="badge badge-sets">$1 &times; $2</span>')
    .replace(/rest\s*[:\-]?\s*(\d+(?:[-–]\d+)?\s*(?:sec(?:onds)?|s|min(?:utes)?|m)\b)/gi, '<span class="badge badge-rest">Rest: $1</span>')
    .replace(/(\d+(?:[-–]\d+)?\s*(?:sec(?:onds)?|min(?:utes)?))\s*rest\b/gi, '<span class="badge badge-rest">Rest: $1</span>');
}

function splitTableRow(line) {
  let l = line.trim();
  if (l.startsWith("|")) l = l.slice(1);
  if (l.endsWith("|")) l = l.slice(0, -1);
  return l.split("|").map((c) => c.trim());
}

const TABLE_DIVIDER_RE = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;

// Splits escaped markdown text into a flat list of block objects.
function parseBlocks(escaped) {
  const lines = escaped.split(/\r?\n/);
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const trimmed = lines[i].trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    // Table: a "|...|" row followed by a "---|---" divider row.
    if (trimmed.includes("|") && TABLE_DIVIDER_RE.test((lines[i + 1] || "").trim())) {
      const header = splitTableRow(trimmed);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().includes("|")) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      blocks.push({ type: "table", header, rows });
      continue;
    }

    if (/^#{1,6}\s+/.test(trimmed)) {
      const level = Math.min(trimmed.match(/^#{1,6}/)[0].length, 4);
      blocks.push({ type: "h" + level, text: trimmed.replace(/^#{1,6}\s+/, "") });
      i += 1;
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push({ type: "hr" });
      i += 1;
      continue;
    }

    // Note: input is already HTML-escaped, so a markdown ">" arrives as "&gt;".
    if (/^&gt;\s?/.test(trimmed)) {
      const bq = [];
      while (i < lines.length && /^&gt;\s?/.test(lines[i].trim())) {
        bq.push(lines[i].trim().replace(/^&gt;\s?/, ""));
        i += 1;
      }
      blocks.push({ type: "blockquote", text: bq.join(" ") });
      continue;
    }

    if (/^```/.test(trimmed)) {
      i += 1;
      const code = [];
      while (i < lines.length && !/^```/.test(lines[i].trim())) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1;
      blocks.push({ type: "code", text: code.join("\n") });
      continue;
    }

    if (/^[-*+]\s+/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^[-*+]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*+]\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "ul", items });
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "ol", items });
      continue;
    }

    // Paragraph: gather until the next blank line or block start.
    const para = [trimmed];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^#{1,6}\s+/.test(lines[i].trim()) &&
      !/^[-*+]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim()) &&
      !/^&gt;\s?/.test(lines[i].trim()) &&
      !/^```/.test(lines[i].trim()) &&
      !lines[i].includes("|")
    ) {
      para.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ type: "p", text: para.join(" ") });
  }

  return blocks;
}

// Groups a flat block list into sections split on H1/H2 headings.
function groupSections(blocks) {
  const sections = [];
  let current = { title: null, blocks: [] };
  blocks.forEach((b) => {
    if (b.type === "h1" || b.type === "h2") {
      if (current.title !== null || current.blocks.length) sections.push(current);
      current = { title: b.text, blocks: [] };
    } else {
      current.blocks.push(b);
    }
  });
  if (current.title !== null || current.blocks.length) sections.push(current);
  return sections;
}

const SECTION_ICONS = [
  [/fitness profile/i, "🧭"],
  [/workout/i, "🏋️"],
  [/equipment/i, "🧰"],
  [/nutrition/i, "🥗"],
  [/recovery/i, "🛌"],
  [/progress|motivation/i, "📈"],
  [/safety/i, "⚠️"],
];
function iconForTitle(title) {
  const hit = SECTION_ICONS.find(([re]) => re.test(title || ""));
  return hit ? hit[1] : "📋";
}

function renderTableGeneric(table) {
  const thead = `<thead><tr>${table.header.map((h) => `<th>${inlineFormat(h)}</th>`).join("")}</tr></thead>`;
  const tbody = `<tbody>${table.rows
    .map((r) => `<tr>${r.map((c) => `<td>${inlineFormat(decorateExercise(c))}</td>`).join("")}</tr>`)
    .join("")}</tbody>`;
  return `<div class="md-table-wrap"><table class="md-table">${thead}${tbody}</table></div>`;
}

function renderBlockGeneric(b) {
  switch (b.type) {
    case "h1":
    case "h2":
      return `<h2>${inlineFormat(b.text)}</h2>`;
    case "h3":
      return `<h3>${inlineFormat(b.text)}</h3>`;
    case "h4":
      return `<h4>${inlineFormat(b.text)}</h4>`;
    case "p": {
      // A paragraph that is ENTIRELY "**Label**" (a bold-only line, e.g.
      // "**Strength**" / "**Cardio**" / "**Notes**") is really a sub-heading
      // the LLM didn't format with "###" -- render it as one, with an icon.
      const soloLabel = b.text.trim().match(/^\*\*([^*]+)\*\*$/);
      if (soloLabel) {
        return `<h4>${fieldIcon(soloLabel[1])}${inlineFormat(soloLabel[1])}</h4>`;
      }
      return `<p>${inlineFormat(decorateExercise(b.text))}</p>`;
    }
    case "ul":
      return `<ul>${b.items.map((it) => `<li>${inlineFormat(decorateExercise(it))}</li>`).join("")}</ul>`;
    case "ol":
      return `<ol>${b.items.map((it) => `<li>${inlineFormat(decorateExercise(it))}</li>`).join("")}</ol>`;
    case "blockquote":
      return `<blockquote>${inlineFormat(b.text)}</blockquote>`;
    case "hr":
      return "<hr>";
    case "code":
      return `<pre><code>${b.text}</code></pre>`;
    case "table":
      return renderTableGeneric(b);
    default:
      return "";
  }
}

function isDayTable(table) {
  return table.header.length > 1 && /day/i.test(table.header[0]);
}

function fieldIcon(label) {
  if (/strength|exercise/i.test(label)) return "🏋️ ";
  if (/cardio/i.test(label)) return "❤️ ";
  if (/note/i.test(label)) return "📝 ";
  if (/focus/i.test(label)) return "🎯 ";
  return "";
}

function renderDayCardsFromTable(table) {
  const cards = table.rows
    .map((row) => {
      const dayLabel = row[0] || "Day";
      const fields = table.header
        .slice(1)
        .map((h, idx) => ({ label: h, value: row[idx + 1] || "" }))
        .filter((f) => f.value && f.value !== "-");
      return `
        <div class="day-card">
          <div class="day-card-head">${inlineFormat(dayLabel)}</div>
          <div class="day-card-body">
            ${fields
              .map(
                (f) => `
              <div class="day-field">
                <span class="day-field-label">${fieldIcon(f.label)}${inlineFormat(f.label)}</span>
                <div class="day-field-value">${inlineFormat(decorateExercise(f.value))}</div>
              </div>`
              )
              .join("")}
          </div>
        </div>`;
    })
    .join("");
  return `<div class="day-card-grid">${cards}</div>`;
}

// Used when the Weekly Workout Plan has no table, but uses "### Day 1"
// style sub-headings instead -- groups everything under each Day
// heading into its own card.
function renderDayCardsFromHeadings(blocks) {
  const groups = [];
  let preamble = [];
  let current = null;

  blocks.forEach((b) => {
    const isDayHeading = (b.type === "h2" || b.type === "h3" || b.type === "h4") && /^day\s*\d+/i.test(b.text.trim());
    if (isDayHeading) {
      if (current) groups.push(current);
      current = { title: b.text, blocks: [] };
    } else if (current) {
      current.blocks.push(b);
    } else {
      preamble.push(b);
    }
  });
  if (current) groups.push(current);

  const preHtml = preamble.map(renderBlockGeneric).join("");
  const cards = groups
    .map(
      (g) => `
    <div class="day-card">
      <div class="day-card-head">${inlineFormat(g.title)}</div>
      <div class="day-card-body day-card-freeform">${g.blocks.map(renderBlockGeneric).join("")}</div>
    </div>`
    )
    .join("");
  return preHtml + (cards ? `<div class="day-card-grid">${cards}</div>` : "");
}

function renderWorkoutBlocks(blocks) {
  const table = blocks.find((b) => b.type === "table");
  if (table && isDayTable(table)) {
    const rest = blocks.filter((b) => b !== table).map(renderBlockGeneric).join("");
    return rest + renderDayCardsFromTable(table);
  }
  const hasDayHeadings = blocks.some(
    (b) => (b.type === "h2" || b.type === "h3" || b.type === "h4") && /^day\s*\d+/i.test(b.text.trim())
  );
  if (hasDayHeadings) return renderDayCardsFromHeadings(blocks);
  return blocks.map(renderBlockGeneric).join("");
}

// Renders the structured fitness_profile object (from the API
// response) as a clean grid -- used instead of whatever raw table
// or prose the LLM wrote under "Fitness Profile Summary", so this
// section is always exact and never a wall of markdown.
function renderProfileGrid(profile) {
  if (!profile) return null;
  const FIELDS = [
    ["goal", "Goal"],
    ["fitness_level", "Fitness Level"],
    ["days_per_week", "Days / Week"],
    ["session_duration", "Session Duration"],
    ["age", "Age"],
    ["equipment", "Equipment"],
    ["preferences", "Training Preference"],
    ["limitations", "Limitations"],
  ];
  const items = FIELDS.map(([key, label]) => {
    let value = profile[key];
    if (Array.isArray(value)) value = value.filter(Boolean).join(", ");
    value = value == null ? "" : String(value).trim();
    return { label, value };
  }).filter((it) => it.value);

  if (!items.length) return null;

  return `<div class="profile-grid">${items
    .map(
      (it) => `
    <div class="profile-item">
      <span class="profile-label">${escapeHtml(it.label)}</span>
      <span class="profile-value">${inlineFormat(escapeHtml(it.value))}</span>
    </div>`
    )
    .join("")}</div>`;
}

// Main entry point: markdown (or plain text) -> polished HTML.
// opts.profile, when passed, is the structured fitness_profile
// object used to render the Fitness Profile Summary grid.
function renderFitnessContent(raw, opts) {
  opts = opts || {};
  if (!raw) return '<p class="empty-note">Nothing generated yet.</p>';

  // The LLM occasionally writes a literal "<br>" instead of a real line
  // break -- turn it into one before escaping so it never shows up as
  // visible text.
  const normalized = String(raw).replace(/<br\s*\/?>/gi, "\n");
  const escaped = escapeHtml(normalized);
  const blocks = parseBlocks(escaped);
  const sections = groupSections(blocks);
  const hasHeadings = sections.some((s) => s.title);

  if (!hasHeadings) {
    return renderWorkoutBlocks(blocks);
  }

  return sections
    .map((sec) => {
      if (!sec.title) {
        return sec.blocks.map(renderBlockGeneric).join("");
      }

      let body;
      if (/fitness profile/i.test(sec.title)) {
        body = renderProfileGrid(opts.profile) || sec.blocks.map(renderBlockGeneric).join("");
      } else if (/workout plan/i.test(sec.title)) {
        body = renderWorkoutBlocks(sec.blocks);
      } else {
        body = sec.blocks.map(renderBlockGeneric).join("");
      }

      return `
      <section class="content-section">
        <div class="content-section-head">
          <span class="content-section-icon">${iconForTitle(sec.title)}</span>
          <h2>${inlineFormat(sec.title)}</h2>
        </div>
        <div class="content-section-body">${body}</div>
      </section>`;
    })
    .join("");
}

// ---------------------------------------------------------
// Toast
// ---------------------------------------------------------
function showToast(message, isError) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.toggle("error", !!isError);
  toast.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toast.classList.add("hidden"), 4200);
}

// ---------------------------------------------------------
// Pipeline animation
// ---------------------------------------------------------
const AGENT_LABELS = {
  supervisor: "Supervisor",
  workout_agent: "Workout",
  equipment_agent: "Equipment",
  nutrition_agent: "Nutrition",
  progress_agent: "Progress",
  fitness_plan_agent: "Plan",
};

function resetPipeline() {
  document.querySelectorAll(".stage").forEach((el) => {
    el.classList.remove("active", "done", "skipped");
    el.classList.add("pending");
  });
  document.getElementById("barbellBar").innerHTML = "";
}

function animatePipeline(selectedAgents, reasoning) {
  return new Promise((resolve) => {
    resetPipeline();
    document.getElementById("supervisorReasoning").textContent = reasoning || "";

    const order = ["supervisor", "workout_agent", "equipment_agent", "nutrition_agent", "progress_agent", "fitness_plan_agent"];
    const included = new Set(["supervisor", "fitness_plan_agent", ...selectedAgents]);
    const bar = document.getElementById("barbellBar");

    let i = 0;
    function step() {
      if (i > 0) {
        const prevEl = document.querySelector(`.stage[data-agent="${order[i - 1]}"]`);
        if (prevEl) {
          prevEl.classList.remove("active");
          prevEl.classList.add(included.has(order[i - 1]) ? "done" : "skipped");
          if (included.has(order[i - 1])) {
            const plate = document.createElement("div");
            plate.className = "plate";
            bar.appendChild(plate);
          }
        }
      }
      if (i >= order.length) {
        resolve();
        return;
      }
      const agent = order[i];
      const el = document.querySelector(`.stage[data-agent="${agent}"]`);
      if (el) {
        el.classList.remove("pending", "skipped");
        el.classList.add(included.has(agent) ? "active" : "skipped");
      }
      i += 1;
      setTimeout(step, included.has(agent) ? 480 : 180);
    }
    step();
  });
}

// ---------------------------------------------------------
// Result rendering
// ---------------------------------------------------------
function renderResults(data) {
  const opts = { profile: data.fitness_profile };
  document.getElementById("panel-plan").innerHTML = renderFitnessContent(data.fitness_plan || data.answer, opts);
  document.getElementById("panel-workout").innerHTML = renderFitnessContent(data.workout_results, opts);
  document.getElementById("panel-equipment").innerHTML = renderFitnessContent(data.equipment_results, opts);
  document.getElementById("panel-nutrition").innerHTML = renderFitnessContent(data.nutrition_results, opts);
  document.getElementById("panel-progress").innerHTML = renderFitnessContent(data.progress_results, opts);
  document.getElementById("resultsSection").classList.remove("hidden");
}

document.querySelectorAll(".result-tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".result-tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".result-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.panel).classList.add("active");
  });
});

// ---------------------------------------------------------
// Main intake form submit
// ---------------------------------------------------------
function buildMessage() {
  const manual = document.getElementById("fMessage").value.trim();
  const parts = [];

  const goal = document.getElementById("fGoal").value;
  const level = document.getElementById("fLevel").value;
  const days = document.getElementById("fDays").value;
  const duration = document.getElementById("fDuration").value.trim();
  const age = document.getElementById("fAge").value.trim();

  if (goal) parts.push(`Goal: ${goal}.`);
  if (level) parts.push(`Fitness level: ${level}.`);
  if (days) parts.push(`Available ${days} days per week.`);
  if (duration) parts.push(`Session length: ${duration}.`);
  if (age) parts.push(`Age: ${age}.`);
  if (state.selectedEquipment.size) parts.push(`Equipment available: ${[...state.selectedEquipment].join(", ")}.`);
  if (manual) parts.push(manual);

  return parts.length ? parts.join(" ") : manual || "Create a personalized fitness plan for me.";
}

function setSubmitting(isSubmitting) {
  document.getElementById("submitBtn").disabled = isSubmitting;
  document.getElementById("submitLabel").textContent = isSubmitting ? "Coaching in progress…" : "Generate my plan";
  document.getElementById("submitSpinner").classList.toggle("hidden", !isSubmitting);
}

async function handleFitnessResponse(data) {
  if (!data.guardrail_allowed) {
    document.getElementById("guardrailReason").textContent = data.guardrail_reason || data.answer;
    document.getElementById("guardrailBanner").classList.remove("hidden");
    document.getElementById("pipelineSection").classList.add("hidden");
    return;
  }
  document.getElementById("guardrailBanner").classList.add("hidden");

  document.getElementById("pipelineSection").classList.remove("hidden");
  await animatePipeline(data.selected_agents || [], data.supervisor_reasoning || "");

  renderResults(data);
  state.thread_id = data.thread_id;
  state.lastData = data;

  if (data.requires_approval) {
    document.getElementById("approvalRequestText").textContent = data.approval_request || "";
    document.getElementById("hitlSection").classList.remove("hidden");
    document.getElementById("finalSection").classList.add("hidden");
    document.getElementById("hitlSection").scrollIntoView({ behavior: "smooth", block: "start" });
  } else {
    document.getElementById("hitlSection").classList.add("hidden");
    document.getElementById("finalContent").innerHTML = renderFitnessContent(data.answer, { profile: data.fitness_profile });
    document.getElementById("finalSection").classList.remove("hidden");
    document.getElementById("finalSection").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

document.getElementById("intakeForm").addEventListener("submit", async (e) => {
  e.preventDefault();

  const message = buildMessage();
  const userId = document.getElementById("fUserId").value.trim();

  document.getElementById("resultsSection").classList.add("hidden");
  document.getElementById("hitlSection").classList.add("hidden");
  document.getElementById("finalSection").classList.add("hidden");
  document.getElementById("guardrailBanner").classList.add("hidden");
  state.thread_id = null;

  setSubmitting(true);
  try {
    const res = await fetch("/api/fitness", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, user_id: userId }),
    });
    const data = await res.json();

    if (!data.success) {
      showToast(data.error || "Something went wrong.", true);
      document.getElementById("pipelineSection").classList.add("hidden");
      return;
    }
    await handleFitnessResponse(data);
  } catch (err) {
    showToast("Network error — is the server running?", true);
  } finally {
    setSubmitting(false);
  }
});

// ---------------------------------------------------------
// HITL: approve / revise
// ---------------------------------------------------------
document.getElementById("approveBtn").addEventListener("click", async () => {
  if (!state.thread_id) return;
  await submitApproval(true, "");
});

document.getElementById("reviseBtn").addEventListener("click", async () => {
  if (!state.thread_id) return;
  const feedback = document.getElementById("feedbackText").value.trim();
  if (!feedback) {
    showToast("Add a note about what to change first.", true);
    return;
  }
  await submitApproval(false, feedback);
});

async function submitApproval(approved, feedback) {
  const approveBtn = document.getElementById("approveBtn");
  const reviseBtn = document.getElementById("reviseBtn");
  approveBtn.disabled = true;
  reviseBtn.disabled = true;

  try {
    const res = await fetch("/api/fitness/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: state.thread_id, approved, feedback }),
    });
    const data = await res.json();

    if (!data.success) {
      showToast(data.error || "Could not process your review.", true);
      return;
    }

    renderResults(data);

    if (data.requires_approval) {
      document.getElementById("approvalRequestText").textContent = data.approval_request || "";
      document.getElementById("feedbackText").value = "";
    } else {
      document.getElementById("hitlSection").classList.add("hidden");
      document.getElementById("finalContent").innerHTML = renderFitnessContent(data.answer, { profile: data.fitness_profile });
      document.getElementById("finalSection").classList.remove("hidden");
      document.getElementById("finalSection").scrollIntoView({ behavior: "smooth", block: "start" });
      showToast("Final plan ready.");
    }
  } catch (err) {
    showToast("Network error while submitting your review.", true);
  } finally {
    approveBtn.disabled = false;
    reviseBtn.disabled = false;
  }
}

// ---------------------------------------------------------
// Progress Log view
// ---------------------------------------------------------
const today = new Date().toISOString().slice(0, 10);
document.getElementById("lDate").value = today;

document.querySelectorAll(".toggle-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    document.querySelectorAll(".toggle-chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    state.logCompleted = chip.dataset.value === "yes";
  });
});

document.getElementById("lDifficulty").addEventListener("input", (e) => {
  document.getElementById("difficultyValue").textContent = e.target.value;
});
document.getElementById("lEnergy").addEventListener("input", (e) => {
  document.getElementById("energyValue").textContent = e.target.value;
});

document.getElementById("logForm").addEventListener("submit", async (e) => {
  e.preventDefault();

  const userId = document.getElementById("lUserId").value.trim();
  if (!userId) {
    showToast("Enter a progress tracking ID first.", true);
    return;
  }

  const payload = {
    user_id: userId,
    session_date: document.getElementById("lDate").value,
    workout_type: document.getElementById("lType").value.trim(),
    duration_minutes: parseInt(document.getElementById("lDuration").value, 10) || null,
    completed: state.logCompleted,
    difficulty_rating: parseInt(document.getElementById("lDifficulty").value, 10),
    energy_level: parseInt(document.getElementById("lEnergy").value, 10),
    notes: document.getElementById("lNotes").value.trim(),
  };

  try {
    const res = await fetch("/api/progress/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (!data.success) {
      showToast(data.error || "Could not log this session.", true);
      return;
    }

    showToast("Workout logged.");
    document.getElementById("pUserId").value = userId;
    renderSummary(data.summary);
    document.getElementById("lNotes").value = "";
  } catch (err) {
    showToast("Network error while logging your workout.", true);
  }
});

document.getElementById("lookupBtn").addEventListener("click", async () => {
  const userId = document.getElementById("pUserId").value.trim();
  if (!userId) {
    showToast("Enter a progress tracking ID first.", true);
    return;
  }
  try {
    const res = await fetch(`/api/progress/${encodeURIComponent(userId)}`);
    const data = await res.json();
    if (!data.success) {
      showToast(data.error || "Could not fetch progress.", true);
      return;
    }
    renderSummary(data.summary);
  } catch (err) {
    showToast("Network error while fetching progress.", true);
  }
});

function renderSummary(summary) {
  if (!summary || !summary.total_sessions_logged) {
    document.getElementById("summaryEmpty").classList.remove("hidden");
    document.getElementById("summaryContent").classList.add("hidden");
    return;
  }

  document.getElementById("summaryEmpty").classList.add("hidden");
  document.getElementById("summaryContent").classList.remove("hidden");

  const rate = summary.completion_rate_percent || 0;
  const circumference = 2 * Math.PI * 52;
  const offset = circumference - (rate / 100) * circumference;
  const ring = document.getElementById("ringFill");
  ring.style.strokeDasharray = `${circumference}`;
  ring.style.strokeDashoffset = `${offset}`;
  document.getElementById("ringNumber").textContent = `${rate}%`;

  document.getElementById("statSessions").textContent = summary.total_sessions_logged;
  document.getElementById("statDifficulty").textContent = summary.average_difficulty ?? "—";
  document.getElementById("statEnergy").textContent = summary.average_energy ?? "—";

  const list = document.getElementById("recentList");
  list.innerHTML = "";
  (summary.recent_sessions || []).forEach((s) => {
    const li = document.createElement("li");
    li.className = "recent-item";
    li.innerHTML = `
      <span class="r-date">${escapeHtml(s.session_date)}</span>
      <span class="r-type">${escapeHtml(s.workout_type || "Workout")}</span>
      <span class="r-status ${s.completed ? "done" : "missed"}">${s.completed ? "Completed" : "Missed"}</span>
    `;
    list.appendChild(li);
  });
}
