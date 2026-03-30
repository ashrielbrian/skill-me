# Validation Report: graph-dl Codebase Concerns

## Concern 1: Hardcoded AWS Credentials or S3 Paths

### Verdict: PARTIALLY VALID -- S3 paths are hardcoded, but no credentials are hardcoded

There are two distinct sub-concerns here that must be separated:

**Hardcoded AWS credentials: NOT a real problem.**

No actual AWS access keys, secret keys, or tokens are hardcoded anywhere in the source code. The codebase uses `AWS_PROFILE` environment variable injection (e.g., `os.environ["AWS_PROFILE"] = "paysec-prod-admin"`) and then relies on the boto3 credential chain to resolve temporary credentials at runtime. This is a standard AWS authentication pattern. The `iceburger.py` utility accepts `aws_access_key_id` and `aws_secret_access_key` as optional function parameters for non-IAM-role use cases, but no actual key values are written in the code. The credentials are resolved dynamically via `boto3.Session().get_credentials()`.

Files examined:
- `project_constellation/main.py` (line 3): sets `AWS_PROFILE`, not a credential
- `profile_scan.py` (line 23): sets `AWS_PROFILE`, not a credential
- `project_constellation/utils/iceburger.py`: all credential handling goes through boto3 session resolution
- `nb/train.ipynb`: sets `AWS_PROFILE` in notebook cells, credential references in notebook output cells are from boto3 session resolution at runtime

**Hardcoded S3 paths: A REAL but LOW-SEVERITY problem.**

Multiple files contain hardcoded S3 bucket paths:

| File | Lines | Hardcoded S3 Path |
|------|-------|-------------------|
| `project_constellation/main.py` | 239-240 | `s3://fra-prod-transformed-data/transactions/date=2025-02-02/` and `date=2025-02-03/` |
| `rgcn.py` | 17, 20, 375, 378, 381, 411 | Same bucket, multiple dates |

These are in `if __name__ == "__main__"` blocks, meaning they are script entry points for experimentation, not library code consumed by other modules. This is common in research/experimentation codebases. The functions themselves (`generate_split`, `get_transform_transactions`) accept S3 keys as parameters, so they are properly parameterized.

**Should you fix it?** For a production deployment, yes -- extract these to a config file or CLI arguments. For an experimental research codebase, this is acceptable. The hardcoded profile name (`paysec-prod-admin`) at the module level in `main.py` (line 3) is more concerning because it executes on import, which could cause issues in different environments. Moving it inside the `if __name__ == "__main__"` guard would be an improvement.

---

## Concern 2: Resource Leaks in custom_loader.py Data Loading Pipeline

### Verdict: NOT A REAL PROBLEM -- You are overthinking this

After thorough investigation of `custom_loader.py`, there are no resource leaks.

**Why there are no resource leaks:**

1. **No file handles are opened.** The `CustomNeighborLoader` and `CustomLinkNeighborLoader` classes operate entirely on in-memory PyTorch Geometric `HeteroData` objects. They do not open files, database connections, network sockets, or any other external resources that would require cleanup.

2. **No context managers needed.** The classes iterate over pre-loaded graph data by sampling neighborhoods. The `__iter__` / `__next__` protocol is correctly implemented with `StopIteration` raised when batches are exhausted.

3. **Memory is managed by Python's garbage collector.** The adjacency dictionaries (`self.adj_dict`) built in `_build_adjacency_dict()` are standard Python dicts that will be garbage collected when the loader goes out of scope.

**One legitimate (but unrelated) concern in `custom_loader.py`:**

The `_build_adjacency_dict()` method (lines 147-184) iterates over every edge using a Python for-loop (`for edge_idx, (src, dst) in enumerate(edge_index.t())`). For large graphs, this is extremely slow because it converts every tensor element to a Python object. This is a performance issue, not a resource leak. A vectorized approach using `torch.Tensor` operations or converting to a CSR/CSC sparse representation would be significantly faster.

**The real data loading pipeline (`project_constellation/pipeline/dataloader.py`):**

The `TransactionsLinkNeighbourLoader` is the actually-used data loader. It also has no resource leaks -- it operates on in-memory `HeteroData` and `pl.DataFrame` objects. The `self.df` reference keeps the full DataFrame in memory for the lifetime of the loader (used for KDE fitting and feature sampling), but this is intentional, not a leak.

---

## Concern 3: Performance Issues in project_constellation/main.py GNN Training Loop with Large Graph Data

