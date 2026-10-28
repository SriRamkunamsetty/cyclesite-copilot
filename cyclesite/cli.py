"""Command line: list cities, build a city's tables, or run the offline demo.

cyclesite cities
cyclesite build-city nyc --dry-run            # estimate cost only
cyclesite build-city nyc --max-gb 600
cyclesite build-city pune --shops-csv my_shops.csv
cyclesite demo                                # no cloud account needed (synthetic data)
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
from types import SimpleNamespace

from .build import BuildAborted, CityBuilder
from .cities import load_cities
from .config import Settings


def _cmd_cities(_: argparse.Namespace) -> int:
    for spec in load_cities().values():
        print(f"{spec.city_id:8} {spec.demand_kind:9} {spec.name}")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    cities = load_cities(args.config)
    if args.city not in cities:
        print(f"Unknown city '{args.city}'. Known: {', '.join(cities)}", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    if args.project:
        settings = dataclasses.replace(settings, project_id=args.project)
    if args.dataset:
        settings = dataclasses.replace(settings, dataset=args.dataset)
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.project_id, location=settings.location)
    builder = CityBuilder(client, settings, cities[args.city], max_bytes_osm=int(args.max_gb * 1e9))
    try:
        report = builder.build(dry_run=args.dry_run, shops_csv=args.shops_csv)
    except BuildAborted as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 3
    print(report.summary())
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    """Run the same tools the agent uses, on synthetic data, and print the decision memo."""
    os.environ["CYCLESITE_BACKEND"] = "synthetic"
    from cycle_agent import services, tools

    services.reset_caches()
    ctx = SimpleNamespace(state={})
    result = tools.choose_sites(args.city, ctx, k=args.k)
    if result["status"] == "needs_confirmation":
        print(result["message"])
        result = tools.choose_sites(args.city, ctx, k=args.k, acknowledge_stale_data=True)
    if result["status"] != "ok":
        print(result.get("message", result), file=sys.stderr)
        return 1
    tools.run_backtest(args.city, ctx)
    tools.red_team_picks(ctx)
    print(tools.build_decision_memo(ctx, question="Demo run on synthetic data")["memo_markdown"])
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="cyclesite", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("cities", help="list configured cities").set_defaults(func=_cmd_cities)

    b = sub.add_parser("build-city", help="build one city's tables in BigQuery")
    b.add_argument("city")
    b.add_argument("--config", help="path to a cities.yaml (default: the packaged one)")
    b.add_argument("--project", help="Google Cloud project (default: GOOGLE_CLOUD_PROJECT)")
    b.add_argument("--dataset", help="our BigQuery dataset (default: CYCLESITE_DATASET)")
    b.add_argument(
        "--max-gb",
        type=float,
        default=300.0,
        help="refuse the OpenStreetMap extract if its dry run exceeds this many GB",
    )
    b.add_argument("--shops-csv", help="CSV with columns name,lat,lon of existing shops")
    b.add_argument("--dry-run", action="store_true", help="estimate scan sizes, change nothing")
    b.set_defaults(func=_cmd_build)

    d = sub.add_parser("demo", help="offline demo on synthetic data (no cloud account)")
    d.add_argument("--city", default="demo_a")
    d.add_argument("-k", type=int, default=3)
    d.set_defaults(func=_cmd_demo)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
