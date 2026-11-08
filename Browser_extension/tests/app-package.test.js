/**
 * app-package 模块测试：manifest.json 契约对拍、offscreen 擦除语义、
 * make_release.py 打包三场景（正常 / 缺运行时文件 / 版本不一致）、
 * package.json 镜像与零依赖、图标与 packaging/icon.png 同源。
 *
 * make_release 的打包场景在临时模拟根上端到端实跑（python -I 子进程），
 * 不触碰仓库正本；仓库正本上的真跑结果记录在模块验收里——接缝文件
 * （background.js 等，归 app-background 等 wave-2 模块）未落地前按设计拒绝打包。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const run = promisify(execFile);
const EXT = fileURLToPath(new URL("..", import.meta.url));
const REPO = fileURLToPath(new URL("../..", import.meta.url));
// Windows 是 python；POSIX 惯用 python3。本仓 CI 为 windows-latest（AGENTS.md）。
const PY = process.platform === "win32" ? "python" : "python3";

// ---------------------------------------------------------------- manifest.json

test("manifest.json：MV3 结构与 C7 常量逐项一致", () => {
  const manifest = JSON.parse(readFileSyncText(join(EXT, "manifest.json")));
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.version, "1.0.0");
  assert.equal(manifest.name, "KeyVault");
  // 权限恰为 C7 声明的四项；绝不加 clipboardRead——只写不读是隐私边界。
  assert.deepEqual(manifest.permissions, ["storage", "alarms", "clipboardWrite", "offscreen"]);
  assert.ok(!manifest.permissions.includes("clipboardRead"));
  assert.deepEqual(manifest.host_permissions, ["<all_urls>"]);
  assert.deepEqual(manifest.background, { service_worker: "background.js", type: "module" });
  assert.deepEqual(manifest.content_scripts, [
    { matches: ["<all_urls>"], js: ["content.js"], run_at: "document_idle" },
  ]);
  assert.equal(manifest.action.default_popup, "popup.html");
  assert.deepEqual(manifest.options_ui, { page: "options.html", open_in_tab: true });
  assert.deepEqual(manifest.icons, {
    "16": "icons/icon.png", "48": "icons/icon.png", "128": "icons/icon.png",
  });
  // C7 的 offscreen 判重要求 chrome.runtime.getContexts（Chrome 116 稳定）。
  assert.ok(Number(manifest.minimum_chrome_version) >= 116);
});

function readFileSyncText(path) {
  return readFileSync(path, "utf-8");
}

// ---------------------------------------------------------------- offscreen.js

// offscreen.js 顶层调用 chrome.runtime.onMessage.addListener——先立桩再动态 import。
const messageListeners = [];
let clipboardWrites = [];
let writeTextImpl = async (text) => { clipboardWrites.push(text); };

globalThis.chrome = {
  runtime: { onMessage: { addListener: (fn) => messageListeners.push(fn) } },
};
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { clipboard: { writeText: (text) => writeTextImpl(text) } },
});

test("offscreen.js：wipe-clipboard 覆盖剪贴板为指定文案并响应 ok", async () => {
  await import("../offscreen.js");
  const listener = messageListeners[0];
  assert.ok(listener, "offscreen.js 必须注册 onMessage 处理器");

  clipboardWrites = [];
  const responses = [];
  const keepOpen = listener({ target: "offscreen", kind: "wipe-clipboard" }, {}, (resp) => responses.push(resp));
  assert.equal(keepOpen, true, "异步 sendResponse 需保持消息通道开启");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(clipboardWrites, ["KeyVault: clipboard cleared"]);
  assert.deepEqual(responses, [{ ok: true }]);
});

test("offscreen.js：写入失败如实上报 error，宁可不擦也不错擦", async () => {
  const listener = messageListeners[0];
  writeTextImpl = async () => { throw new Error("Document is not focused"); };
  const responses = [];
  listener({ target: "offscreen", kind: "wipe-clipboard" }, {}, (resp) => responses.push(resp));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(responses.length, 1);
  assert.match(responses[0].error, /剪贴板擦除失败/);
  assert.match(responses[0].error, /Document is not focused/);
});

test("offscreen.js：只处理 wipe-clipboard，其余消息一律不响应", async () => {
  const listener = messageListeners[0];
  const responses = [];
  // target 不是 offscreen：属于别的上下文（SW 侧已跳过），绝不抢答
  assert.equal(listener({ target: "background", kind: "wipe-clipboard" }, {}, (r) => responses.push(r)), false);
  // target 对但 kind 不支持：本页只处理 wipe-clipboard
  assert.equal(listener({ target: "offscreen", kind: "other" }, {}, (r) => responses.push(r)), false);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(responses, []);
  assert.deepEqual(clipboardWrites.filter((text) => text !== "KeyVault: clipboard cleared"), []);
});

// ---------------------------------------------------------------- make_release.py

/** 从脚本源码提取 RUNTIME_FILES 显式清单（格式由 make_release.py 固定）。 */
function runtimeFilesOf(scriptPath) {
  const source = readFileSync(scriptPath, "utf-8");
  const match = source.match(/RUNTIME_FILES: tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/);
  assert.ok(match, "make_release.py 里必须能解析出 RUNTIME_FILES 清单");
  return [...match[1].matchAll(/"([^"]+)"/g)].map((item) => item[1]);
}

