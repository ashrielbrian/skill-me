# Codebase Audit Report

## Summary

This is a graph deep learning project for financial transaction analysis, using PyTorch Geometric to build heterogeneous graph neural networks (GNNs) for link prediction on account-to-account and account-to-ATM transaction graphs. The codebase is in active development with significant structural issues: several crash-causing bugs exist in the main training pipeline, broken imports prevent tests from running, and hardcoded AWS credentials profiles are scattered throughout the code. The most critical finding is a `TypeError` crash in the optimizer initialization in `main.py` that would prevent any training from executing.

## Findings

### [CRITICAL] TypeError crash in optimizer initialization -- `gnn.parameters() + decoder.parameters()` is invalid

- **Category**: Correctness
- **Location**: `project_constellation/main.py`, line 283
- **Problem**: The code calls `list(gnn.parameters() + decoder.parameters())`. The `+` operator is not defined between two generator objects returned by `.parameters()`. This will raise a `TypeError` at runtime, making the entire training pipeline completely non-functional. This is the main entry point for training.
- **Evidence**:
  ```python
  optimizer = torch.optim.Adam(
      list(gnn.parameters() + decoder.parameters()), lr=LEARNING_RATE
  )
  ```
- **Suggested fix**: Change to `list(gnn.parameters()) + list(decoder.parameters())` -- materialize each generator into a list before concatenating.
- **Effort**: Small (< 1 hour)

### [CRITICAL] Wrong import: `torch.functional` instead of `torch.nn.functional`

- **Category**: Correctness
- **Location**: `project_constellation/main.py`, line 10; `project_constellation/utils/utils.py`, line 2
- **Problem**: `import torch.functional as F` imports the wrong module. `torch.functional` is a low-level internal module that does not contain `binary_cross_entropy_with_logits`. When `F.binary_cross_entropy_with_logits(...)` is called on line 199 of `main.py` and line 31 of `utils.py`, it will raise an `AttributeError` and crash the training loop and diagnostic utilities.
- **Evidence**:
  ```python
  import torch.functional as F
  # ...
  loss = F.binary_cross_entropy_with_logits(logits.squeeze(-1), ...)
  ```
- **Suggested fix**: Change to `import torch.nn.functional as F`.
- **Effort**: Small (< 1 hour)

### [HIGH] Broken test imports -- tests reference nonexistent `project_iris` package

- **Category**: Correctness
- **Location**: `test_sparse_runner.py`, line 5; `project_constellation/utils/tests/test_subgraphs.py`, line 4; `project_constellation/utils/tests/test_subgraphs_sparse.py`, line 4
- **Problem**: All three test files import from `project_iris.utils.subgraphs`, but the package is named `project_constellation`. This means none of the tests can run, providing zero regression protection.
- **Evidence**:
  ```python
  from project_iris.utils.subgraphs import k_hop_subgraph_sampled_with_sparse
  ```
- **Suggested fix**: Change all `project_iris` references to `project_constellation`.
- **Effort**: Small (< 1 hour)

### [HIGH] Typo: `AssertionError` instead of `AssertionError` in exception handler

- **Category**: Correctness
- **Location**: `test_sparse_runner.py`, line 14
- **Problem**: The `except AssertionError` clause has a typo -- `AssertionError` is not a valid Python built-in exception. This means `AssertionError` exceptions will not be caught, and instead a `NameError` will be raised. Test failures will appear as unexpected crashes rather than clean test results.
- **Evidence**:
  ```python
  except AssertionError as e:
  ```
- **Suggested fix**: Change to `except AssertionError as e:`.
- **Effort**: Small (< 1 hour)

### [HIGH] `merge_mappings` in `rgcn.py` starts new IDs from `len(new)` instead of 0

- **Category**: Correctness
- **Location**: `rgcn.py`, lines 190-199
- **Problem**: When `curr` is empty (first call), new node IDs start at `start=len(new)` instead of `start=0`. For example, if 100 accounts are provided, IDs will range from 100 to 199 instead of 0 to 99. This creates a gap at the beginning of the ID space, leading to wasted memory in embedding layers and potentially out-of-bounds indexing if other code expects IDs to start at 0.
- **Evidence**:
  ```python
  if len(curr) == 0:
      return {a: i for i, a in enumerate(new, start=len(new))}
  ```
