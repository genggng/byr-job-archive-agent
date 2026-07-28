#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


function parseArgs(argv) {
  const args = {};
  for (let index = 2; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--input" || token === "--output" || token === "--preview-dir") {
      args[token.slice(2)] = argv[index + 1];
      index += 1;
    }
  }
  if (!args.input || !args.output) {
    throw new Error("Usage: build_byr_job_index.mjs --input state.json --output index.xlsx");
  }
  return args;
}


function safeDate(value) {
  if (!value) return null;
  const wallClock = String(value).match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/,
  );
  // Excel has no timezone-aware cell type. Preserve the China wall-clock
  // components instead of converting +08:00 timestamps to UTC.
  const date = wallClock
    ? new Date(Date.UTC(
      Number(wallClock[1]),
      Number(wallClock[2]) - 1,
      Number(wallClock[3]),
      Number(wallClock[4]),
      Number(wallClock[5]),
      Number(wallClock[6] ?? 0),
    ))
    : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}


function sortPosts(posts) {
  return [...posts].sort((left, right) => {
    const leftTime = safeDate(left.published_at)?.getTime() ?? 0;
    const rightTime = safeDate(right.published_at)?.getTime() ?? 0;
    if (leftTime !== rightTime) return rightTime - leftTime;
    return (right.article_number ?? 0) - (left.article_number ?? 0);
  });
}


function addSummarySheet(sheet, postCount) {
  sheet.showGridLines = false;

  sheet.getRange("A1:H1").merge();
  sheet.getRange("A1").values = [["北邮人论坛就业版面归档"]];
  sheet.getRange("A1:H1").format = {
    fill: "#123B5D",
    font: { bold: true, color: "#FFFFFF", size: 18 },
    verticalAlignment: "center",
  };
  sheet.getRange("A1:H1").format.rowHeight = 34;

  sheet.getRange("A3:B8").values = [
    ["指标", "当前值"],
    ["归档帖子总数", null],
    ["识别为招聘帖", null],
    ["招聘信息专版", null],
    ["毕业生找工作", null],
    ["跳槽就业", null],
  ];
  const upperBound = Math.max(postCount + 1, 2);
  sheet.getRange("B4:B8").formulas = [
    [`=COUNTA('帖子索引'!$A$2:$A$${upperBound})`],
    [`=COUNTIF('帖子索引'!$B$2:$B$${upperBound},"是")`],
    [`=COUNTIF('帖子索引'!$C$2:$C$${upperBound},"JobInfo")`],
    [`=COUNTIF('帖子索引'!$C$2:$C$${upperBound},"Job")`],
    [`=COUNTIF('帖子索引'!$C$2:$C$${upperBound},"Jump")`],
  ];
  sheet.getRange("A3:B3").format = {
    fill: "#2B6F8F",
    font: { bold: true, color: "#FFFFFF" },
  };
  sheet.getRange("A3:B8").format.borders = {
    preset: "inside",
    style: "thin",
    color: "#D8E2E8",
  };
  sheet.getRange("A4:A8").format.fill = "#EEF5F8";
  sheet.getRange("B4:B8").format.numberFormat = "#,##0";
  sheet.getRange("A3:B8").format.rowHeight = 23;

  sheet.getRange("D3:H3").merge();
  sheet.getRange("D3").values = [["使用说明"]];
  sheet.getRange("D3:H3").format = {
    fill: "#2B6F8F",
    font: { bold: true, color: "#FFFFFF" },
  };
  sheet.getRange("D4:H8").merge();
  sheet.getRange("D4").values = [[
    "“帖子索引”记录三个就业版面的所有归档帖子。是否招聘、公司、岗位、地点、届别、联系方式等字段由脚本自动提取，适合筛选但仍应以 Markdown 原文为准。重复运行脚本时，已存在且内容未变化的帖子不会重写；最近一小段帖子会复查，以捕捉发帖人的后续编辑。",
  ]];
  sheet.getRange("D4:H8").format = {
    fill: "#F5F8FA",
    font: { color: "#23313B" },
    wrapText: true,
    verticalAlignment: "top",
  };

  sheet.getRange("A10:H10").merge();
  sheet.getRange("A10").values = [[
    `索引生成时间：${new Date().toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}`,
  ]];
  sheet.getRange("A10:H10").format = {
    font: { italic: true, color: "#5E6B73" },
  };

  sheet.getRange("A:H").format.font = { name: "Aptos", size: 10 };
  sheet.getRange("A:A").format.columnWidth = 22;
  sheet.getRange("B:B").format.columnWidth = 15;
  sheet.getRange("C:C").format.columnWidth = 3;
  sheet.getRange("D:H").format.columnWidth = 15;
  sheet.freezePanes.freezeRows(1);
}


