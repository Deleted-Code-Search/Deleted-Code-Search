import { useEffect, useState } from "react";
import { getJson } from "../api";
import { GRADE_LABEL, KIND_LABEL, REASON_LABEL, SOURCE_NOTE, num } from "../format";
import type { RecordDetailResponse } from "../types";
import { ReasonBadges } from "./ReasonBadges";

interface Props {
  id: string;
  onClose: () => void;
}

// 결과 하나의 상세 (#191). 레코드 원문(/api/records/{id}) 을 그대로 보여준다.
export function RecordDetail({ id, onClose }: Props) {
  const [data, setData] = useState<RecordDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError(null);
    getJson<RecordDetailResponse>(`/api/records/${encodeURIComponent(id)}`, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (err.name !== "AbortError") setError(err.message);
      });
    return () => controller.abort();
  }, [id]);

  if (error) return <aside className="detail error">상세를 불러오지 못했다: {error}</aside>;
  if (!data) return <aside className="detail muted">불러오는 중…</aside>;

  const r = data.record;
  const ctx = r.context;
  const source = r.classification?.source ?? "";
  const linkOnly = r.code_policy === "LINK_ONLY";
  const lines = r.start_line != null ? `:${r.start_line}-${r.end_line ?? r.start_line}` : "";
  const issues = (ctx?.issue_numbers ?? []).map((n, i) => ({
    n,
    title: ctx?.issue_titles?.[i] ?? null,
  }));

  return (
    <aside className="detail" aria-label="레코드 상세">
      <div className="detail-head">
        <div>
          <h2>
            <code>{r.function_name ?? "(이름 없음)"}</code>
          </h2>
          <p className="muted">
            {r.repo} · <code>{r.file_path + lines}</code>
          </p>
          <p className="muted">
            {r.author_date?.slice(0, 10) ?? "날짜 없음"} · {KIND_LABEL[r.deletion_kind] ?? r.deletion_kind}
            {r.is_test_code && " · 테스트 코드"}
          </p>
        </div>
        <button className="close" onClick={onClose} aria-label="상세 닫기">
          ×
        </button>
      </div>

      <section>
        <h3>왜 지웠나</h3>
        <ReasonBadges source={source} label={r.reason?.label ?? null} grade={r.reason?.evidence_grade ?? null} />
        <p className="note">{SOURCE_NOTE[source]}</p>
        {r.reason && (
          <dl className="facts">
            <dt>이유</dt>
            <dd>{REASON_LABEL[r.reason.label] ?? r.reason.label}</dd>
            <dt>근거 등급</dt>
            <dd>{GRADE_LABEL[r.reason.evidence_grade] ?? r.reason.evidence_grade}</dd>
            <dt>근거 문장</dt>
            <dd>{r.reason.evidence_text || <span className="muted">없음</span>}</dd>
            {r.reason.classifier_version && (
              <>
                <dt>분류기</dt>
                <dd>
                  <code>{r.reason.classifier_version}</code>
                </dd>
              </>
            )}
          </dl>
        )}
      </section>

      <section>
        <h3>지워진 코드</h3>
        {linkOnly ? (
          <p className="notice">
            라이선스 때문에 코드 미제공 (<code>LINK_ONLY</code>).{" "}
            <a href={r.source_url} target="_blank" rel="noreferrer">
              GitHub 에서 보기
            </a>
          </p>
        ) : (
          <pre className="code-block">{r.deleted_body ?? ""}</pre>
        )}
      </section>

      {!linkOnly && r.replacement?.code && (
        <section>
          <h3>대신 들어간 코드</h3>
          <p className="muted">
            {r.replacement.match_method ?? "매칭 방법 없음"}
            {r.replacement.confidence != null && ` · 신뢰도 ${r.replacement.confidence.toFixed(2)}`}
          </p>
          <pre className="code-block">{r.replacement.code}</pre>
        </section>
      )}

      <section>
        <h3>커밋 메시지</h3>
        <pre className="text-block">{ctx?.commit_message ?? ""}</pre>
      </section>

      <section>
        <h3>PR · 이슈</h3>
        {ctx?.pr_labels == null && ctx?.pr_number == null ? (
          <p className="muted">맥락 결합 대상이 아니다 (PARTIAL 또는 PR 없음)</p>
        ) : (
          <>
            {ctx?.pr_number != null ? (
              <p>
                PR #{ctx.pr_number} <strong>{ctx.pr_title}</strong>
                {ctx.pr_labels && ctx.pr_labels.length > 0 && (
                  <span className="muted"> · 라벨 {ctx.pr_labels.join(", ")}</span>
                )}
              </p>
            ) : (
              <p className="muted">PR 없음</p>
            )}
            {ctx?.pr_body && <pre className="text-block scroll">{ctx.pr_body}</pre>}
            {issues.length > 0 ? (
              <ul className="issues">
                {issues.map((issue) => (
                  <li key={issue.n}>
                    #{issue.n} {issue.title ?? <span className="muted">(제목 없음)</span>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">참조 이슈 없음</p>
            )}
            <p className="muted">
              리뷰 코멘트{" "}
              {data.review_comment_count == null ? "정보 없음" : `${num(data.review_comment_count)}개`}
            </p>
          </>
        )}
      </section>

      <p>
        <a href={r.source_url} target="_blank" rel="noreferrer">
          GitHub 커밋 열기 ↗
        </a>
        <span className="muted"> · id {r.id}</span>
      </p>
    </aside>
  );
}