- **Suggested fix**: Change `start=len(new)` to `start=0`.
- **Effort**: Small (< 1 hour)

### [HIGH] `construct_custom_encoder` ignores its `new_special_tokens` parameter

- **Category**: Correctness
- **Location**: `src/foundation/tokenizer.py`, lines 25-28
- **Problem**: The function accepts a `new_special_tokens` parameter but immediately overwrites it by re-calling `define_paynet_tokens()`. Any caller passing custom tokens will have their input silently discarded.
- **Evidence**:
  ```python
  def construct_custom_encoder(new_special_tokens, base_enc: str = "cl100k_base"):
      base_encoder = tiktoken.get_encoding(base_enc)
      new_special_tokens = define_paynet_tokens()  # Parameter overwritten!
  ```
- **Suggested fix**: Remove the reassignment on line 28 to use the passed-in parameter, or remove the parameter and call `define_paynet_tokens()` internally if it's always meant to use the defaults.
- **Effort**: Small (< 1 hour)

### [HIGH] `_special_token` private attribute access on tiktoken Encoding

- **Category**: Correctness
- **Location**: `src/foundation/tokenizer.py`, line 30
- **Problem**: `base_encoder._special_token` accesses a private attribute of tiktoken's `Encoding` class. The actual attribute name in tiktoken is `_special_tokens` (plural). This will raise an `AttributeError` at runtime.
- **Evidence**:
  ```python
  special_tokens = dict(base_encoder._special_token)
  ```
- **Suggested fix**: Change to `base_encoder._special_tokens` (with the trailing 's').
- **Effort**: Small (< 1 hour)

### [HIGH] `HeteroGNN.forward` uses raw `x_dict` instead of updated `h` for output projection

- **Category**: Correctness
- **Location**: `gnn/sage_hetero.py`, lines 166-167
- **Problem**: After running all graph convolution layers and updating `h`, the output projection iterates over the original `x_dict` (the raw input features) instead of the updated hidden states `h`. This means the multi-layer GNN output is computed by projecting the raw input features, completely bypassing the learned graph convolutions. The model will not learn any structural information.
- **Evidence**:
  ```python
  for conv in self.convs:
      h = conv(h, edge_index_dict)
  out = {}
  for node_type, x in x_dict.items():  # BUG: should be h.items()
      out[node_type] = self.out_layers[node_type](F.relu(x))
  ```
- **Suggested fix**: Change `x_dict.items()` to `h.items()` in the output loop.
- **Effort**: Small (< 1 hour)

### [HIGH] `SageGNNHetero.forward` crashes when a node type has no message-passing updates

- **Category**: Correctness
- **Location**: `gnn/sage_hetero.py`, lines 72-74
- **Problem**: `torch.stack(h_updated[node_type], dim=0).mean(dim=0)` is called for every node type in `h_updated`, but if a node type received no messages (its list is empty), `torch.stack` on an empty list will raise a `RuntimeError`. This happens when certain edge types are absent from a batch.
- **Evidence**:
  ```python
  for node_type in h_updated:
      h[node_type] = torch.stack(h_updated[node_type], dim=0).mean(dim=0)
  ```
- **Suggested fix**: Add a guard: `if h_updated[node_type]:` before the stack/mean, otherwise keep the original `h[node_type]`.
- **Effort**: Small (< 1 hour)

### [HIGH] `prepare_atm_transactions` parameter name mismatch: called with `map_strategy` but defined as `atm_map_strategy`

- **Category**: Correctness
- **Location**: `project_constellation/pipeline/preprocess.py`, line 339 (call site) vs line 116 (definition)
- **Problem**: The function is defined with the keyword `atm_map_strategy` but called with `map_strategy="drop"`. This will raise a `TypeError` when `generate_split` runs.
- **Evidence**:
  ```python
  # Definition:
  def prepare_atm_transactions(df, atm_mappings, account_mappings, atm_map_strategy="drop"):
  # Call:
  san_transactions_df = prepare_atm_transactions(..., map_strategy="drop")
  ```
