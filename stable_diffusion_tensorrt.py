from diffusers import DiffusionPipeline, AutoencoderKL, StableDiffusionXLPipeline
from torch import float16, bfloat16, float8_e5m2, compile, float32, _dynamo
from accelerate import PartialState
import torch_tensorrt
# vae = AutoencoderKL.from_single_file("/home/katayama_23266031/models/wai-nsfw-illustrious/sdxl_vae.safetensors",
#                                      local_files_only=False, torch_dtype=float16
#                                      ).to("cuda:1")

pipe = DiffusionPipeline.from_pretrained(pretrained_model_name_or_path="/home/katayama_23266031/models/FLUX.1-dev",
                                         torch_dtype=bfloat16)
pipe.enable_model_cpu_offload()
print(_dynamo.list_backends())
pipe.transformer = compile(
    pipe.transformer,
    backend="torch_tensorrt",
    options={
        "truncate_long_and_double": True,
        "enabled_precisions": {float32, float16},
    },
    dynamic=False,
)
# print(dir(pipe))
exit()
pipe(
    "A futuristic anime-style character with bright blue and pink and a bit purple"
    " short hair, featuring a red hairpin on one side. The character has large,"
    " captivating eyes and a gentle expression. Dressed in a casual white outfit,"
    " they are posing with a peace sign. The background is a softly blurred autumn forest scene.",
    # custom_pipeline="stable_diffusion_tensorrt_txt2img",
    num_inference_steps=20,  # サンプリングステップ数
    width=2560,  # 画像の幅
    height=2560  # 画像の高さ)
)
