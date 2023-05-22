from dataclasses import dataclass
from logging import getLogger
from typing import Dict


import torch
from torch import Tensor
from pandas import DataFrame
from transformers import AutoConfig
from optimum.exporters import TasksManager
from transformers.onnx.utils import get_preprocessor

from src.backend.base import Backend
from src.benchmark.base import Benchmark, BenchmarkConfig

BENCHMARK_NAME = "inference"
LOGGER = getLogger(BENCHMARK_NAME)


@dataclass
class InferenceConfig(BenchmarkConfig):
    name: str = BENCHMARK_NAME

    warmup_runs: int = 5
    benchmark_duration: int = 5

    # common
    batch_size: int = 2
    sequence_length: int = 16
    num_choices: int = 2
    # image
    width: int = 64
    height: int = 64
    num_channels: int = 3
    point_batch_size: int = 3
    nb_points_per_image: int = 2
    # audio
    feature_size: int = 80
    nb_max_frames: int = 3000
    audio_sequence_length: int = 16000


class InferenceBenchmark(Benchmark):
    def generate_dummy_inputs(self) -> Dict[str, Tensor]:
        auto_config = AutoConfig.from_pretrained(self.model)
        model_type = auto_config.model_type
        
        onnx_config = TasksManager._SUPPORTED_MODEL_TYPE[model_type]['onnx'][self.task](auto_config)
        LOGGER.info(f"Using {onnx_config.__class__.__name__} as onnx config")
        
        dummy_inputs_generator = onnx_config.DUMMY_INPUT_GENERATOR_CLASSES[0]( # type: ignore
            task=self.task,
            normalized_config=onnx_config.NORMALIZED_CONFIG_CLASS(auto_config), # type: ignore
        )
        LOGGER.info(f"Using {dummy_inputs_generator.__class__.__name__} as dummy inputs generator")

        preprocessor = get_preprocessor(self.model)
        LOGGER.info(f"Using preprocessor {preprocessor.__class__.__name__} to generate dummy inputs")

        dummy_inputs = dict()
        for input_name in preprocessor.model_input_names: # type: ignore
            LOGGER.info(f"Generating dummy input for {input_name}")
            dummy_inputs[input_name] = dummy_inputs_generator.generate(
                input_name,
                framework='pt'
            ).to(self.device)

            if input_name == 'attention_mask' and 'input_values' in dummy_inputs:
                dummy_inputs['attention_mask'] = torch.ones_like(
                    dummy_inputs['input_values'])
            elif input_name == 'attention_mask':
                dummy_inputs['attention_mask'] = torch.ones_like(
                    dummy_inputs['input_ids'])

        return dummy_inputs

    def configure(self, config: InferenceConfig):
        self.warmup_runs = config.warmup_runs
        self.benchmark_duration = config.benchmark_duration

    def run(self, backend: Backend) -> None:
        LOGGER.info(f"Generating dummy input")
        dummy_inputs = self.generate_dummy_inputs()

        LOGGER.info(f"Running inference benchmark")
        self.inference_results = backend.run_inference(
            dummy_inputs, self.warmup_runs, self.benchmark_duration)

    @property
    def results(self) -> DataFrame:
        return self.inference_results

    def save(self, path: str = '') -> None:
        LOGGER.info('Saving inference results')
        self.inference_results.to_csv(path + 'results.csv')
