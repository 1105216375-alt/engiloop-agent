"""Pure preprocessing extracted from legacy_v7/app.py:582.

The production QueryNormalizer's API client/cache/configuration are deliberately
not copied. This compatibility class exposes only the original pure method.
"""
import re


class QueryNormalizer:
    @staticmethod
    def _pre_normalize(text):
        """纯字符串预处理:全角→半角、去尾标点、压缩空白"""
        # 全角ASCII → 半角
        out = []
        for ch in text:
            code = ord(ch)
            if code == 0x3000:
                out.append(" ")
            elif 0xFF01 <= code <= 0xFF5E:
                out.append(chr(code - 0xFEE0))
            else:
                out.append(ch)
        s = "".join(out)
        s = re.sub(r"\s+", " ", s).strip()
        # 去掉末尾的中英文标点(不影响语义)
        s = re.sub(r"[?？!!。.,,;;:::]+$", "", s)
        return s