- **Suggested fix**: Change the call site to use `atm_map_strategy="drop"`.
- **Effort**: Small (< 1 hour)

### [HIGH] ATM transaction filter logic is inverted -- keeps only unmapped rows

- **Category**: Correctness
- **Location**: `project_constellation/pipeline/preprocess.py`, lines 167-169
- **Problem**: The final filter `.filter(pl.col("ofi_account_uid").eq(null_atm_id) | pl.col("rfi_account_uid").eq(null_atm_id))` keeps rows where the account UID equals the sentinel null ATM ID (9999999999999). This is the opposite of the intended behavior. The comment says "drops all rows where the atm account id cannot be mapped," but the filter keeps only those unmappable rows and discards all valid rows.
- **Evidence**:
  ```python
  # drops all rows where the the atm account id cannot be mapped
  .filter(
      pl.col("ofi_account_uid").eq(null_atm_id)
      | pl.col("rfi_account_uid").eq(null_atm_id)
  )
  ```
- **Suggested fix**: Negate the condition: `.filter(~(pl.col("src").eq(null_atm_id) | pl.col("dst").eq(null_atm_id)))`. Note that the filter should be on `src`/`dst` (the mapped integer columns), not the string UID columns.
- **Effort**: Small (< 1 hour)

### [MEDIUM] `median_iqr_scaling` produces NaN/Inf when IQR is zero

- **Category**: Correctness
- **Location**: `project_constellation/pipeline/preprocess.py`, lines 395-407
- **Problem**: If all values in a column are identical (e.g., all zeros), then `q3 - q1` equals zero, resulting in division by zero. This produces NaN or Inf values that propagate through the model, causing training to fail silently with `NaN` losses.
- **Evidence**:
  ```python
  return (t - median) / (q3 - q1)
  ```
- **Suggested fix**: Add a guard: `iqr = q3 - q1; iqr = torch.where(iqr == 0, torch.ones_like(iqr), iqr)` before dividing.
- **Effort**: Small (< 1 hour)

### [MEDIUM] `create_local_subgraph` incorrectly slices `edge_time` by position instead of by edge mask

- **Category**: Correctness
- **Location**: `rgcn.py`, lines 268-269
- **Problem**: Edge times are sliced as `[:account_edge_indices[edge_type].size(1)]` which takes the first N edge times by position. But after subgraph extraction, the relevant edges may not be the first N edges in the original tensor. This assigns incorrect temporal attributes to edges, corrupting temporal information.
- **Evidence**:
  ```python
  subgraph[edge_type].edge_time = full_data[edge_type].edge_time[
      : account_edge_indices[edge_type].size(1)
  ]
  ```
- **Suggested fix**: Track and use the actual edge indices/mask from `k_hop_subgraph` to index into `edge_time`.
- **Effort**: Medium (hours)

### [MEDIUM] Negative sampling uses a fixed seed, producing identical negatives every epoch

- **Category**: Correctness
- **Location**: `project_constellation/utils/negative_sampling.py`, lines 33-34; `project_constellation/pipeline/dataloader.py`, line 269
- **Problem**: `uniform_negative_sampling` sets `torch.manual_seed(seed)` and `random.seed(seed)` at the start of each call with a hardcoded seed (42). Since the dataloader calls this on every batch with the same seed, every batch gets the exact same negative samples. The model repeatedly trains on the same negatives, severely reducing the diversity of training signal and likely hurting generalization.
- **Evidence**:
  ```python
  def uniform_negative_sampling(..., seed: int = 42):
      torch.manual_seed(seed)
      random.seed(seed)
  ```
- **Suggested fix**: Remove the seed-setting from inside the sampling functions. Let the caller control reproducibility at the epoch level if needed.
- **Effort**: Small (< 1 hour)

