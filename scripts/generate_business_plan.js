const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, HeadingLevel, VerticalAlign
} = require("docx");

// ── Constants ──
const PAGE_W = 11906; // A4
const PAGE_H = 16838;
const MARGIN_TB = 1440;
const MARGIN_LR = 1134;
const CONTENT_W = PAGE_W - MARGIN_LR * 2; // 9638
const FONT = "Arial";
const GRAY = "D9D9D9";
const LIGHT_GRAY = "F2F2F2";
const border = { style: BorderStyle.SINGLE, size: 1, color: "999999" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 60, bottom: 60, left: 100, right: 100 };

// ── Helpers ──
function txt(text, opts = {}) {
  return new TextRun({ text, font: FONT, size: opts.size || 20, bold: opts.bold || false, color: opts.color || "000000", ...opts });
}

function para(children, opts = {}) {
  if (typeof children === "string") children = [txt(children, opts)];
  return new Paragraph({ children, spacing: { after: opts.after || 120, line: opts.line || 276 }, alignment: opts.align || AlignmentType.LEFT, ...opts });
}

function sectionTitle(text) {
  return new Paragraph({
    children: [txt(text, { size: 24, bold: true })],
    spacing: { before: 240, after: 160 },
    shading: { fill: GRAY, type: ShadingType.CLEAR },
    indent: { left: 100, right: 100 },
  });
}

function subTitle(text) {
  return new Paragraph({
    children: [txt(text, { size: 22, bold: true })],
    spacing: { before: 200, after: 120 },
  });
}

function oMarker(text, detail) {
  const runs = [txt(text, { bold: true })];
  if (detail) runs.push(txt(detail));
  return para(runs, { after: 80 });
}

function cell(content, opts = {}) {
  const children = typeof content === "string"
    ? [para([txt(content, { size: opts.fontSize || 18, bold: opts.bold || false })], { after: 0, align: opts.align || AlignmentType.LEFT })]
    : content;
  return new TableCell({
    borders,
    margins: cellMargins,
    width: { size: opts.width || 2000, type: WidthType.DXA },
    shading: opts.shading ? { fill: opts.shading, type: ShadingType.CLEAR } : undefined,
    verticalAlign: opts.vAlign || VerticalAlign.CENTER,
    columnSpan: opts.colSpan || 1,
    rowSpan: opts.rowSpan || 1,
    children,
  });
}

function headerCell(text, width) {
  return cell(text, { width, bold: true, shading: GRAY, align: AlignmentType.CENTER });
}

function row(cells) {
  return new TableRow({ children: cells });
}

function table(rows, colWidths) {
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: colWidths,
    rows,
  });
}

function bulletItem(text, opts = {}) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    children: [txt(text, { size: 18 })],
    spacing: { after: 60 },
  });
}

function multiPara(cellTexts, opts = {}) {
  return cellTexts.map(t => para([txt(t, { size: opts.fontSize || 18 })], { after: 40 }));
}

