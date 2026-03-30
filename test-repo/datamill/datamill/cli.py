"""CLI entry point."""
import click
import sys
from .processor import process_file, aggregate_file
from .enricher import enrich_file
from .config import load_config


@click.group()
@click.version_option()
def main():
    """datamill - process and transform datasets."""
    pass


@main.command()
@click.argument("input_file")
@click.option("--filter", "filter_expr", help="Filter expression (e.g., 'age > 30')")
@click.option("--output", "-o", help="Output file path")
@click.option("--format", "fmt", default="csv", help="Output format (csv, json, parquet)")
def process(input_file, filter_expr, output, fmt):
    """Process and filter a dataset."""
    try:
        result = process_file(input_file, filter_expr=filter_expr, output_format=fmt)
        if output:
            result.to_csv(output) if fmt == "csv" else result.to_json(output)
            click.echo(f"Written {len(result)} rows to {output}")
        else:
            click.echo(result.to_string())
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.argument("input_file")
@click.option("--group-by", required=True, help="Column to group by")
@click.option("--sum", "sum_col", help="Column to sum")
@click.option("--mean", "mean_col", help="Column to average")
@click.option("--output", "-o", help="Output file path")
def aggregate(input_file, group_by, sum_col, mean_col, output):
    """Aggregate a dataset by grouping."""
    result = aggregate_file(input_file, group_by, sum_col=sum_col, mean_col=mean_col)
    if output:
        result.to_csv(output)
    else:
        click.echo(result.to_string())


@main.command()
@click.argument("input_file")
@click.option("--api-key", envvar="ENRICHMENT_API_KEY", help="API key for enrichment service")
@click.option("--output", "-o", required=True, help="Output file path")
@click.option("--config", "config_path", help="Config file path")
def enrich(input_file, api_key, output, config_path):
    """Enrich a dataset using an external API."""
    config = load_config(config_path) if config_path else {}
    result = enrich_file(input_file, api_key, config=config)
    result.to_csv(output, index=False)
    click.echo(f"Enriched {len(result)} rows -> {output}")


if __name__ == "__main__":
    main()
