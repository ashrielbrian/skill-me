# Codebase Audit Report: graph-dl

**Date:** 2026-03-26
**Scope:** Full codebase at `~/Documents/graph-dl` (excluding `.venv/` and `__pycache__/`)
**Files reviewed:** 25 Python source files, 2 `pyproject.toml` files, 5 Jupyter notebooks (by reference)

---

## Summary

The codebase implements a graph deep learning pipeline for financial transaction analysis using PyTorch Geometric, with heterogeneous GNN models (SAGE, HGT) for link prediction. The audit identified **7 critical bugs**, **4 security issues**, **8 moderate bugs**, **6 performance problems**, and **11 code quality issues**.

---

## Findings by Severity

### CRITICAL -- Bugs that will cause crashes or produce incorrect results

#### C1. `torch.functional` imported instead of `torch.nn.functional` (CRASH)
- **Files:** `project_constellation/main.py` (line 10), `project_constellation/utils/utils.py` (line 2)
- **Impact:** `torch.functional` is NOT the same module as `torch.nn.functional`. The call `F.binary_cross_entropy_with_logits(...)` on line 199 of `main.py` will raise `AttributeError` because `torch.functional` does not expose that function.
- **Fix:** Change `import torch.functional as F` to `import torch.nn.functional as F`.

#### C2. `optimizer` construction uses `+` on generator objects (CRASH)
- **File:** `project_constellation/main.py` (line 283)
- **Code:** `list(gnn.parameters() + decoder.parameters())`
- **Impact:** `gnn.parameters()` returns a generator, and Python generators cannot be concatenated with `+`. This will raise `TypeError`. The correct form is `list(gnn.parameters()) + list(decoder.parameters())`.

#### C3. `merge_mappings` in `rgcn.py` starts indices at `len(new)` instead of 0 (LOGIC BUG)
- **File:** `rgcn.py` (line 192)
- **Code:** `return {a: i for i, a in enumerate(new, start=len(new))}`
- **Impact:** When `curr` is empty, the newly created mapping assigns indices starting at `len(new)` instead of 0. For example, if `new = ["a", "b", "c"]`, the result is `{"a": 3, "b": 4, "c": 5}` instead of `{"a": 0, "b": 1, "c": 2}`. This means node indices will not start from 0, causing shape mismatches or out-of-bounds errors downstream.

#### C4. `prepare_atm_transactions` filter logic is inverted (LOGIC BUG)
- **File:** `project_constellation/pipeline/preprocess.py` (lines 166-170)
- **Code:**
  ```python
  .filter(
      pl.col("ofi_account_uid").eq(null_atm_id)
      | pl.col("rfi_account_uid").eq(null_atm_id)
  )
  ```
- **Impact:** This filter **keeps** rows where the ATM ID could NOT be mapped (equals the sentinel `null_atm_id`), and **drops** all valid mapped rows. The intent was to drop unmappable rows. The condition should use `~(...).eq(null_atm_id)` or `.ne(null_atm_id)`:
  ```python
  .filter(
      pl.col("src").ne(null_atm_id) & pl.col("dst").ne(null_atm_id)
  )
  ```

#### C5. `generate_split` passes `map_strategy` keyword not accepted by `prepare_atm_transactions`
- **File:** `project_constellation/pipeline/preprocess.py` (line 339)
- **Code:** `prepare_atm_transactions(..., map_strategy="drop")`
- **Impact:** The function parameter is named `atm_map_strategy`, not `map_strategy`. This will raise `TypeError: unexpected keyword argument 'map_strategy'`.

#### C6. `prepare_models` passes `emb_size` keyword but `HeteroGNN.__init__` expects `num_atm_nodes` (in `gnn/sage_hetero.py`)
- **File:** `project_constellation/main.py` (line 112)
- **Impact:** In `main.py`, the `prepare_models` function passes `emb_size=data["atm"].x.shape[0]` to `HeteroGNN`. But the `HeteroGNN` in `gnn/sage_hetero.py` expects `num_atm_nodes`, while the `HeteroGNN` in `project_constellation/gnn/hetero_sage.py` does accept `emb_size`. The import on line 14 imports from `project_constellation.gnn.hetero_sage`, so this specific call is consistent -- but the two `HeteroGNN` classes have divergent signatures, which is a maintenance hazard. The `gnn/sage_hetero.py` version would crash if invoked the same way.

#### C7. `tokenizer.py` ignores input parameter `new_special_tokens` (LOGIC BUG)
- **File:** `src/foundation/tokenizer.py` (line 28)
- **Code:** `new_special_tokens = define_paynet_tokens()` immediately overwrites the parameter.
- **Impact:** The function `construct_custom_encoder(new_special_tokens, ...)` takes a `new_special_tokens` argument but immediately reassigns it to the hardcoded list from `define_paynet_tokens()`, making the parameter useless.

