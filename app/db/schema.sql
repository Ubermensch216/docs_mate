-- 눈치코치 스키마 v1
--
-- 설계 원칙
--  1. 원본 파일 본체는 DB에 넣지 않는다. 경로와 분석 결과만 저장한다.
--  2. 시점(document_dates)은 후보를 지우지 않는다. 채택 결과와 근거를 분리
--     보관해야 "무엇을 근거로 2024년이라 했는지"를 보여줄 수 있고, 추정 규칙을
--     바꿔도 재파싱 없이 재계산할 수 있다.
--  3. AI 결과는 status를 갖는다. 사람이 고친 값은 재분석이 덮어쓰지 않는다.
--  4. 주기(task_cycles)와 순서(task_steps)는 실체화한다. 매번 계산하지 않고
--     저장해야 화면이 빠르고, 무엇보다 사용자 교정이 남는다.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ── 자료원 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    id        INTEGER PRIMARY KEY,
    path      TEXT NOT NULL UNIQUE,
    kind      TEXT NOT NULL DEFAULT 'local',   -- local | removable | unc
    excludes  TEXT NOT NULL DEFAULT '',        -- 줄바꿈 구분 패턴
    added_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 문서 ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY,
    source_id      INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    path           TEXT NOT NULL UNIQUE,
    filename       TEXT NOT NULL,
    ext            TEXT NOT NULL,
    size           INTEGER NOT NULL DEFAULT 0,
    fs_mtime       TEXT,
    fs_ctime       TEXT,
    hash           TEXT,                       -- SHA-256, 완전 중복 판정용
    author         TEXT,
    doc_title      TEXT,                       -- 문서 내부 제목 속성
    meta_created   TEXT,                       -- 문서 속성의 작성일 (시점 추정 근거 ③)
    meta_modified  TEXT,

    -- 채택된 시점 (document_dates에서 고른 결과)
    eff_date       TEXT,                       -- YYYY-MM-DD
    eff_date_kind  TEXT,                       -- body|filename|meta|folder|fs
    eff_precision  TEXT,                       -- day|month|year
    eff_year       INTEGER,
    eff_month      INTEGER,

    parse_status   TEXT NOT NULL DEFAULT 'pending',
    parse_error    TEXT,
    parse_note     TEXT,
    parser         TEXT,
    parser_version TEXT,
    char_count     INTEGER NOT NULL DEFAULT 0,

    analysis_status TEXT NOT NULL DEFAULT 'pending',
    missing_since   TEXT,                      -- 원본이 사라진 시각 (기록은 보존)
    first_seen      TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_documents_hash   ON documents(hash);
CREATE INDEX IF NOT EXISTS idx_documents_ext    ON documents(ext);
CREATE INDEX IF NOT EXISTS idx_documents_year   ON documents(eff_year, eff_month);
CREATE INDEX IF NOT EXISTS idx_documents_parse  ON documents(parse_status);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id);

-- 시점 후보. 하나의 문서가 여러 근거를 가질 수 있다 (ING-003).
CREATE TABLE IF NOT EXISTS document_dates (
    doc_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL,     -- body | filename | meta | folder | fs
    value     TEXT NOT NULL,     -- YYYY-MM-DD (미상 부분은 01로 채우고 precision으로 구분)
    precision TEXT NOT NULL,     -- day | month | year
    raw       TEXT,              -- 판정 근거가 된 원문 ("2024. 9. 12.")
    locator   TEXT,              -- 어느 조각에서 나왔는지 ("3쪽")
    PRIMARY KEY (doc_id, kind, value)
);

