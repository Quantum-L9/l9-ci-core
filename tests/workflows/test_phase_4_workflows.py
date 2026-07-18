from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLICATION = ROOT / ".github/workflows/publish-analysis.yml"


class Phase4WorkflowTests(unittest.TestCase):
    def test_publication_has_explicit_permissions(self) -> None:
        text = PUBLICATION.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(
                r"(?m)^permissions:\s*\n\s+actions:\s+read\s*\n\s+checks:\s+write\s*\n\s+contents:\s+read\s*$"
            ),
        )

    def test_shadow_and_disabled_do_not_publish(self) -> None:
        text = PUBLICATION.read_text(encoding="utf-8")
        self.assertIn("if: inputs.mode == 'shadow' || inputs.mode == 'disabled'", text)
        self.assertIn(
            "if: inputs.mode == 'blocking' || inputs.mode == 'advisory'", text
        )

    def test_bundle_is_revalidated_before_publication(self) -> None:
        text = PUBLICATION.read_text(encoding="utf-8")
        self.assertLess(
            text.index("Revalidate downloaded canonical bundle"),
            text.index("Render publication payload"),
        )
        self.assertLess(
            text.index("Render publication payload"), text.index("Publish GitHub check")
        )

    def test_download_action_is_immutable(self) -> None:
        text = PUBLICATION.read_text(encoding="utf-8")
        self.assertIn(
            "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093", text
        )


if __name__ == "__main__":
    unittest.main()