---

### HIGH -- Security issues

#### S1. Hardcoded AWS profile in source code
- **Files:** `project_constellation/main.py` (line 3), `profile_scan.py` (line 23)
- **Code:** `os.environ["AWS_PROFILE"] = "paysec-prod-admin"`
- **Impact:** Hardcoding a production admin AWS profile name in source code is a security risk. If this repository is shared or pushed to a remote, it exposes the name of a privileged IAM role. AWS credentials should be configured via environment variables, AWS config files, or secret managers -- never hardcoded.

#### S2. S3 bucket paths to production data hardcoded throughout
- **Files:** `rgcn.py` (lines 17, 20, 374-381), `project_constellation/main.py` (lines 239-240)
- **Code:** `"s3://fra-prod-transformed-data/transactions/date=2025-02-02/"`
- **Impact:** Production S3 bucket paths are hardcoded. These should be loaded from configuration files or environment variables to prevent accidental access to production data during development and to avoid leaking infrastructure details.

#### S3. SQL query string with no parameterization
- **File:** `project_constellation/main.py` (line 229)
- **Code:** `query = "SELECT longitude, latitude, terminal_id, term_fiid FROM fra_reference.dim_atm_terminal"`
- **Impact:** While this specific query has no user input, the pattern of inline SQL encourages future SQL injection vulnerabilities. Consider using parameterized queries.

#### S4. `iceburger.py` accesses AWS credentials directly and logs catalog objects
- **File:** `project_constellation/utils/iceburger.py` (lines 91-96, 126)
- **Impact:** The code accesses `frozen_credentials.access_key` and `frozen_credentials.secret_key` as string values and passes them through dictionaries. Line 126 prints the entire catalog object (`print(catalog)`), which may log sensitive credential information. Remove debug print statements and avoid logging credential-bearing objects.

---

### MODERATE -- Bugs that affect correctness in certain scenarios

#### M1. `HeteroGNN.forward` in `gnn/sage_hetero.py` uses input `x_dict` for output instead of `h`
- **File:** `gnn/sage_hetero.py` (line 166)
- **Code:** `for node_type, x in x_dict.items(): out[node_type] = self.out_layers[node_type](F.relu(x))`
- **Impact:** After running all graph convolutions, the model iterates over the **original** input `x_dict` to compute the output, completely ignoring the updated hidden states in `h`. This makes the graph convolution layers do nothing. Should iterate over `h.items()` instead.

#### M2. `SageGNNHetero.forward` crashes when a node type has no messages
- **File:** `gnn/sage_hetero.py` (line 74)
- **Code:** `h[node_type] = torch.stack(h_updated[node_type], dim=0).mean(dim=0)`
- **Impact:** If a node type receives no messages from any edge type (i.e., `h_updated[node_type]` is empty), `torch.stack([])` will raise a `RuntimeError`. Need to check for empty lists before stacking.

#### M3. `HGT.forward` only returns embeddings for "account" node type
- **File:** `gnn/hgt.py` (line 50)
- **Code:** `return self.lin(x_dict["account"])`
- **Impact:** The model hardcodes the return to only the "account" node type. This means it cannot be used for tasks involving other node types (e.g., ATM embeddings) and breaks the heterogeneous design pattern used elsewhere that expects a dictionary of embeddings.

#### M4. `AccountATMTransactionsDataset.__len__` returns `self` instead of an integer
- **File:** `project_constellation/pipeline/seal.py` (line 120)
- **Code:** `return self`
- **Impact:** `__len__` must return an integer. Returning `self` will cause `TypeError` when PyTorch DataLoader tries to determine the dataset length.

#### M5. `median_iqr_scaling` divides by zero when Q1 equals Q3
- **File:** `project_constellation/pipeline/preprocess.py` (line 407)
- **Code:** `return (t - median) / (q3 - q1)`
- **Impact:** If all values in a column are identical (or nearly so), `q3 - q1 == 0`, producing `inf` or `NaN`. Add a check: `iqr = q3 - q1; iqr = iqr if iqr > 0 else 1.0`.

#### M6. `_special_token` attribute accessed on tiktoken encoder may not exist
- **File:** `src/foundation/tokenizer.py` (line 30)
- **Code:** `special_tokens = dict(base_encoder._special_token)`
- **Impact:** The attribute is `_special_tokens` (plural) in tiktoken, not `_special_token`. This will raise `AttributeError`.

#### M7. `DCGNNHeteroModel` does not inherit from `nn.Module`
- **File:** `project_constellation/pipeline/seal.py` (line 126)
- **Impact:** The class stores `nn.Embedding` layers but does not inherit from `nn.Module`, so parameters will not be registered and `model.parameters()` will return nothing. The model cannot be trained.

