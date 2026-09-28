# Template Design Specification — FIN-C2-177

> Financial Outsourcing Due-Diligence Evidence Brief Agent
> Purpose: Specify a source-linked outsourcing due-diligence evidence-review workflow.
> SDK: `agenticstar-agentcore==1.0.0`（実機スパイク確認日 **2026-07-27**）

## Position in AgentCore Architecture

- **Agent Class**: `OutsourcingDueDiligenceBriefAgent`（`src/graph/graph.py`・outer graph）
- **L1 Base**: **AgentBaseGraph**（L1 直接継承）。DocGenerationAgent パターン。
  - 旧 Level 2 base 継承モデルは廃止され、本テンプレートは L1 backbone を直接継承する
    （移行注記: 旧「Level 2 base 継承」→「L1 直接継承」）。
- **Three-Layer Separation**:
  - State: flat TypedDict composition（Pydantic 不可 — msgpack 非互換）
  - Node: L1 継承（Template Method: `execute(self, state) -> dict` のみ override）
  - Graph: composition（`register_nodes()` でノード差し替え、`add_edges()` / `route()` は override しない）

本テンプレートは **Cat 2**。外側 backbone は Cat 1 と同一の固定 5 ノードで、ドメインの多段ロジックは
`main` スロットに割り当てた `GraphNode`（内側 domain workflow graph）に隠蔽する。

### AGENTIC STAR Marketplace 接続用サンプル

repo 直下の `cli.py` は read-only の `invoke` だけを公開し、`config/*.sample.json` の完全に架空の
統制表・観点表・証跡を起動時に検証して使用する。この経路は接続確認専用であり、HTTP adapter には
サンプル pin を自動登録しない。本番では同梱サンプルではなく、顧客の正本システムから同じ pin 契約を
満たす承認済みレコードを供給する。公開する `evidence_sources` は Stage 1 が決定論的に作った
`source_registry` の安全なメタデータ投影であり、モデルに provenance を生成させない。

## Architecture Overview

### 3 実行モード

| Mode | エントリ | 目的 | 主な書込 |
|------|---------|------|---------|
| `ingest` | `POST /ingest` | **当該案件の委託先提出証跡**を安全に取り込み、出典化（`source_registry` 採番）して案件スコープの証跡ストアに格納する。**統制表・観点表はここに入れない**（下記 §統制ガバナンス）。 | `outsourcing_cases` / `case_evidence`（DB）、当該案件の証跡 projection（KB・検索補助用） |
| `invoke` | `POST /invoke` | 新規委託先案件について、統制証跡を**入力として渡された統制表・観点表（版固定）**に照らして照合し、証拠ブリーフのドラフト＋根拠マップを生成。 | なし（読み取り + ドラフト生成）。低信頼時 HITL suspend |
| `feedback` | `POST /feedback` | 人間が編集・確定した論点を、**append-only のレビュー記録**として保存する。**現行の観点表を自動更新しない**（候補キューに積むのみ）。 | `feedback_audit_events` / `perspective_candidates`（DB） |

### 🔴 統制ガバナンス — 統制表・観点表は「入力」であって「KB 蓄積物」ではない

> **設計変更（2026-07-27・実務要件の見直し）**。
> 初版は統制表と「過去のレビュー観点」の両方を `ingest` で KB に蓄積する設計だったが、これは**誤り**。

**実務上の根拠**（⚠️ 検証環境の DNS 制限で公開 Web のライブ取得は不可＝
規制文書の既知条項と公式発行元 URL に基づく**推論**。設計採用前に人が現行版を要確認）:

- 第三者リスク評価のチェックリストは、SIG（Shared Assessments）や GRC 製品
  （ServiceNow VRM / Archer TPRM / OneTrust）に見られるとおり、**所有者・承認・有効日を持つ
  版管理された配布物**として扱われるのが実務。「似た観点をベクトル検索で拾う」対象ではない。
- 米 Interagency Guidance（2023）の文書化要求、EBA outsourcing guideline の登録簿、
  DORA 第 28 条の情報登録簿は、いずれも**「何を基準に、なぜその評価をしたか」を後から
  再現・説明できること**を要求する。
- したがって **「どの版の統制表を適用したか」が監査上の記録対象**になる。
  統制 5.2 が翌年度に改訂されれば、昨年度の `supported` は今年度の 5.2 に対する判定ではない。
  SOC 2 も対象期間と Trust Services Criteria の版に依存する。

**設計上の帰結:**

| 成果物 | 扱い | 理由 |
|---|---|---|
| **統制表（control catalogue）** | 🔴 **`invoke` の入力**（`catalogue_version` + `content_hash` で版固定） | 正本は顧客の GRC / 文書管理基盤。AG は**適用するだけ**で所有しない |
| **観点表（perspective pack）** | 🔴 **`invoke` の入力**（`perspective_pack_version` + `content_hash`） | 「統制 5.2 では BCP 本文・直近テスト結果・対象サービス範囲を確認する」といった**判定に使う知見**は、統制表と同格の**承認済み成果物**。AI が自動蓄積して判定基準を書き換えてはならない |
| **フレームワーク対応表**（SIG ↔ FISC ↔ SOC 2 等） | **入力**（`mapping_pack_version`） | 同上。ベクトル KB ではなく版管理された対応表 |
| **当該案件の委託先提出証跡** | **`ingest` で取り込み・KB 検索対象** | ここが RAG の正当な適用範囲。同一ベンダーの再評価でも有用 |
| **過去の同一ベンダー評価** | 🔴 **結論も含めて参照する**（`prior_finding` として由来明示）。ただし**今回証跡による裏付けなしに今回の判定根拠にはできない** | 年次見直しの中核価値は「前回不足 → その後改善されたか」の差分。結論を捨てると再評価がゼロからになり蓄積の意味が消える。危険なのは「結論を持つこと」ではなく「**検証なしに引き継ぐこと**」 |
| **人間フィードバック** | **append-only のレビュー記録**＋**候補キュー** | 人の承認を経て**次版の観点表へ昇格**させる。現行観点表への自動反映は禁止 |
| **クロスベンダー傾向** | 限定的（匿名化・集計のみ） | 個社提出物の横断検索は守秘義務・目的外利用・競争上の機微の観点で危険。「ベンダー A の弱点」を B の評価に直接持ち込まない |

> **蓄積ストーリーの正直な位置づけ**: 「経験を貯めれば AI が勝手に評価基準を賢くする」という
> moat（AI が判定基準そのものを書き換える形）は**採らない**。本 AG の蓄積価値は
> ①**同一ベンダーの継続評価**（`prior_finding` + `changed_since_last` による前回比。再評価の中核）
> ②案件証跡の安全な取り込みと出典化
> ③人が承認した知見を次版観点表へ昇格させる**人間主導の改善ループ**にある。
> ⚠️ **「過去結論を一切見せない」という意味ではない**（2026-07-28 是正）。結論は由来を明示して渡し、
> 今回証跡による裏付けを必須にする。

**版の記録（監査要件）**: 各評価レコードは最低限
`catalogue_version` / `perspective_pack_version` / `mapping_pack_version` /
証跡の `document_version`・報告対象期間 と、それぞれの `content_hash` を保存する
（同一版番号での差替え・誤配布を検出するため）。

#### 統制パックの解決（resolver）— pin は「中身」ではない

`*_ref` + `*_version` + `*_content_hash` は**同一性の証明**であって**評価可能な内容ではない**。
入口 adapter が正本から実体を解決する責務を負う:

1. **正本ソースから `ref` + `version` で実体を取得**（顧客の GRC / 文書管理基盤。AG は正本を持たない）
2. **検証**: 取得した実体の hash が `content_hash` と一致するか / **承認済みか** /
   **有効期間内か**（`effective_from` ≤ 評価基準日 ≤ `effective_to`）/ **失効・取消されていないか**
3. いずれかに失敗したら **fail-closed で 4xx**（グラフを起動しない）。
   🔴 **呼び出し元が `ref`/`version`/`hash` を自己申告しても、正本に存在しなければ通さない**
   （偽造 pin で任意の統制表を持ち込ませない）
4. 検証済み実体を**不変シール参照**（読み取り専用・当該 invocation スコープ）として渡し、
   Stage 3 はそのシール参照経由でのみ統制表・観点表を読む
5. **pin バンドル一式を出力 envelope と評価レコードの両方に転記**する

| 失敗ケース | 挙動 |
|---|---|
| `ref`/`version`/`hash` のいずれか欠落 | 400（INV-9） |
| 正本に存在しない `ref`/`version` | 404 相当で拒否（存在秘匿のため一律 400/403） |
| hash 不一致（同一版番号で中身が差し替わっている） | **403 で拒否**・監査イベント発火 |
| 未承認 / 失効 / 有効期間外 | 拒否。**「評価基準日」を明示**して、過去日基準の再評価は明示指定時のみ許す |

#### 観点表（perspective pack）のスキーマ契約

観点表は統制表と同格の承認済み成果物なので、**構造を固定**する
（自由記述の寄せ集めにすると「実質的に AI が判定基準を作る」に戻る）:

sealed pack の実体は `{"rules": [...]}` とする。各 rule の `applies_to.control_ids` は
対象統制 ID の非空配列、`applicability.vendor_types` / `service_scope_fingerprints` は
適用値の配列（空配列は当該軸で全対象）とする。Stage 3a は人間指定の `vendor_type` と
固定済み `service_scope_fingerprint` に合い、かつ今回の統制 ID と交差する rule だけを
pack 内の順序を保って選ぶ。sealed ref が解決できない、pack が空、または適用 rule が 0 件なら
LLM を呼ばず全件 `undetermined` とし、`degradation_reason=perspective_pack_unavailable` を残す。

