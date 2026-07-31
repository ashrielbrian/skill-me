# settlement

Runs once per day at 02:00 UTC over ~1.2M order rows.

## Layout

- `cron.py` — entry point; sleeps until the run hour, then executes one
  settlement pass.
- `payouts/` — per-seller rollup, fee schedule, ledger writes, and the
  bank export file.
- `reports/` — nightly report assembly, table rendering, delivery, and
  retention.
- `banking.py` — transfer submission to the banking partner.
- `reconcile.py` — ledger vs bank statement comparison.
- `windows.py`, `holdbacks.py` — day-window math and risk holdbacks.

## Run order

1. Load the previous day's settled orders.
2. Roll up totals per seller and merge manual adjustments.
3. Apply the fee schedule and write the payout ledger in batches; each
   batch is followed by one audit record.
4. Write the bank export file and submit the transfer batch.
5. Render the nightly report, deliver it, and store a copy.
6. Reconcile ledger totals against the bank statement.

## Operations

The process stays resident between runs. A full pass finishes well
inside the nightly window; results land in the payout ledger, the
export spool directory, and the report endpoint.
