"""Check the hand-written QR encoder against a reference implementation.

    python -m c3s_cli.qr_verify          # needs `pip install qrcode`; nothing else does

The encoder in `qr.py` is ours, so it is only as good as what it is compared against: this
builds every version 1–10 at every error-correction level with every one of the eight
masks forced on both sides, and compares the matrices module for module. Forcing the mask
is the point — it takes the mask-choosing penalty rules out of the comparison, so a
difference can only be encoding, error correction, interleaving or placement.

The reference package is never a dependency of `c3s-circuit-agent`: pairing must work on a
machine with no network and no wheel to fetch.
"""

from __future__ import annotations

import random
import string
import sys

from . import qr


def main() -> int:
    try:
        import qrcode
        from qrcode.constants import (ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M,
                                      ERROR_CORRECT_Q)
        from qrcode.util import MODE_8BIT_BYTE, QRData
    except ImportError:
        print("skipped: the reference package is not installed (pip install qrcode)")
        return 0

    levels = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}
    alphabet = string.ascii_letters + string.digits + "/:?=#.-_"
    random.seed(7)
    checked = mismatched = 0
    for version in range(1, qr.MAX_VERSION + 1):
        for level, constant in levels.items():
            room = qr.capacity(version, level)
            texts = ["http://192.168.1.23:8765/#approvals&token=abcDEF1234567890abcDEF12"[:room],
                     "".join(random.choice(alphabet) for _ in range(room)),
                     "x", "aaa"[:room], ("mid" * (room // 6)) or "ab"]
            for text in texts:
                if not text or len(text) > room:
                    continue
                for mask in range(8):
                    mine = qr.matrix(text, level=level, version=version, mask_pattern=mask)
                    reference = qrcode.QRCode(version=version, error_correction=constant,
                                              mask_pattern=mask, border=0, box_size=1)
                    reference.add_data(QRData(text.encode(), mode=MODE_8BIT_BYTE))
                    reference.make(fit=False)
                    theirs = [[1 if v else 0 for v in row] for row in reference.get_matrix()]
                    checked += 1
                    if mine != theirs:
                        mismatched += 1
                        if mismatched <= 3:
                            print(f"mismatch: version {version} level {level} mask {mask} "
                                  f"({len(text)} bytes)")
    print(f"{checked} matrices compared, {mismatched} mismatches")
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