function addPostsSheet(sheet, posts) {
  sheet.showGridLines = false;

  const headers = [
    "唯一键",
    "是否招聘",
    "版面",
    "版面名称",
    "文章编号",
    "标题",
    "作者",
    "公司/机构",
    "招聘类型",
    "岗位类别",
    "地点",
    "发布时间",
    "毕业届别",
    "学历要求",
    "实习要求",
    "经验要求",
    "邮箱",
    "其他联系方式",
    "投递/相关链接",
    "内容摘要",
    "Markdown 相对路径",
    "正文字符数",
    "抓取完整",
    "首次归档",
    "最后检查",
    "内容哈希",
  ];

  const rows = posts.map((post) => [
    post.key ?? "",
    post.is_recruitment ? "是" : "否",
    post.board ?? "",
    post.board_name ?? "",
    post.article_number ?? null,
    post.title ?? "",
    post.author ?? "",
    post.organization ?? "",
    post.recruitment_type ?? "",
    post.role_category ?? "",
    post.locations ?? "",
    safeDate(post.published_at),
    post.cohorts ?? "",
    post.education ?? "",
    post.internship_requirement ?? "",
    post.experience_requirement ?? "",
    post.emails ?? "",
    post.contacts ?? "",
    post.application_urls ?? "",
    post.summary ?? "",
    post.markdown_path ?? "",
    post.character_count ?? 0,
    post.capture_complete ? "是" : "否",
    safeDate(post.first_archived_at),
    safeDate(post.last_checked_at),
    post.content_hash ?? "",
  ]);

  const lastRow = Math.max(rows.length + 1, 2);
  sheet.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
  if (rows.length) {
    sheet.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
  } else {
    sheet.getRangeByIndexes(1, 0, 1, headers.length).values = [
      Array(headers.length).fill(null),
    ];
  }

  const header = sheet.getRangeByIndexes(0, 0, 1, headers.length);
  header.format = {
    fill: "#123B5D",
    font: { bold: true, color: "#FFFFFF", name: "Aptos", size: 10 },
    verticalAlignment: "center",
    wrapText: true,
    borders: {
      bottom: { style: "medium", color: "#0B263C" },
    },
  };
  header.format.rowHeight = 32;

  const used = sheet.getRangeByIndexes(1, 0, Math.max(rows.length, 1), headers.length);
  used.format = {
    font: { name: "Aptos", size: 9 },
    verticalAlignment: "top",
  };
  used.format.rowHeight = 36;
  sheet.getRange(`F2:F${lastRow}`).format.wrapText = true;
  sheet.getRange(`N2:T${lastRow}`).format.wrapText = true;
  sheet.getRange(`K2:K${lastRow}`).format.wrapText = true;

  sheet.getRange(`E2:E${lastRow}`).format.numberFormat = "0";
  sheet.getRange(`L2:L${lastRow}`).format.numberFormat = "yyyy-mm-dd hh:mm";
  sheet.getRange(`V2:V${lastRow}`).format.numberFormat = "#,##0";
  sheet.getRange(`X2:Y${lastRow}`).format.numberFormat = "yyyy-mm-dd hh:mm";

  sheet.getRange(`B2:B${lastRow}`).conditionalFormats.add("containsText", {
    text: "是",
    format: { fill: "#DDF3E4", font: { color: "#176B3A", bold: true } },
  });
  sheet.getRange(`W2:W${lastRow}`).conditionalFormats.add("containsText", {
    text: "否",
    format: { fill: "#FDE7E7", font: { color: "#A12622" } },
  });

  const columnWidths = [
    17, 9, 10, 15, 11, 48, 14, 22, 16, 18, 17, 18, 16,
    18, 28, 24, 26, 22, 38, 58, 34, 12, 10, 18, 18, 18,
  ];
  columnWidths.forEach((width, index) => {
    sheet.getRangeByIndexes(0, index, lastRow, 1).format.columnWidth = width;
  });

  const tableRange = `A1:Z${lastRow}`;
  const table = sheet.tables.add(tableRange, true, "ByrJobPostsTable");
  table.style = "TableStyleMedium2";
  table.showBandedRows = true;
  table.showFilterButton = true;

  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(5);
}


