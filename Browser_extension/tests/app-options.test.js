// app-options 单测：管理页的纯逻辑 + 机械验收。
//
// options.js 是浏览器入口，但模块级不碰 DOM/chrome（boot() 由 document 探测
// 守卫），node 直 import 即可测：分批规划（C6）、行界切块与行号映射、二进制
// 探测（scan.py:92-98 同构）、清库双门、纠正范围建议（suggest_correction_scope
// 对齐）、以及端到端「切块 → 分批 → 真引擎 scanTexts → 行号还原」往返。
//
// 机械验收（评审硬缺口，读源文件断言）：
// - 不再 import ./src/vault/db.js、不碰 deleteDatabase（整库清除走 C1#30
//   reset 鉴权消息，旧版 options.js:497-504 的动态自删清零）；
// - 无 innerHTML（动态内容全 textContent）；
// - TOTP 从新家 ./src/vault/totp.js 导入；
// - options.html 八区齐备（导航 7 区 + 详情抽屉覆盖层）。

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  chunkTextByLines,
  planScanBatches,
  isBinaryHead,
  parseTagList,
  parseHostList,
  clampAutoLock,
  formatStamp,
  decorateHits,
  checkResetGates,
  suggestScopeFor,
  planGateState,
  translateChannelError,
  formatImportResult,
  formatRestoreResult,
  summarizeScanRun,
} from "../options.js";
import { MAX_TEXTS_PER_CALL, MAX_CONTENT_CHARS, MAX_TOTAL_CHARS } from "../src/vault/scan.js";
import { Session, memoryKeyStore } from "../src/vault/crypto.js";
import { memoryBackend } from "../src/vault/memory-backend.js";
import * as store from "../src/vault/store.js";
import { scanTexts } from "../src/vault/scan.js";

const MASTER = "master-pass-8";
const LEAK = "LeakProbe-Alpha-9931XX";

// ------------------------------------------------------------- 机械验收

describe("机械验收（读源文件断言）", () => {
  const source = readFileSync(new URL("../options.js", import.meta.url), "utf8");
  const html = readFileSync(new URL("../options.html", import.meta.url), "utf8");
  // 只看真实的 import 语句（静态 from + 动态 import()），注释里提到旧路径不算数
  const imports = [...source.matchAll(/from\s+"([^"]+)"|\bimport\(\s*"([^"]+)"\s*\)/g)]
    .map((match) => match[1] ?? match[2]);

  it("不再 import ./src/vault/db.js，整库清除走 C1#30 reset 消息", () => {
    for (const specifier of imports) {
      assert.ok(!specifier.includes("db.js"), `不得 import ${specifier}`);
    }
    assert.ok(!source.includes("deleteDatabase"), "不得自删数据库");
    assert.ok(!source.includes("chrome.storage"), "不得直接碰 storage，统一走消息");
    assert.ok(source.includes('send("reset", { password'), "必须发 C1#30 reset 鉴权消息");
  });

  it("无 innerHTML——动态内容全部 textContent", () => {
    assert.ok(!/\.innerHTML\s*\+?=/.test(source), "不得赋值 innerHTML");
    assert.ok(!/\.outerHTML\s*\+?=/.test(source), "不得赋值 outerHTML");
    assert.ok(!source.includes("insertAdjacentHTML"));
    assert.ok(!/document\.write/.test(source));
  });

  it("TOTP 从新家 ./src/vault/totp.js 导入（旧家归整合批删除）", () => {
    assert.ok(imports.includes("./src/vault/totp.js"));
    for (const specifier of imports) {
      assert.ok(!specifier.includes("core/totp"), `不得 import 旧 TOTP 家：${specifier}`);
    }
  });

  it("八区齐备：导航 7 区 + 详情抽屉覆盖层 + 三个扫描输入", () => {
    for (const tab of ["dashboard", "secrets", "audit", "import", "backup", "scan", "settings"]) {
      assert.ok(html.includes(`data-tab="${tab}"`), `缺导航项 ${tab}`);
      assert.ok(html.includes(`id="tab-${tab}"`), `缺分区 ${tab}`);
    }
    assert.ok(html.includes('id="drawer"'), "缺详情抽屉（区③）");
    for (const id of ["scan-paste", "scan-file", "scan-dir-btn", "scan-dir-fallback"]) {
      assert.ok(html.includes(`id="${id}"`), `缺扫描输入 ${id}`);
    }
    assert.ok(html.includes('href="options.css"'));
    assert.ok(html.includes('src="options.js"'));
  });
});