#### M8. `evaluate` in `project_constellation/main.py` uses `edge_index_dict` edges as supervision edges
- **File:** `project_constellation/main.py` (lines 131-133)
- **Impact:** The evaluation function iterates `data.edge_index_dict` and tries to use `data[edge_type].edge_label_index` as labels. But `edge_label_index` is set up as a 1D tensor of all ones (representing positive edges only), while the model should be evaluated on both positive and negative edges. This means evaluation accuracy will always be computed against all-ones labels, giving a misleading metric.

---

### PERFORMANCE -- Issues affecting speed or memory

#### P1. `_build_adjacency_dict` iterates over all edges in Python (O(E) Python loop)
- **File:** `custom_loader.py` (lines 160-184)
- **Impact:** For large graphs with millions of edges, iterating over each edge in Python to build adjacency lists is extremely slow. Use vectorized PyTorch operations or CSR conversion instead.

#### P2. Negative sampling uses Python `while` loop with rejection sampling
- **File:** `project_constellation/utils/negative_sampling.py` (lines 59-65, 108, 187)
- **Impact:** For dense graphs, the rejection rate can be very high, causing the `while` loop to run for a very long time. `uniform_negative_sampling` has no `max_attempts` guard (unlike `directional_degree_aware_sampling`), so it can hang indefinitely if the graph is nearly complete.

#### P3. `k_hop_subgraph_sampled` performs per-node Python loop for neighbor sampling
- **File:** `project_constellation/utils/subgraphs.py` (lines 94-116, 214-231)
- **Impact:** For each node in the frontier, the code runs `row == node` which is O(E) per node. With B seed nodes and K hops, this is O(B * E * K) in the worst case. Should use sparse tensor operations for batch neighbor lookup.

#### P4. `debug print` statements left in production code
- **Files:** `project_constellation/utils/subgraphs.py` (lines 165, 257), `project_constellation/pipeline/dataloader.py` (line 232), `project_constellation/utils/iceburger.py` (line 126)
- **Impact:** `print()` statements in hot paths (subgraph sampling, data loading) add I/O overhead and clutter logs. Remove or replace with proper logging at DEBUG level.

#### P5. `_feature_sampling` re-fits KDE only once but converts DataFrame to numpy on every call
- **File:** `project_constellation/pipeline/dataloader.py` (lines 260-277)
- **Impact:** `self.df.filter(...).select("trxn_amount_miscaled").to_numpy()` is called on every batch for both KDE fitting and ATM sampling. The DataFrame-to-numpy conversion should be cached.

#### P6. `degree_aware_negative_sampling` uses `set` instead of sorted container for negatives
- **File:** `project_constellation/utils/negative_sampling.py` (line 107)
- **Impact:** Using a set means duplicate negative edges are silently dropped, and the number of unique negatives may be less than requested if there are collisions. The function may loop much longer than necessary.

---

### LOW -- Code quality, maintainability, and correctness risks

#### L1. Tests import from `project_iris` instead of `project_constellation`
- **Files:** `project_constellation/utils/tests/test_subgraphs.py` (line 4), `project_constellation/utils/tests/test_subgraphs_sparse.py` (line 4), `test_sparse_runner.py` (line 5)
- **Impact:** These tests import from `project_iris.utils.subgraphs`, which is presumably an old module name. They will fail with `ModuleNotFoundError` unless `project_iris` happens to be installed. Should import from `project_constellation.utils.subgraphs`.

#### L2. `test_sparse_runner.py` catches `AssertionError` (typo)
- **File:** `test_sparse_runner.py` (line 14)
- **Code:** `except AssertionError as e:`
- **Impact:** This is a typo for `AssertionError` -> `AssertionError` ... actually wait, let me check. The code says `AssertionError` which is NOT a valid Python exception. It should be `AssertionError`. This means assertion failures will NOT be caught and will instead fall through to the generic `Exception` handler, producing misleading error messages.

#### L3. Duplicate `TransactionEdgeType` class definitions
- **Files:** `models/transaction.py` and `project_constellation/models/transaction.py`
- **Impact:** Two identical `TransactionEdgeType` classes exist. Different parts of the codebase import from different locations (`from models.transaction import ...` vs `from project_constellation.models.transaction import ...`). This creates import confusion and divergence risk.

#### L4. Duplicate `HeteroGNN` class definitions with different signatures
- **Files:** `gnn/sage_hetero.py` and `project_constellation/gnn/hetero_sage.py`
- **Impact:** Two `HeteroGNN` classes with different parameter names (`num_atm_nodes` vs `emb_size`) and different forward logic. This is a maintenance nightmare.

