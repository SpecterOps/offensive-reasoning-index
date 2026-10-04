import fs from "node:fs/promises";
import path from "node:path";

const artifactToolModule = process.env.ORI_ARTIFACT_TOOL_MODULE ?? "@oai/artifact-tool";
const { Presentation, PresentationFile } = await import(artifactToolModule);

const ROOT = path.resolve(import.meta.dirname, "..");
const OUTPUT = path.join(
  ROOT,
  ".tmp/offensive-ai-con-deck/ori-offensive-ai-con-working-deck.pptx",
);
const PREVIEW_DIR = path.join(ROOT, ".tmp/offensive-ai-con-deck");

const W = 1280;
const H = 720;
const M = 54;
const INK = "#111111";
const MUTED = "#62676F";
const RULE = "#B8BCC4";
const PANEL = "#F2F2F2";
const BLUE = "#3D8DFF";
const BLUE_LIGHT = "#D0EDFA";
const RED = "#C23B22";
const GREEN = "#2A7A52";
const FONT = "Helvetica Neue";

function addShape(slide, geometry, position, fill = "none", line = undefined) {
  return slide.shapes.add({
    geometry,
    position,
    fill,
    line: line ?? { style: "solid", fill: "none", width: 0 },
  });
}

function addText(
  slide,
  text,
  position,
  { size = 24, color = INK, bold = false, align = "left", valign = "top" } = {},
) {
  const box = addShape(slide, "textbox", position);
  box.text = text;
  box.text.style = {
    fontSize: size,
    typeface: FONT,
    color,
    bold,
    alignment: align,
    verticalAlignment: valign,
  };
  return box;
}

function addLine(slide, x1, y1, x2, y2, color = RULE, width = 2) {
  return addShape(
    slide,
    "straightConnector1",
    { left: x1, top: y1, width: x2 - x1, height: y2 - y1 },
    "none",
    { style: "solid", fill: color, width },
  );
}

function addCircle(slide, x, y, diameter, fill, lineColor = fill) {
  return addShape(
    slide,
    "ellipse",
    { left: x, top: y, width: diameter, height: diameter },
    fill,
    { style: "solid", fill: lineColor, width: 1 },
  );
}

function addSlideTitle(slide, title, index, eyebrow = "OFFENSIVE REASONING INDEX") {
  addText(slide, eyebrow, { left: M, top: 30, width: 520, height: 24 }, {
    size: 14,
    color: BLUE,
    bold: true,
  });
  addText(slide, title, { left: M, top: 70, width: 1150, height: 80 }, {
    size: 40,
    bold: true,
  });
  addLine(slide, M, 151, W - M, 151, RULE, 1);
  addText(slide, String(index).padStart(2, "0"), {
    left: W - M - 44,
    top: 674,
    width: 44,
    height: 22,
  }, { size: 13, color: MUTED, align: "right" });
}

function addNotes(slide, body, sources) {
  const sourceLines = sources.map((source) => `- ${source}`).join("\n");
  slide.speakerNotes.textFrame.setText(
    `${body}\n\n[Sources]\n${sourceLines}\n[/Sources]`,
  );
  slide.speakerNotes.setVisible(true);
}

function addArrow(slide, x1, y1, x2, y2, color = RULE, width = 2) {
  if (y2 < y1) {
    addLine(slide, x1, y2, x1, y1, color, width);
    const risingLine = addLine(slide, x1, y2, x2, y2, color, width);
    risingLine.line.endArrowType = "triangle";
    return risingLine;
  }
  const line = addLine(slide, x1, y1, x2, y2, color, width);
  line.line.endArrowType = "triangle";
  return line;
}

function addNode(slide, x, y, label, fill = "#FFFFFF", border = INK, width = 150) {
  addShape(slide, "ellipse", { left: x, top: y, width, height: 72 }, fill, {
    style: "solid",
    fill: border,
    width: 2,
  });
  addText(slide, label, { left: x + 10, top: y + 17, width: width - 20, height: 36 }, {
    size: 20,
    bold: true,
    align: "center",
    valign: "middle",
  });
}

function addPanelLabel(slide, label, x, y, width, color = BLUE) {
  addText(slide, label.toUpperCase(), { left: x, top: y, width, height: 24 }, {
    size: 14,
    color,
    bold: true,
  });
}