| フィールド | 内容 |
|---|---|
| `applies_to` | 適用先の `catalogue_id` + `catalogue_version` + 対象 `control_id` 群 |
| `applicability` | 適用条件（委託先類型・サービス範囲）。条件に合わない観点は**適用しない** |
| `expected_evidence` | 期待する証跡種別（必須 / 推奨の区別を持つ） |
| `minimum_sufficiency` | 「最低これが書かれていなければ `insufficient`」の決定論的条件 |
| `contradiction_signals` | 矛盾とみなすシグナル |
| `verification_questions` | 不足時に出す確認質問のひな型 |
| `approval` | 所有者・承認者・承認日・`effective_from` / `effective_to` |

**競合時の解決**: 同一 `control_id` に複数の観点が該当した場合の優先順位を pack 内で宣言する
（未宣言なら fail-closed＝AI に優先順位を決めさせない）。

#### 候補キュー（`perspective_candidates`）の状態遷移

`feedback` が積むのは**候補**であり、観点表そのものではない:

```
proposed（feedback が積む・出典と提案理由つき）
   → under_review（観点表オーナーが査読）
   → approved → 【AG の外で人間が次版 pack を発行】→ 新 perspective_pack_version
   → rejected（理由を記録）
```

🔴 **`approved` になっても AG が現行 pack を書き換えることはない**。
次版 pack の発行は**顧客の統制ガバナンス側の作業**であり、AG はそこに書込権限を持たない。

`mode` は `state["mode"]` で分岐し、内側 domain workflow graph が条件辺でルーティングする。

> 🔴 **mode は書込権限の境界であり、リクエストボディから採らない**。
> `ingest` と `feedback` はテナントの DB / KB への**書込**を認可する。ボディの自己申告 `mode` を信じると、
> `/invoke` の呼び出し元が `mode=ingest` を名乗って書込できてしまう。
> → **mode は認証済みルート（どのエンドポイントを叩いたか）からのみ導出**し、入口 adapter が
> `input_context["entry_mode"]` として渡す。ボディに `mode` があっても**採用しない**
> （不一致は 400 で拒否）。`pre_process` は `input_context` 側の値だけを `state["mode"]` に採る。

### Node Configuration（outer backbone — 固定 5 ノード）

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | セッション初期化（framework 既定） | user_input | session_id, node_history | `InitializeNode`（default） |
| pre_process | テナント/組織/権限解決（fail-closed）・入力検証・S-2 情報最小化・mode 判定・案件参照 ID 化・**証跡本文の payload_ref 間接化** | user_input, invocation ctx | validated_input, tenant_id, org_id, mode, case_ref, evidence_payload_ref | `PreProcessNode(FunctionNode)` |
| main | `GraphNode` として内側 domain workflow graph を呼ぶ（Stage 1〜4 / mode 分岐） | validated_input, tenant_id, org_id, mode, case_ref, evidence_payload_ref | evidence_brief, evidence_map, gaps, contradictions, requires_human_review, status | `OutsourcingBriefGraphNode(GraphNode)` |
| post_process | S-3 出力検査（closed allowlist・claim→source 対応・機微再走査）・**`formatted_output` への envelope 畳み込み** | evidence_brief, evidence_map, … | formatted_output, result, status | `PostProcessNode(FunctionNode)` |
| finalize | 出力確定（framework 既定） | formatted_output | final_output | `FinalizeNode`（default） |

> 🔴 **`formatted_output` が必須な理由（SDK 実機確認 2026-07-27）**:
> `AgentBaseGraph.get_output()` は `state.get("formatted_output") or state.get("result")` しか外部に出さず、
> `evidence_brief` / `evidence_map` / `gaps` 等の**カスタム欄は透過しない**。
> post_process で envelope に畳み込まなければ、外部からブリーフ本体が一切見えない。

### 内側 domain workflow graph（`src/graph/domain_workflow_graph.py`）

`OutsourcingBriefGraphNode.get_subgraph()` が返す内側グラフ。mode により経路が分岐する。

```
                          ┌─ mode=ingest ──→ [IngestNode] ──────────────────────→ END
[inner initialize] → {route by mode} ─┼─ mode=feedback → [FeedbackNode] ───────────────────→ END
                          └─ mode=invoke ─→ [Stage1 DeterministicCheckNode]
                                               → [Stage2 RecallNode（同一テナント/組織 KB 想起）]
                                               → [Stage3a CurrentAssessmentNode（LLM 当てはめ・核／今回証跡のみ）]
                                               → [Stage3b ComparisonNode（LLM／凍結3a + 前回評価で前回比）]
                                               → [Stage4 EvidenceVerifyNode（claim→source 検証・HITL）]
                                               → END
```

> 内側グラフの分岐関数（`route`）と `get_output()` は **自スキーマで型注釈**する
> （`AgentState` 注釈だと射影でカスタム欄が消え誤 route する）。

| Inner Node | 主要ロジック | 出力 |
|---|---|---|
| Stage 1 `DeterministicCheckNode` | 書式・ファイル妥当性を**決定論的**に検証。1 文書 200,000 文字に加え、dereference 直後に文書数（既定 20）と全文書の合計文字数（既定 400,000）を検査し、超過時は**切り詰め・間引きをせず invocation を fail-closed**。🔴 **統制に紐づく「必須証跡」の定義は config に置かない**（版管理されない影の統制表になる）— 必須証跡は**観点表 `expected_evidence`（版固定入力）から引く**。config に置いてよいのは技術的な形式チェックのみ。意味的結論は出さない。**提出証跡から `source_registry`（source_id → doc_ref / span_ref / provenance）を採番**する。 | `docs_present`, `format_check`, `gaps`（構造欠落）, `source_registry` |
| Stage 2 `RecallNode` | DB をテナント・組織で絞り（`tenant_id` **と** `org_id` の両方が必須・不在は fail-closed）、**2 つの独立した検索述語**で引く（下記）。🔴 **判定に使う観点は KB から想起しない**（入力の観点表が唯一の判定基準）。 | `prior_findings`, `changed_since_last`, `case_evidence_hits` |
| Stage 3a `CurrentAssessmentNode`（**LLM 核**） | 統制証跡を承認済み統制表と、sealed 観点表から決定論選択した適用 rule に当てはめ、統制要求ごとに**支持 / 不足 / 矛盾**を抽出。統制表・観点表・pin は `system` の `TRUSTED_CONFIG`、委託先本文は別 `user` message の `UNTRUSTED_INPUT` とし、同一 JSON/文字列へ連結しない。**事実と推論を分離**。**source は `source_registry` の既存ハンドルの選択のみ可（LLM に source ID を創作させない）**。🔴 **前回評価をプロンプトに含めない**（アンカリング遮断）。`undetermined` 以外にはちょうど 1 件の `current_evidence_basis` を必須で付し、`undetermined` には basis を禁止する。 | `evidence_brief`（draft）, `current_evidence_basis` |
| Stage 3b `ComparisonNode`（LLM） | **凍結した 3a の結果**（読み取り専用）と `prior_finding` を突き合わせ、統制要求ごとの前回比と差分説明を生成。🔴 **3a の `support_level` / `claim` は書き換えられない**。 | `changed_since_last`（理由コード付き） |
| Stage 4 `EvidenceVerifyNode` | 全論点に source link 必須。**`evidence_map` は `source_registry` から決定論的に構築**し、LLM が付けた source ID が registry に**実在するか**・span が原文の範囲内かを検証。未知 ID / 範囲外は reject → fail-closed。低信頼で HITL `interrupt()`。 | `evidence_map`, `contradictions`, `requires_human_review` |
| `IngestNode` | **当該案件の委託先提出証跡**を品質ゲート（マジックバイト判定・テキスト品質）にかけ、案件スコープで格納し出典化する。🔴 **統制表・観点表はここで受け付けない**（それらは `invoke` の版固定入力）。 | `ingest_result` |
| `FeedbackNode` | 人間が確定・修正した論点を **append-only のレビュー記録**として保存し、再利用候補は `perspective_candidates` キューに積む。🔴 **現行の観点表を自動更新しない**（次版への昇格は人間の承認を経る）。 | `feedback_result` |

> LLM ノードは Stage 3a / 3b の 2 つのみ（3b は比較専用で今回判定を変更しない）。証跡有無・書式（Stage 1）と根拠検証（Stage 4 の決定論部分）は
> LLM に依存しない。**LLM に事実文字列（source ID・規制適合結論）を創作させない**設計。

#### Stage 2 の検索述語（2 本を明確に分ける）

| 述語 | スコープキー | 目的 |
|---|---|---|
| **当該案件の証跡** | `tenant_id` + `org_id` + **`case_ref`** | 今回提出された証跡の中を検索（RAG の正当な適用範囲） |
| **同一ベンダーの過去評価** | `tenant_id` + `org_id` + **`vendor_id`**（**案件をまたぐ**） | 再評価サイクルで「前回どこを見たか」を引く。`case_ref` で絞ると過去案件が引けないため別述語にする |

`vendor_id` は**入口 adapter が認証済みの委託先マスタから解決**する（呼び出し元の自己申告は採らない）。

#### 🔴 過去評価の扱い — 「渡さない」ではなく「由来を明示して渡し、再検証を強制する」

> **設計変更（2026-07-28・再評価要件の見直し）**。
> 一つ前の版は「前回の `support_level` と claim をプロンプトに渡さない」としていたが、これは**誤り**。
> ①**法的な誤認**: 参照した外部委託ガバナンス関連資料の範囲では、**AI が可能性を指摘することを一律に禁じる定めは確認できなかった**。
> これらが求めるのは「金融機関が委託先を適切に管理・評価すること」と**判断根拠の事後説明可能性**であり、
> 「AI に何を見せたか」を直接規律してはいない。前版は監査要件（適用版の再現性）から**必要以上に広い禁止**を導いていた。
> ⚠️ **これは法的クリアランスではない**。守秘義務・個人情報・契約上の制約・モデルリスク管理・社内規程は別途適用される。
> FISC は業界標準であって法令そのものではない。**採用可否は各機関の法務・コンプライアンス確認事項**。
> ②**業務価値の毀損**: 年次見直しの価値の中核は「**前回ここが不足で、その後改善されたか**」の差分。
> 過去結論を捨てると再評価が毎回ゼロからになり、同一ベンダー蓄積の意味がほぼ消える。

