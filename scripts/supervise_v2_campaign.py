#!/usr/bin/env python3
"""Supervise one exact ORI V2 campaign under restart and token bounds."""

from __future__ import annotations

import argparse
from pathlib import Path

from ori.eval.v2.campaign_supervisor import (
    CampaignSupervisorError,
    supervise_v2_campaign,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--token-ceiling", type=int, required=True)
    parser.add_argument("--max-restarts", type=int, required=True)
    parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
    parser.add_argument(
        "--ori-executable",
        required=True,
        help="Absolute path to the approved ORI console executable.",
    )
    parser.add_argument(
        "--execute-approved",
        action="store_true",
        help="Explicitly authorize paid execution and execution recovery.",
    )
    parser.add_argument("--model-card-output", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--display-name")
    args = parser.parse_args()
    try:
        decision = supervise_v2_campaign(
            config_path=args.config,
            state_path=args.state,
            token_ceiling=args.token_ceiling,
            max_restarts=args.max_restarts,
            execute_approved=args.execute_approved,
            poll_interval_seconds=args.poll_interval_seconds,
            ori_executable=args.ori_executable,
            model_card_output=args.model_card_output,
            model=args.model,
            display_name=args.display_name,
        )
    except CampaignSupervisorError as exc:
        parser.exit(1, f"supervisor stopped: {exc}\n")
    if decision.action == "await_execution_approval":
        parser.exit(2, "paid execution requires --execute-approved\n")
    if decision.action == "stop":
        parser.exit(1, f"supervisor stopped: {decision.reason}\n")
    print(f"supervisor: {decision.reason}")


if __name__ == "__main__":
    main()