async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}

function buildDeck() {
  const deck = Presentation.create({ slideSize: { width: W, height: H } });

  // 1 — Minimal cover.
  {
    const slide = deck.slides.add();
    slide.background.fill = "#FFFFFF";
    addText(slide, "OFFENSIVE AI CON · OCTOBER 5, 2026", {
      left: M,
      top: 52,
      width: 620,
      height: 28,
    }, { size: 16, color: BLUE, bold: true });
    addText(slide, "Offensive\nReasoning Index", {
      left: M,
      top: 175,
      width: 810,
      height: 210,
    }, { size: 72, bold: true });
    addText(slide, "Building a graph-grounded benchmark for offensive AI", {
      left: M,
      top: 445,
      width: 820,
      height: 84,
    }, { size: 30, color: MUTED });
    addLine(slide, 950, 180, 950, 540, BLUE, 8);
    addText(slide, "Plausible\nis not\nproven.", {
      left: 985,
      top: 205,
      width: 230,
      height: 260,
    }, { size: 36, bold: true });
    addNotes(
      slide,
      "Open on the distinction between a fluent answer and a mechanically grounded answer. The accepted conference title and speaker line should replace the working title when organizers confirm them.",
      ["docs/offensive-ai-con-talk-outline.md"],
    );
  }

  // 2 — Graph reasoning laboratory.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "Attack paths expose whether reasoning is grounded", 2);
    addArrow(slide, 210, 320, 500, 250, INK, 3);
    addArrow(slide, 210, 340, 500, 430, INK, 3);
    addArrow(slide, 650, 250, 930, 330, INK, 3);
    addArrow(slide, 650, 430, 930, 350, RED, 3);
    addNode(slide, 80, 295, "Operator", BLUE_LIGHT, BLUE);
    addNode(slide, 500, 205, "Server A");
    addNode(slide, 500, 395, "Decoy", "#FFF4F1", RED);
    addNode(slide, 930, 300, "Tier 0", "#E9F5EE", GREEN);
    addText(slide, "session + ACL", { left: 294, top: 244, width: 170, height: 28 }, {
      size: 18,
      color: MUTED,
      align: "center",
    });
    addText(slide, "plausible shortcut", { left: 690, top: 401, width: 190, height: 28 }, {
      size: 18,
      color: RED,
      align: "center",
    });
    addText(slide, "The model must separate a real bounded route from a tempting but unsupported one.", {
      left: 170,
      top: 565,
      width: 930,
      height: 58,
    }, { size: 25, bold: true, align: "center" });
    addNotes(
      slide,
      "Use the synthetic graph to explain alternate routes, decoys, ordered endpoints, negative controls, and bounded decisions. The diagram is conceptual and intentionally does not reveal a sealed answer.",
      [
        "docs/offensive-ai-con-talk-outline.md",
        "docs/benchmark-v2-design-rationale.md",
      ],
    );
  }

  // 3 — Direct versus MCP.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "Direct and MCP test different capabilities", 3);
    addLine(slide, 640, 195, 640, 622, RULE, 1);
    addPanelLabel(slide, "Direct", 80, 190, 260);
    addText(slide, "The model writes one CySQL query.", {
      left: 80,
      top: 232,
      width: 490,
      height: 58,
    }, { size: 28, bold: true });
    addText(slide, "ORI contains the query, executes it once, projects graph evidence, and scores the structured result.", {
      left: 80,
      top: 320,
      width: 480,
      height: 150,
    }, { size: 22, color: MUTED });
    addText(slide, "query → result → Evidence IR", {
      left: 80,
      top: 525,
      width: 480,
      height: 42,
    }, { size: 24, color: BLUE, bold: true });
    addPanelLabel(slide, "MCP", 710, 190, 260);
    addText(slide, "The model chooses tools over multiple turns.", {
      left: 710,
      top: 232,
      width: 490,
      height: 70,
    }, { size: 28, bold: true });
    addText(slide, "ORI records tool evidence, capability and launcher identity, whole-task budgets, and the final answer boundary.", {
      left: 710,
      top: 320,
      width: 480,
      height: 150,
    }, { size: 22, color: MUTED });
    addText(slide, "tool loop → receipts → Evidence IR", {
      left: 710,
      top: 525,
      width: 480,
      height: 42,
    }, { size: 24, color: BLUE, bold: true });
    addNotes(
      slide,
      "Keep the surfaces separate throughout the talk. Direct measures constrained query generation and result interpretation. MCP measures multi-turn tool use and proof construction. Their scores are not one leaderboard.",
      ["docs/benchmark-v2-design-rationale.md", "README.md"],
    );
  }

  // 4 — Failure-driven history.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "Bad evidence taught the benchmark to improve", 4);
    addLine(slide, 100, 350, 1180, 350, INK, 2);
    const points = [150, 475, 800, 1125];
    const labels = ["V1", "V2", "V28", "V29"];
    const heads = [
      "Exact answers",
      "Typed claims",
      "Live parity",
      "Durable release",
    ];
    const bodies = [
      "Useful experiment; scorer drift could look like model failure.",
      "Public AcceptanceSpec and sealed oracle compile from one claim.",
      "Track adapters, evidence projection, and graph gates cross the same fixtures.",
      "Semantic scheduling, campaign locks, checkpoints, and per-track publication.",
    ];
    for (let i = 0; i < points.length; i += 1) {
      addCircle(slide, points[i] - 8, 342, 16, i === 3 ? BLUE : INK);
      addText(slide, labels[i], { left: points[i] - 35, top: 292, width: 70, height: 30 }, {
        size: 20,
        bold: true,
        align: "center",
      });
      addText(slide, heads[i], { left: points[i] - 105, top: 390, width: 210, height: 42 }, {
        size: 23,
        bold: true,
        align: "center",
      });
      addText(slide, bodies[i], { left: points[i] - 125, top: 447, width: 250, height: 125 }, {
        size: 18,
        color: MUTED,
        align: "center",
      });
    }
    addText(slide, "Benchmark defects can masquerade as model failures.", {
      left: 170,
      top: 190,
      width: 940,
      height: 52,
    }, { size: 30, color: RED, bold: true, align: "center" });
    addNotes(
      slide,
      "Tell the history as a sequence of incidents that became contracts. Avoid implying that V1, V28, and V29 scores are directly comparable.",
      [
        "docs/offensive-ai-con-readiness-plan.md",
        "docs/benchmark-v2-design-rationale.md",
      ],
    );
  }

  // 5 — Truth pipeline.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "Graph truth comes before model spend", 5);
    const labels = [
      "Generate",
      "Archive\nvalidate",
      "Verify\ningest",
      "Compile +\ncertify",
      "Readiness",
      "Execute",
      "Post-track\ngate",
    ];
    const x0 = 60;
    const gap = 12;
    const width = 157;
    for (let i = 0; i < labels.length; i += 1) {
      const x = x0 + i * (width + gap);
      if (i < labels.length - 1) addArrow(slide, x + width, 355, x + width + gap, 355, RULE, 2);
      addShape(slide, "rect", { left: x, top: 285, width, height: 140 }, i === 5 ? BLUE_LIGHT : PANEL, {
        style: "solid",
        fill: i === 5 ? BLUE : RULE,
        width: 1,
      });
      addText(slide, labels[i], { left: x + 10, top: 320, width: width - 20, height: 72 }, {
        size: 21,
        bold: true,
        align: "center",
        valign: "middle",
      });
    }
    addText(slide, "STOP", { left: 377, top: 451, width: 90, height: 32 }, {
      size: 18,
      color: RED,
      bold: true,
      align: "center",
    });
    addText(slide, "wrong graph", { left: 360, top: 486, width: 124, height: 32 }, {
      size: 17,
      color: MUTED,
      align: "center",
    });
    addText(slide, "STOP", { left: 884, top: 451, width: 90, height: 32 }, {
      size: 18,
      color: RED,
      bold: true,
      align: "center",
    });
    addText(slide, "stale or incompatible", { left: 842, top: 486, width: 174, height: 32 }, {
      size: 17,
      color: MUTED,
      align: "center",
    });
    addText(slide, "Health is not ingest. Readiness is not execution.", {
      left: 190,
      top: 575,
      width: 900,
      height: 50,
    }, { size: 30, bold: true, align: "center" });
    addNotes(
      slide,
      "Walk left to right. Every gate proves a different layer. The post-track graph check can invalidate a completed-looking run; that is a feature, not a nuisance.",
      [
        "README.md",
        "docs/benchmark-v2-certification-evidence.md",
        "docs/benchmark-hardening-runbook.md",
      ],
    );
  }

  // 6 — Typed claim boundaries.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "One claim. Two boundaries.", 6);
    addShape(slide, "rect", { left: 80, top: 290, width: 210, height: 110 }, BLUE_LIGHT, {
      style: "solid",
      fill: BLUE,
      width: 2,
    });
    addText(slide, "Typed claim", { left: 100, top: 326, width: 170, height: 40 }, {
      size: 28,
      bold: true,
      align: "center",
    });
    addArrow(slide, 290, 325, 455, 250, BLUE, 3);
    addArrow(slide, 290, 365, 455, 460, INK, 3);
    addShape(slide, "rect", { left: 455, top: 190, width: 310, height: 160 }, "#FFFFFF", {
      style: "solid",
      fill: BLUE,
      width: 2,
    });
    addPanelLabel(slide, "Solver-visible", 480, 214, 240);
    addText(slide, "Question + AcceptanceSpec + answer schema", {
      left: 480,
      top: 258,
      width: 260,
      height: 72,
    }, { size: 22, bold: true });
    addShape(slide, "rect", { left: 455, top: 400, width: 310, height: 160 }, PANEL, {
      style: "solid",
      fill: INK,
      width: 2,
    });
    addPanelLabel(slide, "Sealed", 480, 424, 240, RED);
    addText(slide, "Oracle + expected graph facts + comparator policy", {
      left: 480,
      top: 468,
      width: 260,
      height: 72,
    }, { size: 22, bold: true });
    addArrow(slide, 765, 270, 920, 320, BLUE, 3);
    addArrow(slide, 765, 480, 920, 390, INK, 3);
    addShape(slide, "rect", { left: 920, top: 285, width: 275, height: 150 }, "#FFFFFF", {
      style: "solid",
      fill: GREEN,
      width: 2,
    });
    addText(slide, "Evidence IR\n+ comparator", {
      left: 945,
      top: 325,
      width: 225,
      height: 85,
    }, { size: 28, bold: true, align: "center" });
    addText(slide, "If a hidden scoring rule cannot be derived from the public contract, compilation stops.", {
      left: 170,
      top: 592,
      width: 940,
      height: 52,
    }, { size: 25, bold: true, align: "center" });
    addNotes(
      slide,
      "Explain that the solver never sees the sealed oracle. Both public and sealed artifacts compile from the same typed claim. EvidenceIR is the shared semantic boundary across Direct and MCP.",
      [
        "docs/benchmark-v2-design-rationale.md",
        "docs/benchmark-v2-task-authoring.md",
      ],
    );
  }

  // 7 — Tier 6 example.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "Difficulty should come from reasoning—not hidden rules", 7);
    addPanelLabel(slide, "Sanitized task", 70, 190, 280);
    addText(slide, "Find a bounded route from the operator to Tier 0. Return the ordered path and required edge evidence. Reject disconnected decoys.", {
      left: 70,
      top: 235,
      width: 470,
      height: 160,
    }, { size: 26, bold: true });
    addText(slide, "Public contract", { left: 70, top: 445, width: 240, height: 32 }, {
      size: 22,
      color: BLUE,
      bold: true,
    });
    addText(slide, "• ordered source and target\n• bounded hop ceiling\n• path-shaped graph receipt\n• declared supporting properties", {
      left: 70,
      top: 490,
      width: 430,
      height: 140,
    }, { size: 21, color: MUTED });
    addLine(slide, 610, 200, 610, 625, RULE, 1);
    addArrow(slide, 735, 300, 955, 300, INK, 3);
    addArrow(slide, 955, 300, 1110, 465, INK, 3);
    addNode(slide, 640, 265, "Source", BLUE_LIGHT, BLUE, 150);
    addNode(slide, 910, 265, "Pivot", "#FFFFFF", INK, 150);
    addNode(slide, 1060, 430, "Target", "#E9F5EE", GREEN, 150);
    addNode(slide, 750, 470, "Decoy", "#FFF4F1", RED, 150);
    addLine(slide, 830, 455, 955, 455, RED, 2);
    addLine(slide, 955, 337, 955, 455, RED, 2);
    addText(slide, "sealed answer omitted", {
      left: 785,
      top: 590,
      width: 330,
      height: 34,
    }, { size: 20, color: RED, bold: true, align: "center" });
    addNotes(
      slide,
      "This is an illustrative task shape, not an actual sealed answer. Emphasize ordered endpoints, bounded traversal, path evidence, and declared properties. A hard task is still auditable by the solver.",
      [
        "docs/benchmark-v2-design-rationale.md",
        "docs/benchmark-v2-task-authoring.md",
      ],
    );
  }

  // 8 — Incidents to boundaries.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "Incidents became typed runtime boundaries", 8);
    const rows = [
      ["Nullable provider content", "typed output items; text is never nullable"],
      ["Nous rejected null schemas", "normalized tool schemas before transport"],
      ["Unsafe or runaway CySQL", "model-blind query containment + deny cache"],
      ["Wrong launcher binary", "resolved executable identity in readiness fingerprints"],
      ["Neo4j failed mid-campaign", "pre-provider circuit gate + post-track graph check"],
    ];
    addText(slide, "Incident", { left: 70, top: 190, width: 380, height: 34 }, {
      size: 18,
      color: RED,
      bold: true,
    });
    addText(slide, "Regression-covered boundary", { left: 540, top: 190, width: 580, height: 34 }, {
      size: 18,
      color: BLUE,
      bold: true,
    });
    rows.forEach(([incident, boundary], index) => {
      const y = 235 + index * 80;
      addLine(slide, 70, y + 62, 1190, y + 62, RULE, 1);
      addText(slide, incident, { left: 70, top: y + 12, width: 390, height: 42 }, {
        size: 22,
        bold: true,
      });
      addArrow(slide, 465, y + 33, 520, y + 33, RULE, 2);
      addText(slide, boundary, { left: 550, top: y + 9, width: 610, height: 50 }, {
        size: 21,
        color: MUTED,
      });
    });
    addText(slide, "Failures become scores only when the evidence says they are model outcomes.", {
      left: 165,
      top: 645,
      width: 950,
      height: 38,
    }, { size: 24, bold: true, align: "center" });
    addNotes(
      slide,
      "Label the provider and circuit changes as current Release 1 branch behavior until the branch is merged and the OpenRouter canaries pass. Do not call staged code shipped code.",
      [
        "docs/offensive-ai-con-readiness-plan.md",
        "docs/evidence/provider-hardening-v13-acceptance.json",
        "docs/benchmark-hardening-runbook.md",
      ],
    );
  }

  // 9 — V29 boundary.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "V29 is a certified development boundary", 9);
    const stats = [
      ["46", "Direct certified"],
      ["70", "MCP certified"],
      ["42", "Direct scheduled"],
      ["55", "MCP scheduled"],
    ];
    stats.forEach(([value, label], index) => {
      const x = 70 + index * 295;
      addText(slide, value, { left: x, top: 215, width: 230, height: 100 }, {
        size: 68,
        bold: true,
        align: "center",
      });
      addText(slide, label, { left: x, top: 325, width: 230, height: 52 }, {
        size: 21,
        color: MUTED,
        bold: true,
        align: "center",
      });
    });
    addLine(slide, 70, 420, 1190, 420, RULE, 1);
    addText(slide, "Graph", { left: 75, top: 465, width: 120, height: 30 }, {
      size: 18,
      color: BLUE,
      bold: true,
    });
    addText(slide, "fb0b6785…b74b1c4", { left: 75, top: 505, width: 420, height: 40 }, {
      size: 26,
      bold: true,
    });
    addText(slide, "MCP capability", { left: 590, top: 465, width: 210, height: 30 }, {
      size: 18,
      color: BLUE,
      bold: true,
    });
    addText(slide, "ori-mcp-92a37dd-bhce-9.1-cypher-v7", {
      left: 590,
      top: 505,
      width: 600,
      height: 44,
    }, { size: 23, bold: true });
    addText(slide, "Not the future official 100 Direct / 100 MCP suite", {
      left: 250,
      top: 610,
      width: 780,
      height: 40,
    }, { size: 25, color: RED, bold: true, align: "center" });
    addNotes(
      slide,
      "This is the exact honesty boundary. Certified inventory and semantic release are different counts. The graph fingerprint and MCP capability pin belong to this development release, not every future ORI campaign.",
      [
        "README.md",
        "docs/benchmark-v2-design-rationale.md",
        "docs/offensive-ai-con-readiness-plan.md",
      ],
    );
  }

  // 10 — Historical Direct range plot.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "Historical Direct scores cluster tightly", 10);
    const data = [
      ["GPT-5.5", 98.57, 95.24, 100.0],
      ["Daybreak Red", 98.10, 95.24, 100.0],
      ["Daybreak Blue", 97.62, 95.24, 100.0],
      ["GPT-5.6 Sol", 97.14, 95.24, 100.0],
      ["GPT-5.6 Luna", 91.90, 88.10, 95.24],
      ["GPT-5.6 Terra", 85.71, 78.57, 95.24],
    ];
    const plotLeft = 390;
    const plotRight = 1125;
    const min = 75;
    const max = 100;
    const mapX = (value) => plotLeft + ((value - min) / (max - min)) * (plotRight - plotLeft);
    [75, 80, 85, 90, 95, 100].forEach((tick) => {
      const x = mapX(tick);
      addLine(slide, x, 210, x, 565, tick === 100 ? INK : RULE, 1);
      addText(slide, `${tick}%`, { left: x - 30, top: 575, width: 60, height: 28 }, {
        size: 16,
        color: MUTED,
        align: "center",
      });
    });
    data.forEach(([name, value, low, high], index) => {
      const y = 235 + index * 58;
      addText(slide, name, { left: 70, top: y - 15, width: 240, height: 32 }, {
        size: 20,
        bold: index < 4,
      });
      addLine(slide, mapX(low), y, mapX(high), y, index < 4 ? BLUE : MUTED, 6);
      addCircle(slide, mapX(value) - 8, y - 8, 16, index < 4 ? BLUE : INK);
      addText(slide, `${value.toFixed(2)}%`, {
        left: plotRight + 16,
        top: y - 15,
        width: 95,
        height: 30,
      }, { size: 18, bold: true, align: "right" });
    });
    addText(slide, "5 passes · 42 tasks/pass · n=210/model · n=1,260 overall", {
      left: 70,
      top: 620,
      width: 680,
      height: 32,
    }, { size: 19, color: MUTED, bold: true });
    addText(slide, "0 infrastructure, harness, or unexecuted outcomes", {
      left: 745,
      top: 620,
      width: 450,
      height: 32,
    }, { size: 18, color: GREEN, bold: true, align: "right" });
    addNotes(
      slide,
      "The horizontal line is the five-run minimum-to-maximum range; the dot is pooled effective accuracy. The top four differ by one to three answers out of 210 and overlap in repeat ranges, so the evidence does not support a strong total ordering.",
      [
        "docs/assets/offensive-ai-con/historical-v29-direct-evidence.json",
        "docs/assets/offensive-ai-con/historical-v29-direct-results.csv",
      ],
    );
  }

  // 11 — MCP invalidation incident.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "ORI withheld the invalid MCP ranking", 11);
    const steps = [
      ["1", "Neo4j fails", "A controlled graph dependency becomes unavailable."],
      ["2", "Old runner keeps spending", "The circuit opened, but later provider calls continued."],
      ["3", "Post-track gate fails", "The live graph no longer matches the certified boundary."],
      ["4", "No public report", "Without a valid completion receipt, there is no leaderboard."],
    ];
    steps.forEach(([num, head, body], index) => {
      const y = 190 + index * 112;
      if (index < steps.length - 1) addArrow(slide, 105, y + 65, 105, y + 108, RULE, 2);
      addCircle(slide, 76, y, 58, index === 3 ? RED : INK);
      addText(slide, num, { left: 76, top: y + 9, width: 58, height: 40 }, {
        size: 25,
        color: "#FFFFFF",
        bold: true,
        align: "center",
      });
      addText(slide, head, { left: 170, top: y, width: 350, height: 40 }, {
        size: 26,
        bold: true,
      });
      addText(slide, body, { left: 550, top: y + 2, width: 630, height: 52 }, {
        size: 22,
        color: MUTED,
      });
    });
    addText(slide, "Invalid infrastructure state is not model performance.", {
      left: 230,
      top: 640,
      width: 820,
      height: 40,
    }, { size: 28, color: RED, bold: true, align: "center" });
    addNotes(
      slide,
      "Describe the incident sequence without publishing the incomplete MCP diagnostic outputs as model scores. The current branch adds a pre-provider circuit gate to prevent the spending amplification seen in the older runtime.",
      [
        "docs/offensive-ai-con-readiness-plan.md",
        "docs/benchmark-v2-design-rationale.md",
      ],
    );
  }

  // 12 — Anti-claims.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "Honest boundaries", 12);
    const claims = [
      "No official 100 / 100 selector—yet",
      "No unified Direct + MCP score",
      "No SOTA claim from a tight historical cluster",
      "No valid historical MCP leaderboard",
    ];
    claims.forEach((claim, index) => {
      const y = 195 + index * 108;
      addLine(slide, 80, y + 68, 1180, y + 68, RULE, 1);
      addText(slide, "NOT", { left: 80, top: y + 10, width: 105, height: 48 }, {
        size: 22,
        color: RED,
        bold: true,
      });
      addText(slide, claim, { left: 210, top: y, width: 930, height: 60 }, {
        size: 31,
        bold: true,
      });
    });
    addNotes(
      slide,
      "Pause here. These are deliberate anti-claims, not caveats in tiny type. The benchmark is more credible when incompatible tracks, invalid runs, and unfinished release goals remain explicit.",
      ["docs/offensive-ai-con-talk-outline.md", "README.md"],
    );
  }

  // 13 — Roadmap and close.
  {
    const slide = deck.slides.add();
    addSlideTitle(slide, "New evidence must replace old assumptions", 13);
    const stages = [
      ["Now", "Merge Release 1", "OpenRouter + Nous canaries, signed PR, fresh runtime boundary"],
      ["Next", "Run current Direct + MCP", "Fresh campaign roots, valid per-track receipts, frozen public bundle"],
      ["Then", "Freeze evidence", "Deck, offline demo, reproducibility package, rehearsal cut points"],
      ["Future", "Select the official suite", "Deterministic 100 Direct / 100 MCP release with new certification"],
    ];
    stages.forEach(([label, head, body], index) => {
      const x = 70 + index * 295;
      if (index < stages.length - 1) addArrow(slide, x + 250, 360, x + 286, 360, RULE, 2);
      addText(slide, label.toUpperCase(), { left: x, top: 205, width: 250, height: 28 }, {
        size: 15,
        color: index === 0 ? BLUE : MUTED,
        bold: true,
      });
      addText(slide, head, { left: x, top: 255, width: 250, height: 78 }, {
        size: 28,
        bold: true,
      });
      addCircle(slide, x, 349, 22, index === 0 ? BLUE : INK);
      addText(slide, body, { left: x, top: 405, width: 250, height: 150 }, {
        size: 20,
        color: MUTED,
      });
    });
    addText(slide, "Build the benchmark so it can say “we do not know” without turning uncertainty into a score.", {
      left: 130,
      top: 610,
      width: 1020,
      height: 58,
    }, { size: 27, bold: true, align: "center" });
    addNotes(
      slide,
      "Resolve the opening tension: plausible is not proven, and uncertainty should remain typed and visible. Replace the Release 1 step with merged evidence once the OpenRouter canaries and PR complete.",
      [
        "docs/offensive-ai-con-readiness-plan.md",
        "docs/offensive-ai-con-talk-outline.md",
      ],
    );
  }

  return deck;
}

async function main() {
  await fs.mkdir(path.dirname(OUTPUT), { recursive: true });
  await fs.rm(PREVIEW_DIR, { recursive: true, force: true });
  await fs.mkdir(PREVIEW_DIR, { recursive: true });

  const deck = buildDeck();
  for (const [index, slide] of deck.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    await writeBlob(
      path.join(PREVIEW_DIR, `${stem}.png`),
      await deck.export({ slide, format: "png", scale: 1 }),
    );
    await fs.writeFile(
      path.join(PREVIEW_DIR, `${stem}.layout.json`),
      await (await slide.export({ format: "layout" })).text(),
    );
  }
  await writeBlob(
    path.join(PREVIEW_DIR, "deck-montage.webp"),
    await deck.export({ format: "webp", montage: true, scale: 1 }),
  );
  const pptx = await PresentationFile.exportPptx(deck);
  await pptx.save(OUTPUT);
  console.log(OUTPUT);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