// ------------------------------------------------------------- 分批与切块

describe("chunkTextByLines（行界切块，行号映射基础）", () => {
  it("小文本单块，lineOffset=0", () => {
    const chunks = chunkTextByLines("a\nb\nc", 1000);
    assert.equal(chunks.length, 1);
    assert.equal(chunks[0].lineOffset, 0);
    assert.equal(chunks[0].content, "a\nb\nc");
  });

  it("超限文本按行界切块且每块 ≤ 上限，行序拼回一致", () => {
    const line = "x".repeat(600);
    const content = Array.from({ length: 6 }, () => line).join("\n");
    const chunks = chunkTextByLines(content, 1300);
    assert.deepEqual(chunks.map((chunk) => chunk.lineOffset), [0, 2, 4]);
    for (const chunk of chunks) assert.ok(chunk.content.length <= 1300);
    const rejoined = chunks.flatMap((chunk) => chunk.content.split("\n")).join("\n");
    assert.equal(rejoined, content);
  });

  it("\\r\\n 行数与引擎切行一致（scan.js 同式切分）", () => {
    const chunks = chunkTextByLines("a\r\nb\r\nc", 1000);
    assert.equal(chunks.length, 1);
    assert.equal(chunks[0].content.split(/\r\n|\r|\n/).length, 3);
  });

  it("单行超上限：独立成块不截断（留给显式报错路径）", () => {
    const big = "y".repeat(MAX_CONTENT_CHARS + 1);
    const chunks = chunkTextByLines(`head\n${big}\ntail`, MAX_CONTENT_CHARS);
    assert.equal(chunks.length, 3);
    assert.equal(chunks[0].lineOffset, 0);
    assert.equal(chunks[1].content.length, MAX_CONTENT_CHARS + 1);
    assert.equal(chunks[1].lineOffset, 1);
    assert.equal(chunks[2].lineOffset, 2);
  });

  it("空串 → 单块（引擎对空内容零命中，不炸）", () => {
    const chunks = chunkTextByLines("", 100);
    assert.equal(chunks.length, 1);
    assert.equal(chunks[0].content, "");
  });
});

describe("planScanBatches（C6 分批）", () => {
  const item = (name, size) => ({ name, path: name, chunk: "x".repeat(size), lineOffset: 0 });

  it("100 条塞得下 → 单批", () => {
    const batches = planScanBatches(Array.from({ length: MAX_TEXTS_PER_CALL }, (_, i) => item(`t${i}`, 100)));
    assert.equal(batches.length, 1);
    assert.equal(batches[0].length, MAX_TEXTS_PER_CALL);
  });

  it("超 100 条 → 按条数切批", () => {
    const batches = planScanBatches(
      Array.from({ length: MAX_TEXTS_PER_CALL + 1 }, (_, i) => item(`t${i}`, 10)),
    );
    assert.deepEqual(batches.map((batch) => batch.length), [MAX_TEXTS_PER_CALL, 1]);
  });

  it("总字符超 4M → 按字符预算切批，每批总量 ≤ 4M", () => {
    const items = Array.from({ length: 200 }, (_, i) => item(`t${i}`, 100_000));
    const batches = planScanBatches(items);
    assert.ok(batches.length > 1);
    for (const batch of batches) {
      assert.ok(batch.length <= MAX_TEXTS_PER_CALL);
      const total = batch.reduce((sum, it) => sum + it.chunk.length, 0);
      assert.ok(total <= MAX_TOTAL_CHARS);
    }
  });

  it("单条超 1M 的块独占一批，不拖累其他文本", () => {
    const batches = planScanBatches([item("big", MAX_CONTENT_CHARS + 1), item("small", 10)]);
    assert.equal(batches.length, 2);
    assert.deepEqual(batches[0].map((it) => it.name), ["big"]);
    assert.deepEqual(batches[1].map((it) => it.name), ["small"]);
  });

  it("空输入 → 零批", () => {
    assert.deepEqual(planScanBatches([]), []);
  });
});

