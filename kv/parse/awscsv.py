"""AWS credentials.csv。

与 GCP 服务账号的处理**相反**：这里要拆成两条记录。理由是 kv use 要把
AWS_ACCESS_KEY_ID 和 AWS_SECRET_ACCESS_KEY 作为两个独立的环境变量注入，
而 scan 必须独立指纹那个真正的机密（secret access key），不能把它和
「非机密但能定位身份」的 access key id 绑在同一个 blob 里。
"""

from __future__ import annotations

import csv
import io
import re
from typing import Iterator

from kv.model import Candidate, Rejection
from kv.parse.candidate import emit
from kv.parse.placeholders import Context

ACCESS_KEY_HEADER = re.compile(r"(?i)access\s*key\s*id")
SECRET_KEY_HEADER = re.compile(r"(?i)secret\s*access\s*key")
USER_HEADER = re.compile(r"(?i)^user\s*name$")


def looks_like_aws_csv(text: str) -> bool:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return bool(ACCESS_KEY_HEADER.search(first) and SECRET_KEY_HEADER.search(first))


def parse_awscsv(text: str, *, source: str = "csv") -> Iterator[Candidate | Rejection]:
    if not looks_like_aws_csv(text):
        return
    try:
        rows = list(csv.DictReader(io.StringIO(text.strip())))
    except csv.Error:
        return

    for number, row in enumerate(rows, start=2):  # 第 1 行是表头
        access_key = _pick(row, ACCESS_KEY_HEADER)
        secret_key = _pick(row, SECRET_KEY_HEADER)
        user_name = _pick(row, USER_HEADER)
        if not secret_key:
            continue

        # 两条记录用 pair_id 关联：access key id 是标识符，secret 才是机密。
        pair_id = f"aws-{number}"
        context = Context(source=source, line_no=number, line_text="aws credentials.csv row")
        extra = {"pair_id": pair_id, "aws_pair_role": "secret"}
        if user_name:
            extra["aws_user_name"] = user_name

        yield emit(secret_key, key_name="AWS_SECRET_ACCESS_KEY", source=source,
                   span=f"csv row {number}", extra=extra, context=context)

        if access_key:
            yield emit(
                access_key, key_name="AWS_ACCESS_KEY_ID", source=source,
                span=f"csv row {number}",
                extra={"pair_id": pair_id, "aws_pair_role": "identifier",
                       "aws_user_name": user_name or ""},
                context=context,
            )


def _pick(row: dict, header_pattern: re.Pattern) -> str:
    for key, value in row.items():
        if key and header_pattern.search(key.strip()):
            return (value or "").strip()
    return ""
