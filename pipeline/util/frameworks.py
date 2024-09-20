import typing as t


def get_cog_image_name(pipeline_name) -> str:
    """Use consistent name for cog images"""
    return f"{pipeline_name}-cog"


def is_using_cog(pipeline_extras: dict[str, t.Any] | None) -> bool:
    """Check if pipeline is wrapping a Cog model"""
    extras = pipeline_extras or {}
    try:
        is_using_cog = extras.get("model_framework", {}).get("framework") == "cog"
    except Exception:
        is_using_cog = False
    return is_using_cog
