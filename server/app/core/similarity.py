"""轻量文本相似度：用于记忆去重，不依赖外部服务或 embedding。

实现用标准库 `difflib.SequenceMatcher`（Ratcliff-Obershelp 最长匹配块）：对中文这种
没有天然词边界的文本比字符 bigram 更鲁棒——「用户偏好简洁中文」与「用户喜欢简洁的中文
风格」这类替换个别词、但保留大量公共子序列的近似，bigram 只能给 0.33，SequenceMatcher
能给 0.6+。配合 Unicode NFKC 归一化（全角转半角）与去标点/空白，能识别「改标点、加字、
全角半角不一致、换个别词」这些精确匹配识别不了的近重复。

阈值分两层：
- 写入去重（DUPLICATE_THRESHOLD=0.75）偏保守，只拦「高度近似」，宁可漏判也不误删，
  避免把两条只是部分相关的记忆当成重复、导致 Bot 少注入一条重要信息。
- 疑似重复整理（SIMILAR_THRESHOLD=0.6）偏宽松，宁可多报，交给用户在记忆页人工判断。
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# 写入去重阈值：相似度达到该值即视为重复。
DUPLICATE_THRESHOLD = 0.75
# 疑似重复（用于界面整理，宁可多报、由用户判断）：略低于去重阈值。
SIMILAR_THRESHOLD = 0.6

# 归一化后只保留 Unicode 字母/数字（含中文、英文、数字），去掉标点与空白。
_NON_WORD = re.compile(r"[\W_]+", flags=re.UNICODE)


def normalize(text: str) -> str:
    """NFKC 全角转半角 + 小写 + 去空白/标点，得到可比较的规范串。"""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.lower()
    return _NON_WORD.sub("", t)


def similarity(a: str, b: str) -> float:
    """返回 [0, 1] 的相似度：最长匹配块比率（SequenceMatcher.ratio），先归一化。"""
    na, nb = normalize(a), normalize(b)
    if not na and not nb:
        return 1.0
    if na == nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def is_duplicate(a: str, b: str, threshold: float = DUPLICATE_THRESHOLD) -> bool:
    """是否视为重复：归一化后相等、短内容被完整包含、或相似度达标。"""
    na, nb = normalize(a), normalize(b)
    if not na and not nb:
        return True
    if na == nb:
        return True
    if not na or not nb:
        return False
    # 短内容被长内容完整包含（且不算太短），视为高度重叠。
    if len(na) >= 4 and len(nb) >= 4 and (na in nb or nb in na):
        return True
    return similarity(a, b) >= threshold


def duplicates_in(items: list[tuple[str, str]], threshold: float = SIMILAR_THRESHOLD) -> list[tuple[int, int, float]]:
    """在 [(id, content), ...] 里两两比对，返回 [(i, j, score), ...]（i<j，score 降序）。

    O(n²)，用于界面「疑似重复整理」；调用方应控制条数上限。
    """
    out: list[tuple[int, int, float]] = []
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            score = similarity(items[i][1], items[j][1])
            if score >= threshold:
                out.append((i, j, score))
    out.sort(key=lambda t: t[2], reverse=True)
    return out