### [MEDIUM] KDE feature sampling also uses a fixed seed, always producing the same features

- **Category**: Correctness
- **Location**: `project_constellation/pipeline/dataloader.py`, lines 265-270
- **Problem**: `_feature_sampling` calls `sd.sample_gaussian_kde_1d(self.kde, ..., random_state=self.seed)` with a fixed seed. Every batch gets identical synthetic features for negative edges, reducing training diversity.
- **Evidence**:
  ```python
  acc_neg_feats = torch.tensor(
      sd.sample_gaussian_kde_1d(self.kde, n_samples=..., random_state=self.seed)
  )
  ```
- **Suggested fix**: Use a different seed per batch (e.g., increment a counter) or remove fixed seeding.
- **Effort**: Small (< 1 hour)

### [MEDIUM] Potential infinite loop in `uniform_negative_sampling` for dense graphs

- **Category**: Performance
- **Location**: `project_constellation/utils/negative_sampling.py`, lines 58-65
- **Problem**: The while loop generating negatives has no maximum attempt limit. In dense graphs where most node pairs already have positive edges, the loop may take extremely long or never terminate. `directional_degree_aware_sampling` correctly has a `max_attempts` guard (line 187), but the uniform and degree-aware versions do not.
- **Evidence**:
  ```python
  while len(negatives) < num_negatives:
      src = random.choice(src_list)
      dst = random.choice(dst_list)
      if (allow_self_loops or src != dst) and (src, dst) not in positive_set:
          negatives.append((src, dst))
  ```
- **Suggested fix**: Add a `max_attempts` counter similar to `directional_degree_aware_sampling`.
- **Effort**: Small (< 1 hour)

### [MEDIUM] `HGT.forward` only returns account embeddings, ignoring other node types

- **Category**: Correctness
- **Location**: `gnn/hgt.py`, line 50
- **Problem**: The forward method returns `self.lin(x_dict["account"])` which only projects and returns account node embeddings. ATM node embeddings (and any other node types) are discarded. If the model is used in a heterogeneous link prediction setting involving ATM nodes, this will fail.
- **Evidence**:
  ```python
  return self.lin(x_dict["account"])
  ```
- **Suggested fix**: Return the full `x_dict` with output projections for all node types, similar to how `HeteroGNN` works.
- **Effort**: Small (< 1 hour)

### [MEDIUM] Hardcoded AWS profile name `paysec-prod-admin` in source code

- **Category**: Security
- **Location**: `project_constellation/main.py`, line 3; `profile_scan.py`, line 23
- **Problem**: `os.environ["AWS_PROFILE"] = "paysec-prod-admin"` hardcodes a production AWS profile name. This exposes internal infrastructure naming, and if this code is committed to a shared or public repository, it reveals the production admin profile name. More importantly, setting this at module import time means importing `main.py` for any purpose (including tests or utility use) immediately configures the environment to use a production admin profile.
- **Evidence**:
  ```python
  os.environ["AWS_PROFILE"] = "paysec-prod-admin"
  ```
- **Suggested fix**: Use environment variables, a `.env` file, or CLI arguments to configure the AWS profile. Never set production credentials at import time.
- **Effort**: Small (< 1 hour)

### [MEDIUM] `AccountATMTransactionsDataset.__len__` returns `self` instead of an integer

- **Category**: Correctness
- **Location**: `project_constellation/pipeline/seal.py`, lines 119-120
- **Problem**: The `__len__` method returns `self` (the dataset object itself) instead of an integer. This will cause a `TypeError` whenever PyTorch's `DataLoader` calls `len()` on the dataset.
- **Evidence**:
  ```python
  def __len__(self):
      return self
  ```
- **Suggested fix**: Return the actual dataset length (e.g., the number of edges/samples).
- **Effort**: Small (< 1 hour)

### [MEDIUM] Validation done inside training loop with `next(val_data_loader)` -- exhausts iterator

