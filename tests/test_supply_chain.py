"""잠금 파일·SBOM 시험 (계획서 §30-1, §30-2).

둘 다 "지금 이 안에 무엇이 들어 있는가"를 파일 하나로 고정하는 물건이다.
그러니 확인할 것은 형식이 아니라 **두 번 만들어도 같은 답이 나오는가**와
**지금 환경과 어긋났을 때 그것을 말해 주는가**다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.tools import lockfile, sbom


# ── 잠금 파일 ───────────────────────────────────────────────────────

def test_lock_pins_exact_versions():
    text = lockfile.render([("PySide6", "6.11.1"), ("numpy", "2.5.2")])
    assert "PySide6==6.11.1" in text
    assert "numpy==2.5.2" in text
    assert ">=" not in text          # 범위 지정이 남으면 재현 빌드가 아니다


def test_lock_writes_hashes_only_when_they_were_actually_computed():
    text = lockfile.render(
        [("numpy", "2.5.2"), ("olefile", "0.47")],
        {"numpy": ["a" * 64, "b" * 64]},
    )
    assert "numpy==2.5.2 \\" in text
    assert f"    --hash=sha256:{'a' * 64} \\" in text
    assert f"    --hash=sha256:{'b' * 64}" in text
    # 해시를 모르는 것에는 지어내지 않는다.
    assert "olefile==0.47" in text
    assert text.count("--hash") == 2


def test_lock_round_trips_through_the_parser():
    packages = [("PySide6", "6.11.1"), ("python-docx", "1.2.0")]
    parsed = lockfile.parse_lock(lockfile.render(packages, {"pyside6": ["c" * 64]}))
    assert parsed == {"pyside6": "6.11.1", "python-docx": "1.2.0"}


def test_names_are_compared_the_way_pip_compares_them():
    """`PySide6_Addons`와 `pyside6-addons`는 같은 것이다 (PEP 503)."""
    assert lockfile.normalize("PySide6_Addons") == lockfile.normalize("pyside6-addons")
    assert not lockfile.differences({"pyside6-addons": "6.11.1"}, [("PySide6_Addons", "6.11.1")])


def test_differences_names_what_drifted():
    gaps = lockfile.differences(
        {"numpy": "2.5.2", "olefile": "0.47"},
        [("numpy", "2.6.0"), ("httpx", "0.28.1")],
    )
    assert any("numpy" in line and "2.5.2 → 2.6.0" in line for line in gaps)
    assert any("olefile" in line and "설치되지 않음" in line for line in gaps)
    assert any("httpx" in line and "잠금 파일에 없음" in line for line in gaps)


@pytest.mark.skipif(
    Path(sys.prefix).resolve() != (Path(lockfile.LOCK_PATH).parent / ".venv").resolve(),
    reason="프로젝트 .venv가 아닌 환경 — 잠금 파일은 start.bat이 세우는 그 환경의 것이다",
)
def test_the_checked_in_lock_matches_this_environment():
    """개발 환경이 잠금 파일에서 흘러가면 여기서 잡는다.

    실패하면 `python -m app.tools.lockfile`로 다시 만들고, 무엇이 왜 바뀌었는지
    커밋 메시지에 남긴다 — 의존성 변경은 조용히 지나가면 안 되는 사건이다.

    다른 파이썬(시스템 설치 등)으로 시험을 돌릴 때는 건너뛴다. 배포 기준은
    `start.bat`이 만드는 `.venv` 하나이고, 그 밖의 환경과 대조하면 거짓
    경보만 는다.
    """
    path = Path(lockfile.LOCK_PATH)
    assert path.exists(), "requirements.lock이 없다"
    gaps = lockfile.differences(
        lockfile.parse_lock(path.read_text(encoding="utf-8")), lockfile.installed()
    )
    assert not gaps, "환경이 잠금 파일과 다르다:\n" + "\n".join(gaps)


def test_environment_tooling_is_not_pinned():
    """pip를 잠그면 설치 도중 제 발밑을 갈아 끼운다(start.bat에서 겪은 사고)."""
    assert "pip" in lockfile.SKIP
    assert all(name not in dict(lockfile.installed()) for name in ("pip", "setuptools"))


# ── SBOM ────────────────────────────────────────────────────────────

def test_sbom_is_valid_cyclonedx():
    document = sbom.build(app_version="1.0.0", when=datetime(2026, 8, 15, tzinfo=timezone.utc))

    assert document["bomFormat"] == "CycloneDX"
    assert document["specVersion"] == sbom.SPEC_VERSION
    assert document["serialNumber"].startswith("urn:uuid:")
    assert document["metadata"]["timestamp"] == "2026-08-15T00:00:00Z"
    assert document["metadata"]["component"]["name"] == "NunchiCoach"
    assert document["metadata"]["component"]["version"] == "1.0.0"
    assert json.loads(json.dumps(document))          # 직렬화 가능해야 산출물이 된다


def test_sbom_lists_the_runtime_libraries_with_package_urls():
    names = {item["name"].lower() for item in sbom.components()}
    assert {"pyside6", "numpy", "httpx"} <= names

    numpy = next(item for item in sbom.components() if item["name"].lower() == "numpy")
    assert numpy["purl"].startswith("pkg:pypi/numpy@")
    assert numpy["type"] == "library"


def test_sbom_order_is_stable_so_releases_can_be_compared():
    first = [item["name"] for item in sbom.components()]
    assert first == sorted(first, key=lockfile.normalize)
    assert first == [item["name"] for item in sbom.components()]


def test_sbom_licenses_are_labels_not_whole_documents():
    """`License` 항목에 본문 전체를 담는 배포판이 흔하다. SBOM이 라이선스
    문서 모음이 되면 심사 서류로 못 쓴다."""
    for item in sbom.components():
        for entry in item.get("licenses", []):
            name = entry["license"]["name"]
            assert len(name) <= 64 and "\n" not in name, item["name"]


def test_sbom_and_lock_describe_the_same_set():
    assert {lockfile.normalize(item["name"]) for item in sbom.components()} == {
        lockfile.normalize(name) for name, _version in lockfile.installed()
    }
