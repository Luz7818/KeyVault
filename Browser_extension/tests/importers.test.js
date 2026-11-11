// importers.test.js —— 批量导入解析器（parseBulk）向量测试：
// ① Bitwarden CSV（RFC 4180 子集：引号内逗号/换行/转义引号；列缺失行进 skipped）；
// ② Bitwarden JSON（{items:[…]} 与裸数组两形态）；
// ③ otpauth:// 清单（label/issuer 提取、坏行进 skipped）；
// ④ kv-text 复用 core/parse.js 的候选抽取；
// ⑤ 红线：skipped 列表绝不含任何原始值（C1#20 的机械断言）。

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { parseBulk, parseCsvRows, sniffFormat, previewText } from "../src/vault/importers.js";
import { parseSeed } from "../src/vault/totp.js";

const PASSWORD = "Xk9-mQw2-vTp7-Lz3nRb";
const SEED = "JBSWY3DPEHPK3PXP";
const URI = `otpauth://totp/DeepSeek:luna?secret=${SEED}&issuer=DeepSeek`;

const BITWARDEN_CSV = [
  "folder,name,login_uri,login_username,login_password,login_totp,note",
  ",GitHub,https://github.com,octocat,ghp_AAAAABBBBCCCCDDDD,",
  `工作,DeepSeek,https://api.deepseek.com,luna,${PASSWORD},${URI},主账号`,
  ",NoCreds,https://example.com,someone,,,",
];

const JSON_EXPORT = JSON.stringify({
  items: [
    {
      name: "GitHub",
      login: { username: "octocat", password: "ghp_AAAABBBBCCCC", totp: "",
        uris: [{ uri: "https://github.com" }] },
      notes: "work",
    },
    { name: "Vault", login: { username: "luna", password: PASSWORD, totp: URI } },
    { name: "Note", notes: "no login" },
  ],
});

describe("parseCsvRows（RFC 4180 子集）", () => {
  it("普通行按逗号切；空行丢弃", () => {
    assert.deepEqual(parseCsvRows("a,b,c\n1,2,3"), [["a", "b", "c"], ["1", "2", "3"]]);
    assert.deepEqual(parseCsvRows("a,b\n\n1,2\n"), [["a", "b"], ["1", "2"]]);
  });

  it("引号字段里的逗号/换行/双引号转义", () => {
    const rows = parseCsvRows('name,note\n"Git,Hub","line1\nline2"\n"say ""hi""",x');
    assert.deepEqual(rows[1], ["Git,Hub", "line1\nline2"]);
    assert.deepEqual(rows[2], ['say "hi"', "x"]);
  });

  it("\\r\\n 行分隔与尾 \\r", () => {
    assert.deepEqual(parseCsvRows("a,b\r\n1,2\r\n"), [["a", "b"], ["1", "2"]]);
  });
});

describe("sniffFormat", () => {
  it("四格式识别", () => {
    assert.equal(sniffFormat(BITWARDEN_CSV.join("\n")), "bitwarden-csv");
    assert.equal(sniffFormat('{"items":[{"name":"x"}]}'), "bitwarden-json");
    assert.equal(sniffFormat('[{"name":"x"}]'), "bitwarden-json");
    assert.equal(sniffFormat(URI), "otpauth-list");
    assert.equal(sniffFormat("DEEPSEEK_API_KEY=sk-abc"), "kv-text");
  });

  it("空串与坏 JSON → null（明示失败优于静默当文本）", () => {
    assert.equal(sniffFormat(""), null);
    assert.equal(sniffFormat("   "), null);
    assert.equal(sniffFormat("{broken"), null);
  });
});

describe("parseBulk · bitwarden-csv", () => {
  it("密码行 → 条目（平台建议=login_uri 主机名，keyName=用户名）", () => {
    const { format, items, skipped } = parseBulk(BITWARDEN_CSV.join("\n"));
    assert.equal(format, "bitwarden-csv");
    assert.equal(skipped.length, 1, "NoCreds 行进 skipped");
    assert.equal(skipped[0].reason, "行里既没有密码也没有 TOTP 种子");
    const github = items.find((item) => item.name === "GitHub");
    assert.equal(github.value, "ghp_AAAAABBBBCCCCDDDD");
    assert.equal(github.platform, "github.com");
    assert.equal(github.keyName, "octocat");
    assert.equal(github.sourceUrl, "https://github.com");
    assert.equal(github.totp, null);
  });

  it("密码+TOTP 同行 → 展开两个条目（密码 + <name>-totp，平台=issuer）", () => {
    const { items } = parseBulk(BITWARDEN_CSV.join("\n"));
    const totp = items.find((item) => item.name === "DeepSeek-totp");
    assert.equal(totp.value, SEED);
    assert.equal(totp.platform, "DeepSeek");
    const main = items.find((item) => item.name === "DeepSeek");
    assert.equal(main.value, PASSWORD);
    assert.equal(main.warnings.length, 0);
  });

  it("裸 base32 的 login_totp 也认（不强制 otpauth URI）", () => {
    const csv = "name,login_uri,login_username,login_password,login_totp\n"
      + `Authy,,luna,${PASSWORD},${SEED}`;
    const { items } = parseBulk(csv);
    const totp = items.find((item) => item.name === "Authy-totp");
    assert.equal(totp.value, SEED);
    assert.equal(totp.platform, "totp");
  });

  it("坏 TOTP：密码行保留并带 warning；纯 TOTP 行进 skipped", () => {
    const csv = "name,login_uri,login_username,login_password,login_totp\n"
      + `Broken,,luna,${PASSWORD},ABC1\nOnlyTotp,,x,,ABC1`;
    const { items, skipped } = parseBulk(csv);
    const broken = items.find((item) => item.name === "Broken");
    assert.equal(broken.value, PASSWORD);
    assert.match(broken.warnings[0], /不是合法种子/);
    assert.equal(skipped.length, 1);
    assert.match(skipped[0].reason, /TOTP 字段不合法/);
  });

  it("显式指定格式时表头缺列 → 报错不静默（嗅探对非 Bitwarden CSV 落 kv-text）", () => {
    assert.throws(() => parseBulk("foo,bar\n1,2", "bitwarden-csv"), /Bitwarden 导出格式/);
    assert.throws(() => parseBulk("name", "bitwarden-csv"), /至少要有表头/);
    // 嗅探口径：无 Bitwarden 表头的逗号文本不算 Bitwarden（宁走 kv-text 不硬猜）
    assert.equal(sniffFormat("foo,bar\n1,2"), "kv-text");
  });
});

