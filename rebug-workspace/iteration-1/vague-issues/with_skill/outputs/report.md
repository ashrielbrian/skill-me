# Issue Validation Report

## Summary

3 concerns were reviewed. 1 is partially valid (hardcoded AWS credentials/S3 paths), 1 is disputed (resource leaks in custom_loader.py), and 1 is confirmed with additional bugs discovered (training loop performance issues in main.py). The original concerns were a mix of legitimate worries and overthinking, but the investigation uncovered several concrete bugs the original concerns did not identify.

## Detailed Findings

### Issue: Hardcoded AWS credentials or S3 paths in the source code

- **Original severity**: Not specified (implied security concern)
- **Verdict**: Partially valid
- **Investigation**:
  There are **no hardcoded AWS credentials** (no access keys, secret keys, or tokens) anywhere in the source code. The grep for patterns like `AKIA` (AWS access key prefix) turned up nothing. The `iceburger.py` file (`/Users/brian.tang/Documents/graph-dl/project_constellation/utils/iceburger.py`, lines 59-60) contains `"YOUR_KEY"` and `"YOUR_SECRET"` but these are docstring/example placeholders, not real credentials. The actual credential flow in that file uses `boto3.Session().get_credentials()` to obtain temporary IAM role credentials at runtime (lines 89-91, 178-180, 251, 304), which is the correct approach.

  However, there **are** hardcoded S3 paths and an AWS profile name:
  - `/Users/brian.tang/Documents/graph-dl/project_constellation/main.py` line 3: `os.environ["AWS_PROFILE"] = "paysec-prod-admin"` -- sets a production admin profile name directly in source code.
  - `/Users/brian.tang/Documents/graph-dl/project_constellation/main.py` lines 239-240: `S3_KEY = "s3://fra-prod-transformed-data/transactions/date=2025-02-02/"` and similar for validation data.
  - `/Users/brian.tang/Documents/graph-dl/profile_scan.py` line 23: Same `AWS_PROFILE` setting.
  - `/Users/brian.tang/Documents/graph-dl/rgcn.py` lines 17, 20, 375, 378, 381, 411: Multiple hardcoded S3 paths to production data.

  The S3 paths themselves are not a direct security vulnerability since they require valid AWS credentials to access. The `AWS_PROFILE = "paysec-prod-admin"` is more concerning: it forces the production admin profile when the module is imported, which could lead to unintended production data access. This is in the `__main__` block for main.py but is at module-level (line 3), meaning it executes on import, not just on direct execution.

  The `.gitignore` does not exclude `.env` files, and there is no `.env` file present, so configuration is not managed through environment files either.

- **Fix assessment**: No fix was proposed. For the S3 paths: since this is a research/experimentation codebase (evidenced by notebooks, TODO comments, and the overall structure), hardcoded S3 paths in `__main__` blocks are a low-severity code smell, not a security issue. The `AWS_PROFILE` at module level (line 3 of main.py) should be moved inside the `if __name__ == "__main__":` block to prevent it from affecting imports.

### Issue: Resource leaks in custom_loader.py data loading pipeline

- **Original severity**: Not specified (implied reliability concern)
- **Verdict**: Disputed
- **Investigation**:
  I read the entire `custom_loader.py` file (`/Users/brian.tang/Documents/graph-dl/custom_loader.py`, 592 lines). This file contains three classes: `LinkSnapshotNeighborLoader` (stub, empty `__init__`), `CustomNeighborLoader`, and `CustomLinkNeighborLoader`.

  None of these classes open files, database connections, network sockets, or any other external resources that would need explicit cleanup. The data flow is:
  1. A `HeteroData` object (PyTorch Geometric in-memory graph) is passed into the constructor (line 81, 93).
  2. An adjacency dictionary is built in memory from the graph's edge indices (`_build_adjacency_dict`, lines 147-184).
  3. Batches are created as lists of tensor slices (`_create_batches`, lines 186-195).
  4. Iteration yields subgraphs built from in-memory data (`_build_subgraph`, lines 300-439).

  All data structures are Python dicts, lists, and PyTorch tensors that are managed by Python's garbage collector and PyTorch's memory management. There are no file handles, no `open()` calls, no database cursors, no streaming connections.

  The `_build_adjacency_dict` method (lines 147-184) does iterate over all edges and build Python dicts of lists, which can be memory-intensive for large graphs, but this is a memory usage concern, not a resource leak. The memory is properly referenced by `self.adj_dict` and will be freed when the loader is garbage collected.

  The actual production data loader used in the training pipeline is `TransactionsLinkNeighbourLoader` (`/Users/brian.tang/Documents/graph-dl/project_constellation/pipeline/dataloader.py`), not `CustomNeighborLoader`. This loader also has no resource leaks -- it operates entirely on in-memory `HeteroData` and Polars DataFrames.

- **Fix assessment**: No fix needed. There are no resource leaks.

### Issue: Performance issues in GNN training loop with large graph data

