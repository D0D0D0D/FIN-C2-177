# テスト仕様書

## 方針

外部 API、LLM、DB、ベクトル検索は注入シームの fake に置換し、テスト中にネットワークへ接続しない。HITL は outer / inner を別 `InMemorySaver` で compile した実グラフを用いる。S-3 の正常系と拒否系は、出荷する `config/config.yaml` の値を読み込んで実行する。

## Framework compliance

| ID | 対象 | 期待結果 | 実装テスト | 結果 |
|---|---|---|---|---|
| TC-01 | State 契約 | flat TypedDict、credential field なし | `test_tc01_state_is_typed_dict_and_contains_no_credential_fields` | PASS |
| TC-02 | S-2 違反 | trusted scope 不在を fail-closed | `test_tc02_preprocess_security_violation_blocks_untrusted_scope` | PASS |
| TC-03 | credential 不在 | State / checkpoint に credential を置かない | `test_state_safety.py`、checkpoint PB | PASS |
| TC-04 | InvocationContext 経路 | tenant / org は `input_context` のみから取得 | `test_tc04_scope_comes_from_input_context_not_invocation_context` | PASS |
| TC-05 | S-4 | lifecycle event を node execute で重複発行しない | 静的レビュー + 全 node の domain event | PASS |
| TC-06 | S-2 非バイパス | final input gate の override は TypeError | `test_framework_compliance_tc06_tc07.py` | PASS |
| TC-07 | S-3 非バイパス | final output gate の override は TypeError | `test_framework_compliance_tc06_tc07.py` | PASS |
| TC-08 | trust level | ANONYMOUS は VERIFIED_EXTERNAL node に到達不可 | `test_tc08_verified_external_trust_is_required` | PASS |

## 不変条件・業務ロジック

| ID | 受入条件 | テスト | 結果 |
|---|---|---|---|
| INV-1 | 単一の検証済み pin のみを使う | adapter resolver の ref/version/hash 検証 | PASS |
| INV-2 | vendor_type を推定しない | `PreProcessNode` の必須入力、Stage 3a prompt | PASS |
| INV-3 | 禁止判断キーを構造排除 | `test_s3_closed_schema_rejects_forbidden_nested_judgement_key` | PASS |
| INV-4 | support_level は 4 値 | `test_inv4_support_level_is_limited_to_candidate_values` | PASS |
| INV-5 | unknown source は reject | Stage 4 / S-3 source registry 検証 | PASS |
| INV-6 | tenant + org の二重境界 | `test_inv6_and_inv11_scoped_retrieval_requires_both_boundaries` | PASS |
| INV-7 | 生証跡を checkpoint に残さない | `test_invoke_fallback_emits_closed_envelope_and_never_checkpoints_evidence_body` | PASS |
| INV-8 | ingest は統制表・観点表を受理しない | `PreProcessNode` ingest allowlist | PASS |
| INV-9 | pin 版・hash を出力に残す | integration envelope `applied_pins` | PASS |
| INV-10 | feedback は観点表を更新しない | `ScopedKnowledgeBase.append_feedback` は audit / candidate のみ | PASS |
| INV-11 | case / vendor の検索述語を分離 | `test_inv6_and_inv11_scoped_retrieval_requires_both_boundaries` | PASS |
| INV-12 | non-undetermined は今回・適格・control一致・span内の根拠を要する | `test_inv12_stage4_rejects_missing_foreign_or_out_of_range_basis` | PASS |
| INV-13 | 偽造・失効 pin を fail-closed | `PinResolver.resolve` の resolver contract | PASS |
| INV-14 | prior_finding は as_of を必須に分離 | `validate_invoke_envelope` | PASS |
| INV-15 | requirement / scope 変更時は not_comparable | `ComparisonNode` deterministic comparator | PASS |
| INV-16 | prior あり + non-undetermined は HITL | PB-7 compiled graph | PASS |
| INV-17 | Stage 3b は Stage 3a を変更しない | `test_inv17_comparison_does_not_overwrite_current_assessment` | PASS |

## Proof of boundary

| ID | 境界 | テスト | 結果 |
|---|---|---|---|
| PB-1 | audit | 各 execute の domain trace event | PASS |
| PB-2 | State 直列化 | `test_state_safety.py` | PASS |
| PB-3 | 外部サービス | payload / KB / LLM の fake 注入 | PASS |
| PB-4 | import isolation | `test_import_isolation.py` | PASS |
| PB-5 | checkpoint safety | sentinel not-in checkpoint | PASS |
| PB-6 | invoke 順序 | `test_pb_invoke_order.py` | PASS |
| PB-7 | GraphInterrupt 伝播・resume | `test_pb7_hitl_interrupt_propagation.py` | PASS |

## 実行結果

2026-07-28 に repo root で `../.venv/bin/python -m pytest -q --tb=line` を実行。**163 passed, 11 errors** だった。errors はリリース検証用の `HTTPServer` fixture が loopback socket を bind する処理に起因し、この sandbox では socket bind が禁止されるため、その fixture を使う 11 ケースに波及したもの。それ以外の template / boundary tests は PASS。

## 実 LLM 1 回転検証（未達 — LLM 経路は到達済み・Stage 3a のプロンプト欠陥で縮退）

FW はまだ実 LLM をサポートしないため、注入シームへ実 LLM クライアントを刺して 1 回転させる
検証を実施した。

