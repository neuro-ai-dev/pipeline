import base64
import copy
from io import BytesIO

# from pathlib import Path
from queue import Queue
from threading import Thread

import torch
from DeepCache import DeepCacheSDHelper
from diffusers import (  # AutoPipelineForImage2Image,; StableDiffusionXLPipeline,
    AutoencoderKL,
    AutoPipelineForText2Image,
    DiffusionPipeline,
    EulerDiscreteScheduler,
    UNet2DConditionModel,
)
from huggingface_hub import hf_hub_download
from safetensors.torch import load_file

from pipeline import Pipeline, Variable, entity, pipe
from pipeline.objects.graph import InputField, InputSchema
from pipeline.objects.variables import Stream


class ModelKwargs(InputSchema):
    num_images_per_prompt: int = InputField(
        title="num_images_per_prompt",
        description="The number of images to generate per prompt.",
        default=1,
        le=4,
        ge=1,
        optional=True,
        multiple_of=1,
    )
    height: int = InputField(
        title="height",
        description="The height in pixels of the generated image.",
        default=512,
        optional=True,
        multiple_of=64,
        ge=64,
        le=1024,
    )
    width: int = InputField(
        title="width",
        description="The width in pixels of the generated image.",
        default=512,
        optional=True,
        multiple_of=64,
        ge=64,
        le=1024,
    )
    num_inference_steps: int = InputField(
        title="num_inference_steps",
        description=(
            "The number of denoising steps. More denoising steps "
            "usually lead to a higher quality image at the expense "
            "of slower inference."
        ),
        default=4,
        optional=True,
        le=100,
        ge=1,
    )

    # source_image: File | None = InputField(
    #     title="source_image",
    #     description="The source image to condition the generation on.",
    #     optional=True,
    #     default=None,
    # )

    strength: float | None = InputField(
        title="strength",
        description="The strength of the new image from the input image. The lower the strength the closer to the original image the output will be.",  # noqa
        default=0.8,
        optional=True,
        ge=0.0,
        le=1.0,
    )