- **Category**: Correctness
- **Location**: `project_constellation/main.py`, line 211
- **Problem**: `val_batch_data = next(val_data_loader)` is called inside the inner training batch loop. This consumes one batch from the validation loader per training batch. Once the validation loader is exhausted, `next()` will raise `StopIteration`, crashing the training loop. Additionally, the `evaluate()` result is not stored or logged.
- **Evidence**:
  ```python
  for batch_data in data_loader:
      # ... training code ...
      val_batch_data = next(val_data_loader)
      evaluate(model, decoder, val_batch_data)
  ```
- **Suggested fix**: Move validation outside the inner loop (e.g., validate once per epoch). Create a fresh iterator per validation pass.
- **Effort**: Small (< 1 hour)

### [MEDIUM] `account_features_df` only joins accounts that appear in edge type 0 AND edge type 1

- **Category**: Correctness
- **Location**: `project_constellation/main.py`, lines 44-84
- **Problem**: `edge_0_df` is filtered to unique `ofi_account_uid` for edge type 0 only, and `edge_1_df` for edge type 1 only. The `full` join between them means accounts that only appear in edge type 0 will have null `total_withdrawn`, and accounts that only appear as `rfi` (destination of transfers) but not as `ofi` (source) in either edge type will be missing entirely. The `.fill_null(0.0)` partially addresses this, but accounts not present as `ofi_account_uid` in either DataFrame are lost entirely, leading to `null` node features after the left join on line 90.
- **Evidence**: The join is on `ofi_account_uid`, so any account that only appears as a destination (`rfi_account_uid`) will have no features.
- **Suggested fix**: Build account features using all unique account IDs (both `ofi` and `rfi`) to ensure complete coverage.
- **Effort**: Medium (hours)

### [MEDIUM] `data["account"].x` is set from transaction rows, not unique accounts

- **Category**: Correctness
- **Location**: `project_constellation/pipeline/preprocess.py`, lines 211-217
- **Problem**: `data["account"].x` is set from `acct_to_acct_df` columns directly, which is at the transaction level (one row per transaction). This means the feature tensor has shape `(num_transactions, 3)` instead of `(num_accounts, 3)`. Since account node features should be one feature vector per account, this produces an incorrectly shaped feature matrix.
- **Evidence**:
  ```python
  data["account"].x = (
      acct_to_acct_df["total_sent_miscaled", "total_received_miscaled", "total_withdrawn_miscaled"]
      .to_torch()
      .type(torch.float)
  )
  ```
- **Suggested fix**: Deduplicate by account before converting to features, or use the account features DataFrame that was already computed.
- **Effort**: Medium (hours)

### [LOW] `DCGNNHeteroModel` does not inherit from `nn.Module`

- **Category**: Correctness
- **Location**: `project_constellation/pipeline/seal.py`, lines 126-144
- **Problem**: `DCGNNHeteroModel` is a plain Python class, not an `nn.Module`. The `nn.Embedding` layers assigned as attributes will not be registered as parameters, so `model.parameters()` will return nothing, and `model.to(device)` will not move embeddings to GPU. This class appears to be a work-in-progress stub.
- **Evidence**:
  ```python
  class DCGNNHeteroModel:  # No nn.Module inheritance
      def __init__(self, ...):
          self.z_embedding = nn.Embedding(max_z, hidden_channels)
  ```
- **Suggested fix**: Change to `class DCGNNHeteroModel(nn.Module):` and add `super().__init__()`.
- **Effort**: Small (< 1 hour)

### [LOW] Undefined `flow_engine` reference in `load_transactions`

- **Category**: Correctness
- **Location**: `project_constellation/utils/data/transactions.py`, line 26
- **Problem**: `flow_engine.preprocess_pl(...)` is called but `flow_engine` is never imported or defined in the file. This function will crash with a `NameError` if called.
- **Evidence**:
  ```python
  df, _ = flow_engine.preprocess_pl(...)
  ```
- **Suggested fix**: Add the missing import, or refactor to remove the dependency if it no longer exists.
- **Effort**: Small (< 1 hour)

### [LOW] Missing `__init__.py` files make some imports fragile