**本当の危険は「結論を持つこと」ではなく「検証なしに引き継ぐこと」**:

| 過去結論の扱い | 危険度 | 設計 |
|---|---|---|
| **参照・表示**する | ✅ 本設計の範囲では問題を確認せず | 再評価の中核価値。ただし守秘・契約・社内規程の制約は別途適用 |
| **今回の判定の根拠**にする | ⚠️ 危険 | 統制版・サービス範囲・文書日付が変われば無効 → **今回の証跡による裏付けを必須化** |
| **今回の証跡であるかのように混ぜる** | 🔴 危険 | 由来を構造的に分離（下記） |

**🔴 アンカリング対策 — Stage 3 を 2 段に分ける（同じ推論ステップに前回結論を混ぜない）**

前回結論を「同じ評価ステップ」に渡すと、LLM が前回 claim を言い換えて再提出し、
今回証跡から**関連の薄いものを 1 件引用してつじつまを合わせる**ことができてしまう
（＝結論ロンダリング）。「引用先が今回 registry に実在する」だけの検査では**これを止められない**。
→ 評価と比較を**別ステップ**にし、前者を凍結してから後者を回す。

| ステップ | 入力 | 出力 | 前回結論 |
|---|---|---|---|
| **Stage 3a `CurrentAssessmentNode`** | 統制表・観点表（版固定）＋**今回証跡のみ** | 今回の `support_level` / `claim` / `current_evidence_basis` | 🔴 **渡さない**（プロンプトに一切含めない） |
| **Stage 3b `ComparisonNode`** | **凍結した 3a の結果** ＋ `prior_finding` | `changed_since_last` ＋ 差分の説明 | ✅ 渡す |

🔴 **3b は 3a の `support_level` / `claim` を書き換えられない**（読み取り専用で受け取り、
比較結果のみを生成する）。これで「前回結論に引きずられた今回判定」が構造的に発生しない。

**Stage 3b へ渡す `prior_finding`:**

```
prior_finding: {
  support_level, claim, verification_questions, evidence_source_ids,
  as_of: { catalogue_version, catalogue_content_hash, requirement_text_hash,
           assessed_at, service_scope_fingerprint, document_versions[] }
}
```

**`current_evidence_basis`（今回判定の裏付け・決定論検証の対象）:**

```
current_evidence_basis: {
  control_id, source_ids[]（今回 invocation のもののみ）, spans[],
  observation（当該 requirement に紐づく限定的な観察）
}
```

- 🔴 **`undetermined` 以外の今回判定は `current_evidence_basis` が必須**。Stage 4 が決定論的に検証する:
  ①source が**今回の invocation** で登録されたものか（前回 assessment の source ID は不可）
  ②観点表の**時点・スコープ適格性**を満たすか ③**当該 control に紐づく**か（他 control の source の流用不可）
  ④span が原文範囲内か。いずれか不成立で reject（INV-12）。
- `undetermined` の basis、control requirement に対応しない orphan、同一 control の重複 basis は reject。
  残る basis は例外なく上記検証を通し、span は invocation 全体で最大 100 件とする
  （`source_ids[]` と `spans[]` は非空かつ同数）。S-3 も同じ構造制約を再検査する。
- ⚠️ **これは「意味的に支持している」ことの証明ではない**（§Stage 4 の注記と同じ限界）。
  意味的妥当性の最終確認は **HITL の人間レビュー**が担う。
  🔴 **`prior_finding` が存在する統制で今回 `undetermined` 以外を出す場合は HITL を必須**にする
  （前回比が出る＝再評価の核心部分なので人間の目を通す）。

**「今回の証跡」の定義**（曖昧さを残さない）: `source_registry` に載っているだけでは不十分で、
**①当該 assessment invocation で登録され ②観点表の時点・スコープ規則で当該 control に適格**なもの。
registry の各エントリは `assessment_ref` / provenance / `document_version` + `content_hash` /
報告対象期間 / 適格性ステータス を持つ。

**`changed_since_last` の決定論コンパレータ**:

| 条件 | 結果 |
|---|---|
| 前回評価が無い | `not_comparable`（理由 `no_prior_assessment`） |
| `requirement_text_hash` が変化 | `not_comparable`（理由 `requirement_changed`） |
| `service_scope_fingerprint` が不一致で**承認済み crosswalk が無い** | `not_comparable`（理由 `scope_changed`）。🔴 部分的な比較可能性を推測しない |
| 上記を満たし support_level が変化 | `improved` / `regressed` |
| 上記を満たし support_level 同一 | `unchanged` |

- 証跡が**再発行**された場合: `content_hash` が同一なら比較可。変化していれば
  「証跡が変わった」であって**自動的に改善/悪化ではない**（理由コードを添えて提示）。
- `changed_since_last` には**機械可読の理由コード**を必ず添える。
- **`prior_finding` の選択規則**: 当該 `control_id` について、cutoff 以前に**確定した最新**の評価を 1 件。
  同時刻の同点は `assessment_ref` の辞書順で決定（決定論的タイブレーク）。
- 再利用した source は**現行の統制要求・現行の証跡に対して必ず再リンク**する
  （前回の紐付けをそのまま流用しない）。

> 🔴 **Stage 4 の検証が保証する範囲を誇張しない**。
> registry 照合が証明するのは **「引用された source が実在し、span が原文の範囲内であること」**（ハンドル妥当性）
> であって、**「その証跡が当該統制要求を実際に支持していること」（意味的対応）ではない**。
> 意味的対応の最終確認は **HITL の人間レビュー**が担う。
> docs / 運用ガイドでも "claim→source 実在検証" と "意味的妥当性" を書き分け、
> 後者を機械保証として扱わない。

### Data Flow（outer backbone）

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (retry, max 3)
                                          pre_process