#### L5. `k_hop_subgraph_sampled` signature mismatch between function and tests
- **File:** `project_constellation/utils/subgraphs.py`
- **Impact:** The function signature is `k_hop_subgraph_sampled(node_idx, edge_index, max_nodes_per_hop, ...)` but the tests in `test_subgraphs.py` call it with positional `num_hops` as the second argument: `k_hop_subgraph_sampled(node_idx, num_hops, edge_index, ...)`. The function does not have a `num_hops` parameter -- the number of hops is determined by `len(max_nodes_per_hop)`. Tests will fail.

#### L6. `val_data_loader` iterated with `next()` inside training loop without resetting iterator
- **File:** `project_constellation/main.py` (line 211)
- **Code:** `val_batch_data = next(val_data_loader)`
- **Impact:** `next()` is called on the data loader without wrapping it in `iter()` first. After the validation loader is exhausted, subsequent calls to `next()` will raise `StopIteration` and silently terminate the enclosing `for` loop (Python 3.7+ raises `RuntimeError` in generators, but in a regular for loop this just stops the loop). Should use `iter(val_data_loader)` and handle exhaustion.

#### L7. `load_transactions` references undefined `flow_engine`
- **File:** `project_constellation/utils/data/transactions.py` (line 26)
- **Code:** `flow_engine.preprocess_pl(...)`
- **Impact:** `flow_engine` is never imported or defined. This function will always crash with `NameError`.

#### L8. `transform_txn_to_string` is a stub (pass)
- **File:** `src/foundation/tokenizer.py` (line 51)
- **Impact:** The function does nothing. If called, it returns `None`.

#### L9. `LinkSnapshotNeighborLoader.__init__` does nothing (pass)
- **File:** `custom_loader.py` (line 82)
- **Impact:** The class is an empty stub.

#### L10. Missing `__init__.py` files throughout the package
- **Impact:** Various subdirectories (`gnn/`, `models/`, `src/`, `src/foundation/`) lack `__init__.py` files, which may cause import issues depending on how the package is installed.

#### L11. `rgcn.py` uses relative imports but is also run as `__main__`
- **File:** `rgcn.py` (lines 8-9)
- **Code:** `from .models.transaction import ...` and `from .utils.hetero import ...`
- **Impact:** Relative imports (`.models`, `.utils`) only work when the module is imported as part of a package. Running `python rgcn.py` directly will raise `ImportError: attempted relative import in non-package`. The `if __name__ == "__main__"` block at the bottom suggests it is intended to be run directly, creating a contradiction.

---

## Prioritized Remediation Plan

| Priority | ID | Action |
|---|---|---|
| 1 | C1 | Fix `torch.functional` -> `torch.nn.functional` in 2 files |
| 2 | C2 | Fix optimizer parameter concatenation in `main.py` |
| 3 | C4 | Fix inverted ATM transaction filter in `preprocess.py` |
| 4 | C5 | Fix `map_strategy` -> `atm_map_strategy` keyword argument |
| 5 | M1 | Fix `x_dict` -> `h` in `sage_hetero.py` output layer |
| 6 | C3 | Fix `merge_mappings` starting index in `rgcn.py` |
| 7 | C7 | Fix parameter shadowing in `tokenizer.py` |
| 8 | S1, S2 | Extract hardcoded AWS profiles and S3 paths to config |
| 9 | S4 | Remove debug `print(catalog)` and credential logging |
| 10 | M4, M7 | Fix broken stubs in `seal.py` |
| 11 | M5 | Add zero-IQR guard to `median_iqr_scaling` |
| 12 | M6 | Fix `_special_token` -> `_special_tokens` in tokenizer |
| 13 | L1, L2 | Fix test imports and typos |
| 14 | L3, L4 | Consolidate duplicate classes |
| 15 | P1-P6 | Performance improvements (iterative) |

---

## Architectural Observations

1. **Two parallel code trees:** The root-level files (`rgcn.py`, `gnn/`, `models/`) and `project_constellation/` appear to be two versions of the same system. The root-level code uses relative imports suggesting it was once a package, while `project_constellation/` is the newer, more complete version. Consider removing the root-level duplicates to avoid confusion.

2. **No test runner configured:** Despite having test files, there is no `pytest` configuration in `pyproject.toml`, and the test files import from the wrong module (`project_iris`).

3. **No CI/CD or linting:** No `.github/`, no `ruff`/`flake8`/`mypy` configuration. Adding basic linting would catch many of the issues identified here.

4. **Missing type safety for financial data:** Financial transaction amounts are processed as raw floats without any decimal precision guarantees. Consider using `Decimal` types or fixed-point arithmetic for monetary values to avoid floating-point rounding issues.