describe("parseBulk · bitwarden-json", () => {
  it("{items:[…]} 与裸数组两形态等价", () => {
    const wrapped = parseBulk(JSON_EXPORT);
    const bare = parseBulk(JSON.stringify(JSON.parse(JSON_EXPORT).items));
    assert.equal(wrapped.format, "bitwarden-json");
    assert.deepEqual(
      wrapped.items.map((item) => [item.name, item.value]),
      bare.items.map((item) => [item.name, item.value]),
    );
  });

  it("login 字段映射 + TOTP 展开 + 无登录字段条目进 skipped", () => {
    const { items, skipped } = parseBulk(JSON_EXPORT);
    const github = items.find((item) => item.name === "GitHub");
    assert.equal(github.value, "ghp_AAAABBBBCCCC");
    assert.equal(github.platform, "github.com");
    assert.equal(github.note, "work");
    const totp = items.find((item) => item.name === "Vault-totp");
    assert.equal(totp.value, SEED);
    assert.equal(skipped.length, 1);
    assert.equal(skipped[0].preview, "Note");
  });

  it("不是 Bitwarden 形态的 JSON → 明确报错（显式格式给形状错误，嗅探给认不出）", () => {
    assert.throws(() => parseBulk('{"foo": 1}', "bitwarden-json"), /items 数组/);
    assert.throws(() => parseBulk('{"foo": 1}'), /认不出导入格式/);
    assert.throws(() => parseBulk("{bad json!", "bitwarden-json"), /JSON 解析失败/);
  });
});

describe("parseBulk · otpauth-list", () => {
  it("逐行解析：label→name、issuer→平台、secret→value", () => {
    const text = `${URI}\n\notpauth://totp/luna?secret=${SEED}\n`;
    const { items, skipped } = parseBulk(text);
    assert.equal(items.length, 2);
    assert.equal(items[0].name, "luna");
    assert.equal(items[0].platform, "DeepSeek");
    assert.equal(items[1].platform, "totp");
    assert.deepEqual([...parseSeed(items[1].value)], [...parseSeed(SEED)]);
    assert.equal(skipped.length, 0);
  });

  it("坏行进 skipped 且预览绝不含 secret（C1#20）", () => {
    const badLine = "otpauth://totp/Corp:root?issuer=Corp"; // 缺 secret
    const { items, skipped } = parseBulk(`${URI}\n${badLine}\nplain line`);
    assert.equal(items.length, 1);
    assert.equal(skipped.length, 2);
    assert.match(skipped[1].reason, /不是 otpauth/);
    for (const entry of skipped) {
      assert.equal(entry.preview.includes(SEED), false, "skipped 预览泄漏种子");
    }
  });
});

describe("parseBulk · kv-text（.env 复用 core/parse.js）", () => {
  it("KEY=value 候选 → 条目，平台留空（确认步走 SW 现场检测）", () => {
    const text = "DEEPSEEK_API_KEY=sk-abc123_def456ghi789jklmnopqrstu12\n"
      + "GITHUB_TOKEN=ghp_AAAABBBBCCCCDDDDEEEE";
    const { format, items } = parseBulk(text);
    assert.equal(format, "kv-text");
    assert.equal(items.length, 2);
    const deepseek = items.find((item) => item.keyName === "DEEPSEEK_API_KEY");
    assert.equal(deepseek.value, "sk-abc123_def456ghi789jklmnopqrstu12");
    assert.equal(deepseek.platform, "");
    assert.equal(deepseek.name, "");
  });
});

describe("importers 红线（机械断言）", () => {
  it("skipped 条目绝不携带任何条目原始值（C1#20）", () => {
    const samples = [
      BITWARDEN_CSV.join("\n"),
      JSON_EXPORT,
      `${URI}\nbroken line\notpauth://totp/x?secret=ABC1`,
      `secret,login_password\n"top,secret",${PASSWORD}\nx,`,
    ];
    for (const text of samples) {
      const { items, skipped } = parseBulk(text);
      const values = items.map((item) => item.value);
      const dumped = JSON.stringify(skipped);
      for (const value of values) {
        assert.equal(dumped.includes(value), false, `skipped 泄漏值：${value.slice(0, 8)}…`);
      }
    }
  });

  it("previewText 只出掩码：保留首尾 2 位、中段全 *", () => {
    assert.equal(previewText(PASSWORD), `${PASSWORD.slice(0, 2)}******${PASSWORD.slice(-2)}`);
    assert.equal(previewText("short"), "*****");
    assert.equal(previewText(""), "");
  });

  it("条目上限 500：截断时 truncated=true", () => {
    const rows = ["name,login_uri,login_username,login_password,login_totp"];
    for (let i = 0; i < 510; i += 1) rows.push(`row${i},,u,pwd-${i},`);
    const { items, truncated } = parseBulk(rows.join("\n"));
    assert.equal(items.length, 500);
    assert.equal(truncated, true);
  });
});
