# WP13 Review Checklist

- `shadow_only` path never calls `placeOrder`
- `paper_submit` refuses without `CPDSHADOW_ENABLE_PAPER_SUBMIT=1`
- account allowlist and paper-account prefix checks are enforced
- contract resolution rejects ambiguity instead of guessing
- paper limit prices come only from local `contracts_daily` references
- `broker_positions_snapshot` and `broker_open_orders_snapshot` are persisted
- broker callbacks are captured and reconciled deterministically
- `order_intents` carry broker enrichment fields
- `qa_report.json` and `qa_report.md` are written under the broker workspace
- no live submit mode is exposed in config or CLI
