# Issue Validation Report

## Summary

22 issues were reviewed from the original audit report. 15 were confirmed as valid at their original severity. 3 were confirmed but reseveritied. 2 were partially valid. 1 was disputed. 1 (the retracted `AssertionError` finding) was already self-retracted by the original reporter and is noted but not counted. Overall, the original report is of high quality -- the vast majority of findings are real and accurately described. The most significant error in the report is the `HeteroGNN.forward` x_dict bug, which applies to the copy at `gnn/sage_hetero.py` but not to the actual copy used in production at `project_constellation/gnn/hetero_sage.py`, where the code correctly iterates over `h.items()`.

## Detailed Findings

### Issue: Wrong import `torch.functional` causes crash in training loop

- **Original severity**: CRITICAL
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/main.py` line 10: `import torch.functional as F`. The module `torch.functional` exists but does not expose `binary_cross_entropy_with_logits`. Line 199 calls `F.binary_cross_entropy_with_logits(...)`, which will raise `AttributeError` at runtime. The same incorrect import appears at `/Users/brian.tang/Documents/graph-dl/project_constellation/utils/utils.py` line 2. Notably, the `evaluate()` function in the same `main.py` file (line 144) correctly uses `torch.nn.functional.binary_cross_entropy_with_logits`, showing the author knew the correct module but used the wrong import for `F`.
- **Fix assessment**: The proposed fix (change to `import torch.nn.functional as F`) is correct and sufficient.

### Issue: Parameter name mismatch in `prepare_atm_transactions` call causes TypeError

- **Original severity**: CRITICAL
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/pipeline/preprocess.py`. Line 116 defines the parameter as `atm_map_strategy`, but line 339 calls it with `map_strategy="drop"`. This will raise `TypeError: prepare_atm_transactions() got an unexpected keyword argument 'map_strategy'`.
- **Fix assessment**: The proposed fix (change `map_strategy` to `atm_map_strategy`) is correct.

### Issue: TypeError in optimizer construction: adding generator objects

