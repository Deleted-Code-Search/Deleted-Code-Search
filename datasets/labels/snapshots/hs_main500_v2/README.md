# 희수 라벨의 다른 버전 스냅샷 (`hs_main500_v2.jsonl`)

같은 라벨러(hs)가 본 라벨링 500건의 자기 라벨에서 근거 등급을 고친 버전이다. **v3 점검의 결과(기준 0.6 이상·미달)가 원래 제출본과 같은지 확인하는 데만 쓰고, 확정에는 쓰지 않았다** — 확정(`datasets/labeled_500.jsonl`)과 제3자 판정은 원래 제출본 `datasets/labels/hs_main500.jsonl` 로 했다 (#166, `docs/reports/labels_main500.md` §3.3).

| 파일 | sha256 | 줄 |
|---|---|---:|
| `hs_main500_v2.jsonl` | `079faea6f512330df8ae20e23fa0f1b6c564b09bb2aefbaeca9ed4809787d15b` | 333 |

- 파일 이름의 `v2` 는 **파일 버전**이다. 가이드 버전(`guide_version`)이 아니고, `../v2_interim/`(1~50번 가이드 v2 스냅샷, #164)과도 다른 것이다
- 원래 제출본과 다른 줄은 25줄이다. 등급 INFERRED → EXPLICIT 22건, UNKNOWN → EXPLICIT 3건(이 3건은 이유도 UNK → FEAT·DEAD·LIB). `record_id` 순서와 `labeled_at` 은 같다
- v3 점검(51~100번) `evidence_grade` kappa: 블록 B 0.782, 블록 C 0.345 (원래 제출본 0.737, 0.233). B 이상·C 미달로 결과가 같다
- **이 디렉터리는 고치지 않는다.** 기록용이다

재현 (임시 폴더에 이 파일을 `hs_main500.jsonl` 이름으로 놓고 돌린다):

```bash
mkdir -p /tmp/hs_v2 && cp datasets/labels/{sj,jh}_main500.jsonl datasets/labels/main500_assignment.jsonl /tmp/hs_v2/
cp datasets/labels/snapshots/hs_main500_v2/hs_main500_v2.jsonl /tmp/hs_v2/hs_main500.jsonl
python -m classify.labels --labels-dir /tmp/hs_v2 --interim v3
```
