# RAG 전략 정리 + backend-new 비교 (2026-08-25 세션 메모)

이 문서는 2026-08-25 세션에서 (1) 현재 `backend`의 RAG 파이프라인을 코드 레벨로 조사하고
(2) 같은 `sidepjt/` 루트의 형제 디렉토리 `backend-new`(별도 git repo, main에 머지된
`feature/rag-mvp` 프로토타입)와 비교한 내용을 정리한 것이다. 다른 세션에서 이어서 작업할 때
바로 참고할 수 있도록 파일 경로·라인 번호를 최대한 남겨 뒀다. **단, 이 문서는 스냅샷이다 —
실제 작업 전에 언급된 파일이 여전히 그 내용인지 다시 확인할 것.**

---

## 1. 현재 backend의 RAG 파이프라인

전체 흐름:

```
질문 → (후속 질문이면) 검색용 재작성 → 로컬 임베딩(활성 모델)
     → pgvector 코사인 검색 + PostgreSQL FTS 검색 → RRF 융합
     → 형제(heading_path) 청크 확장 → 컨텍스트 조립(정렬+절단)
     → GPT 프롬프트(근거만 사용, [n] 인용 강제) → 답변 + 인용 매핑
     → rag_conversations/rag_messages 저장
```

검색(Retrieval)과 생성(Generation)이 명확히 분리되어 있다 — `RagService.search()`는
①(검색)만, `RagService.query()`는 ①+②(검색+GPT) 전체를 담당한다
([app/services/rag_service.py:1-7](../app/services/rag_service.py#L1-L7)).

### 1.1 청킹 (Document Core → Chunk)

`app/services/chunking_service.py`가 오케스트레이션만 담당하고, 실제 분할 로직은
`app/services/chunking/` 패키지에 있다.

heading 유무로 전략을 **자동 분기**한다
([chunking_service.py:54-59](../app/services/chunking_service.py#L54-L59)):

- **heading이 있는 문서** (사내 규정 등) → **structure-aware 청킹**
  (`app/services/chunking/structure_chunker.py`)
  - heading 섹션 자체는 청크로 만들지 않고 breadcrumb(`heading_path`)로만 쓴다.
  - 같은 heading 아래 본문 섹션들을 `min_chars`(기본 300자)에 닿을 때까지 뭉친다.
    **왜 뭉치는가**: `document_sections`는 파서/교정 apply를 위한 fine-grained 단위라
    문단 1개 = 섹션 1개다. 이 경계를 그대로 청크 경계로 승격시키면 단어 조각이 RAG 검색
    단위가 되어 컨텍스트로 못 쓴다 (실측: structure 청크의 83%가 섹션 1개 그대로, 그중
    72%가 50자 미만). `split_to_size`는 상한(max_chars)만 있고 하한이 없어 이를 못 막아서
    이 청커에서 별도로 뭉친다 ([structure_chunker.py:6-11](../app/services/chunking/structure_chunker.py#L6-L11)).
  - `max_chars`(기본 1000자) 상한을 넘으면 문장 경계 기준 `split_to_size`로 안전 분할,
    `overlap`(기본 150자) 적용.
  - 다른 heading 아래거나 section_type이 다르면(paragraph vs table) 섞지 않고 flush.

- **heading이 없는 자유 형식 문서** (txt/hwpx, 스타일 없는 docx) → **semantic 청킹**
  (`app/services/chunking/semantic_chunker.py`)
  - LlamaIndex `SemanticSplitterNodeParser` 방식: 문장을 `sentence_window`(기본 3문장)
    슬라이딩 윈도우로 묶어 임베딩.
  - 인접 윈도우 간 **코사인 거리**가 튀는 지점을 percentile breakpoint(기본 95)로 잡아
    경계로 삼는다 — 벡터가 이미 정규화되어 있어 내적 = 코사인 유사도
    ([semantic_chunker.py:35-45](../app/services/chunking/semantic_chunker.py#L35-L45)).
  - 경계 탐지용 임베딩이 필요한데 활성 임베딩 모델이 없으면 structure 청킹으로 폴백
    ([chunking_service.py:68-78](../app/services/chunking_service.py#L68-L78)).

공통 자료구조는 `app/services/chunking/base.py`: `ChunkingParams`(전략+파라미터),
`ChunkDraft`(저장 전 중간 표현), `split_to_size`(문장 경계 유지 분할 + hard-wrap 폴백).

### 1.2 임베딩

`app/services/embedding/local_provider.py` — `SentenceTransformerProvider`:

- `sentence-transformers` 로컬 모델을 백엔드 프로세스 안에서 직접 로드. 생성자에서는
  로드하지 않고 `load()`에서 지연 로딩(스레드 락으로 중복 로드 방지).
- 기본 모델: **KURE-v1** (`nlpai-lab/KURE-v1`), 1024차원
  (`app/core/config.py:58-59`: `embedding_model_key`, `embedding_model_name`).
- `embed_documents`/`embed_query` 모두 `normalize_embeddings=True` — **정규화된 벡터라
  내적 = 코사인 유사도**.
- `embedding_cache_dir = "../models"` — OS 공용 캐시 대신 프로젝트 하위 `models/`에 가중치
  저장(컨테이너 재시작 시 재다운로드 방지).
- `chunk`와 `chunk_embedding`은 1:N 분리 저장(embedding은 chunk 컬럼이 아님) — 여러 모델을
  나란히 비교/교체할 수 있게 하기 위한 설계(DESIGN.md 원칙).

인터페이스는 `app/services/embedding/base.py`의 `EmbeddingProvider` Protocol
(`load`, `embed_documents`, `embed_query`, `get_dimension`, `get_model_name`).

### 1.3 검색 (Retrieval) — 하이브리드 + RRF 융합

`app/services/rag/retriever.py`의 `Retriever.retrieve()`:

1. 활성 임베딩 모델(`EmbeddingModelRepository.get_active()`)로 질문 임베딩.
   활성 모델이 없으면 `NoActiveEmbeddingModelError`를 명시적으로 던진다 — "검색해도
   아무것도 안 나온다"와 "검색 자체가 불가능하다"는 운영상 다른 상황이라 조용히 빈 결과로
   넘기지 않는다 ([retriever.py:25-30](../app/services/rag/retriever.py#L25-L30)).
2. **벡터 검색** (`ChunkSearchRepository.search()`,
   [app/repositories/rag_repository.py:78-115](../app/repositories/rag_repository.py#L78-L115)):
   - `ChunkEmbedding.embedding.cosine_distance(query_vector)` → `score = 1 - distance`.
   - 지켜야 하는 필터 두 가지 (안 지키면 조용히 틀린 결과):
     1. `embedding_model_id = 활성 모델` — 문서와 질문이 같은 모델로 임베딩된 것끼리만
        비교(모델별 벡터가 한 테이블에 공존하므로 차원은 같아도 의미 공간이 다를 수 있음).
     2. `document_versions.id = documents.current_version_id` — 교정 apply로 v2가 생기면
        v1의 청크도 그대로 남아 있어서, 이 조건이 없으면 오탈자가 있던 구버전이 검색에 섞임.
3. **FTS 검색** (`search_fts()`, `rag_hybrid_enabled=True`가 기본):
   - `to_tsquery('simple', ...)`에 **접두어(`:*`) OR 매칭**. 'simple' config는 형태소 분석을
     안 해서 "배포는"과 "배포"가 다른 lexeme이라 정확 매칭(`plainto_tsquery`)은 한국어
     조사 있는 질문에서 거의 항상 0건 (실측 확인됨, "배포는 무슨 요일?" 예시)
     ([rag_repository.py:11-14](../app/repositories/rag_repository.py#L11-L14)).
   - 토큰은 OR로 묶는다(AND면 자연어 긴 문장이 거의 매칭 안 됨). 여러 토큰이 맞을수록
     `ts_rank_cd`가 자연히 더 높은 순위.
   - 한 글자 토큰(조사/어미 잔여물)은 제외, 최대 8개 토큰만 사용.
4. **RRF(Reciprocal Rank Fusion)** (`_rrf_fuse`,
   [retriever.py:144-171](../app/services/rag/retriever.py#L144-L171)):
   - 코사인 유사도(0~1)와 `ts_rank_cd`(임의 스케일)는 값을 직접 더할 수 없어 **등수만** 사용.
   - `score = Σ 1/(k + rank)`, `k = rag_hybrid_rrf_k`(기본 60, Elasticsearch 기본값).
   - 결과 청크의 `.score`는 이 융합 점수이지 원본 코사인 유사도가 아님 — citation에
     노출되는 값이므로 주의.
5. 후보 풀은 최종 top_k보다 넉넉히(`rag_hybrid_candidate_k`, 기본 20) 뽑아 RRF가 순위를
   재배열할 여지를 둔다.

### 1.4 형제 청크 확장 (Sibling Expansion)

`Retriever._expand()`
([retriever.py:173-205](../app/services/rag/retriever.py#L173-L205)):
검색은 조문의 한 조각만 맞히는데 답변엔 그 조/절 전체가 필요한 경우가 많아서, 같은
`heading_path` 아래 나머지 청크를 `rag_expand_max_extra`(기본 6)개까지 추가
(`ChunkSearchRepository.siblings_of()`). semantic 청킹 결과는 `heading_path`가 비어 있어
확장 안 됨. 확장분(`expanded=True`)은 인용 대상에서 제외, 컨텍스트로만 사용, 부모 청크의
점수를 그대로 물려받음(직접 검색된 게 아니므로).

### 1.5 질문 재작성 (Query Rewrite)

`RagService._rewrite_for_search()`
([rag_service.py:250-266](../app/services/rag_service.py#L250-L266)):
후속 질문("그건 언제야?" 같은 대명사 질문)을 대화 이력 기반으로 **검색 전용 독립 질문**으로
재작성. 답변 생성 프롬프트에는 영향 없이 원래 질문 그대로 사용, 검색에만 재작성된 질문을
씀. LLM 호출 실패 시 원문으로 안전 폴백(검색 자체를 막으면 안 되므로).

### 1.6 컨텍스트 조립

`app/services/rag/context.py`의 `build_contexts()`: 청크당 `rag_context_max_chars`
(기본 1200자)로 절단하고 1부터 번호 매김. RAG 답변과 퀴즈 AI 생성이 같은 절단/번호매기기
규칙을 공유(정렬 규칙만 호출자가 다르게 함 — RAG는 점수순, 퀴즈는 문서 등장순).

`RagService._contexts()` ([rag_service.py:108-121](../app/services/rag_service.py#L108-L121)):
직접 검색된 청크를 점수 순으로 먼저, 확장분을 문서 등장순으로 뒤에 붙임.

### 1.7 답변 생성 (GPT)

`app/services/llm/prompts.py`:
- 시스템 프롬프트(`RAG_SYSTEM`, `RAG_SYSTEM_STREAM`)가 "근거 문서 조각만 사용, 없으면
  모른다고 답변, `[1][3]` 형태로 인용 표기" 강제
  ([prompts.py:66-80](../app/services/llm/prompts.py#L66-L80)).
- 일반(비스트리밍) 요청은 JSON `{"answer", "citations"}` 강제 출력.
- 스트리밍은 텍스트를 그대로 흘려보내고, `extract_cited_indexes()`가 정규식 `\[(\d+)\]`로
  인용 번호를 등장 순서대로 추출 + `valid_indexes`로 환각 방어
  ([app/services/llm/base.py:136-150](../app/services/llm/base.py#L136-L150)).
- 이전 대화(최근 `rag_history_turns`=4턴)는 근거 블록과 분리해서 프롬프트에 넣음 — 섞으면
  모델이 이전 답변을 "문서 사실"로 재인용하는 문제 방지
  ([prompts.py:127-139](../app/services/llm/prompts.py#L127-L139)).

### 1.8 대화 이력 관리

`RagConversation`(1건, scope/title 보유) — `RagMessage`(role=user/assistant, citations,
provider, retrieved_count, latency_ms 등) 1:N 관계. `RagService.query()`(동기)와
`query_stream_prepare()`/`query_stream_events()`(SSE 스트리밍) 두 경로 모두 지원.
동일 대화 내에서는 스코프(project_ids/folder_ids)를 첫 질문 것으로 유지(중간에 범위가
바뀌면 인용이 뒤섞이기 때문).

### 1.9 주요 설정값

`app/core/config.py:70-86`:

| 설정 | 기본값 | 의미 |
|---|---|---|
| `rag_top_k` | 6 | 최종 반환 청크 수 |
| `rag_min_score` | 0.3 | 코사인 유사도 하한 |
| `rag_context_max_chars` | 1200 | 청크당 프롬프트 절단 길이 |
| `rag_expand_siblings` | True | 형제 청크 확장 여부 |
| `rag_expand_max_extra` | 6 | 확장 청크 최대 개수 |
| `rag_history_turns` | 4 | 프롬프트에 넣는 이전 대화 턴 수 |
| `rag_hybrid_enabled` | True | 벡터+FTS 하이브리드 (False면 벡터 단독, 롤백용) |
| `rag_hybrid_candidate_k` | 20 | 융합 전 후보 풀 크기 |
| `rag_hybrid_rrf_k` | 60 | RRF 감쇠 상수 |
| `rag_query_rewrite_enabled` | True | 후속 질문 재작성 여부 (False면 롤백용) |

### 1.10 관련 파일 전체 목록

- `app/services/chunking_service.py` — 청킹 오케스트레이션(전략 분기, 저장)
- `app/services/chunking/base.py` — 공통 자료구조 + `split_to_size`
- `app/services/chunking/structure_chunker.py` — heading 기반 구조 청킹
- `app/services/chunking/semantic_chunker.py` — 임베딩 거리 기반 semantic 청킹
- `app/services/embedding/base.py` — `EmbeddingProvider` Protocol
- `app/services/embedding/local_provider.py` — `SentenceTransformerProvider`(KURE-v1)
- `app/services/embedding/factory.py` — provider 인스턴스 생성/캐시
- `app/services/rag/retriever.py` — 하이브리드 검색 + RRF + 형제 확장
- `app/services/rag/context.py` — 근거 청크 → 프롬프트 블록 변환
- `app/services/rag_service.py` — 검색+GPT 오케스트레이션, 대화 이력, SSE 스트리밍
- `app/repositories/rag_repository.py` — pgvector 검색 + FTS 검색 + 대화/메시지 DB 접근
- `app/services/llm/prompts.py` — RAG 시스템 프롬프트, query rewrite 프롬프트
- `app/services/llm/base.py` — `LLMProvider` 인터페이스, `extract_cited_indexes`
- `app/models/document_chunk.py`, `app/models/chunk_embedding.py` — 청크/임베딩 테이블
- `app/models/rag_conversation.py`, `app/models/rag_message.py` — 대화 스레드 테이블
- `alembic/versions/07bbfce32694_add_rag_conversations_and_messages.py`
- `alembic/versions/c1a2b3d4e5f6_add_content_tsv_to_document_chunks.py` — FTS용 tsvector 컬럼
- `alembic/versions/40c2cc65c244_add_chunk_min_chars_to_chunking_configs.py`

---

## 2. backend-new와의 비교

`backend-new`는 `sidepjt/` 루트의 형제 디렉토리(`c:\Users\pcn\Desktop\sidepjt\backend-new`)로,
**별도의 독립 git 저장소**다. `feature/rag-mvp` 브랜치가 이미 `main`에 머지된 상태이며,
커밋 로그(`9e160eb RAG 초기 구현`, `949ea38 청킹 재그룹핑`)와 브랜치명으로 볼 때
**MVP 초기 프로토타입 단계에서 멈춘 것으로 보인다** (지금 backend가 그 이후 발전형으로 추정).

### 2.1 비교표

| 항목 | backend (현재, 이 저장소) | backend-new |
|---|---|---|
| **청킹 전략** | heading 유무로 structure/semantic **자동 분기**. semantic은 문장 임베딩 거리 기반 경계 탐지 | 조/부칙(`제N조`, `부칙`) 정규식 패턴 하나로만 그룹핑 — 법률/규정 문서 전용, semantic 없음 |
| **청킹 파라미터** | `max_chars`(1000), `overlap`(150), `min_chars`(300), `sentence_window`(3), `breakpoint_percentile`(95) | `chunk_max_chars`(1000), `overlap`(150)만 — min_chars 없음 |
| **임베딩** | 로컬 KURE-v1, 1024차원, 정규화 | 동일(KURE-v1, 1024차원, 정규화) — **거의 같음** |
| **검색** | pgvector 코사인 + **PostgreSQL FTS 하이브리드 + RRF 융합** | pgvector 코사인 **단독** |
| **ANN 인덱스** | **없음** (§3 참조 — 확인 완료, 전체 스캔) | **HNSW 인덱스** 있음(`m=16, ef_construction=64, vector_cosine_ops`) |
| **형제 확장** | `heading_path` 기준 | `article_key`(조 단위) 기준 — 개념은 같음 |
| **질문 재작성** | 있음 (후속 질문 → 독립 질문) | **없음** |
| **대화 이력** | `rag_conversations`/`rag_messages` — 멀티턴 스레드 | `rag_queries` — **단발 질의 로그**(대화 개념 없음) |
| **인용 처리** | JSON `citations` / 스트리밍은 `[n]` 정규식 추출, 둘 다 valid_indexes로 환각 방어 | JSON `used_indices`, 유효 인덱스 필터 — 개념 유사 |
| **스트리밍(SSE)** | 있음 | 조사 시 언급 없음 — 없을 가능성 높음(미확인) |

### 2.2 backend-new 세부 내용 (조사 결과 원문 요약)

- **청킹**: `app/services/chunking_service.py`. `_ARTICLE_RE`로 "제N조/제N조의2/제N조(제목)/
  제N장/절", `_ADDENDUM_RE`로 "부칙"을 새 그룹 시작으로 인식하고 나머지(항/호/문장 조각)는
  직전 그룹에 병합(`_build_section_groups`). 그룹 내부는 문장 단위 `split_to_size`로 절단,
  넘치면 `_hard_wrap`. heading_path/article_key는 `chunk_metadata`(JSONB)에 저장.
- **임베딩**: `app/services/embedding/local_provider.py`. KURE-v1, 1024차원
  (`app/models/chunk_embedding.py:17`의 `EMBEDDING_DIM`), `normalize_embeddings=True`,
  provider는 프로세스당 1회 로드(`lru_cache`), device="cpu" 기본.
- **검색**: `app/repositories/embedding_repository.py:64-102`. `cosine_distance` 단일 쿼리 +
  `ORDER BY distance LIMIT top_k`, project_ids/folder_ids 필터만 부가. FTS/하이브리드 없음.
  `rag_top_k=6`, `rag_min_score=0.3`, `rag_context_max_chars=1200`.
- **HNSW 마이그레이션**: `alembic/versions/48c3cff4ac42_add_chunk_embeddings_hnsw_index.py`.
  `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)`.
  런타임 `ef_search` 튜닝 코드는 없음(기본값 사용 추정).
- **형제 확장**: `RagService._expand_siblings`(`rag_service.py:174-198`),
  `get_chunks_by_article_key`(`embedding_repository.py:104-136`)로 같은 조(article_key)의
  나머지 청크를 컨텍스트에 추가. 형제 청크는 원 매치의 distance를 그대로 물려받음.
- **질문 재작성/대화 이력**: 둘 다 없음. `ask()`는 단발 질의만 받음(`question` 단일 문자열).
  `/rag/history`(`api/v1/rag.py:50-61`)는 단순 최근 질의 목록 조회일 뿐 대화 컨텍스트로
  재사용 안 함.
- **인용**: 프롬프트에 `[번호] 출처: 문서명 > 섹션제목`으로 표기(`prompts.py:99-107`).
  LLM 응답 JSON `{"answer":..., "used_indices":[...]}` (`prompts.py:87-96`,
  `openai_provider.py:79-91`)에서 `used_indices`만 신뢰, 유효 index로 필터
  (`openai_provider.py:89-90`). 서버가 `used_indices`에 해당하는 검색 결과로
  `CitationRead` 조립(`rag_service.py:131-142`).
- **`rag_queries` 테이블** (`alembic/versions/c9ed22e2d607_add_rag_queries_table.py`):
  질문/답변/citations(JSONB)/project_ids·folder_ids/provider/embedding_model_id를
  한 행에 저장하는 **단발 질의 로그**, 대화 스레드나 role 개념 없음. 마이그레이션 주석
  (22-25줄)에 "ai_analysis_results는 document_id NOT NULL FK라 문서 1건당 분석 1건을
  전제하는데, RAG 질의는 여러 문서/프로젝트에 걸칠 수 있어 재사용하지 않는다"고 명시.
  → 현재 backend의 `rag_conversations`/`rag_messages`(멀티턴 스레드 모델)는 이것의
  발전형으로 보임(추정).

---

## 3. 확인 완료: backend에 HNSW 인덱스 없음

같은 세션에서 직접 확인한 사실 (2026-08-25):

- `alembic/versions` 전체를 `hnsw|ivfflat|vector_cosine_ops|CREATE INDEX` 로 검색해도
  매치 없음.
- `chunk_embeddings` 테이블은
  `alembic/versions/b41df68258a9_create_document_core_tables.py:121-130`에서 생성되는데,
  걸려 있는 제약은 PK와 `(chunk_id, embedding_model_id)` unique 뿐. `embedding` 벡터
  컬럼에 대한 ANN 인덱스는 **어떤 마이그레이션에도 없음**.

**결론**: 지금 `ChunkSearchRepository.search()`의 `cosine_distance` 쿼리
([app/repositories/rag_repository.py:90-106](../app/repositories/rag_repository.py#L90-L106))는
매번 전체 청크를 스캔하는 **정확 검색(exact scan)**이다. 문서/청크 수가 적은 지금은
문제없지만, 청크 수가 수만 건 이상으로 늘어나면 쿼리마다 선형 비용이 붙어 RAG 응답 지연이
눈에 띄게 늘어난다.

`vector_cosine_ops`는 지금 backend가 쓰는 `cosine_distance()`(`<=>` 연산자)와 정확히
맞아떨어지므로, backend-new의 `48c3cff4ac42_add_chunk_embeddings_hnsw_index.py`를
참고해 동일한 HNSW 인덱스를 backend에 이식하는 것이 합리적이다.

---

## 4. 다음 단계 (미완료, 세션 중단 시점)

사용자에게 제안했고 **아직 실행하지 않은** 작업:

1. **HNSW 인덱스 추가** (권장, 우선순위 높음) — 새 alembic 리비전을 만들어
   `chunk_embeddings.embedding`에
   `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)`
   추가. backend-new 마이그레이션을 그대로 참고 가능. **사용자 승인 대기 중이었음.**
2. **조/부칙 정규식 청킹 이식 여부 검토** (미착수) — 현재 backend의 `structure_chunker`는
   파서가 뽑아낸 `section_type == "heading"` 섹션에 의존하는데, hwpx/docx가 스타일 없이
   "제3조" 같은 텍스트만 있는 경우 heading으로 안 잡힐 수 있음. backend-new의 정규식
   패턴(`_ARTICLE_RE`, `_ADDENDUM_RE`)이 이런 케이스의 안전망이 될 수 있는지 실제 코드
   기준으로 확인 필요. 이번 세션에서는 **사용자가 우선순위에서 뒤로 미뤘음**
   (HNSW 확인을 먼저 선택).
3. **backend-new 폐기 여부 판단** — 기능 이식(HNSW, 정규식 청킹 폴백)이 끝난 뒤 결정할
   문제로 보류.

두 저장소가 별도 git repo이므로 코드 레벨 머지(`git merge`)는 불가능 — 기능 단위로
사람이 직접 이식해야 한다.
