from pipeline import (
    onnx_model
)
from pipeline.util.torch_utils import tensor_to_list
import torch

onnx_pipeline = onnx_model('./example.onnx')

#run locally
input = tensor_to_list(torch.rand(1, 28, 28, device='cpu'))
result = onnx_pipeline.run(['output'],{'input':input})
print(result)