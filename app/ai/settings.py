"""프로젝트마다 사용하는 로컬 모델. 저장소 연결은 호출한 스레드에서만 쓴다."""

from .client import OllamaClient, DEFAULT_GEN_MODEL, DEFAULT_EMBED_MODEL


def model_names(db) -> tuple[str, str]:
    return (db.get_meta("gen_model") or DEFAULT_GEN_MODEL,
            db.get_meta("embed_model") or DEFAULT_EMBED_MODEL)


def project_client(db, factory=OllamaClient):
    generation, embedding = model_names(db)
    return factory(gen_model=generation, embed_model=embedding)


def save_models(db, generation: str, embedding: str) -> bool:
    generation, embedding = generation.strip(), embedding.strip()
    if not generation or not embedding or any(c.isspace() for c in generation + embedding):
        raise ValueError("설치된 모델 이름을 공백 없이 입력하세요.")
    old = model_names(db)
    # 기존 벡터는 해당 모델의 재생성이 완료될 때 교체된다. UI·검색에서
    # 모델을 필터하므로 새 모델과 옛 모델을 섞어서 검색하지 않는다.
    with db.transaction():
        db.set_meta("gen_model", generation)
        db.set_meta("embed_model", embedding)
        if old[1] != embedding:
            db.con.execute("DELETE FROM meta WHERE key IN ('chunks_checked','cycles_checked','steps_checked')")
    db.audit("ai.models_changed", result="reindex" if old[1] != embedding else "ok")
    return old[1] != embedding
