"""Validação da IA sem brokers: python -m app.ai_cli generate|train."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from app.core.datagen import DatasetConfig, generate_dataset
from app.core.training import TrainingConfig, train_models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("output", type=Path)
    generate.add_argument("--pumps", type=int, default=100)
    generate.add_argument("--days", type=int, default=7)
    generate.add_argument("--seed", type=int, default=42)
    generate.add_argument("--failure-fraction", type=float, default=0.3)
    train = commands.add_parser("train")
    train.add_argument("dataset", type=Path)
    train.add_argument("output", type=Path)
    train.add_argument("--version", required=True)
    args = parser.parse_args()
    started = perf_counter()
    if args.command == "generate":
        result = generate_dataset(DatasetConfig(num_pumps=args.pumps, days=args.days,
                                               seed=args.seed, failure_fraction=args.failure_fraction), args.output)
    else:
        result = train_models(args.dataset, args.output, args.version, TrainingConfig())
    result["elapsed_seconds"] = round(perf_counter() - started, 3)
    # JSON serve também de evidência reproduzível para a apresentação.
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
