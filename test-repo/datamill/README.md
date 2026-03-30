# datamill

A CLI tool for processing and transforming CSV/JSON datasets. Supports filtering, aggregation, enrichment from external APIs, and export to multiple formats.

```bash
python -m datamill process input.csv --filter "age > 30" --output results.json
python -m datamill enrich users.csv --api-key $API_KEY --output enriched.csv
python -m datamill aggregate sales.csv --group-by region --sum revenue
```
