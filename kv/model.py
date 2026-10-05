"""跨层传递的数据类型。

这里的每个类型都是一个接缝的契约：
  Candidate  —— parse 的产出、detect 的输入。只带**信号**（主机名、键名），
                永不带平台名。
  Verdict    —— detect 的产出、ops/save 的输入。
  SecretRow  —— core/vault 的产出，cli 与 gui 的输入。

Candidate.value 是 bytes 而不是 str：PEM 和 JSON 需要精确字节，而且 detect 里
为跑正则做的 decode() 会产生一个无法归零的不可变 str —— 把它限制在
Candidate.text() 这一个方法里，那个 str 随判定返回而死。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kv.core import masking

CONFIDENCE_MANUAL = "manual"
CONFIDENCE_EXACT = "exact"
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_AMBIGUOUS = "ambiguous"
CONFIDENCE_NONE = "none"

CONFIDENCES = (
    CONFIDENCE_MANUAL,
    CONFIDENCE_EXACT,
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_NONE,
)

STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"
STATUS_EXPIRED = "expired"
STATUSES = (STATUS_ACTIVE, STATUS_REVOKED, STATUS_EXPIRED)

PLATFORM_UNKNOWN = "unknown"
PLATFORM_OPENAI_COMPATIBLE = "openai-compatible"


@dataclass(frozen=True)
class Candidate:
    """从一段文本里抽出来的一个待判定候选。"""

    value: bytes
    kind: str = masking.KIND_TOKEN
    key_name: str | None = None
    hostnames: tuple[str, ...] = ()
    context_hostnames: tuple[str, ...] = ()
    source: str = "manual"
    span: str = ""
    extra: dict = field(default_factory=dict)

    # hostnames 与 context_hostnames 的区别是**证据强度**，不是来源：
    #   hostnames          直接关联 —— 同一条 curl 命令、同一个 JSON 对象里的 endpoint。
    #                      这把 key 就是发给那个主机的，所以它压过键名。
    #   context_hostnames  上下文级 —— 同一块 .env 里别处出现的 URL。它只是「这块配置
    #                      里提到过这个主机」，不能压过附着在值上的键名或形态，
    #                      否则一块含 deepseek base_url 的 .env 会把同块里的
    #                      GITLAB_TOKEN 误判成 deepseek。
    # detect 的分级顺序把这条区别落成了行为，见 kv/detect/resolve.py。

    def text(self) -> str:
        """仅供 detect.resolve 内部跑正则用。别处不要调 —— 它造出一个不可归零的 str。"""
        return self.value.decode("utf-8", "replace")

    def canonical(self) -> bytes:
        return masking.canonicalize(self.value, self.kind)

    def sha256(self) -> str:
        return masking.fingerprint(self.value, self.kind)

    def mask_parts(self, keep_head: int = 0, keep_tail: int = 4) -> tuple[str, str]:
        """要露在掩码两侧的头尾两段。渲染时再和掩码字符合成，于是字符可后改。"""
        return masking.split_mask(self.canonical(), keep_head, keep_tail)

    def preview(self, char: str = "*", keep_head: int = 0, keep_tail: int = 4) -> str:
        head, tail = self.mask_parts(keep_head, keep_tail)
        return masking.preview(head, tail, char)


@dataclass(frozen=True)
class Rejection:
    """被占位符闸门挡下的东西。**不含值** —— 只有定位和原因。"""

    span: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class ParseResult:
    candidates: tuple[Candidate, ...] = ()
    rejected: tuple[Rejection, ...] = ()


@dataclass(frozen=True)
class Verdict:
    """detect 对一个 Candidate 的判定。"""

    platform: str = PLATFORM_UNKNOWN
    confidence: str = CONFIDENCE_NONE
    source: str = "none"
    evidence: str = ""
    candidates: tuple[str, ...] = ()
    mask_style: tuple[int, int] = (0, 4)
    kind: str = masking.KIND_TOKEN

    @property
    def trusted(self) -> bool:
        """可以无需 --force 落盘的判定。ambiguous 也算可信 ——
        它诚实地说自己不确定，落进 openai-compatible 而不是猜一个厂商。"""
        return self.confidence in (
            CONFIDENCE_MANUAL,
            CONFIDENCE_EXACT,
            CONFIDENCE_HIGH,
            CONFIDENCE_MEDIUM,
            CONFIDENCE_AMBIGUOUS,
        )


@dataclass(frozen=True)
class SecretRow:
    """secret 表的一行。value_blob 是 DPAPI 密文；明文值永不进这个对象。"""

    id: int
    name: str
    platform: str
    confidence: str
    evidence: str
    detect_source: str
    key_name: str | None
    kind: str
    value_blob: bytes
    sha256: str
    mask_head: str
    mask_tail: str
    value_len: int
    status: str
    note: str
    source_url: str
    origin: str
    extra: dict
    created_at: str
    updated_at: str
    expires_at: str | None
    last_used_at: str | None
    last_revealed_at: str | None
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    def preview(self, char: str = "*") -> str:
        """渲染期的预览串。不需要解密 —— 头尾两段本来就存着。"""
        return masking.preview(self.mask_head, self.mask_tail, char)


@dataclass(frozen=True)
class AuditEvent:
    id: int
    ts: str
    event: str
    actor: str
    secret_id: int | None
    name_snapshot: str | None
    sha256_prefix: str | None
    detail: dict
    chain_hash: str


@dataclass(frozen=True)
class SaveResult:
    outcome: str  # created | deduped | rejected
    secret_id: int | None = None
    name: str = ""
    merged_tags: tuple[str, ...] = ()
    warning: str = ""


@dataclass(frozen=True)
class ImportResult:
    """批量导入的汇总。每条记录独立走 saveops.commit，这里只数结果。"""
    created: int = 0
    deduped: int = 0
    rejected: int = 0
    errors: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.created + self.deduped + self.rejected


@dataclass(frozen=True)
class InjectResult:
    """kv use 把值写进 .env 的结果。line_no 是写入或更新的行号（0 = 新增）。"""
    path: str
    key_name: str
    line_no: int
    action: str  # written | updated | unchanged


@dataclass(frozen=True)
class RotationResult:
    """kv rotate 的结果。旧指纹留在 fingerprint 表里，status='revoked'。"""
    secret_id: int
    name: str
    old_sha256: str
    new_sha256: str
    old_revoked_at: str


@dataclass(frozen=True)
class ScanHit:
    path: str
    line_no: int
    secret_id: int | None
    name: str
    platform: str
    status: str
    sha256_prefix: str