- **Original severity**: CRITICAL
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/main.py` line 282-283: `list(gnn.parameters() + decoder.parameters())`. The `+` operator is applied inside `list()`, meaning it operates on the generator objects before they are converted to a list. This will raise `TypeError`. Interestingly, the same file's `utils/utils.py` at line 10 does it correctly: `list(model.parameters()) + list(decoder.parameters())`, and `rgcn.py` line 62 also does it correctly. Only the `main.py` entrypoint has this bug.
- **Fix assessment**: The proposed fix (`list(gnn.parameters()) + list(decoder.parameters())`) is correct.

### Issue: `merge_mappings` produces incorrect starting index for empty mapping

- **Original severity**: HIGH
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/rgcn.py` lines 190-192. When `curr` is empty (`len(curr) == 0`), the code returns `{a: i for i, a in enumerate(new, start=len(new))}`. If `new` has 5 elements, they get IDs 5, 6, 7, 8, 9 instead of 0, 1, 2, 3, 4. This creates a gap in node indices (IDs 0-4 unused), which causes misalignment with PyTorch Geometric tensors that expect contiguous 0-indexed node IDs. Note: this function is in `rgcn.py`, not in the main `project_constellation/` pipeline, so it may not currently be called by the production training code (which uses `preprocess.py`'s `merge` function instead). However, it is still a real bug.
- **Fix assessment**: The proposed fix (`start=0`) is correct.

### Issue: Inverted filter logic keeps unmapped ATM rows instead of dropping them

- **Original severity**: HIGH
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/pipeline/preprocess.py` lines 167-170. The filter `.filter(pl.col("ofi_account_uid").eq(null_atm_id) | pl.col("rfi_account_uid").eq(null_atm_id))` keeps rows WHERE the column equals the null sentinel (9999999999999), which is the opposite of the intent stated in the comment on line 166 ("drops all rows where the atm account id cannot be mapped"). The filter should be negated. The report also correctly notes that the filter references `ofi_account_uid`/`rfi_account_uid` (string columns from the original dataframe) when the sentinel value `null_atm_id` is an integer assigned to the `src`/`dst` columns. However, in this context, Polars `.with_columns` on lines 129-141 maps the ATM IDs into `src` and `dst` columns, while `ofi_account_uid` and `rfi_account_uid` remain as the original string columns. So comparing a string column to an integer sentinel will never match, making this filter a no-op regardless of the inversion -- effectively passing all rows through, neither dropping nor keeping based on the intended logic. This means the bug is actually worse than described: the filter does nothing useful.
- **Fix assessment**: The report's suggested fix is directionally correct but the column names in the fix (`src`/`dst`) are the right ones to use. The negation is also needed. So the complete fix would be: `.filter(~(pl.col("src").eq(null_atm_id) | pl.col("dst").eq(null_atm_id)))`.

### Issue: `construct_custom_encoder` ignores its `new_special_tokens` parameter

- **Original severity**: HIGH
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/src/foundation/tokenizer.py` lines 25-28. The function accepts `new_special_tokens` as a parameter but immediately reassigns it on line 28 with `new_special_tokens = define_paynet_tokens()`. Any caller-supplied tokens are silently discarded.
- **Fix assessment**: The proposed fix is correct. The most pragmatic approach would be to either remove the parameter (if it is always supposed to use `define_paynet_tokens()`) or remove the reassignment (if callers should be able to pass custom tokens).

### Issue: Accessing private `_special_token` attribute on tiktoken encoder

- **Original severity**: HIGH
- **Verdict**: Partially valid
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/src/foundation/tokenizer.py` line 30: `dict(base_encoder._special_token)`. The report claims the correct attribute name is `_special_tokens` (plural). In tiktoken's `Encoding` class, the attribute is indeed `_special_tokens` (plural) -- the object stores it as a dict mapping token strings to IDs. Using `_special_token` (singular) would raise `AttributeError`. However, the report also says "relying on private attributes is fragile." While true in general, tiktoken's `Encoding` class exposes `_special_tokens`, `_pat_str`, and `_mergeable_ranks` and they are commonly used in extension code (lines 42-43 of the same file use `_pat_str` and `_mergeable_ranks`). The core observation (typo in attribute name) is correct. The fragility concern is valid but less relevant given tiktoken's stable API.
- **Fix assessment**: The immediate fix (change `_special_token` to `_special_tokens`) is correct. The suggestion to use `special_tokens_set` is not directly applicable since that returns only the set of token strings without their integer IDs, which are needed here.

### Issue: `HeteroGNN.forward` uses input `x_dict` instead of computed `h` for output layer

- **Original severity**: HIGH
- **Verdict**: Partially valid (applies to one copy, not the production copy)
- **Investigation**: The report cites `gnn/sage_hetero.py` lines 164-168. At `/Users/brian.tang/Documents/graph-dl/gnn/sage_hetero.py` line 166: `for node_type, x in x_dict.items()` -- this is indeed a bug. After computing `h` through graph convolution layers (lines 159-160), the output projection iterates over `x_dict` (raw input features) rather than `h` (GNN-computed hidden representations), bypassing all convolution layers. However, the production code that `main.py` actually imports is `project_constellation/gnn/hetero_sage.py`, where line 120 reads `for node_type, x in h.items()` -- this is correct. The bug exists only in the `gnn/sage_hetero.py` copy, which appears to be an older or alternative version. The report does not clarify that the production entrypoint uses the other copy.
- **Revised severity**: MEDIUM -- the bug is real but exists in a file that is not imported by the production training entrypoint.
- **Fix assessment**: The fix (`x_dict.items()` to `h.items()`) is correct for the `gnn/sage_hetero.py` file.

### Issue: `SageGNNHetero.forward` crashes when a node type has no messages from any edge type

- **Original severity**: HIGH
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/gnn/sage_hetero.py` lines 72-74. The code `torch.stack(h_updated[node_type], dim=0).mean(dim=0)` will fail if `h_updated[node_type]` is an empty list. `torch.stack([])` raises `RuntimeError: stack expects a non-empty TensorList`. This can occur if an edge type is missing from the input data (e.g., no ATM withdrawal edges in a batch).
- **Fix assessment**: The proposed fix (guard with `if h_updated[node_type]:`) is correct.

### Issue: `AccountATMTransactionsDataset.__len__` returns `self` instead of an integer

- **Original severity**: HIGH
- **Verdict**: Confirmed, reseveritied
- **Revised severity**: LOW
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/pipeline/seal.py` lines 119-120: `return self`. This is clearly wrong -- `__len__` must return an integer. However, the entire class is a stub: the constructor is mostly commented out (lines 109-115), `__getitem__` just calls `super().__getitem__()` which will also fail. This class is not used anywhere in the codebase (the import is commented out). It is dead code / a placeholder.
- **Fix assessment**: The fix should be part of completing the class. As it stands, this is not a production bug.

### Issue: `median_iqr_scaling` produces NaN/Inf when IQR is zero

- **Original severity**: HIGH
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/pipeline/preprocess.py` lines 395-407. The function computes `(t - median) / (q3 - q1)` with no guard against zero IQR. When all values in a column are identical (common for sparse data), `q3 == q1` and the division produces `inf`/`NaN`. This is called from `main.py` lines 49-71 on feature columns that could easily have zero IQR (e.g., `total_withdrawn` for accounts with no ATM transactions, which would be all zeros after `fill_null(0)`).
- **Fix assessment**: The proposed epsilon-based fix is correct and standard practice.

### Issue: Hardcoded AWS profile name in source code

- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/main.py` line 3: `os.environ["AWS_PROFILE"] = "paysec-prod-admin"`. Also at `/Users/brian.tang/Documents/graph-dl/profile_scan.py` line 23: same value. This leaks the production admin AWS profile name into source code.
- **Fix assessment**: The proposed fix (use externally set environment variables) is correct.

### Issue: Hardcoded S3 paths to production data

- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/main.py` lines 239-240, and `/Users/brian.tang/Documents/graph-dl/rgcn.py` lines 17-18, 374-382. Multiple files contain hardcoded S3 paths like `s3://fra-prod-transformed-data/transactions/date=2025-02-02/`.
- **Fix assessment**: The proposed fix (move to environment variables or configuration file) is correct.

### Issue: Hardcoded Athena query exposes database schema

- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/main.py` line 229: `query = "SELECT longitude, latitude, terminal_id, term_fiid FROM fra_reference.dim_atm_terminal"`. This exposes the database name, table name, and column schema.
- **Fix assessment**: The proposed fix is correct. Moving to configuration or using parameterized queries would be better.

### Issue: `edge_time` slicing in `create_local_subgraph` is incorrect

- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/rgcn.py` lines 267-269. The code takes the first N edge times from the full graph: `full_data[edge_type].edge_time[: account_edge_indices[edge_type].size(1)]`. These timestamps correspond to the first N edges in the full graph, not the actual edges in the subgraph. The code itself has a comment acknowledging this: "This is simplified - you may need more complex mapping."
- **Fix assessment**: The proposed fix direction is correct -- should use the edge mask or indices from subgraph extraction.

### Issue: Negative sampling can loop indefinitely on dense graphs

- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/utils/negative_sampling.py` lines 58-65. The `while len(negatives) < num_negatives` loop has no maximum iteration guard. For dense graphs or when `num_negatives` is close to the total number of possible non-existing edges, this loop could run for a very long time. By contrast, `directional_degree_aware_sampling` (lines 184-187) has a `max_attempts` guard.
- **Fix assessment**: The proposed fix (add a `max_attempts` counter) is correct.

### Issue: Fixed random seed in negative sampling prevents batch diversity

- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/utils/negative_sampling.py` lines 33-34. `uniform_negative_sampling` calls `torch.manual_seed(seed)` and `random.seed(seed)` with a default seed of 42 every time it is invoked. Looking at the call site in `/Users/brian.tang/Documents/graph-dl/project_constellation/pipeline/dataloader.py` lines 215-227, the `_negative_sampling` method calls `uniform_negative_sampling` without overriding the seed, meaning the default seed=42 is used every time. This produces identical negative samples for every batch and epoch. The report also mentions `self.seed` in `_feature_sampling` (line 269), which passes `random_state=self.seed` to KDE sampling -- verified at dataloader.py line 269: `random_state=self.seed` where `self.seed` is set once in `__init__` and never changed.
- **Fix assessment**: The proposed fix (remove fixed seed or pass varying seed) is correct.

### Issue: All test files import from non-existent `project_iris` package

- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Verified at:
  - `/Users/brian.tang/Documents/graph-dl/test_sparse_runner.py` line 5: `from project_iris.utils.subgraphs import k_hop_subgraph_sampled_with_sparse`
  - `/Users/brian.tang/Documents/graph-dl/project_constellation/utils/tests/test_subgraphs.py` line 4: `from project_iris.utils.subgraphs import k_hop_subgraph_sampled`
  - `/Users/brian.tang/Documents/graph-dl/project_constellation/utils/tests/test_subgraphs_sparse.py` line 4: `from project_iris.utils.subgraphs import k_hop_subgraph_sampled_with_sparse`

  All three import from `project_iris` which does not exist. The package is `project_constellation`. None of these tests can run.
- **Fix assessment**: Change all `project_iris` imports to `project_constellation`.

### Issue: `test_sparse_runner.py` catches `AssertionError` (typo)

- **Original severity**: MEDIUM
- **Verdict**: The reporter self-retracted this finding after confusion. Let me verify independently. At `/Users/brian.tang/Documents/graph-dl/test_sparse_runner.py` line 14: `except AssertionError as e:`. Python's built-in exception is `AssertionError` (not `AssertionError`). Wait -- the actual Python built-in is spelled `AssertionError`. Let me check precisely: the built-in is `AssertionError` -- no, the correct Python built-in is `AssertionError`. Actually: `A-s-s-e-r-t-i-o-n-E-r-r-o-r`. The file says `AssertionError` on line 14. This matches the Python built-in `AssertionError` exactly. There is no typo. The reporter was correct to retract. (Note: the reporter got confused because they kept writing `AssertionError` in their analysis, which is the correct spelling.)

### Issue: `HGT.forward` only returns embeddings for "account" nodes

- **Original severity**: MEDIUM
- **Verdict**: Confirmed, reseveritied
- **Revised severity**: LOW
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/gnn/hgt.py` line 50: `return self.lin(x_dict["account"])`. Only account embeddings are returned; ATM embeddings computed by the HGT convolution layers are discarded. However, this class is not imported or used anywhere in the production pipeline -- `main.py` imports `HeteroGNN` from `project_constellation/gnn/hetero_sage.py`, not `HGT` from `gnn/hgt.py`. The HGT class appears to be an experimental alternative architecture. If it were to be used for account-to-ATM link prediction, the missing ATM embeddings would be a problem, but for account-only tasks this design is intentional (returning a single tensor rather than a dict is appropriate for single-node-type downstream tasks).
- **Fix assessment**: The proposed fix is correct if the model is to be used for multi-node-type tasks. As it stands, this is more of a design limitation than a bug.

### Issue: `load_transactions` references undefined `flow_engine`

- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/utils/data/transactions.py` line 26: `df, _ = flow_engine.preprocess_pl(...)`. The name `flow_engine` is never imported or defined in this module. This will raise `NameError` at runtime. I searched the entire codebase for `flow_engine` and it appears nowhere else -- it is likely a reference to an external package or internal module that was removed or never committed.
- **Fix assessment**: The fix requires knowing what `flow_engine` was supposed to be. The import needs to be added, or the function needs to be rewritten to use available data loading methods.

### Issue: Validation inside training loop calls `next(val_data_loader)` without iterator protocol

- **Original severity**: MEDIUM
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/main.py` line 211: `val_batch_data = next(val_data_loader)`. The `TransactionsLinkNeighbourLoader.__next__` method (dataloader.py line 309) checks if `epoch_seeds` exists and generates them if not, so the first call will work. However, once all seeds are exhausted (`self.current_index >= len(self.epoch_seeds)`), it raises `StopIteration`. Since the training loop `for batch_data in data_loader` catches `StopIteration` from its own iterator, a `StopIteration` raised by `next(val_data_loader)` will be caught by the for-loop mechanism, silently terminating the training loop early. Additionally, the validation loader's `current_index` is never reset between epochs (no `__iter__` is called on `val_data_loader`), so from the second epoch onward, validation would immediately raise `StopIteration`.
- **Fix assessment**: The proposed fix (create a cycling iterator or evaluate periodically with a fresh iterator) is correct. The effort estimate of "hours" is appropriate.

### Issue: Missing `ACCOUNT_DEPOSIT_ATM` edge type in `SageGNNHetero`

- **Original severity**: LOW
- **Verdict**: Confirmed, reseveritied
- **Revised severity**: LOW (no change -- confirming the severity is appropriate)
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/gnn/sage_hetero.py` lines 35-42. Only `ACCOUNT_TO_ACCOUNT` and `ACCOUNT_WITHDRAW_ATM` SAGEConv layers are registered. The `TransactionEdgeType` enum defines `ATM_DEPOSIT_ACCOUNT = 2`, but the comment in `models/transaction.py` line 14 explicitly says "ignore 2, because this isn't as useful for now." Additionally, the preprocessing code in `preprocess.py` line 164-165 explicitly filters out edge type 2: `.filter(~pl.col("edge_type").eq(2))`. So deposit edges never reach the model.
- **Fix assessment**: The report's assessment is fair -- an explicit check or warning when deposit edges exist would be good practice, but the current pipeline deliberately excludes them.

### Issue: `DCGNNHeteroModel` does not inherit from `nn.Module`

- **Original severity**: LOW
- **Verdict**: Confirmed
- **Investigation**: Verified at `/Users/brian.tang/Documents/graph-dl/project_constellation/pipeline/seal.py` lines 126-144: `class DCGNNHeteroModel:`. The class creates `nn.Embedding` layers but without inheriting from `nn.Module`, the parameters will not be registered, won't appear in `.parameters()`, and won't be moved to GPU with `.to(device)`. Like the `AccountATMTransactionsDataset` in the same file, this class is incomplete (ends at line 144 with just `__init__` and a comment about SortingLayer). It is dead code.
- **Fix assessment**: The fix is correct as stated but this is part of an incomplete class that needs full implementation.

### Issue: Debug `print` statements left in production code

- **Original severity**: LOW
- **Verdict**: Confirmed
- **Investigation**: Verified at multiple locations:
  - `/Users/brian.tang/Documents/graph-dl/project_constellation/utils/subgraphs.py` line 165: `print("Using sparse")`
  - `/Users/brian.tang/Documents/graph-dl/project_constellation/utils/subgraphs.py` line 257: `print(f"From {node_idx.shape[-1]} seed nodes...")`
  - `/Users/brian.tang/Documents/graph-dl/project_constellation/pipeline/dataloader.py` line 232: `print(atm_pos_edge_index.shape)`
  - `/Users/brian.tang/Documents/graph-dl/project_constellation/pipeline/dataloader.py` lines 129-132: multiple print statements in the relabel_nodes path
  - `/Users/brian.tang/Documents/graph-dl/project_constellation/utils/iceburger.py` line 126: `print(catalog)`

  All confirmed. These prints fire on every batch/invocation.
- **Fix assessment**: Replacing with `logging.debug()` or removing is correct.

### Issue: Duplicate `TransactionEdgeType` class across two modules

- **Original severity**: LOW
- **Verdict**: Confirmed
- **Investigation**: Verified both files are identical:
  - `/Users/brian.tang/Documents/graph-dl/models/transaction.py`
  - `/Users/brian.tang/Documents/graph-dl/project_constellation/models/transaction.py`

  Both contain identical `TransactionEdgeType` enum definitions. The `gnn/sage_hetero.py` imports from `models.transaction`, while `project_constellation/` modules import from `project_constellation.models.transaction`.
- **Fix assessment**: Correct -- consolidate to a single source of truth.

### Issue: Unused imports and dead code

- **Original severity**: LOW
- **Verdict**: Partially valid
- **Investigation**:
  - `rgcn.py` lines 8-9: The report claims relative imports from `.models.transaction` and `.utils.hetero`. Checking `/Users/brian.tang/Documents/graph-dl/rgcn.py` line 8: `from .models.transaction import TransactionEdgeType` and line 9: `from .utils.hetero import generate_split`. These are indeed relative imports in a top-level file, which would fail if run as a script (not as a module). However, if the package is installed (there is a `pyproject.toml` and `graph_dl.egg-info/`), these might work depending on how the module is invoked. The report is directionally correct.
  - `preprocess.py` commented-out `prepare_hetero_data`: Verified at lines 174-196 -- this is commented out and a new version exists at line 199. Confirmed dead code.
  - `custom_loader.py` `LinkSnapshotNeighborLoader.__init__` is empty: Verified at line 81-82: `def __init__(self, data: HeteroData): pass`. Confirmed.
  - The report mentions `rgcn.py` imports `generate_hetero_sage` and `LinkPredictorDecoder`: I checked and `rgcn.py` line 9 imports `generate_split` from `.utils.hetero`, not `generate_hetero_sage`. Also, `LinkPredictorDecoder` is not imported in `rgcn.py`. The report got these specific import names wrong.
- **Fix assessment**: Directionally correct but some details are inaccurate about which specific names are imported.

## New Issues Discovered

### [MEDIUM] `preprocess.py` imports from `models.transaction` (top-level) instead of `project_constellation.models.transaction`

- **Category**: Correctness
- **Location**: `/Users/brian.tang/Documents/graph-dl/project_constellation/pipeline/preprocess.py` line 9
- **Problem**: The file imports `from models.transaction import TransactionEdgeType`. Since the file is inside `project_constellation/`, this import uses the top-level `models/` package rather than the local `project_constellation/models/`. While both currently contain identical code, this is fragile -- it depends on the top-level `models/` being on the Python path and staying in sync with the project_constellation copy.
- **Suggested fix**: Change to `from project_constellation.models.transaction import TransactionEdgeType`.
- **Effort**: Small (< 1 hour)

### [LOW] `HeteroGNN` in `gnn/sage_hetero.py` uses `num_atm_nodes` parameter while `project_constellation/gnn/hetero_sage.py` uses `emb_size`

- **Category**: Maintainability
- **Location**: `/Users/brian.tang/Documents/graph-dl/gnn/sage_hetero.py` line 88 vs `/Users/brian.tang/Documents/graph-dl/project_constellation/gnn/hetero_sage.py` line 29
- **Problem**: Two copies of `HeteroGNN` exist with different parameter names (`num_atm_nodes` vs `emb_size`) and slightly different implementations. The `main.py` entrypoint passes `emb_size` and imports from `project_constellation/gnn/hetero_sage.py`. The `gnn/sage_hetero.py` copy would fail if called with `emb_size` since it expects `num_atm_nodes`.
- **Suggested fix**: Consolidate to a single copy or clearly mark one as deprecated.
- **Effort**: Small (< 1 hour)