# Put your model inside of the below entity class
@entity
class MyModelClass:
    @pipe(run_once=True, on_startup=True)
    def load(self) -> None:
        # Perform any operations needed to load your model here
        print("Loading model...")
        base = "stabilityai/stable-diffusion-xl-base-1.0"
        repo = "ByteDance/SDXL-Lightning"
        ckpt = "sdxl_lightning_4step_unet.safetensors"
        unet = UNet2DConditionModel.from_config(base, subfolder="unet").to(
            "cuda", torch.float16
        )
        unet.load_state_dict(load_file(hf_hub_download(repo, ckpt), device="cuda"))

        self.pipeline_text2image = AutoPipelineForText2Image.from_pretrained(
            base,
            unet=unet,
            torch_dtype=torch.float16,
            variant="fp16",
            # use_safetensors=True,
        )

        self.pipeline_text2image = self.pipeline_text2image.to("cuda")
        self.pipeline_text2image.scheduler = EulerDiscreteScheduler.from_config(
            self.pipeline_text2image.scheduler.config,
            timestep_spacing="trailing",
        )

        if False:
            helper = DeepCacheSDHelper(pipe=self.pipeline_text2image)
            helper.set_params(
                cache_interval=3,
                cache_branch_id=0,
            )
            helper.enable()
        # self.pipeline_image2image = AutoPipelineForImage2Image.from_pipe(
        #     self.pipeline_text2image
        # )

        self.queue = Queue()
        self.render_vae: AutoencoderKL = copy.deepcopy(self.pipeline_text2image.vae)
        self.render_vae.to(torch.float32).to("cuda")

        print("Model loaded!")

    def callback_on_step_end(
        self,
        inference_pipeline: DiffusionPipeline,
        step: int,
        timestep: int,
        callback_kwargs: dict,
    ):
        if True:  # toggle for testing speed with no image streaming
            latents = (
                copy.deepcopy(callback_kwargs.get("latents"))
                .to(dtype=torch.float32)
                .to("cuda")
            )

            image: torch.Tensor = self.render_vae.decode(
                latents / self.render_vae.config.scaling_factor,
                return_dict=False,
            )[0]

            image = inference_pipeline.image_processor.postprocess(image)

            buffered = BytesIO()

            image[0].save(buffered, format="JPEG")
            img_str = base64.b64encode(buffered.getvalue())

            self.queue.put(img_str.decode("utf-8"))

        return callback_kwargs

    def stream_func(self):
        while True:
            value = self.queue.get(timeout=5)
            if value is None:
                raise StopIteration()
            else:
                yield value

    def run_func(self, prompt: str, kwargs: ModelKwargs):
        images = self.pipeline_text2image(
            prompt=prompt,
            guidance_scale=0.0,
            callback_on_step_end=self.callback_on_step_end,
            **kwargs.to_dict(),
        ).images

        buffered = BytesIO()
        images[0].save(buffered, format="JPEG")
        img_str = base64.b64encode(buffered.getvalue())

        self.queue.put(img_str.decode())
        self.queue.put(None)

    @pipe
    def predict(self, prompt: str, kwargs: ModelKwargs) -> Stream[list[str]]:
        # Perform any operations needed to predict with your model here
        print("Predicting...")
        if not hasattr(self, "pipeline_text2image"):
            raise ValueError("Model not loaded")

        # if kwargs.source_image is not None:
        #     if kwargs.num_inference_steps * kwargs.strength < 1:
        #         raise ValueError(
        #             "The strength and the number of inference steps are too low."
        #             "Please increase the number of inference steps or the strength so that the product is at least 1."  # noqa
        #         )

        #     source_image = kwargs.source_image
        #     image = load_image(str(source_image.path))
        #     new_width = kwargs.width
        #     new_height = kwargs.height

        #     # Calculate the new dimensions while preserving aspect ratio
        #     img_width, img_height = image.size
        #     aspect_ratio = img_width / img_height
        #     if img_width > img_height:
        #         new_height = int(new_width / aspect_ratio)
        #     else:
        #         new_width = int(new_height * aspect_ratio)

        #     # Resize the image
        #     img = image.resize((new_width, new_height), Image.LANCZOS)

        #     # Calculate the center crop box
        #     left = (new_width - new_height) / 2
        #     top = (new_height - new_width) / 2
        #     right = (new_width + new_height) / 2
        #     bottom = (new_height + new_width) / 2
        #     box = (left, top, right, bottom)

        #     # Perform the crop
        #     cropped_img = img.crop(box)

        #     input_kwargs = kwargs.to_dict()
        #     input_kwargs.pop("source_image")
        #     # input_kwargs.pop("height")
        #     # input_kwargs.pop("width")

        #     images = self.pipeline_image2image(
        #         image=cropped_img,
        #         prompt=prompt,
        #         guidance_scale=0.0,
        #         **input_kwargs,
        #     ).images

        thread = Thread(target=self.run_func, args=(prompt, kwargs))
        thread.start()
        stream = self.stream_func()
        return Stream(stream)

        # else:
        #     images = self.pipeline_text2image(
        #         prompt=prompt,
        #         guidance_scale=0.0,
        #         **kwargs.to_dict(),
        #     ).images
        # output_images = []
        # for i, image in enumerate(images):
        #     path = Path(f"/tmp/sd/image-{i}.jpg")
        #     path.parent.mkdir(parents=True, exist_ok=True)
        #     image.save(str(path))
        #     output_images.append(File(path=path, allow_out_of_context_creation=True))
        # return output_images


with Pipeline() as builder:
    input_var = Variable(
        str,
        description="Input prompt",
        title="Input prompt",
    )

    kwargs = Variable(
        ModelKwargs,
        description="Model arguments",
        title="Model arguments",
    )

    my_model = MyModelClass()
    my_model.load()

    output_var = my_model.predict(input_var, kwargs)

    builder.output(output_var)

my_new_pipeline = builder.get_pipeline()
