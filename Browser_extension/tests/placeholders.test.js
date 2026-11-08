/** placeholders.js 闸门测试：占位符拒绝、确定性哈希、键名拒绝清单、可枚举上下文。 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  classify, isAcceptable, isNonCredentialKey, isCredentialKeyName,
  Context, REASON_PLACEHOLDER, REASON_NOT_A_SECRET, REASON_EMPTY, REASON_TOO_LONG,
  MAX_VALUE_LEN,
} from "../src/core/placeholders.js";

const HEX32 = "0123456789abcdef0123456789abcdef";

test("占位符拒绝：sk- 模板与重复填充", () => {
  assert.deepEqual(classify("sk-xxxxxxxx"), [REASON_PLACEHOLDER, "sk- 模板"]);
  assert.deepEqual(classify("sk-your-key-here"), [REASON_PLACEHOLDER, "sk- 模板"]);
  assert.deepEqual(classify("x".repeat(20)), [REASON_PLACEHOLDER, "重复字符填充"]);
  assert.deepEqual(classify("0".repeat(16)), [REASON_PLACEHOLDER, "重复字符填充"]);
});

test("占位符拒绝：your-key / change_me / dummy / 中文 / 尖括号", () => {
  assert.deepEqual(classify("your-api-key-here"), [REASON_PLACEHOLDER, "your-key 模板"]);
  assert.deepEqual(classify("change_me"), [REASON_PLACEHOLDER, "change_me 模板"]);
  assert.deepEqual(classify("dummy-value"), [REASON_PLACEHOLDER, "dummy/sample 模板"]);
  assert.deepEqual(classify("请替换为你的密钥"), [REASON_PLACEHOLDER, "中文占位提示"]);
  assert.deepEqual(classify("<api-key-here>"), [REASON_PLACEHOLDER, "尖括号占位"]);
  assert.deepEqual(classify("insert-here"), [REASON_PLACEHOLDER, "insert-here 模板"]);
  // 模式按声明顺序先命中先胜：尖括号里的 your-api-key 先被 your-key 模板拦下
  // （与桌面 classify 实测输出逐字一致）。
  assert.deepEqual(classify("<your-api-key>"), [REASON_PLACEHOLDER, "your-key 模板"]);
});

test("占位符拒绝：变量展开嵌在中间也要拦", () => {
  assert.deepEqual(classify("${OPENAI_API_KEY}"), [REASON_PLACEHOLDER, "shell 变量展开"]);
  assert.deepEqual(
    classify("prefix-${OPENAI_API_KEY}-suffix"),
    [REASON_PLACEHOLDER, "含 shell 变量展开"],
  );
  assert.deepEqual(classify("%MY_API_KEY%"), [REASON_PLACEHOLDER, "含 Windows 环境变量展开"]);
});

test("占位符拒绝：字面空值、类型标注、裸 URL、example 域名", () => {
  assert.deepEqual(classify("none"), [REASON_PLACEHOLDER, "字面空值"]);
  assert.deepEqual(classify("str"), [REASON_PLACEHOLDER, "类型标注"]);
  assert.deepEqual(classify("https://platform.deepseek.com/api_keys"), [REASON_PLACEHOLDER, "裸 URL（不含凭据）"]);
  // example 一词先被 dummy/sample 模板拦下（模式顺序与桌面一致，实测对拍过）。
  assert.deepEqual(classify("https://api.example.com/v1"), [REASON_PLACEHOLDER, "dummy/sample 模板"]);
  // 类型标注词后面跟着别的字母不是标注（string 不是 str）
  assert.equal(classify("stringish-token-value-abc"), null);
});

test("确定性哈希形态拒绝：40/64/128 hex、UUID、纯数字", () => {
  assert.deepEqual(
    classify("0123456789abcdef0123456789abcdef01234567"),
    [REASON_NOT_A_SECRET, "纯哈希形态（40/64/128 位 hex）"],
  );
  assert.deepEqual(classify("0123456789abcdef".repeat(4)), [REASON_NOT_A_SECRET, "纯哈希形态（40/64/128 位 hex）"]);
  assert.deepEqual(classify("550e8400-e29b-41d4-a716-446655440000"), [REASON_NOT_A_SECRET, "UUID 形态"]);
  assert.deepEqual(classify("1735689600"), [REASON_NOT_A_SECRET, "纯数字"]);
  // 大写 40 位 hex 不在哈希形态之列（桌面正则无 (?i)，语义逐字一致）
  assert.equal(classify("0123456789ABCDEF0123456789ABCDEF01234567"), null);
});

test("空值与超长拒绝", () => {
  assert.deepEqual(classify(""), [REASON_EMPTY, "空值"]);
  assert.deepEqual(classify("   "), [REASON_EMPTY, "空值"]);
  assert.deepEqual(
    classify("A".repeat(MAX_VALUE_LEN + 1)),
    [REASON_TOO_LONG, `超过 ${MAX_VALUE_LEN} 字符，不像凭据`],
  );
});

test("键名拒绝清单：明确不是凭据的键名被拒", () => {
  const ctx = (keyName) => new Context({ keyName });
  assert.deepEqual(
    classify("deepseek-chat", ctx("LLM_MODEL")),
    [REASON_NOT_A_SECRET, "键名 LLM_MODEL 明确不是凭据"],
  );
  assert.equal(classify("30", ctx("AMAP_TIMEOUT"))[0], REASON_NOT_A_SECRET);
  assert.equal(classify("https://api.deepseek.com/v1", ctx("LLM_BASE_URL"))[0], REASON_NOT_A_SECRET);
  assert.equal(isNonCredentialKey("BASE_URL"), true);
  assert.equal(isNonCredentialKey("MAX_TOKENS"), true);
  assert.equal(isNonCredentialKey("DEFAULT_REGION"), true);
});

test("键名拒绝清单不误杀：AWS_ACCESS_KEY_ID / SESSION_SECRET / 不认识的键名", () => {
  // 回归守卫：桌面曾把 _id$ 写进清单，把 AWS_ACCESS_KEY_ID 一起杀了。
  assert.equal(isNonCredentialKey("AWS_ACCESS_KEY_ID"), false);
  assert.equal(isNonCredentialKey("SESSION_SECRET"), false);
  assert.equal(isNonCredentialKey("MY_VENDOR_CREDENTIAL"), false);
  assert.equal(isNonCredentialKey("OPENAI_API_KEY"), false);
  const ctx = new Context({ keyName: "AWS_ACCESS_KEY_ID" });
  assert.equal(classify("AKIAABCDEFGHIJKLMNOP", ctx), null);
});

test("凭据键名宽松判定：决定块级主机名是否传播", () => {
  assert.equal(isCredentialKeyName("LLM_API_KEY"), true);
  assert.equal(isCredentialKeyName("GITLAB_TOKEN"), true);
  assert.equal(isCredentialKeyName("DATABASE_PASSWORD"), true);
  assert.equal(isCredentialKeyName("LLM_MODEL"), false);
  assert.equal(isCredentialKeyName("LLM_BASE_URL"), false);
  assert.equal(isCredentialKeyName(null), false);
});

test("连接串豁免必须先于 URL 占位符规则：带密码的 URL 是真凭据", () => {
  assert.equal(classify("postgres://app_user:secret@db.internal.example:5432/app"), null);
  assert.equal(classify("https://user:secret@db.example.com/db"), null);
  // 不带凭据的 URL 才是「裸 URL」占位符。
  assert.equal(classify("https://db.example.com/db")[0], REASON_PLACEHOLDER);
});

test("可枚举上下文：.git/ 内、锁文件、integrity 行内的 hex 不是凭据", () => {
  assert.deepEqual(
    classify(HEX32, new Context({ path: "repo/.git/index" })),
    [REASON_NOT_A_SECRET, "可枚举上下文里的 hex 串（index）"], // 说明里是 file_name，与桌面一致
  );
  assert.equal(
    classify(HEX32, new Context({ path: "app/package-lock.json" }))[0],
    REASON_NOT_A_SECRET,
  );
  assert.equal(
    classify(HEX32, new Context({ path: "C:\\repo\\.git\\logs", lineText: "" }))[0],
    REASON_NOT_A_SECRET,
  );
  assert.equal(
    classify(HEX32, new Context({ lineText: 'integrity "sha512-abc"' }))[0],
    REASON_NOT_A_SECRET,
  );
  // 白名单必须窄：普通上下文里的 hex-32 放行（那是潜在的真高德 Key）。
  assert.equal(classify(HEX32, new Context({ lineText: "API_KEY=..." })), null);
  assert.equal(classify(HEX32, new Context({ path: "src/config.py" })), null);
});

test("Context：file_name 与路径归一", () => {
  assert.equal(new Context({ path: "a/b/package.json" }).fileName, "package.json");
  assert.equal(new Context({ path: "a\\b\\yarn.lock" }).fileName, "yarn.lock");
  assert.equal(new Context({}).fileName, "");
  assert.equal(new Context({ path: "x/.git/config" }).inDotGit(), true);
  assert.equal(new Context({ path: "x/.github/workflows" }).inDotGit(), false);
});

test("isAcceptable 与 classify 一致", () => {
  assert.equal(isAcceptable("sk-abcdefgh12345678"), true);
  assert.equal(isAcceptable("sk-xxxxxxxx"), false);
  assert.equal(isAcceptable("", null), false);
});