describe("isBinaryHead（scan.py:92-98 头 8KB 探测同构）", () => {
  it("UTF-8 文本（含中文）→ false", () => {
    assert.equal(isBinaryHead(new TextEncoder().encode("const x = '中文密钥';\n")), false);
  });

  it("含 \\x00 → true", () => {
    assert.equal(isBinaryHead(new Uint8Array([0x50, 0x4b, 0x00, 0x03])), true);
  });

  it("空字节串 → false（空文件当文本，扫了也白扫但不炸）", () => {
    assert.equal(isBinaryHead(new Uint8Array(0)), false);
  });
});

// ------------------------------------------------------------- 小工具

describe("小工具", () => {
  it("parseTagList：中英文逗号/顿号/分号分隔，去空白", () => {
    assert.deepEqual(parseTagList("work, prod，个人、side; "), ["work", "prod", "个人", "side"]);
    assert.deepEqual(parseTagList(""), []);
    assert.deepEqual(parseTagList(null), []);
  });

  it("parseHostList：按行、小写化、去重（C7 主机名精确匹配口径）", () => {
    assert.deepEqual(
      parseHostList("Mail.Example.com\nmail.example.com\n  \nintranet.corp"),
      ["mail.example.com", "intranet.corp"],
    );
  });

  it("clampAutoLock：负数/非数字 → 0（禁用），封顶 240", () => {
    assert.equal(clampAutoLock("15"), 15);
    assert.equal(clampAutoLock(0), 0);
    assert.equal(clampAutoLock("-3"), 0);
    assert.equal(clampAutoLock("abc"), 0);
    assert.equal(clampAutoLock("999"), 240);
  });

  it("formatStamp：ISO → YYYY-MM-DD HH:MM:SS（桌面 [:19] 截断同款）", () => {
    assert.equal(formatStamp("2026-11-09T08:30:01.123Z"), "2026-11-09 08:30:01");
    assert.equal(formatStamp(null), "");
  });
});

// ------------------------------------------------------------- 清库双门

describe("checkResetGates（整库清除双门，缺一不可）", () => {
  it("只给主密码不给 RESET → 拒在二次确认门", () => {
    assert.match(checkResetGates("pw-123456", ""), /RESET/);
    assert.match(checkResetGates("pw-123456", "reset"), /RESET/);
  });

  it("只给 RESET 不给主密码 → 拒在鉴权门", () => {
    assert.match(checkResetGates("", "RESET"), /主密码/);
  });

  it("双门齐 → 放行（真正的口令认证在 background 的 C1#30 reset）", () => {
    assert.equal(checkResetGates("pw-123456", "RESET"), null);
  });
});

// ------------------------------------------------------------- 纠正范围建议

describe("suggestScopeFor（对齐 suggest_correction_scope：键名 > 主机名）", () => {
  it("有键名 → 最窄的 keyname 纠正（正则元字符转义）", () => {
    const scope = suggestScopeFor({ keyName: "OPENAI.API_KEY(1)", sourceUrl: "" });
    assert.equal(scope.kind, "keyname");
    assert.equal(scope.pattern, "^OPENAI\\.API_KEY\\(1\\)$");
    assert.ok(scope.desc.length > 0);
  });

  it("无键名 → 回落 sourceUrl 里的主机名", () => {
    const scope = suggestScopeFor({ keyName: null, sourceUrl: "https://api.deepseek.com/v1" });
    assert.equal(scope.kind, "hostname");
    assert.equal(scope.pattern, "api.deepseek.com");
  });

  it("两者都无 → null（组默认不被静默建议）", () => {
    assert.equal(suggestScopeFor({ keyName: null, sourceUrl: "" }), null);
    assert.equal(suggestScopeFor(null), null);
  });
});

