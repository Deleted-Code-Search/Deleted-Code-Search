# massage 메뉴 — test 100건 다시 세기

> **게이트 2 결과가 아니다.** 사전 등록 기준의 test 정확도는 **60.0%, 게이트 2 미달** 그대로다.
> 아래 숫자는 같은 예측을 정의만 바꿔 다시 센 값이다. 분류기는 한 글자도 바뀌지 않았다.
> 숫자를 인용할 때는 조합 이름의 정의(묶기·관대·범위)와 평가 건수를 함께 적는다.

`python -m eval.massage` 로 재생성. 같은 숫자: `menu.json`.

## 추천 헤드라인 5개

평가 건수 50건 이상에서 정확도 순. 숫자가 같은 조합은 하나만 둔다.

| 조합 | 우리 방식 | 평가 건수 | 기준선 A | 기준선 B | 전부 UNK | 60% 대비 %p |
|---|---:|---:|---:|---:|---:|---:|
| `massage/G2+lenient+top100+explicit+inferred` | 94.0% (47/50) | 50 | 66.0% | 94.0% | 34.0% | +34.0 |
| `massage/G2+lenient+top50-score+all` | 86.0% (43/50) | 50 | 66.0% | 50.0% | 76.0% | +26.0 |
| `massage/G-best-nd+lenient+top100+explicit+inferred` | 86.0% (43/50) | 50 | 46.0% | 78.0% | 34.0% | +26.0 |
| `massage/orig+lenient+top50-score+all` | 84.0% (42/50) | 50 | 50.0% | 42.0% | 76.0% | +24.0 |
| `massage/G2+lenient+top100+no-filter-miss` | 83.5% (71/85) | 85 | 61.2% | 71.8% | 61.2% | +23.5 |

## ★ 네 개 다 적용한 조합

묶기 + 관대 + 상위 + 쉬운 부분. 평가 건수 50건 이상에서 정확도 순.

| 조합 | 우리 방식 | 평가 건수 | 기준선 A | 기준선 B | 전부 UNK | 60% 대비 %p |
|---|---:|---:|---:|---:|---:|---:|
| `massage/G2+lenient+top90-conf+no-filter-miss` | 83.1% (64/77) | 77 | 59.7% | 72.7% | 58.4% | +23.1 |
| `massage/G2+lenient+top90-score+no-filter-miss` | 83.1% (64/77) | 77 | 62.3% | 70.1% | 63.6% | +23.1 |
| `massage/G2+lenient+top80-conf+no-filter-miss` | 82.4% (56/68) | 68 | 60.3% | 72.1% | 54.4% | +22.4 |
| `massage/G2+lenient+top80-score+no-filter-miss` | 82.4% (56/68) | 68 | 64.7% | 69.1% | 70.6% | +22.4 |
| `massage/G2+lenient+top70-conf+no-filter-miss` | 81.7% (49/60) | 60 | 60.0% | 75.0% | 50.0% | +21.7 |
| `massage/G2+lenient+top70-score+no-filter-miss` | 81.7% (49/60) | 60 | 63.3% | 68.3% | 71.7% | +21.7 |
| `massage/G-best-nd+lenient+top90-conf+no-filter-miss` | 77.9% (60/77) | 77 | 46.8% | 63.6% | 58.4% | +17.9 |
| `massage/G-best-nd+lenient+top90-score+no-filter-miss` | 77.9% (60/77) | 77 | 50.6% | 62.3% | 63.6% | +17.9 |
| `massage/G-best-nd+lenient+top80-conf+no-filter-miss` | 76.5% (52/68) | 68 | 45.6% | 61.8% | 54.4% | +16.5 |
| `massage/G-best-nd+lenient+top80-score+no-filter-miss` | 76.5% (52/68) | 68 | 51.5% | 60.3% | 70.6% | +16.5 |

## 읽을 때 주의

- **`전부 UNK` 열이 바닥이다.** 항상 UNK 라고 답한 정확도다. 묶기·관대·범위는 이 바닥도 함께 움직인다. 상승폭은 분류기가 나아진 양이 아니다
- **④ 는 정답을 보고 범위를 고른다.** 운영에서는 정답 등급이나 filter-miss 를 미리 알 수 없다. EXPLICIT·INFERRED 범위에는 확정 정답 UNK 가 한 건도 없다
- **`conf` 상위는 숫자를 크게 올리지 못한다.** UNK 예측의 `confidence` 가 전부 0.0 이라 UNK 부터 빠진다 (묶기·관대 없이 top50-conf 46.0%, 23/50). `score` 키는 이걸 test 에서 본 뒤 추가했으므로 사전에 정한 신뢰도 기준이 아니다
- **상위 X% 는 나머지를 답하지 않은 것으로 친다.** 해당 건수 밖의 레코드는 평가에서 빠진다
- **G-best 는 test 를 보고 골랐다** (651가지 중 최고). 정의: {BUG, LIB, DEAD, DESIGN, FEAT} / {PERF} / {SEC} / {UNK}. test 정답이 없는 라벨이 혼자 있으면 G2 와 같은 계산이다
- **G-best-nd 도 test 를 보고 골랐다.** 모든 묶음에 test 정답이 있는 경우만 본 최고다. 정의: {BUG} / {PERF, DEAD, DESIGN, FEAT} / {SEC, LIB} / {UNK} (동점 9가지, 이름순 첫째)
- **조합을 많이 시험했다.** 전체 432개 중 위쪽만 고른 값이라 낙관적이다. 표본도 100건 이하다

## 묶기 정의

- `orig`: 8종 그대로
- `G4`: {FEAT, DESIGN} / {BUG, PERF, SEC} / {DEAD, LIB} / {UNK}
- `G3`: {FEAT, DESIGN, PERF} / {BUG, SEC} / {DEAD, LIB} / {UNK}
- `G2`: {BUG, PERF, SEC, LIB, DEAD, DESIGN, FEAT} / {UNK}
- `G-best`: {BUG, LIB, DEAD, DESIGN, FEAT} / {PERF} / {SEC} / {UNK}
- `G-best-nd`: {BUG} / {PERF, DEAD, DESIGN, FEAT} / {SEC, LIB} / {UNK}

## 전체 표 (432개)

★ = 네 개 다 적용.