/** 在临时目录搭一个完整的模拟扩展根：清单内文件 + 干扰文件（tests/、文档、旧 zip）。 */
async function buildFakeRoot({ omitFile = null, packageVersion = "1.0.0" } = {}) {
  const root = await mkdtemp(join(tmpdir(), "kv-release-"));
  const files = runtimeFilesOf(join(EXT, "make_release.py"));
  for (const name of files) {
    if (name === omitFile) continue;
    await mkdir(dirname(join(root, name)), { recursive: true });
    await writeFile(join(root, name), `// placeholder for ${name}\n`);
  }
  await writeFile(join(root, "manifest.json"), JSON.stringify({ version: "1.0.0" }));
  await writeFile(join(root, "package.json"), JSON.stringify({ version: packageVersion }));
  // 干扰文件：绝不允许进发布包
  await mkdir(join(root, "tests"), { recursive: true });
  await writeFile(join(root, "tests", "golden.test.js"), "干扰文件\n");
  await writeFile(join(root, "README.md"), "文档不进包\n");
  await writeFile(join(root, "CONTRACTS.md"), "契约不进包\n");
  await copyFileTo(join(EXT, "make_release.py"), join(root, "make_release.py"));
  return { root, files };
}

async function copyFileTo(from, to) {
  await writeFile(to, await readFile(from));
}

async function readZipEntries(python, zipPath, workdir) {
  const reader = join(workdir, "readzip.py");
  await writeFile(reader, "import json,sys,zipfile\n"
    + "with zipfile.ZipFile(sys.argv[1]) as z: print(json.dumps(z.namelist()))\n");
  const { stdout } = await run(python, ["-I", reader, zipPath], { cwd: workdir });
  return JSON.parse(stdout);
}

test("make_release.py：模拟根端到端——产出 zip 且条目恰好等于运行时清单", async () => {
  const { root, files } = await buildFakeRoot();
  try {
    const { stdout } = await run(PY, ["-I", "make_release.py"], { cwd: root });
    const zipPath = join(root, "dist", "keyvault-browser-extension-v1.0.0.zip");
    assert.ok(existsSync(zipPath), stdout);
    assert.match(stdout, /共 \d+ 个运行时文件/);
    const entries = await readZipEntries(PY, zipPath, root);
    assert.deepEqual(entries, files); // 恰好清单：多一个少一个都算错
    assert.ok(!entries.some((name) => /^(tests|README|CONTRACTS|package\.json)/.test(name)),
      "tests、文档与 package.json 绝不进包");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("make_release.py：人为删一个运行时文件 → 报错退出且不打包", async () => {
  const { root, files } = await buildFakeRoot();
  try {
    const victim = files.find((name) => name !== "manifest.json" && name !== "package.json");
    await rm(join(root, victim));
    let failed = null;
    try {
      await run(PY, ["-I", "make_release.py"], { cwd: root });
    } catch (error) {
      failed = error;
    }
    assert.ok(failed, "缺文件必须拒绝打包");
    assert.notEqual(failed.code, 0);
    assert.match(String(failed.stderr), /运行时文件缺失/);
    assert.match(String(failed.stderr), new RegExp(victim.replaceAll(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    assert.ok(!existsSync(join(root, "dist")), "拒绝打包时不得产出 dist 目录");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("make_release.py：package.json 版本改乱 → 报版本不一致退出（新校验）", async () => {
  const { root } = await buildFakeRoot({ packageVersion: "0.3.0" });
  try {
    let failed = null;
    try {
      await run(PY, ["-I", "make_release.py"], { cwd: root });
    } catch (error) {
      failed = error;
    }
    assert.ok(failed, "版本不一致必须拒绝打包");
    assert.notEqual(failed.code, 0);
    assert.match(String(failed.stderr), /版本不一致/);
    assert.match(String(failed.stderr), /1\.0\.0/);
    assert.ok(!existsSync(join(root, "dist")));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("make_release.py：manifest 引用的每个文件都在运行时清单里", () => {
  const manifest = JSON.parse(readFileSyncText(join(EXT, "manifest.json")));
  const files = runtimeFilesOf(join(EXT, "make_release.py"));
  const referenced = [
    manifest.background.service_worker,
    ...manifest.content_scripts.flatMap((script) => script.js),
    manifest.action.default_popup,
    manifest.options_ui.page,
    ...Object.values(manifest.icons),
  ];
  for (const name of referenced) {
    assert.ok(files.includes(name), `manifest 引用的 ${name} 必须在 RUNTIME_FILES 清单里`);
  }
});

// ---------------------------------------------------------------- package.json

test("package.json：1.0.0 镜像、ESM、零 npm 依赖、test=node --test", () => {
  const pkg = JSON.parse(readFileSyncText(join(EXT, "package.json")));
  assert.equal(pkg.version, "1.0.0", "版本镜像 manifest.json（make_release.py 校验一致）");
  assert.equal(pkg.type, "module");
  assert.equal(pkg.scripts.test, "node --test");
  for (const key of ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies"]) {
    assert.ok(!(key in pkg), `package.json 不得出现 ${key}——零 npm 依赖是仓的铁律`);
  }
});

// ---------------------------------------------------------------- 图标同源

test("icons/icon.png 与 packaging/icon.png 同源（md5 一致）", () => {
  const md5 = (path) => createHash("md5").update(readFileSync(path)).digest("hex");
  assert.equal(md5(join(EXT, "icons", "icon.png")), md5(join(REPO, "packaging", "icon.png")));
});