```

`route()` は override しない（framework 既定）。実機確認（2026-07-27）した挙動:
`hitl_status` が立ちかつ `hitl_feedback is not None` のときのみ HITL ルーティング
（REJECTED→finalize / APPROVED・CORRECTED→post_process）。通常時は `state["status"]` を
`AgentStatus(value)` で解決し success→post_process / retry（かつ `retry_count < max_retry`）→pre_process /
それ以外→finalize。**不正な status 文字列は `UnknownStatusError`** になるため、
必ず `AgentStatus.<X>.value`（小文字文字列）を返す。

### State Definition（`src/schemas/state.py` — `State(AgentState)`）

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `mode` | `str` | 実行モード（`ingest`/`invoke`/`feedback`）。`pre_process.execute()` が raw 入力から判定して返す | ✅ |
| `tenant_id` | `str` | テナント境界キー（全 DB/KB 検索を貫通）。未解決は fail-closed | ✅ |
| `org_id` | `str` | 同一金融機関内の組織境界キー。**tenant だけでは同一社内の他組織へ未公開の委託先情報が漏れる**ため両方必須 | ✅ |
| `case_ref` | `str` | 委託先案件の**参照 ID**（生の機微情報・証跡全文は保持しない）。`ingest` も案件スコープなので必須 | ✅(全モード) |
| `vendor_id` | `str` | 委託先の識別子（**入口 adapter が認証済みの委託先マスタから解決**・呼び出し元の自己申告は採らない）。同一ベンダーの過去評価を**案件をまたいで**引くためのキー | ✅(invoke/feedback) |
| `assessment_ref` | `str` | 生成された評価の不変スナップショット参照。`feedback` はこれを指して記録する | ✅(feedback) |
| `evidence_payload_ref` | `str` | 統制証跡**本文**の不透明参照（transient payload store のキー）。**英字のみ**で発行 | ✅(invoke) |
| `evidence_text_ref` | `str` | **サニタイズ済み証跡本文**の不透明参照（同じ transient payload store・同じ tenant/org/case スコープ束縛）。Stage 1 が採番し **Stage 3a が 1 回だけ dereference** する。🔴 `payload_store.resolve()` は single-use で Stage 1 が `evidence_payload_ref` を消費済みのため、Stage 3a へ「今回証跡」を渡す経路はこの second handle 以外に無い（§8）。**本文は State に載らない**ので checkpoint も汚さない | ✅(invoke) |
| `validated_input` | `dict` | S-2 通過済みの最小化入力（統制表 ID / 版・委託先類型・証跡の参照・mode） | ✅ |
| `source_registry` | `dict` | **決定論的な source ハンドル表**（Stage 1 が提出証跡から採番。`{source_id: {doc_ref, span_ref, provenance}}`）。LLM は既存ハンドルの**選択のみ**可・採番不可 | ✅(invoke) |
| `control_catalogue_ref` | `str` | 適用する承認済み統制表の**参照 ID**（1 版のみ。人間指定・`invoke` の入力） | ✅(invoke) |
| `catalogue_version` | `str` | 適用した統制表の**版**（監査記録の必須項目） | ✅(invoke) |
| `catalogue_content_hash` | `str` | 統制表の内容ハッシュ（同一版番号での差替え検出） | ✅(invoke) |
| `perspective_pack_ref` | `str` | 適用する**観点表**の参照 ID（統制表と同格の承認済み入力。KB からの想起ではない） | ✅(invoke) |
| `perspective_pack_version` | `str` | 観点表の版 | ✅(invoke) |
| `perspective_pack_content_hash` | `str` | 観点表の内容ハッシュ | ✅(invoke) |
| `mapping_pack_version` | `str` | フレームワーク対応表（SIG ↔ FISC ↔ SOC 2 等）の版 | —(任意) |
| `vendor_type` | `str` | 委託先類型（**人間が指定**。AG は推定しない） | ✅(invoke) |
| `docs_present` | `dict` | Stage 1 の証跡有無結果 | — |
| `format_check` | `dict` | Stage 1 の書式検証結果 | — |
| `prior_findings` | `list` | Stage 2 が引いた同一ベンダーの過去評価。統制要求ごとに `{support_level, claim, verification_questions, evidence_source_ids, as_of:{catalogue_version, catalogue_content_hash, requirement_text_hash, assessed_at, service_scope_fingerprint, document_versions[]}}`。🔴 **Stage 3a には渡さない**（3b のみ） | — |
| `current_evidence_basis` | `dict` | 統制要求ごとの今回判定の裏付け（`{control_id, source_ids[], spans[], observation}`）。`undetermined` 以外では**必須**。Stage 4 が適格性を決定論検証 | ✅(invoke・undetermined 以外) |
| `changed_since_last` | `dict` | 統制要求ごとの前回比（`improved`/`regressed`/`unchanged`/`not_comparable`）＋**機械可読の理由コード**。再評価ユースケースの主たる価値 | — |
| `case_evidence_hits` | `list` | Stage 2 が引いた当該案件証跡の検索結果（source-linked） | — |
| `evidence_brief` | `dict` | 証拠ブリーフのドラフト（下記出力契約） | — |
| `evidence_map` | `dict` | claim→source 対応マップ（Stage 4 が `source_registry` から構築） | — |
| `gaps` | `list` | 不足論点 | — |
| `contradictions` | `list` | 矛盾論点 | — |
| `requires_human_review` | `bool` | 人間レビュー要否 | — |
| `degradation_reason` | `str` | 縮退理由（KB 空振り・観点表不在・LLM 不在等）。各 stage は既存理由を上書きせず、単一 `str` のまま `"+"` 結合して envelope まで運ぶ | — |
| `ingest_result` | `dict` | `mode=ingest` の蓄積結果（件数・品質フラグ） | —(ingest) |
| `feedback_result` | `dict` | `mode=feedback` の記録結果（`assessment_ref` / 監査イベント ID / 候補キューに積んだ件数）。🔴 **「観点表を更新した」ことは意味しない**（昇格は AG 外の人間作業） | —(feedback) |
| `formatted_output` | `dict` | **外部に出る envelope**（`get_output()` が拾う唯一のカスタム経路） | ✅ |
| `result` | `dict` | S-3 通過済みの整形出力（`formatted_output` のフォールバック） | — |
| `status` | `str` | outer routing 用。`AgentStatus.<X>.value`（`"success"`/`"retry"`/`"error"`）。bare Enum 禁止 | ✅ |

**出力契約 — closed schema は 2 層ある（混同しない）:**

S-3 は **envelope 全体**に掛かるので、`evidence_brief` の 7 キーだけを閉じても不十分。
**2 つの closed schema を別々に定義**する（どちらも全階層 `additionalProperties: false`）。

**層 1 — `evidence_brief`（ちょうど 7 キー・完全一致）:**

`control_requirements`（統制要求）/ `evidence_sources`（source ID）/ `gaps`（不足論点）/
`contradictions`（矛盾）/ `verification_questions`（確認質問）/ `prior_finding`（前回評価・`as_of` 必須）/
`changed_since_last`（前回比・理由コード付き）

**層 2 — `formatted_output`（`invoke` モードの envelope・列挙されたキーのみ）:**

`evidence_brief`（**層 1 の schema に適合**）/ `evidence_map` / `current_evidence_basis` /
`applied_pins` / `assessment_ref` / `requires_human_review` / `degradation_reason` / `mode`

🔴 **`prior_finding` / `changed_since_last` を envelope のトップレベルに重複させない**
（`evidence_brief` の中の値が唯一の正）。`merge_output()` もこの 2 層に沿って明示マッピングする。
未宣言キーは S-3 で**全 reject**（どちらの層でも）。

`regulatory_compliance_verdict` / `vendor_selection` / `materiality_verdict` / `contract_adequacy` に
相当するフィールドは **schema に持たない**（構造的排除）。

> 🔴 **denylist は持たない**（関連するテンプレートの比較でも同じ結論に至っている）。
> 「規制適合である」等の自由文断定を禁止フレーズ正規表現で叩く denylist は**言い換えで必ずすり抜ける**うえ、
> 「網羅している」という誤った安心（false assurance）を生むため**実装しない**。
> 非結論性は**構造**で担保する: ①closed schema（未宣言キー全 reject）②禁止キーの再帰排除
> ③`support_level` を `supported` / `insufficient` / `contradicted` / `undetermined` の**候補粒度に固定**
> （`compliant` 等の確定値を作れない）④claim→source 実在検証 ⑤HITL 人間レビュー。
> 残余リスク（自由文で断定的に読める文が出る可能性）は運用ガイドに
> **「lexicon による保証はしない・最終判断は人間」**と正直に記載する。

> 🔴 **allowlist は「再帰的」かつ「外部に出る全経路」に掛ける**。
> トップレベル 5 キーの一致だけでは不十分 — **ネストしたオブジェクトの中に
> `assessment` / `recommendation` のような自由キーを作れば結論を載せられる**。
> 適用範囲を次のとおり明示する:
> - **モードごとに 1 つの再帰 JSON schema** を定義し、**全オブジェクト階層で `additionalProperties: false`**、
>   配列要素も型・キーを閉じる。
> - 検証対象は `evidence_brief` 単体ではなく、**外部に出る `formatted_output` envelope の全体**。
>   `result` は `formatted_output` と**同一内容**にする（別物にすると片方が未検証で漏れる）。
> - **`evidence_map` / `ingest_result` / `feedback_result` も同じ closed schema 配下**に置く。
> - 🔴 **HITL の `interrupt()` payload も検証対象**。post_process の S-3 は interrupt より**後**に走るため、
>   **draft を人間に見せる前に S-3 と同じ closed schema を通す**（checkpoint に載る値・
>   `hitl_metadata` として外部に返る値の両方）。ここを塞がないと「S-3 を通らない出力経路」が残る。
> - `source_registry` の `doc_ref` / `span_ref` は**内部専用**。外部にはサーバ側で採番した
>   **不透明な source ID のみ**を出す（ファイルパス・署名 URL・原文抜粋を漏らさない）。

**State Constraints (mandatory):**
- Flat TypedDict のみ（primitives + JSON-serializable）
- JWT / API keys / credentials を State に置かない（checkpoint DB 漏洩）
- **生の統制証跡本文・委託先の機微な商流情報を State に置かない**（`evidence_payload_ref` / `evidence_text_ref` の参照のみ）
- 🔴 **Stage 3a が証跡本文を読むようになった以降（2026-08-19）は、モデルが本文を `claim` /
  `observation` へ写して State に還流させる経路が生じる**（証跡の provenance は
  `vendor_submission`＝非信頼なので、文書自身が写経を指示することもありうる）。Stage 3a は
  草案を**受け取る時点**で自由記述の長さと source からの逐語連続長を検査し、越えたものは
  `llm_draft_rejected` として決定論草案へ差し戻す（切り詰めない — 半端な文は主張を変えるため）
- 逐語窓（既定 60 文字）以下の短い source は**全文を保護対象**にする。したがって、その全文を
  `claim` / `observation` として持ち出さなければ説明できない短い証跡からは、非縮退の評価を生成できない
  （`llm_draft_rejected` に倒す）。これは短文だけガードをすり抜ける逆転を防ぐ意図した fail-closed 挙動である
- InvocationContext は `config["configurable"]` 経由のみ（State に置かない）
- Pydantic / dataclass / 任意 Python オブジェクト禁止（msgpack 非互換）

## Framework Utilization

### Shared Components Used

- [x] **InvocationContext** — ノード内では `InvocationContext.from_state(state)` で取得（直接構築しない）。
      State には**格納しない**。
      🔴 **実機確認（2026-07-27）: `InvocationContext` の実フィールドは
      `correlation_id / session_id / thread_id / parent_trace_id / caller_id / caller_trust_level /
      secrets / hitl_allowed` の 8 つのみ。`tenant_id` / `org_id` は存在しない。**
- [x] **テナント／組織スコープの伝播シーム = `BaseGraph.invoke(..., input_context: dict)`**
      🔴 実機確認（2026-07-27）: `invoke()` は
      `invoke(user_input, session_id="", ctx=None, input_context: dict | None = None)` の
      シグネチャを持ち、`input_context` を **`initial_state["input_context"]` にそのまま載せる**。
      これが**サーバ側が決めた値をノードに渡す唯一の信頼できる経路**である。
      → **入口 adapter（`src/api/server.py`）が認証結果から `{tenant_id, org_id, entry_mode}` を確定し、
      `input_context` として渡す**。`pre_process.execute()` は `state["input_context"]` から読み、
      **リクエストボディの自己申告は採用しない**。`ctx.tenant_id` を参照する実装は書かない。
- [x] `ctx.secrets.require("KEY")` で secrets 取得（`os.environ` 禁止・入口 caller-auth token を除く）
- [x] `SecurityViolationError`（S-2/S-3 違反時）
- [x] **S-2**: `PreProcessNode._extra_security_gate_input()` — **raw `user_input` + invocation config のみを読む**
      （`execute()` が書く `tenant_id`/`org_id`/`mode`/`case_ref` に依存しない＝ゲートは execute の**前**に走るため）。
      チェック: 入力サイズ制限・mode allowlist・統制表参照の形式検証・PII 追加スキャン。
      **`_security_gate_input()` は override しない**（`@final`）。
- [x] **S-3**: `PostProcessNode._extra_security_gate_output()` — **closed allowlist 検査**。
      引数は 🔴 **`result: dict`（state ではない）**（実機確認）。
      検査: 出力キーの完全一致 allowlist・禁止キーの再帰排除・全論点の source link 必須・
      claim→source 対応・`support_level` の候補粒度固定・機微再走査。
      **`_security_gate_output()` は override しない**（`@final`）。
- [x] **S-4**: 各 `execute()` 内で `emit_trace_event()` を最低 1 回
      （`node_start`/`node_complete`/`node_error` は framework が emit するので**重複させない**）。
      import は `from shared.utils.audit_logger import emit_trace_event`。
- [x] **`OutsourcingBriefGraphNode`（GraphNode）の S-2/S-3**: 内側 graph / 子ノードでゲート適用済みのため、
      outer の GraphNode 側は deliberate no-op passthrough（ADR-017）。

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass（PreProcessNode / PostProcessNode / 内側 Stage 各ノード）→ framework `@final` gate が
>   自動実行。`_extra_*` フックで拡張のみ
> - `GraphNode`（OutsourcingBriefGraphNode）→ deliberate no-op（内側ノードのゲートが適用済み）

### 🔴 fail-closed の実効化 — `status="error"` だけでは main を止められない

**実機確認（2026-07-27）**: `AgentBaseGraph.add_edges()` は
`pre_process → main` を**無条件辺**として張る（`route()` が走るのは **main の後**）。
したがって `pre_process.execute()` が `status="error"` を返しても、**`main`（＝ GraphNode）は実行される**。
スコープ未解決を「pre_process で error を返す」だけに頼る設計は**実効的な fail-closed ではない**。

多層で塞ぐ:

| 層 | 実装 | 効果 |
|---|---|---|
| ① 入口 adapter | 認証結果から `{tenant_id, org_id, entry_mode}` を確定し `input_context` で渡す。未認証は HTTP 403 | グラフに入る前に遮断 |
| ② `PreProcessNode._extra_security_gate_input()` | `state["input_context"]` に `tenant_id` / `org_id` が**無ければ `SecurityViolationError`** を送出（gate は execute の前に走るので、execute が書く値には依存しない） | ノード実行前に例外で停止 |
| ③ `OutsourcingBriefGraphNode._extra_security_gate_input()` | 🔴 **no-op passthrough にしない**。`tenant_id` / `org_id` 不在なら例外で停止 | 無条件辺で到達しても main を実行させない |
| ④ inner `InputDecodeNode` | 必須キー欠落を fail-closed | subgraph 内での二重確認 |
| ⑤ サービス層 | DB / KB / payload store の**全アクセスが両キーを必須引数**に取る（省略可能にしない） | データ到達点での最終防壁 |

> ⚠️ 上表 ③ は、§Composition の「GraphNode の S-2/S-3 は deliberate no-op」という
> 一般則に対する**本テンプレ固有の例外**である。no-op は「子ノードでゲート済み」を前提にするが、
> スコープ境界は**子に入る前**に確定していなければならないため、GraphNode 側でも検査する。

> 🔴 **S-2 既定 PII スキャンとの相互作用（実機確認 2026-07-27）**:
> `FunctionNode._security_gate_input()` は `_PII_SCAN_FIELDS = ('user_input', 'validated_input', 'llm_response')`
> を走査し、検出スパンを **`[MASKED]` に破壊的置換**する（例外ではない）。
>
> 🔴 **順序の罠**: S-2 は **`execute()` より前**に走る。したがって
> 「pre_process の `execute()` が生の証跡本文を payload store に預ける」設計は**成立しない** —
> `execute()` に届く時点で `user_input` は既にマスク済みで、**store に `[MASKED]` が保存される**。
> → **証跡本文の預け入れは入口 adapter（グラフ呼び出しより前）で行う**。
> グラフには**最初から不透明 ref と非機微メタデータしか渡さない**。
> `pre_process` は ref の形式と**サーバ側スコープの照合のみ**を行い、生本文は決して受け取らない。
> ①`user_input` / `validated_input` に**生の証跡本文を載せない**
> ②参照 ID に **`uuid4()` を使わない**（数字列が PII 誤検知され ref が `[MASKED]` に破壊される既知欠陥）。
> **暗号論的乱数から生成した英字のみの ref** を発行する（parts:payload-store v0.4.0 の方式）。

### substrate の REAL / STUB 判別（🔴 実機確認・再実装防止に直結）

> ⚠️ **訂正記録**: 初版の設計書は「`shared.services.*` はすべて空シム」と書いていたが、これは
> **パッケージの `__init__.py`（2 行）だけを見た誤り**。実体は**サブパッケージ**であり、
> REAL なものと STUB なものが混在する。REAL な機能を再実装しないため、
> この判別は設計時に確定させる必要がある（2026-07-27 実機再確認）。

| 機能 | SDK 実体（`agenticstar-agentcore==1.0.0` で確認） | 判定 | 本テンプレの対応 |
|---|---|---|---|
| LLM 呼び出し | `shared.services.llm.base_llm.BaseLLM`（59 行・`NotImplementedError` なし）＋ `AnthropicClient` / `OpenAIClient` / `BedrockClient` | **REAL** | 🔴 **自前 LLM クライアントを書かない**。`config["llm"]` に `BaseLLM` 実装を**注入**し、`src/services/llm_client.py` は**注入シームのみ**（モデル ID / 具体クライアントを `src/` に書かない） |
| credential / PII scan | `FunctionNode` が既定で自動実行 | **REAL** | 🔴 **自前の正規表現を書かない**。ドメイン検査は `_extra_*` フックのみ |
| DB アクセス | `shared.services.database.postgres_client` = 23 行・`NotImplementedError` ×3・STUB マーカーあり（`base_db.BaseDatabase` は ABC） | **STUB** | 再利用対象外。`src/services/kb_service.py` に自前実装（scope guard / ID 生成 / safe projection）。`BaseDatabase` の形に合わせる |
| ベクトル検索 | `vector_store.pgvector_store` = 21 行・`NotImplementedError` ×3 | **STUB** | 同上（`scoped-retrieval` パターン適用） |
| embedding | `embedding.openai_embedding` = 18 行・`NotImplementedError` ×2 | **STUB** | 必要になった時点で自前実装 |
| memory | `memory.redis_memory` = 21 行・`NotImplementedError` ×3 | **STUB** | checkpointer は下記 §HITL 設計のとおり別途調達 |
| secrets | `shared.secrets.factory` | **REAL** | `src/api/server.py` で使用 |
| audit | `shared.utils.audit_logger.emit_trace_event` | **REAL** | 各ノードで使用 |
| `framework.llm` / `framework.security` | **モジュールごと不在**（`ModuleNotFoundError`） | — | これらを import しない（LLM は `shared.services.llm`、scan は `FunctionNode` 既定） |

> **LLM 必須性と縮退の整合**: `agent.yaml` の `requires.secrets` に
> `LLM_API_KEY` を宣言する＝**通常構成では LLM 必須**。deterministic fallback は
> 「LLM 障害時に業務を止めない」ための縮退であり、**縮退時は意味的な支持判定を出さない**
> （`support_level` は全件 `undetermined`、Stage 1 の構造的 gap のみ返す）。
> 「LLM 無しでも支持/矛盾を判定する」ことは**しない**。

### Composition Pattern

- **Pattern**: **GraphNode (subgraph)** — main スロットに `OutsourcingBriefGraphNode` を割り当て、
  内側 `OutsourcingBriefWorkflowGraph`（`src/graph/domain_workflow_graph.py`）を呼ぶ
- **Composition target**: `OutsourcingBriefWorkflowGraph`（inner・🔴 **`BaseGraph` 継承**）
  > **なぜ inner は `AgentBaseGraph` ではないか（実機確認 2026-07-27）**:
  > `AgentBaseGraph.register_nodes()` は `initialize / pre_process / main / post_process / finalize` の
  > **固定 5 スロットを強制**し、`add_edges()` も `START→initialize→pre_process→main→{route}→post_process→
  > finalize→END` を固定で張る。本テンプレの inner は **mode 3 分岐 + Stage 1〜4 の直列**という別トポロジが
  > 必要なので、inner を `AgentBaseGraph` にすると `add_edges()` を override せざるを得ず「override 禁止」と
  > 自己矛盾する。→ inner は **`BaseGraph` を直接継承**し、`register_nodes()` / `add_edges()` / `route()` /
  > `get_output()` / `state_schema` / `name` / `_validate_config()`（= `BaseGraph` の 7 つの abstract）を
  > 自前で実装する。**outer だけが `AgentBaseGraph`**（L1 backbone 準拠）。
  > `GraphNode.execute()` は inner に `resume()` を要求するため、`BaseGraph` 継承は必須条件を満たす。
- **inner の入力デコード**: `GraphNode` は inner に**単一 str**しか渡せないため、inner の先頭に
  **`InputDecodeNode`（strict JSON デコード + 必須キー検証）** を置く。
  デコード失敗・必須キー（`tenant_id` / `org_id` / `mode`）欠落は **fail-closed**（`status="error"`）。
- **Error propagation strategy**: **propagate**（内側エラーは `SubgraphError` として再送出・fail fast）
- **GraphNode 契約**— 3 つとも `@abstractmethod`（実機確認）:
  - `get_subgraph()` → 内側 graph を返す。**`self._subgraph = None` → 初回生成後キャッシュ**
    （毎 `execute` で新規生成すると HITL resume が無限ループする）
  - `extract_input()` → 🔴 inner の `invoke()` は**単一 str 入力**（実機確認）。
    **最小マップを JSON 直列化して渡す**:
    `{mode, tenant_id, org_id, case_ref, vendor_id, vendor_type, evidence_payload_ref, validated_input,
      pins: {catalogue_ref, catalogue_version, catalogue_content_hash,
             perspective_pack_ref, perspective_pack_version, perspective_pack_content_hash,
             mapping_pack_version, mapping_pack_content_hash},
      sealed_content: {catalogue_sealed_ref, perspective_pack_sealed_ref}}`。
    🔴 **pin だけでは評価できない**（ハッシュは中身ではない）。入口 adapter が正本から**実体を解決・検証**し、
    その**不変シール参照**を併せて渡す（下記 §統制パックの解決）。
    テナント/組織分離を構造保証にするため `tenant_id` と `org_id` を必ず含め、内側ノードは不在なら fail-closed。
    （`correlation_id`/`session_id`/`caller_trust_level`/`hitl_allowed` は framework が自動伝播）
  - `merge_output()` → **mode 別に明示マッピング**（raw pass-through しない・None 埋めしない）:
    - `invoke`: `evidence_brief`（層 1 適合）/ `evidence_map` / `current_evidence_basis` / `changed_since_last` / `requires_human_review` / `status`
    - `ingest`: `ingest_result` / `status`
    - `feedback`: `feedback_result` / `status`
    - `error_log` は非空時のみ。`node_history` は返さない（framework の reducer 管理）
- **propagate_hitl**: 🔴 **`True` を明示宣言**（既定は `False`。実機確認: `execute()` は
  `self.propagate_hitl and state.get("hitl_allowed", True)` で inner の `hitl_allowed` を計算するため、
  既定のままだと Stage 4 の `interrupt()` が outer に伝播しない）
- **`required_trust_level`**: `VERIFIED_EXTERNAL` を**明示宣言**（GraphNode 既定の ANONYMOUS のままだと
  内側 S-1 と不整合になる既知バグ）。
  🔴 **`agent.yaml` の宣言はノードの S-1 ゲートの代わりにならない**。
  S-1 を強制するのは `BaseNode.__call__()` が読む**クラス属性**なので、
  **`PreProcessNode` / `OutsourcingBriefGraphNode` / `PostProcessNode` および
  スコープ付きデータを読む inner の全 `FunctionNode`（Stage 1〜4・Ingest・Feedback）に
  `required_trust_level = TrustLevel.VERIFIED_EXTERNAL` を個別に明示宣言する**
  （暗黙の ANONYMOUS 継承は不可）。

> **post_process の mode 別契約**: `invoke` では `evidence_brief`/`evidence_map` を S-3 検査して
> `formatted_output` へ envelope 化、`ingest`/`feedback` では `ingest_result`/`feedback_result` を要約して
> `formatted_output` へ。**invoke 用フィールドが無い mode で missing-field エラーにしない**（mode で分岐）。
> `degradation_reason` も envelope に畳み込む（`get_output()` が透過しないため）。

## HITL 設計

- **`hitl.enabled: true`**（`config/config.yaml`）+ **`memory_enabled: true`**。
- 🔴 **checkpointer は config フラグでは生えない（実機確認 2026-07-27）**:
  `AgentBaseGraph._validate_config()` は `hitl` / `memory_enabled` の**型検査しかせず**、
  「HITL 有効なのに checkpointer 無し」で `ConfigError` を出さない。
  `compile(checkpointer=None)` が既定であり、**saver はテンプレが明示的に渡さないと存在しない**。
  （`invoke()` は interrupt 発火時に checkpointer 不在を検出して `RuntimeError` を投げる＝
  失敗は実行時であって compile 時ではない。）
  → **`src/api/server.py` が永続 saver を構築し、outer / inner の各 `compile(checkpointer=...)` に
  別インスタンスとして渡す**。saver の実体・ライフサイクル・プロセス再起動をまたぐ永続性は
  実装時に確定し、`/invoke → /resume` を**プロセス再起動を挟んで**テストする。
  `shared.services.memory.*` の具象は STUB なので**そのままでは使えない**（自前調達が要る）。
- Stage 4 `EvidenceVerifyNode.execute()` で、claim→source 対応の信頼度が閾値未満、または
  `source_registry` に実在しない source ID を LLM が付けた場合に
  **`state.get("hitl_allowed", True)` ガード付きで `interrupt()`**（unconditional interrupt は
  blocking defect になる）。
- `hitl_draft` に draft を退避して**resume 時の LLM 再実行を防ぐ**。
- **通常の `/invoke` は `hitl_allowed=True`**（入口 adapter の既定）＝ Stage 4 の HITL が実際に発火し、
  `propagate_hitl: True` により outer へ伝播する（PB-7 が検証する経路）。
- `hitl_allowed=False` は**バッチ/バックグラウンド実行や、親 `GraphNode` から呼ばれる合成時のみ**。
  その場合は interrupt を skip して継続する（自動パイプラインをデッドロックさせない）。
  🔴 **ただし「`requires_human_review=True` を立てるだけ」は承認バイパスである**。
  下流の呼び出し元がその boolean を無視すれば、レビュー未了のブリーフをそのまま使えてしまう
  ＝**技術的保証になっていない**。→ この経路では **`formatted_output` を
  `draft_pending_review` 専用の closed envelope**（`support_level` は全件 `undetermined` に落とし、
  確定ブリーフと**同じ形を返さない**）にする。確定形の成果物は
  **レビュー完了イベントが記録されるまで生成しない**。
  さらに **`hitl_allowed` と `mode` をリクエストボディから選ばせない**（入口 adapter が決める）。
- `HitlStatus` **3 値（APPROVED / CORRECTED / REJECTED）を全て処理**する
  （`CORRECTED` を無視すると訂正分岐が黙って skip される）。
  `CORRECTED`（人間の訂正）にも **claim→source 再検証と PII 再スキャンを強制**する
  （「人間が直したから信頼できる」という油断を作らない）。
- 🔴 **framework が自動で書く HITL フィールドは限られる（実機確認 2026-07-27）**:
  `BaseGraph._build_awaiting_human_output()` が書くのは **`hitl_metadata` / `hitl_draft`
  （payload に `draft` がある場合）/ `subgraph_thread_id`** のみ。
  **`resume()` は `hitl_status` を設定せず、`hitl_count` も増やさない。**
  → `route()` の HITL 分岐（`hitl_status` + `hitl_feedback` を見る）は、**誰かが `hitl_status` を
  書かない限り発火しない**。本テンプレは Stage 4（inner）で interrupt するため、
  **inner の resume 直後のノードが `HitlStatus.from_feedback(feedback)`（framework 提供の分類ヘルパー）で
  status を判定し、有界カウンタを自前で進め、REJECTED なら打ち切る**という
  **ドメイン側のレビュー状態機械**を実装する。
  「framework が状態遷移を管理してくれる」という前提では**設計しない**。
- テンプレが書いてよい HITL フィールドは **`hitl_draft` のみ**（`hitl_count` / `hitl_allowed` は書かない）。
  上記の状態機械は **outer の予約フィールドを書き換えるのではなく、inner の自スキーマ上の
  専用フィールド**（例: `review_round` / `review_outcome`）で管理し、outer へは
  `merge_output()` で明示マッピングする。これで「予約フィールド禁止」と両立させる。
- **inner / outer で別 checkpointer インスタンス**を明示 `compile(checkpointer=)` する
  （同一 saver 共有は checkpoint 相互汚染 → resume 非収束）。
- **PB-7（HITL interrupt propagation）が必須**になる（`hitl.enabled: true` のため）。
- `/resume` エンドポイントは `/invoke` と**同等の入口認証**＋**checkpoint 所有権照合**を行う
  （`resume(thread_id, feedback)` は ctx を受け取らないため、所有権検証は入口でしかできない）。
  foreign と unknown は**同じ 403** にして checkpoint の存在を秘匿する。

## config 設計

### `config/agent.yaml`（静的マニフェスト）

| Key | 値 | 備考 |
|---|---|---|
| `agent.id` | `FIN-C2-177` | |
| `agent.name` | `FinancialOutsourcingDueDiligenceEvidenceBriefAgent` | |
| `agent.category` | `Cat 2` | gate-cat-consistency が README と照合 |
| `agent.industry` | `FIN` | |
| `agent.base_type` | `AgentBaseGraph` | outer graph の基底型 |
| `agent.module` | `src.graph.graph` | |
| `agent.class` | `OutsourcingDueDiligenceBriefAgent` | 実クラス名 |
| `agent.required_trust_level` | `VERIFIED_EXTERNAL` | |
| `requires.secrets` | LLM / DB 接続に必要なキー | `ctx.secrets.require()` で取得 |

> ⚠️ **runtime パラメータを `agent.yaml` に置かない**（マニフェスト契約の誤用）。

### `config/config.yaml`（runtime パラメータ・**新規作成**）

| Key | 設計値 | 根拠 |
|---|---|---|
| `max_retry` | `3` | framework 既定に合わせる（`_validate_config()` が範囲検査する） |
| `timeout_s` | `60` | 多様式証跡の照合で LLM 往復が長い。🔴 **framework は `timeout_s` を強制しない**（`_validate_config()` の検査対象外・実機確認）。**LLM クライアント呼び出し側でこの値を適用する責務がテンプレにある**（実装時に適用箇所とテストを明示） |
| `memory_enabled` | `true` | **HITL 必須要件**（checkpointer） |
| `hitl.enabled` | `true` | proposal の HITL Gate = Yes |
| `hitl.max_hitl` | `8` | 統制要求ごとの差戻しを想定した壁打ち回数 |
| `evidence_confidence_threshold` | `0.8` | Stage 4 の HITL 発火閾値 |
| `kb_top_k` | `8` | 長文の統制証跡・同一ベンダー過去評価の参照を拾うため多め |
| `llm_temperature` | `0.0` | 金融規制文脈・事実照合のため決定論寄り |
| `payload_ttl_s` | `900` | transient payload store の TTL |
| `max_evidence_documents` | `20` | Stage 1 が dereference 後に許可する文書数。超過時は切り詰めず fail-closed |
| `max_aggregate_evidence_chars` | `400000` | Stage 1 が許可する全文書の合計文字数（概ね 100k tokens 相当の上限）。超過時は切り詰めず fail-closed |

## 適用部品一覧（parts registry）

以下の再利用可能なユニットを適用し、テンプレート固有の adaptation points を埋める。

| part-id | version | 適用先 | adaptation_points の埋め方 |
|---|---|---|---|
| `cat2-graph-skeleton` | 最新 | `src/nodes/main_node.py` / `src/graph/domain_workflow_graph.py` | mode = `ingest`/`invoke`/`feedback`。inner の 3 分岐と Stage 1〜4 の直列を本テンプレ仕様で配線。lazy/cache subgraph・inner checkpointer 分離・HITL propagate を有効化 |
| `output-envelope` | 最新 | `src/nodes/post_process_node.py` | `formatted_output` を §出力契約の**層 2 の列挙キー**で畳み込む（`prior_finding` / `changed_since_last` は `evidence_brief` の中が唯一の正・トップレベルに重複させない）。🔴 **加えて `applied_pins`（`catalogue_version` / `perspective_pack_version` / `mapping_pack_version` と各 `content_hash`）と `assessment_ref` を必ず含める**（INV-9 の実効化。これが無いと「どの版で評価したか」が外部に残らない） |
| `payload-store` | v0.4.0 以上 | `src/services/` + `src/api/server.py` | 統制証跡本文を transient store に預け State は `evidence_payload_ref` のみ。**預け入れは入口 adapter**（S-2 マスクより前）。🔴 **ref は暗号論的乱数の英字のみ**（uuid4 は S-2 PII ゲートに破壊される）。`scope_keys=("tenant_id", "org_id", "case_ref")`。dereference のたびにサーバ側でスコープ authorization・TTL 経過で削除・single-use（replay 防止） |
| `ingest-file-sanitizer` | 最新 | `src/services/` | SOC 報告 / BCP / 契約書の**マジックバイト判定**・未知形式 reject・OCR 崩れの品質ゲート（拡張子や Content-Type の自己申告を信用しない） |
| `scoped-retrieval` | 最新 | `src/services/kb_service.py` | `tenant_id` **と** `org_id` の両方で fail-closed。🔴 検索対象は**当該案件の証跡と同一ベンダーの過去評価に限定**（統制表・観点表は検索しない＝入力で渡る）。safe projection（source-linked・provenance のみ）。`top_k` は config |
| `evidence-gate` | 最新 | `src/nodes/post_process_node.py`（S-3） | `source_registry` に対する引用 ID 実在照合・捏造 ID 検出・不一致時 fail-closed |
| `llm-draft-stage` | 最新 | 内側 Stage 3a / 3b | `config["llm"]` 注入シーム・deterministic fallback・`degradation_reason`・evidence hook |

> **`scope_keys` の適用可否**: `scope_keys` は「マルチテナント分離」用ではなく
> 「1 顧客 KB 内の混ぜてはいけない区画」用。本テンプレは**同一金融機関内の組織境界（`org_id`）**という
> 実在の区画があるため **適用する**。
> KB 検索の区画キーは `("org_id",)`、**payload store の binding は `("tenant_id", "org_id", "case_ref")`**
> （payload は「区画」ではなく**個別の機微実体へのケイパビリティ**なので、テナントを含む完全束縛にする。
> `org_id` だけの束縛では別テナントの同名 org で参照が通り得る）。
> テナント分離自体は KB 接続レベル（認証 caller_id → 認可顧客 → provision 済み接続）で担保し、
> 入力 `tenant_id` は**照合のみ**に使う（default KB を持たない）。

## スコープ固定境界（testable invariant）

スコープ境界を**機械検証可能な不変条件**に落とす。受入テストは `docs/03_test_spec.md` に記載する。

| # | 不変条件 | 検証方法 |
|---|---|---|
| INV-1 | 適用される統制表は **1 版のみ**。`control_catalogue_ref` が単一値でなければ reject | pre_process の入力検証 + 単体テスト |
| INV-2 | 委託先類型（`vendor_type`）は**人間が指定**。AG は推定しない | LLM プロンプトに類型を推定させる指示を含めない + 出力 schema に類型判定フィールドを持たない |
| INV-3 | 出力に `regulatory_compliance_verdict` / `vendor_selection` / `materiality_verdict` / `contract_adequacy` 相当のキーが**構造的に存在しない** | S-3 closed schema **2 層**（層 1 `evidence_brief` = 7 キー完全一致 / 層 2 `formatted_output` = 列挙キーのみ）+ 全階層 `additionalProperties: false` + 禁止キー再帰排除テスト |
| INV-4 | `support_level` は `supported` / `insufficient` / `contradicted` / `undetermined` の**候補粒度のみ**（`compliant` 等の確定値を作れない） | schema 検証 + S-3 |
| INV-5 | 全論点に source link 必須。`source_registry` に**実在しない** source ID は reject | Stage 4 決定論検証 + fail-closed テスト |
| INV-6 | `tenant_id` **または** `org_id` 不在で DB/KB に到達しない（fail-closed） | 永続層に対する PB テスト（テナント A → B の不可視性・組織 A → B の不可視性） |
| INV-7 | 生の統制証跡本文が checkpoint に残らない | PB テスト（3 モードで checkpoint 全直列化へのセンチネル not-in を assert） |
| INV-8 | **統制表・観点表は `invoke` の入力であり、`ingest` では受け付けない** | `/ingest` の**リクエストスキーマ allowlist**（宣言外フィールド reject）＋ 取り込み元 provenance が「委託先提出物」であることの検証。※呼び出し元が自己申告する種別ラベルに依存しない |
| INV-9 | **適用した版が出力とレビュー記録に必ず残る** — `catalogue_version` / `perspective_pack_version` と各 `content_hash` が欠落したら `invoke` を fail-closed | pre_process の入力検証 + envelope に版情報が含まれることの assert |
| INV-10 | **`feedback` は現行の観点表を書き換えない** | ①`FeedbackNode` の書込先が `feedback_audit_events` / `perspective_candidates` に限られる ②🔴 **観点表ストアへは最小権限で read-only 資格情報しか持たない**（書込パス不在をコードで示すだけでなく権限で担保） |
| INV-11 | **他ベンダーの提出証跡が検索結果に混入しない** | Stage 2 の 2 述語がそれぞれ `case_ref` スコープ / `vendor_id` スコープで閉じることの PB テスト（ベンダー A → B の不可視性・案件 X → Y の証跡不可視性） |
| INV-12 | **`undetermined` 以外の今回判定には、今回 invocation の適格な証跡に紐づく `current_evidence_basis` がちょうど 1 件存在し、`undetermined` には存在しない**（※これは「意味的に支持している」証明ではない — 意味的妥当性は HITL が担保） | Stage 4 + S-3 の決定論検証。reject されることをテスト: ①前回 assessment の source ID を流用 ②時点・スコープ不適格な source ③他 control に紐づく source の流用 ④span 欠落・範囲外 ⑤basis 欠落 ⑥`undetermined` / orphan / duplicate basis ⑦ invocation 全体で 100 span 超過 |
| INV-16 | **前回評価がある統制で `undetermined` 以外を出す場合は HITL 必須** | `prior_finding` あり × 今回 `supported` の入力で `requires_human_review` が立ち HITL 経路に入ることのテスト |
| INV-17 | **Stage 3b は Stage 3a の判定を書き換えられない** | 3b の出力に `support_level` / `claim` が含まれないこと、3a→3b 前後で当該値が不変であることのテスト |
| INV-14 | **`prior_finding` が今回証跡と混ざらない** | 出力 schema 上 `prior_finding` は `evidence_sources` と別フィールドで、`as_of`(統制版・評価日・サービス範囲) が必須。テスト: `as_of` 欠落で reject |
| INV-15 | **統制版が変わった統制は前回結論を根拠に使わない** | 前回と今回で当該統制の requirement 本文が変化していれば `changed_since_last = not_comparable` になることのテスト |
| INV-13 | **偽造・失効した pin で評価できない** | resolver テスト: 正本に存在しない `ref`/`version`、hash 不一致、未承認 / 有効期間外 のそれぞれで fail-closed |

## 実装時の受入条件

以下の条件を実装と回帰テストで満たすこと。

| # | 項目 | 受入条件 |
|---|---|---|
| 1 | `src/graph/graph.py` のクラス実装 | `agent.yaml` の `class: OutsourcingDueDiligenceBriefAgent` と**実クラス名を一致**させ、`module: src.graph.graph` で解決できること |
| 2 | `config/config.yaml` の読み込み配線 | runtime config を読み込んで `config=` に渡し、`compile(checkpointer=...)` を呼ぶ |
| 3 | **PB-7 を実テストにする** | 🔴 現状 PB-7 は `pytest.skip()` の TODO プレースホルダのままで、**HITL 有効なのに 2 件 skip する**（緑でも何も証明していない）。`EvidenceVerifyNode` の `interrupt()` が `GraphInterrupt` として伝播し、`status` が `error` にならないことを**実 compiled graph**で検証する。`hitl_allowed=False` 経路の抑止も検証 |
| 4 | PB-7 設定の回帰ケース | **runtime-config のみで有効化した場合 / 静的設定と矛盾する場合 / YAML が壊れている場合**を検証する。壊れた YAML は「HITL 無し」と黙って解釈せず**失敗させる** |
| 5 | 入口 adapter の認証・スコープ確定 | `input_context` に `{tenant_id, org_id, entry_mode}` を**サーバ側で**設定。`/resume` は `/invoke` と同等認証 + checkpoint 所有権照合（foreign と unknown は同じ 403） |
| 6 | 縮退時の出力制限 | LLM 不在時は `support_level` を全件 `undetermined` にし、意味的な支持/矛盾を主張しない |

## Import Isolation Confirmation

- [x] Template は agenticstar-platform SDK（Level 0）を import しない
- [x] Import 先は `framework/` と `shared/` のみ（`agents/base/` 不要）
- [x] 他テンプレートを直接 import しない（部品は**写経**で取り込む）
- [x] 部品の import identity は **`src.<part>` 一本**に統一（top-level import は editable install に隠れる
      `ModuleNotFoundError` の温床。root conftest は repo root のみ）

## Financial Regulatory Consultation Response Evidence Brief Agent（市中協議回答 証拠ブリーフ）との差異

両者は「当てはめ型・証拠ブリーフ生成」という**パターンは共通**だが、規則体系・入力・出力スキーマが異なる。

| 軸 | Financial Regulatory Consultation Response Evidence Brief Agent | **FIN-C2-177（本テンプレ）** |
|---|---|---|
| 規則体系 | 市中協議文書の**設問・論点** | **外部委託監督要求**（金融庁監督指針 / FISC 安全対策基準ベースの統制表） |
| 入力 | 回答案・意見書・影響分析・規程根拠 | **委託先の統制証跡**（質問票回答・SOC/監査報告・BCP・契約） |
| 出力スキーマ | **設問‐主張‐source 行列**（`questions`/`claims`/`evidence_sources`/`gaps`/`verification_questions`） | **統制要求‐証跡の十分性行列**（`control_requirements`/`evidence_sources`/`gaps`/`contradictions`/`verification_questions`/`prior_finding`/`changed_since_last` の 7 キー） |
| KB projection | 過去協議論点観点 | **当該案件の証跡 + 同一ベンダー過去評価の参照**（統制表・観点表は KB に置かず版固定入力） |
| 禁止（人間留保）判定 | 回答提出・法的適合の最終判定 | **委託可否・規制適合・重要性・例外受容・契約妥当性** |
| 買い手 / オーナー | コンプライアンス／規制対応部門 | **外部委託管理／調達部門** |

本テンプレートは**統制要求‐証跡の十分性行列**を固有スキーマとして固定し Financial Regulatory Consultation Response Evidence Brief Agent と分離する。

**Professional Services Engagement Acceptance Evidence Agent（受任受入証拠）との差異**: 第三者評価の証跡十分性照合という表面類似はあるが、
規則体系（外部委託監督要求 vs 利益相反・独立性・AML）・利用者・出力が別。

## Design-entry blocker の反映（decision.md §12）

| # | blocker | 反映先 |
|---|---|---|
| (a) | DB schema と KB projection の分離 | 🔴 **設計変更**（§統制ガバナンス）: `control_catalogues` / `review_perspectives` は **AG が所有する DB テーブルではなく `invoke` の版固定入力**に変更。AG が持つのは `outsourcing_cases` / `case_evidence` / `feedback_audit_events` / `perspective_candidates`。KB 検索は**当該案件証跡と同一ベンダー過去評価**に限定 |
| (b) | 顧客承認済み統制表の版管理・委託先類型の人間指定 IF・照合ロジック | §State（`control_catalogue_ref` / `vendor_type`）/ §スコープ固定境界 INV-1・INV-2 |
| (c) | S-3 安全契約（規制適合判定を自動生成できない・全論点 source link 必須。※「context に結論ラベルを入れない」は 2026-07-28 に是正 — 過去評価の結論は `prior_finding` として由来明示で渡し、今回証跡の裏付けを必須にする方式へ） | §出力契約（closed allowlist）/ §スコープ固定境界 INV-3〜INV-5 → 受入テストは `docs/03_test_spec.md` |
| (d) | 顧客データ export の最小要件・機微情報のデータ品質・保持/削除/監査の統治契約 | §適用部品（`ingest-file-sanitizer` 品質ゲート）/ 統治契約の顧客合意は **PoC 開始前**（運用ガイドで責務分界を明記） |
| (e) | Financial Regulatory Consultation Response Evidence Brief Agent との差異明文化 | §Financial Regulatory Consultation Response Evidence Brief Agent との差異 |
| (f) | PoC 記録 | 導入判断前に人が記録する |

## ユーザビリティ設計レビュー（設計判断の修正点）

エンドユーザー（外部委託管理／調達担当者）のユースケースを想定して設計を見直し、以下を修正した。

| # | 観点 | 当初案 | **修正後** | 理由 |
|---|---|---|---|---|
| 1 | **入力** | 統制表・観点表を `ingest` で KB に蓄積し、検索で想起する | 🔴 **統制表・観点表は `invoke` の版固定入力**（`*_ref` + `*_version` + `content_hash`）。KB 蓄積の対象から外した | 実務では所有者・承認・有効日を持つ**版管理された配布物**（GRC / SIG 等）。ベクトル検索で「似た観点」を拾わせる対象ではなく、**どの版を適用したかが監査記録**になる |
| 2 | **入力** | 委託先類型を AG が証跡から推定 | **人間が `vendor_type` を指定**（必須入力） | 類型を AG が誤推定すると、間違った統制表が適用され「誤った基準で不足を指摘する」最悪の失敗になる。スコープ爆発（T9）も招く |
| 3 | **出力** | 統制要求ごとにフラットな判定リスト | **`support_level` を候補粒度 4 値**に固定し、**確認質問を分離フィールド**で返す | 担当者の次アクションは「委託先への追加資料要求」。確認質問がそのまま送付文面の素材になる形が最も使える |
| 4 | **出力** | 不足のみ返す | **`contradictions`（矛盾）を独立フィールド**に | 「証跡はあるが記述が食い違う」ケースは不足とは対応が全く違う（追加資料要求ではなく委託先への照会）。混ぜると担当者が仕分けし直す手間が出る |
| 5 | **対話** | 一発生成のみ | **Stage 4 で HITL 壁打ち**（低信頼・source 不一致時に人間へ）。`CORRECTED` でも再検証を強制 | 外部委託 DD は担当者が「この証跡はこの要求に効いている」と判断を差し込む工程。壁打ちが正常系（Cat 2 の正常用法） |
| 6 | **エラー/縮退** | KB 空振り・LLM 不在で error | **`status="success"` + `degradation_reason`** で縮退し、Stage 1 の決定論結果 + 当該委託先の照合だけでドラフト生成 | 過去レビューが少ない立ち上げ期でも使えないと導入できない。「KB が薄いから何も返さない」は業務価値ゼロ |
| 7 | **エラー** | 縮退理由が外部から見えない | `degradation_reason` を **`formatted_output` に畳み込む** | `get_output()` がカスタム欄を透過しない（実機確認）ため、畳み込まないと担当者が「なぜ結果が薄いか」を知れない |

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | **AgentBaseGraph** | AutonomousBaseGraph | **AgentBaseGraph** | 決定論的な pre→main→post パイプライン。自律 think-act ループは不要（Cat 2） |
| Composition pattern | Standalone | **GraphNode (subgraph)** | **GraphNode** | mode 分岐 + Stage 1〜4 の多段ドメインロジックを内側 graph に隠蔽 |
| Error propagation | **propagate** | handle | **propagate** | 根拠検証は fail fast。安全側に倒す |
| HITL | 無効 | **有効** | **有効** | 低信頼・claim→source 不整合時に外部委託管理担当のレビューへ回す壁打ち。FIN Cat 2 は HITL Gate 必須 |
| 規制適合・委託可否判定 | 出力する | **schema から構造排除** | **構造排除** | 誤用リスク🔴高。委託可否・規制適合の最終判定は有資格者に残す |
| 非結論性の担保 | denylist（禁止フレーズ正規表現） | **closed allowlist + 候補粒度固定 + HITL** | **allowlist 一本** | 自由文の断定言い換えは lexicon で原理的に閉じない。denylist を残すと false assurance になる |
| 証跡本文の扱い | State に載せる | **transient payload store + 英字 ref** | **payload_ref 間接化** | 生本文が checkpoint に永続化される。uuid4 ref は S-2 PII ゲートに破壊されるため英字のみ |
| テナント境界キーの取得元 | `ctx.tenant_id` | **State（pre_process が解決）** | **State 経由** | 実機確認で `InvocationContext` に `tenant_id` は**存在しない**（2026-07-27） |
| 組織境界 | tenant のみ | **tenant + org の二重 fail-closed** | **両方必須** | tenant だけだと同一金融機関内の他組織へ未公開の委託先情報が漏れる |
| **過去評価の結論の扱い** | LLM に一切渡さない（前版） | **由来明示で渡し・今回証跡の裏付けを必須化** | **渡す** | 「AI が可能性を指摘すると違法」は**誤認**（監督指針が求めるのは金融機関による適切な管理と判断根拠の事後説明可能性）。渡さない設計は再評価の中核価値（前回不足→改善の差分）を潰す。危険なのは結論を持つことでなく**検証なしに引き継ぐこと**なので、`prior_finding` + `as_of` の構造分離と今回証跡による裏付け必須化で担保 |
| **統制表・観点表の所在** | AG が KB に蓄積し検索で想起（初版） | **版固定の `invoke` 入力**（AG は所有しない） | **入力** | 実務では所有者・承認・有効日を持つ版管理配布物であり、**どの版を適用したかが監査記録**。🔴 **この結果、当初の「経験を貯めて賢くなる」蓄積 moat は放棄した**。残る価値は ①案件証跡の安全な取り込みと出典化 ②同一ベンダー再評価の比較 ③人間承認を経た次版昇格ループ ④監査可能な不変レビュー記録 という**ワークフロー/監査側の moat**。これらが実装されなければ本 AG は薄い文書→行列変換 Tool に退化する、という点を正直に記録する |