// ------------------------------------------------------------- 命中行装饰

describe("decorateHits（块内行号 → 文件真实行号）", () => {
  it("按内部块名映射回显示路径并加行偏移", () => {
    const items = [
      { name: "a.js\u0001L1", path: "a.js", lineOffset: 0 },
      { name: "a.js\u0001L1001", path: "a.js", lineOffset: 1000 },
    ];
    const hits = decorateHits([
      { textName: "a.js\u0001L1001", lineNo: 3, name: "k", platform: "openai", status: "active" },
      { textName: "a.js\u0001L1", lineNo: 2, name: "k", platform: "openai", status: "active" },
    ], items);
    assert.deepEqual(hits.map((hit) => [hit.path, hit.lineNo]), [["a.js", 1003], ["a.js", 2]]);
  });

  it("未知块名原样保留（一次映射失败不吞掉命中）", () => {
    const hits = decorateHits([{ textName: "ghost", lineNo: 5 }], []);
    assert.equal(hits[0].textName, "ghost");
    assert.equal(hits[0].lineNo, 5);
  });
});

// ------------------------------------------------------------- 编排层（三态门/结果文案/通道异常）

describe("planGateState（三态门编排，renderGate 只做渲染）", () => {
  it("background 无响应（null）→ error 门 + 重试", () => {
    const plan = planGateState(null);
    assert.equal(plan.mode, "error");
    assert.equal(plan.button, "重试");
    assert.match(plan.desc, /没有响应/);
  });

  it("status.error（含通道翻译文案）→ error 门，desc 原样透传", () => {
    const plan = planGateState({ error: "消息通道失败：x" });
    assert.equal(plan.mode, "error");
    assert.equal(plan.desc, "消息通道失败：x");
  });

  it("未初始化 → setup 门；已初始化未解锁 → unlock 门", () => {
    assert.equal(planGateState({ initialized: false, unlocked: false }).mode, "setup");
    assert.match(planGateState({ initialized: false, unlocked: false }).desc, /主密码不设恢复/);
    assert.equal(planGateState({ initialized: true, unlocked: false }).mode, "unlock");
  });

  it("已解锁 → open（门隐藏），文案不含在 plan 里", () => {
    const plan = planGateState({ initialized: true, unlocked: true });
    assert.equal(plan.mode, "open");
    assert.equal(plan.title, undefined);
  });
});

describe("translateChannelError（MV3 通道异常翻译）", () => {
  it("Error 对象取 message，附重开本页指引", () => {
    const text = translateChannelError(new Error("Extension context invalidated."));
    assert.match(text, /Extension context invalidated/);
    assert.match(text, /重新打开/);
  });

  it("非 Error 值 String 化不抛", () => {
    assert.match(translateChannelError("boom"), /boom/);
    assert.match(translateChannelError(undefined), /消息通道失败/);
  });
});

describe("formatImportResult（导入结果文案，skipped 不含值）", () => {
  it("新建/去重/跳过计数 + skipped 首条（platform·confidence·掩码预览）", () => {
    const text = formatImportResult({
      created: 2, deduped: 0, rejected: 0,
      skipped: [{ platform: "unknown", confidence: "none", preview: "sk-****c31f" }],
    });
    assert.equal(text, "新建 2 · 去重 0 · 跳过 1 · 首条：unknown · none · sk-****c31f");
    assert.ok(!text.includes("secret-value"), "文案不得携带值本身");
  });

  it("rejected > 0 时追加拒绝计数；无跳过时不带首条段", () => {
    const text = formatImportResult({ created: 1, deduped: 1, rejected: 1, skipped: [] });
    assert.equal(text, "新建 1 · 去重 1 · 跳过 0 · 拒绝 1");
  });

  it("error/缺失响应 → null（调用方走错误分支）", () => {
    assert.equal(formatImportResult({ error: "x" }), null);
    assert.equal(formatImportResult(null), null);
  });
});

