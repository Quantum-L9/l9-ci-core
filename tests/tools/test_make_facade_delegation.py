"""Behavioral proof that the Core Make facade delegates publication to ``l9``.

The generated Core facade is intentionally not a publication engine.  The
``l9`` executable is the Cursor-Governance dispatcher and, in turn, resolves
its consumer-safe target in the Governance SSOT Makefile.  A fake dispatcher
lets this test prove that ``make pr`` reaches that boundary with exactly one
argument and without contacting Git or GitHub.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class MakeFacadeDelegationTests(unittest.TestCase):
    def test_pr_delegates_only_to_l9_pr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = pathlib.Path(temporary)
            receipt = sandbox / "dispatcher-argv.txt"
            dispatcher = sandbox / "l9"
            dispatcher.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'printf \'%s\\n\' "$@" > "$L9_TEST_RECEIPT"\n',
                encoding="utf-8",
            )
            dispatcher.chmod(0o755)

            environment = os.environ.copy()
            environment["L9"] = str(dispatcher)
            environment["L9_TEST_RECEIPT"] = str(receipt)
            result = subprocess.run(
                ["make", "--no-print-directory", "pr"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(receipt.read_text(encoding="utf-8"), "pr\n")
            self.assertNotIn("git push", result.stdout + result.stderr)
            self.assertNotIn("gh pr", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