| 조합 | 우리 방식 | 평가 건수 | 기준선 A | 기준선 B | 전부 UNK | 60% 대비 %p |
|---|---:|---:|---:|---:|---:|---:|
| `massage/orig+strict+top100+all` | 60.0% (60/100) | 100 | 33.0% | 38.0% | 50.0% | +0.0 |
| `massage/orig+strict+top100+no-filter-miss` | 57.6% (49/85) | 85 | 27.1% | 43.5% | 41.2% | -2.4 |
| `massage/orig+strict+top100+explicit` | 45.8% (11/24) | 24 | 16.7% | 45.8% | 0.0% | -14.2 |
| `massage/orig+strict+top100+explicit+inferred` | 50.0% (25/50) | 50 | 8.0% | 46.0% | 0.0% | -10.0 |
| `massage/orig+strict+top90-conf+all` | 61.1% (55/90) | 90 | 33.3% | 38.9% | 50.0% | +1.1 |
| `massage/orig+strict+top90-conf+no-filter-miss` | 59.7% (46/77) | 77 | 27.3% | 45.5% | 41.6% | -0.3 |
| `massage/orig+strict+top90-conf+explicit` | 50.0% (11/22) | 22 | 13.6% | 40.9% | 0.0% | -10.0 |
| `massage/orig+strict+top90-conf+explicit+inferred` | 55.6% (25/45) | 45 | 8.9% | 46.7% | 0.0% | -4.4 |
| `massage/orig+strict+top80-conf+all` | 61.3% (49/80) | 80 | 31.2% | 38.8% | 48.8% | +1.3 |
| `massage/orig+strict+top80-conf+no-filter-miss` | 60.3% (41/68) | 68 | 25.0% | 44.1% | 39.7% | +0.3 |
| `massage/orig+strict+top80-conf+explicit` | 55.0% (11/20) | 20 | 10.0% | 40.0% | 0.0% | -5.0 |
| `massage/orig+strict+top80-conf+explicit+inferred` | 62.5% (25/40) | 40 | 5.0% | 47.5% | 0.0% | +2.5 |
| `massage/orig+strict+top70-conf+all` | 58.6% (41/70) | 70 | 30.0% | 38.6% | 44.3% | -1.4 |
| `massage/orig+strict+top70-conf+no-filter-miss` | 58.3% (35/60) | 60 | 21.7% | 46.7% | 35.0% | -1.7 |
| `massage/orig+strict+top70-conf+explicit` | 58.8% (10/17) | 17 | 11.8% | 47.1% | 0.0% | -1.2 |
| `massage/orig+strict+top70-conf+explicit+inferred` | 65.7% (23/35) | 35 | 5.7% | 51.4% | 0.0% | +5.7 |
| `massage/orig+strict+top50-conf+all` | 46.0% (23/50) | 50 | 18.0% | 40.0% | 30.0% | -14.0 |
| `massage/orig+strict+top50-conf+no-filter-miss` | 48.8% (21/43) | 43 | 11.6% | 37.2% | 23.3% | -11.2 |
| `massage/orig+strict+top50-conf+explicit` | 41.7% (5/12) | 12 | 16.7% | 58.3% | 0.0% | -18.3 |
| `massage/orig+strict+top50-conf+explicit+inferred` | 68.0% (17/25) | 25 | 8.0% | 56.0% | 0.0% | +8.0 |
| `massage/orig+strict+top90-score+all` | 61.1% (55/90) | 90 | 35.6% | 36.7% | 53.3% | +1.1 |
| `massage/orig+strict+top90-score+no-filter-miss` | 59.7% (46/77) | 77 | 28.6% | 44.2% | 42.9% | -0.3 |
| `massage/orig+strict+top90-score+explicit` | 50.0% (11/22) | 22 | 18.2% | 45.5% | 0.0% | -10.0 |
| `massage/orig+strict+top90-score+explicit+inferred` | 51.1% (23/45) | 45 | 8.9% | 46.7% | 0.0% | -8.9 |
| `massage/orig+strict+top80-score+all` | 61.3% (49/80) | 80 | 37.5% | 37.5% | 57.5% | +1.3 |
| `massage/orig+strict+top80-score+no-filter-miss` | 58.8% (40/68) | 68 | 29.4% | 44.1% | 47.1% | -1.2 |
| `massage/orig+strict+top80-score+explicit` | 50.0% (10/20) | 20 | 20.0% | 50.0% | 0.0% | -10.0 |
| `massage/orig+strict+top80-score+explicit+inferred` | 50.0% (20/40) | 40 | 7.5% | 45.0% | 0.0% | -10.0 |
| `massage/orig+strict+top70-score+all` | 65.7% (46/70) | 70 | 41.4% | 41.4% | 60.0% | +5.7 |
| `massage/orig+strict+top70-score+no-filter-miss` | 60.0% (36/60) | 60 | 31.7% | 48.3% | 50.0% | +0.0 |
| `massage/orig+strict+top70-score+explicit` | 47.1% (8/17) | 17 | 17.6% | 47.1% | 0.0% | -12.9 |
| `massage/orig+strict+top70-score+explicit+inferred` | 45.7% (16/35) | 35 | 5.7% | 48.6% | 0.0% | -14.3 |
| `massage/orig+strict+top50-score+all` | 80.0% (40/50) | 50 | 46.0% | 40.0% | 66.0% | +20.0 |
| `massage/orig+strict+top50-score+no-filter-miss` | 76.7% (33/43) | 43 | 37.2% | 53.5% | 55.8% | +16.7 |
| `massage/orig+strict+top50-score+explicit` | 25.0% (3/12) | 12 | 16.7% | 58.3% | 0.0% | -35.0 |
| `massage/orig+strict+top50-score+explicit+inferred` | 56.0% (14/25) | 25 | 8.0% | 52.0% | 0.0% | -4.0 |
| `massage/orig+lenient+top100+all` | 69.0% (69/100) | 100 | 45.0% | 42.0% | 67.0% | +9.0 |
| `massage/orig+lenient+top100+no-filter-miss` | 68.2% (58/85) | 85 | 41.2% | 48.2% | 61.2% | +8.2 |
| `massage/orig+lenient+top100+explicit` | 66.7% (16/24) | 24 | 37.5% | 50.0% | 41.7% | +6.7 |
| `massage/orig+lenient+top100+explicit+inferred` | 68.0% (34/50) | 50 | 32.0% | 54.0% | 34.0% | +8.0 |
| `massage/orig+lenient+top90-conf+all` | 66.7% (60/90) | 90 | 42.2% | 42.2% | 64.4% | +6.7 |
| `massage/orig+lenient+top90-conf+no-filter-miss` | 66.2% (51/77) | 77 | 37.7% | 49.4% | 58.4% | +6.2 |
| `massage/orig+lenient+top90-conf+explicit` | 63.6% (14/22) | 22 | 31.8% | 45.5% | 36.4% | +3.6 |
| `massage/orig+lenient+top90-conf+explicit+inferred` | 66.7% (30/45) | 45 | 26.7% | 53.3% | 28.9% | +6.7 |
| `massage/orig+lenient+top80-conf+all` | 63.7% (51/80) | 80 | 40.0% | 41.2% | 61.3% | +3.7 |
| `massage/orig+lenient+top80-conf+no-filter-miss` | 63.2% (43/68) | 68 | 35.3% | 47.1% | 54.4% | +3.2 |
| `massage/orig+lenient+top80-conf+explicit` | 60.0% (12/20) | 20 | 25.0% | 40.0% | 30.0% | +0.0 |
| `massage/orig+lenient+top80-conf+explicit+inferred` | 67.5% (27/40) | 40 | 22.5% | 52.5% | 25.0% | +7.5 |
| `massage/orig+lenient+top70-conf+all` | 60.0% (42/70) | 70 | 38.6% | 40.0% | 57.1% | +0.0 |
| `massage/orig+lenient+top70-conf+no-filter-miss` | 60.0% (36/60) | 60 | 31.7% | 48.3% | 50.0% | +0.0 |
| `massage/orig+lenient+top70-conf+explicit` | 58.8% (10/17) | 17 | 29.4% | 47.1% | 29.4% | -1.2 |
| `massage/orig+lenient+top70-conf+explicit+inferred` | 65.7% (23/35) | 35 | 22.9% | 54.3% | 22.9% | +5.7 |
| `massage/orig+lenient+top50-conf+all` | 46.0% (23/50) | 50 | 30.0% | 42.0% | 46.0% | -14.0 |
| `massage/orig+lenient+top50-conf+no-filter-miss` | 48.8% (21/43) | 43 | 25.6% | 39.5% | 41.9% | -11.2 |
| `massage/orig+lenient+top50-conf+explicit` | 41.7% (5/12) | 12 | 33.3% | 58.3% | 33.3% | -18.3 |
| `massage/orig+lenient+top50-conf+explicit+inferred` | 68.0% (17/25) | 25 | 28.0% | 60.0% | 28.0% | +8.0 |
| `massage/orig+lenient+top90-score+all` | 70.0% (63/90) | 90 | 48.9% | 41.1% | 71.1% | +10.0 |
| `massage/orig+lenient+top90-score+no-filter-miss` | 70.1% (54/77) | 77 | 44.2% | 49.4% | 63.6% | +10.1 |
| `massage/orig+lenient+top90-score+explicit` | 72.7% (16/22) | 22 | 40.9% | 50.0% | 45.5% | +12.7 |
| `massage/orig+lenient+top90-score+explicit+inferred` | 68.9% (31/45) | 45 | 35.6% | 55.6% | 35.6% | +8.9 |
| `massage/orig+lenient+top80-score+all` | 71.2% (57/80) | 80 | 51.2% | 42.5% | 76.2% | +11.3 |
| `massage/orig+lenient+top80-score+no-filter-miss` | 70.6% (48/68) | 68 | 47.1% | 50.0% | 70.6% | +10.6 |
| `massage/orig+lenient+top80-score+explicit` | 70.0% (14/20) | 20 | 45.0% | 55.0% | 45.0% | +10.0 |
| `massage/orig+lenient+top80-score+explicit+inferred` | 70.0% (28/40) | 40 | 37.5% | 55.0% | 40.0% | +10.0 |
| `massage/orig+lenient+top70-score+all` | 74.3% (52/70) | 70 | 51.4% | 45.7% | 77.1% | +14.3 |
| `massage/orig+lenient+top70-score+no-filter-miss` | 71.7% (43/60) | 60 | 45.0% | 53.3% | 71.7% | +11.7 |
| `massage/orig+lenient+top70-score+explicit` | 70.6% (12/17) | 17 | 47.1% | 52.9% | 52.9% | +10.6 |
| `massage/orig+lenient+top70-score+explicit+inferred` | 68.6% (24/35) | 35 | 40.0% | 60.0% | 45.7% | +8.6 |
| `massage/orig+lenient+top50-score+all` | 84.0% (42/50) | 50 | 50.0% | 42.0% | 76.0% | +24.0 |
| `massage/orig+lenient+top50-score+no-filter-miss` | 86.0% (37/43) | 43 | 44.2% | 58.1% | 72.1% | +26.0 |
| `massage/orig+lenient+top50-score+explicit` | 58.3% (7/12) | 12 | 50.0% | 66.7% | 66.7% | -1.7 |
| `massage/orig+lenient+top50-score+explicit+inferred` | 72.0% (18/25) | 25 | 28.0% | 60.0% | 40.0% | +12.0 |
| `massage/G4+strict+top100+all` | 62.0% (62/100) | 100 | 33.0% | 41.0% | 50.0% | +2.0 |
| `massage/G4+strict+top100+no-filter-miss` | 60.0% (51/85) | 85 | 27.1% | 47.1% | 41.2% | +0.0 |
| `massage/G4+strict+top100+explicit` | 54.2% (13/24) | 24 | 16.7% | 54.2% | 0.0% | -5.8 |
| `massage/G4+strict+top100+explicit+inferred` | 54.0% (27/50) | 50 | 8.0% | 52.0% | 0.0% | -6.0 |
| `massage/G4+strict+top90-conf+all` | 63.3% (57/90) | 90 | 33.3% | 42.2% | 50.0% | +3.3 |
| `massage/G4+strict+top90-conf+no-filter-miss` | 62.3% (48/77) | 77 | 27.3% | 49.4% | 41.6% | +2.3 |
| `massage/G4+strict+top90-conf+explicit` | 59.1% (13/22) | 22 | 13.6% | 50.0% | 0.0% | -0.9 |
| `massage/G4+strict+top90-conf+explicit+inferred` | 60.0% (27/45) | 45 | 8.9% | 53.3% | 0.0% | +0.0 |
| `massage/G4+strict+top80-conf+all` | 63.7% (51/80) | 80 | 31.2% | 41.2% | 48.8% | +3.7 |
| `massage/G4+strict+top80-conf+no-filter-miss` | 63.2% (43/68) | 68 | 25.0% | 47.1% | 39.7% | +3.2 |
| `massage/G4+strict+top80-conf+explicit` | 65.0% (13/20) | 20 | 10.0% | 50.0% | 0.0% | +5.0 |
| `massage/G4+strict+top80-conf+explicit+inferred` | 67.5% (27/40) | 40 | 5.0% | 52.5% | 0.0% | +7.5 |
| `massage/G4+strict+top70-conf+all` | 61.4% (43/70) | 70 | 30.0% | 41.4% | 44.3% | +1.4 |
| `massage/G4+strict+top70-conf+no-filter-miss` | 61.7% (37/60) | 60 | 21.7% | 50.0% | 35.0% | +1.7 |
| `massage/G4+strict+top70-conf+explicit` | 64.7% (11/17) | 17 | 11.8% | 58.8% | 0.0% | +4.7 |
| `massage/G4+strict+top70-conf+explicit+inferred` | 68.6% (24/35) | 35 | 5.7% | 57.1% | 0.0% | +8.6 |
| `massage/G4+strict+top50-conf+all` | 48.0% (24/50) | 50 | 18.0% | 44.0% | 30.0% | -12.0 |
| `massage/G4+strict+top50-conf+no-filter-miss` | 51.2% (22/43) | 43 | 11.6% | 41.9% | 23.3% | -8.8 |
| `massage/G4+strict+top50-conf+explicit` | 50.0% (6/12) | 12 | 16.7% | 66.7% | 0.0% | -10.0 |
| `massage/G4+strict+top50-conf+explicit+inferred` | 68.0% (17/25) | 25 | 8.0% | 60.0% | 0.0% | +8.0 |
| `massage/G4+strict+top90-score+all` | 62.2% (56/90) | 90 | 35.6% | 40.0% | 53.3% | +2.2 |
| `massage/G4+strict+top90-score+no-filter-miss` | 61.0% (47/77) | 77 | 28.6% | 48.1% | 42.9% | +1.0 |
| `massage/G4+strict+top90-score+explicit` | 54.5% (12/22) | 22 | 18.2% | 54.5% | 0.0% | -5.5 |
| `massage/G4+strict+top90-score+explicit+inferred` | 53.3% (24/45) | 45 | 8.9% | 53.3% | 0.0% | -6.7 |
| `massage/G4+strict+top80-score+all` | 62.5% (50/80) | 80 | 37.5% | 40.0% | 57.5% | +2.5 |
| `massage/G4+strict+top80-score+no-filter-miss` | 60.3% (41/68) | 68 | 29.4% | 48.5% | 47.1% | +0.3 |
| `massage/G4+strict+top80-score+explicit` | 55.0% (11/20) | 20 | 20.0% | 60.0% | 0.0% | -5.0 |
| `massage/G4+strict+top80-score+explicit+inferred` | 52.5% (21/40) | 40 | 7.5% | 52.5% | 0.0% | -7.5 |
| `massage/G4+strict+top70-score+all` | 65.7% (46/70) | 70 | 41.4% | 44.3% | 60.0% | +5.7 |
| `massage/G4+strict+top70-score+no-filter-miss` | 61.7% (37/60) | 60 | 31.7% | 51.7% | 50.0% | +1.7 |
| `massage/G4+strict+top70-score+explicit` | 52.9% (9/17) | 17 | 17.6% | 58.8% | 0.0% | -7.1 |
| `massage/G4+strict+top70-score+explicit+inferred` | 48.6% (17/35) | 35 | 5.7% | 54.3% | 0.0% | -11.4 |
| `massage/G4+strict+top50-score+all` | 80.0% (40/50) | 50 | 46.0% | 42.0% | 66.0% | +20.0 |
| `massage/G4+strict+top50-score+no-filter-miss` | 76.7% (33/43) | 43 | 37.2% | 55.8% | 55.8% | +16.7 |
| `massage/G4+strict+top50-score+explicit` | 33.3% (4/12) | 12 | 16.7% | 66.7% | 0.0% | -26.7 |
| `massage/G4+strict+top50-score+explicit+inferred` | 56.0% (14/25) | 25 | 8.0% | 60.0% | 0.0% | -4.0 |
| `massage/G4+lenient+top100+all` | 72.0% (72/100) | 100 | 46.0% | 46.0% | 67.0% | +12.0 |
| `massage/G4+lenient+top100+no-filter-miss` | 71.8% (61/85) | 85 | 42.4% | 52.9% | 61.2% | +11.8 |
| `massage/G4+lenient+top100+explicit` | 75.0% (18/24) | 24 | 41.7% | 62.5% | 41.7% | +15.0 |
| `massage/G4+lenient+top100+explicit+inferred` | 74.0% (37/50) | 50 | 34.0% | 62.0% | 34.0% | +14.0 |
| `massage/G4+lenient+top90-conf+all` | 70.0% (63/90) | 90 | 43.3% | 46.7% | 64.4% | +10.0 |
| ★ `massage/G4+lenient+top90-conf+no-filter-miss` | 70.1% (54/77) | 77 | 39.0% | 54.5% | 58.4% | +10.1 |
| ★ `massage/G4+lenient+top90-conf+explicit` | 72.7% (16/22) | 22 | 36.4% | 59.1% | 36.4% | +12.7 |
| ★ `massage/G4+lenient+top90-conf+explicit+inferred` | 73.3% (33/45) | 45 | 28.9% | 62.2% | 28.9% | +13.3 |
| `massage/G4+lenient+top80-conf+all` | 67.5% (54/80) | 80 | 41.2% | 45.0% | 61.3% | +7.5 |
| ★ `massage/G4+lenient+top80-conf+no-filter-miss` | 67.6% (46/68) | 68 | 36.8% | 51.5% | 54.4% | +7.6 |
| ★ `massage/G4+lenient+top80-conf+explicit` | 70.0% (14/20) | 20 | 30.0% | 55.0% | 30.0% | +10.0 |
| ★ `massage/G4+lenient+top80-conf+explicit+inferred` | 75.0% (30/40) | 40 | 25.0% | 60.0% | 25.0% | +15.0 |
| `massage/G4+lenient+top70-conf+all` | 64.3% (45/70) | 70 | 40.0% | 44.3% | 57.1% | +4.3 |
| ★ `massage/G4+lenient+top70-conf+no-filter-miss` | 65.0% (39/60) | 60 | 33.3% | 53.3% | 50.0% | +5.0 |
| ★ `massage/G4+lenient+top70-conf+explicit` | 64.7% (11/17) | 17 | 29.4% | 58.8% | 29.4% | +4.7 |
| ★ `massage/G4+lenient+top70-conf+explicit+inferred` | 71.4% (25/35) | 35 | 22.9% | 60.0% | 22.9% | +11.4 |
| `massage/G4+lenient+top50-conf+all` | 50.0% (25/50) | 50 | 30.0% | 46.0% | 46.0% | -10.0 |
| ★ `massage/G4+lenient+top50-conf+no-filter-miss` | 53.5% (23/43) | 43 | 25.6% | 44.2% | 41.9% | -6.5 |
| ★ `massage/G4+lenient+top50-conf+explicit` | 50.0% (6/12) | 12 | 33.3% | 66.7% | 33.3% | -10.0 |
| ★ `massage/G4+lenient+top50-conf+explicit+inferred` | 72.0% (18/25) | 25 | 28.0% | 64.0% | 28.0% | +12.0 |
| `massage/G4+lenient+top90-score+all` | 72.2% (65/90) | 90 | 48.9% | 44.4% | 71.1% | +12.2 |
| ★ `massage/G4+lenient+top90-score+no-filter-miss` | 72.7% (56/77) | 77 | 44.2% | 53.2% | 63.6% | +12.7 |
| ★ `massage/G4+lenient+top90-score+explicit` | 77.3% (17/22) | 22 | 45.5% | 63.6% | 45.5% | +17.3 |
| ★ `massage/G4+lenient+top90-score+explicit+inferred` | 73.3% (33/45) | 45 | 35.6% | 62.2% | 35.6% | +13.3 |
| `massage/G4+lenient+top80-score+all` | 73.8% (59/80) | 80 | 51.2% | 45.0% | 76.2% | +13.8 |
| ★ `massage/G4+lenient+top80-score+no-filter-miss` | 73.5% (50/68) | 68 | 47.1% | 54.4% | 70.6% | +13.5 |
| ★ `massage/G4+lenient+top80-score+explicit` | 75.0% (15/20) | 20 | 45.0% | 65.0% | 45.0% | +15.0 |
| ★ `massage/G4+lenient+top80-score+explicit+inferred` | 75.0% (30/40) | 40 | 37.5% | 62.5% | 40.0% | +15.0 |
| `massage/G4+lenient+top70-score+all` | 74.3% (52/70) | 70 | 51.4% | 48.6% | 77.1% | +14.3 |
| ★ `massage/G4+lenient+top70-score+no-filter-miss` | 73.3% (44/60) | 60 | 45.0% | 56.7% | 71.7% | +13.3 |
| ★ `massage/G4+lenient+top70-score+explicit` | 76.5% (13/17) | 17 | 47.1% | 64.7% | 52.9% | +16.5 |
| ★ `massage/G4+lenient+top70-score+explicit+inferred` | 74.3% (26/35) | 35 | 40.0% | 65.7% | 45.7% | +14.3 |
| `massage/G4+lenient+top50-score+all` | 84.0% (42/50) | 50 | 50.0% | 44.0% | 76.0% | +24.0 |
| ★ `massage/G4+lenient+top50-score+no-filter-miss` | 86.0% (37/43) | 43 | 44.2% | 60.5% | 72.1% | +26.0 |
| ★ `massage/G4+lenient+top50-score+explicit` | 66.7% (8/12) | 12 | 50.0% | 75.0% | 66.7% | +6.7 |
| ★ `massage/G4+lenient+top50-score+explicit+inferred` | 72.0% (18/25) | 25 | 28.0% | 68.0% | 40.0% | +12.0 |
| `massage/G3+strict+top100+all` | 62.0% (62/100) | 100 | 34.0% | 41.0% | 50.0% | +2.0 |
| `massage/G3+strict+top100+no-filter-miss` | 60.0% (51/85) | 85 | 28.2% | 47.1% | 41.2% | +0.0 |
| `massage/G3+strict+top100+explicit` | 54.2% (13/24) | 24 | 16.7% | 54.2% | 0.0% | -5.8 |
| `massage/G3+strict+top100+explicit+inferred` | 54.0% (27/50) | 50 | 10.0% | 52.0% | 0.0% | -6.0 |
| `massage/G3+strict+top90-conf+all` | 63.3% (57/90) | 90 | 34.4% | 42.2% | 50.0% | +3.3 |
| `massage/G3+strict+top90-conf+no-filter-miss` | 62.3% (48/77) | 77 | 28.6% | 49.4% | 41.6% | +2.3 |
| `massage/G3+strict+top90-conf+explicit` | 59.1% (13/22) | 22 | 13.6% | 50.0% | 0.0% | -0.9 |
| `massage/G3+strict+top90-conf+explicit+inferred` | 60.0% (27/45) | 45 | 11.1% | 53.3% | 0.0% | +0.0 |
| `massage/G3+strict+top80-conf+all` | 63.7% (51/80) | 80 | 32.5% | 41.2% | 48.8% | +3.7 |
| `massage/G3+strict+top80-conf+no-filter-miss` | 63.2% (43/68) | 68 | 26.5% | 47.1% | 39.7% | +3.2 |
| `massage/G3+strict+top80-conf+explicit` | 65.0% (13/20) | 20 | 10.0% | 50.0% | 0.0% | +5.0 |
| `massage/G3+strict+top80-conf+explicit+inferred` | 67.5% (27/40) | 40 | 7.5% | 52.5% | 0.0% | +7.5 |
| `massage/G3+strict+top70-conf+all` | 61.4% (43/70) | 70 | 31.4% | 41.4% | 44.3% | +1.4 |
| `massage/G3+strict+top70-conf+no-filter-miss` | 61.7% (37/60) | 60 | 23.3% | 50.0% | 35.0% | +1.7 |
| `massage/G3+strict+top70-conf+explicit` | 64.7% (11/17) | 17 | 11.8% | 58.8% | 0.0% | +4.7 |
| `massage/G3+strict+top70-conf+explicit+inferred` | 68.6% (24/35) | 35 | 8.6% | 57.1% | 0.0% | +8.6 |
| `massage/G3+strict+top50-conf+all` | 48.0% (24/50) | 50 | 20.0% | 44.0% | 30.0% | -12.0 |
| `massage/G3+strict+top50-conf+no-filter-miss` | 51.2% (22/43) | 43 | 14.0% | 41.9% | 23.3% | -8.8 |
| `massage/G3+strict+top50-conf+explicit` | 50.0% (6/12) | 12 | 16.7% | 66.7% | 0.0% | -10.0 |
| `massage/G3+strict+top50-conf+explicit+inferred` | 68.0% (17/25) | 25 | 12.0% | 60.0% | 0.0% | +8.0 |
| `massage/G3+strict+top90-score+all` | 62.2% (56/90) | 90 | 36.7% | 40.0% | 53.3% | +2.2 |
| `massage/G3+strict+top90-score+no-filter-miss` | 61.0% (47/77) | 77 | 29.9% | 48.1% | 42.9% | +1.0 |
| `massage/G3+strict+top90-score+explicit` | 54.5% (12/22) | 22 | 18.2% | 54.5% | 0.0% | -5.5 |
| `massage/G3+strict+top90-score+explicit+inferred` | 53.3% (24/45) | 45 | 11.1% | 53.3% | 0.0% | -6.7 |
| `massage/G3+strict+top80-score+all` | 62.5% (50/80) | 80 | 38.8% | 40.0% | 57.5% | +2.5 |
| `massage/G3+strict+top80-score+no-filter-miss` | 60.3% (41/68) | 68 | 30.9% | 48.5% | 47.1% | +0.3 |
| `massage/G3+strict+top80-score+explicit` | 55.0% (11/20) | 20 | 20.0% | 60.0% | 0.0% | -5.0 |
| `massage/G3+strict+top80-score+explicit+inferred` | 52.5% (21/40) | 40 | 10.0% | 52.5% | 0.0% | -7.5 |
| `massage/G3+strict+top70-score+all` | 65.7% (46/70) | 70 | 42.9% | 44.3% | 60.0% | +5.7 |
| `massage/G3+strict+top70-score+no-filter-miss` | 61.7% (37/60) | 60 | 33.3% | 51.7% | 50.0% | +1.7 |
| `massage/G3+strict+top70-score+explicit` | 52.9% (9/17) | 17 | 17.6% | 58.8% | 0.0% | -7.1 |
| `massage/G3+strict+top70-score+explicit+inferred` | 48.6% (17/35) | 35 | 8.6% | 54.3% | 0.0% | -11.4 |
| `massage/G3+strict+top50-score+all` | 80.0% (40/50) | 50 | 48.0% | 42.0% | 66.0% | +20.0 |
| `massage/G3+strict+top50-score+no-filter-miss` | 76.7% (33/43) | 43 | 39.5% | 55.8% | 55.8% | +16.7 |
| `massage/G3+strict+top50-score+explicit` | 33.3% (4/12) | 12 | 16.7% | 66.7% | 0.0% | -26.7 |
| `massage/G3+strict+top50-score+explicit+inferred` | 56.0% (14/25) | 25 | 12.0% | 60.0% | 0.0% | -4.0 |
| `massage/G3+lenient+top100+all` | 72.0% (72/100) | 100 | 47.0% | 46.0% | 67.0% | +12.0 |
| `massage/G3+lenient+top100+no-filter-miss` | 71.8% (61/85) | 85 | 43.5% | 52.9% | 61.2% | +11.8 |
| `massage/G3+lenient+top100+explicit` | 75.0% (18/24) | 24 | 41.7% | 62.5% | 41.7% | +15.0 |
| `massage/G3+lenient+top100+explicit+inferred` | 74.0% (37/50) | 50 | 36.0% | 62.0% | 34.0% | +14.0 |
| `massage/G3+lenient+top90-conf+all` | 70.0% (63/90) | 90 | 44.4% | 46.7% | 64.4% | +10.0 |
| ★ `massage/G3+lenient+top90-conf+no-filter-miss` | 70.1% (54/77) | 77 | 40.3% | 54.5% | 58.4% | +10.1 |
| ★ `massage/G3+lenient+top90-conf+explicit` | 72.7% (16/22) | 22 | 36.4% | 59.1% | 36.4% | +12.7 |
| ★ `massage/G3+lenient+top90-conf+explicit+inferred` | 73.3% (33/45) | 45 | 31.1% | 62.2% | 28.9% | +13.3 |
| `massage/G3+lenient+top80-conf+all` | 67.5% (54/80) | 80 | 42.5% | 45.0% | 61.3% | +7.5 |
| ★ `massage/G3+lenient+top80-conf+no-filter-miss` | 67.6% (46/68) | 68 | 38.2% | 51.5% | 54.4% | +7.6 |
| ★ `massage/G3+lenient+top80-conf+explicit` | 70.0% (14/20) | 20 | 30.0% | 55.0% | 30.0% | +10.0 |
| ★ `massage/G3+lenient+top80-conf+explicit+inferred` | 75.0% (30/40) | 40 | 27.5% | 60.0% | 25.0% | +15.0 |
| `massage/G3+lenient+top70-conf+all` | 64.3% (45/70) | 70 | 41.4% | 44.3% | 57.1% | +4.3 |
| ★ `massage/G3+lenient+top70-conf+no-filter-miss` | 65.0% (39/60) | 60 | 35.0% | 53.3% | 50.0% | +5.0 |
| ★ `massage/G3+lenient+top70-conf+explicit` | 64.7% (11/17) | 17 | 29.4% | 58.8% | 29.4% | +4.7 |
| ★ `massage/G3+lenient+top70-conf+explicit+inferred` | 71.4% (25/35) | 35 | 25.7% | 60.0% | 22.9% | +11.4 |
| `massage/G3+lenient+top50-conf+all` | 50.0% (25/50) | 50 | 32.0% | 46.0% | 46.0% | -10.0 |
| ★ `massage/G3+lenient+top50-conf+no-filter-miss` | 53.5% (23/43) | 43 | 27.9% | 44.2% | 41.9% | -6.5 |
| ★ `massage/G3+lenient+top50-conf+explicit` | 50.0% (6/12) | 12 | 33.3% | 66.7% | 33.3% | -10.0 |
| ★ `massage/G3+lenient+top50-conf+explicit+inferred` | 72.0% (18/25) | 25 | 32.0% | 64.0% | 28.0% | +12.0 |
| `massage/G3+lenient+top90-score+all` | 72.2% (65/90) | 90 | 50.0% | 44.4% | 71.1% | +12.2 |
| ★ `massage/G3+lenient+top90-score+no-filter-miss` | 72.7% (56/77) | 77 | 45.5% | 53.2% | 63.6% | +12.7 |
| ★ `massage/G3+lenient+top90-score+explicit` | 77.3% (17/22) | 22 | 45.5% | 63.6% | 45.5% | +17.3 |
| ★ `massage/G3+lenient+top90-score+explicit+inferred` | 73.3% (33/45) | 45 | 37.8% | 62.2% | 35.6% | +13.3 |
| `massage/G3+lenient+top80-score+all` | 73.8% (59/80) | 80 | 52.5% | 45.0% | 76.2% | +13.8 |
| ★ `massage/G3+lenient+top80-score+no-filter-miss` | 73.5% (50/68) | 68 | 48.5% | 54.4% | 70.6% | +13.5 |
| ★ `massage/G3+lenient+top80-score+explicit` | 75.0% (15/20) | 20 | 45.0% | 65.0% | 45.0% | +15.0 |
| ★ `massage/G3+lenient+top80-score+explicit+inferred` | 75.0% (30/40) | 40 | 40.0% | 62.5% | 40.0% | +15.0 |
| `massage/G3+lenient+top70-score+all` | 74.3% (52/70) | 70 | 52.9% | 48.6% | 77.1% | +14.3 |
| ★ `massage/G3+lenient+top70-score+no-filter-miss` | 73.3% (44/60) | 60 | 46.7% | 56.7% | 71.7% | +13.3 |
| ★ `massage/G3+lenient+top70-score+explicit` | 76.5% (13/17) | 17 | 47.1% | 64.7% | 52.9% | +16.5 |
| ★ `massage/G3+lenient+top70-score+explicit+inferred` | 74.3% (26/35) | 35 | 42.9% | 65.7% | 45.7% | +14.3 |
| `massage/G3+lenient+top50-score+all` | 84.0% (42/50) | 50 | 52.0% | 44.0% | 76.0% | +24.0 |
| ★ `massage/G3+lenient+top50-score+no-filter-miss` | 86.0% (37/43) | 43 | 46.5% | 60.5% | 72.1% | +26.0 |
| ★ `massage/G3+lenient+top50-score+explicit` | 66.7% (8/12) | 12 | 50.0% | 75.0% | 66.7% | +6.7 |
| ★ `massage/G3+lenient+top50-score+explicit+inferred` | 72.0% (18/25) | 25 | 32.0% | 68.0% | 40.0% | +12.0 |
| `massage/G2+strict+top100+all` | 74.0% (74/100) | 100 | 51.0% | 59.0% | 50.0% | +14.0 |
| `massage/G2+strict+top100+no-filter-miss` | 74.1% (63/85) | 85 | 48.2% | 68.2% | 41.2% | +14.1 |
| `massage/G2+strict+top100+explicit` | 83.3% (20/24) | 24 | 50.0% | 91.7% | 0.0% | +23.3 |
| `massage/G2+strict+top100+explicit+inferred` | 78.0% (39/50) | 50 | 44.0% | 88.0% | 0.0% | +18.0 |
| `massage/G2+strict+top90-conf+all` | 76.7% (69/90) | 90 | 53.3% | 60.0% | 50.0% | +16.7 |
| `massage/G2+strict+top90-conf+no-filter-miss` | 77.9% (60/77) | 77 | 50.6% | 70.1% | 41.6% | +17.9 |
| `massage/G2+strict+top90-conf+explicit` | 90.9% (20/22) | 22 | 50.0% | 90.9% | 0.0% | +30.9 |
| `massage/G2+strict+top90-conf+explicit+inferred` | 86.7% (39/45) | 45 | 48.9% | 88.9% | 0.0% | +26.7 |
| `massage/G2+strict+top80-conf+all` | 78.8% (63/80) | 80 | 53.8% | 61.3% | 48.8% | +18.8 |
| `massage/G2+strict+top80-conf+no-filter-miss` | 80.9% (55/68) | 68 | 51.5% | 70.6% | 39.7% | +20.9 |
| `massage/G2+strict+top80-conf+explicit` | 100.0% (20/20) | 20 | 50.0% | 95.0% | 0.0% | +40.0 |
| `massage/G2+strict+top80-conf+explicit+inferred` | 97.5% (39/40) | 40 | 50.0% | 90.0% | 0.0% | +37.5 |
| `massage/G2+strict+top70-conf+all` | 78.6% (55/70) | 70 | 55.7% | 62.9% | 44.3% | +18.6 |
| `massage/G2+strict+top70-conf+no-filter-miss` | 81.7% (49/60) | 60 | 51.7% | 75.0% | 35.0% | +21.7 |
| `massage/G2+strict+top70-conf+explicit` | 100.0% (17/17) | 17 | 47.1% | 94.1% | 0.0% | +40.0 |
| `massage/G2+strict+top70-conf+explicit+inferred` | 100.0% (35/35) | 35 | 51.4% | 91.4% | 0.0% | +40.0 |
| `massage/G2+strict+top50-conf+all` | 70.0% (35/50) | 50 | 50.0% | 68.0% | 30.0% | +10.0 |
| `massage/G2+strict+top50-conf+no-filter-miss` | 76.7% (33/43) | 43 | 48.8% | 69.8% | 23.3% | +16.7 |
| `massage/G2+strict+top50-conf+explicit` | 100.0% (12/12) | 12 | 58.3% | 100.0% | 0.0% | +40.0 |
| `massage/G2+strict+top50-conf+explicit+inferred` | 100.0% (25/25) | 25 | 60.0% | 92.0% | 0.0% | +40.0 |
| `massage/G2+strict+top90-score+all` | 72.2% (65/90) | 90 | 52.2% | 55.6% | 53.3% | +12.2 |
| `massage/G2+strict+top90-score+no-filter-miss` | 72.7% (56/77) | 77 | 48.1% | 66.2% | 42.9% | +12.7 |
| `massage/G2+strict+top90-score+explicit` | 81.8% (18/22) | 22 | 54.5% | 90.9% | 0.0% | +21.8 |
| `massage/G2+strict+top90-score+explicit+inferred` | 75.6% (34/45) | 45 | 44.4% | 86.7% | 0.0% | +15.6 |
| `massage/G2+strict+top80-score+all` | 71.2% (57/80) | 80 | 53.8% | 53.8% | 57.5% | +11.3 |
| `massage/G2+strict+top80-score+no-filter-miss` | 70.6% (48/68) | 68 | 48.5% | 64.7% | 47.1% | +10.6 |
| `massage/G2+strict+top80-score+explicit` | 80.0% (16/20) | 20 | 50.0% | 90.0% | 0.0% | +20.0 |
| `massage/G2+strict+top80-score+explicit+inferred` | 72.5% (29/40) | 40 | 42.5% | 85.0% | 0.0% | +12.5 |
| `massage/G2+strict+top70-score+all` | 72.9% (51/70) | 70 | 55.7% | 52.9% | 60.0% | +12.9 |
| `massage/G2+strict+top70-score+no-filter-miss` | 70.0% (42/60) | 60 | 50.0% | 63.3% | 50.0% | +10.0 |
| `massage/G2+strict+top70-score+explicit` | 76.5% (13/17) | 17 | 47.1% | 88.2% | 0.0% | +16.5 |
| `massage/G2+strict+top70-score+explicit+inferred` | 68.6% (24/35) | 35 | 42.9% | 85.7% | 0.0% | +8.6 |
| `massage/G2+strict+top50-score+all` | 82.0% (41/50) | 50 | 62.0% | 48.0% | 66.0% | +22.0 |
| `massage/G2+strict+top50-score+no-filter-miss` | 79.1% (34/43) | 43 | 55.8% | 62.8% | 55.8% | +19.1 |
| `massage/G2+strict+top50-score+explicit` | 66.7% (8/12) | 12 | 50.0% | 91.7% | 0.0% | +6.7 |
| `massage/G2+strict+top50-score+explicit+inferred` | 76.0% (19/25) | 25 | 48.0% | 84.0% | 0.0% | +16.0 |
| `massage/G2+lenient+top100+all` | 82.0% (82/100) | 100 | 62.0% | 62.0% | 67.0% | +22.0 |
| `massage/G2+lenient+top100+no-filter-miss` | 83.5% (71/85) | 85 | 61.2% | 71.8% | 61.2% | +23.5 |
| `massage/G2+lenient+top100+explicit` | 100.0% (24/24) | 24 | 70.8% | 95.8% | 41.7% | +40.0 |
| `massage/G2+lenient+top100+explicit+inferred` | 94.0% (47/50) | 50 | 66.0% | 94.0% | 34.0% | +34.0 |
| `massage/G2+lenient+top90-conf+all` | 81.1% (73/90) | 90 | 61.1% | 62.2% | 64.4% | +21.1 |
| ★ `massage/G2+lenient+top90-conf+no-filter-miss` | 83.1% (64/77) | 77 | 59.7% | 72.7% | 58.4% | +23.1 |
| ★ `massage/G2+lenient+top90-conf+explicit` | 100.0% (22/22) | 22 | 68.2% | 95.5% | 36.4% | +40.0 |
| ★ `massage/G2+lenient+top90-conf+explicit+inferred` | 95.6% (43/45) | 45 | 64.4% | 93.3% | 28.9% | +35.6 |
| `massage/G2+lenient+top80-conf+all` | 80.0% (64/80) | 80 | 61.3% | 62.5% | 61.3% | +20.0 |
| ★ `massage/G2+lenient+top80-conf+no-filter-miss` | 82.4% (56/68) | 68 | 60.3% | 72.1% | 54.4% | +22.4 |
| ★ `massage/G2+lenient+top80-conf+explicit` | 100.0% (20/20) | 20 | 65.0% | 95.0% | 30.0% | +40.0 |
| ★ `massage/G2+lenient+top80-conf+explicit+inferred` | 100.0% (40/40) | 40 | 65.0% | 92.5% | 25.0% | +40.0 |
| `massage/G2+lenient+top70-conf+all` | 78.6% (55/70) | 70 | 62.9% | 62.9% | 57.1% | +18.6 |
| ★ `massage/G2+lenient+top70-conf+no-filter-miss` | 81.7% (49/60) | 60 | 60.0% | 75.0% | 50.0% | +21.7 |
| ★ `massage/G2+lenient+top70-conf+explicit` | 100.0% (17/17) | 17 | 64.7% | 94.1% | 29.4% | +40.0 |
| ★ `massage/G2+lenient+top70-conf+explicit+inferred` | 100.0% (35/35) | 35 | 65.7% | 91.4% | 22.9% | +40.0 |
| `massage/G2+lenient+top50-conf+all` | 70.0% (35/50) | 50 | 60.0% | 68.0% | 46.0% | +10.0 |
| ★ `massage/G2+lenient+top50-conf+no-filter-miss` | 76.7% (33/43) | 43 | 60.5% | 69.8% | 41.9% | +16.7 |
| ★ `massage/G2+lenient+top50-conf+explicit` | 100.0% (12/12) | 12 | 75.0% | 100.0% | 33.3% | +40.0 |
| ★ `massage/G2+lenient+top50-conf+explicit+inferred` | 100.0% (25/25) | 25 | 76.0% | 92.0% | 28.0% | +40.0 |
| `massage/G2+lenient+top90-score+all` | 81.1% (73/90) | 90 | 64.4% | 58.9% | 71.1% | +21.1 |
| ★ `massage/G2+lenient+top90-score+no-filter-miss` | 83.1% (64/77) | 77 | 62.3% | 70.1% | 63.6% | +23.1 |
| ★ `massage/G2+lenient+top90-score+explicit` | 100.0% (22/22) | 22 | 77.3% | 95.5% | 45.5% | +40.0 |
| ★ `massage/G2+lenient+top90-score+explicit+inferred` | 93.3% (42/45) | 45 | 68.9% | 93.3% | 35.6% | +33.3 |
| `massage/G2+lenient+top80-score+all` | 81.2% (65/80) | 80 | 66.2% | 57.5% | 76.2% | +21.3 |
| ★ `massage/G2+lenient+top80-score+no-filter-miss` | 82.4% (56/68) | 68 | 64.7% | 69.1% | 70.6% | +22.4 |
| ★ `massage/G2+lenient+top80-score+explicit` | 100.0% (20/20) | 20 | 75.0% | 95.0% | 45.0% | +40.0 |
| ★ `massage/G2+lenient+top80-score+explicit+inferred` | 92.5% (37/40) | 40 | 70.0% | 92.5% | 40.0% | +32.5 |
| `massage/G2+lenient+top70-score+all` | 81.4% (57/70) | 70 | 65.7% | 57.1% | 77.1% | +21.4 |
| ★ `massage/G2+lenient+top70-score+no-filter-miss` | 81.7% (49/60) | 60 | 63.3% | 68.3% | 71.7% | +21.7 |
| ★ `massage/G2+lenient+top70-score+explicit` | 100.0% (17/17) | 17 | 76.5% | 94.1% | 52.9% | +40.0 |
| ★ `massage/G2+lenient+top70-score+explicit+inferred` | 91.4% (32/35) | 35 | 74.3% | 94.3% | 45.7% | +31.4 |
| `massage/G2+lenient+top50-score+all` | 86.0% (43/50) | 50 | 66.0% | 50.0% | 76.0% | +26.0 |
| ★ `massage/G2+lenient+top50-score+no-filter-miss` | 88.4% (38/43) | 43 | 62.8% | 67.4% | 72.1% | +28.4 |
| ★ `massage/G2+lenient+top50-score+explicit` | 100.0% (12/12) | 12 | 83.3% | 100.0% | 66.7% | +40.0 |
| ★ `massage/G2+lenient+top50-score+explicit+inferred` | 92.0% (23/25) | 25 | 68.0% | 92.0% | 40.0% | +32.0 |
| `massage/G-best+strict+top100+all` | 74.0% (74/100) | 100 | 50.0% | 59.0% | 50.0% | +14.0 |
| `massage/G-best+strict+top100+no-filter-miss` | 74.1% (63/85) | 85 | 47.1% | 68.2% | 41.2% | +14.1 |
| `massage/G-best+strict+top100+explicit` | 83.3% (20/24) | 24 | 50.0% | 91.7% | 0.0% | +23.3 |
| `massage/G-best+strict+top100+explicit+inferred` | 78.0% (39/50) | 50 | 42.0% | 88.0% | 0.0% | +18.0 |
| `massage/G-best+strict+top90-conf+all` | 76.7% (69/90) | 90 | 52.2% | 60.0% | 50.0% | +16.7 |
| `massage/G-best+strict+top90-conf+no-filter-miss` | 77.9% (60/77) | 77 | 49.4% | 70.1% | 41.6% | +17.9 |
| `massage/G-best+strict+top90-conf+explicit` | 90.9% (20/22) | 22 | 50.0% | 90.9% | 0.0% | +30.9 |
| `massage/G-best+strict+top90-conf+explicit+inferred` | 86.7% (39/45) | 45 | 46.7% | 88.9% | 0.0% | +26.7 |
| `massage/G-best+strict+top80-conf+all` | 78.8% (63/80) | 80 | 52.5% | 61.3% | 48.8% | +18.8 |
| `massage/G-best+strict+top80-conf+no-filter-miss` | 80.9% (55/68) | 68 | 50.0% | 70.6% | 39.7% | +20.9 |
| `massage/G-best+strict+top80-conf+explicit` | 100.0% (20/20) | 20 | 50.0% | 95.0% | 0.0% | +40.0 |
| `massage/G-best+strict+top80-conf+explicit+inferred` | 97.5% (39/40) | 40 | 47.5% | 90.0% | 0.0% | +37.5 |
| `massage/G-best+strict+top70-conf+all` | 78.6% (55/70) | 70 | 54.3% | 62.9% | 44.3% | +18.6 |
| `massage/G-best+strict+top70-conf+no-filter-miss` | 81.7% (49/60) | 60 | 50.0% | 75.0% | 35.0% | +21.7 |
| `massage/G-best+strict+top70-conf+explicit` | 100.0% (17/17) | 17 | 47.1% | 94.1% | 0.0% | +40.0 |
| `massage/G-best+strict+top70-conf+explicit+inferred` | 100.0% (35/35) | 35 | 48.6% | 91.4% | 0.0% | +40.0 |
| `massage/G-best+strict+top50-conf+all` | 70.0% (35/50) | 50 | 48.0% | 68.0% | 30.0% | +10.0 |
| `massage/G-best+strict+top50-conf+no-filter-miss` | 76.7% (33/43) | 43 | 46.5% | 69.8% | 23.3% | +16.7 |
| `massage/G-best+strict+top50-conf+explicit` | 100.0% (12/12) | 12 | 58.3% | 100.0% | 0.0% | +40.0 |
| `massage/G-best+strict+top50-conf+explicit+inferred` | 100.0% (25/25) | 25 | 56.0% | 92.0% | 0.0% | +40.0 |
| `massage/G-best+strict+top90-score+all` | 72.2% (65/90) | 90 | 51.1% | 55.6% | 53.3% | +12.2 |
| `massage/G-best+strict+top90-score+no-filter-miss` | 72.7% (56/77) | 77 | 46.8% | 66.2% | 42.9% | +12.7 |
| `massage/G-best+strict+top90-score+explicit` | 81.8% (18/22) | 22 | 54.5% | 90.9% | 0.0% | +21.8 |
| `massage/G-best+strict+top90-score+explicit+inferred` | 75.6% (34/45) | 45 | 42.2% | 86.7% | 0.0% | +15.6 |
| `massage/G-best+strict+top80-score+all` | 71.2% (57/80) | 80 | 52.5% | 53.8% | 57.5% | +11.3 |
| `massage/G-best+strict+top80-score+no-filter-miss` | 70.6% (48/68) | 68 | 47.1% | 64.7% | 47.1% | +10.6 |
| `massage/G-best+strict+top80-score+explicit` | 80.0% (16/20) | 20 | 50.0% | 90.0% | 0.0% | +20.0 |
| `massage/G-best+strict+top80-score+explicit+inferred` | 72.5% (29/40) | 40 | 40.0% | 85.0% | 0.0% | +12.5 |
| `massage/G-best+strict+top70-score+all` | 72.9% (51/70) | 70 | 54.3% | 52.9% | 60.0% | +12.9 |
| `massage/G-best+strict+top70-score+no-filter-miss` | 70.0% (42/60) | 60 | 48.3% | 63.3% | 50.0% | +10.0 |
| `massage/G-best+strict+top70-score+explicit` | 76.5% (13/17) | 17 | 47.1% | 88.2% | 0.0% | +16.5 |
| `massage/G-best+strict+top70-score+explicit+inferred` | 68.6% (24/35) | 35 | 40.0% | 85.7% | 0.0% | +8.6 |
| `massage/G-best+strict+top50-score+all` | 82.0% (41/50) | 50 | 60.0% | 48.0% | 66.0% | +22.0 |
| `massage/G-best+strict+top50-score+no-filter-miss` | 79.1% (34/43) | 43 | 53.5% | 62.8% | 55.8% | +19.1 |
| `massage/G-best+strict+top50-score+explicit` | 66.7% (8/12) | 12 | 50.0% | 91.7% | 0.0% | +6.7 |
| `massage/G-best+strict+top50-score+explicit+inferred` | 76.0% (19/25) | 25 | 44.0% | 84.0% | 0.0% | +16.0 |
| `massage/G-best+lenient+top100+all` | 82.0% (82/100) | 100 | 61.0% | 62.0% | 67.0% | +22.0 |
| `massage/G-best+lenient+top100+no-filter-miss` | 83.5% (71/85) | 85 | 60.0% | 71.8% | 61.2% | +23.5 |
| `massage/G-best+lenient+top100+explicit` | 100.0% (24/24) | 24 | 70.8% | 95.8% | 41.7% | +40.0 |
| `massage/G-best+lenient+top100+explicit+inferred` | 94.0% (47/50) | 50 | 64.0% | 94.0% | 34.0% | +34.0 |
| `massage/G-best+lenient+top90-conf+all` | 81.1% (73/90) | 90 | 60.0% | 62.2% | 64.4% | +21.1 |
| ★ `massage/G-best+lenient+top90-conf+no-filter-miss` | 83.1% (64/77) | 77 | 58.4% | 72.7% | 58.4% | +23.1 |
| ★ `massage/G-best+lenient+top90-conf+explicit` | 100.0% (22/22) | 22 | 68.2% | 95.5% | 36.4% | +40.0 |
| ★ `massage/G-best+lenient+top90-conf+explicit+inferred` | 95.6% (43/45) | 45 | 62.2% | 93.3% | 28.9% | +35.6 |
| `massage/G-best+lenient+top80-conf+all` | 80.0% (64/80) | 80 | 60.0% | 62.5% | 61.3% | +20.0 |
| ★ `massage/G-best+lenient+top80-conf+no-filter-miss` | 82.4% (56/68) | 68 | 58.8% | 72.1% | 54.4% | +22.4 |
| ★ `massage/G-best+lenient+top80-conf+explicit` | 100.0% (20/20) | 20 | 65.0% | 95.0% | 30.0% | +40.0 |
| ★ `massage/G-best+lenient+top80-conf+explicit+inferred` | 100.0% (40/40) | 40 | 62.5% | 92.5% | 25.0% | +40.0 |
| `massage/G-best+lenient+top70-conf+all` | 78.6% (55/70) | 70 | 61.4% | 62.9% | 57.1% | +18.6 |
| ★ `massage/G-best+lenient+top70-conf+no-filter-miss` | 81.7% (49/60) | 60 | 58.3% | 75.0% | 50.0% | +21.7 |
| ★ `massage/G-best+lenient+top70-conf+explicit` | 100.0% (17/17) | 17 | 64.7% | 94.1% | 29.4% | +40.0 |
| ★ `massage/G-best+lenient+top70-conf+explicit+inferred` | 100.0% (35/35) | 35 | 62.9% | 91.4% | 22.9% | +40.0 |
| `massage/G-best+lenient+top50-conf+all` | 70.0% (35/50) | 50 | 58.0% | 68.0% | 46.0% | +10.0 |
| ★ `massage/G-best+lenient+top50-conf+no-filter-miss` | 76.7% (33/43) | 43 | 58.1% | 69.8% | 41.9% | +16.7 |
| ★ `massage/G-best+lenient+top50-conf+explicit` | 100.0% (12/12) | 12 | 75.0% | 100.0% | 33.3% | +40.0 |
| ★ `massage/G-best+lenient+top50-conf+explicit+inferred` | 100.0% (25/25) | 25 | 72.0% | 92.0% | 28.0% | +40.0 |
| `massage/G-best+lenient+top90-score+all` | 81.1% (73/90) | 90 | 63.3% | 58.9% | 71.1% | +21.1 |
| ★ `massage/G-best+lenient+top90-score+no-filter-miss` | 83.1% (64/77) | 77 | 61.0% | 70.1% | 63.6% | +23.1 |
| ★ `massage/G-best+lenient+top90-score+explicit` | 100.0% (22/22) | 22 | 77.3% | 95.5% | 45.5% | +40.0 |
| ★ `massage/G-best+lenient+top90-score+explicit+inferred` | 93.3% (42/45) | 45 | 66.7% | 93.3% | 35.6% | +33.3 |
| `massage/G-best+lenient+top80-score+all` | 81.2% (65/80) | 80 | 65.0% | 57.5% | 76.2% | +21.3 |
| ★ `massage/G-best+lenient+top80-score+no-filter-miss` | 82.4% (56/68) | 68 | 63.2% | 69.1% | 70.6% | +22.4 |
| ★ `massage/G-best+lenient+top80-score+explicit` | 100.0% (20/20) | 20 | 75.0% | 95.0% | 45.0% | +40.0 |
| ★ `massage/G-best+lenient+top80-score+explicit+inferred` | 92.5% (37/40) | 40 | 67.5% | 92.5% | 40.0% | +32.5 |
| `massage/G-best+lenient+top70-score+all` | 81.4% (57/70) | 70 | 64.3% | 57.1% | 77.1% | +21.4 |
| ★ `massage/G-best+lenient+top70-score+no-filter-miss` | 81.7% (49/60) | 60 | 61.7% | 68.3% | 71.7% | +21.7 |
| ★ `massage/G-best+lenient+top70-score+explicit` | 100.0% (17/17) | 17 | 76.5% | 94.1% | 52.9% | +40.0 |
| ★ `massage/G-best+lenient+top70-score+explicit+inferred` | 91.4% (32/35) | 35 | 71.4% | 94.3% | 45.7% | +31.4 |
| `massage/G-best+lenient+top50-score+all` | 86.0% (43/50) | 50 | 64.0% | 50.0% | 76.0% | +26.0 |
| ★ `massage/G-best+lenient+top50-score+no-filter-miss` | 88.4% (38/43) | 43 | 60.5% | 67.4% | 72.1% | +28.4 |
| ★ `massage/G-best+lenient+top50-score+explicit` | 100.0% (12/12) | 12 | 83.3% | 100.0% | 66.7% | +40.0 |
| ★ `massage/G-best+lenient+top50-score+explicit+inferred` | 92.0% (23/25) | 25 | 64.0% | 92.0% | 40.0% | +32.0 |
| `massage/G-best-nd+strict+top100+all` | 69.0% (69/100) | 100 | 40.0% | 50.0% | 50.0% | +9.0 |
| `massage/G-best-nd+strict+top100+no-filter-miss` | 68.2% (58/85) | 85 | 35.3% | 57.6% | 41.2% | +8.2 |
| `massage/G-best-nd+strict+top100+explicit` | 70.8% (17/24) | 24 | 33.3% | 70.8% | 0.0% | +10.8 |
| `massage/G-best-nd+strict+top100+explicit+inferred` | 68.0% (34/50) | 50 | 22.0% | 70.0% | 0.0% | +8.0 |
| `massage/G-best-nd+strict+top90-conf+all` | 71.1% (64/90) | 90 | 41.1% | 51.1% | 50.0% | +11.1 |
| `massage/G-best-nd+strict+top90-conf+no-filter-miss` | 71.4% (55/77) | 77 | 36.4% | 59.7% | 41.6% | +11.4 |
| `massage/G-best-nd+strict+top90-conf+explicit` | 77.3% (17/22) | 22 | 31.8% | 68.2% | 0.0% | +17.3 |
| `massage/G-best-nd+strict+top90-conf+explicit+inferred` | 75.6% (34/45) | 45 | 24.4% | 71.1% | 0.0% | +15.6 |
| `massage/G-best-nd+strict+top80-conf+all` | 72.5% (58/80) | 80 | 40.0% | 51.2% | 48.8% | +12.5 |
| `massage/G-best-nd+strict+top80-conf+no-filter-miss` | 73.5% (50/68) | 68 | 35.3% | 58.8% | 39.7% | +13.5 |
| `massage/G-best-nd+strict+top80-conf+explicit` | 85.0% (17/20) | 20 | 30.0% | 70.0% | 0.0% | +25.0 |
| `massage/G-best-nd+strict+top80-conf+explicit+inferred` | 85.0% (34/40) | 40 | 22.5% | 70.0% | 0.0% | +25.0 |
| `massage/G-best-nd+strict+top70-conf+all` | 71.4% (50/70) | 70 | 40.0% | 51.4% | 44.3% | +11.4 |
| `massage/G-best-nd+strict+top70-conf+no-filter-miss` | 73.3% (44/60) | 60 | 33.3% | 61.7% | 35.0% | +13.3 |
| `massage/G-best-nd+strict+top70-conf+explicit` | 82.4% (14/17) | 17 | 29.4% | 76.5% | 0.0% | +22.4 |
| `massage/G-best-nd+strict+top70-conf+explicit+inferred` | 85.7% (30/35) | 35 | 22.9% | 74.3% | 0.0% | +25.7 |
| `massage/G-best-nd+strict+top50-conf+all` | 60.0% (30/50) | 50 | 30.0% | 56.0% | 30.0% | +0.0 |
| `massage/G-best-nd+strict+top50-conf+no-filter-miss` | 65.1% (28/43) | 43 | 25.6% | 55.8% | 23.3% | +5.1 |
| `massage/G-best-nd+strict+top50-conf+explicit` | 75.0% (9/12) | 12 | 33.3% | 83.3% | 0.0% | +15.0 |
| `massage/G-best-nd+strict+top50-conf+explicit+inferred` | 84.0% (21/25) | 25 | 28.0% | 76.0% | 0.0% | +24.0 |
| `massage/G-best-nd+strict+top90-score+all` | 66.7% (60/90) | 90 | 41.1% | 47.8% | 53.3% | +6.7 |
| `massage/G-best-nd+strict+top90-score+no-filter-miss` | 66.2% (51/77) | 77 | 35.1% | 57.1% | 42.9% | +6.2 |
| `massage/G-best-nd+strict+top90-score+explicit` | 68.2% (15/22) | 22 | 36.4% | 72.7% | 0.0% | +8.2 |
| `massage/G-best-nd+strict+top90-score+explicit+inferred` | 64.4% (29/45) | 45 | 22.2% | 71.1% | 0.0% | +4.4 |
| `massage/G-best-nd+strict+top80-score+all` | 65.0% (52/80) | 80 | 41.2% | 46.2% | 57.5% | +5.0 |
| `massage/G-best-nd+strict+top80-score+no-filter-miss` | 63.2% (43/68) | 68 | 33.8% | 54.4% | 47.1% | +3.2 |
| `massage/G-best-nd+strict+top80-score+explicit` | 65.0% (13/20) | 20 | 35.0% | 75.0% | 0.0% | +5.0 |
| `massage/G-best-nd+strict+top80-score+explicit+inferred` | 60.0% (24/40) | 40 | 17.5% | 67.5% | 0.0% | +0.0 |
| `massage/G-best-nd+strict+top70-score+all` | 68.6% (48/70) | 70 | 45.7% | 48.6% | 60.0% | +8.6 |
| `massage/G-best-nd+strict+top70-score+no-filter-miss` | 63.3% (38/60) | 60 | 36.7% | 56.7% | 50.0% | +3.3 |
| `massage/G-best-nd+strict+top70-score+explicit` | 58.8% (10/17) | 17 | 29.4% | 70.6% | 0.0% | -1.2 |
| `massage/G-best-nd+strict+top70-score+explicit+inferred` | 54.3% (19/35) | 35 | 14.3% | 68.6% | 0.0% | -5.7 |
| `massage/G-best-nd+strict+top50-score+all` | 80.0% (40/50) | 50 | 52.0% | 46.0% | 66.0% | +20.0 |
| `massage/G-best-nd+strict+top50-score+no-filter-miss` | 76.7% (33/43) | 43 | 44.2% | 60.5% | 55.8% | +16.7 |
| `massage/G-best-nd+strict+top50-score+explicit` | 41.7% (5/12) | 12 | 25.0% | 75.0% | 0.0% | -18.3 |
| `massage/G-best-nd+strict+top50-score+explicit+inferred` | 64.0% (16/25) | 25 | 20.0% | 72.0% | 0.0% | +4.0 |
| `massage/G-best-nd+lenient+top100+all` | 78.0% (78/100) | 100 | 52.0% | 54.0% | 67.0% | +18.0 |
| `massage/G-best-nd+lenient+top100+no-filter-miss` | 78.8% (67/85) | 85 | 49.4% | 62.4% | 61.2% | +18.8 |
| `massage/G-best-nd+lenient+top100+explicit` | 87.5% (21/24) | 24 | 54.2% | 75.0% | 41.7% | +27.5 |
| `massage/G-best-nd+lenient+top100+explicit+inferred` | 86.0% (43/50) | 50 | 46.0% | 78.0% | 34.0% | +26.0 |
| `massage/G-best-nd+lenient+top90-conf+all` | 76.7% (69/90) | 90 | 50.0% | 54.4% | 64.4% | +16.7 |
| ★ `massage/G-best-nd+lenient+top90-conf+no-filter-miss` | 77.9% (60/77) | 77 | 46.8% | 63.6% | 58.4% | +17.9 |
| ★ `massage/G-best-nd+lenient+top90-conf+explicit` | 86.4% (19/22) | 22 | 50.0% | 72.7% | 36.4% | +26.4 |
| ★ `massage/G-best-nd+lenient+top90-conf+explicit+inferred` | 86.7% (39/45) | 45 | 42.2% | 77.8% | 28.9% | +26.7 |
| `massage/G-best-nd+lenient+top80-conf+all` | 75.0% (60/80) | 80 | 48.8% | 53.8% | 61.3% | +15.0 |
| ★ `massage/G-best-nd+lenient+top80-conf+no-filter-miss` | 76.5% (52/68) | 68 | 45.6% | 61.8% | 54.4% | +16.5 |
| ★ `massage/G-best-nd+lenient+top80-conf+explicit` | 85.0% (17/20) | 20 | 45.0% | 70.0% | 30.0% | +25.0 |
| ★ `massage/G-best-nd+lenient+top80-conf+explicit+inferred` | 90.0% (36/40) | 40 | 40.0% | 75.0% | 25.0% | +30.0 |
| `massage/G-best-nd+lenient+top70-conf+all` | 72.9% (51/70) | 70 | 48.6% | 52.9% | 57.1% | +12.9 |
| ★ `massage/G-best-nd+lenient+top70-conf+no-filter-miss` | 75.0% (45/60) | 60 | 43.3% | 63.3% | 50.0% | +15.0 |
| ★ `massage/G-best-nd+lenient+top70-conf+explicit` | 82.4% (14/17) | 17 | 47.1% | 76.5% | 29.4% | +22.4 |
| ★ `massage/G-best-nd+lenient+top70-conf+explicit+inferred` | 88.6% (31/35) | 35 | 40.0% | 77.1% | 22.9% | +28.6 |
| `massage/G-best-nd+lenient+top50-conf+all` | 62.0% (31/50) | 50 | 42.0% | 58.0% | 46.0% | +2.0 |
| ★ `massage/G-best-nd+lenient+top50-conf+no-filter-miss` | 67.4% (29/43) | 43 | 39.5% | 58.1% | 41.9% | +7.4 |
| ★ `massage/G-best-nd+lenient+top50-conf+explicit` | 75.0% (9/12) | 12 | 50.0% | 83.3% | 33.3% | +15.0 |
| ★ `massage/G-best-nd+lenient+top50-conf+explicit+inferred` | 88.0% (22/25) | 25 | 48.0% | 80.0% | 28.0% | +28.0 |
| `massage/G-best-nd+lenient+top90-score+all` | 76.7% (69/90) | 90 | 54.4% | 52.2% | 71.1% | +16.7 |
| ★ `massage/G-best-nd+lenient+top90-score+no-filter-miss` | 77.9% (60/77) | 77 | 50.6% | 62.3% | 63.6% | +17.9 |
| ★ `massage/G-best-nd+lenient+top90-score+explicit` | 86.4% (19/22) | 22 | 59.1% | 77.3% | 45.5% | +26.4 |
| ★ `massage/G-best-nd+lenient+top90-score+explicit+inferred` | 84.4% (38/45) | 45 | 48.9% | 80.0% | 35.6% | +24.4 |
| `massage/G-best-nd+lenient+top80-score+all` | 76.2% (61/80) | 80 | 55.0% | 51.2% | 76.2% | +16.2 |
| ★ `massage/G-best-nd+lenient+top80-score+no-filter-miss` | 76.5% (52/68) | 68 | 51.5% | 60.3% | 70.6% | +16.5 |
| ★ `massage/G-best-nd+lenient+top80-score+explicit` | 85.0% (17/20) | 20 | 60.0% | 80.0% | 45.0% | +25.0 |
| ★ `massage/G-best-nd+lenient+top80-score+explicit+inferred` | 82.5% (33/40) | 40 | 47.5% | 77.5% | 40.0% | +22.5 |
| `massage/G-best-nd+lenient+top70-score+all` | 77.1% (54/70) | 70 | 55.7% | 52.9% | 77.1% | +17.1 |
| ★ `massage/G-best-nd+lenient+top70-score+no-filter-miss` | 75.0% (45/60) | 60 | 50.0% | 61.7% | 71.7% | +15.0 |
| ★ `massage/G-best-nd+lenient+top70-score+explicit` | 82.4% (14/17) | 17 | 58.8% | 76.5% | 52.9% | +22.4 |
| ★ `massage/G-best-nd+lenient+top70-score+explicit+inferred` | 80.0% (28/35) | 35 | 48.6% | 80.0% | 45.7% | +20.0 |
| `massage/G-best-nd+lenient+top50-score+all` | 84.0% (42/50) | 50 | 56.0% | 48.0% | 76.0% | +24.0 |
| ★ `massage/G-best-nd+lenient+top50-score+no-filter-miss` | 86.0% (37/43) | 43 | 51.2% | 65.1% | 72.1% | +26.0 |
| ★ `massage/G-best-nd+lenient+top50-score+explicit` | 75.0% (9/12) | 12 | 58.3% | 83.3% | 66.7% | +15.0 |
| ★ `massage/G-best-nd+lenient+top50-score+explicit+inferred` | 80.0% (20/25) | 25 | 40.0% | 80.0% | 40.0% | +20.0 |
