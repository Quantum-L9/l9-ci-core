from __future__ import annotations
import json
import validate_action_pins as vap
import validate_ci_dependencies as vcd

FAKE_HASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _minimal_repo(tmp_path):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True)
    (wdir / "valid.yml").write_text(
        "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2\n"
    )
    gov = tmp_path / ".github" / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    (gov / "download-integrity.yaml").write_text('schema_version: "1.0"\ndownloads: {}\n')
    (gov / "action-pins.lock.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-07-01",
                "entries": {
                    "checkout": {
                        "action": "actions/checkout",
                        "kind": "action",
                        "version": "v4.2.2",
                        "commit_sha": "11bd71901bbe5b1630ceea73d27597364c9af683",
                        "upstream_repository": "actions/checkout",
                        "verification_method": "upstream-tag-resolution",
                        "verified_at": "2026-07-01",
                    }
                },
            }
        )
    )
    req = tmp_path / "requirements"
    req.mkdir()
    (req / "bootstrap.lock").write_text(f"ruamel.yaml==0.18.10 \\\n    --hash=sha256:{FAKE_HASH}\n")
    return tmp_path


def test_action_pins_deterministic(tmp_path):
    root = _minimal_repo(tmp_path)
    outs = []
    for i in range(2):
        out = tmp_path / f"r{i}.json"
        vap.run(root, root / ".github" / "workflows", out, "text", True)
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]


def test_ci_dependencies_deterministic(tmp_path):
    root = _minimal_repo(tmp_path)
    outs = []
    for i in range(2):
        out = tmp_path / f"r{i}.json"
        vcd.run(root, out, "text", True)
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]
