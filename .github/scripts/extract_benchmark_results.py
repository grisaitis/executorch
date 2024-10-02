#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.


import json
from typing import Any


def parse_args() -> Any:
    from argparse import ArgumentParser

    parser = ArgumentParser(
        "extract Android benchmark results from AWS Device Farm artifacts"
    )
    parser.add_argument(
        "--artifacts",
        type=str,
        required=True,
        help="the list of artifacts from AWS in JSON format",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    json.loads(args.artifacts)


if __name__ == "__main__":
    main()