| 項目 | 内容 |
|---|---|
| 実施日 | 2026-08-19 |
| 使用 LLM | Azure OpenAI 上の GPT モデルを `config["llm"]` へ注入 |
| 注入シーム | `config["llm"]`（`CurrentAssessmentNode` が `self._config.get("llm")` で取得） |
| 実 LLM 呼び出し | **1 回**（prompt 135 字 / response 151 字）— シームには届いている |
| 入力 | PB-7 と同じ構築前提・同じ入力（`tests/conftest.py` の `agent_config` / `case_payload` / `live_input_context`） |
| 結果 | ❌ `degradation_reason: llm_fallback`。`evidence_brief.control_requirements` が deterministic フォールバック（`support_level: undetermined`）のまま返る |

**原因（AG 側のプロンプト欠陥）**: `CurrentAssessmentNode.execute()` が LLM へ渡すのは
`{"controls": …, "source_ids": …, "vendor_type": …}` の **JSON ダンプのみ**で、
指示文も出力スキーマも無い。実 LLM は返す形を知りようがなく、
`json.loads` / `control_requirements` の形検査に落ちて `llm_fallback` へ縮退する。
ユニット/統合テストの `_DraftLLM` は完成形の JSON を返すため、この欠陥は fake では顕在化しない。

**ゲートは正しく機能している**（縮退を握り潰さず `degradation_reason` に立てている）。
直すべきはプロンプト側で、実装基盤の更新とは独立した変更として対応する。
同型の既知事例: EnvironmentalImpactAssessmentDraftingAgent / Return Fraud Investigation Memo Drafting Agent / Meeting Commitment Ledger & Follow-up Recall Agent / Tourist Information Desk Visitor Concierge Agent（いずれも
「プロンプトが出力スキーマを強制していない・fake は完成品を返す」）。

再検証では、PB-7 と同じ fixture、入力、注入シームを使用する。

### 修正後の再検証（2026-08-19・Stage 3a へ今回証跡を渡す）

**修正の性質**: 単なるプロンプト不備ではなく、`docs/02_design.md` §8 が定める
「Stage 3a には統制表・観点表（版固定）＋**今回証跡のみ**を渡す」が実装されていなかった
（渡していたのは `source_id` の一覧だけ）。**設計に実装を合わせた**。

🔴 **`TransientPayloadStore.resolve()` は one-shot**（`entry.consumed = True`）。Stage 1
`DeterministicCheckNode` が `evidence_payload_ref` を消費済みのため、Stage 3a は再読み込みできない。
そこで Stage 1 が**サニタイズ済み本文を同じ tenant/org/case スコープで second handle に束ね直し**、
State には ref（`evidence_text_ref`）だけを載せる。**本文は State に一切載らない**ので
checkpoint も汚さない（既存の sentinel 非混入テストが継続して守る）。
本文のオフセットは `source_registry[*].span_end` を測った対象と同一なので、
モデルが引用する spans は Stage 4 の決定論検証とそのまま整合する。

| 項目 | 修正前 | 修正後 |
|---|---|---|
| `degradation_reason` | `llm_fallback`（毎回） | **空**（縮退なし） |
| LLM 応答 | 形が合わず shape 検査で棄却 | `support_level: "supported"` + 証跡本文に接地した `claim` + `spans: [{start:0, end:104}]`（`span_end` = 104 と整合） |
| invoke 結末 | `success`（決定論フォールバックの `undetermined`） | **`awaiting_human`** — 過去評価があり今回が `undetermined` 以外になったため INV-16 の HITL ゲートが設計どおり発火 |

**この結末の変化が正しい理由**: 修正前は LLM が実質的に無力で常に `undetermined` に落ちていたため、
「過去あり × `undetermined` 以外 → 人手レビュー」という設計上の中核ゲートが**一度も発火し得なかった**。
実 LLM で初めて発火した。

**回帰の固定**: `tests/unit/test_stage3a_evidence_prompt.py`
（①証跡本文と `span_end` がプロンプトに入る ②出力契約と `support_level` の許容値が明記される
③`prior_findings` はプロンプトに入らない＝アンカリング遮断 ④本文が返り値＝State に載らない
⑤証跡を読めなかったとき **黙って `undetermined` にせず `degradation_reason` を立てる**）。
①と⑤は、旧実装へ戻す変異でそれぞれ赤くなることを確認済み。

再検証では同じ fixture と入力を使用し、縮退理由、根拠 span、HITL 到達を確認する。

#### 🔴 この修正が新たに開けた経路と、その塞ぎ方

Stage 3a が証跡本文を読むようになったことで、**モデルが本文を `claim` / `observation` へ写して
State に還流させる**経路が生じた。これらは返り値なので **checkpoint にも呼び出し元にも届く**。
証跡の provenance は `vendor_submission`（非信頼）なので、**文書自身が写経を指示**することもありうる。
修正前の実装ではモデルが本文を見られなかったため、この経路は存在しなかった＝**本修正が作った退行**。

実測（`tests/integration/test_invoke_workflow.py::test_body_echoing_model_cannot_place_evidence_into_the_checkpoint_or_output`）:
ガード無しでは sentinel が **checkpoint と output の両方に出る**（実際に確認）。

**塞ぎ方**: Stage 3a が草案を**受け取る時点**で（＝返り値に載せる前。本文を返り値に載せる
最初のノードが検査を担う）、
`claim` / `observation` の長さ（既定 400 字）と source からの**逐語連続長**（既定 60 字）を検査し、
越えたものは `degradation_reason: "llm_draft_rejected"` として決定論草案へ差し戻す。
🔴 **切り詰めない** — 半端な文は主張の意味を変えるため。閾値は `claim_max_chars` /
`verbatim_guard_chars` で上書きできる。

ガードを外す変異で当該テストが赤くなること、および**実 LLM の正当な短い claim は誤って
弾かれない**（`llm_draft_rejected` が立たず縮退なしで通る）ことの両方を実測済み。