describe("formatRestoreResult（恢复结果文案）", () => {
  it("新建/去重，rejected 追加", () => {
    assert.equal(formatRestoreResult({ created: 3, deduped: 1, rejected: 0 }), "恢复完成：新建 3 · 去重 1");
    assert.equal(formatRestoreResult({ created: 0, deduped: 0, rejected: 2 }), "恢复完成：新建 0 · 去重 0 · 拒绝 2");
  });

  it("error/缺失响应 → null", () => {
    assert.equal(formatRestoreResult({ error: "口令错误" }), null);
    assert.equal(formatRestoreResult(undefined), null);
  });
});

describe("summarizeScanRun（扫描结果聚合：状态行 + 错误清单）", () => {
  const collected = {
    items: [{}, {}, {}],
    skippedBinary: 2,
    skippedLarge: 1,
    readErrors: ["a.txt —— 读取失败：x"],
  };

  it("命中 > 0 → bad 配色；跳过计数只在 > 0 时出现", () => {
    const summary = summarizeScanRun(collected, { batchCount: 3, hits: [{}], failed: [] });
    assert.equal(summary.statusCls, "bad");
    assert.match(summary.statusText, /^扫描完成：1 处命中 · 3 批 \/ 3 条文本 · 跳过二进制 2 · 跳过超大文件 1$/);
    assert.deepEqual(summary.errors, ["a.txt —— 读取失败：x"]);
  });

  it("零命中 → ok 配色；被拒批次错误排在读取错误之后", () => {
    const summary = summarizeScanRun(collected, { batchCount: 4, hits: [], failed: ["b.js —— 本批被拒：单条文本最多"] });
    assert.equal(summary.statusCls, "ok");
    assert.match(summary.statusText, /^扫描完成：0 处命中 · 4 批/);
    assert.deepEqual(summary.errors, ["a.txt —— 读取失败：x", "b.js —— 本批被拒：单条文本最多"]);
  });

  it("零跳过 → 状态行不含跳过段", () => {
    const summary = summarizeScanRun(
      { items: [{}], skippedBinary: 0, skippedLarge: 0, readErrors: [] },
      { batchCount: 1, hits: [], failed: [] },
    );
    assert.equal(summary.statusText, "扫描完成：0 处命中 · 1 批 / 1 条文本");
    assert.deepEqual(summary.errors, []);
  });
});

// ------------------------------------------------------------- 端到端往返

describe("端到端：切块 → 分批 → 真引擎 scanTexts → 行号还原", () => {
  it("大文件分块后，命中的行号与原文件行号一致", async () => {
    const backend = memoryBackend();
    const session = new Session(memoryKeyStore());
    await store.setupVault(backend, MASTER);
    await store.unlock(backend, session, MASTER);
    await store.add(backend, session, {
      value: LEAK, platform: "acme", confidence: "manual",
      evidence: "测试录入", source: "manual", keyName: "E2E_KEY", origin: "manual",
    });

    const pad = Array.from({ length: 1500 }, (_, i) => `filler ${i}`).join("\n");
    const content = `${pad}\nleak line: ${LEAK}\n`; // 命中在第 1501 行
    const items = chunkTextByLines(content, 4000).map((chunk) => ({
      name: `big.txt\u0001L${chunk.lineOffset + 1}`,
      path: "big.txt",
      lineOffset: chunk.lineOffset,
      chunk: chunk.content,
    }));
    assert.ok(items.length > 1, "样例应切成多块");

    const hits = [];
    for (const batch of planScanBatches(items)) {
      const texts = batch.map((item) => ({ name: item.name, content: item.chunk }));
      hits.push(...decorateHits(await scanTexts(backend, session, texts), batch));
    }
    assert.equal(hits.length, 1);
    assert.equal(hits[0].path, "big.txt");
    assert.equal(hits[0].lineNo, 1501);
    assert.equal(hits[0].status, "active");
    assert.equal(hits[0].name, "e2e-key");
    await session.lock(); // 扫完即锁：明文不驻留内存
  });
});
