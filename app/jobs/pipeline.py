"""처리 파이프라인 — 찾기 → 해시 → 내용 읽기.

재개는 별도 큐가 아니라 **문서의 상태 자체**로 이뤄진다. `hash IS NULL`이면
아직 해시 전이고 `parse_status='pending'`이면 아직 안 읽은 것이다. 앱이 강제
종료돼도 다음 실행에서 같은 질의가 남은 일을 그대로 집어낸다(ING-006).

jobs 테이블은 재시도 횟수를 세는 데만 쓴다. 실패한 파일을 재시작할 때마다
무한히 다시 시도하면 영원히 끝나지 않기 때문이다.

원본은 읽기만 한다. 이 계층에도 쓰기 연산이 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from ..ai import OllamaClient
from ..ai.discover import discover
from ..core import dating, timeline
from ..db import Database
from ..ingest import scanner
from ..search import pack
from ..ingest.hasher import sha256
from ..ingest.parsers import parse
from ..ingest.parsers.base import PARSER_VERSION

MAX_ATTEMPTS = 3
COMMIT_EVERY = 50

STAGE_SCAN = "파일 찾기"
STAGE_HASH = "중복 확인"
STAGE_PARSE = "내용 읽기"
STAGE_DATE = "시점 확인"
STAGE_EMBED = "의미 색인"
STAGE_DISCOVER = "업무 파악"
STAGE_CYCLES = "일정 파악"

# 임베딩에 쓸 글자 수. 파일명과 앞부분만으로도 문서의 정체는 거의 드러난다.
EMBED_CHARS = 1200
EMBED_BATCH = 32

# 시점 추정에 쓸 앞쪽 조각 수. 문서 자신의 날짜는 표지·머리말에 있다.
DATE_SECTIONS = 6


@dataclass
class StageReport:
    stage: str
    done: int = 0
    total: int = 0
    note: str = ""
    errors: list[str] = field(default_factory=list)


class Pipeline(QObject):
    """워커 스레드에서 도는 처리 본체.

    DB 연결은 스레드마다 따로 열어야 하므로 경로만 받아 여기서 연다.
    """

    progress = Signal(str, int, int, str)   # stage, done, total, note
    stage_done = Signal(object)             # StageReport
    finished = Signal()
    failed = Signal(str)

    def __init__(self, db_path: Path | str, parent: QObject | None = None):
        super().__init__(parent)
        self._db_path = Path(db_path)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _stopped(self) -> bool:
        return self._stop

    # ── 실행 ────────────────────────────────────────────────────────
    def run(self) -> None:
        db = Database(self._db_path)
        try:
            db.audit("pipeline.start")
            self._scan(db)
            if not self._stop:
                self._hash(db)
            if not self._stop:
                self._parse(db)
            if not self._stop:
                self._date(db)
            if not self._stop:
                self._embed(db)
            if not self._stop:
                self._discover(db)
            if not self._stop:
                self._cycles(db)
            db.audit("pipeline.stop", result="cancelled" if self._stop else "completed")
        except Exception as exc:  # 워커가 조용히 죽으면 사용자는 영문을 모른다
            import traceback

            db.audit("pipeline.error", detail=type(exc).__name__, result="failed")
            self.failed.emit(
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}"
            )
        finally:
            db.close()
            self.finished.emit()

    # ── 1단계: 찾기 ─────────────────────────────────────────────────
    def _scan(self, db: Database) -> None:
        sources = db.sources()
        report = StageReport(STAGE_SCAN)
        total = scanner.ScanStats()

        for source in sources:
            if self._stop:
                break
            stats = scanner.scan_source(
                db,
                source["id"],
                source["path"],
                source["excludes"],
                on_progress=lambda n, name: self.progress.emit(STAGE_SCAN, n, 0, name),
                should_stop=self._stopped,
            )
            total.merge(stats)

        report.done = total.found
        report.total = total.found
        report.errors = total.errors[:20]
        report.note = (
            f"문서 {total.documents:,}건 · 새 파일 {total.new:,} · "
            f"변경 {total.changed:,} · 원본 없음 {total.missing:,}"
        )
        self.stage_done.emit(report)

    # ── 2단계: 해시 (완전 중복 판정) ────────────────────────────────
    def _hash(self, db: Database) -> None:
        rows = db.con.execute(
            "SELECT id, path FROM documents "
            "WHERE hash IS NULL AND missing_since IS NULL AND parse_status != 'skipped'"
        ).fetchall()
        report = StageReport(STAGE_HASH, total=len(rows))

        for index, row in enumerate(rows, start=1):
            if self._stop:
                break
            digest, error = sha256(row["path"])
            if digest:
                db.update_document(row["id"], hash=digest)
            elif error:
                report.errors.append(f"{Path(row['path']).name}: {error}")
            report.done = index
            if index % 20 == 0 or index == len(rows):
                self.progress.emit(STAGE_HASH, index, len(rows), Path(row["path"]).name)

        extra = db.counts()["duplicate_extra"]
        report.note = f"완전 중복 {extra:,}건" if extra else "중복 없음"
        self.stage_done.emit(report)

    # ── 3단계: 내용 읽기 ────────────────────────────────────────────
    def _parse(self, db: Database) -> None:
        rows = db.con.execute(
            """
            SELECT d.id, d.path, d.filename
            FROM documents d
            LEFT JOIN jobs j ON j.doc_id = d.id AND j.kind = 'parse'
            WHERE d.missing_since IS NULL
              AND (d.parse_status = 'pending'
                   OR (d.parse_status IN ('ok','partial') AND d.parser_version != ?))
              AND COALESCE(j.attempts, 0) < ?
            ORDER BY d.id
            """,
            (PARSER_VERSION, MAX_ATTEMPTS),
        ).fetchall()
        report = StageReport(STAGE_PARSE, total=len(rows))
        failures = 0

        for index, row in enumerate(rows, start=1):
            if self._stop:
                break
            result = parse(row["path"])

            db.update_document(
                row["id"],
                parse_status=result.status,
                parse_error=result.error,
                parse_note=result.note,
                parser=result.parser,
                parser_version=result.parser_version,
                char_count=result.char_count,
                author=result.meta.author,
                doc_title=result.meta.title,
                meta_created=result.meta.created,
                meta_modified=result.meta.modified,
            )

            if result.ok:
                db.replace_sections(
                    row["id"],
                    [(s.kind, s.ordinal, s.locator, s.text) for s in result.sections],
                )
                db.index_document(
                    row["id"], row["filename"], row["path"],
                    result.meta.author or "", result.text,
                )
                _clear_job(db, row["id"], "parse")
            else:
                failures += 1
                _record_failure(db, row["id"], "parse", result.error or result.status)
                report.errors.append(f"{row['filename']}: {result.error or result.status}")

            report.done = index
            if index % 10 == 0 or index == len(rows):
                self.progress.emit(STAGE_PARSE, index, len(rows), row["filename"])

        counts = db.counts()
        report.note = f"읽음 {counts['parsed']:,}건"
        if failures:
            report.note += f" · 읽지 못함 {failures:,}건"
        report.errors = report.errors[:20]
        self.stage_done.emit(report)


    # ── 4단계: 시점 확인 ────────────────────────────────────────────
    def _date(self, db: Database) -> None:
        """When과 How 전체가 이 단계에 걸려 있다.

        후보를 다섯 갈래로 모아 우선순위대로 하나를 채택하되, 채택하지 않은
        후보도 남긴다. 화면에서 "무엇을 근거로 2024년이라 했는지" 보여줘야
        사용자가 검증하고 고칠 수 있다.
        """
        rows = db.con.execute(
            "SELECT id, filename, path, fs_mtime, meta_created, meta_modified "
            "FROM documents "
            "WHERE eff_date IS NULL AND missing_since IS NULL AND parse_status != 'skipped'"
        ).fetchall()
        report = StageReport(STAGE_DATE, total=len(rows))
        undated = 0

        for index, row in enumerate(rows, start=1):
            if self._stop:
                break
            sections = db.con.execute(
                "SELECT locator, text FROM document_sections "
                "WHERE doc_id = ? ORDER BY ordinal LIMIT ?",
                (row["id"], DATE_SECTIONS),
            ).fetchall()

            resolution = dating.collect(
                sections=[(s["locator"], s["text"]) for s in sections],
                filename=row["filename"],
                path=row["path"],
                meta_created=row["meta_created"],
                meta_modified=row["meta_modified"],
                fs_mtime=row["fs_mtime"],
            )

            db.replace_dates(row["id"], [c.as_row() for c in resolution.candidates])
            db.update_document(
                row["id"],
                eff_date=resolution.value,
                eff_date_kind=resolution.kind,
                eff_precision=resolution.precision,
                eff_year=resolution.year,
                eff_month=resolution.month,
            )
            if resolution.value is None:
                undated += 1

            report.done = index
            if index % 20 == 0 or index == len(rows):
                self.progress.emit(STAGE_DATE, index, len(rows), row["filename"])

        breakdown = db.con.execute(
            "SELECT eff_date_kind AS kind, COUNT(*) AS n FROM documents "
            "WHERE eff_date IS NOT NULL GROUP BY eff_date_kind"
        ).fetchall()
        parts = [f"{_KIND_LABEL.get(r['kind'], r['kind'])} {r['n']:,}" for r in breakdown]
        report.note = " · ".join(parts) if parts else "시점을 찾지 못함"
        if undated:
            report.note += f" · 단서 없음 {undated:,}"
        self.stage_done.emit(report)


    # ── 5단계: 의미 색인 ────────────────────────────────────────────
    def _embed(self, db: Database) -> None:
        """전 문서를 벡터로 만든다. 업무 발견(군집화)의 재료다.

        생성 모델은 문서 1만 건에 27시간이 걸리지만 임베딩은 11분이면 끝난다.
        그래서 전수 처리는 여기서 하고, 생성 모델은 묶음 대표에만 쓴다.

        Ollama가 없으면 조용히 건너뛴다 — AI는 단일 장애점이 아니다.
        """
        client = OllamaClient()
        health = client.health()
        if not health.embedding_ready:
            self.stage_done.emit(
                StageReport(STAGE_EMBED, note=f"건너뜀 — {health.message}")
            )
            return

        rows = db.con.execute(
            """
            SELECT d.id, d.filename, di.body
            FROM documents d
            JOIN document_index di ON di.doc_id = d.id
            LEFT JOIN doc_embeddings e ON e.doc_id = d.id AND e.model = ?
            WHERE d.missing_since IS NULL
              AND d.parse_status IN ('ok', 'partial')
              AND e.doc_id IS NULL
            ORDER BY d.id
            """,
            (client.embed_model,),
        ).fetchall()
        report = StageReport(STAGE_EMBED, total=len(rows))

        for start in range(0, len(rows), EMBED_BATCH):
            if self._stop:
                break
            batch = rows[start : start + EMBED_BATCH]
            texts = [_embed_text(r["filename"], r["body"]) for r in batch]
            vectors, error = client.embed(texts)
            if error:
                report.errors.append(error)
                break   # 연결이 끊긴 상태에서 계속 두드릴 이유가 없다

            for row, vector, text in zip(batch, vectors, texts):
                db.save_doc_embedding(
                    row["id"], client.embed_model, len(vector), pack(vector), len(text)
                )
            report.done = min(start + len(batch), len(rows))
            self.progress.emit(STAGE_EMBED, report.done, len(rows), batch[-1]["filename"])

        total = db.counts()["embedded"]
        report.note = f"색인 {total:,}건"
        if report.errors:
            report.note += " · 중단됨"
        self.stage_done.emit(report)


    # ── 6단계: 업무 파악 (What) ─────────────────────────────────────
    def _discover(self, db: Database) -> None:
        """벡터를 묶어 업무 후보를 만들고 묶음마다 한 번씩 이름을 묻는다.

        문서마다 모델에게 분류를 물으면 1만 건에 7시간이 걸리지만, 묶은 뒤
        묶음당 한 번만 부르면 수십 번으로 끝난다. 이름이 흩어지는 것도 막는다.
        """
        if db.counts()["embedded"] == 0:
            self.stage_done.emit(
                StageReport(STAGE_DISCOVER, note="건너뜀 — 의미 색인이 없습니다")
            )
            return

        client = OllamaClient()
        report = StageReport(STAGE_DISCOVER)

        def progress(done: int, total: int, label: str) -> None:
            report.done, report.total = done, total
            self.progress.emit(STAGE_DISCOVER, done, total, label)

        result = discover(db, client, on_progress=progress)
        report.total = report.total or result.clusters
        report.done = report.total
        report.note = result.summary()
        report.errors = result.errors[:20]
        self.stage_done.emit(report)


    # ── 7단계: 일정 파악 (When) ─────────────────────────────────────
    def _cycles(self, db: Database) -> None:
        """업무마다 연도×월 격자를 만들어 반복 주기를 찾는다.

        AI를 쓰지 않는다 — 시점 데이터로 하는 순수 계산이라 Ollama가 꺼져
        있어도 이 단계는 항상 돈다(제품 원칙 2).
        """
        tasks = db.tasks()
        report = StageReport(STAGE_CYCLES, total=len(tasks))
        found = 0

        for index, task in enumerate(tasks, start=1):
            rows = db.task_grid_documents(task["id"])
            docs = [
                timeline.DatedDoc(
                    doc_id=row["id"],
                    year=row["year"],
                    month=row["month"],
                    day=int(row["eff_date"][8:10]) if row["eff_precision"] == "day" else None,
                    trustworthy=row["eff_date_kind"] != "fs",
                )
                for row in rows
            ]
            grid = timeline.build_grid(docs)
            guess = timeline.detect_cycle(grid)

            cycles = []
            if guess:
                found += 1
                cycles.append({
                    "kind": guess.kind,
                    "months": ",".join(str(m) for m in guess.months),
                    "day_hint": timeline.day_hint(docs, guess.months or None),
                    "years_observed": guess.years_observed,
                    "confidence": guess.confidence,
                    "evidence": ",".join(str(i) for i in guess.evidence_doc_ids),
                })
            db.replace_task_cycles(task["id"], cycles)

            if index % 5 == 0 or index == len(tasks):
                self.progress.emit(STAGE_CYCLES, index, len(tasks), task["name"])

        report.done = report.total
        report.note = f"반복 업무 {found}개 발견" if tasks else "업무가 없습니다"
        # cycles_found==0(반복을 하나도 못 찾음)과 '아직 안 살펴봄'을 구분해야
        # 매 실행마다 파이프라인이 불필요하게 다시 도는 것을 막을 수 있다.
        db.set_meta("cycles_checked", "1")
        self.stage_done.emit(report)


def _embed_text(filename: str, body: str) -> str:
    """파일명을 앞에 붙인다. 공직 자료는 파일명에 업무 단서가 많이 들어 있다."""
    head = " ".join((body or "").split())[:EMBED_CHARS]
    return f"{filename}\n{head}".strip()


_KIND_LABEL = {
    "body": "본문",
    "filename": "파일명",
    "meta": "문서속성",
    "folder": "폴더",
    "fs": "파일날짜",
}


class PipelineRunner(QObject):
    """UI 쪽에서 파이프라인을 켜고 끄는 손잡이.

    QThread를 직접 상속하지 않고 moveToThread를 쓴다. 그래야 워커 객체의
    슬롯이 워커 스레드에서 돌고, DB 연결이 스레드를 넘나들지 않는다.
    """

    progress = Signal(str, int, int, str)
    stage_done = Signal(object)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, db_path: Path | str, parent: QObject | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self._thread: QThread | None = None
        self._pipeline: Pipeline | None = None

        # 창의 closeEvent를 거치지 않고 끝나는 종료 경로가 있다. 실행 중인
        # QThread가 그대로 파괴되면 프로세스가 죽으므로 종료 신호에도 건다.
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self) -> None:
        if self.running:
            return
        self._thread = QThread()
        self._pipeline = Pipeline(self._db_path)
        self._pipeline.moveToThread(self._thread)

        self._pipeline.progress.connect(self.progress)
        self._pipeline.stage_done.connect(self.stage_done)
        self._pipeline.failed.connect(self.failed)
        self._pipeline.finished.connect(self._cleanup)
        self._thread.started.connect(self._pipeline.run)
        self._thread.start()

    def stop(self, wait_ms: int = 5000) -> None:
        """일시정지 요청 후 안전 지점에서 멈춘다."""
        if self._pipeline is not None:
            self._pipeline.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(wait_ms)

    def _cleanup(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
            self._thread.deleteLater()
        if self._pipeline is not None:
            self._pipeline.deleteLater()
        self._thread = None
        self._pipeline = None
        self.finished.emit()


def _record_failure(db: Database, doc_id: int, kind: str, error: str) -> None:
    db.con.execute(
        "INSERT INTO jobs(kind, doc_id, state, attempts, error, tool_version) "
        "VALUES (?, ?, 'failed_retryable', 1, ?, ?) "
        "ON CONFLICT(kind, doc_id) DO UPDATE SET "
        "  attempts = jobs.attempts + 1, error = excluded.error, "
        "  state = CASE WHEN jobs.attempts + 1 >= ? THEN 'failed_final' ELSE 'failed_retryable' END, "
        "  updated_at = datetime('now')",
        (kind, doc_id, error[:500], PARSER_VERSION, MAX_ATTEMPTS),
    )


def _clear_job(db: Database, doc_id: int, kind: str) -> None:
    db.con.execute("DELETE FROM jobs WHERE kind = ? AND doc_id = ?", (kind, doc_id))
