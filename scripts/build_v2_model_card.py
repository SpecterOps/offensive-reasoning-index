#!/usr/bin/env python3
"""Build a public-safe V29 model card from a completed V2 campaign."""

from ori.eval.v2.model_card import ModelCardBuildError, build_model_card, main

__all__ = ["ModelCardBuildError", "build_model_card", "main"]


if __name__ == "__main__":
    main()
