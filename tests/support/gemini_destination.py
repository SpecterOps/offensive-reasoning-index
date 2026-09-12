from __future__ import annotations

from types import SimpleNamespace

from ori.eval.v2 import campaign_runner as runner
from ori.eval.v2.campaign_config import ResolvedV2CampaignConfig, V2CampaignConfig
from ori.eval.v2.schema import Track
from tests.support.v2_campaign import _base_provenance

MODEL = "Namespace/CaseSensitive:TAG"


def _config_payload(*, model=MODEL, model_url=None, defaults_url=None):
    return {
        "version": 2,
        "source": {"manifest": "manifest.json", "archive": "archive.zip"},
        "tracks": {
            "direct": {
                "public": "public.json",
                "oracles": "oracle.json",
                "candidates": "candidate.json",
                "live_certification": "live.json",
            }
        },
        "modes": ["direct"],
        "output_dir": "campaign",
        "defaults": {"model_base_url": defaults_url},
        "models": [
            {"name": "synthetic", "provider": "gemini", "model": model, "model_base_url": model_url}
        ],
    }


def _resolved(s, *, model=MODEL, model_url=None, defaults_url=None, source="a" * 64):
    for name in ("manifest.json", "archive.zip"):
        (s.root / name).touch(exist_ok=True)
    return ResolvedV2CampaignConfig(
        source_manifest=s.root / "manifest.json",
        archive=s.root / "archive.zip",
        tracks={},
        output_dir=s.root / "campaign",
        config=V2CampaignConfig.model_validate(
            _config_payload(model=model, model_url=model_url, defaults_url=defaults_url)
        ),
        source_config_fingerprint=source,
        mcp_dir=None,
    )


def _provenance(s, resolved):
    s.patch.setattr(runner, "build_run_provenance", lambda *args: _base_provenance(Track.DIRECT))
    return runner._provenance(
        resolved=resolved,
        prepared=SimpleNamespace(
            track=Track.DIRECT,
            pair=object(),
            profile=object(),
            task_ids=("synthetic-provenance-task",),
            release=SimpleNamespace(release_fingerprint="b" * 64),
            live=SimpleNamespace(artifact_fingerprint="c" * 64),
        ),
        model=resolved.config.models[0],
        run_index=1,
        loop=None,
    )
