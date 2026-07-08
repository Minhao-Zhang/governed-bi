---
skill_id: skill_beer_factory_routing
db: beer_factory
kind: routing
provenance: { source: curator, status: draft }
---

# Beer factory: routing & gotchas

## Scope
Sales, customers, root beer brands, and reviews for a root beer factory.
`transaction` is the sales fact table; `rootbeer` is the unit dimension, which
rolls up to `rootbeerbrand`.

## Routing triggers
- Revenue / sales questions use `metric_revenue` (`SUM(PurchasePrice)` on
  `tbl_beer_factory_transaction`). To break revenue down by brand, join
  transaction to rootbeer (`join_transaction_rootbeer`) then rootbeer to
  rootbeerbrand (`join_rootbeer_rootbeerbrand`).
- Rating / review-quality questions use `metric_avg_rating`
  (`AVG(StarRating)` on `tbl_beer_factory_rootbeerreview`); join to
  `tbl_beer_factory_rootbeerbrand` via `join_review_rootbeerbrand`.

## Gotchas
- Ingredient and availability flags on `rootbeerbrand` are the strings
  `'TRUE'`/`'FALSE'`, not integers (see `rule_boolean_flags`). Filter with
  `= 'TRUE'`.
- `customers.ZipCode` is an INTEGER, so leading zeros are lost; do not use it as
  a postal key (see its reliability caveat).
- `transaction.CreditCardNumber` is PII and is excluded; never select it.