function addRecruitmentSheet(sheet, posts) {
  sheet.showGridLines = false;
  const headers = [
    "发布时间",
    "版面",
    "公司/机构",
    "标题",
    "招聘类型",
    "岗位类别",
    "地点",
    "毕业届别",
    "学历要求",
    "邮箱",
    "其他联系方式",
    "Markdown 相对路径",
  ];
  const recruitments = posts.filter((post) => post.is_recruitment);
  const rows = recruitments.map((post) => [
    safeDate(post.published_at),
    post.board_name ?? post.board ?? "",
    post.organization ?? "",
    post.title ?? "",
    post.recruitment_type ?? "",
    post.role_category ?? "",
    post.locations ?? "",
    post.cohorts ?? "",
    post.education ?? "",
    post.emails ?? "",
    post.contacts ?? "",
    post.markdown_path ?? "",
  ]);
  const lastRow = Math.max(rows.length + 1, 2);

  sheet.getRange("A1:L1").values = [headers];
  sheet.getRange("A1:L1").format = {
    fill: "#123B5D",
    font: { bold: true, color: "#FFFFFF", name: "Aptos", size: 10 },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("A1:L1").format.rowHeight = 32;

  if (rows.length) {
    sheet.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
  } else {
    sheet.getRange("A2:L2").values = [Array(headers.length).fill(null)];
  }
  sheet.getRange(`A2:A${lastRow}`).format.numberFormat = "yyyy-mm-dd hh:mm";
  sheet.getRange(`A2:L${lastRow}`).format = {
    font: { name: "Aptos", size: 9 },
    verticalAlignment: "top",
  };
  sheet.getRange(`C2:L${lastRow}`).format.wrapText = true;
  sheet.getRange(`A2:L${lastRow}`).format.rowHeight = 34;

  const widths = [18, 16, 22, 52, 16, 18, 20, 16, 18, 28, 22, 36];
  widths.forEach((width, index) => {
    sheet.getRangeByIndexes(0, index, lastRow, 1).format.columnWidth = width;
  });

  const table = sheet.tables.add(`A1:L${lastRow}`, true, "RecruitmentQuickViewTable");
  table.style = "TableStyleMedium2";
  table.showBandedRows = true;
  table.showFilterButton = true;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(2);
}


async function main() {
  const args = parseArgs(process.argv);
  const state = JSON.parse(await fs.readFile(args.input, "utf8"));
  const posts = sortPosts(Object.values(state.posts ?? {}));

  const workbook = Workbook.create();
  // Create all formula-referenced sheets before writing cross-sheet formulas.
  const summarySheet = workbook.worksheets.add("汇总");
  const recruitmentSheet = workbook.worksheets.add("招聘帖速览");
  const postsSheet = workbook.worksheets.add("帖子索引");
  addSummarySheet(summarySheet, posts.length);
  addRecruitmentSheet(recruitmentSheet, posts);
  addPostsSheet(postsSheet, posts);

  await fs.mkdir(path.dirname(args.output), { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(args.output);

  if (args["preview-dir"]) {
    await fs.mkdir(args["preview-dir"], { recursive: true });
    for (const [sheetName, fileName, range] of [
      ["汇总", "summary.png", "A1:H10"],
      ["招聘帖速览", "recruitments.png", "A1:L12"],
      ["帖子索引", "posts.png", "A1:Z10"],
    ]) {
      const preview = await workbook.render({
        sheetName,
        range,
        scale: 1,
        format: "png",
      });
      await fs.writeFile(
        path.join(args["preview-dir"], fileName),
        new Uint8Array(await preview.arrayBuffer()),
      );
    }
  }

  const inspection = await workbook.inspect({
    kind: "region",
    sheetId: "帖子索引",
    range: `A1:Z${Math.min(posts.length + 1, 8)}`,
    include: "values,formulas",
    tableMaxRows: 8,
    tableMaxCols: 26,
    maxChars: 8000,
  });
  process.stdout.write(`${inspection.ndjson}\n`);

  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: "final formula error scan",
    maxChars: 5000,
  });
  process.stdout.write(`${errors.ndjson}\n`);
}


await main();
