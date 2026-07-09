from __future__ import annotations

import json
import logging
from pathlib import Path

import click

from clinicaltrials_parser.client import ClinicalTrialsClient
from clinicaltrials_parser.parser import StudyParser
from clinicaltrials_parser.storage import StorageWriter

logger = logging.getLogger(__name__)


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase verbosity")
@click.version_option(version="0.1.0", prog_name="ctgov-parser")
def main(verbose: int) -> None:
    level = logging.WARNING
    if verbose >= 2:
        level = logging.DEBUG
    elif verbose >= 1:
        level = logging.INFO
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=level,
    )


@main.command()
@click.option("-o", "--output", type=click.Path(), default="studies.jsonl", show_default=True)
@click.option(
    "-f", "--format",
    type=click.Choice(["jsonl", "json", "csv", "parquet", "duckdb", "iceberg"]),
    default="jsonl", show_default=True,
)
@click.option("--table", default="studies", help="DuckDB table name or Iceberg table name")
@click.option("--compression", type=click.Choice(["snappy", "zstd", "gzip", "lz4", "brotli"]), default="snappy", show_default=True,
              help="Parquet compression codec (parquet/iceberg formats)")
@click.option("--batch-size", type=int, default=10000, show_default=True,
              help="Records per batch for streaming writes (parquet/duckdb/iceberg)")
@click.option("--page-size", type=int, default=100, show_default=True)
@click.option("--max-studies", type=int, default=None, help="Stop after N studies")
@click.option("--rate-limit", type=float, default=10, show_default=True)
@click.option("--fields", type=str, default=None, help="Comma-separated field list")
@click.option("--flat/--no-flat", default=True, help="Flatten nested structure")
@click.option("--resume", type=click.Path(), default=None, help="Resume state file")
@click.option("--query-term", type=str, default=None, help="Full-text search term")
@click.option("--query-cond", type=str, default=None, help="Condition filter")
@click.option("--query-intr", type=str, default=None, help="Intervention filter")
@click.option("--query-spons", type=str, default=None, help="Sponsor filter")
@click.option("--query-lead", type=str, default=None, help="Lead sponsor filter")
@click.option(
    "--status", type=str, default=None,
    help="Comma-separated statuses: RECRUITING, COMPLETED, etc.",
)
@click.option(
    "--phase", type=str, default=None,
    help="Comma-separated phases: PHASE1, PHASE2, PHASE3, PHASE4, EARLY_PHASE1, NA",
)
@click.option(
    "--study-type", type=click.Choice(["INTERVENTIONAL", "OBSERVATIONAL", "EXPANDED_ACCESS"]),
    default=None,
)
def fetch(
    output: str,
    format: str,
    table: str,
    compression: str,
    batch_size: int,
    page_size: int,
    max_studies: int | None,
    rate_limit: float,
    fields: str | None,
    flat: bool,
    resume: str | None,
    query_term: str | None,
    query_cond: str | None,
    query_intr: str | None,
    query_spons: str | None,
    query_lead: str | None,
    status: str | None,
    phase: str | None,
    study_type: str | None,
) -> None:
    client = ClinicalTrialsClient(page_size=page_size, rate_limit=rate_limit)
    storage = StorageWriter(fmt=format, batch_size=batch_size, compression=compression)
    if format in ("duckdb", "iceberg"):
        storage.table = table

    parser = StudyParser(client=client, storage=storage, resume_file=resume)

    query_params: dict = {}
    if query_term:
        query_params["query.term"] = query_term
    if query_cond:
        query_params["query.cond"] = query_cond
    if query_intr:
        query_params["query.intr"] = query_intr
    if query_spons:
        query_params["query.spons"] = query_spons
    if query_lead:
        query_params["query.lead"] = query_lead
    if status:
        query_params["filter.overallStatus"] = [s.strip() for s in status.split(",")]
    if phase:
        query_params["filter.phase"] = [p.strip() for p in phase.split(",")]
    if study_type:
        query_params["filter.studyType"] = study_type

    if format == "iceberg":
        click.echo(f"Creating Iceberg warehouse at {Path(output).resolve()}")
        click.echo(f"Iceberg table: {table}, Compression: {compression}")
    else:
        click.echo(f"Fetching studies -> {output} ({format})")
    click.echo(f"Query params: {query_params or '(none — all studies)'}")

    out_path = Path(output)

    parser.to_storage(
        output_path=out_path,
        fmt=format,
        max_studies=max_studies,
        fields=fields,
        flat=flat,
        **query_params,
    )
    click.echo(f"Done. Output: {out_path.resolve()}")


@main.command()
@click.option("--page-size", type=int, default=100)
@click.option("--query-cond", type=str, default=None)
@click.option("--status", type=str, default=None)
@click.option("--study-type", type=str, default=None)
def stats(
    page_size: int,
    query_cond: str | None,
    status: str | None,
    study_type: str | None,
) -> None:
    client = ClinicalTrialsClient(page_size=page_size)
    params: dict = {}
    if query_cond:
        params["query.cond"] = query_cond
    if status:
        params["filter.overallStatus"] = [s.strip() for s in status.split(",")]
    if study_type:
        params["filter.studyType"] = study_type
    total = client.get_total_count(**params)
    click.echo(f"Total studies matching filters: {total:,}")


@main.command()
def fields() -> None:
    client = ClinicalTrialsClient()
    metadata = client.get_field_metadata()
    click.echo(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