-- 본문 조각. locator는 근거 표시(RAG 출처)에 그대로 쓰인다 (PAR-002).
CREATE TABLE IF NOT EXISTS document_sections (
    id      INTEGER PRIMARY KEY,
    doc_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind    TEXT NOT NULL,       -- paragraph | table | page | sheet | slide | note | preview
    ordinal INTEGER NOT NULL,
    locator TEXT NOT NULL,
    text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sections_doc ON document_sections(doc_id, ordinal);

-- ── 통합 검색 ───────────────────────────────────────────────────────
-- 토크나이저는 trigram을 쓴다. 실측 결과 unicode61은 "행정사무감사"에서
-- "사무감사"를 찾지 못했고 trigram은 찾았다. 다만 trigram은 3글자 미만
-- 질의를 처리하지 못하므로, 2글자 이하는 LIKE로 폴백한다(search/fts.py).
CREATE TABLE IF NOT EXISTS document_index (
    doc_id   INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    filename TEXT NOT NULL DEFAULT '',
    path     TEXT NOT NULL DEFAULT '',
    author   TEXT NOT NULL DEFAULT '',
    body     TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(
    filename, path, author, body,
    content='document_index',
    content_rowid='doc_id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS document_index_ai AFTER INSERT ON document_index BEGIN
    INSERT INTO document_fts(rowid, filename, path, author, body)
    VALUES (new.doc_id, new.filename, new.path, new.author, new.body);
END;
CREATE TRIGGER IF NOT EXISTS document_index_ad AFTER DELETE ON document_index BEGIN
    INSERT INTO document_fts(document_fts, rowid, filename, path, author, body)
    VALUES ('delete', old.doc_id, old.filename, old.path, old.author, old.body);
END;
CREATE TRIGGER IF NOT EXISTS document_index_au AFTER UPDATE ON document_index BEGIN
    INSERT INTO document_fts(document_fts, rowid, filename, path, author, body)
    VALUES ('delete', old.doc_id, old.filename, old.path, old.author, old.body);
    INSERT INTO document_fts(rowid, filename, path, author, body)
    VALUES (new.doc_id, new.filename, new.path, new.author, new.body);
END;

-- ── 중복과 버전 ─────────────────────────────────────────────────────
-- 완전 중복은 해시로 결정된다. AI를 쓰지 않으므로 오탐이 없다 (DUP-001).
CREATE VIEW IF NOT EXISTS duplicate_groups AS
    SELECT hash, COUNT(*) AS n, MIN(id) AS representative_id
    FROM documents
    WHERE hash IS NOT NULL AND missing_since IS NULL
    GROUP BY hash HAVING COUNT(*) > 1;

-- 버전 후보는 이름·날짜·본문 유사도로 묶는 '제안'이다. 확정하지 않는다.
CREATE TABLE IF NOT EXISTS version_groups (
    id              INTEGER PRIMARY KEY,
    label           TEXT NOT NULL,
    latest_doc_id   INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    latest_reason   TEXT,           -- "'송부' 표기 + 최신 수정일"
    confidence      TEXT NOT NULL DEFAULT 'medium',
    decided_by      TEXT NOT NULL DEFAULT 'ai',   -- ai | user
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS version_members (
    group_id INTEGER NOT NULL REFERENCES version_groups(id) ON DELETE CASCADE,
    doc_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal  INTEGER NOT NULL DEFAULT 0,
    reason   TEXT,
    PRIMARY KEY (group_id, doc_id)
);

-- ── AI 문서 분석 (제안 상태로 저장) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_document (
    doc_id         INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    title          TEXT,
    doc_type       TEXT,
    summary        TEXT,
    keywords       TEXT,            -- JSON 배열
    importance     INTEGER,
    confidence     TEXT,            -- high | medium | low | unknown
    evidence       TEXT,            -- 판단 근거가 된 구절
    model          TEXT,
    prompt_version TEXT,
    status         TEXT NOT NULL DEFAULT 'proposed',  -- proposed | approved | edited
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 업무 (What) ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    origin      TEXT NOT NULL DEFAULT 'ai',   -- ai | user | seeded
    status      TEXT NOT NULL DEFAULT 'proposed',
    confidence  TEXT NOT NULL DEFAULT 'medium',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_docs (
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    doc_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    origin     TEXT NOT NULL DEFAULT 'ai',    -- ai | user
    confidence TEXT NOT NULL DEFAULT 'medium',
    evidence   TEXT,
    PRIMARY KEY (task_id, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_task_docs_doc ON task_docs(doc_id);

-- 먼저 읽을 문서. 추천 이유를 반드시 함께 저장한다 — 이유 없는 별점은
-- 신뢰를 만들지 못한다.
CREATE TABLE IF NOT EXISTS task_reading (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    doc_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    score   REAL NOT NULL DEFAULT 0,
    reason  TEXT NOT NULL,
    PRIMARY KEY (task_id, doc_id)
);

-- ── 주기 (When) ─────────────────────────────────────────────────────
-- 타임라인 격자를 세로로 읽은 결과.
CREATE TABLE IF NOT EXISTS task_cycles (
    id             INTEGER PRIMARY KEY,
    task_id        INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,        -- monthly | quarterly | yearly
    months         TEXT NOT NULL,        -- "9,10,11" (yearly) / "" (monthly)
    day_hint       TEXT,                 -- "5~10일"
    years_observed INTEGER NOT NULL DEFAULT 0,
    confidence     TEXT NOT NULL DEFAULT 'low',  -- high(3년+) | medium(2년) | low
    decided_by     TEXT NOT NULL DEFAULT 'ai',
    evidence       TEXT                  -- 근거 문서 id JSON 배열
);
CREATE INDEX IF NOT EXISTS idx_cycles_task ON task_cycles(task_id);

-- ── 처리 순서 (How) ─────────────────────────────────────────────────
-- 타임라인 격자를 가로로 읽은 결과. 모든 단계는 문서에 앵커링된다.
CREATE TABLE IF NOT EXISTS task_steps (
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    year        INTEGER NOT NULL,
    ordinal     INTEGER NOT NULL,
    label       TEXT NOT NULL,
    month       INTEGER,
    day_hint    TEXT,                    -- "9월 초"
    doc_id      INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    is_inferred INTEGER NOT NULL DEFAULT 0,   -- 1이면 근거 없는 추정 — UI에서 점선
    gap_note    TEXT,                    -- "③과 ④ 사이 2주는 확인되지 않음"
    decided_by  TEXT NOT NULL DEFAULT 'ai'
);
CREATE INDEX IF NOT EXISTS idx_steps_task ON task_steps(task_id, year, ordinal);

-- ── 질문·근거 (Step 9) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
    id      INTEGER PRIMARY KEY,
    doc_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    locator TEXT NOT NULL,
    text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model    TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vector   BLOB NOT NULL             -- float32 배열
);

-- 문서 단위 벡터. 업무 발견(군집화)의 재료다.
-- 생성 모델은 문서 1만 건에 27시간이 걸리지만 임베딩은 11분이면 끝난다.
-- 그래서 전수 처리는 여기서 하고 생성 모델은 묶음 대표에만 쓴다.
CREATE TABLE IF NOT EXISTS doc_embeddings (
    doc_id     INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    source_len INTEGER NOT NULL DEFAULT 0,   -- 임베딩에 쓴 글자 수
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS questions (
    id         INTEGER PRIMARY KEY,
    question   TEXT NOT NULL,
    filters    TEXT,
    answer     TEXT,
    citations  TEXT,                   -- JSON: [{doc_id, locator}]
    withheld   INTEGER NOT NULL DEFAULT 0,  -- 근거 부족으로 유보했는가
    model      TEXT,
    rating     TEXT,
    asked_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── 교정·작업·감사 ──────────────────────────────────────────────────
-- 사람이 고친 값은 자동 재분석으로 덮어쓰지 않는다 (NFR-SAF-004).
CREATE TABLE IF NOT EXISTS corrections (
    id         INTEGER PRIMARY KEY,
    target     TEXT NOT NULL,          -- ai_document.task | task_cycles | task_steps ...
    target_id  INTEGER,
    doc_id     INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    field      TEXT NOT NULL,
    before_val TEXT,
    after_val  TEXT,
    scope      TEXT NOT NULL DEFAULT 'single',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,        -- scan | hash | parse | date | analyze | embed
    doc_id       INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    state        TEXT NOT NULL DEFAULT 'pending',
    attempts     INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    input_hash   TEXT,                 -- 입력이 바뀌면 재실행 대상
    tool_version TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(kind, state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_unique ON jobs(kind, doc_id);

-- 로그에는 본문이나 민감정보를 남기지 않는다 (SEC-005).
CREATE TABLE IF NOT EXISTS audit_logs (
    id        INTEGER PRIMARY KEY,
    action    TEXT NOT NULL,
    target    TEXT,
    detail    TEXT,
    result    TEXT,
    at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_logs(at);
