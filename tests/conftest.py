"""Shared fixtures. No test in this suite touches the network or the real
config/log directories — every one redirects storage into tmp_path.

**Every name, address, number, and organisation below is invented.** This is a
public repository for a tool whose subject matter is employment disputes, so the
fixtures are held to the same standard the tool asks of its users:

* People and the employer are fictional composites, not anyone real.
* Email addresses use the ``.example`` TLD and ``example.com`` domain, both
  reserved by RFC 2606 and guaranteed never to resolve.
* The telephone number is in the 555-0100..0199 block, reserved for fiction.
* Identifiers are arbitrary and match no real payroll scheme.

If you add a fixture, keep to this. Never paste a real document into this suite,
even a redacted one.
"""

from __future__ import annotations

import pytest

from jobmonger import consent, paths
from jobmonger.intake import Document

SAMPLE = """From: Sarah Chen
To: Marcus Okafor

Marcus,

Following our conversation on 14 March, I am confirming that your request for
flexible hours has been declined by the scheduling committee. You may raise this
with Priya Raman in Human Resources if you wish. Her direct line is
(555) 555-0147 and her address is p.raman@northgate-logistics.example.

Your employee number is EMP-449201. Please quote it in any correspondence.

The handbook states that appeals must be lodged within ten working days.

Regards,

Sarah Chen
Operations Manager
"""


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Point every path helper at a temp directory for the duration of a test."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()
    monkeypatch.setattr(paths, "config_dir", lambda: config_dir)
    monkeypatch.setattr(paths, "data_dir", lambda: data_dir)
    monkeypatch.setattr(paths, "config_file", lambda: config_dir / "config.json")
    monkeypatch.setattr(paths, "consent_file", lambda: config_dir / "consent.json")
    monkeypatch.setattr(paths, "log_file", lambda: data_dir / "activity.log.jsonl")
    yield


@pytest.fixture
def granted():
    """Consent recorded, so document loading is permitted."""
    consent.grant()
    yield


@pytest.fixture
def document() -> Document:
    return Document(source_name="letter.txt", text=SAMPLE)