// ── Document ──
const doc = new Document({
  numbering: {
    config: [{
      reference: "bullets",
      levels: [{
        level: 0, format: LevelFormat.BULLET, text: "\u2022",
        alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 360, hanging: 180 } } },
      }],
    }],
  },
  styles: {
    default: { document: { run: { font: FONT, size: 20 } } },
  },
  sections: [{
    properties: {
      page: {
        size: { width: PAGE_W, height: PAGE_H },
        margin: { top: MARGIN_TB, right: MARGIN_LR, bottom: MARGIN_TB, left: MARGIN_LR },
      },
    },
    headers: {
      default: new Header({
        children: [para([txt("2026 \uC608\uBE44\uCC3D\uC5C5\uD328\uD0A4\uC9C0 \uC0AC\uC5C5\uACC4\uD68D\uC11C", { size: 16, color: "888888" })], { align: AlignmentType.RIGHT, after: 0 })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [txt("- ", { size: 16, color: "888888" }), new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16, color: "888888" }), txt(" -", { size: 16, color: "888888" })],
        })],
      }),
    },
    children: [
      // ── 안내 박스 ──
      new Paragraph({
        children: [txt("\u203B \uC0AC\uC5C5 \uC2E0\uCCAD \uC2DC, \uC0AC\uC5C5\uACC4\uD68D\uC11C \uC791\uC131 \uBAA9\uCC28 \uD398\uC774\uC9C0\uB294 \uC0AD\uC81C\uD558\uACE0 \uC81C\uCD9C", { size: 18, color: "555555" })],
        alignment: AlignmentType.CENTER,
        shading: { fill: LIGHT_GRAY, type: ShadingType.CLEAR },
        spacing: { after: 300 },
      }),

      // ══════════ 섹션 1: 일반현황 ══════════
      sectionTitle("\u25A1 \uC77C\uBC18\uD604\uD669"),

      // 창업아이템명
      table([
        row([
          headerCell("\uCC3D\uC5C5\uC544\uC774\uD15C\uBA85", 2000),
          cell("AI \uAE30\uBC18 \uD3B8\uC758\uC810 \uC790\uB3D9\uBC1C\uC8FC SaaS '\uC624\uB354\uD54F AI(OrderFit AI)'", { width: CONTENT_W - 2000 }),
        ]),
        row([
          headerCell("\uC0B0\uCD9C\uBB3C\n(\uD611\uC57D\uAE30\uAC04 \uB0B4 \uBAA9\uD45C)", 2000),
          cell([
            para([txt("SaaS \uC6F9 \uD50C\uB7AB\uD3FC(1\uAC1C), \uC790\uB3D9 \uC628\uBCF4\uB529 \uC2DC\uC2A4\uD15C(1\uAC1C)", { size: 18 })], { after: 40 }),
            para([txt("\u203B \uD611\uC57D\uAE30\uAC04 \uB0B4 \uC81C\uC791\uB7C5\uAC1C\uBC1C \uC644\uB8CC\uD560 \uCD5C\uC885 \uC0B0\uCD9C\uBB3C\uC758 \uD615\uD0DC, \uC218\uB7C9 \uB4F1 \uAE30\uC7AC", { size: 16, color: "888888" })], { after: 0 }),
          ], { width: CONTENT_W - 2000 }),
        ]),
        row([
          headerCell("\uC9C1\uC5C5", 2000),
          cell("\uD3B8\uC758\uC810 \uC810\uC8FC(\uC790\uC601\uC5C5)", { width: (CONTENT_W - 2000) / 3 }),
          headerCell("\uAE30\uC5C5(\uC608\uC815)\uBA85", (CONTENT_W - 2000) / 3),
          cell("\uC624\uB354\uD54F", { width: (CONTENT_W - 2000) / 3 }),
        ]),
      ], [2000, (CONTENT_W - 2000) / 3, (CONTENT_W - 2000) / 3, (CONTENT_W - 2000) / 3]),

      para("", { after: 120 }),

      // 팀 구성현황
      para([txt("(\uC608\uBE44)\uCC3D\uC5C5\uD300 \uAD6C\uC131\uD604\uD669 (\uB300\uD45C\uC790 \uBCF8\uC778 \uC81C\uC678)", { size: 20, bold: true })], { after: 80 }),
      table([
        row([
          headerCell("\uC21C\uBC88", 600),
          headerCell("\uC9C1\uC704", 1200),
          headerCell("\uB2F4\uB2F9\uC5C5\uBB34", 2500),
          headerCell("\uBCF4\uC720\uC5ED\uB7C9(\uACBD\uB825 \uBC0F \uD559\uB825 \uB4F1)", 3538),
          headerCell("\uAD6C\uC131\uC0C1\uD0DC", 1800),
        ]),
        row([
          cell("1", { width: 600, align: AlignmentType.CENTER }),
          cell("CTO \uACF5\uB3D9\uB300\uD45C", { width: 1200 }),
          cell("\uD074\uB77C\uC6B0\uB4DC \uC778\uD504\uB77C\uB7C5\uBC31\uC5D4\uB4DC \uAC1C\uBC1C", { width: 2500 }),
          cell("Python\uB7C5AWS\uB7C5DevOps \uC804\uACF5 \uACBD\uB825 5\uB144+", { width: 3538 }),
          cell("\uC608\uC815('26.Q2)", { width: 1800, align: AlignmentType.CENTER }),
        ]),
        row([
          cell("2", { width: 600, align: AlignmentType.CENTER }),
          cell("\uC601\uC5C5\uD300\uC7A5", { width: 1200 }),
          cell("\uD3B8\uC758\uC810 \uC810\uC8FC \uC601\uC5C5\uB7C5\uB9C8\uCF00\uD305", { width: 2500 }),
          cell("\uC720\uD1B5\uCC44\uB110 \uC601\uC5C5 \uACBD\uB825 3\uB144+", { width: 3538 }),
          cell("\uC608\uC815('26.Q3)", { width: 1800, align: AlignmentType.CENTER }),
        ]),
      ], [600, 1200, 2500, 3538, 1800]),

      new Paragraph({ children: [new PageBreak()] }),

      // ══════════ 섹션 2: 개요(요약) ══════════
      sectionTitle("\u25A1 \uCC3D\uC5C5 \uC544\uC774\uD15C \uAC1C\uC694(\uC694\uC57D)"),

      table([
        row([headerCell("\uBA85\uCE6D", 1800), cell("\uC624\uB354\uD54F AI (OrderFit AI)", { width: CONTENT_W - 1800 })]),
        row([headerCell("\uBC94\uC8FC", 1800), cell("B2B SaaS / \uC720\uD1B5\uB7C5\uBB3C\uB958 AI / \uD3B8\uC758\uC810 \uBC1C\uC8FC \uC790\uB3D9\uD654", { width: CONTENT_W - 1800 })]),
        row([
          headerCell("\uC544\uC774\uD15C \uAC1C\uC694", 1800),
          cell([para([txt("LightGBM \uAE30\uBC18 AI\uAC00 \uD3B8\uC758\uC810 \uB9E4\uC7A5\uC758 \uD310\uB9E4 \uC774\uB825\uB7C5\uB0A0\uC528\uB7C5\uD589\uC0AC\uB7C5\uAE09\uC5EC\uC77C \uB4F1 47\uAC1C \uD53C\uCC98\uB97C \uD559\uC2B5\uD558\uC5EC \uB9E4\uC77C \uC544\uCE68 CU \uD3B8\uC758\uC810\uC758 \uCD5C\uC801 \uBC1C\uC8FC\uB7C9\uC744 \uC790\uB3D9 \uC0B0\uCD9C\uB7C5\uC2E4\uD589\uD558\uB294 SaaS \uD50C\uB7AB\uD3FC. \uC810\uC8FC\uC758 \uC77C\uC77C 1~2\uC2DC\uAC04 \uBC1C\uC8FC \uC791\uC5C5\uC744 AI\uB85C \uB300\uCCB4\uD558\uC5EC \uBC1C\uC8FC \uC2DC\uAC04\uC744 \uC808\uAC10\uD558\uACE0 \uC7AC\uACE0 \uB0AD\uBE44\uB97C \uC904\uC784. \uD604\uC7AC 3\uAC1C CU \uB9E4\uC7A5\uC5D0\uC11C \uC2E4\uC99D \uC6B4\uC601 \uC911 (\uC608\uCE21 \uC815\uD655\uB3C4 82.4%, \uC790\uB3D9\uD654\uC728 57.1% \uB2EC\uC131).", { size: 18 })], { after: 0 })], { width: CONTENT_W - 1800 }),
        ]),
        row([
          headerCell("\uBB38\uC81C\uC778\uC2DD\n(Problem)", 1800),
          cell([para([txt("\uC804\uAD6D CU \uD3B8\uC758\uC810 17,000\uAC1C \uC810\uC8FC\uAC00 \uB9E4\uC77C 1~2\uC2DC\uAC04\uC744 \uC218\uC791\uC5C5 \uBC1C\uC8FC\uC5D0 \uC18C\uBE44. BGF \uB9AC\uD14C\uC77C \uC81C\uACF5 \uC2A4\uB9C8\uD2B8\uBC1C\uC8FC\uB294 \uB2E8\uC21C \uC774\uB3D9\uD3C9\uADE0 \uAE30\uBC18\uC73C\uB85C \uB0A0\uC528\uB7C5\uD589\uC0AC\uB7C5\uAE09\uC5EC\uC77C \uB4F1 \uC678\uBD80 \uC694\uC778 \uBBF8\uBC18\uC601. \uD3C9\uADE0 5% \uC774\uC0C1 \uD3D0\uAE30 \uC190\uC2E4 \uBC1C\uC0DD (\uC6D4 \uB9E4\uC785\uC561 800\uB9CC\uC6D0 \uAE30\uC900 \uC6D4 40\uB9CC\uC6D0 \uC774\uC0C1 \uC190\uC2E4).", { size: 18 })], { after: 0 })], { width: CONTENT_W - 1800 }),
        ]),
        row([
          headerCell("\uC2E4\uD604\uAC00\uB2A5\uC131\n(Solution)", 1800),
          cell([para([txt("BGF \uB125\uC0AC\uD06C\uB85C Direct API \uC5ED\uBD84\uC11D\uC744 \uD1B5\uD55C \uBC1C\uC8FC \uC644\uC804 \uC790\uB3D9\uD654 \uAD6C\uD604. LightGBM + WMA \uC559\uC0C1\uBE14, 47\uAC1C \uD53C\uCC98 \uD559\uC2B5. 3\uB2E8 \uD3F4\uBC31(Direct API \u2192 Batch Grid \u2192 Selenium)\uC73C\uB85C 100% \uBC1C\uC8FC \uC131\uACF5. Python 12\uB9CC\uC904, \uD14C\uC2A4\uD2B8 3,700+. 3\uAC1C \uB9E4\uC7A5 2\uAC1C\uC6D4 \uC2E4\uC99D \uC644\uB8CC.", { size: 18 })], { after: 0 })], { width: CONTENT_W - 1800 }),
        ]),
        row([
          headerCell("\uC131\uC7A5\uC804\uB7B5\n(Scale-up)", 1800),
          cell([para([txt("\uC6D4 49,000\uC6D0 \uAD6C\uB3C5 SaaS. \uC774\uCC9C \uB808\uD37C\uB7F0\uC2A4 20\uAC1C \uD655\uBCF4 \u2192 CU \uAC00\uB9F9\uBCF8\uBD80 \uCC44\uB110 \uC804\uAD6D \uD655\uC7A5. SAM: CU 17,000\uAC1C \u00D7 49,000\uC6D0 = \uC6D4 8.3\uC5B5\uC6D0. 1\uB144 \uBAA9\uD45C: 100\uAC1C \uB9E4\uC7A5, \uC2DC\uB4DC \uD22C\uC790 \uC720\uCE58.", { size: 18 })], { after: 0 })], { width: CONTENT_W - 1800 }),
        ]),
        row([
          headerCell("\uD300\uAD6C\uC131\n(Team)", 1800),
          cell([para([txt("\uB300\uD45C\uC790: CU 3\uC810\uD3EC \uC6B4\uC601 3\uB144+, AI \uC2DC\uC2A4\uD15C \uB3C5\uC790 \uAC1C\uBC1C(10\uAC1C\uC6D4). \uD074\uB77C\uC6B0\uB4DC \uC778\uD504\uB77C \uAC1C\uBC1C\uC778\uB825 \uBC0F \uC601\uC5C5\uC778\uB825 \uCD94\uAC00 \uC608\uC815.", { size: 18 })], { after: 0 })], { width: CONTENT_W - 1800 }),
        ]),
      ], [1800, CONTENT_W - 1800]),

      new Paragraph({ children: [new PageBreak()] }),

      // ══════════ 섹션 3: 문제인식 ══════════
      sectionTitle("1. \uBB38\uC81C\uC778\uC2DD (Problem)"),

      subTitle("1-1. \uAD6D\uB0B4 \uD3B8\uC758\uC810 \uC0B0\uC5C5 \uD604\uD669 \uBC0F \uAD6C\uC870\uC801 \uBB38\uC81C"),

      oMarker("\u3147 \uD3B8\uC758\uC810 \uC2DC\uC7A5 \uADDC\uBAA8: ", "\uAD6D\uB0B4 \uD3B8\uC758\uC810\uC740 CU\uB7C5GS25\uB7C5\uC138\uBE10\uC77C\uB808\uBE10 \uB4F1 \uC57D 55,000\uAC1C\uAC00 \uC6B4\uC601 \uC911\uC774\uBA70, CU(BGF\uB9AC\uD14C\uC77C)\uB294 \uC57D 17,000\uAC1C\uB85C \uCD5C\uB300 \uBE0C\uB79C\uB4DC. \uD3B8\uC758\uC810 \uC804\uCCB4 \uD488\uBAA9\uC758 30% \uC774\uC0C1\uC774 \uC720\uD1B5\uAE30\uD55C\uC774 \uC9E7\uC740 \uC2DD\uD488\uC73C\uB85C \uAD6C\uC131\uB418\uC5B4 '\uC801\uC2DC \uC801\uB7C9 \uBC1C\uC8FC'\uAC00 \uC218\uC775\uC131\uC758 \uD575\uC2EC \uC694\uC778."),
      bulletItem("\uD3B8\uC758\uC810 1\uAC1C \uB9E4\uC7A5 \uD3C9\uADE0 \uCDE8\uAE09 SKU: 4,000~5,000\uAC1C"),
      bulletItem("\uB9E4\uC77C \uC544\uCE68 \uD544\uC694 \uD488\uBAA9 \uC120\uBCC4 \uBC0F \uBC1C\uC8FC \uC218\uB7C9 \uACB0\uC815\uC5D0 \uC18C\uC694\uB418\uB294 \uC2DC\uAC04: \uD3C9\uADE0 1~2\uC2DC\uAC04/\uC77C"),
      bulletItem("\uC6D4 \uAE30\uC900 30~60\uC2DC\uAC04, \uC2DC\uAE09 1\uB9CC\uC6D0 \uD658\uC0B0 \uC2DC 30~60\uB9CC\uC6D0\uC758 \uAE30\uD68C\uBE44\uC6A9 \uBC1C\uC0DD"),

      oMarker("\u3147 \uD3D0\uAE30\uC728 \uBB38\uC81C: ", "\uB0A0\uC528, \uC9C0\uC5ED \uD589\uC0AC, \uAE09\uC5EC\uC77C \uB4F1 \uC678\uBD80 \uC694\uC778\uC5D0 \uC758\uD55C \uC218\uC694 \uBCC0\uB3D9\uC744 \uC218\uC791\uC5C5\uC73C\uB85C \uC608\uCE21\uD558\uAE30 \uC5B4\uB824\uC6CC \uC2DD\uD488\uB958 \uC911\uC2EC \uD3C9\uADE0 5~6% \uC218\uC900\uC758 \uD3D0\uAE30 \uC190\uC2E4 \uBC1C\uC0DD. \uC6D4 \uB9E4\uC785\uC561 800\uB9CC\uC6D0 \uAE30\uC900 \uC6D4 40~50\uB9CC\uC6D0, \uC5F0\uAC04 480~600\uB9CC\uC6D0\uC758 \uC9C1\uC811 \uC190\uC2E4."),

      oMarker("\u3147 \uAE30\uC874 \uC2A4\uB9C8\uD2B8\uBC1C\uC8FC \uC2DC\uC2A4\uD15C\uC758 \uD55C\uACC4: ", "BGF\uB9AC\uD14C\uC77C\uC774 \uC81C\uACF5\uD558\uB294 \uBCF8\uC0AC \uC2A4\uB9C8\uD2B8\uBC1C\uC8FC \uC2DC\uC2A4\uD15C\uC740 \uB2E8\uC21C \uC774\uB3D9\uD3C9\uADE0(MA) \uAE30\uBC18\uC73C\uB85C \uB0A0\uC528\uB7C5\uD589\uC0AC\uB7C5\uAE09\uC5EC\uC77C\uB7C5\uC694\uC77C \uD328\uD134 \uB4F1 \uBCF5\uD569 \uC678\uBD80 \uC694\uC778\uC744 \uBC18\uC601\uD558\uC9C0 \uBABB\uD568. \uD2B9\uD788 \uAC04\uD5D0\uC218\uC694(\uB9E4\uC77C \uD314\uB9AC\uC9C0 \uC54A\uB294 \uC0C1\uD488) \uCC98\uB9AC \uC54C\uACE0\uB9AC\uC998\uC774 \uC5C6\uC5B4 \uC7AC\uACE0 \uACFC\uC789 \uB610\uB294 \uACB0\uD488\uC774 \uBC18\uBCF5\uB428."),

      subTitle("1-2. \uBB38\uC81C \uD574\uACB0\uC744 \uC704\uD55C \uCC3D\uC5C5 \uC544\uC774\uD15C\uC758 \uAC1C\uBC1C \uD544\uC694\uC131"),

      oMarker("\u3147 \uC2DC\uC7A5 \uB0B4 \uC194\uB8E8\uC158 \uACF5\uBC31: ", "\uD604\uC7AC CU \uD3B8\uC758\uC810 \uC804\uC6A9 \uC678\uBD80 AI \uBC1C\uC8FC \uC194\uB8E8\uC158\uC740 \uC2DC\uC7A5\uC5D0 \uC874\uC7AC\uD558\uC9C0 \uC54A\uC74C. BGF \uB9AC\uD14C\uC77C\uC758 \uBC1C\uC8FC \uC2DC\uC2A4\uD15C\uC740 '\uB125\uC0AC\uD06C\uB85C(Nexacro)' \uAE30\uBC18\uC758 \uD3D0\uC1C4\uD615 \uD50C\uB7AB\uD3FC\uC73C\uB85C \uC678\uBD80 API\uAC00 \uACF5\uAC1C\uB418\uC9C0 \uC54A\uC544 \uC11C\uB4DC\uD30C\uD2F0\uAC00 AI \uBC1C\uC8FC \uC194\uB8E8\uC158\uC744 \uAD6C\uD604\uD558\uAE30 \uADF9\uD788 \uC5B4\uB824\uC6C0."),
      oMarker("\u3147 \uC2E4\uC99D \uAE30\uBC18 \uAC1C\uBC1C \uD544\uC694\uC131: ", "\uCC3D\uC5C5\uC790\uB294 CU \uD3B8\uC758\uC810 3\uAC1C \uC810\uD3EC\uB97C \uC9C1\uC811 \uC6B4\uC601\uD558\uBA70 \uBC1C\uC8FC \uBE44\uD6A8\uC728 \uBB38\uC81C\uB97C \uCCB4\uAC10, 10\uAC1C\uC6D4\uC758 \uB3C5\uC790 \uAC1C\uBC1C\uB85C BGF \uB125\uC0AC\uD06C\uB85C \uB0B4\uBD80 API\uB97C \uC5ED\uBD84\uC11D\uD558\uC5EC AI \uC790\uB3D9\uBC1C\uC8FC \uC2DC\uC2A4\uD15C\uC744 \uAD6C\uD604\uD558\uACE0 3\uAC1C \uB9E4\uC7A5\uC5D0\uC11C \uC2E4\uC99D \uC644\uB8CC. \uC774\uB97C \uD074\uB77C\uC6B0\uB4DC SaaS\uB85C \uC804\uD658\uD558\uBA74 \uC804\uAD6D CU \uC810\uC8FC\uB4E4\uC5D0\uAC8C \uB3D9\uC77C\uD55C \uD6A8\uACFC\uB97C \uC81C\uACF5 \uAC00\uB2A5."),

      new Paragraph({ children: [new PageBreak()] }),

      // ══════════ 섹션 4: 실현가능성 ══════════
      sectionTitle("2. \uC2E4\uD604\uAC00\uB2A5\uC131 (Solution)"),

      subTitle("2-1. \uCC3D\uC5C5 \uC544\uC774\uD15C\uC758 \uAC1C\uBC1C \uACC4\uD68D"),

      oMarker("\u3147 \uC0B0\uCD9C\uBB3C \uAC1C\uC694: ", "\uBCF8 \uC0AC\uC5C5\uC758 \uD575\uC2EC \uC0B0\uCD9C\uBB3C\uC740 CU \uD3B8\uC758\uC810 AI \uC790\uB3D9\uBC1C\uC8FC SaaS \uD50C\uB7AB\uD3FC(\uC6F9 \uC11C\uBE44\uC2A4 1\uAC1C)\uACFC \uC2E0\uADDC \uB9E4\uC7A5 \uC790\uB3D9 \uC628\uBCF4\uB529 \uC2DC\uC2A4\uD15C(1\uAC1C)\uC784. \uD604\uC7AC \uB85C\uCEEC \uD658\uACBD\uC5D0\uC11C 3\uAC1C \uB9E4\uC7A5 \uB300\uC0C1\uC73C\uB85C \uAC80\uC99D\uB41C \uC2DC\uC2A4\uD15C\uC744 \uD074\uB77C\uC6B0\uB4DC \uAE30\uBC18 SaaS\uB85C \uC804\uD658\uD558\uB294 \uAC83\uC774 \uD611\uC57D \uAE30\uAC04 \uB0B4 \uC8FC\uC694 \uACFC\uC81C."),

      oMarker("\u3147 \uD604\uC7AC \uAD6C\uD604 \uC644\uB8CC\uB41C \uD575\uC2EC \uAE30\uB2A5:"),
      bulletItem("AI \uC218\uC694\uC608\uCE21 \uC5D4\uC9C4: LightGBM + WMA \uC801\uC751\uD615 \uC559\uC0C1\uBE14, 47\uAC1C \uD53C\uCC98, 5\uAC1C \uCE74\uD14C\uACE0\uB9AC \uADF8\uB8F9"),
      bulletItem("\uBC1C\uC8FC \uC790\uB3D9\uD654: BGF Direct API + Selenium 3\uB2E8 \uD3F4\uBC31 (100% \uBC1C\uC8FC \uC131\uACF5\uB960)"),
      bulletItem("\uC218\uC694 \uD328\uD134 \uBD84\uB958: 4\uB2E8\uACC4(daily/frequent/intermittent/slow), Croston/TSB \uAC04\uD5D0\uC218\uC694 \uBAA8\uB378"),
      bulletItem("\uC678\uBD80\uC694\uC778 \uC790\uB3D9 \uC5F0\uB3D9: \uB0A0\uC528(\uAE30\uC628 4\uB2E8\uACC4, \uAC15\uC218 4\uB2E8\uACC4), \uAE09\uC5EC\uC77C, \uACF5\uD734\uC77C, 1+1/2+1 \uD589\uC0AC"),
      bulletItem("\uBA40\uD2F0\uD14C\uB10C\uD2B8 \uC6B4\uC601: \uB9E4\uC7A5\uBCC4 DB \uC644\uC804 \uACA9\uB9AC, \uC5ED\uD560 \uAE30\uBC18 \uC811\uADFC\uC81C\uC5B4(admin/viewer)"),
      bulletItem("\uC6F9 \uB300\uC2DC\uBCF4\uB4DC: \uC2E4\uC2DC\uAC04 \uBC1C\uC8FC\uD604\uD669, \uC608\uCE21\uBD84\uC11D, \uD3D0\uAE30\uBD84\uC11D, \uCE74\uCE74\uC624\uD1A1 \uC54C\uB9BC"),

      oMarker("\u3147 \uD611\uC57D \uAE30\uAC04 \uB0B4 \uCD94\uAC00 \uAC1C\uBC1C \uACC4\uD68D:"),
      bulletItem("\uD074\uB77C\uC6B0\uB4DC \uC804\uD658: AWS EC2 \uBC30\uD3EC, PostgreSQL \uB9C8\uC774\uADF8\uB808\uC774\uC158 (SQLite \u2192 \uD655\uC7A5\uC131 \uD655\uBCF4)"),
      bulletItem("\uC790\uB3D9 \uC628\uBCF4\uB529 \uC644\uC131: \uC2E0\uADDC \uB9E4\uC7A5 \uAC00\uC785 \u2192 BGF \uC778\uC99D \u2192 AI \uD559\uC2B5 \u2192 \uC790\uB3D9\uBC1C\uC8FC \uC2DC\uC791\uAE4C\uC9C0 \uBB34\uC778 \uC140\uD504\uC11C\uBE44\uC2A4"),
      bulletItem("\uBAA8\uBC14\uC77C \uCD5C\uC801\uD654: PWA \uBC18\uC751\uD615 \uB300\uC2DC\uBCF4\uB4DC (\uD604\uC7AC \uC6F9 \uC804\uC6A9 \u2192 \uC2A4\uB9C8\uD2B8\uD3F0 \uC9C0\uC6D0)"),
      bulletItem("\uBCF4\uC548 \uAC15\uD654: \uBCF4\uC548 \uAC10\uB9AC, BGF \uACC4\uC815 AES-256 \uC554\uD638\uD654, SSL/TLS"),

      subTitle("2-2. \uCC3D\uC5C5 \uC544\uC774\uD15C\uC758 \uCC28\uBCC4\uC131 \uBC0F \uACBD\uC7C1\uB825 \uD655\uBCF4 \uC804\uB7B5"),

      oMarker("\u3147 BGF \uB125\uC0AC\uD06C\uB85C Direct API \uC5F0\uB3D9 (\uD575\uC2EC \uACBD\uC7C1 \uC7A5\uBCBD): ", "BGF \uB9AC\uD14C\uC77C\uC758 \uBC1C\uC8FC \uC2DC\uC2A4\uD15C\uC740 \uB125\uC0AC\uD06C\uB85C \uAE30\uBC18\uC758 \uD3D0\uC1C4\uD615 \uD50C\uB7AB\uD3FC\uC73C\uB85C \uACF5\uC2DD API\uAC00 \uC5C6\uC74C. \uCC3D\uC5C5\uC790\uB294 10\uAC1C\uC6D4\uAC04 \uB124\uD2B8\uC6CC\uD06C \uBD84\uC11D\uC73C\uB85C \uB125\uC0AC\uD06C\uB85C \uB0B4\uBD80 REST API\uB97C \uC5ED\uBD84\uC11D\uD558\uC5EC \uC9C1\uC811 \uD638\uCD9C\uD558\uB294 \uBC29\uC2DD\uC744 \uAD6C\uD604. Selenium(\uD654\uBA74 \uC790\uB3D9\uD654) \uB300\uBE44 \uBC1C\uC8FC \uC18D\uB3C4 \uC57D 30\uBC30 \uD5A5\uC0C1. \uC774 \uAE30\uC220\uC740 \uB2E8\uAE30\uAC04 \uBCF5\uC81C\uAC00 \uC5B4\uB824\uC6B4 \uD575\uC2EC \uACBD\uC7C1 \uC7A5\uBCBD\uC784."),

      oMarker("\u3147 47\uAC1C \uD53C\uCC98 \uAE30\uBC18 AI \uC608\uCE21: ", "\uD310\uB9E4 \uC774\uB825 \uC678\uC5D0\uB3C4 \uAE30\uC628\uB7C5\uAC15\uC218\uB7C9, \uAE09\uC5EC\uC77C, \uACF5\uD734\uC77C, 1+1 \uD589\uC0AC, \uC694\uC77C\uB7C5\uC2DC\uAC04\uB300 \uD328\uD134 \uB4F1 47\uAC1C \uD53C\uCC98\uB97C LightGBM\uC774 \uD559\uC2B5. \uCE74\uD14C\uACE0\uB9AC\uBCC4(\uC2DD\uD488/\uC8FC\uB958/\uB2F4\uBC30/\uB514\uC800\uD2B8 \uB4F1 12\uC885) \uCD5C\uC801 \uC54C\uACE0\uB9AC\uC998\uC744 \uC790\uB3D9 \uC120\uD0DD\uD558\uC5EC \uB2E8\uC77C \uC54C\uACE0\uB9AC\uC998 \uB300\uBE44 \uC608\uCE21 \uC815\uD655\uB3C4 \uD5A5\uC0C1."),

      oMarker("\u3147 \uC2E4\uC99D \uAC80\uC99D \uC131\uACFC (3\uAC1C CU \uB9E4\uC7A5, 2\uAC1C\uC6D4 \uC6B4\uC601):"),

      table([
        row([
          headerCell("\uC9C0\uD45C", 3200),
          headerCell("\uC131\uACFC", 3200),
          headerCell("\uBE44\uACE0", 3238),
        ]),
        row([
          cell("\uC608\uCE21 \uC815\uD655\uB3C4 (Accuracy@1)", { width: 3200 }),
          cell("82.4%", { width: 3200, bold: true, align: AlignmentType.CENTER }),
          cell("\uC2E4\uC81C \uD310\uB9E4\uB7C9 \u00B11\uAC1C \uC774\uB0B4 \uBE44\uC728", { width: 3238 }),
        ]),
        row([
          cell("\uC608\uCE21 \uC815\uD655\uB3C4 (Accuracy@2)", { width: 3200 }),
          cell("92.7%", { width: 3200, bold: true, align: AlignmentType.CENTER }),
          cell("\uC2E4\uC81C \uD310\uB9E4\uB7C9 \u00B12\uAC1C \uC774\uB0B4 \uBE44\uC728", { width: 3238 }),
        ]),
        row([
          cell("\uBC1C\uC8FC \uC790\uB3D9\uD654\uC728", { width: 3200 }),
          cell("57.1%", { width: 3200, bold: true, align: AlignmentType.CENTER }),
          cell("7,846\uAC74 / 18,263\uAC74 \uC804\uCCB4 \uBC1C\uC8FC", { width: 3238 }),
        ]),
        row([
          cell("\uBC1C\uC8FC \uC815\uD655\uB3C4(CORRECT)", { width: 3200 }),
          cell("67.6%", { width: 3200, bold: true, align: AlignmentType.CENTER }),
          cell("\uC801\uC815 \uBC1C\uC8FC \uBE44\uC728", { width: 3238 }),
        ]),
        row([
          cell("\uAD00\uB9AC SKU", { width: 3200 }),
          cell("4,000~4,900\uAC1C/\uB9E4\uC7A5", { width: 3200, align: AlignmentType.CENTER }),
          cell("3\uAC1C \uB9E4\uC7A5 \uD3C9\uADE0", { width: 3238 }),
        ]),
      ], [3200, 3200, 3238]),

      subTitle("2-3. \uC815\uBD80\uC9C0\uC6D0\uC0AC\uC5C5\uBE44 \uC9D1\uD589 \uACC4\uD68D"),

      para([txt("< 1\uB2E8\uACC4 \uC815\uBD80\uC9C0\uC6D0\uC0AC\uC5C5\uBE44 \uC9D1\uD589\uACC4\uD68D >", { size: 20, bold: true })], { after: 80 }),
      table([
        row([
          headerCell("\uBE44\uBAA9", 1800),
          headerCell("\uC0B0\uCD9C \uADFC\uAC70", 5238),
          headerCell("\uC815\uBD80\uC9C0\uC6D0\uC0AC\uC5C5\uBE44(\uC6D0)", 2600),
        ]),
        row([
          cell("\uC678\uC8FC\uC6A9\uC5ED\uBE44", { width: 1800 }),
          cell("\u25AA AWS EC2 \uD074\uB77C\uC6B0\uB4DC \uC778\uD504\uB77C \uAD6C\uCD95 \uC678\uC8FC(1\uC2DD)", { width: 5238 }),
          cell("8,000,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uC678\uC8FC\uC6A9\uC5ED\uBE44", { width: 1800 }),
          cell("\u25AA PostgreSQL DB \uB9C8\uC774\uADF8\uB808\uC774\uC158 \uBC0F \uC11C\uBC84 \uCD5C\uC801\uD654 \uC678\uC8FC(1\uC2DD)", { width: 5238 }),
          cell("4,000,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uC678\uC8FC\uC6A9\uC5ED\uBE44", { width: 1800 }),
          cell("\u25AA \uBCF4\uC548 \uCDE8\uC57D\uC810 \uC810\uAC80 \uBC0F \uAC10\uB9AC(1\uC2DD)", { width: 5238 }),
          cell("3,000,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uC7AC\uB8CC\uBE44", { width: 1800 }),
          cell("\u25AA \uAC1C\uBC1C \uD658\uACBD \uAD6C\uC131 \uC7A5\uBE44(\uBAA8\uB2C8\uD130, \uAC1C\uBC1C\uC6A9 PC \uC5C5\uADF8\uB808\uC774\uB4DC)", { width: 5238 }),
          cell("2,000,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uC9C0\uAE09\uC218\uC218\uB8CC", { width: 1800 }),
          cell("\u25AA AWS \uD074\uB77C\uC6B0\uB4DC \uC11C\uBE44\uC2A4 \uC774\uC6A9\uB8CC(6\uAC1C\uC6D4)", { width: 5238 }),
          cell("1,800,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uC9C0\uAE09\uC218\uC218\uB8CC", { width: 1800 }),
          cell("\u25AA \uB3C4\uBA54\uC778\uB7C5SSL \uC778\uC99D\uC11C, \uAC1C\uBC1C\uB3C4\uAD6C \uB77C\uC774\uC120\uC2A4(1\uB144)", { width: 5238 }),
          cell("1,200,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uD569\uACC4", { width: 1800, bold: true, shading: LIGHT_GRAY }),
          cell("", { width: 5238, shading: LIGHT_GRAY }),
          cell("20,000,000", { width: 2600, align: AlignmentType.RIGHT, bold: true, shading: LIGHT_GRAY }),
        ]),
      ], [1800, 5238, 2600]),

      para("", { after: 120 }),
      para([txt("< 2\uB2E8\uACC4 \uC815\uBD80\uC9C0\uC6D0\uC0AC\uC5C5\uBE44 \uC9D1\uD589\uACC4\uD68D >", { size: 20, bold: true })], { after: 80 }),
      table([
        row([
          headerCell("\uBE44\uBAA9", 1800),
          headerCell("\uC0B0\uCD9C \uADFC\uAC70", 5238),
          headerCell("\uC815\uBD80\uC9C0\uC6D0\uC0AC\uC5C5\uBE44(\uC6D0)", 2600),
        ]),
        row([
          cell("\uC678\uC8FC\uC6A9\uC5ED\uBE44", { width: 1800 }),
          cell("\u25AA \uBAA8\uBC14\uC77C \uCD5C\uC801\uD654 UI/UX \uB514\uC790\uC778 \uC678\uC8FC(1\uC2DD)", { width: 5238 }),
          cell("6,000,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uC678\uC8FC\uC6A9\uC5ED\uBE44", { width: 1800 }),
          cell("\u25AA \uC790\uB3D9 \uC628\uBCF4\uB529 \uD50C\uB85C\uC6B0 QA \uC804\uBB38 \uD14C\uC2A4\uD2B8 \uC678\uC8FC(1\uC2DD)", { width: 5238 }),
          cell("2,000,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uC778\uAC74\uBE44", { width: 1800 }),
          cell("\u25AA \uD074\uB77C\uC6B0\uB4DC \uAC1C\uBC1C \uC9C0\uC6D0 \uC778\uB825 \uCC44\uC6A9(3\uAC1C\uC6D4 \u00D7 1\uBA85 \u00D7 2,000,000\uC6D0)", { width: 5238 }),
          cell("6,000,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uC9C0\uAE09\uC218\uC218\uB8CC", { width: 1800 }),
          cell("\u25AA CU \uC810\uC8FC \uCEE4\uBBA4\uB2C8\uD2F0\uB7C5SNS \uCC44\uB110 \uD64D\uBCF4\uBE44(3\uAC1C\uC6D4)", { width: 5238 }),
          cell("4,000,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uC9C0\uAE09\uC218\uC218\uB8CC", { width: 1800 }),
          cell("\u25AA \uBC95\uC778 \uC124\uB9BD \uB4F1\uAE30 \uBE44\uC6A9, \uD2B9\uD5C8 \uCD9C\uC6D0 \uC218\uC218\uB8CC(1\uAC74)", { width: 5238 }),
          cell("2,000,000", { width: 2600, align: AlignmentType.RIGHT }),
        ]),
        row([
          cell("\uD569\uACC4", { width: 1800, bold: true, shading: LIGHT_GRAY }),
          cell("", { width: 5238, shading: LIGHT_GRAY }),
          cell("20,000,000", { width: 2600, align: AlignmentType.RIGHT, bold: true, shading: LIGHT_GRAY }),
        ]),
      ], [1800, 5238, 2600]),

      new Paragraph({ children: [new PageBreak()] }),

      // ══════════ 섹션 5: 성장전략 ══════════
      sectionTitle("3. \uC131\uC7A5\uC804\uB7B5 (Scale-up)"),

      subTitle("3-1. \uACBD\uC7C1\uC0AC \uBD84\uC11D \uBC0F \uBAA9\uD45C \uC2DC\uC7A5 \uC9C4\uC785 \uC804\uB7B5"),

      oMarker("\u3147 \uACBD\uC7C1 \uD658\uACBD \uBD84\uC11D:"),
      table([
        row([
          headerCell("\uAD6C\uBD84", 1800),
          headerCell("\uC8FC\uC694 \uB0B4\uC6A9", 2400),
          headerCell("\uD55C\uACC4\uC810", 2600),
          headerCell("\uC624\uB354\uD54F AI \uC6B0\uC704", 2838),
        ]),
        row([
          cell("BGF \uBCF8\uC0AC\n\uC2A4\uB9C8\uD2B8\uBC1C\uC8FC", { width: 1800, fontSize: 16 }),
          cell("CU \uD3B8\uC758\uC810 \uAE30\uBCF8 \uC81C\uACF5", { width: 2400, fontSize: 16 }),
          cell("\uB2E8\uC21C \uC774\uB3D9\uD3C9\uADE0, \uC678\uBD80\uC694\uC778 \uBBF8\uBC18\uC601", { width: 2600, fontSize: 16 }),
          cell("LightGBM \uC559\uC0C1\uBE14 + 47\uAC1C \uD53C\uCC98", { width: 2838, fontSize: 16 }),
        ]),
        row([
          cell("\uC77C\uBC18 \uC7AC\uACE0\uAD00\uB9AC SaaS", { width: 1800, fontSize: 16 }),
          cell("POS \uC5F0\uB3D9 \uC7AC\uACE0\uAD00\uB9AC", { width: 2400, fontSize: 16 }),
          cell("CU \uBC1C\uC8FC \uC2DC\uC2A4\uD15C \uC5F0\uB3D9 \uBD88\uAC00", { width: 2600, fontSize: 16 }),
          cell("BGF Direct API \uB3C5\uC810 \uAD6C\uD604", { width: 2838, fontSize: 16 }),
        ]),
        row([
          cell("CU \uC804\uC6A9 \uC678\uBD80 AI", { width: 1800, fontSize: 16 }),
          cell("\uD604\uC7AC \uC2DC\uC7A5\uC5D0 \uC5C6\uC74C", { width: 2400, fontSize: 16 }),
          cell("-", { width: 2600, fontSize: 16 }),
          cell("\uC120\uC810 \uC6B0\uC704 (\uC720\uC77C\uD55C \uC194\uB8E8\uC158)", { width: 2838, fontSize: 16 }),
        ]),
      ], [1800, 2400, 2600, 2838]),

      oMarker("\u3147 \uC2DC\uC7A5 \uADDC\uBAA8 \uBC0F \uC9C4\uC785 \uC804\uB7B5:"),
      bulletItem("TAM (\uC804\uCCB4 \uD3B8\uC758\uC810): 55,000\uAC1C \u00D7 49,000\uC6D0/\uC6D4 = \uC57D 27\uC5B5\uC6D0/\uC6D4 (\uC5F0 324\uC5B5\uC6D0)"),
      bulletItem("SAM (CU \uD3B8\uC758\uC810): 17,000\uAC1C \u00D7 49,000\uC6D0/\uC6D4 = \uC57D 8.3\uC5B5\uC6D0/\uC6D4 (\uC5F0 100\uC5B5\uC6D0)"),
      bulletItem("SOM 1\uB144 \uBAA9\uD45C: 100\uAC1C \u00D7 49,000\uC6D0/\uC6D4 = \uC57D 490\uB9CC\uC6D0/\uC6D4"),

      subTitle("3-2. \uCC3D\uC5C5 \uC544\uC774\uD15C\uC758 \uBE44\uC988\uB2C8\uC2A4 \uBAA8\uB378(\uC218\uC775\uD654 \uBAA8\uB378)"),

      oMarker("\u3147 \uC8FC \uC218\uC785\uC6D0 \u2013 \uC6D4 \uAD6C\uB3C5 SaaS:"),
      bulletItem("\uC6D4\uAC04 \uD50C\uB79C: 49,000\uC6D0/\uC6D4/\uB9E4\uC7A5"),
      bulletItem("\uC5F0\uAC04 \uD50C\uB79C: 39,000\uC6D0/\uC6D4/\uB9E4\uC7A5 (20% \uD560\uC778, \uC5F0 468,000\uC6D0)"),
      bulletItem("\uD3B8\uC758\uC810 \uC810\uC8FC \uD3C9\uADE0 \uC6D4 \uB9E4\uC785\uC561 800\uB9CC\uC6D0 \uAE30\uC900 ROI \uC57D 4.7\uBC30 (\uAD6C\uB3C5\uB8CC \uB300\uBE44 \uC808\uAC10\uC561 \uCD94\uC815)"),

      subTitle("3-3. \uC0AC\uC5C5 \uD655\uC7A5\uC744 \uC704\uD55C \uD22C\uC790\uC720\uCE58(\uC790\uAE08\uD655\uBCF4) \uC804\uB7B5"),

      oMarker("\u3147 \uC9C0\uC6D0\uC0AC\uC5C5\uBE44 \uD65C\uC6A9: ", "\uC608\uBE44\uCC3D\uC5C5\uD328\uD0A4\uC9C0 \uC9C0\uC6D0\uAE08\uC73C\uB85C \uD074\uB77C\uC6B0\uB4DC \uC804\uD658 \uBC0F \uC628\uBCF4\uB529 \uC790\uB3D9\uD654 \uC644\uC131 \u2192 \uC720\uB8CC \uAC00\uC785 \uAC00\uB2A5\uD55C \uC644\uC131\uB41C SaaS \uD615\uD0DC \uAD6C\uCD95"),
      oMarker("\u3147 \uC2DC\uB4DC \uD22C\uC790 \uBAA9\uD45C: ", "\uC774\uCC9C\uB7C5\uACBD\uAE30 \uC9C0\uC5ED 20\uAC1C \uB9E4\uC7A5 \uB808\uD37C\uB7F0\uC2A4 \uD655\uBCF4 \uD6C4, \uACBD\uAE30\uCC3D\uC870\uACBD\uC81C\uD601\uC2E0\uC13C\uD130 \uB4F1 \uD22C\uC790 \uB124\uD2B8\uC6CC\uD06C \uC5F0\uACC4\uB97C \uD1B5\uD574 \uC2DC\uB4DC \uD22C\uC790 \uC720\uCE58 (\uBAA9\uD45C: 2\uC5B5\uC6D0, 2026\uB144 \uD558\uBC18\uAE30)"),
      oMarker("\u3147 \uD6C4\uC18D \uC131\uC7A5: ", "CU \uAC00\uB9F9\uBCF8\uBD80 MOU \uCCB4\uACB0 \uD6C4 \uCC44\uB110 \uD30C\uD2B8\uB108\uC2ED \uAE30\uBC18 \uAE09\uC131\uC7A5 \u2192 Series A \uC900\uBE44 (2027~2028\uB144)"),

      subTitle("3-4. \uC0AC\uC5C5 \uC804\uCCB4 \uB85C\uB4DC\uB9F5 \uBC0F \uC911\uC7A5\uAE30 \uC0AC\uD68C\uC801 \uAC00\uCE58"),

      para([txt("< \uC0AC\uC5C5\uCD94\uC9C4 \uC77C\uC815(\uC804\uCCB4 \uC0AC\uC5C5\uB2E8\uACC4) >", { size: 20, bold: true })], { after: 80 }),
      table([
        row([
          headerCell("\uAD6C\uBD84", 600),
          headerCell("\uCD94\uC9C4 \uB0B4\uC6A9", 3000),
          headerCell("\uCD94\uC9C4 \uAE30\uAC04", 1400),
          headerCell("\uC138\uBD80 \uB0B4\uC6A9", 4638),
        ]),
        row([
          cell("1", { width: 600, align: AlignmentType.CENTER }),
          cell("\uD074\uB77C\uC6B0\uB4DC SaaS \uC804\uD658", { width: 3000 }),
          cell("26\uB144 Q2", { width: 1400, align: AlignmentType.CENTER }),
          cell("AWS EC2 \uBC30\uD3EC, PostgreSQL \uB9C8\uC774\uADF8\uB808\uC774\uC158, \uC790\uB3D9 \uC628\uBCF4\uB529 \uC644\uC131", { width: 4638, fontSize: 16 }),
        ]),
        row([
          cell("2", { width: 600, align: AlignmentType.CENTER }),
          cell("\uC774\uCC9C \uC9C0\uC5ED \uB808\uD37C\uB7F0\uC2A4 \uD655\uBCF4", { width: 3000 }),
          cell("26\uB144 Q2~Q3", { width: 1400, align: AlignmentType.CENTER }),
          cell("CU \uC810\uC8FC \uCEE4\uBBA4\uB2C8\uD2F0 \uBCA0\uD0C0 \uC720\uCE58, \uC720\uB8CC \uAD6C\uB3C5 20\uAC1C \uB2EC\uC131", { width: 4638, fontSize: 16 }),
        ]),
        row([
          cell("3", { width: 600, align: AlignmentType.CENTER }),
          cell("\uACBD\uAE30 \uB0A8\uBD80 \uD655\uC7A5", { width: 3000 }),
          cell("26\uB144 Q4", { width: 1400, align: AlignmentType.CENTER }),
          cell("\uC218\uC6D0\uB7C5\uC131\uB0A8\uB7C5\uC6A9\uC778 \uB4F1 \uACBD\uAE30 \uB0A8\uBD80 50\uAC1C \uD655\uC7A5", { width: 4638, fontSize: 16 }),
        ]),
        row([
          cell("4", { width: 600, align: AlignmentType.CENTER }),
          cell("\uC804\uAD6D \uD655\uC7A5 \uBC0F \uC2DC\uB4DC \uD22C\uC790", { width: 3000 }),
          cell("27\uB144", { width: 1400, align: AlignmentType.CENTER }),
          cell("\uC804\uAD6D 100\uAC1C, \uC2DC\uB4DC \uD22C\uC790 \uC720\uCE58, CU \uBCF8\uC0AC \uD611\uC758", { width: 4638, fontSize: 16 }),
        ]),
        row([
          cell("5", { width: 600, align: AlignmentType.CENTER }),
          cell("\uD0C0 \uBE0C\uB79C\uB4DC \uD655\uC7A5", { width: 3000 }),
          cell("28\uB144+", { width: 1400, align: AlignmentType.CENTER }),
          cell("GS25\uB7C5\uC138\uBE10 \uBC84\uC804 \uAC1C\uBC1C, 500\uAC1C+ \uB9E4\uC7A5, Series A", { width: 4638, fontSize: 16 }),
        ]),
      ], [600, 3000, 1400, 4638]),

      para("", { after: 80 }),
      oMarker("\u3147 \uC911\uC7A5\uAE30 \uC0AC\uD68C\uC801 \uAC00\uCE58: ", "\uC804\uAD6D CU \uC810\uC8FC(\uAC1C\uC778\uC0AC\uC5C5\uC790)\uC758 \uACBD\uC601 \uD6A8\uC728\uD654\uB97C \uD1B5\uD574 \uC18C\uC0C1\uACF5\uC778\uC758 \uC218\uC775\uC131 \uAC1C\uC120\uC5D0 \uAE30\uC5EC. \uBC1C\uC8FC \uC790\uB3D9\uD654\uB85C \uC808\uAC10\uB41C \uC2DC\uAC04\uC744 \uACE0\uAC1D \uC11C\uBE44\uC2A4\uB7C5\uB9E4\uC7A5 \uAD00\uB9AC\uC5D0 \uC7AC\uD22C\uC790\uD558\uC5EC \uC9C0\uC5ED \uC0C1\uAD8C \uD65C\uC131\uD654 \uC9C0\uC6D0. AI \uB3C4\uC785 \uC7A5\uBCBD\uC744 \uB0AE\uCDB0 \uAE30\uC220 \uC18C\uC678 \uACC4\uCE35\uC778 \uD3B8\uC758\uC810 \uC810\uC8FC\uC5D0\uAC8C AI \uD61C\uD0DD \uC81C\uACF5."),

      new Paragraph({ children: [new PageBreak()] }),

      // ══════════ 섹션 6: 팀구성 ══════════
      sectionTitle("4. \uD300\uAD6C\uC131 (Team)"),

      subTitle("4-1. \uB300\uD45C\uC790\uC758 \uBCF4\uC720 \uC5ED\uB7C9"),

      oMarker("\u3147 \uB3C4\uBA54\uC778 \uC804\uBB38\uC131: ", "CU \uD3B8\uC758\uC810 3\uAC1C \uC810\uD3EC \uC9C1\uC811 \uC6B4\uC601 3\uB144 \uC774\uC0C1. \uBC1C\uC8FC \uCD5C\uC801\uD654 \uD544\uC694\uC131\uC744 \uD604\uC7A5\uC5D0\uC11C \uC9C1\uC811 \uCCB4\uAC10\uD558\uACE0 \uBB38\uC81C \uC815\uC758 \uBC0F \uD574\uACB0\uCC45 \uC124\uACC4. \uB3D9\uC77C \uBB38\uC81C\uB97C \uAC00\uC9C4 \uC804\uAD6D \uD3B8\uC758\uC810 \uC810\uC8FC\uB4E4\uC758 \uD398\uC778\uD3EC\uC778\uD2B8\uB97C \uC815\uD655\uD788 \uC774\uD574."),
      oMarker("\u3147 \uAE30\uC220 \uC5ED\uB7C9: ", "Python\uB7C5Flask\uB7C5LightGBM \uAE30\uBC18 AI \uC2DC\uC2A4\uD15C \uB3C5\uC790 \uAC1C\uBC1C 10\uAC1C\uC6D4 (12\uB9CC\uC904, 337\uAC1C \uD30C\uC77C). BGF \uB125\uC0AC\uD06C\uB85C \uB0B4\uBD80 API \uC5ED\uBD84\uC11D\uC73C\uB85C \uACBD\uC7C1\uC0AC \uBD88\uAC00 \uAE30\uC220 \uD655\uBCF4. \uD14C\uC2A4\uD2B8 \uC8FC\uB3C4 \uAC1C\uBC1C(TDD) \uC801\uC6A9, 3,700+ \uD14C\uC2A4\uD2B8\uCF00\uC774\uC2A4\uB85C \uD488\uC9C8 \uAD00\uB9AC. 5-Layer \uD074\uB9B0 \uC544\uD0A4\uD14D\uCC98 \uC124\uACC4."),
      oMarker("\u3147 \uC2E4\uC99D \uC131\uACFC: ", "\uBCF8\uC778 \uC6B4\uC601 3\uAC1C CU \uB9E4\uC7A5\uC5D0\uC11C \uC57D 2\uAC1C\uC6D4\uAC04 \uC2DC\uC2A4\uD15C \uC9C1\uC811 \uC6B4\uC601. \uC608\uCE21 \uC815\uD655\uB3C4 82.4%, \uBC1C\uC8FC \uC790\uB3D9\uD654\uC728 57.1% \uB2EC\uC131. DB \uADDC\uBAA8: \uC77C\uBCC4 \uD310\uB9E4 100,112\uAC74, \uC608\uCE21 \uB85C\uADF8 72,779\uAC74, \uBC1C\uC8FC \uCD94\uC801 8,099\uAC74."),

      para([txt("< \uD300 \uAD6C\uC131(\uC548) >", { size: 20, bold: true })], { after: 80 }),
      table([
        row([
          headerCell("\uAD6C\uBD84", 600),
          headerCell("\uC9C1\uC704", 1200),
          headerCell("\uB2F4\uB2F9\uC5C5\uBB34", 2400),
          headerCell("\uBCF4\uC720 \uC5ED\uB7C9(\uACBD\uB825 \uBC0F \uD559\uB825 \uB4F1)", 3638),
          headerCell("\uAD6C\uC131 \uC0C1\uD0DC", 1800),
        ]),
        row([
          cell("1", { width: 600, align: AlignmentType.CENTER }),
          cell("CEO\uB7C5CTO", { width: 1200 }),
          cell("\uAE30\uC220\uAC1C\uBC1C \uCD1D\uAD04, \uC804\uB7B5 \uC218\uB9BD, \uB9E4\uC7A5 \uC6B4\uC601", { width: 2400, fontSize: 16 }),
          cell("Python\uB7C5ML\uB7C5Flask \uD480\uC2A4\uD0DD, CU \uD3B8\uC758\uC810 \uC6B4\uC601 3\uB144+", { width: 3638, fontSize: 16 }),
          cell("\uC644\uB8CC", { width: 1800, align: AlignmentType.CENTER }),
        ]),
        row([
          cell("2", { width: 600, align: AlignmentType.CENTER }),
          cell("CTO(\uACF5\uB3D9\uB300\uD45C)", { width: 1200, fontSize: 16 }),
          cell("\uD074\uB77C\uC6B0\uB4DC \uC778\uD504\uB77C, \uBC31\uC5D4\uB4DC \uD655\uC7A5", { width: 2400, fontSize: 16 }),
          cell("Python\uB7C5AWS\uB7C5DevOps \uACBD\uB825 5\uB144+", { width: 3638, fontSize: 16 }),
          cell("\uC608\uC815('26.Q2)", { width: 1800, align: AlignmentType.CENTER }),
        ]),
        row([
          cell("3", { width: 600, align: AlignmentType.CENTER }),
          cell("\uC601\uC5C5\uD300\uC7A5", { width: 1200 }),
          cell("\uD3B8\uC758\uC810 \uC810\uC8FC \uC601\uC5C5, \uACE0\uAC1D \uC628\uBCF4\uB529", { width: 2400, fontSize: 16 }),
          cell("\uC720\uD1B5\uCC44\uB110 \uC601\uC5C5 \uACBD\uB825 3\uB144+", { width: 3638, fontSize: 16 }),
          cell("\uC608\uC815('26.Q3)", { width: 1800, align: AlignmentType.CENTER }),
        ]),
      ], [600, 1200, 2400, 3638, 1800]),

      para("", { after: 120 }),
      para([txt("< \uD611\uB825 \uAE30\uAD00 \uD604\uD669 \uBC0F \uD611\uC5C5 \uBC29\uC548 >", { size: 20, bold: true })], { after: 80 }),
      table([
        row([
          headerCell("\uAD6C\uBD84", 500),
          headerCell("\uD30C\uD2B8\uB108\uBA85", 1800),
          headerCell("\uBCF4\uC720 \uC5ED\uB7C9", 2200),
          headerCell("\uD611\uC5C5 \uBC29\uC548", 3338),
          headerCell("\uD611\uB825 \uC2DC\uAE30", 1800),
        ]),
        row([
          cell("1", { width: 500, align: AlignmentType.CENTER }),
          cell("\uC774\uCC9C\uC2DC CU \uC810\uC8FC \uD611\uC758\uCCB4\n(\uD611\uC758 \uC608\uC815)", { width: 1800, fontSize: 16 }),
          cell("\uC774\uCC9C \uC9C0\uC5ED CU \uC810\uC8FC \uB124\uD2B8\uC6CC\uD06C", { width: 2200, fontSize: 16 }),
          cell("\uBCA0\uD0C0 \uD14C\uC2A4\uD130 \uBAA8\uC9D1, \uCD08\uAE30 \uB808\uD37C\uB7F0\uC2A4 \uD655\uBCF4", { width: 3338, fontSize: 16 }),
          cell("26.Q2", { width: 1800, align: AlignmentType.CENTER }),
        ]),
        row([
          cell("2", { width: 500, align: AlignmentType.CENTER }),
          cell("BGF \uB9AC\uD14C\uC77C\n(\uD611\uC758 \uC608\uC815)", { width: 1800, fontSize: 16 }),
          cell("CU \uAC00\uB9F9 17,000\uAC1C \uCC44\uB110", { width: 2200, fontSize: 16 }),
          cell("\uD30C\uD2B8\uB108\uC2ED, \uC2E0\uADDC \uC810\uC8FC \uD328\uD0A4\uC9C0 \uD611\uC758", { width: 3338, fontSize: 16 }),
          cell("26.Q4", { width: 1800, align: AlignmentType.CENTER }),
        ]),
        row([
          cell("3", { width: 500, align: AlignmentType.CENTER }),
          cell("\uD074\uB77C\uC6B0\uB4DC \uAC1C\uBC1C\uC0AC\n(\uC678\uC8FC \uC608\uC815)", { width: 1800, fontSize: 16 }),
          cell("AWS \uC778\uD504\uB77C \uAD6C\uCD95 \uC804\uBB38\uC131", { width: 2200, fontSize: 16 }),
          cell("\uD074\uB77C\uC6B0\uB4DC \uC804\uD658 \uC678\uC8FC \uAC1C\uBC1C \uD30C\uD2B8\uB108", { width: 3338, fontSize: 16 }),
          cell("26.Q2", { width: 1800, align: AlignmentType.CENTER }),
        ]),
      ], [500, 1800, 2200, 3338, 1800]),

      // 끝
      para("", { after: 200 }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [txt("- \uC774\uD558 \uC5EC\uBC31 -", { size: 18, color: "888888" })],
      }),
    ],
  }],
});

// ── Generate ──
const outputDir = path.resolve(__dirname, "..");
const outputPath = path.join(outputDir, "\uC0AC\uC5C5\uACC4\uD68D\uC11C_\uC624\uB354\uD54FAI_\uC608\uBE44\uCC3D\uC5C5\uD328\uD0A4\uC9C0_v1.docx");

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(outputPath, buffer);
  console.log("Created: " + outputPath);
  console.log("Size: " + (buffer.length / 1024).toFixed(1) + " KB");
}).catch(err => {
  console.error("Error:", err.message);
  process.exit(1);
});
