def get_cog_image_name(pipeline_name) -> str:
    """Use consistent name for cog images"""
    return f"{pipeline_name}--cog"
