"""SBOM(구성요소 목록) 만들기 — CycloneDX 1.5 JSON (계획서 §30-2).

    python -m app.tools.sbom [--out dist/sbom.cdx.json] [--pretty]

공직 환경 도입 심사에서 "이 프로그램 안에 무엇이 들어 있는가"를 문서로
요구한다. 릴리스 산출물에 함께 넣는다.

바깥 라이브러리(cyclonedx-bom)를 쓰지 않는다. SBOM은 **의존성을 줄이려고**
만드는 물건인데 그것을 만들려고 의존성을 하나 더 들이면 앞뒤가 맞지 않고,
오프라인 빌드 환경에 설치 부담을 더한다. 표준이 요구하는 항목은 표준
라이브러리로 충분히 채운다.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from .lockfile import SKIP, normalize

SPEC_VERSION = "1.5"
PRODUCT = "NunchiCoach"
DEFAULT_OUT = Path("dist") / "sbom.cdx.json"


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = _parse_args(argv)
    document = build(app_version=args.version)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(document, ensure_ascii=False, indent=2 if args.pretty else None),
        encoding="utf-8",
    )
    print(f"{out} — 구성요소 {len(document['components'])}개")
    return 0


def build(app_version: str = "0.0.0", when: datetime | None = None) -> dict:
    stamp = (when or datetime.now(timezone.utc)).replace(microsecond=0)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": stamp.isoformat().replace("+00:00", "Z"),
            "tools": [{"vendor": PRODUCT, "name": "app.tools.sbom", "version": "1"}],
            "component": {
                "type": "application",
                "name": PRODUCT,
                "version": app_version,
                "description": "순환보직 공직자를 위한 로컬 업무 인수인계 도구",
            },
        },
        "components": components(),
    }


def components() -> list[dict]:
    """설치된 배포판 하나가 구성요소 하나. 이름순으로 고정해 두 번 만들어도
    같은 파일이 나오게 한다 — 릴리스마다 순서만 바뀐 파일은 비교가 안 된다."""
    out: list[dict] = []
    seen: set[str] = set()
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        key = normalize(name or "")
        if not name or key in SKIP or key in seen:
            continue
        seen.add(key)
        entry = {
            "type": "library",
            "name": name,
            "version": dist.version,
            "purl": f"pkg:pypi/{key}@{dist.version}",
        }
        license_name = _license(dist)
        if license_name:
            entry["licenses"] = [{"license": {"name": license_name}}]
        out.append(entry)
    return sorted(out, key=lambda item: normalize(item["name"]))


def _license(dist) -> str:
    """메타데이터의 라이선스 표기는 배포판마다 제각각이다.

    `License` 항목이 본문 전체(수천 자)인 배포판이 흔해서 그대로 담으면
    SBOM이 라이선스 문서 모음이 된다. 분류자(Classifier)를 먼저 보고,
    없을 때만 짧은 `License` 값을 쓴다.
    """
    for value in dist.metadata.get_all("Classifier") or []:
        if value.startswith("License :: "):
            return value.rsplit(" :: ", 1)[-1]
    value = (dist.metadata.get("License") or "").strip()
    if value and len(value) <= 64 and "\n" not in value:
        return value
    expression = (dist.metadata.get("License-Expression") or "").strip()
    return expression


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sbom", description="설치된 구성요소를 CycloneDX SBOM으로 적는다."
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"출력 경로 (기본: {DEFAULT_OUT})")
    parser.add_argument("--version", default="0.0.0", help="이 릴리스의 제품 버전")
    parser.add_argument("--pretty", action="store_true", help="들여쓰기해서 적는다")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