### Verdict: VALID -- There are several real performance problems

The training loop in `project_constellation/main.py` (lines 171-219) has multiple genuine performance issues:

### Issue 3a: Bug in optimizer parameter grouping (line 283)

```python
optimizer = torch.optim.Adam(
    list(gnn.parameters() + decoder.parameters()), lr=LEARNING_RATE
)
```

This will raise a `TypeError` at runtime. `gnn.parameters()` and `decoder.parameters()` return generators, and you cannot use `+` on generators. The correct form is:

```python
list(gnn.parameters()) + list(decoder.parameters())
```

The older `rgcn.py` file (line 62) has the correct version. This is not a performance issue but an outright bug that would crash training.

### Issue 3b: Validation inside the training batch loop (lines 211-212)

```python
for batch_data in data_loader:
    ...
    loss.backward()
    optimizer.step()

    val_batch_data = next(val_data_loader)
    evaluate(model, decoder, val_batch_data)
```

`evaluate()` is called after every single training batch, not after each epoch. This is a significant performance problem:

1. **Wasted computation**: Running inference on every batch doubles the forward-pass cost.
2. **Validation iterator exhaustion**: `next(val_data_loader)` will exhaust the validation loader iterator during the first epoch. Subsequent calls will raise `StopIteration` or (if `__next__` is called without `__iter__` first) silently fail. There is no `iter()` call resetting the validation loader between epochs.
3. **Results are discarded**: The return value of `evaluate()` is never captured or logged, so the validation is pure waste.

### Issue 3c: Incorrect import (line 10)

```python
import torch.functional as F
```

This should be `import torch.nn.functional as F`. `torch.functional` is a different, internal module. The `F.binary_cross_entropy_with_logits` call on line 199 would fail at runtime.

### Issue 3d: GNN_HIDDEN_CHANNELS = 2048 is extremely large (line 243)

With `GNN_NUM_LAYERS = 3`, each SAGEConv layer produces 2048-dimensional hidden states for every node. Given that the model processes heterogeneous financial transaction graphs with potentially hundreds of thousands of nodes, this creates:

- Very high memory consumption per layer (nodes x 2048 floats per forward pass)
- High risk of overfitting, given the input features are only 3-dimensional (total_sent, total_received, total_withdrawn)
- Slow training due to large matrix multiplications

For comparison, `rgcn.py` uses `hidden_channels = 64` and the older `rgcn.py` training function also uses 64. A hidden dimension of 64-256 would be more appropriate for this graph structure.

### Issue 3e: LR scheduler steps per batch, not per epoch (line 215)

```python
for batch_data in data_loader:
    ...
    if lr_scheduler:
        lr_scheduler.step()
```

`CosineAnnealingLR` with `T_max=10_000` is stepped every batch. This means the learning rate will complete its cosine decay after 10,000 batches rather than 10,000 epochs. Whether this is intentional depends on the design, but with `NUM_EPOCHS = 2`, the cosine schedule will barely begin to decrease, making the scheduler effectively useless.

### Issue 3f: device="auto" is not handled (line 246, 182)

`device = "auto"` is passed to `train()`, which does `model.to(device)`. PyTorch does not recognize `"auto"` as a valid device string. This will raise a `RuntimeError`. The correct pattern is to resolve `"auto"` to an actual device first, e.g.:

```python
device = torch.device("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else torch.device(device)
```

### Issue 3g: Batch data not moved to device (line 190)

```python
out_embeddings = model(batch_data.x_dict, batch_data.edge_index_dict)
```

The model is moved to `device` but `batch_data` is never moved to the same device. If training on GPU, this will cause a device mismatch error.

---

## Summary

| Concern | Verdict | Severity |
|---------|---------|----------|
| Hardcoded AWS credentials | Not a real problem | N/A |
| Hardcoded S3 paths | Real but low severity (script entry points only) | Low |
| Resource leaks in custom_loader.py | Not a real problem | N/A |
| Performance issues in training loop | Real and significant | High |

The third concern (training loop performance) is the most serious. The training code in `project_constellation/main.py` has multiple bugs that would prevent it from running at all (`torch.functional` import, `parameters() + parameters()` TypeError, `device="auto"`), along with genuine performance issues (per-batch validation, oversized hidden dimensions, batch data not on device). The code appears to be in an early development state and has not been run end-to-end successfully.

The first two concerns are largely unfounded. The codebase does not contain hardcoded credentials, and the data loading pipeline does not have resource leaks.
