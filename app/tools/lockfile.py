"""의존성 잠금 파일 만들기·검사 (계획서 §30-1).

    python -m app.tools.lockfile                 # 지금 환경으로 requirements.lock 생성
    python -m app.tools.lockfile --check         # 지금 환경이 잠금 파일과 같은지 검사
    python -m app.tools.lockfile --wheelhouse dir  # 받아 둔 wheel의 해시까지 적는다

`requirements.txt`는 `>=` 범위라 같은 파일로 두 번 설치해도 다른 것이 깔린다.
오프라인 재현 빌드가 성립하려면 "무엇이 깔렸는지"가 파일 하나로 고정돼야 하고,
공직 환경에 배포하는 물건이라 그 파일이 사람 눈으로 확인 가능해야 한다.

해시는 **받아 둔 wheel 폴더가 있을 때만** 적는다. 해시를 지어내지 않는다 —
설치 파일을 직접 보지 않고 적은 해시는 검증이 아니라 장식이다. 오프라인
설치 꾸러미를 만들 때 함께 만든다.

    pip download -r requirements.txt -d wheelhouse
    python -m app.tools.lockfile --wheelhouse wheelhouse
    pip install --no-index --find-links wheelhouse -r requirements.lock
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from datetime import date
from importlib import metadata
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parents[2] / "requirements.lock"

# 환경을 세우는 도구 자신은 잠그지 않는다. pip를 고정한 잠금 파일을 pip로
# 설치하면 설치 도중 제 발밑을 갈아 끼우게 된다(start.bat 주석 참고).
SKIP = {"pip", "setuptools", "wheel"}

HEADER = "# 눈치코치 의존성 잠금 파일 — 배포·재현 빌드의 기준이다."


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = _parse_args(argv)
    path = Path(args.out) if args.out else LOCK_PATH

    if args.check:
        return _check(path)

    hashes = _wheel_hashes(Path(args.wheelhouse)) if args.wheelhouse else {}
    text = render(installed(), hashes)
    path.write_text(text, encoding="utf-8")
    print(f"{path} — {len(installed())}개 패키지"
          + (f", 해시 {len(hashes)}건" if hashes else ", 해시 없음"))
    return 0


# ── 만들기 ──────────────────────────────────────────────────────────
def installed() -> list[tuple[str, str]]:
    """(이름, 버전) 목록. 이름은 pip가 쓰는 정규화 형태로 맞춘다."""
    found: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name or normalize(name) in SKIP:
            continue
        found[name] = dist.version
    return sorted(found.items(), key=lambda item: normalize(item[0]))


def normalize(name: str) -> str:
    """PEP 503. `PySide6_Addons`와 `pyside6-addons`는 같은 것이다."""
    return re.sub(r"[-_.]+", "-", name).lower()


def render(packages: list[tuple[str, str]], hashes: dict[str, list[str]] | None = None) -> str:
    hashes = hashes or {}
    lines = [
        HEADER,
        f"# 만든 날 {date.today().isoformat()} · Python "
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} · {sys.platform}",
        "# 다시 만들기:  python -m app.tools.lockfile",
        "# 검사하기:     python -m app.tools.lockfile --check",
        "",
    ]
    for name, version in packages:
        digests = hashes.get(normalize(name), [])
        if not digests:
            lines.append(f"{name}=={version}")
            continue
        lines.append(f"{name}=={version} \\")
        for index, digest in enumerate(digests):
            tail = "" if index == len(digests) - 1 else " \\"
            lines.append(f"    --hash=sha256:{digest}{tail}")
    return "\n".join(lines) + "\n"


def _wheel_hashes(folder: Path) -> dict[str, list[str]]:
    """받아 둔 wheel/sdist에서 sha256을 직접 계산한다."""
    if not folder.is_dir():
        raise SystemExit(f"wheel 폴더를 찾을 수 없습니다: {folder}")
    out: dict[str, list[str]] = {}
    for item in sorted(folder.iterdir()):
        if item.suffix not in (".whl", ".gz", ".zip"):
            continue
        name = normalize(item.name.split("-")[0])
        out.setdefault(name, []).append(_sha256(item))
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ── 검사 ────────────────────────────────────────────────────────────
def parse_lock(text: str) -> dict[str, str]:
    """잠금 파일에서 {정규화된 이름: 버전}. 해시 줄과 주석은 건너뛴다."""
    pinned: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip().rstrip("\\").strip()
        if not line or line.startswith("#") or line.startswith("--hash"):
            continue
        if "==" not in line:
            continue
        name, version = line.split("==", 1)
        pinned[normalize(name)] = version.strip()
    return pinned


def differences(pinned: dict[str, str], current: list[tuple[str, str]]) -> list[str]:
    """사람이 읽는 차이 목록. 빈 목록이면 환경이 잠금 파일과 같다."""
    now = {normalize(name): version for name, version in current}
    out: list[str] = []
    for name in sorted(set(pinned) | set(now)):
        want, have = pinned.get(name), now.get(name)
        if want == have:
            continue
        if want is None:
            out.append(f"+ {name} {have} (잠금 파일에 없음)")
        elif have is None:
            out.append(f"- {name} {want} (설치되지 않음)")
        else:
            out.append(f"! {name} {want} → {have} (버전 다름)")
    return out


def _check(path: Path) -> int:
    if not path.exists():
        print(f"잠금 파일이 없습니다: {path}", file=sys.stderr)
        return 2
    gaps = differences(parse_lock(path.read_text(encoding="utf-8")), installed())
    if not gaps:
        print(f"환경이 잠금 파일과 같습니다 — {path}")
        return 0
    print(f"환경이 잠금 파일과 다릅니다 ({len(gaps)}건) — {path}", file=sys.stderr)
    for line in gaps:
        print("  " + line, file=sys.stderr)
    return 1


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lockfile",
        description="설치된 패키지를 requirements.lock으로 고정하거나 대조한다.",
    )
    parser.add_argument("--check", action="store_true", help="생성하지 않고 대조만 한다")
    parser.add_argument("--wheelhouse", default=None, help="해시를 계산할 wheel 폴더")
    parser.add_argument("--out", default=None, help=f"잠금 파일 경로 (기본: {LOCK_PATH})")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
