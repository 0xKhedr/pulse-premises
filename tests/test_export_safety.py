from __future__ import annotations

import io

from app.services.export_safety import SafeDictWriter, csv_safe, redact_secrets


def test_redact_secrets_is_recursive_and_does_not_mutate_input():
    original = {
        "device_id": "node-1",
        "app_key": "TOP-SECRET-ROOT-KEY",
        "integration": {"api_token": "TOKEN", "status": "ok"},
    }

    safe = redact_secrets(original)

    assert safe == {
        "device_id": "node-1",
        "app_key": "***hidden***",
        "integration": {"api_token": "***hidden***", "status": "ok"},
    }
    assert original["app_key"] == "TOP-SECRET-ROOT-KEY"


def test_csv_safe_blocks_formula_prefixes_including_leading_whitespace():
    assert csv_safe('=HYPERLINK("https://example.test")').startswith("'")
    assert csv_safe("  +1+1").startswith("'")
    assert csv_safe("ordinary text") == "ordinary text"


def test_safe_dict_writer_applies_csv_safety_to_every_cell():
    output = io.StringIO()
    writer = SafeDictWriter(output, fieldnames=["name", "status"])
    writer.writeheader()
    writer.writerow({"name": "@malicious", "status": "ok"})

    assert "'@malicious" in output.getvalue()
