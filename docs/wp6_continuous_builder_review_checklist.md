# WP6 Continuous Builder Review Checklist

- `continuous_daily` rows are keyed by `(series_id, as_of_date, root)`.
- The builder never uses vendor continuous prices or broker-facing identifiers.
- `adj_factor(d)` only includes roll ratios with `effective_date > d`.
- The roll effective date keeps the new contract unadjusted.
- The canonical single-roll case produces `return(D4)=206/204-1`.
- Future roll events outside the requested end date do not affect earlier factors.
- Missing or nonpositive roll ratios fail in strict mode.
- Missing raw prices produce unusable rows with explicit quality flags.
- `close_fallback` rows are tagged explicitly.
- QA artifacts are written as both JSON and Markdown.
- Normal tests run offline with synthetic fixtures only.
