from pathlib import Path
from typing import List

import torch
from diffusers import StableDiffusionPipeline

from pipeline import Pipeline, Variable, entity, pipe
from pipeline.cloud import compute_requirements, environments, pipelines
from pipeline.objects import File
from pipeline.objects.graph import InputField, InputSchema


class ModelKwargs(InputSchema):
    height: int | None = InputField(
        title="Height",
        default=512,
        ge=64,
        le=1024,
        multiple_of=8,
    )
    width: int | None = InputField(
        title="Width",
        default=512,
        ge=64,
        le=1024,
        multiple_of=8,
    )
    num_inference_steps: int | None = InputField(
        title="Number of inference steps",
        default=25,
    )
    num_images_per_prompt: int | None = InputField(
        default=1, title="Number of images", ge=1, le=4
    )
    guidance_scale: float | None = InputField(
        default=7.5,
        title="Guidance scale",
        ge=0.0,
        le=20.0,
        # multiple_of=0.1,
    )

    allow_nsfw: bool | None = InputField(
        default=True,
        title="Allow NSFW",
    )


@entity
class StableDiffusionModel:
    @pipe(on_startup=True, run_once=True)
    def load(self):
        model_id = "runwayml/stable-diffusion-v1-5"
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.pipe = StableDiffusionPipeline.from_pretrained(
            model_id,
        )
        self.pipe = self.pipe.to(device)

        def disabled_safety_checker(images, clip_input):
            if len(images.shape) == 4:
                num_images = images.shape[0]
                return images, [False] * num_images
            else:
                return images, False

        self.no_filter = disabled_safety_checker
        self.nsfw_filter = self.pipe.safety_checker

    @pipe
    def predict(self, prompt: str, kwargs: ModelKwargs) -> List[File]:
        defaults = kwargs.to_dict()
        del defaults["allow_nsfw"]

        if kwargs.allow_nsfw:
            self.pipe.safety_checker = self.no_filter

        images = self.pipe(prompt, **defaults).images

        output_images = []
        for i, image in enumerate(images):
            path = Path(f"/tmp/sd/image-{i}.jpg")
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(path))
            output_images.append(File(path=path, allow_out_of_context_creation=True))

        return output_images


with Pipeline() as builder:
    prompt = Variable(
        str,
        title="Prompt",
    )
    kwargs = Variable(
        ModelKwargs,
        title="Model kwargs",
    )

    model = StableDiffusionModel()

    model.load()

    output = model.predict(prompt, kwargs)

    builder.output(output)

my_pl = builder.get_pipeline()

try:
    environments.create_environment(
        "runwayml/stable-diffusion",
        python_requirements=[
            "torch==2.0.1",
            "transformers==4.30.2",
            "diffusers==0.19.3",
            "accelerate==0.21.0",
            "xformers==0.0.21",
        ],
    )
except Exception:
    pass


pipelines.upload_pipeline(
    my_pl,
    "runwayml/stable-diffusion-v1-5",
    environment_id_or_name="runwayml/stable-diffusion",
    required_gpu_vram_mb=10_000,
    accelerators=[
        compute_requirements.Accelerator.nvidia_a100,
    ],
)