- **Category**: Maintainability
- **Location**: `project_constellation/` subdirectories
- **Problem**: Several imports use relative paths (`from models.transaction import ...`) and absolute paths (`from project_constellation.gnn.hetero_sage import ...`) inconsistently. Some imports assume the working directory is the project root. Without `__init__.py` files in all package directories, imports will fail depending on how the code is invoked.
- **Suggested fix**: Add `__init__.py` files consistently and standardize import paths.
- **Effort**: Small (< 1 hour)

### [LOW] Duplicate `TransactionEdgeType` class defined in two places

- **Category**: Maintainability
- **Location**: `models/transaction.py` and `project_constellation/models/transaction.py`
- **Problem**: The same `TransactionEdgeType` enum is defined identically in two locations. Some files import from `models.transaction` and others from `project_constellation.models.transaction`. If one copy is modified without the other, edge type definitions will silently diverge, causing data corruption.
- **Suggested fix**: Consolidate to a single location and update all imports.
- **Effort**: Small (< 1 hour)

### [LOW] Duplicate `HeteroGNN` class with divergent implementations

- **Category**: Maintainability
- **Location**: `gnn/sage_hetero.py` and `project_constellation/gnn/hetero_sage.py`
- **Problem**: Two different `HeteroGNN` classes exist with the same name but different constructor signatures (`num_atm_nodes` vs `emb_size`), different forward pass logic (the `project_constellation` version has ATM-specific layer routing, the `gnn` version has the `x_dict` bug). This creates confusion about which model is actually being trained.
- **Suggested fix**: Remove the older `gnn/sage_hetero.py` version or clearly separate the two implementations.
- **Effort**: Small (< 1 hour)

### [LOW] Debug `print()` statements left in production code

- **Category**: Maintainability
- **Location**: Multiple files including `project_constellation/pipeline/dataloader.py` line 232, `project_constellation/utils/subgraphs.py` lines 165/257, `project_constellation/utils/iceburger.py` line 126
- **Problem**: Various `print()` statements output debug information during normal execution (e.g., `print(atm_pos_edge_index.shape)`, `print("Using sparse")`, `print(catalog)`). In production training runs, this creates excessive console noise and can significantly slow down I/O-bound logging.
- **Suggested fix**: Replace with proper logging using Python's `logging` module, or remove entirely.
- **Effort**: Small (< 1 hour)

### [LOW] `rgcn.py` references undefined `generate_hetero_sage` and `LinkPredictorDecoder`

- **Category**: Correctness
- **Location**: `rgcn.py`, lines 57-58
- **Problem**: The `train()` function uses `generate_hetero_sage` and `LinkPredictorDecoder`, neither of which is imported or defined in the file. The file imports from `.models.transaction` and `.utils.hetero`, but these helper functions are not there. The file will crash if `train()` is called.
- **Evidence**:
  ```python
  gnn = generate_hetero_sage(train_data, hidden_channels, out_channels).to(device)
  decoder = LinkPredictorDecoder(out_channels).to(device)
  ```
- **Suggested fix**: Add the missing imports or remove the dead code.
- **Effort**: Small (< 1 hour)

## Issues Not Found

- **SQL Injection / Command Injection**: No user-supplied strings are interpolated into SQL queries. The Athena queries in `iceburger.py` use hardcoded table names, and PyIceberg's scan API uses parameterized filters, not raw SQL string concatenation.
- **Hardcoded Secrets**: No API keys, passwords, or tokens are hardcoded in source. The `AWS_PROFILE` name is a reference, not a credential. AWS credentials are properly obtained at runtime via `boto3.Session()`.
- **Circular Dependencies**: No circular import chains were found.
- **Race Conditions**: The code is single-threaded (no multi-threading or multi-processing), so race conditions are not applicable at this stage. The TODO for DDP conversion will introduce concurrency concerns.
- **Dependency Vulnerabilities**: The pinned `torch==2.2.2` is not the latest version, and `pyg-lib` is installed from `master` branch (which is unstable), but no known critical CVEs were identified for the specific versions listed.