- **Original severity**: Not specified (implied performance concern)
- **Verdict**: Confirmed, plus additional bugs discovered
- **Investigation**:
  The training loop in `/Users/brian.tang/Documents/graph-dl/project_constellation/main.py` (lines 171-219) has several real issues, some of which are outright bugs rather than performance concerns:

  **Bug 1 -- Wrong import: `torch.functional` vs `torch.nn.functional` (line 10)**
  The file imports `import torch.functional as F` instead of `import torch.nn.functional as F`. The module `torch.functional` is a different, internal module that does NOT contain `binary_cross_entropy_with_logits`. Line 199 calls `F.binary_cross_entropy_with_logits(...)`, which will raise an `AttributeError` at runtime. This means the training loop has never actually been run successfully as-is. The same wrong import exists in `project_constellation/utils/utils.py` line 2, though in that file it is also used in the same way (line 31) and would similarly fail.

  **Bug 2 -- `parameters()` concatenation (line 283)**
  ```python
  optimizer = torch.optim.Adam(
      list(gnn.parameters() + decoder.parameters()), lr=LEARNING_RATE
  )
  ```
  `gnn.parameters()` and `decoder.parameters()` return generators. You cannot use `+` to concatenate generators in Python -- this will raise a `TypeError: unsupported operand type(s) for +: 'generator' and 'generator'`. The correct form is `list(gnn.parameters()) + list(decoder.parameters())`, which is exactly what the `diagnose_zero_gradients` function in `utils.py` (line 10) does correctly.

  **Bug 3 -- Validation iterator exhaustion (line 211)**
  ```python
  val_batch_data = next(val_data_loader)
  ```
  This calls `next()` on the validation data loader inside the inner training batch loop, without calling `iter()` first and without handling `StopIteration`. The `TransactionsLinkNeighbourLoader.__next__` (dataloader.py line 309) raises `StopIteration` when `current_index >= len(epoch_seeds)`. Since the validation loader is iterated once per training batch (not once per epoch), it will exhaust quickly and crash with `StopIteration` (or silently end the loop in Python 3.7+ if caught by the for-loop machinery). There is no re-initialization of the validation loader between epochs either.

  **Bug 4 -- Batch data not moved to device (line 190)**
  The model and decoder are moved to the device (line 182: `model, decoder = model.to(device), decoder.to(device)`), but `batch_data` from the data loader is never moved to the device. If `device` is anything other than CPU (e.g., CUDA), line 190 `model(batch_data.x_dict, batch_data.edge_index_dict)` will fail because the input tensors are on CPU while the model parameters are on GPU.

  **Performance concern -- LR scheduler stepped per batch (line 214-215)**
  The `CosineAnnealingLR` scheduler is stepped after every batch, not after every epoch. With `T_max=10_000`, this means the cosine schedule completes over 10,000 batches rather than 10,000 epochs. This is not necessarily wrong (some training regimes do per-step scheduling), but given that `NUM_EPOCHS = 2` and the scheduler was configured with `T_max=10_000`, it appears the intent was per-epoch scheduling. The learning rate will barely change during training.

  **Performance concern -- Gradient clipping only on model, not decoder (line 208)**
  `clip_grad_norm_` is called only on `model.parameters()`, but not on `decoder.parameters()`. The decoder's gradients could still explode. This is a minor concern since the decoder is a single linear layer, but it is inconsistent with the apparent intent.

- **Fix assessment**: No fixes were proposed. Recommended fixes:
  1. Change line 10 to `import torch.nn.functional as F`.
  2. Change line 283 to `list(gnn.parameters()) + list(decoder.parameters())`.
  3. Properly initialize and reset the validation loader, or restructure validation to run once per epoch rather than once per batch.
  4. Add `batch_data = batch_data.to(device)` before the forward pass on line 190.
  5. Move `lr_scheduler.step()` outside the inner batch loop if per-epoch scheduling is intended.

## New Issues Discovered

### Issue: Module-level side effect from AWS_PROFILE setting
- **File**: `/Users/brian.tang/Documents/graph-dl/project_constellation/main.py`, line 3
- **Severity**: Medium
- **Description**: `os.environ["AWS_PROFILE"] = "paysec-prod-admin"` is at module level, outside the `__main__` guard. Any code that imports from this module (even just the `train` or `evaluate` functions) will silently switch the AWS profile to `paysec-prod-admin` as a side effect. This could cause other parts of a larger system to unexpectedly use production credentials.
- **Recommended fix**: Move this line inside the `if __name__ == "__main__":` block (after line 238).

### Issue: Wrong import in utils.py
- **File**: `/Users/brian.tang/Documents/graph-dl/project_constellation/utils/utils.py`, line 2
- **Severity**: High (blocks execution)
- **Description**: Same `import torch.functional as F` bug as in main.py. The `diagnose_zero_gradients` function (line 31) calls `F.binary_cross_entropy_with_logits(...)` which will fail at runtime with `AttributeError`.
- **Recommended fix**: Change to `import torch.nn.functional as F`.
